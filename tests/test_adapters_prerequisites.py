from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from opencloud_backup.adapters.prerequisites import (
    COMPOSE_COMMAND_LABEL,
    CONFIG_WRITE_ACCESS_LABEL,
    DATA_WRITE_ACCESS_LABEL,
    DOCKER_PS_COMMAND_LABEL,
    ENV_READ_ACCESS_LABEL,
    HostProbe,
    check_binaries,
    check_disk,
    check_docker_compose,
    check_docker_ps,
    check_stack_path_access,
    run_prerequisite_checks,
)
from opencloud_backup.config import StackPaths
from opencloud_backup.domain.archive import CompressionFormat
from opencloud_backup.domain.prereqs import DiskThreshold, JobMode


def _stack_paths(root: Path) -> StackPaths:
    return StackPaths(
        opencloud_root=root,
        config_dir=root / "config",
        data_dir=root / "data",
        compose_dir=root,
        compose_file=root / "docker-compose.yml",
    )


def _make_probe(
    *,
    which_map: dict[str, str | None] | None = None,
    compose_returncode: int = 0,
    compose_raises: bool = False,
    ps_returncode: int = 0,
    ps_raises: bool = False,
    disk_usage_result: tuple[int, int, int] = (100 * 1024**3, 10 * 1024**3, 90 * 1024**3),
) -> HostProbe:
    which_map = which_map or {}

    def which(binary_name: str) -> str | None:
        return which_map.get(binary_name)

    def run_command(command_argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command_argv == ["docker", "ps"]:
            if ps_raises:
                raise OSError("docker ps failed")
            return subprocess.CompletedProcess(
                args=["docker", "ps"],
                returncode=ps_returncode,
                stdout="",
                stderr="",
            )
        if compose_raises:
            raise OSError("compose failed")
        return subprocess.CompletedProcess(
            args=["docker", "compose", "version"],
            returncode=compose_returncode,
            stdout="Docker Compose version v2.0.0\n",
            stderr="",
        )

    def disk_usage(_path: str | Path) -> tuple[int, int, int]:
        return disk_usage_result

    return HostProbe(which=which, run_command=run_command, disk_usage=disk_usage)


def _all_tools_which_map() -> dict[str, str | None]:
    return {
        "docker": "/usr/bin/docker",
        "tar": "/usr/bin/tar",
        "zstd": "/usr/bin/zstd",
        "gzip": "/usr/bin/gzip",
        "rsync": "/usr/bin/rsync",
    }


def test_happy_path_mode_all() -> None:
    probe = _make_probe(which_map=_all_tools_which_map())
    with patch("opencloud_backup.adapters.prerequisites.os.access", return_value=True):
        report = run_prerequisite_checks(
            mode=JobMode.ALL,
            stack_paths=_stack_paths(Path("/data")),
            disk_check_path=Path("/data"),
            probe=probe,
        )
    assert report.ok
    assert report.missing_binaries == ()
    assert report.failed_commands == ()


def test_missing_docker() -> None:
    which_map = _all_tools_which_map()
    which_map["docker"] = None
    probe = _make_probe(which_map=which_map)
    missing = check_binaries(JobMode.ALL, probe)
    assert "docker" in missing
    with patch("opencloud_backup.adapters.prerequisites.os.access", return_value=True):
        report = run_prerequisite_checks(
            mode=JobMode.ALL,
            stack_paths=_stack_paths(Path("/data")),
            disk_check_path=Path("/data"),
            probe=probe,
        )
    assert not report.ok
    assert report.failed_commands == ()


def test_missing_rsync_restore_mode() -> None:
    which_map = _all_tools_which_map()
    which_map["rsync"] = None
    probe = _make_probe(which_map=which_map)
    missing = check_binaries(JobMode.RESTORE, probe)
    assert "rsync" in missing


def test_backup_mode_ok_without_rsync() -> None:
    which_map = _all_tools_which_map()
    which_map["rsync"] = None
    probe = _make_probe(which_map=which_map)
    missing = check_binaries(JobMode.BACKUP, probe)
    assert "rsync" not in missing
    with patch("opencloud_backup.adapters.prerequisites.os.access", return_value=True):
        report = run_prerequisite_checks(
            mode=JobMode.BACKUP,
            stack_paths=_stack_paths(Path("/data")),
            disk_check_path=Path("/data"),
            probe=probe,
        )
    assert report.ok


def test_docker_compose_version_fails() -> None:
    probe = _make_probe(which_map=_all_tools_which_map(), compose_returncode=1)
    failed = check_docker_compose((), probe)
    assert failed == (COMPOSE_COMMAND_LABEL,)
    with patch("opencloud_backup.adapters.prerequisites.os.access", return_value=True):
        report = run_prerequisite_checks(
            mode=JobMode.ALL,
            stack_paths=_stack_paths(Path("/data")),
            disk_check_path=Path("/data"),
            probe=probe,
        )
    assert not report.ok
    assert COMPOSE_COMMAND_LABEL in report.failed_commands


def test_docker_compose_skipped_when_docker_missing() -> None:
    which_map = _all_tools_which_map()
    which_map["docker"] = None
    probe = _make_probe(which_map=which_map)
    failed = check_docker_compose(("docker",), probe)
    assert failed == ()


def test_docker_ps_happy_path() -> None:
    probe = _make_probe(which_map=_all_tools_which_map())
    failed = check_docker_ps((), probe)
    assert failed == ()


def test_docker_ps_fails_nonzero() -> None:
    probe = _make_probe(which_map=_all_tools_which_map(), ps_returncode=1)
    failed = check_docker_ps((), probe)
    assert failed == (DOCKER_PS_COMMAND_LABEL,)


def test_docker_ps_os_error() -> None:
    probe = _make_probe(which_map=_all_tools_which_map(), ps_raises=True)
    failed = check_docker_ps((), probe)
    assert failed == (DOCKER_PS_COMMAND_LABEL,)


def test_docker_ps_skipped_when_docker_missing() -> None:
    which_map = _all_tools_which_map()
    which_map["docker"] = None
    probe = _make_probe(which_map=which_map)
    failed = check_docker_ps(("docker",), probe)
    assert failed == ()


def test_docker_ps_in_run_prerequisite_checks() -> None:
    probe = _make_probe(which_map=_all_tools_which_map(), ps_returncode=1)
    with patch("opencloud_backup.adapters.prerequisites.os.access", return_value=True):
        report = run_prerequisite_checks(
            mode=JobMode.ALL,
            stack_paths=_stack_paths(Path("/data")),
            disk_check_path=Path("/data"),
            probe=probe,
        )
    assert not report.ok
    assert DOCKER_PS_COMMAND_LABEL in report.failed_commands


def test_stack_path_access_backup_mode_no_write_check() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        (root / "config").mkdir()
        (root / "data").mkdir()
        stack_paths = _stack_paths(root)

        def fake_access(path: Path, mode: int) -> bool:
            return not (path == stack_paths.data_dir and mode == os.W_OK)

        with patch("opencloud_backup.adapters.prerequisites.os.access", side_effect=fake_access):
            failures = check_stack_path_access(stack_paths, JobMode.BACKUP)
        assert failures == ()


def test_stack_path_access_restore_mode_write_failures() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        (root / "config").mkdir()
        (root / "data").mkdir()
        stack_paths = _stack_paths(root)

        with patch("opencloud_backup.adapters.prerequisites.os.access", return_value=False):
            failures = check_stack_path_access(stack_paths, JobMode.RESTORE)
        assert CONFIG_WRITE_ACCESS_LABEL in failures
        assert DATA_WRITE_ACCESS_LABEL in failures


def test_stack_path_access_env_read_failure() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        (root / "config").mkdir()
        (root / "data").mkdir()
        (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
        stack_paths = _stack_paths(root)

        def fake_access(path: Path, mode: int) -> bool:
            return not (path == root / ".env" and mode == os.R_OK)

        with patch("opencloud_backup.adapters.prerequisites.os.access", side_effect=fake_access):
            failures = check_stack_path_access(stack_paths, JobMode.BACKUP)
        assert failures == (ENV_READ_ACCESS_LABEL,)


def test_stack_path_access_env_absent_skipped() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        (root / "config").mkdir()
        (root / "data").mkdir()
        stack_paths = _stack_paths(root)
        with patch("opencloud_backup.adapters.prerequisites.os.access", return_value=True):
            failures = check_stack_path_access(stack_paths, JobMode.ALL)
        assert ENV_READ_ACCESS_LABEL not in failures


def test_disk_bytes_below_threshold() -> None:
    probe = _make_probe(
        which_map=_all_tools_which_map(),
        disk_usage_result=(100 * 1024**3, 95 * 1024**3, 5 * 1024**3),
    )
    threshold = DiskThreshold(kind="bytes", value=10 * 1024**3)
    disk_result = check_disk(Path("/data"), threshold, probe)
    assert not disk_result.ok


def test_disk_percent_below_threshold() -> None:
    probe = _make_probe(
        which_map=_all_tools_which_map(),
        disk_usage_result=(100 * 1024**3, 99 * 1024**3, 1 * 1024**3),
    )
    threshold = DiskThreshold(kind="percent", value=5)
    disk_result = check_disk(Path("/data"), threshold, probe)
    assert not disk_result.ok


def test_compression_none_skips_zstd_and_gzip() -> None:
    which_map = _all_tools_which_map()
    which_map["zstd"] = None
    which_map["gzip"] = None
    probe = _make_probe(which_map=which_map)
    missing = check_binaries(JobMode.BACKUP, probe, compression=CompressionFormat.NONE)
    assert "zstd" not in missing
    assert "gzip" not in missing


def test_compression_zstd_requires_zstd_only() -> None:
    which_map = _all_tools_which_map()
    which_map["zstd"] = None
    which_map["gzip"] = "/usr/bin/gzip"
    probe = _make_probe(which_map=which_map)
    missing = check_binaries(JobMode.BACKUP, probe, compression=CompressionFormat.ZSTD)
    assert missing == ("zstd",)


def test_compression_gzip_requires_gzip_only() -> None:
    which_map = _all_tools_which_map()
    which_map["zstd"] = "/usr/bin/zstd"
    which_map["gzip"] = None
    probe = _make_probe(which_map=which_map)
    missing = check_binaries(JobMode.BACKUP, probe, compression=CompressionFormat.GZIP)
    assert missing == ("gzip",)


def test_gzip_only_compression_ok_when_unspecified() -> None:
    which_map = _all_tools_which_map()
    which_map["zstd"] = None
    probe = _make_probe(which_map=which_map)
    missing = check_binaries(JobMode.BACKUP, probe)
    assert "zstd" not in missing
    assert "gzip" not in missing


def test_no_compression_both_missing_when_unspecified() -> None:
    which_map = _all_tools_which_map()
    which_map["zstd"] = None
    which_map["gzip"] = None
    probe = _make_probe(which_map=which_map)
    missing = check_binaries(JobMode.BACKUP, probe)
    assert "zstd" in missing
    assert "gzip" in missing


def test_compose_command_os_error() -> None:
    probe = _make_probe(which_map=_all_tools_which_map(), compose_raises=True)
    failed = check_docker_compose((), probe)
    assert failed == (COMPOSE_COMMAND_LABEL,)


def test_disk_no_threshold_always_ok() -> None:
    probe = _make_probe(disk_usage_result=(100, 99, 1))
    disk_result = check_disk(Path("/tiny"), None, probe)
    assert disk_result.ok


def test_run_prerequisite_checks_combines_path_and_docker_failures() -> None:
    probe = _make_probe(which_map=_all_tools_which_map(), ps_returncode=1)
    with patch("opencloud_backup.adapters.prerequisites.os.access", return_value=False):
        report = run_prerequisite_checks(
            mode=JobMode.RESTORE,
            stack_paths=_stack_paths(Path("/data")),
            disk_check_path=Path("/data"),
            probe=probe,
        )
    assert not report.ok
    assert DOCKER_PS_COMMAND_LABEL in report.failed_commands
    assert CONFIG_WRITE_ACCESS_LABEL in report.failed_commands
    assert DATA_WRITE_ACCESS_LABEL in report.failed_commands
