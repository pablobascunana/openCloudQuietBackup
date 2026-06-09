from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from opencloud_backup.config import ValidationError, load_stack_paths

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    return Path(raw)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="opencloud-quiet-backup",
        description="OpenCloud Quiet Backup — backups coherentes de OpenCloud en Docker.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    validate = sub.add_parser(
        "validate",
        help="Valida rutas del stack (US-001): config/, data/ y fichero compose.",
    )
    validate.add_argument(
        "--opencloud-root",
        type=Path,
        default=_env_path("OCB_OPENCLOUD_ROOT"),
        help="Raíz con config/ y data/ (o variable OCB_OPENCLOUD_ROOT).",
    )
    validate.add_argument(
        "--compose-dir",
        type=Path,
        default=_env_path("OCB_COMPOSE_DIR"),
        help="Directorio del proyecto compose (por defecto: igual que --opencloud-root). "
        "Variable: OCB_COMPOSE_DIR.",
    )
    validate.add_argument(
        "--compose-file",
        type=Path,
        default=_env_path("OCB_COMPOSE_FILE"),
        help="Ruta explícita a docker-compose.yml/.yaml (relativa a --compose-dir). "
        "Si se omite, se busca en --compose-dir. Variable: OCB_COMPOSE_FILE.",
    )
    return p


def cmd_validate(args: argparse.Namespace) -> int:
    if args.opencloud_root is None:
        sys.stderr.write(
            "Error: falta --opencloud-root o la variable de entorno OCB_OPENCLOUD_ROOT.\n"
        )
        return EXIT_USAGE

    try:
        paths = load_stack_paths(
            opencloud_root=args.opencloud_root,
            compose_dir=args.compose_dir,
            compose_file=args.compose_file,
        )
    except ValidationError as e:
        sys.stderr.write(f"Error de configuración: {e}\n")
        return EXIT_ERROR

    print("Configuración válida:")
    print(f"  opencloud_root: {paths.opencloud_root}")
    print(f"  config_dir:     {paths.config_dir}")
    print(f"  data_dir:       {paths.data_dir}")
    print(f"  compose_dir:    {paths.compose_dir}")
    print(f"  compose_file:   {paths.compose_file}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate":
        raise SystemExit(cmd_validate(args))
    raise SystemExit(EXIT_USAGE)


if __name__ == "__main__":
    main()
