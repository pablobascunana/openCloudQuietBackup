from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

ARCHIVE_FORMAT_VERSION = 1
ARCHIVE_BASENAME_PREFIX = "opencloud"
ARCHIVE_INTERNAL_PREFIX = "opencloud/"
TAR_TRANSFORM = "s,^,opencloud/,"


class CompressionFormat(str, Enum):
    ZSTD = "zstd"
    GZIP = "gzip"
    NONE = "none"


def default_backup_output_dir(opencloud_root: Path) -> Path:
    return opencloud_root / "backups"


def archive_filename(*, timestamp: datetime, compression: CompressionFormat) -> str:
    stem = f"{ARCHIVE_BASENAME_PREFIX}-{timestamp.strftime('%Y-%m-%d_%H%M%S')}"
    if compression == CompressionFormat.ZSTD:
        return f"{stem}.tar.zst"
    if compression == CompressionFormat.GZIP:
        return f"{stem}.tar.gz"
    return f"{stem}.tar"


def archive_output_path(
    output_dir: Path,
    *,
    timestamp: datetime,
    compression: CompressionFormat,
) -> Path:
    return output_dir / archive_filename(timestamp=timestamp, compression=compression)


def tar_member_paths(*, include_env: bool) -> tuple[str, ...]:
    if include_env:
        return ("config", "data", ".env")
    return ("config", "data")


def resolve_tar_members(opencloud_root: Path, *, include_env: bool) -> tuple[str, ...]:
    if include_env and (opencloud_root / ".env").is_file():
        return ("config", "data", ".env")
    return ("config", "data")
