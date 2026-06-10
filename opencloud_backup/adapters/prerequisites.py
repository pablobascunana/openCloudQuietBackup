from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from opencloud_backup.domain.prereqs import (
    COMPRESSION_BINARIES,
    DiskCheckResult,
    DiskThreshold,
    JobMode,
    PrerequisiteReport,
    required_binaries,
)

COMPOSE_VERSION_COMMAND: tuple[str, ...] = ("docker", "compose", "version")
COMPOSE_COMMAND_LABEL = "docker compose version"
COMPOSE_TIMEOUT_SECONDS = 10

DiskUsageResult = tuple[int, int, int]


@dataclass
class HostProbe:
    which: Callable[[str], str | None] = shutil.which
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
    disk_usage: Callable[[str | os.PathLike[str]], DiskUsageResult] = shutil.disk_usage


def check_binaries(mode: JobMode, probe: HostProbe) -> tuple[str, ...]:
    missing_binary_names: list[str] = []
    for binary_name in required_binaries(mode):
        if probe.which(binary_name) is None:
            missing_binary_names.append(binary_name)
    if not any(probe.which(compression_binary) for compression_binary in COMPRESSION_BINARIES):
        missing_binary_names.extend(["zstd", "gzip"])
    return tuple(missing_binary_names)


def check_docker_compose(missing_binaries: tuple[str, ...], probe: HostProbe) -> tuple[str, ...]:
    if "docker" in missing_binaries:
        return ()
    try:
        completed_process = probe.run_command(
            list(COMPOSE_VERSION_COMMAND),
            capture_output=True,
            text=True,
            timeout=COMPOSE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return (COMPOSE_COMMAND_LABEL,)
    if completed_process.returncode != 0:
        return (COMPOSE_COMMAND_LABEL,)
    return ()


def check_disk(
    disk_check_path: Path,
    disk_threshold: DiskThreshold | None,
    probe: HostProbe,
) -> DiskCheckResult:
    disk_usage_result = probe.disk_usage(disk_check_path)
    total_bytes, _used_bytes, free_bytes = disk_usage_result
    if disk_threshold is None:
        return DiskCheckResult(
            path=disk_check_path,
            total_bytes=total_bytes,
            free_bytes=free_bytes,
            threshold=None,
            ok=True,
        )
    if disk_threshold.kind == "bytes":
        disk_ok = free_bytes >= disk_threshold.value
    elif total_bytes == 0:
        disk_ok = False
    else:
        free_percent = (free_bytes / total_bytes) * 100.0
        disk_ok = free_percent >= disk_threshold.value
    return DiskCheckResult(
        path=disk_check_path,
        total_bytes=total_bytes,
        free_bytes=free_bytes,
        threshold=disk_threshold,
        ok=disk_ok,
    )


def run_prerequisite_checks(
    *,
    mode: JobMode,
    disk_check_path: Path,
    disk_threshold: DiskThreshold | None = None,
    probe: HostProbe | None = None,
) -> PrerequisiteReport:
    host_probe = probe if probe is not None else HostProbe()
    missing_binaries = check_binaries(mode, host_probe)
    failed_commands = check_docker_compose(missing_binaries, host_probe)
    disk_check_result = check_disk(disk_check_path, disk_threshold, host_probe)
    checks_ok = not missing_binaries and not failed_commands and disk_check_result.ok
    return PrerequisiteReport(
        ok=checks_ok,
        mode=mode,
        missing_binaries=missing_binaries,
        failed_commands=failed_commands,
        disk=disk_check_result,
    )
