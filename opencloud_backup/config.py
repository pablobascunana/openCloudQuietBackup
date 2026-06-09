from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "StackPaths",
    "ValidationError",
    "load_stack_paths",
    "resolve_compose_file",
]


class ValidationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class StackPaths:
    opencloud_root: Path
    config_dir: Path
    data_dir: Path
    compose_dir: Path
    compose_file: Path

    @property
    def compose_project_directory(self) -> Path:
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


def _require_subdir(root: Path, name: str) -> Path:
    path = root / name
    if not path.exists():
        raise ValidationError(
            f"No existe el directorio «{name}» bajo la raíz de OpenCloud: {path}. "
            f"Raíz indicada: {root}"
        )
    if not path.is_dir():
        raise ValidationError(
            f"«{name}» existe pero no es un directorio: {path}"
        )
    return path


def resolve_compose_file(compose_dir: Path, compose_file: Path | None) -> Path:
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
    root_abs = _ensure_abs_dir(Path(opencloud_root), "opencloud-root")
    config_dir = _require_subdir(root_abs, "config")
    data_dir = _require_subdir(root_abs, "data")

    cdir: Path
    if compose_dir is None:
        cdir = root_abs
    else:
        cdir = _ensure_abs_dir(Path(compose_dir), "compose-dir")

    cfile_arg = Path(compose_file) if compose_file is not None else None
    compose_path = resolve_compose_file(cdir, cfile_arg)

    _check_readable_dir(config_dir, "config")
    _check_readable_dir(data_dir, "data")
    _check_readable_dir(cdir, "compose-dir")
    _check_readable_file(compose_path, "compose-file")

    return StackPaths(
        opencloud_root=root_abs,
        config_dir=config_dir.resolve(),
        data_dir=data_dir.resolve(),
        compose_dir=cdir.resolve(),
        compose_file=compose_path,
    )
