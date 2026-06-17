from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pytest
from conftest import make_valid_stack_tree

from opencloud_backup.config import StackPaths, ValidationError
from opencloud_backup.domain.archive import CompressionFormat, RESTORE_STAGING_DIR_PREFIX
from opencloud_backup.domain.errors import ArchiveCommandError, ComposeCommandError, PrerequisiteCheckError, RsyncCommandError
from opencloud_backup.domain.prereqs import DiskCheckResult, DiskThreshold, JobMode, PrerequisiteReport
from opencloud_backup.domain.restore import RestoreJobResult
from opencloud_backup.domain.snapshot import SNAPSHOT_SUBDIR_PREFIX
from opencloud_backup.jobs.restore import _format_phase_log_line, run_restore_job


class FakeComposeRunner:
    def __init__(
        self,
        *,
        should_fail_down: bool = False,
        should_fail_up: bool = False,
        should_fail_ps: bool = False,
    ) -> None:
        self.should_fail_down = should_fail_down
        self.should_fail_up = should_fail_up
        self.should_fail_ps = should_fail_ps
        self.down_calls: list[tuple[StackPaths, int]] = []
        self.up_calls: list[tuple[StackPaths, int]] = []
        self.ps_calls: list[StackPaths] = []

    def down(self, stack_paths: StackPaths, timeout_seconds: int) -> None:
        self.down_calls.append((stack_paths, timeout_seconds))
        if self.should_fail_down:
            raise ComposeCommandError("docker compose down", 1, "compose failed")

    def up(self, stack_paths: StackPaths, timeout_seconds: int) -> None:
        self.up_calls.append((stack_paths, timeout_seconds))
        if self.should_fail_up:
            raise ComposeCommandError("docker compose up -d", 1, "compose failed")

    def ps(self, stack_paths: StackPaths) -> str:
        self.ps_calls.append(stack_paths)
        if self.should_fail_ps:
            raise ComposeCommandError("docker ps (compose project)", 1, "compose failed")
        return "ps-ok"


class FakeTreeSyncer:
    def __init__(self, *, fail_on_label: str | None = None) -> None:
        self.fail_on_label = fail_on_label
        self.sync_calls: list[tuple[Path, Path, int | None, str, bool]] = []

    def sync_tree(
        self,
        source: Path,
        destination: Path,
        *,
        timeout_seconds: int | None = None,
        command_label: str = "rsync snapshot",
        delete: bool = False,
    ) -> None:
        if self.fail_on_label == command_label:
            raise RsyncCommandError(command_label, 1, "rsync failed")
        self.sync_calls.append((source, destination, timeout_seconds, command_label, delete))


class FakeArchiveExtractor:
    def __init__(
        self,
        *,
        members: tuple[str, ...] = ("opencloud/config/", "opencloud/data/file"),
        fail_on: Literal["list", "extract"] | None = None,
    ) -> None:
        self.members = members
        self.fail_on = fail_on
        self.list_calls: list[tuple[Path, CompressionFormat, int | None]] = []
        self.extract_calls: list[tuple[Path, Path, CompressionFormat, int | None]] = []

    def list_members(
        self,
        archive_path: Path,
        *,
        compression: CompressionFormat,
        timeout_seconds: int | None = None,
    ) -> tuple[str, ...]:
        self.list_calls.append((archive_path, compression, timeout_seconds))
        if self.fail_on == "list":
            raise ArchiveCommandError("restore archive list", 1, "list failed")
        return self.members

    def extract_archive(
        self,
        archive_path: Path,
        dest_dir: Path,
        *,
        compression: CompressionFormat,
        timeout_seconds: int | None = None,
    ) -> None:
        self.extract_calls.append((archive_path, dest_dir, compression, timeout_seconds))
        if self.fail_on == "extract":
            raise ArchiveCommandError("restore archive extract", 1, "extract failed")


def _stack_paths(root: Path) -> StackPaths:
    return StackPaths(
        opencloud_root=root,
        config_dir=root / "config",
        data_dir=root / "data",
        compose_dir=root,
        compose_file=root / "docker-compose.yml",
    )


def _archive_path(tmp_path: Path) -> Path:
    archive = tmp_path / "opencloud-2026-06-16_120000.tar.zst"
    archive.write_bytes(b"fake")
    return archive


