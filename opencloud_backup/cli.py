from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from opencloud_backup.config import ValidationError, load_stack_paths

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def _path_from_environment_variable(environment_variable_name: str) -> Path | None:
    environment_variable_value = os.environ.get(environment_variable_name)
    if environment_variable_value is None or environment_variable_value.strip() == "":
        return None
    return Path(environment_variable_value)


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


def main(command_line_arguments: list[str] | None = None) -> None:
    argument_parser = build_argument_parser()
    parsed_arguments = argument_parser.parse_args(command_line_arguments)
    if parsed_arguments.command == "validate":
        raise SystemExit(run_validate_command(parsed_arguments))
    raise SystemExit(EXIT_USAGE)


if __name__ == "__main__":
    main()
