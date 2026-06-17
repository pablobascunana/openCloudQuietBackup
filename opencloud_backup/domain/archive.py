from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from pathlib import Path

from opencloud_backup.config import ValidationError

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


REQUIRED_ARCHIVE_MEMBER_DIRS: tuple[str, ...] = ("opencloud/config", "opencloud/data")
ARCHIVE_ENV_MEMBER: str = "opencloud/.env"
RESTORE_STAGING_DIR_PREFIX: str = ".restore-staging-"


def detect_compression_format(archive_path: Path) -> CompressionFormat:
    name = archive_path.name
    if name.endswith(".tar.zst"):
        return CompressionFormat.ZSTD
    if name.endswith(".tar.gz"):
        return CompressionFormat.GZIP
    if name.endswith(".tar"):
        return CompressionFormat.NONE
    raise ValidationError(
        "Formato de archivo no soportado: se esperaba una extensión .tar.zst, .tar.gz o .tar."
    )


def normalize_tar_member_line(line: str) -> str:
    normalized = line.rstrip("\n")
    if normalized.startswith("./"):
        return normalized[2:]
    return normalized


def member_indicates_directory(members: Sequence[str], dir_path: str) -> bool:
    directory_marker = f"{dir_path}/"
    if directory_marker in members:
        return True
    prefix = f"{dir_path}/"
    return any(member.startswith(prefix) for member in members)


def listing_contains_env_member(members: Sequence[str]) -> bool:
    normalized_members = tuple(normalize_tar_member_line(member) for member in members)
    if ARCHIVE_ENV_MEMBER in normalized_members:
        return True
    env_prefix = f"{ARCHIVE_ENV_MEMBER}/"
    return any(member.startswith(env_prefix) for member in normalized_members)


def validate_archive_listing(members: Sequence[str]) -> None:
    normalized_members = tuple(normalize_tar_member_line(member) for member in members)
    missing_dirs: list[str] = []
    for required_dir in REQUIRED_ARCHIVE_MEMBER_DIRS:
        if not member_indicates_directory(normalized_members, required_dir):
            missing_dirs.append(required_dir)
    if missing_dirs:
        missing_list = ", ".join(missing_dirs)
        raise ValidationError(
            f"El archivo de backup no contiene los directorios requeridos: {missing_list}."
        )


def resolve_staging_dir(opencloud_root: Path, timestamp: datetime) -> Path:
    staging_name = f"{RESTORE_STAGING_DIR_PREFIX}{timestamp.strftime('%Y-%m-%d_%H%M%S')}"
    return opencloud_root / staging_name
