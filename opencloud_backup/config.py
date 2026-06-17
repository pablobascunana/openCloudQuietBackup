from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "StackPaths",
    "ValidationError",
    "ensure_snapshot_base_dir",
    "load_stack_paths",
    "resolve_backup_output_dir",
    "resolve_compose_file",
    "resolve_snapshot_base_dir",
    "validate_backup_output_dir",
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


def _resolve_existing_directory(path: Path, path_label: str) -> Path:
    try:
        resolved_path = path.expanduser().resolve()
    except OSError as operating_system_error:
        raise ValidationError(
            f"Could not resolve path «{path_label}»: {path}. {operating_system_error}"
        ) from operating_system_error
    if not resolved_path.exists():
        raise ValidationError(
            f"Path «{path_label}» does not exist: {resolved_path}. Check the path or mount permissions."
        )
    if not resolved_path.is_dir():
        raise ValidationError(f"Path «{path_label}» exists but is not a directory: {resolved_path}")
    return resolved_path


def _require_directory_read_access(directory_path: Path, path_label: str) -> None:
    if not os.access(directory_path, os.R_OK | os.X_OK):
        raise ValidationError(
            f"No read/execute permissions on «{path_label}»: {directory_path}. "
            "Run as the correct user or adjust permissions (chmod/chown)."
        )


def _require_file_read_access(file_path: Path, path_label: str) -> None:
    if not os.access(file_path, os.R_OK):
        raise ValidationError(
            f"No read permission on «{path_label}»: {file_path}. "
            "Run as the correct user or adjust permissions (chmod/chown)."
        )


def _require_subdirectory(opencloud_root: Path, subdirectory_name: str) -> Path:
    subdirectory_path = opencloud_root / subdirectory_name
    if not subdirectory_path.exists():
        raise ValidationError(
            f"Directory «{subdirectory_name}» does not exist under OpenCloud root: {subdirectory_path}. "
            f"OpenCloud root: {opencloud_root}"
        )
    if not subdirectory_path.is_dir():
        raise ValidationError(f"«{subdirectory_name}» exists but is not a directory: {subdirectory_path}")
    return subdirectory_path


def resolve_compose_file(compose_directory: Path, compose_file: Path | None) -> Path:
    compose_directory = compose_directory.resolve()
    if compose_file is not None:
        compose_file_path = compose_file.expanduser()
        if not compose_file_path.is_absolute():
            compose_file_path = compose_directory / compose_file_path
        try:
            compose_file_path = compose_file_path.resolve()
        except OSError as operating_system_error:
            raise ValidationError(
                f"Could not resolve compose file: {compose_file}. {operating_system_error}"
            ) from operating_system_error
        if not compose_file_path.exists():
            raise ValidationError(f"Specified compose file does not exist: {compose_file_path}")
        if not compose_file_path.is_file():
            raise ValidationError(f"Compose path is not a regular file: {compose_file_path}")
        return compose_file_path
    for compose_filename in ("docker-compose.yml", "docker-compose.yaml"):
        default_compose_file_candidate = compose_directory / compose_filename
        if default_compose_file_candidate.is_file():
            return default_compose_file_candidate.resolve()
    raise ValidationError(
        f"Neither «docker-compose.yml» nor «docker-compose.yaml» found in "
        f"{compose_directory}. Specify --compose-file or create one of those files."
    )


def validate_backup_output_dir(output_dir: Path | str) -> Path:
    try:
        resolved_output_dir = Path(output_dir).expanduser().resolve()
    except OSError as operating_system_error:
        raise ValidationError(
            f"Could not resolve output directory: {output_dir}. {operating_system_error}"
        ) from operating_system_error
    if not resolved_output_dir.exists():
        raise ValidationError(f"Output directory does not exist: {resolved_output_dir}")
    if not resolved_output_dir.is_dir():
        raise ValidationError(
            f"Output directory exists but is not a directory: {resolved_output_dir}"
        )
    if not os.access(resolved_output_dir, os.W_OK):
        raise ValidationError(f"Output directory is not writable: {resolved_output_dir}")
    return resolved_output_dir


def resolve_backup_output_dir(opencloud_root: Path, output_dir: Path | str | None = None) -> Path:
    from opencloud_backup.domain.archive import default_backup_output_dir

    target_output_dir = (
        default_backup_output_dir(opencloud_root) if output_dir is None else Path(output_dir)
    )
    return validate_backup_output_dir(target_output_dir)


def resolve_snapshot_base_dir(
    opencloud_root: Path,
    snapshot_dir: Path | str | None = None,
) -> Path:
    from opencloud_backup.domain.snapshot import default_snapshot_base_dir

    if snapshot_dir is None:
        return default_snapshot_base_dir(opencloud_root)
    candidate = Path(snapshot_dir).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        return candidate.resolve()
    except OSError as operating_system_error:
        raise ValidationError(
            f"Could not resolve snapshot directory: {snapshot_dir}. {operating_system_error}"
        ) from operating_system_error


def ensure_snapshot_base_dir(path: Path) -> Path:
    if not path.exists():
        try:
            path.mkdir(parents=True)
        except OSError as operating_system_error:
            raise ValidationError(
                f"Could not create snapshot directory: {path}. {operating_system_error}"
            ) from operating_system_error
    if not path.is_dir():
        raise ValidationError(f"Snapshot path exists but is not a directory: {path}")
    if not os.access(path, os.W_OK):
        raise ValidationError(f"Snapshot directory is not writable: {path}")
    return path.resolve()


def load_stack_paths(
    opencloud_root: Path | str,
    compose_dir: Path | str | None = None,
    compose_file: Path | str | None = None,
) -> StackPaths:
    resolved_opencloud_root = _resolve_existing_directory(Path(opencloud_root), "opencloud-root")
    config_directory = _require_subdirectory(resolved_opencloud_root, "config")
    data_directory = _require_subdirectory(resolved_opencloud_root, "data")

    compose_directory = (
        resolved_opencloud_root
        if compose_dir is None
        else _resolve_existing_directory(Path(compose_dir), "compose-dir")
    )

    explicit_compose_file = Path(compose_file) if compose_file is not None else None
    resolved_compose_file = resolve_compose_file(compose_directory, explicit_compose_file)

    _require_directory_read_access(config_directory, "config")
    _require_directory_read_access(data_directory, "data")
    _require_directory_read_access(compose_directory, "compose-dir")
    _require_file_read_access(resolved_compose_file, "compose-file")

    return StackPaths(
        opencloud_root=resolved_opencloud_root,
        config_dir=config_directory.resolve(),
        data_dir=data_directory.resolve(),
        compose_dir=compose_directory.resolve(),
        compose_file=resolved_compose_file,
    )
