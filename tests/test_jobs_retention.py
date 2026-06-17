from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from conftest import make_backup_archive_name
from opencloud_backup.domain.errors import RetentionError
from opencloud_backup.domain.integrity import sidecar_path_for_archive
from opencloud_backup.domain.retention import RetentionPolicy
from opencloud_backup.jobs.retention import RetentionResult, run_retention_job


class FakeRetentionDeleter:
    def __init__(self, *, fail_on: Path | None = None) -> None:
        self.deleted: list[Path] = []
        self.fail_on = fail_on

    def delete_file(self, path: Path) -> None:
        if self.fail_on is not None and path == self.fail_on:
            raise RetentionError(path)
        self.deleted.append(path)


def test_run_retention_job_inactive_policy_is_no_op() -> None:
    inactive_policy = RetentionPolicy(max_age_days=None, max_count=None)
    with patch("opencloud_backup.jobs.retention.list_backup_archives") as mock_list:
        result = run_retention_job(
            output_dir=Path("/backups"),
            policy=inactive_policy,
        )
    assert result == RetentionResult((), ())
    mock_list.assert_not_called()


def test_run_retention_job_deletes_archive_and_sidecar(tmp_path: Path) -> None:
    archive_name = make_backup_archive_name(datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc))
    archive_path = tmp_path / archive_name
    archive_path.write_bytes(b"archive")
    sidecar_path = sidecar_path_for_archive(archive_path)
    sidecar_path.write_text("digest\n", encoding="utf-8")
    keep_name = make_backup_archive_name(datetime(2026, 6, 17, 10, 0, 0, tzinfo=timezone.utc))
    (tmp_path / keep_name).write_bytes(b"keep")

    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    policy = RetentionPolicy(max_age_days=None, max_count=1)
    log_lines: list[str] = []
    result = run_retention_job(
        output_dir=tmp_path,
        policy=policy,
        now=now,
        stderr_log=log_lines.append,
    )

    assert not archive_path.exists()
    assert not sidecar_path.exists()
    assert (tmp_path / keep_name).exists()
    assert result.deleted_archives == (archive_path,)
    assert result.deleted_sidecars == (sidecar_path,)
    assert any("retention: deleting " + archive_name in line for line in log_lines)
    assert any("retention: deleting sidecar" in line for line in log_lines)


def test_run_retention_job_leaves_orphan_sidecar_when_archive_kept(tmp_path: Path) -> None:
    keep_name = make_backup_archive_name(datetime(2026, 6, 17, 10, 0, 0, tzinfo=timezone.utc))
    keep_path = tmp_path / keep_name
    keep_path.write_bytes(b"keep")
    orphan_sidecar = tmp_path / "opencloud-2026-01-01_120000.tar.zst.sha256"
    orphan_sidecar.write_text("orphan\n", encoding="utf-8")

    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    policy = RetentionPolicy(max_age_days=None, max_count=1)
    result = run_retention_job(
        output_dir=tmp_path,
        policy=policy,
        now=now,
    )

    assert result.deleted_archives == ()
    assert orphan_sidecar.exists()


def test_run_retention_job_delete_failure_aborts(tmp_path: Path) -> None:
    old_name = make_backup_archive_name(datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc))
    old_path = tmp_path / old_name
    old_path.write_bytes(b"old")
    newer_name = make_backup_archive_name(datetime(2026, 6, 10, 10, 0, 0, tzinfo=timezone.utc))
    newer_path = tmp_path / newer_name
    newer_path.write_bytes(b"newer")

    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    policy = RetentionPolicy(max_age_days=None, max_count=1)
    fake_deleter = FakeRetentionDeleter(fail_on=old_path)

    with pytest.raises(RetentionError):
        run_retention_job(
            output_dir=tmp_path,
            policy=policy,
            now=now,
            deleter=fake_deleter,
        )

    assert old_path.exists()
    assert newer_path.exists()


def test_run_retention_job_logs_phase_started_and_finished(tmp_path: Path) -> None:
    keep_name = make_backup_archive_name(datetime(2026, 6, 17, 10, 0, 0, tzinfo=timezone.utc))
    (tmp_path / keep_name).write_bytes(b"keep")
    log_lines: list[str] = []
    policy = RetentionPolicy(max_age_days=30, max_count=5)

    run_retention_job(
        output_dir=tmp_path,
        policy=policy,
        now=datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc),
        stderr_log=log_lines.append,
    )

    assert any("retention: phase started" in line for line in log_lines)
    assert any("retention: phase finished" in line for line in log_lines)


def test_run_retention_job_protect_archive_is_forwarded(tmp_path: Path) -> None:
    protected_name = make_backup_archive_name(datetime(2026, 6, 17, 10, 0, 0, tzinfo=timezone.utc))
    protected_path = tmp_path / protected_name
    protected_path.write_bytes(b"protected")
    older_name = make_backup_archive_name(datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc))
    older_path = tmp_path / older_name
    older_path.write_bytes(b"old")

    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    policy = RetentionPolicy(max_age_days=None, max_count=1)
    result = run_retention_job(
        output_dir=tmp_path,
        policy=policy,
        now=now,
        protect_archive=protected_path,
    )

    assert protected_path.exists()
    assert not older_path.exists()
    assert result.deleted_archives == (older_path,)
