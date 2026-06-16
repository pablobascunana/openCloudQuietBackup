from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from opencloud_backup.domain.archive import ARCHIVE_FORMAT_VERSION
from opencloud_backup.domain.errors import IntegrityError

SIDECAR_FORMAT_VERSION: int = 1
SIDECAR_SUFFIX: str = ".sha256"

_GNU_DIGEST_LINE_RE = re.compile(r"^([0-9a-f]{64})  (\S+)$")
_COMMENT_LINE_RE = re.compile(r"^# ([a-z_]+)=(.+)$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)


def sidecar_path_for_archive(archive_path: Path) -> Path:
    return Path(f"{archive_path}{SIDECAR_SUFFIX}")


@dataclass(frozen=True, slots=True)
class IntegrityRecord:
    sha256_hex: str
    archive_basename: str
    size_bytes: int
    recorded_at: datetime
    format_version: int
    archive_format_version: int


def _validate_sha256_hex(value: str) -> str:
    if _SHA256_HEX_RE.fullmatch(value) is None:
        raise IntegrityError("Invalid SHA-256 hex digest")
    return value


def _parse_comment_line(line: str) -> tuple[str, str] | None:
    match = _COMMENT_LINE_RE.match(line)
    if match is None:
        return None
    return match.group(1), match.group(2)


def format_sidecar(record: IntegrityRecord) -> str:
    lines = [
        f"{record.sha256_hex}  {record.archive_basename}",
        f"# format_version={record.format_version}",
        f"# size_bytes={record.size_bytes}",
        f"# recorded_at={record.recorded_at.isoformat()}",
        f"# archive_format_version={record.archive_format_version}",
    ]
    return "\n".join(lines) + "\n"


def parse_sidecar(content: str) -> IntegrityRecord:
    stripped_content = content.rstrip("\n")
    if stripped_content == "":
        raise IntegrityError("Sidecar file is empty")

    lines = stripped_content.split("\n")
    match = _GNU_DIGEST_LINE_RE.match(lines[0])
    if match is None:
        raise IntegrityError("Invalid sidecar digest line")

    sha256_hex = _validate_sha256_hex(match.group(1))
    archive_basename = match.group(2)

    format_version = SIDECAR_FORMAT_VERSION
    archive_format_version = ARCHIVE_FORMAT_VERSION
    size_bytes = 0
    recorded_at = _EPOCH_UTC

    for line in lines[1:]:
        if not line.startswith("#"):
            continue
        parsed_comment = _parse_comment_line(line)
        if parsed_comment is None:
            continue
        key, value = parsed_comment
        if key == "format_version":
            format_version = int(value)
        elif key == "size_bytes":
            try:
                size_bytes = int(value)
            except ValueError as parse_error:
                raise IntegrityError("Invalid size_bytes in sidecar") from parse_error
            if size_bytes < 0:
                raise IntegrityError("Invalid size_bytes in sidecar")
        elif key == "recorded_at":
            recorded_at = datetime.fromisoformat(value)
        elif key == "archive_format_version":
            archive_format_version = int(value)

    return IntegrityRecord(
        sha256_hex=sha256_hex,
        archive_basename=archive_basename,
        size_bytes=size_bytes,
        recorded_at=recorded_at,
        format_version=format_version,
        archive_format_version=archive_format_version,
    )
