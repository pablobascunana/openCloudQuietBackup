from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from opencloud_backup.adapters.docker_compose import SubprocessComposeRunner
from opencloud_backup.adapters.prerequisites import run_prerequisite_checks
from opencloud_backup.config import ValidationError, load_stack_paths
from opencloud_backup.domain.errors import ComposeCommandError, PrerequisiteCheckError
from opencloud_backup.domain.prereqs import DiskThreshold, JobMode, PrerequisiteReport
from opencloud_backup.jobs.backup import run_backup_job

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

_BYTES_PER_GIBIBYTE = 1024**3
DEFAULT_STOP_TIMEOUT_SECONDS = 180
MIN_STOP_TIMEOUT_SECONDS = 1
MAX_STOP_TIMEOUT_SECONDS = 3600
DOCKER_PS_FAILURE_HINT = (
    "Hint: add your user to the 'docker' group, verify /var/run/docker.sock permissions, "
    "then re-login or restart your session."
)


def _path_from_environment_variable(environment_variable_name: str) -> Path | None:
    environment_variable_value = os.environ.get(environment_variable_name)
    if environment_variable_value is None or environment_variable_value.strip() == "":
        return None
    return Path(environment_variable_value)


def _optional_int_from_environment_variable(environment_variable_name: str) -> int | None:
    environment_variable_value = os.environ.get(environment_variable_name)
    if environment_variable_value is None or environment_variable_value.strip() == "":
        return None
    return int(environment_variable_value)


def _optional_float_from_environment_variable(environment_variable_name: str) -> float | None:
    environment_variable_value = os.environ.get(environment_variable_name)
    if environment_variable_value is None or environment_variable_value.strip() == "":
        return None
    return float(environment_variable_value)


def _format_byte_size(byte_count: int) -> str:
    gibibytes = byte_count / _BYTES_PER_GIBIBYTE
    if gibibytes >= 1:
        return f"{gibibytes:.1f} GiB"
    mebibytes = byte_count / (1024**2)
    if mebibytes >= 1:
        return f"{mebibytes:.1f} MiB"
    return f"{byte_count} B"


def _parse_job_mode(mode_value: str) -> JobMode:
    return JobMode(mode_value)


def _build_disk_threshold(
    min_free_bytes: int | None,
    min_free_percent: float | None,
) -> DiskThreshold | None:
    has_bytes_threshold = min_free_bytes is not None
    has_percent_threshold = min_free_percent is not None
    if has_bytes_threshold and has_percent_threshold:
        return None
    if min_free_bytes is not None:
        return DiskThreshold(kind="bytes", value=min_free_bytes)
    if min_free_percent is not None:
        return DiskThreshold(kind="percent", value=int(min_free_percent))
    return None


def _format_prerequisite_success(report: PrerequisiteReport) -> str:
    lines = [f"Prerequisites OK (mode: {report.mode.value}):"]
    binary_summary = "  Binaries: docker, tar, compression (zstd or gzip)"
    if report.mode != JobMode.BACKUP:
        binary_summary += ", rsync"
    lines.append(binary_summary)
    lines.append("  Docker Compose: OK")
    lines.append("  Docker daemon: OK")
    lines.append("  Stack path access: OK")
    if report.disk is not None:
        disk_line = f"  Disk {report.disk.path}: {_format_byte_size(report.disk.free_bytes)} free"
        if report.disk.threshold is None:
            disk_line += " (no threshold)"
        else:
            disk_line += " (threshold met)"
        lines.append(disk_line)
    return "\n".join(lines)


def _format_prerequisite_failure(report: PrerequisiteReport) -> str:
    lines = ["Prerequisite error:"]
    if report.missing_binaries:
        lines.append(f"  Missing binaries: {', '.join(report.missing_binaries)}")
    if report.failed_commands:
        lines.append(f"  Failed commands: {', '.join(report.failed_commands)}")
        if "docker ps" in report.failed_commands:
            lines.append(f"  {DOCKER_PS_FAILURE_HINT}")
    if report.disk is not None and not report.disk.ok and report.disk.threshold is not None:
        threshold = report.disk.threshold
        if threshold.kind == "bytes":
            lines.append(
                f"  Disk {report.disk.path}: {_format_byte_size(report.disk.free_bytes)} free; "
                f"requires {_format_byte_size(threshold.value)}"
            )
        else:
            lines.append(
                f"  Disk {report.disk.path}: {report.disk.free_percent:.1f}% free; "
                f"requires {threshold.value}%"
            )
    return "\n".join(lines)