def _job_kwargs(stack_paths: StackPaths, snapshot_base: Path, archive_path: Path, **overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "stack_paths": stack_paths,
        "archive_path": archive_path,
        "snapshot_base_dir": snapshot_base,
        "disk_check_path": stack_paths.opencloud_root,
        "stop_timeout_seconds": 180,
        "start_timeout_seconds": 180,
    }
    kwargs.update(overrides)
    return kwargs


def _ok_prereq_report() -> PrerequisiteReport:
    return PrerequisiteReport(
        ok=True,
        mode=JobMode.RESTORE,
        missing_binaries=(),
        failed_commands=(),
        disk=DiskCheckResult(
            path=Path("/data"),
            total_bytes=100,
            free_bytes=50,
            threshold=None,
            ok=True,
        ),
    )


def _failed_prereq_report() -> PrerequisiteReport:
    return PrerequisiteReport(
        ok=False,
        mode=JobMode.RESTORE,
        missing_binaries=("rsync",),
        failed_commands=(),
        disk=None,
    )


def test_format_phase_log_line_uses_utc_iso() -> None:
    fixed_now = datetime(2026, 6, 14, 10, 15, 30, 123456, tzinfo=timezone.utc)
    line = _format_phase_log_line("restore: stop phase started", now=fixed_now)
    assert line == "[2026-06-14T10:15:30.123456+00:00] restore: stop phase started"


def test_run_restore_job_happy_path_returns_result() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        (root / ".env").write_text("KEY=1\n", encoding="utf-8")
        snapshot_base = root / "snapshots"
        stack_paths = _stack_paths(root)
        archive_path = _archive_path(Path(temporary_directory))
        fake_runner = FakeComposeRunner()
        fake_syncer = FakeTreeSyncer()
        fake_extractor = FakeArchiveExtractor(
            members=("opencloud/config/", "opencloud/data/file", "opencloud/.env")
        )
        log_lines: list[str] = []
        fixed_timestamp = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)

        with patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ):
            result = run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, archive_path),
                compose_runner=fake_runner,
                tree_syncer=fake_syncer,
                archive_extractor=fake_extractor,
                stderr_log=log_lines.append,
                snapshot_timestamp=fixed_timestamp,
            )

        assert isinstance(result, RestoreJobResult)
        assert result.snapshot_path == (snapshot_base / "pre-restore-2026-06-16_120000").resolve()
        assert result.archive_path == archive_path.resolve()
        assert result.staging_path is None
        assert len(fake_runner.down_calls) == 1
        assert len(fake_runner.up_calls) == 1
        assert fake_runner.up_calls[0] == (stack_paths, 180)
        assert len(fake_runner.ps_calls) == 1
        assert fake_runner.ps_calls[0] == stack_paths
        assert len(fake_syncer.sync_calls) == 6
        snapshot_calls = [call for call in fake_syncer.sync_calls if call[3].startswith("rsync snapshot")]
        apply_calls = [call for call in fake_syncer.sync_calls if call[3].startswith("rsync apply")]
        assert len(snapshot_calls) == 3
        assert len(apply_calls) == 3
        assert apply_calls[0][4] is True
        assert apply_calls[1][4] is True
        assert apply_calls[2][4] is False
        assert fake_extractor.list_calls
        assert fake_extractor.extract_calls
        staging_dir = root / f"{RESTORE_STAGING_DIR_PREFIX}2026-06-16_120000"
        assert not staging_dir.exists()
        assert any(line.endswith("restore: extract phase finished") for line in log_lines)
        assert any(line.endswith("restore: apply phase finished") for line in log_lines)
        assert any(line.endswith("restore: up phase finished") for line in log_lines)
        assert any(line.endswith("restore: ps phase finished") for line in log_lines)


def test_run_restore_job_include_env_false_skips_snapshot_env_only() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        (root / ".env").write_text("KEY=1\n", encoding="utf-8")
        snapshot_base = root / "snapshots"
        stack_paths = _stack_paths(root)
        archive_path = _archive_path(Path(temporary_directory))
        fake_syncer = FakeTreeSyncer()
        fake_extractor = FakeArchiveExtractor()

        with patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ):
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, archive_path, include_env=False),
                compose_runner=FakeComposeRunner(),
                tree_syncer=fake_syncer,
                archive_extractor=fake_extractor,
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        snapshot_calls = [call for call in fake_syncer.sync_calls if call[3].startswith("rsync snapshot")]
        assert len(snapshot_calls) == 2


