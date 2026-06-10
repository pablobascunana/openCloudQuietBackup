from __future__ import annotations

import subprocess
from pathlib import Path

from opencloud_backup.adapters.prerequisites import (
    COMPOSE_COMMAND_LABEL,
    HostProbe,
    check_binaries,
    check_disk,
    check_docker_compose,
    run_prerequisite_checks,
)
from opencloud_backup.domain.prereqs import DiskThreshold, JobMode


def _make_probe(
    *,
    which_map: dict[str, str | None] | None = None,
    compose_returncode: int = 0,
    compose_raises: bool = False,
    disk_usage_result: tuple[int, int, int] = (100 * 1024**3, 10 * 1024**3, 90 * 1024**3),
) -> HostProbe:
    which_map = which_map or {}

    def which(binary_name: str) -> str | None:
        return which_map.get(binary_name)

    def run_command(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
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
    report = run_prerequisite_checks(
        mode=JobMode.ALL,
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
    report = run_prerequisite_checks(mode=JobMode.ALL, disk_check_path=Path("/data"), probe=probe)
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
    report = run_prerequisite_checks(mode=JobMode.BACKUP, disk_check_path=Path("/data"), probe=probe)
    assert report.ok


def test_docker_compose_version_fails() -> None:
    probe = _make_probe(which_map=_all_tools_which_map(), compose_returncode=1)
    failed = check_docker_compose((), probe)
    assert failed == (COMPOSE_COMMAND_LABEL,)
    report = run_prerequisite_checks(mode=JobMode.ALL, disk_check_path=Path("/data"), probe=probe)
    assert not report.ok
    assert COMPOSE_COMMAND_LABEL in report.failed_commands


def test_docker_compose_skipped_when_docker_missing() -> None:
    which_map = _all_tools_which_map()
    which_map["docker"] = None
    probe = _make_probe(which_map=which_map)
    failed = check_docker_compose(("docker",), probe)
    assert failed == ()


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


def test_gzip_only_compression_ok() -> None:
    which_map = _all_tools_which_map()
    which_map["zstd"] = None
    probe = _make_probe(which_map=which_map)
    missing = check_binaries(JobMode.BACKUP, probe)
    assert "zstd" not in missing
    assert "gzip" not in missing


def test_no_compression_both_missing() -> None:
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