def build_argument_parser() -> argparse.ArgumentParser:
    root_argument_parser = argparse.ArgumentParser(
        prog="opencloud-quiet-backup",
        description="OpenCloud Quiet Backup — coherent backups of OpenCloud on Docker.",
    )
    subcommand_parsers = root_argument_parser.add_subparsers(dest="command", required=True)

    validate_subparser = subcommand_parsers.add_parser(
        "validate",
        help="Validate stack paths (US-001): config/, data/ and compose file.",
    )
    validate_subparser.add_argument(
        "--opencloud-root",
        type=Path,
        default=_path_from_environment_variable("OCB_OPENCLOUD_ROOT"),
        help="Root with config/ and data/ (or OCB_OPENCLOUD_ROOT env var).",
    )
    validate_subparser.add_argument(
        "--compose-dir",
        type=Path,
        default=_path_from_environment_variable("OCB_COMPOSE_DIR"),
        help="Compose project directory (default: same as --opencloud-root). Env: OCB_COMPOSE_DIR.",
    )
    validate_subparser.add_argument(
        "--compose-file",
        type=Path,
        default=_path_from_environment_variable("OCB_COMPOSE_FILE"),
        help="Explicit path to docker-compose.yml/.yaml (relative to --compose-dir). "
        "If omitted, searched under --compose-dir. Env: OCB_COMPOSE_FILE.",
    )

    prereqs_subparser = subcommand_parsers.add_parser(
        "prereqs",
        help="Check host prerequisites (US-002, US-003): Docker, tools, path access, disk space.",
    )
    prereqs_subparser.add_argument(
        "--opencloud-root",
        type=Path,
        default=_path_from_environment_variable("OCB_OPENCLOUD_ROOT"),
        help="OpenCloud root (or OCB_OPENCLOUD_ROOT env var). Used for path validation and default disk check.",
    )
    prereqs_subparser.add_argument(
        "--compose-dir",
        type=Path,
        default=_path_from_environment_variable("OCB_COMPOSE_DIR"),
        help="Compose project directory (default: same as --opencloud-root). Env: OCB_COMPOSE_DIR.",
    )
    prereqs_subparser.add_argument(
        "--compose-file",
        type=Path,
        default=_path_from_environment_variable("OCB_COMPOSE_FILE"),
        help="Explicit path to docker-compose.yml/.yaml (relative to --compose-dir). "
        "If omitted, searched under --compose-dir. Env: OCB_COMPOSE_FILE.",
    )
    prereqs_subparser.add_argument(
        "--mode",
        type=_parse_job_mode,
        choices=list(JobMode),
        default=JobMode.ALL,
        help="Job mode: backup, restore, or all (default: all).",
    )
    prereqs_subparser.add_argument(
        "--min-free-bytes",
        type=int,
        default=_optional_int_from_environment_variable("OCB_MIN_FREE_BYTES"),
        help="Minimum free disk space in bytes. Env: OCB_MIN_FREE_BYTES.",
    )
    prereqs_subparser.add_argument(
        "--min-free-percent",
        type=float,
        default=_optional_float_from_environment_variable("OCB_MIN_FREE_PERCENT"),
        help="Minimum free disk space as percent of volume (1-100). Env: OCB_MIN_FREE_PERCENT.",
    )
    prereqs_subparser.add_argument(
        "--disk-check-path",
        type=Path,
        default=None,
        help="Path for disk space check (default: resolved opencloud_root).",
    )

    backup_subparser = subcommand_parsers.add_parser(
        "backup",
        help="Stop OpenCloud stack before backup (US-010): prereqs then docker compose down.",
    )
    backup_subparser.add_argument(
        "--opencloud-root",
        type=Path,
        default=_path_from_environment_variable("OCB_OPENCLOUD_ROOT"),
        help="Root with config/ and data/ (or OCB_OPENCLOUD_ROOT env var).",
    )
    backup_subparser.add_argument(
        "--compose-dir",
        type=Path,
        default=_path_from_environment_variable("OCB_COMPOSE_DIR"),
        help="Compose project directory (default: same as --opencloud-root). Env: OCB_COMPOSE_DIR.",
    )
    backup_subparser.add_argument(
        "--compose-file",
        type=Path,
        default=_path_from_environment_variable("OCB_COMPOSE_FILE"),
        help="Explicit path to docker-compose.yml/.yaml (relative to --compose-dir). "
        "If omitted, searched under --compose-dir. Env: OCB_COMPOSE_FILE.",
    )
    backup_subparser.add_argument(
        "--stop-timeout",
        type=int,
        default=_optional_int_from_environment_variable("OCB_STOP_TIMEOUT") or DEFAULT_STOP_TIMEOUT_SECONDS,
        help=f"Timeout in seconds for docker compose down (default: {DEFAULT_STOP_TIMEOUT_SECONDS}). "
        "Env: OCB_STOP_TIMEOUT.",
    )
    backup_subparser.add_argument(
        "--min-free-bytes",
        type=int,
        default=_optional_int_from_environment_variable("OCB_MIN_FREE_BYTES"),
        help="Minimum free disk space in bytes. Env: OCB_MIN_FREE_BYTES.",
    )
    backup_subparser.add_argument(
        "--min-free-percent",
        type=float,
        default=_optional_float_from_environment_variable("OCB_MIN_FREE_PERCENT"),
        help="Minimum free disk space as percent of volume (1-100). Env: OCB_MIN_FREE_PERCENT.",
    )
    backup_subparser.add_argument(
        "--disk-check-path",
        type=Path,
        default=None,
        help="Path for disk space check (default: resolved opencloud_root).",
    )
    return root_argument_parser


