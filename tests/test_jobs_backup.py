from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from opencloud_backup.config import StackPaths
from opencloud_backup.domain.archive import CompressionFormat
from opencloud_backup.domain.errors import (
    ArchiveCommandError,
    ComposeCommandError,
    IntegrityError,
    PrerequisiteCheckError,
)
from opencloud_backup.domain.integrity import IntegrityRecord, sidecar_path_for_archive
from opencloud_backup.domain.prereqs import DiskCheckResult, DiskThreshold, JobMode, PrerequisiteReport
from opencloud_backup.jobs.backup import _format_phase_log_line, run_backup_job


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


class FakeArchiveBuilder:
    def __init__(self, *, should_fail: bool = False, archive_path: Path | None = None) -> None:
        self.should_fail = should_fail
        self.archive_path = archive_path or Path("/backups/opencloud-2026-06-14_101530.tar.zst")
        self.create_calls: list[dict[str, object]] = []

    def create_backup_archive(
        self,
        stack_paths: StackPaths,
        *,
        output_dir: Path,
        compression: CompressionFormat,
        include_env: bool,
        pack_timeout_seconds: int | None = None,
        archive_timestamp: datetime | None = None,
    ) -> Path:
        self.create_calls.append(
            {
                "stack_paths": stack_paths,
                "output_dir": output_dir,
                "compression": compression,
                "include_env": include_env,
                "archive_timestamp": archive_timestamp,
            }
        )
        if self.should_fail:
            raise ArchiveCommandError("tar create", 1, "pack failed")
        return self.archive_path


class FakeFileHasher:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls: list[Path] = []

    def compute_sha256(self, path: Path) -> str:
        self.calls.append(path)
        if self.should_fail:
            raise IntegrityError("hash failed")
        return "a" * 64


class FakeSidecarStore:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.writes: list[tuple[Path, IntegrityRecord]] = []

    def write(self, archive_path: Path, record: IntegrityRecord) -> Path:
        if self.should_fail:
            raise IntegrityError("sidecar write failed")
        sidecar_path = sidecar_path_for_archive(archive_path)
        self.writes.append((archive_path, record))
        return sidecar_path

    def read(self, sidecar_path: Path) -> IntegrityRecord:
        raise IntegrityError("not implemented in backup tests")


def _stack_paths(root: Path) -> StackPaths:
    return StackPaths(
        opencloud_root=root,
        config_dir=root / "config",
        data_dir=root / "data",
        compose_dir=root,
        compose_file=root / "docker-compose.yml",
    )


def _job_kwargs(
    stack_paths: StackPaths,
    *,
    output_dir: Path | None = None,
    **overrides: object,
) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "stack_paths": stack_paths,
        "output_dir": output_dir or Path("/data/backups"),
        "compression": CompressionFormat.ZSTD,
        "include_env": True,
        "disk_check_path": output_dir or Path("/data/backups"),
        "stop_timeout_seconds": 180,
        "start_timeout_seconds": 180,
    }
    kwargs.update(overrides)
    return kwargs


def _ok_prereq_report() -> PrerequisiteReport:
    return PrerequisiteReport(
        ok=True,
        mode=JobMode.BACKUP,
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
        mode=JobMode.BACKUP,
        missing_binaries=("tar",),
        failed_commands=(),
        disk=None,
    )


def test_format_phase_log_line_uses_utc_iso() -> None:
    fixed_now = datetime(2026, 6, 14, 10, 15, 30, 123456, tzinfo=timezone.utc)
    line = _format_phase_log_line("backup: stop phase started", now=fixed_now)
    assert line == "[2026-06-14T10:15:30.123456+00:00] backup: stop phase started"


def test_run_backup_job_happy_path_returns_archive_path() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    fake_packer = FakeArchiveBuilder()
    log_lines: list[str] = []

    with patch(
        "opencloud_backup.jobs.backup.run_prerequisite_checks",
        return_value=_ok_prereq_report(),
    ):
        archive_path = run_backup_job(
            **_job_kwargs(stack_paths),
            compose_runner=fake_runner,
            archive_builder=fake_packer,
            stderr_log=log_lines.append,
        )

    assert archive_path == fake_packer.archive_path
    assert len(fake_runner.down_calls) == 1
    assert fake_runner.down_calls[0] == (stack_paths, 180)
    assert len(fake_runner.up_calls) == 1
    assert fake_runner.up_calls[0] == (stack_paths, 180)
    assert len(fake_runner.ps_calls) == 1
    assert fake_runner.ps_calls[0] == stack_paths
    assert len(fake_packer.create_calls) == 1
    assert log_lines[0].endswith("backup: stop phase started")
    assert log_lines[1].endswith("backup: stop phase finished")
    assert log_lines[2].endswith("backup: pack phase started")
    assert log_lines[3].endswith("backup: pack phase finished")
    assert log_lines[4].endswith("backup: up phase started")
    assert log_lines[5].endswith("backup: up phase finished")
    assert log_lines[6].endswith("backup: ps phase started")
    assert log_lines[7].endswith("backup: ps phase finished")
    assert log_lines[8] == "ps-ok"