def test_run_restore_job_without_env_in_archive_skips_env_apply() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        stack_paths = _stack_paths(root)
        archive_path = _archive_path(Path(temporary_directory))
        fake_syncer = FakeTreeSyncer()
        fake_extractor = FakeArchiveExtractor(members=("opencloud/config/", "opencloud/data/file"))

        with patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ):
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, archive_path),
                compose_runner=FakeComposeRunner(),
                tree_syncer=fake_syncer,
                archive_extractor=fake_extractor,
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        apply_calls = [call for call in fake_syncer.sync_calls if call[3].startswith("rsync apply")]
        assert len(apply_calls) == 2
        assert all(call[4] is True for call in apply_calls)


def test_run_restore_job_verify_hash_before_list() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        stack_paths = _stack_paths(root)
        archive_path = _archive_path(Path(temporary_directory))
        fake_extractor = FakeArchiveExtractor()
        call_order: list[str] = []

        def fake_verify(_archive: Path) -> None:
            call_order.append("verify")

        original_list = fake_extractor.list_members

        def tracking_list(*args: object, **kwargs: object) -> tuple[str, ...]:
            call_order.append("list")
            return original_list(*args, **kwargs)  # type: ignore[arg-type]

        fake_extractor.list_members = tracking_list  # type: ignore[method-assign]

        with (
            patch(
                "opencloud_backup.jobs.restore.run_prerequisite_checks",
                return_value=_ok_prereq_report(),
            ),
            patch("opencloud_backup.jobs.restore.verify_archive_integrity", side_effect=fake_verify),
        ):
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, archive_path, verify_hash=True),
                compose_runner=FakeComposeRunner(),
                tree_syncer=FakeTreeSyncer(),
                archive_extractor=fake_extractor,
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        assert call_order == ["verify", "list"]


def test_run_restore_job_invalid_listing_no_staging() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        stack_paths = _stack_paths(root)
        archive_path = _archive_path(Path(temporary_directory))
        fake_extractor = FakeArchiveExtractor(members=("opencloud/config/",))
        staging_dir = root / f"{RESTORE_STAGING_DIR_PREFIX}2026-06-16_120000"

        with (
            patch(
                "opencloud_backup.jobs.restore.run_prerequisite_checks",
                return_value=_ok_prereq_report(),
            ),
            pytest.raises(ValidationError, match="opencloud/data"),
        ):
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, archive_path),
                compose_runner=FakeComposeRunner(),
                tree_syncer=FakeTreeSyncer(),
                archive_extractor=fake_extractor,
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        assert not staging_dir.exists()
        assert fake_extractor.extract_calls == []


def test_run_restore_job_extract_fail_no_apply() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        stack_paths = _stack_paths(root)
        archive_path = _archive_path(Path(temporary_directory))
        fake_syncer = FakeTreeSyncer()
        fake_extractor = FakeArchiveExtractor(fail_on="extract")
        fake_runner = FakeComposeRunner()
        log_lines: list[str] = []

        with (
            patch(
                "opencloud_backup.jobs.restore.run_prerequisite_checks",
                return_value=_ok_prereq_report(),
            ),
            pytest.raises(ArchiveCommandError),
        ):
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, archive_path),
                compose_runner=fake_runner,
                tree_syncer=fake_syncer,
                archive_extractor=fake_extractor,
                stderr_log=log_lines.append,
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        apply_calls = [call for call in fake_syncer.sync_calls if call[3].startswith("rsync apply")]
        assert apply_calls == []
        assert fake_runner.up_calls == []
        assert any(line.endswith("restore: extract phase failed") for line in log_lines)


