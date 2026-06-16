from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from opencloud_backup.config import StackPaths
from opencloud_backup.domain.archive import CompressionFormat
from opencloud_backup.domain.prereqs import (
    COMPRESSION_BINARIES,
    DiskCheckResult,
    DiskThreshold,
    JobMode,
    PrerequisiteReport,
    required_binaries,
    stack_paths_requiring_write_check,
)

COMPOSE_VERSION_COMMAND: tuple[str, ...] = ("docker", "compose", "version")
COMPOSE_COMMAND_LABEL = "docker compose version"
COMPOSE_TIMEOUT_SECONDS = 10

DOCKER_PS_COMMAND: tuple[str, ...] = ("docker", "ps")
DOCKER_PS_COMMAND_LABEL = "docker ps"
DOCKER_PS_TIMEOUT_SECONDS = 10

ENV_READ_ACCESS_LABEL = "read access: .env"
CONFIG_WRITE_ACCESS_LABEL = "write access: config/"
DATA_WRITE_ACCESS_LABEL = "write access: data/"

DiskUsageResult = tuple[int, int, int]


@dataclass
class HostProbe:
    which: Callable[[str], str | None] = shutil.which
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
    disk_usage: Callable[[str | os.PathLike[str]], DiskUsageResult] = shutil.disk_usage


def check_binaries(
    mode: JobMode,
    probe: HostProbe,
    *,
    compression: CompressionFormat | None = None,
) -> tuple[str, ...]:
    missing_binary_names: list[str] = []
    for binary_name in required_binaries(mode):
        if probe.which(binary_name) is None:
            missing_binary_names.append(binary_name)
    if compression is None:
        if not any(probe.which(compression_binary) for compression_binary in COMPRESSION_BINARIES):
            missing_binary_names.extend(["zstd", "gzip"])
    elif compression == CompressionFormat.ZSTD and probe.which("zstd") is None:
        missing_binary_names.append("zstd")
    elif compression == CompressionFormat.GZIP and probe.which("gzip") is None:
        missing_binary_names.append("gzip")
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


def check_docker_ps(missing_binaries: tuple[str, ...], probe: HostProbe) -> tuple[str, ...]:
    if "docker" in missing_binaries:
        return ()
    try:
        completed_process = probe.run_command(
            list(DOCKER_PS_COMMAND),
            capture_output=True,
            text=True,
            timeout=DOCKER_PS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return (DOCKER_PS_COMMAND_LABEL,)
    if completed_process.returncode != 0:
        return (DOCKER_PS_COMMAND_LABEL,)
    return ()


def check_stack_path_access(stack_paths: StackPaths, mode: JobMode) -> tuple[str, ...]:
    access_failures: list[str] = []
    env_file_path = stack_paths.opencloud_root / ".env"
    if env_file_path.is_file() and not os.access(env_file_path, os.R_OK):
        access_failures.append(ENV_READ_ACCESS_LABEL)
    if stack_paths_requiring_write_check(mode):
        if not os.access(stack_paths.config_dir, os.W_OK):
            access_failures.append(CONFIG_WRITE_ACCESS_LABEL)
        if not os.access(stack_paths.data_dir, os.W_OK):
            access_failures.append(DATA_WRITE_ACCESS_LABEL)
    return tuple(access_failures)


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
    stack_paths: StackPaths,
    disk_check_path: Path,
    disk_threshold: DiskThreshold | None = None,
    compression: CompressionFormat | None = None,
    probe: HostProbe | None = None,
) -> PrerequisiteReport:
    host_probe = probe if probe is not None else HostProbe()
    missing_binaries = check_binaries(mode, host_probe, compression=compression)
    failed_commands: list[str] = []
    failed_commands.extend(check_docker_compose(missing_binaries, host_probe))
    failed_commands.extend(check_docker_ps(missing_binaries, host_probe))
    failed_commands.extend(check_stack_path_access(stack_paths, mode))
    disk_check_result = check_disk(disk_check_path, disk_threshold, host_probe)
    checks_ok = not missing_binaries and not failed_commands and disk_check_result.ok
    return PrerequisiteReport(
        ok=checks_ok,
        mode=mode,
        missing_binaries=missing_binaries,
        failed_commands=tuple(failed_commands),
        disk=disk_check_result,
    )