def test_run_backup_job_prereqs_fail_no_down_no_logs() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    fake_packer = FakeArchiveBuilder()
    log_lines: list[str] = []

    with (
        patch(
            "opencloud_backup.jobs.backup.run_prerequisite_checks",
            return_value=_failed_prereq_report(),
        ),
        pytest.raises(PrerequisiteCheckError),
    ):
        run_backup_job(
            **_job_kwargs(stack_paths),
            compose_runner=fake_runner,
            archive_builder=fake_packer,
            stderr_log=log_lines.append,
        )

    assert fake_runner.down_calls == []
    assert fake_packer.create_calls == []
    assert log_lines == []


def test_run_backup_job_compose_fail_logs_failed_no_pack() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner(should_fail_down=True)
    fake_packer = FakeArchiveBuilder()
    log_lines: list[str] = []

    with (
        patch(
            "opencloud_backup.jobs.backup.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ),
        pytest.raises(ComposeCommandError),
    ):
        run_backup_job(
            **_job_kwargs(stack_paths, stop_timeout_seconds=90),
            compose_runner=fake_runner,
            archive_builder=fake_packer,
            stderr_log=log_lines.append,
        )

    assert log_lines[0].endswith("backup: stop phase started")
    assert log_lines[1].endswith("backup: stop phase failed")
    assert fake_packer.create_calls == []


def test_run_backup_job_pack_fail_logs_failed() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    fake_packer = FakeArchiveBuilder(should_fail=True)
    log_lines: list[str] = []

    with (
        patch(
            "opencloud_backup.jobs.backup.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ),
        pytest.raises(ArchiveCommandError),
    ):
        run_backup_job(
            **_job_kwargs(stack_paths),
            compose_runner=fake_runner,
            archive_builder=fake_packer,
            stderr_log=log_lines.append,
        )

    assert log_lines[2].endswith("backup: pack phase started")
    assert log_lines[3].endswith("backup: pack phase failed")
    assert len(fake_runner.up_calls) == 1
    assert len(fake_runner.ps_calls) == 1
    assert log_lines[4].endswith("backup: up phase started")
    assert log_lines[5].endswith("backup: up phase finished")
    assert log_lines[6].endswith("backup: ps phase started")
    assert log_lines[7].endswith("backup: ps phase finished")
    assert log_lines[8] == "ps-ok"


def test_run_backup_job_passes_disk_threshold_and_compression() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    fake_packer = FakeArchiveBuilder()
    threshold = DiskThreshold(kind="bytes", value=1024)
    log_lines: list[str] = []

    with patch(
        "opencloud_backup.jobs.backup.run_prerequisite_checks",
        return_value=_ok_prereq_report(),
    ) as mock_checks:
        run_backup_job(
            **_job_kwargs(stack_paths, compression=CompressionFormat.GZIP, disk_threshold=threshold),
            compose_runner=fake_runner,
            archive_builder=fake_packer,
            stderr_log=log_lines.append,
        )

    assert mock_checks.call_args.kwargs["disk_threshold"] == threshold
    assert mock_checks.call_args.kwargs["mode"] == JobMode.BACKUP
    assert mock_checks.call_args.kwargs["compression"] == CompressionFormat.GZIP


def test_run_backup_job_passes_include_env_to_packer() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_packer = FakeArchiveBuilder()

    with patch(
        "opencloud_backup.jobs.backup.run_prerequisite_checks",
        return_value=_ok_prereq_report(),
    ):
        run_backup_job(
            **_job_kwargs(stack_paths, include_env=False),
            compose_runner=FakeComposeRunner(),
            archive_builder=fake_packer,
        )

    assert fake_packer.create_calls[0]["include_env"] is False


def test_run_backup_job_passes_timestamp_to_packer() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_packer = FakeArchiveBuilder()
    fixed_timestamp = datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc)

    with patch(
        "opencloud_backup.jobs.backup.run_prerequisite_checks",
        return_value=_ok_prereq_report(),
    ):
        run_backup_job(
            **_job_kwargs(stack_paths),
            compose_runner=FakeComposeRunner(),
            archive_builder=fake_packer,
            timestamp=fixed_timestamp,
        )

    assert fake_packer.create_calls[0]["archive_timestamp"] == fixed_timestamp


def test_prerequisite_check_error_carries_report() -> None:
    failed_report = _failed_prereq_report()
    with (
        patch(
            "opencloud_backup.jobs.backup.run_prerequisite_checks",
            return_value=failed_report,
        ),
        pytest.raises(PrerequisiteCheckError) as error_info,
    ):
        run_backup_job(
            **_job_kwargs(_stack_paths(Path("/data"))),
            compose_runner=FakeComposeRunner(),
            archive_builder=FakeArchiveBuilder(),
            stderr_log=list.append,
        )
    assert error_info.value.report is failed_report


def test_backup_without_write_integrity_skips_hash_phase() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    fake_hasher = FakeFileHasher()
    log_lines: list[str] = []

    with patch(
        "opencloud_backup.jobs.backup.run_prerequisite_checks",
        return_value=_ok_prereq_report(),
    ):
        run_backup_job(
            **_job_kwargs(stack_paths),
            compose_runner=fake_runner,
            archive_builder=FakeArchiveBuilder(),
            file_hasher=fake_hasher,
            stderr_log=log_lines.append,
        )

    assert fake_hasher.calls == []
    assert not any("hash phase" in line for line in log_lines)


def test_backup_write_integrity_runs_hash_before_up(tmp_path: Path) -> None:
    archive_path = tmp_path / "opencloud-2026-06-14_101530.tar.zst"
    archive_path.write_bytes(b"archive-data")
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    fake_hasher = FakeFileHasher()
    call_order: list[str] = []

    original_up = fake_runner.up

    def tracked_up(stack_paths_arg: StackPaths, timeout_seconds: int) -> None:
        call_order.append("up")
        original_up(stack_paths_arg, timeout_seconds)

    fake_runner.up = tracked_up  # type: ignore[method-assign]

    original_compute = fake_hasher.compute_sha256

    def tracked_hash(path: Path) -> str:
        call_order.append("hash")
        return original_compute(path)

    fake_hasher.compute_sha256 = tracked_hash  # type: ignore[method-assign]

    with patch(
        "opencloud_backup.jobs.backup.run_prerequisite_checks",
        return_value=_ok_prereq_report(),
    ):
        run_backup_job(
            **_job_kwargs(stack_paths),
            compose_runner=fake_runner,
            archive_builder=FakeArchiveBuilder(archive_path=archive_path),
            write_integrity=True,
            file_hasher=fake_hasher,
            sidecar_store=FakeSidecarStore(),
        )

    assert call_order == ["hash", "up"]


def test_backup_hash_phase_logs_started_finished(tmp_path: Path) -> None:
    archive_path = tmp_path / "opencloud-2026-06-14_101530.tar.zst"
    archive_path.write_bytes(b"archive-data")
    stack_paths = _stack_paths(Path("/data/opencloud"))
    log_lines: list[str] = []

    with patch(
        "opencloud_backup.jobs.backup.run_prerequisite_checks",
        return_value=_ok_prereq_report(),
    ):
        run_backup_job(
            **_job_kwargs(stack_paths),
            compose_runner=FakeComposeRunner(),
            archive_builder=FakeArchiveBuilder(archive_path=archive_path),
            write_integrity=True,
            file_hasher=FakeFileHasher(),
            sidecar_store=FakeSidecarStore(),
            stderr_log=log_lines.append,
        )

    assert any(line.endswith("backup: hash phase started") for line in log_lines)
    assert any(line.endswith("backup: hash phase finished") for line in log_lines)
    pack_finished_index = next(i for i, line in enumerate(log_lines) if line.endswith("backup: pack phase finished"))
    hash_started_index = next(i for i, line in enumerate(log_lines) if line.endswith("backup: hash phase started"))
    up_started_index = next(i for i, line in enumerate(log_lines) if line.endswith("backup: up phase started"))
    assert pack_finished_index < hash_started_index < up_started_index


def test_backup_hash_failure_still_attempts_up() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    fake_hasher = FakeFileHasher(should_fail=True)

    with (
        patch(
            "opencloud_backup.jobs.backup.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ),
        pytest.raises(IntegrityError),
    ):
        run_backup_job(
            **_job_kwargs(stack_paths),
            compose_runner=fake_runner,
            archive_builder=FakeArchiveBuilder(),
            write_integrity=True,
            file_hasher=fake_hasher,
            sidecar_store=FakeSidecarStore(),
        )

    assert len(fake_runner.up_calls) == 1


def test_backup_hash_failure_raises_after_successful_up() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))

    with (
        patch(
            "opencloud_backup.jobs.backup.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ),
        pytest.raises(IntegrityError, match="hash failed"),
    ):
        run_backup_job(
            **_job_kwargs(stack_paths),
            compose_runner=FakeComposeRunner(),
            archive_builder=FakeArchiveBuilder(),
            write_integrity=True,
            file_hasher=FakeFileHasher(should_fail=True),
            sidecar_store=FakeSidecarStore(),
        )


def test_backup_pack_failure_skips_hash_phase() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_hasher = FakeFileHasher()
    log_lines: list[str] = []

    with (
        patch(
            "opencloud_backup.jobs.backup.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ),
        pytest.raises(ArchiveCommandError),
    ):
        run_backup_job(
            **_job_kwargs(stack_paths),
            compose_runner=FakeComposeRunner(),
            archive_builder=FakeArchiveBuilder(should_fail=True),
            write_integrity=True,
            file_hasher=fake_hasher,
            stderr_log=log_lines.append,
        )

    assert fake_hasher.calls == []
    assert not any("hash phase" in line for line in log_lines)
