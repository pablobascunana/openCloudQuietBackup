from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import make_valid_stack_tree

from opencloud_backup.config import StackPaths, ValidationError
from opencloud_backup.domain.snapshot import (
    SNAPSHOT_SUBDIR_PREFIX,
    apply_replace_policy,
    default_disk_check_path_for_snapshot_base,
    default_snapshot_base_dir,
    resolve_snapshot_paths,
    snapshot_subdirectory_name,
)


def _stack_paths(root: Path) -> StackPaths:
    return StackPaths(
        opencloud_root=root,
        config_dir=root / "config",
        data_dir=root / "data",
        compose_dir=root,
        compose_file=root / "docker-compose.yml",
    )


def test_snapshot_subdirectory_name_format() -> None:
    timestamp = datetime(2026, 6, 16, 14, 30, 45, tzinfo=timezone.utc)
    assert snapshot_subdirectory_name(timestamp) == "pre-restore-2026-06-16_143045"


def test_default_snapshot_base_dir() -> None:
    root = Path("/data/opencloud")
    assert default_snapshot_base_dir(root) == Path("/data/opencloud/snapshots")


def test_default_disk_check_path_for_snapshot_base_uses_parent() -> None:
    snapshot_base = Path("/mnt/backup/snapshots")
    assert default_disk_check_path_for_snapshot_base(snapshot_base) == Path("/mnt/backup")


def test_default_disk_check_path_for_root_level_base() -> None:
    snapshot_base = Path("/snapshots")
    assert default_disk_check_path_for_snapshot_base(snapshot_base) == Path("/")


def test_resolve_snapshot_paths_with_env_file() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        make_valid_stack_tree(root)
        (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
        timestamp = datetime(2026, 6, 16, 10, 0, 0, tzinfo=timezone.utc)
        base = root / "snapshots"
        paths = resolve_snapshot_paths(base, timestamp, _stack_paths(root), include_env=True)
        assert paths.target_dir == base / "pre-restore-2026-06-16_100000"
        assert paths.config_src == root / "config"
        assert paths.data_src == root / "data"
        assert paths.env_src == root / ".env"


def test_resolve_snapshot_paths_without_env_file() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        make_valid_stack_tree(root)
        timestamp = datetime(2026, 6, 16, 10, 0, 0, tzinfo=timezone.utc)
        paths = resolve_snapshot_paths(root / "snapshots", timestamp, _stack_paths(root), include_env=True)
        assert paths.env_src is None


def test_resolve_snapshot_paths_include_env_false() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        make_valid_stack_tree(root)
        (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
        timestamp = datetime(2026, 6, 16, 10, 0, 0, tzinfo=timezone.utc)
        paths = resolve_snapshot_paths(root / "snapshots", timestamp, _stack_paths(root), include_env=False)
        assert paths.env_src is None


def test_resolve_snapshot_paths_env_directory_raises() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        make_valid_stack_tree(root)
        (root / ".env").mkdir()
        timestamp = datetime(2026, 6, 16, 10, 0, 0, tzinfo=timezone.utc)
        with pytest.raises(ValidationError, match="not a regular file"):
            resolve_snapshot_paths(root / "snapshots", timestamp, _stack_paths(root), include_env=True)


def test_apply_replace_policy_removes_pre_restore_dirs() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        base = Path(temporary_directory)
        old_snapshot = base / f"{SNAPSHOT_SUBDIR_PREFIX}-2026-01-01_000000"
        old_snapshot.mkdir()
        (old_snapshot / "config").mkdir()
        (base / f"{SNAPSHOT_SUBDIR_PREFIX}-note.txt").write_text("keep", encoding="utf-8")
        apply_replace_policy(base, keep_previous=False)
        assert not old_snapshot.exists()
        assert (base / f"{SNAPSHOT_SUBDIR_PREFIX}-note.txt").is_file()


def test_apply_replace_policy_keeps_when_flag_set() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        base = Path(temporary_directory)
        old_snapshot = base / f"{SNAPSHOT_SUBDIR_PREFIX}-2026-01-01_000000"
        old_snapshot.mkdir()
        apply_replace_policy(base, keep_previous=True)
        assert old_snapshot.is_dir()
