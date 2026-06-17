from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from opencloud_backup.config import StackPaths, ValidationError

SNAPSHOT_SUBDIR_PREFIX = "pre-restore"


def default_snapshot_base_dir(opencloud_root: Path) -> Path:
    return opencloud_root / "snapshots"


def snapshot_subdirectory_name(timestamp: datetime) -> str:
    return f"{SNAPSHOT_SUBDIR_PREFIX}-{timestamp.strftime('%Y-%m-%d_%H%M%S')}"


def default_disk_check_path_for_snapshot_base(snapshot_base: Path) -> Path:
    parent = snapshot_base.parent
    if parent == snapshot_base:
        return snapshot_base
    return parent


@dataclass(frozen=True, slots=True)
class SnapshotPaths:
    target_dir: Path
    config_src: Path
    data_src: Path
    env_src: Path | None


def resolve_snapshot_paths(
    base_dir: Path,
    timestamp: datetime,
    stack_paths: StackPaths,
    *,
    include_env: bool,
) -> SnapshotPaths:
    target_dir = base_dir / snapshot_subdirectory_name(timestamp)
    env_src: Path | None = None
    if include_env:
        env_path = stack_paths.opencloud_root / ".env"
        if env_path.exists():
            if not env_path.is_file():
                raise ValidationError(
                    f"Path «.env» exists but is not a regular file: {env_path}"
                )
            env_src = env_path
    return SnapshotPaths(
        target_dir=target_dir,
        config_src=stack_paths.config_dir,
        data_src=stack_paths.data_dir,
        env_src=env_src,
    )


def apply_replace_policy(base_dir: Path, *, keep_previous: bool) -> None:
    if keep_previous:
        return
    for entry in base_dir.glob(f"{SNAPSHOT_SUBDIR_PREFIX}-*"):
        if entry.is_dir():
            shutil.rmtree(entry)