def test_run_restore_job_apply_fail_logs_apply_failed() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        stack_paths = _stack_paths(root)
        archive_path = _archive_path(Path(temporary_directory))
        fake_syncer = FakeTreeSyncer(fail_on_label="rsync apply data")
        fake_runner = FakeComposeRunner()
        log_lines: list[str] = []

        with (
            patch(
                "opencloud_backup.jobs.restore.run_prerequisite_checks",
                return_value=_ok_prereq_report(),
            ),
            pytest.raises(RsyncCommandError),
        ):
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, archive_path),
                compose_runner=fake_runner,
                tree_syncer=fake_syncer,
                archive_extractor=FakeArchiveExtractor(),
                stderr_log=log_lines.append,
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        assert fake_runner.up_calls == []
        assert any(line.endswith("restore: apply phase failed") for line in log_lines)


def test_run_restore_job_prereqs_fail_no_down_no_logs() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    fake_syncer = FakeTreeSyncer()
    log_lines: list[str] = []

    with (
        patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_failed_prereq_report(),
        ),
        pytest.raises(PrerequisiteCheckError),
    ):
        run_restore_job(
            **_job_kwargs(stack_paths, Path("/data/snapshots"), Path("/backups/x.tar.zst")),
            compose_runner=fake_runner,
            tree_syncer=fake_syncer,
            archive_extractor=FakeArchiveExtractor(),
            stderr_log=log_lines.append,
        )

    assert fake_runner.down_calls == []
    assert fake_syncer.sync_calls == []
    assert log_lines == []


def test_run_restore_job_compose_fail_logs_failed_no_snapshot() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner(should_fail_down=True)
    fake_syncer = FakeTreeSyncer()
    log_lines: list[str] = []

    with (
        patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ),
        pytest.raises(ComposeCommandError),
    ):
        run_restore_job(
            **_job_kwargs(stack_paths, Path("/data/snapshots"), Path("/backups/x.tar.zst"), stop_timeout_seconds=90),
            compose_runner=fake_runner,
            tree_syncer=fake_syncer,
            archive_extractor=FakeArchiveExtractor(),
            stderr_log=log_lines.append,
        )

    assert log_lines[0].endswith("restore: stop phase started")
    assert log_lines[1].endswith("restore: stop phase failed")
    assert fake_syncer.sync_calls == []
    assert fake_runner.up_calls == []


def test_run_restore_job_rsync_fail_logs_snapshot_failed() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        stack_paths = _stack_paths(root)
        archive_path = _archive_path(Path(temporary_directory))
        fake_syncer = FakeTreeSyncer(fail_on_label="rsync snapshot data")
        log_lines: list[str] = []

        with (
            patch(
                "opencloud_backup.jobs.restore.run_prerequisite_checks",
                return_value=_ok_prereq_report(),
            ),
            pytest.raises(RsyncCommandError),
        ):
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, archive_path),
                compose_runner=FakeComposeRunner(),
                tree_syncer=fake_syncer,
                archive_extractor=FakeArchiveExtractor(),
                stderr_log=log_lines.append,
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        assert any(line.endswith("restore: snapshot phase started") for line in log_lines)
        assert any(line.endswith("restore: snapshot phase failed") for line in log_lines)
        assert "restore: snapshot phase finished" not in log_lines


def test_run_restore_job_replace_removes_old_snapshot() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        old_snapshot = snapshot_base / f"{SNAPSHOT_SUBDIR_PREFIX}-2026-01-01_000000"
        old_snapshot.mkdir(parents=True)
        stack_paths = _stack_paths(root)
        archive_path = _archive_path(Path(temporary_directory))

        with patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ):
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, archive_path),
                compose_runner=FakeComposeRunner(),
                tree_syncer=FakeTreeSyncer(),
                archive_extractor=FakeArchiveExtractor(),
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        assert not old_snapshot.exists()
        assert (snapshot_base / "pre-restore-2026-06-16_120000").is_dir()


def test_run_restore_job_keep_previous_snapshot() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        old_snapshot = snapshot_base / f"{SNAPSHOT_SUBDIR_PREFIX}-2026-01-01_000000"
        old_snapshot.mkdir(parents=True)
        stack_paths = _stack_paths(root)
        archive_path = _archive_path(Path(temporary_directory))

        with patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ):
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, archive_path, keep_previous_snapshot=True),
                compose_runner=FakeComposeRunner(),
                tree_syncer=FakeTreeSyncer(),
                archive_extractor=FakeArchiveExtractor(),
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        assert old_snapshot.is_dir()
        assert (snapshot_base / "pre-restore-2026-06-16_120000").is_dir()


