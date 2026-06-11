from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

COMPRESSION_BINARIES: tuple[str, ...] = ("zstd", "gzip")


class JobMode(str, Enum):
    BACKUP = "backup"
    RESTORE = "restore"
    ALL = "all"


@dataclass(frozen=True, slots=True)
class DiskThreshold:
    kind: Literal["bytes", "percent"]
    value: int


@dataclass(frozen=True, slots=True)
class DiskCheckResult:
    path: Path
    total_bytes: int
    free_bytes: int
    threshold: DiskThreshold | None
    ok: bool

    @property
    def free_percent(self) -> float:
        if self.total_bytes == 0:
            return 0.0
        return (self.free_bytes / self.total_bytes) * 100.0


@dataclass(frozen=True, slots=True)
class PrerequisiteReport:
    ok: bool
    mode: JobMode
    missing_binaries: tuple[str, ...]
    failed_commands: tuple[str, ...]
    disk: DiskCheckResult | None


def required_binaries(mode: JobMode) -> tuple[str, ...]:
    base_binaries: tuple[str, ...] = ("docker", "tar")
    if mode in (JobMode.RESTORE, JobMode.ALL):
        return base_binaries + ("rsync",)
    return base_binaries


def stack_paths_requiring_write_check(mode: JobMode) -> bool:
    return mode in (JobMode.RESTORE, JobMode.ALL)