def run_validate_command(parsed_arguments: argparse.Namespace) -> int:
    if parsed_arguments.opencloud_root is None:
        sys.stderr.write("Error: --opencloud-root or OCB_OPENCLOUD_ROOT environment variable is required.\n")
        return EXIT_USAGE

    try:
        stack_paths = load_stack_paths(
            opencloud_root=parsed_arguments.opencloud_root,
            compose_dir=parsed_arguments.compose_dir,
            compose_file=parsed_arguments.compose_file,
        )
    except ValidationError as validation_error:
        sys.stderr.write(f"Configuration error: {validation_error}\n")
        return EXIT_ERROR

    print("Valid configuration:")
    print(f"  opencloud_root: {stack_paths.opencloud_root}")
    print(f"  config_dir:     {stack_paths.config_dir}")
    print(f"  data_dir:       {stack_paths.data_dir}")
    print(f"  compose_dir:    {stack_paths.compose_dir}")
    print(f"  compose_file:   {stack_paths.compose_file}")
    return EXIT_OK


def run_prereqs_command(parsed_arguments: argparse.Namespace) -> int:
    if parsed_arguments.opencloud_root is None:
        sys.stderr.write(
            "Error: --opencloud-root or OCB_OPENCLOUD_ROOT environment variable is required.\n"
        )
        return EXIT_USAGE

    min_free_bytes: int | None = parsed_arguments.min_free_bytes
    min_free_percent: float | None = parsed_arguments.min_free_percent
    if min_free_bytes is not None and min_free_percent is not None:
        sys.stderr.write(
            "Error: --min-free-bytes and --min-free-percent are mutually exclusive; specify only one.\n"
        )
        return EXIT_USAGE

    try:
        stack_paths = load_stack_paths(
            opencloud_root=parsed_arguments.opencloud_root,
            compose_dir=parsed_arguments.compose_dir,
            compose_file=parsed_arguments.compose_file,
        )
    except ValidationError as validation_error:
        sys.stderr.write(f"Configuration error: {validation_error}\n")
        return EXIT_ERROR

    disk_check_path = (
        parsed_arguments.disk_check_path.expanduser().resolve()
        if parsed_arguments.disk_check_path is not None
        else stack_paths.opencloud_root
    )
    disk_threshold = _build_disk_threshold(min_free_bytes, min_free_percent)

    prerequisite_report = run_prerequisite_checks(
        mode=parsed_arguments.mode,
        stack_paths=stack_paths,
        disk_check_path=disk_check_path,
        disk_threshold=disk_threshold,
    )
    if prerequisite_report.ok:
        print(_format_prerequisite_success(prerequisite_report))
        return EXIT_OK

    sys.stderr.write(_format_prerequisite_failure(prerequisite_report) + "\n")
    return EXIT_ERROR


