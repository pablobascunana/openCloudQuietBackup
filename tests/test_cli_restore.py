from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import make_valid_stack_tree

from opencloud_backup.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main
from opencloud_backup.config import StackPaths
from opencloud_backup.domain.errors import ComposeCommandError, PrerequisiteCheckError, RsyncCommandError
from opencloud_backup.domain.prereqs import DiskCheckResult, JobMode, PrerequisiteReport


class FakeComposeRunner:
    def down(self, stack_paths: StackPaths, timeout_seconds: int) -> None:
        return None

    def up(self, stack_paths: StackPaths, timeout_seconds: int) -> None:
        return None

    def ps(self, stack_paths: StackPaths) -> str:
        return "ps-ok"


class FakeTreeSyncer:
    def sync_tree(
        self,
        source: Path,
        destination: Path,
        *,
        timeout_seconds: int | None = None,
        command_label: str = "rsync snapshot",
    ) -> None:
        return None


def _ok_prereq_report() -> PrerequisiteReport:
    return PrerequisiteReport(
        ok=True,
        mode=JobMode.RESTORE,
        missing_binaries=(),
        failed_commands=(),
        disk=DiskCheckResult(
            path=Path("/data"),
            total_bytes=100 * 1024**3,
            free_bytes=50 * 1024**3,
            threshold=None,
            ok=True,
        ),
    )


def _setup_opencloud_root(temporary_directory: str) -> Path:
    opencloud_root = Path(temporary_directory) / "oc"
    make_valid_stack_tree(opencloud_root)
    return opencloud_root


def test_restore_missing_root_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as system_exit:
        main(["restore"])
    assert system_exit.value.code == EXIT_USAGE
    assert "OCB_OPENCLOUD_ROOT" in capsys.readouterr().err


