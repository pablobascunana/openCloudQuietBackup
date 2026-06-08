"""US-001: rutas del stack OpenCloud y validación."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ValidationError(Exception):
    """Error de configuración con mensaje orientado al administrador."""

    pass


@dataclass(frozen=True, slots=True)
class StackPaths:
    """Rutas resueltas y validadas del despliegue OpenCloud."""

    opencloud_root: Path
    """Directorio que contiene ``config/`` y ``data/`` (ruta absoluta)."""

    compose_dir: Path
    """Directorio de proyecto para ``docker compose --project-directory`` (absoluta)."""

    compose_file: Path
    """Fichero ``docker-compose`` usado por el stack (absoluta)."""

    config_dir: Path
    data_dir: Path

    @property
    def compose_project_directory(self) -> Path:
        """Alias explícito para invocaciones a Docker Compose."""
        return self.compose_dir


def _ensure_abs_dir(path: Path, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve()
    except OSError as e:
        raise ValidationError(
            f"No se pudo resolver la ruta «{label}»: {path}. {e}"
        ) from e
    if not resolved.exists():
        raise ValidationError(
            f"La ruta «{label}» no existe: {resolved}. "
            "Comprueba la ruta o los permisos de montaje."
        )
    if not resolved.is_dir():
        raise ValidationError(
            f"La ruta «{label}» existe pero no es un directorio: {resolved}"
        )
    return resolved


def _check_readable_dir(path: Path, label: str) -> None:
    if not os.access(path, os.R_OK | os.X_OK):
        raise ValidationError(
            f"No hay permisos de lectura/ejecución sobre «{label}»: {path}. "
            "Ejecuta con el usuario adecuado o ajusta permisos (chmod/chown)."
        )


def _check_readable_file(path: Path, label: str) -> None:
    if not os.access(path, os.R_OK):
        raise ValidationError(
            f"No hay permisos de lectura sobre «{label}»: {path}. "
            "Ejecuta con el usuario adecuado o ajusta permisos (chmod/chown)."
        )


def resolve_compose_file(compose_dir: Path, compose_file: Path | None) -> Path:
    """
    Resuelve el fichero compose: explícito o ``docker-compose.yml`` / ``.yaml``.

    Las rutas relativas de ``compose_file`` se interpretan respecto a ``compose_dir``.
    """
    compose_dir = compose_dir.resolve()
    if compose_file is not None:
        p = compose_file.expanduser()
        if not p.is_absolute():
            p = compose_dir / p
        try:
            p = p.resolve()
        except OSError as e:
            raise ValidationError(
                f"No se pudo resolver el fichero compose: {compose_file}. {e}"
            ) from e
        if not p.exists():
            raise ValidationError(
                f"El fichero compose indicado no existe: {p}"
            )
        if not p.is_file():
            raise ValidationError(
                f"La ruta del compose no es un fichero regular: {p}"
            )
        return p
    for name in ("docker-compose.yml", "docker-compose.yaml"):
        candidate = compose_dir / name
        if candidate.is_file():
            return candidate.resolve()
    raise ValidationError(
        f"No se encontró «docker-compose.yml» ni «docker-compose.yaml» en "
        f"{compose_dir}. Indica la ruta con --compose-file o crea uno de esos ficheros."
    )


def load_stack_paths(
    opencloud_root: Path | str,
    compose_dir: Path | str | None = None,
    compose_file: Path | str | None = None,
) -> StackPaths:
    """
    Carga y valida rutas del stack (US-001).

    - ``opencloud_root``: debe contener subdirectorios ``config/`` y ``data/``.
    - ``compose_dir``: por defecto igual que ``opencloud_root``.
    - ``compose_file``: opcional; si falta, se busca ``docker-compose.yml`` o ``.yaml``
        dentro de ``compose_dir``.
    """
    root = Path(opencloud_root)
    root_abs = _ensure_abs_dir(root, "opencloud-root")

    cdir: Path
    if compose_dir is None:
        cdir = root_abs
    else:
        cdir = _ensure_abs_dir(Path(compose_dir), "compose-dir")

    cfile_arg = Path(compose_file) if compose_file is not None else None
    compose_path = resolve_compose_file(cdir, cfile_arg)

    config_dir = root_abs / "config"
    data_dir = root_abs / "data"

    if not config_dir.exists():
        raise ValidationError(
            f"No existe el directorio «config» bajo la raíz de OpenCloud: {config_dir}. "
            f"Raíz indicada: {root_abs}"
        )
    if not config_dir.is_dir():
        raise ValidationError(
            f"«config» existe pero no es un directorio: {config_dir}"
        )
    if not data_dir.exists():
        raise ValidationError(
            f"No existe el directorio «data» bajo la raíz de OpenCloud: {data_dir}. "
            f"Raíz indicada: {root_abs}"
        )
    if not data_dir.is_dir():
        raise ValidationError(
            f"«data» existe pero no es un directorio: {data_dir}"
        )

    _check_readable_dir(config_dir, "config")
    _check_readable_dir(data_dir, "data")
    _check_readable_dir(cdir, "compose-dir")
    _check_readable_file(compose_path, "compose-file")

    return StackPaths(
        opencloud_root=root_abs,
        compose_dir=cdir.resolve(),
        compose_file=compose_path,
        config_dir=config_dir.resolve(),
        data_dir=data_dir.resolve(),
    )
