from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import make_backup_archive_name
from opencloud_backup.config import ValidationError
from opencloud_backup.domain.retention import (
    RetentionPolicy,
    archive_age_days,
    build_retention_policy,
    is_backup_archive_name,
    parse_backup_timestamp_from_name,
    select_archives_for_deletion,
)


def test_is_backup_archive_name_accepts_canonical_suffixes() -> None:
    assert is_backup_archive_name("opencloud-2026-06-14_101530.tar.zst")
    assert is_backup_archive_name("opencloud-2026-06-14_101530.tar.gz")
    assert is_backup_archive_name("opencloud-2026-06-14_101530.tar")


def test_is_backup_archive_name_rejects_non_canonical_names() -> None:
    assert not is_backup_archive_name("manual.tar")
    assert not is_backup_archive_name("opencloud-backup.tar.zst")
    assert not is_backup_archive_name("opencloud-2026-01-01.tar.zst")
    assert not is_backup_archive_name("opencloud-2026-01-01_120000.tar.zst.sha256")


def test_parse_backup_timestamp_from_name_round_trip() -> None:
    timestamp = datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc)
    parsed = parse_backup_timestamp_from_name(make_backup_archive_name(timestamp))
    assert parsed == timestamp


def test_parse_backup_timestamp_from_name_invalid_returns_none() -> None:
    assert parse_backup_timestamp_from_name("opencloud-not-a-date_120000.tar.zst") is None


def test_archive_age_days_same_day_is_zero() -> None:
    timestamp = datetime(2026, 6, 17, 10, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 6, 17, 20, 0, 0, tzinfo=timezone.utc)
    assert archive_age_days(timestamp, now) == 0


def test_archive_age_days_25_hours_is_one() -> None:
    timestamp = datetime(2026, 6, 16, 11, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    assert archive_age_days(timestamp, now) == 1


def test_build_retention_policy_rejects_keep_days_zero() -> None:
    with pytest.raises(ValidationError, match="keep-days debe ser al menos 1"):
        build_retention_policy(max_age_days=0, max_count=None)


def test_build_retention_policy_rejects_keep_count_zero() -> None:
    with pytest.raises(ValidationError, match="keep-count debe ser al menos 1"):
        build_retention_policy(max_age_days=None, max_count=0)


def test_select_archives_for_deletion_keep_count_only() -> None:
    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    candidates = (
        Path(f"/backups/{make_backup_archive_name(datetime(2026, 6, 17, 10, 0, 0, tzinfo=timezone.utc))}"),
        Path(f"/backups/{make_backup_archive_name(datetime(2026, 6, 10, 10, 0, 0, tzinfo=timezone.utc))}"),
        Path(f"/backups/{make_backup_archive_name(datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc))}"),
    )
    policy = RetentionPolicy(max_age_days=None, max_count=2)
    deleted = select_archives_for_deletion(candidates, policy, now=now)
    assert len(deleted) == 1
    assert deleted[0].name == candidates[2].name


def test_select_archives_for_deletion_keep_days_strict_gt() -> None:
    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    exactly_seven_days = Path(
        f"/backups/{make_backup_archive_name(datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc))}"
    )
    older = Path(
        f"/backups/{make_backup_archive_name(datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc))}"
    )
    policy = RetentionPolicy(max_age_days=7, max_count=None)
    deleted = select_archives_for_deletion((exactly_seven_days, older), policy, now=now)
    assert deleted == (older,)


def test_select_archives_for_deletion_both_limits() -> None:
    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    newest = Path(
        f"/backups/{make_backup_archive_name(datetime(2026, 6, 17, 10, 0, 0, tzinfo=timezone.utc))}"
    )
    middle = Path(
        f"/backups/{make_backup_archive_name(datetime(2026, 6, 10, 10, 0, 0, tzinfo=timezone.utc))}"
    )
    oldest = Path(
        f"/backups/{make_backup_archive_name(datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc))}"
    )
    policy = RetentionPolicy(max_age_days=7, max_count=1)
    deleted = select_archives_for_deletion((newest, middle, oldest), policy, now=now)
    assert [path.name for path in deleted] == [oldest.name, middle.name]


def test_select_archives_for_deletion_respects_protect_archive() -> None:
    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    protected = Path(
        f"/backups/{make_backup_archive_name(datetime(2026, 6, 17, 10, 0, 0, tzinfo=timezone.utc))}"
    )
    older = Path(
        f"/backups/{make_backup_archive_name(datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc))}"
    )
    policy = RetentionPolicy(max_age_days=None, max_count=1)
    deleted = select_archives_for_deletion(
        (protected, older),
        policy,
        now=now,
        protect_archive=protected,
    )
    assert deleted == (older,)