def test_run_restore_job_passes_snapshot_timeout_to_syncer() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        stack_paths = _stack_paths(root)
        archive_path = _archive_path(Path(temporary_directory))
        fake_syncer = FakeTreeSyncer()

        with patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ):
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, archive_path, snapshot_timeout_seconds=120),
                compose_runner=FakeComposeRunner(),
                tree_syncer=fake_syncer,
                archive_extractor=FakeArchiveExtractor(),
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        snapshot_calls = [call for call in fake_syncer.sync_calls if call[3].startswith("rsync snapshot")]
        assert all(call[2] == 120 for call in snapshot_calls)


def test_run_restore_job_passes_disk_check_path_and_compression() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        stack_paths = _stack_paths(root)
        archive_path = _archive_path(Path(temporary_directory))
        disk_path = Path(temporary_directory) / "mnt" / "volume"
        disk_path.mkdir(parents=True)
        threshold = DiskThreshold(kind="bytes", value=1024)

        with patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ) as mock_checks:
            run_restore_job(
                **_job_kwargs(
                    stack_paths,
                    snapshot_base,
                    archive_path,
                    disk_check_path=disk_path,
                    disk_threshold=threshold,
                ),
                compose_runner=FakeComposeRunner(),
                tree_syncer=FakeTreeSyncer(),
                archive_extractor=FakeArchiveExtractor(),
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        assert mock_checks.call_args.kwargs["disk_check_path"] == disk_path
        assert mock_checks.call_args.kwargs["disk_threshold"] == threshold
        assert mock_checks.call_args.kwargs["mode"] == JobMode.RESTORE
        assert mock_checks.call_args.kwargs["compression"] == CompressionFormat.ZSTD


def test_run_restore_job_timestamp_collision_raises() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        snapshot_base.mkdir()
        fixed_timestamp = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
        (snapshot_base / "pre-restore-2026-06-16_120000").mkdir()
        stack_paths = _stack_paths(root)
        archive_path = _archive_path(Path(temporary_directory))
        log_lines: list[str] = []

        with (
            patch(
                "opencloud_backup.jobs.restore.run_prerequisite_checks",
                return_value=_ok_prereq_report(),
            ),
            pytest.raises(ValidationError, match="already exists"),
        ):
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, archive_path, keep_previous_snapshot=True),
                compose_runner=FakeComposeRunner(),
                tree_syncer=FakeTreeSyncer(),
                archive_extractor=FakeArchiveExtractor(),
                stderr_log=log_lines.append,
                snapshot_timestamp=fixed_timestamp,
            )

        assert any(line.endswith("restore: snapshot phase failed") for line in log_lines)


def test_run_restore_job_up_failure_raises_compose_error() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        stack_paths = _stack_paths(root)
        archive_path = _archive_path(Path(temporary_directory))
        fake_runner = FakeComposeRunner(should_fail_up=True)
        log_lines: list[str] = []

        with (
            patch(
                "opencloud_backup.jobs.restore.run_prerequisite_checks",
                return_value=_ok_prereq_report(),
            ),
            pytest.raises(ComposeCommandError, match="docker compose up -d"),
        ):
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, archive_path),
                compose_runner=fake_runner,
                tree_syncer=FakeTreeSyncer(),
                archive_extractor=FakeArchiveExtractor(),
                stderr_log=log_lines.append,
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        assert len(fake_runner.up_calls) == 1
        assert fake_runner.ps_calls == []
        assert any(line.endswith("restore: up phase failed") for line in log_lines)


def test_run_restore_job_ps_failure_still_succeeds() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        stack_paths = _stack_paths(root)
        archive_path = _archive_path(Path(temporary_directory))
        fake_runner = FakeComposeRunner(should_fail_ps=True)
        log_lines: list[str] = []

        with patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ):
            result = run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, archive_path),
                compose_runner=fake_runner,
                tree_syncer=FakeTreeSyncer(),
                archive_extractor=FakeArchiveExtractor(),
                stderr_log=log_lines.append,
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        assert isinstance(result, RestoreJobResult)
        assert len(fake_runner.up_calls) == 1
        assert len(fake_runner.ps_calls) == 1
        assert any(line.endswith("restore: ps phase failed") for line in log_lines)
        assert not any(line.endswith("restore: ps phase finished") for line in log_lines)
