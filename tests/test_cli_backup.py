from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import make_backup_output_dir, make_valid_stack_tree

from opencloud_backup.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main
from opencloud_backup.config import StackPaths
from opencloud_backup.domain.archive import CompressionFormat
from opencloud_backup.domain.errors import ArchiveCommandError, ComposeCommandError, PrerequisiteCheckError
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


def _setup_opencloud_root(temporary_directory: str) -> Path:
    opencloud_root = Path(temporary_directory) / "oc"
    make_valid_stack_tree(opencloud_root)
    make_backup_output_dir(opencloud_root)
    return opencloud_root


def test_backup_missing_root_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as system_exit:
        main(["backup"])
    assert system_exit.value.code == EXIT_USAGE
    assert "OCB_OPENCLOUD_ROOT" in capsys.readouterr().err


def test_backup_happy_path_exit_0(capsys: pytest.CaptureFixture[str]) -> None:
    archive_path = Path("/backups/opencloud-2026-06-14_101530.tar.zst")
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with (
            patch("opencloud_backup.cli.run_backup_job", return_value=archive_path) as mock_job,
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["backup", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_OK
        captured = capsys.readouterr()
        assert "Backup completed successfully." in captured.out
        assert f"archive: {archive_path}" in captured.out
        mock_job.assert_called_once()


def test_backup_prereqs_fail_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
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
        opencloud_root = _setup_opencloud_root(temporary_directory)
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


def test_backup_archive_fail_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with (
            patch(
                "opencloud_backup.cli.run_backup_job",
                side_effect=ArchiveCommandError("tar create", 1, "pack failed"),
            ),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["backup", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_ERROR
        standard_error = capsys.readouterr().err
        assert "Backup error: tar create (exit 1)" in standard_error
        assert "pack failed" in standard_error


def test_backup_invalid_stop_timeout_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with pytest.raises(SystemExit) as system_exit:
            main(["backup", "--opencloud-root", str(opencloud_root), "--stop-timeout", "0"])
        assert system_exit.value.code == EXIT_USAGE
        assert "stop-timeout" in capsys.readouterr().err


def test_backup_missing_output_dir_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        with pytest.raises(SystemExit) as system_exit:
            main(["backup", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_ERROR
        assert "Output directory does not exist:" in capsys.readouterr().err


def test_backup_invalid_pack_timeout_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with pytest.raises(SystemExit) as system_exit:
            main(["backup", "--opencloud-root", str(opencloud_root), "--pack-timeout", "0"])
        assert system_exit.value.code == EXIT_USAGE
        assert "--pack-timeout must be at least 1 second" in capsys.readouterr().err


def test_backup_disk_check_path_defaults_to_output_dir() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        expected_output_dir = opencloud_root / "backups"
        with (
            patch("opencloud_backup.cli.run_backup_job", return_value=Path("/x.tar.zst")) as mock_job,
            pytest.raises(SystemExit),
        ):
            main(["backup", "--opencloud-root", str(opencloud_root)])
        assert mock_job.call_args.kwargs["disk_check_path"] == expected_output_dir.resolve()


def test_backup_env_compression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        monkeypatch.setenv("OCB_OPENCLOUD_ROOT", str(opencloud_root))
        monkeypatch.setenv("OCB_COMPRESSION", "gzip")
        with (
            patch("opencloud_backup.cli.run_backup_job", return_value=Path("/x.tar.gz")) as mock_job,
            pytest.raises(SystemExit),
        ):
            main(["backup"])
        assert mock_job.call_args.kwargs["compression"] == CompressionFormat.GZIP


def test_backup_config_error_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "missing"
        with pytest.raises(SystemExit) as system_exit:
            main(["backup", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_ERROR
        assert "Configuration error" in capsys.readouterr().err


def test_backup_passes_stop_timeout_to_job() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with (
            patch("opencloud_backup.cli.run_backup_job", return_value=Path("/x.tar.zst")) as mock_job,
            pytest.raises(SystemExit),
        ):
            main(["backup", "--opencloud-root", str(opencloud_root), "--stop-timeout", "300"])
        assert mock_job.call_args.kwargs["stop_timeout_seconds"] == 300


def test_backup_passes_compression_and_no_env() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with (
            patch("opencloud_backup.cli.run_backup_job", return_value=Path("/x.tar")) as mock_job,
            pytest.raises(SystemExit),
        ):
            main(
                [
                    "backup",
                    "--opencloud-root",
                    str(opencloud_root),
                    "--compression",
                    "none",
                    "--no-env",
                ]
            )
        assert mock_job.call_args.kwargs["compression"] == CompressionFormat.NONE
        assert mock_job.call_args.kwargs["include_env"] is False


def test_backup_stop_and_pack_timestamps_on_success(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        fake_archive = opencloud_root / "backups" / "opencloud.tar.zst"
        with (
            patch(
                "opencloud_backup.jobs.backup.run_prerequisite_checks",
                return_value=_ok_prereq_report(),
            ),
            patch("opencloud_backup.cli.SubprocessComposeRunner", return_value=FakeComposeRunner()),
            patch(
                "opencloud_backup.jobs.backup.SubprocessArchiveBuilder.create_backup_archive",
                return_value=fake_archive,
            ),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["backup", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_OK
        standard_error = capsys.readouterr().err
        assert "backup: stop phase started" in standard_error
        assert "backup: stop phase finished" in standard_error
        assert "backup: pack phase started" in standard_error
        assert "backup: pack phase finished" in standard_error


def test_backup_env_stop_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        monkeypatch.setenv("OCB_OPENCLOUD_ROOT", str(opencloud_root))
        monkeypatch.setenv("OCB_STOP_TIMEOUT", "240")
        with (
            patch("opencloud_backup.cli.run_backup_job", return_value=Path("/x.tar.zst")) as mock_job,
            pytest.raises(SystemExit),
        ):
            main(["backup"])
        assert mock_job.call_args.kwargs["stop_timeout_seconds"] == 240