def run_backup_command(parsed_arguments: argparse.Namespace) -> int:
    if parsed_arguments.opencloud_root is None:
        sys.stderr.write(
            "Error: --opencloud-root or OCB_OPENCLOUD_ROOT environment variable is required.\n"
        )
        return EXIT_USAGE

    min_free_bytes: int | None = parsed_arguments.min_free_bytes
    min_free_percent: float | None = parsed_arguments.min_free_percent
    if min_free_bytes is not None and min_free_percent is not None:
        sys.stderr.write(
            "Error: --min-free-bytes and --min-free-percent are mutually exclusive; specify only one.\n"
        )
        return EXIT_USAGE

    stop_timeout_seconds: int = parsed_arguments.stop_timeout
    if not MIN_STOP_TIMEOUT_SECONDS <= stop_timeout_seconds <= MAX_STOP_TIMEOUT_SECONDS:
        sys.stderr.write(
            f"Error: --stop-timeout must be between {MIN_STOP_TIMEOUT_SECONDS} and "
            f"{MAX_STOP_TIMEOUT_SECONDS} seconds.\n"
        )
        return EXIT_USAGE

    try:
        stack_paths = load_stack_paths(
            opencloud_root=parsed_arguments.opencloud_root,
            compose_dir=parsed_arguments.compose_dir,
            compose_file=parsed_arguments.compose_file,
        )
    except ValidationError as validation_error:
        sys.stderr.write(f"Configuration error: {validation_error}\n")
        return EXIT_ERROR

    disk_check_path = (
        parsed_arguments.disk_check_path.expanduser().resolve()
        if parsed_arguments.disk_check_path is not None
        else stack_paths.opencloud_root
    )
    disk_threshold = _build_disk_threshold(min_free_bytes, min_free_percent)

    try:
        run_backup_job(
            stack_paths=stack_paths,
            disk_check_path=disk_check_path,
            disk_threshold=disk_threshold,
            stop_timeout_seconds=stop_timeout_seconds,
            compose_runner=SubprocessComposeRunner(),
        )
    except PrerequisiteCheckError as prerequisite_check_error:
        sys.stderr.write(_format_prerequisite_failure(prerequisite_check_error.report) + "\n")
        return EXIT_ERROR
    except ComposeCommandError as compose_command_error:
        sys.stderr.write(
            f"Backup error: {compose_command_error.command_label} "
            f"(exit {compose_command_error.return_code})\n"
        )
        if compose_command_error.stderr:
            sys.stderr.write(f"  {compose_command_error.stderr}\n")
        return EXIT_ERROR

    print("Backup stop phase completed successfully.")
    return EXIT_OK


def main(command_line_arguments: list[str] | None = None) -> None:
    argument_parser = build_argument_parser()
    parsed_arguments = argument_parser.parse_args(command_line_arguments)
    if parsed_arguments.command == "validate":
        raise SystemExit(run_validate_command(parsed_arguments))
    if parsed_arguments.command == "prereqs":
        raise SystemExit(run_prereqs_command(parsed_arguments))
    if parsed_arguments.command == "backup":
        raise SystemExit(run_backup_command(parsed_arguments))
    raise SystemExit(EXIT_USAGE)


if __name__ == "__main__":
    main()
