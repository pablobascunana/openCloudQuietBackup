from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import make_valid_stack_tree

from opencloud_backup.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main
from opencloud_backup.config import StackPaths
from opencloud_backup.domain.errors import ComposeCommandError, PrerequisiteCheckError
from opencloud_backup.domain.prereqs import DiskCheckResult, JobMode, PrerequisiteReport


class FakeComposeRunner:
    def down(self, stack_paths: StackPaths, timeout_seconds: int) -> None:
        return None


def _ok_prereq_report() -> PrerequisiteReport:
    return PrerequisiteReport(
        ok=True,
        mode=JobMode.BACKUP,
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


def test_backup_missing_root_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as system_exit:
        main(["backup"])
    assert system_exit.value.code == EXIT_USAGE
    assert "OCB_OPENCLOUD_ROOT" in capsys.readouterr().err


def test_backup_happy_path_exit_0(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        with (
            patch("opencloud_backup.cli.run_backup_job") as mock_job,
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["backup", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_OK
        assert "Backup stop phase completed successfully." in capsys.readouterr().out
        mock_job.assert_called_once()


def test_backup_prereqs_fail_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        failed_report = PrerequisiteReport(
            ok=False,
            mode=JobMode.BACKUP,
            missing_binaries=("tar",),
            failed_commands=(),
            disk=None,
        )
        with (
            patch(
                "opencloud_backup.cli.run_backup_job",
                side_effect=PrerequisiteCheckError(failed_report),
            ),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["backup", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_ERROR
        assert "Missing binaries: tar" in capsys.readouterr().err


def test_backup_compose_fail_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        with (
            patch(
                "opencloud_backup.cli.run_backup_job",
                side_effect=ComposeCommandError("docker compose down", 1, "compose failed"),
            ),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["backup", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_ERROR
        standard_error = capsys.readouterr().err
        assert "Backup error: docker compose down (exit 1)" in standard_error
        assert "compose failed" in standard_error


def test_backup_invalid_stop_timeout_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        with pytest.raises(SystemExit) as system_exit:
            main(["backup", "--opencloud-root", str(opencloud_root), "--stop-timeout", "0"])
        assert system_exit.value.code == EXIT_USAGE
        assert "stop-timeout" in capsys.readouterr().err


def test_backup_config_error_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "missing"
        with pytest.raises(SystemExit) as system_exit:
            main(["backup", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_ERROR
        assert "Configuration error" in capsys.readouterr().err


def test_backup_passes_stop_timeout_to_job() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        with (
            patch("opencloud_backup.cli.run_backup_job") as mock_job,
            pytest.raises(SystemExit),
        ):
            main(["backup", "--opencloud-root", str(opencloud_root), "--stop-timeout", "300"])
        assert mock_job.call_args.kwargs["stop_timeout_seconds"] == 300


def test_backup_stop_timestamps_on_success(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        with (
            patch(
                "opencloud_backup.jobs.backup.run_prerequisite_checks",
                return_value=_ok_prereq_report(),
            ),
            patch("opencloud_backup.cli.SubprocessComposeRunner", return_value=FakeComposeRunner()),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["backup", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_OK
        standard_error = capsys.readouterr().err
        assert "backup: stop phase started" in standard_error
        assert "backup: stop phase finished" in standard_error


def test_backup_env_stop_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        monkeypatch.setenv("OCB_OPENCLOUD_ROOT", str(opencloud_root))
        monkeypatch.setenv("OCB_STOP_TIMEOUT", "240")
        with (
            patch("opencloud_backup.cli.run_backup_job") as mock_job,
            pytest.raises(SystemExit),
        ):
            main(["backup"])
        assert mock_job.call_args.kwargs["stop_timeout_seconds"] == 240
