from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from opencloud_backup.adapters.docker_compose import SubprocessComposeRunner
from opencloud_backup.adapters.prerequisites import run_prerequisite_checks
from opencloud_backup.config import (
    ValidationError,
    load_stack_paths,
    resolve_backup_output_dir,
    resolve_snapshot_base_dir,
)
from opencloud_backup.domain.archive import CompressionFormat, detect_compression_format
from opencloud_backup.domain.errors import (
    ArchiveCommandError,
    ComposeCommandError,
    HashMismatchError,
    IntegrityError,
    PrerequisiteCheckError,
    RsyncCommandError,
    SidecarNotFoundError,
)
from opencloud_backup.domain.integrity import sidecar_path_for_archive
from opencloud_backup.domain.prereqs import DiskThreshold, JobMode, PrerequisiteReport
from opencloud_backup.domain.snapshot import default_disk_check_path_for_snapshot_base
from opencloud_backup.jobs.backup import run_backup_job
from opencloud_backup.jobs.integrity import verify_archive_integrity
from opencloud_backup.jobs.restore import run_restore_job

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

_BYTES_PER_GIBIBYTE = 1024**3
DEFAULT_STOP_TIMEOUT_SECONDS = 180
MIN_STOP_TIMEOUT_SECONDS = 1
MAX_STOP_TIMEOUT_SECONDS = 3600
DEFAULT_START_TIMEOUT_SECONDS = 180
MIN_START_TIMEOUT_SECONDS = 1
MAX_START_TIMEOUT_SECONDS = 3600
MIN_PACK_TIMEOUT_SECONDS = 1
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


def _parse_compression(compression_value: str) -> CompressionFormat:
    return CompressionFormat(compression_value)


def _compression_from_environment_variable() -> CompressionFormat | None:
    compression_value = os.environ.get("OCB_COMPRESSION")
    if compression_value is None or compression_value.strip() == "":
        return None
    return _parse_compression(compression_value.strip())


