from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from opencloud_backup.config import ValidationError

BACKUP_ARCHIVE_NAME_RE = re.compile(
    r"^opencloud-\d{4}-\d{2}-\d{2}_\d{6}\.tar(\.zst|\.gz)?$"
)
_TIMESTAMP_STEM_RE = re.compile(
    r"^opencloud-(?P<stem>\d{4}-\d{2}-\d{2}_\d{6})\.tar"
)
_SECONDS_PER_DAY = 86_400


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    max_age_days: int | None = None
    max_count: int | None = None

    @property
    def is_active(self) -> bool:
        return self.max_age_days is not None or self.max_count is not None


def is_backup_archive_name(name: str) -> bool:
    return BACKUP_ARCHIVE_NAME_RE.fullmatch(name) is not None


def parse_backup_timestamp_from_name(name: str) -> datetime | None:
    match = _TIMESTAMP_STEM_RE.match(name)
    if match is None:
        return None
    try:
        parsed_timestamp = datetime.strptime(match.group("stem"), "%Y-%m-%d_%H%M%S")
    except ValueError:
        return None
    return parsed_timestamp.replace(tzinfo=timezone.utc)


def archive_age_days(archive_timestamp: datetime, now: datetime) -> int:
    archive_utc = archive_timestamp.astimezone(timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    delta_seconds = (now_utc - archive_utc).total_seconds()
    if delta_seconds < 0:
        return 0
    return int(delta_seconds // _SECONDS_PER_DAY)


def build_retention_policy(
    *,
    max_age_days: int | None,
    max_count: int | None,
) -> RetentionPolicy:
    if max_age_days is not None and max_age_days < 1:
        raise ValidationError("keep-days debe ser al menos 1")
    if max_count is not None and max_count < 1:
        raise ValidationError("keep-count debe ser al menos 1")
    return RetentionPolicy(max_age_days=max_age_days, max_count=max_count)


def select_archives_for_deletion(
    candidates: Sequence[Path],
    policy: RetentionPolicy,
    *,
    now: datetime,
    protect_archive: Path | None = None,
) -> tuple[Path, ...]:
    if not policy.is_active:
        return ()

    parsed_candidates: list[tuple[Path, datetime]] = []
    for candidate_path in candidates:
        parsed_timestamp = parse_backup_timestamp_from_name(candidate_path.name)
        if parsed_timestamp is None:
            continue
        parsed_candidates.append((candidate_path, parsed_timestamp))

    parsed_candidates.sort(key=lambda item: (-item[1].timestamp(), item[0].name))

    protected_path = protect_archive.resolve() if protect_archive is not None else None
    to_delete: list[Path] = []
    for index, (candidate_path, parsed_timestamp) in enumerate(parsed_candidates):
        if protected_path is not None and candidate_path.resolve() == protected_path:
            continue
        age_days = archive_age_days(parsed_timestamp, now)
        too_old = policy.max_age_days is not None and age_days > policy.max_age_days
        beyond_count = policy.max_count is not None and index >= policy.max_count
        if too_old or beyond_count:
            to_delete.append(candidate_path)

    return tuple(
        sorted(
            to_delete,
            key=lambda path: (
                parse_backup_timestamp_from_name(path.name) or datetime.min.replace(tzinfo=timezone.utc),
                path.name,
            ),
        )
    )