def test_restore_happy_path_exit_0(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        snapshot_path = opencloud_root / "snapshots" / "pre-restore-2026-06-16_120000"
        with (
            patch("opencloud_backup.cli.run_restore_job", return_value=snapshot_path) as mock_job,
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["restore", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_OK
        captured = capsys.readouterr()
        assert "Stack parado y snapshot de seguridad creado" in captured.out
        assert str(snapshot_path) in captured.out
        assert "US-022" in captured.out
        assert "US-023" in captured.out
        mock_job.assert_called_once()


def test_restore_prereqs_fail_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        failed_report = PrerequisiteReport(
            ok=False,
            mode=JobMode.RESTORE,
            missing_binaries=("rsync",),
            failed_commands=(),
            disk=None,
        )
        with (
            patch(
                "opencloud_backup.cli.run_restore_job",
                side_effect=PrerequisiteCheckError(failed_report),
            ),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["restore", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_ERROR
        assert "Missing binaries: rsync" in capsys.readouterr().err


def test_restore_compose_fail_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with (
            patch(
                "opencloud_backup.cli.run_restore_job",
                side_effect=ComposeCommandError("docker compose down", 1, "compose failed"),
            ),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["restore", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_ERROR
        standard_error = capsys.readouterr().err
        assert "Restore error: docker compose down (exit 1)" in standard_error
        assert "compose failed" in standard_error


def test_restore_rsync_fail_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with (
            patch(
                "opencloud_backup.cli.run_restore_job",
                side_effect=RsyncCommandError("rsync snapshot data", 23, "disk full"),
            ),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["restore", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_ERROR
        standard_error = capsys.readouterr().err
        assert "Error de restore: falló la copia de snapshot" in standard_error
        assert "rsync snapshot data" in standard_error
        assert "disk full" in standard_error


def test_restore_invalid_stop_timeout_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with pytest.raises(SystemExit) as system_exit:
            main(["restore", "--opencloud-root", str(opencloud_root), "--stop-timeout", "0"])
        assert system_exit.value.code == EXIT_USAGE
        assert "stop-timeout" in capsys.readouterr().err


def test_restore_invalid_snapshot_timeout_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with pytest.raises(SystemExit) as system_exit:
            main(["restore", "--opencloud-root", str(opencloud_root), "--snapshot-timeout", "0"])
        assert system_exit.value.code == EXIT_USAGE
        assert "snapshot-timeout" in capsys.readouterr().err


def test_restore_disk_check_path_defaults_to_snapshot_base_parent() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with (
            patch("opencloud_backup.cli.run_restore_job") as mock_job,
            pytest.raises(SystemExit),
        ):
            main(["restore", "--opencloud-root", str(opencloud_root)])
        expected_disk_path = (opencloud_root / "snapshots").parent.resolve()
        assert mock_job.call_args.kwargs["disk_check_path"] == expected_disk_path


def test_restore_custom_snapshot_dir_disk_check_uses_its_parent() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        custom_snapshot_base = Path(temporary_directory) / "mnt" / "snapshots"
        with (
            patch("opencloud_backup.cli.run_restore_job") as mock_job,
            pytest.raises(SystemExit),
        ):
            main(
                [
                    "restore",
                    "--opencloud-root",
                    str(opencloud_root),
                    "--snapshot-dir",
                    str(custom_snapshot_base),
                ]
            )
        assert mock_job.call_args.kwargs["disk_check_path"] == custom_snapshot_base.parent.resolve()


def test_restore_config_error_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "missing"
        with pytest.raises(SystemExit) as system_exit:
            main(["restore", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_ERROR
        assert "Configuration error" in capsys.readouterr().err


def test_restore_passes_stop_timeout_to_job() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with (
            patch("opencloud_backup.cli.run_restore_job", return_value=Path("/snap")) as mock_job,
            pytest.raises(SystemExit),
        ):
            main(["restore", "--opencloud-root", str(opencloud_root), "--stop-timeout", "300"])
        assert mock_job.call_args.kwargs["stop_timeout_seconds"] == 300


def test_restore_passes_snapshot_flags_to_job() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        custom_snapshot = Path(temporary_directory) / "custom-snapshots"
        with (
            patch("opencloud_backup.cli.run_restore_job", return_value=Path("/snap")) as mock_job,
            pytest.raises(SystemExit),
        ):
            main(
                [
                    "restore",
                    "--opencloud-root",
                    str(opencloud_root),
                    "--snapshot-dir",
                    str(custom_snapshot),
                    "--keep-previous-snapshot",
                    "--no-env",
                    "--snapshot-timeout",
                    "600",
                ]
            )
        kwargs = mock_job.call_args.kwargs
        assert kwargs["snapshot_base_dir"] == custom_snapshot.resolve()
        assert kwargs["keep_previous_snapshot"] is True
        assert kwargs["include_env"] is False
        assert kwargs["snapshot_timeout_seconds"] == 600


def test_restore_snapshot_dir_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        env_snapshot = Path(temporary_directory) / "env-snapshots"
        monkeypatch.setenv("OCB_SNAPSHOT_DIR", str(env_snapshot))
        with (
            patch("opencloud_backup.cli.run_restore_job", return_value=Path("/snap")) as mock_job,
            pytest.raises(SystemExit),
        ):
            main(["restore", "--opencloud-root", str(opencloud_root)])
        assert mock_job.call_args.kwargs["snapshot_base_dir"] == env_snapshot.resolve()


def test_restore_snapshot_timeout_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        monkeypatch.setenv("OCB_SNAPSHOT_TIMEOUT", "900")
        with (
            patch("opencloud_backup.cli.run_restore_job", return_value=Path("/snap")) as mock_job,
            pytest.raises(SystemExit),
        ):
            main(["restore", "--opencloud-root", str(opencloud_root)])
        assert mock_job.call_args.kwargs["snapshot_timeout_seconds"] == 900


def test_restore_stop_phase_logs_on_success(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with (
            patch(
                "opencloud_backup.jobs.restore.run_prerequisite_checks",
                return_value=_ok_prereq_report(),
            ),
            patch("opencloud_backup.cli.SubprocessComposeRunner", return_value=FakeComposeRunner()),
            patch("opencloud_backup.jobs.restore.SubprocessTreeSyncer", return_value=FakeTreeSyncer()),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["restore", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_OK
        standard_error = capsys.readouterr().err
        assert "restore: stop phase started" in standard_error
        assert "restore: stop phase finished" in standard_error
        assert "restore: snapshot phase started" in standard_error
        assert "restore: snapshot phase finished" in standard_error
        assert "restore: up phase" not in standard_error