def _truthy_env(environment_variable_name: str) -> bool:
    environment_variable_value = os.environ.get(environment_variable_name)
    if environment_variable_value is None or environment_variable_value.strip() == "":
        return False
    return environment_variable_value.strip().lower() in ("1", "true", "yes")


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
        help="Backup OpenCloud stack (US-010, US-011): prereqs, stop, pack canonical archive.",
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
        "--start-timeout",
        type=int,
        default=_optional_int_from_environment_variable("OCB_START_TIMEOUT") or DEFAULT_START_TIMEOUT_SECONDS,
        help=f"Timeout in seconds for docker compose up -d (default: {DEFAULT_START_TIMEOUT_SECONDS}). "
        "Env: OCB_START_TIMEOUT.",
    )
    backup_subparser.add_argument(
        "--output-dir",
        type=Path,
        default=_path_from_environment_variable("OCB_OUTPUT_DIR"),
        help="Directory for backup archives (default: {opencloud_root}/backups). Env: OCB_OUTPUT_DIR.",
    )
    backup_subparser.add_argument(
        "--compression",
        type=_parse_compression,
        choices=list(CompressionFormat),
        default=_compression_from_environment_variable() or CompressionFormat.ZSTD,
        help="Archive compression format (default: zstd). Env: OCB_COMPRESSION.",
    )
    backup_subparser.add_argument(
        "--no-env",
        action="store_true",
        help="Exclude .env from the backup archive even when the file exists.",
    )
    backup_subparser.add_argument(
        "--pack-timeout",
        type=int,
        default=_optional_int_from_environment_variable("OCB_PACK_TIMEOUT"),
        help="Timeout in seconds for the pack phase (default: unlimited). Env: OCB_PACK_TIMEOUT.",
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
        help="Path for disk space check (default: resolved output directory).",
    )
    backup_subparser.add_argument(
        "--write-hash",
        action="store_true",
        help="Write SHA-256 sidecar .sha256 after pack (US-013). Env: OCB_WRITE_HASH.",
    )

    restore_subparser = subcommand_parsers.add_parser(
        "restore",
        help="Restore OpenCloud stack (US-020–US-022): prereqs, stop, snapshot, extract, apply.",
    )
    restore_subparser.add_argument(
        "--archive",
        type=Path,
        required=True,
        help="Path to backup archive file (.tar.zst, .tar.gz, or .tar).",
    )
    restore_subparser.add_argument(
        "--opencloud-root",
        type=Path,
        default=_path_from_environment_variable("OCB_OPENCLOUD_ROOT"),
        help="Root with config/ and data/ (or OCB_OPENCLOUD_ROOT env var).",
    )
    restore_subparser.add_argument(
        "--compose-dir",
        type=Path,
        default=_path_from_environment_variable("OCB_COMPOSE_DIR"),
        help="Compose project directory (default: same as --opencloud-root). Env: OCB_COMPOSE_DIR.",
    )
    restore_subparser.add_argument(
        "--compose-file",
        type=Path,
        default=_path_from_environment_variable("OCB_COMPOSE_FILE"),
        help="Explicit path to docker-compose.yml/.yaml (relative to --compose-dir). "
        "If omitted, searched under --compose-dir. Env: OCB_COMPOSE_FILE.",
    )
    restore_subparser.add_argument(
        "--stop-timeout",
        type=int,
        default=_optional_int_from_environment_variable("OCB_STOP_TIMEOUT") or DEFAULT_STOP_TIMEOUT_SECONDS,
        help=f"Timeout in seconds for docker compose down (default: {DEFAULT_STOP_TIMEOUT_SECONDS}). "
        "Env: OCB_STOP_TIMEOUT.",
    )
    restore_subparser.add_argument(
        "--min-free-bytes",
        type=int,
        default=_optional_int_from_environment_variable("OCB_MIN_FREE_BYTES"),
        help="Minimum free disk space in bytes. Env: OCB_MIN_FREE_BYTES.",
    )
    restore_subparser.add_argument(
        "--min-free-percent",
        type=float,
        default=_optional_float_from_environment_variable("OCB_MIN_FREE_PERCENT"),
        help="Minimum free disk space as percent of volume (1-100). Env: OCB_MIN_FREE_PERCENT.",
    )
    restore_subparser.add_argument(
        "--disk-check-path",
        type=Path,
        default=None,
        help="Path for disk space check (default: parent of resolved snapshot base). "
        "Ensure free space is at least the combined size of config/, data/, and .env "
        "(estimate with: du -sh config data .env).",
    )
    restore_subparser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=_path_from_environment_variable("OCB_SNAPSHOT_DIR"),
        help="Snapshot base directory (default: {opencloud_root}/snapshots). "
        "Created automatically if missing. Env: OCB_SNAPSHOT_DIR.",
    )
    restore_subparser.add_argument(
        "--keep-previous-snapshot",
        action="store_true",
        help="Keep existing pre-restore-* snapshot subdirectories (default: replace them).",
    )
    restore_subparser.add_argument(
        "--no-env",
        action="store_true",
        help="Exclude .env from the security snapshot.",
    )
    restore_subparser.add_argument(
        "--snapshot-timeout",
        type=int,
        default=_optional_int_from_environment_variable("OCB_SNAPSHOT_TIMEOUT"),
        help="Per-rsync timeout in seconds (default: unlimited). Env: OCB_SNAPSHOT_TIMEOUT.",
    )
    restore_subparser.add_argument(
        "--verify-hash",
        action="store_true",
        help="Verify archive SHA-256 sidecar before extract (US-013). Env: OCB_VERIFY_HASH.",
    )
    restore_subparser.add_argument(
        "--extract-timeout",
        type=int,
        default=_optional_int_from_environment_variable("OCB_EXTRACT_TIMEOUT"),
        help="Timeout in seconds for archive list/extract (default: unlimited). Env: OCB_EXTRACT_TIMEOUT.",
    )
    restore_subparser.add_argument(
        "--apply-timeout",
        type=int,
        default=_optional_int_from_environment_variable("OCB_APPLY_TIMEOUT"),
        help="Timeout in seconds for rsync apply phase (default: unlimited). Env: OCB_APPLY_TIMEOUT.",
    )

    verify_subparser = subcommand_parsers.add_parser(
        "verify",
        help="Verify backup archive integrity against .sha256 sidecar (US-013).",
    )
    verify_subparser.add_argument(
        "--archive",
        type=Path,
        required=True,
        help="Path to backup archive file.",
    )
    verify_subparser.add_argument(
        "--sidecar",
        type=Path,
        default=None,
        help="Path to sidecar file (default: {archive}.sha256).",
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

    start_timeout_seconds: int = parsed_arguments.start_timeout
    if not MIN_START_TIMEOUT_SECONDS <= start_timeout_seconds <= MAX_START_TIMEOUT_SECONDS:
        sys.stderr.write(
            f"Error: --start-timeout debe estar entre {MIN_START_TIMEOUT_SECONDS} y "
            f"{MAX_START_TIMEOUT_SECONDS} segundos.\n"
        )
        return EXIT_USAGE

    pack_timeout_seconds: int | None = parsed_arguments.pack_timeout
    if pack_timeout_seconds is not None and pack_timeout_seconds < MIN_PACK_TIMEOUT_SECONDS:
        sys.stderr.write("Error: --pack-timeout must be at least 1 second.\n")
        return EXIT_USAGE

    try:
        stack_paths = load_stack_paths(
            opencloud_root=parsed_arguments.opencloud_root,
            compose_dir=parsed_arguments.compose_dir,
            compose_file=parsed_arguments.compose_file,
        )
        output_dir = resolve_backup_output_dir(
            stack_paths.opencloud_root,
            parsed_arguments.output_dir,
        )
    except ValidationError as validation_error:
        sys.stderr.write(f"Configuration error: {validation_error}\n")
        return EXIT_ERROR

    disk_check_path = (
        parsed_arguments.disk_check_path.expanduser().resolve()
        if parsed_arguments.disk_check_path is not None
        else output_dir
    )
    disk_threshold = _build_disk_threshold(min_free_bytes, min_free_percent)
    include_env = not parsed_arguments.no_env
    write_integrity = parsed_arguments.write_hash or _truthy_env("OCB_WRITE_HASH")

    try:
        archive_path = run_backup_job(
            stack_paths=stack_paths,
            output_dir=output_dir,
            compression=parsed_arguments.compression,
            include_env=include_env,
            disk_check_path=disk_check_path,
            disk_threshold=disk_threshold,
            stop_timeout_seconds=stop_timeout_seconds,
            start_timeout_seconds=start_timeout_seconds,
            pack_timeout_seconds=pack_timeout_seconds,
            write_integrity=write_integrity,
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
    except ArchiveCommandError as archive_command_error:
        sys.stderr.write(
            f"Backup error: {archive_command_error.command_label} "
            f"(exit {archive_command_error.return_code})\n"
        )
        if archive_command_error.stderr:
            sys.stderr.write(f"  {archive_command_error.stderr}\n")
        return EXIT_ERROR
    except IntegrityError:
        sys.stderr.write("Error de integridad: no se pudo registrar el hash del archivo de backup.\n")
        return EXIT_ERROR

    print("Backup completed successfully.")
    print(f"  archive: {archive_path}")
    if write_integrity:
        print(f"  sidecar: {sidecar_path_for_archive(archive_path)}")
    return EXIT_OK


def run_restore_command(parsed_arguments: argparse.Namespace) -> int:
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

    snapshot_timeout_seconds: int | None = parsed_arguments.snapshot_timeout
    if snapshot_timeout_seconds is not None and snapshot_timeout_seconds < MIN_PACK_TIMEOUT_SECONDS:
        sys.stderr.write("Error: --snapshot-timeout must be at least 1 second.\n")
        return EXIT_USAGE

    extract_timeout_seconds: int | None = parsed_arguments.extract_timeout
    if extract_timeout_seconds is not None and extract_timeout_seconds < MIN_PACK_TIMEOUT_SECONDS:
        sys.stderr.write("Error: --extract-timeout must be at least 1 second.\n")
        return EXIT_USAGE

    apply_timeout_seconds: int | None = parsed_arguments.apply_timeout
    if apply_timeout_seconds is not None and apply_timeout_seconds < MIN_PACK_TIMEOUT_SECONDS:
        sys.stderr.write("Error: --apply-timeout must be at least 1 second.\n")
        return EXIT_USAGE

    try:
        stack_paths = load_stack_paths(
            opencloud_root=parsed_arguments.opencloud_root,
            compose_dir=parsed_arguments.compose_dir,
            compose_file=parsed_arguments.compose_file,
        )
        snapshot_base_dir = resolve_snapshot_base_dir(
            stack_paths.opencloud_root,
            parsed_arguments.snapshot_dir,
        )
        archive_path = parsed_arguments.archive.expanduser().resolve()
    except ValidationError as validation_error:
        sys.stderr.write(f"Configuration error: {validation_error}\n")
        return EXIT_ERROR

    if not archive_path.is_file():
        sys.stderr.write("Error: el archivo de backup no existe o no es accesible.\n")
        return EXIT_ERROR

    try:
        detect_compression_format(archive_path)
    except ValidationError as validation_error:
        sys.stderr.write(f"Configuration error: {validation_error}\n")
        return EXIT_ERROR

    disk_check_path = (
        parsed_arguments.disk_check_path.expanduser().resolve()
        if parsed_arguments.disk_check_path is not None
        else default_disk_check_path_for_snapshot_base(snapshot_base_dir)
    )
    disk_threshold = _build_disk_threshold(min_free_bytes, min_free_percent)
    verify_hash = parsed_arguments.verify_hash or _truthy_env("OCB_VERIFY_HASH")

    try:
        result = run_restore_job(
            stack_paths=stack_paths,
            archive_path=archive_path,
            snapshot_base_dir=snapshot_base_dir,
            keep_previous_snapshot=parsed_arguments.keep_previous_snapshot,
            include_env=not parsed_arguments.no_env,
            verify_hash=verify_hash,
            disk_check_path=disk_check_path,
            disk_threshold=disk_threshold,
            stop_timeout_seconds=stop_timeout_seconds,
            snapshot_timeout_seconds=snapshot_timeout_seconds,
            extract_timeout_seconds=extract_timeout_seconds,
            apply_timeout_seconds=apply_timeout_seconds,
            compose_runner=SubprocessComposeRunner(),
        )
    except PrerequisiteCheckError as prerequisite_check_error:
        sys.stderr.write(_format_prerequisite_failure(prerequisite_check_error.report) + "\n")
        return EXIT_ERROR
    except ComposeCommandError as compose_command_error:
        sys.stderr.write(
            f"Restore error: {compose_command_error.command_label} "
            f"(exit {compose_command_error.return_code})\n"
        )
        if compose_command_error.stderr:
            sys.stderr.write(f"  {compose_command_error.stderr}\n")
        return EXIT_ERROR
    except RsyncCommandError as rsync_command_error:
        sys.stderr.write(
            "Error de restore: falló rsync "
            f"({rsync_command_error.command_label}, código {rsync_command_error.return_code})\n"
        )
        if rsync_command_error.stderr:
            sys.stderr.write(f"  {rsync_command_error.stderr}\n")
        return EXIT_ERROR
    except ArchiveCommandError as archive_command_error:
        sys.stderr.write(
            f"Error de restore: falló {archive_command_error.command_label} "
            f"(código {archive_command_error.return_code})\n"
        )
        if archive_command_error.stderr:
            sys.stderr.write(f"  {archive_command_error.stderr}\n")
        return EXIT_ERROR
    except SidecarNotFoundError:
        sys.stderr.write("Error de integridad: no se encontró el archivo sidecar .sha256.\n")
        return EXIT_ERROR
    except HashMismatchError:
        sys.stderr.write("Error de integridad: el hash del archivo no coincide con el sidecar.\n")
        return EXIT_ERROR
    except IntegrityError:
        sys.stderr.write("Error de integridad: no se pudo verificar el archivo de backup.\n")
        return EXIT_ERROR
    except ValidationError as validation_error:
        sys.stderr.write(f"Configuration error: {validation_error}\n")
        return EXIT_ERROR

    print("Restore completado (extracción y aplicación).")
    print(f"  snapshot: {result.snapshot_path}")
    print(f"  archive: {result.archive_path}")
    print(
        "El arranque del stack (docker compose up, US-023) debe ejecutarse manualmente."
    )
    return EXIT_OK


def run_verify_command(parsed_arguments: argparse.Namespace) -> int:
    archive_path = parsed_arguments.archive.expanduser().resolve()
    sidecar_path = (
        parsed_arguments.sidecar.expanduser().resolve()
        if parsed_arguments.sidecar is not None
        else None
    )

    if not archive_path.is_file():
        sys.stderr.write("Error: el archivo de backup no existe o no es accesible.\n")
        return EXIT_ERROR

    try:
        verify_archive_integrity(archive_path, sidecar_path=sidecar_path)
    except SidecarNotFoundError:
        sys.stderr.write("Error de integridad: no se encontró el fichero sidecar .sha256.\n")
        return EXIT_ERROR
    except HashMismatchError:
        sys.stderr.write("Error de integridad: el hash del archivo no coincide con el sidecar.\n")
        return EXIT_ERROR
    except IntegrityError as integrity_error:
        sys.stderr.write(f"Error de integridad: {integrity_error}\n")
        return EXIT_ERROR

    resolved_sidecar = sidecar_path or sidecar_path_for_archive(archive_path)
    print("Integridad verificada correctamente.")
    print(f"  archive: {archive_path}")
    print(f"  sidecar: {resolved_sidecar}")
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
    if parsed_arguments.command == "restore":
        raise SystemExit(run_restore_command(parsed_arguments))
    if parsed_arguments.command == "verify":
        raise SystemExit(run_verify_command(parsed_arguments))
    raise SystemExit(EXIT_USAGE)


if __name__ == "__main__":
    main()
