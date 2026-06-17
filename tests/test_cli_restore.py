from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import make_valid_stack_tree

from opencloud_backup.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main
from opencloud_backup.config import StackPaths, ValidationError
from opencloud_backup.domain.errors import ArchiveCommandError, ComposeCommandError, PrerequisiteCheckError, RsyncCommandError
from opencloud_backup.domain.prereqs import DiskCheckResult, JobMode, PrerequisiteReport
from opencloud_backup.domain.restore import RestoreJobResult


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
        delete: bool = False,
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


def _archive_file(temporary_directory: str) -> Path:
    archive = Path(temporary_directory) / "opencloud-2026-06-16_120000.tar.zst"
    archive.write_bytes(b"fake")
    return archive


def _restore_argv(
    opencloud_root: Path,
    archive_path: Path,
    *extra: str,
    confirm: bool = True,
) -> list[str]:
    argv = ["restore", "--opencloud-root", str(opencloud_root), "--archive", str(archive_path), *extra]
    if confirm:
        argv.append("--i-know-what-im-doing")
    return argv


def _tty_patches(
    *,
    stdin_tty: bool = True,
    stdout_tty: bool = True,
) -> tuple[object, object]:
    return (
        patch("opencloud_backup.cli.sys.stdin.isatty", return_value=stdin_tty),
        patch("opencloud_backup.cli.sys.stdout.isatty", return_value=stdout_tty),
    )


def test_restore_missing_root_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as system_exit:
        main(["restore", "--archive", "/tmp/x.tar.zst"])
    assert system_exit.value.code == EXIT_USAGE
    assert "OCB_OPENCLOUD_ROOT" in capsys.readouterr().err


def test_restore_missing_archive_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with pytest.raises(SystemExit) as system_exit:
            main(["restore", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_USAGE
        assert "archive" in capsys.readouterr().err.lower()


def test_restore_archive_not_found_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        missing_archive = Path(temporary_directory) / "missing.tar.zst"
        with pytest.raises(SystemExit) as system_exit:
            main(_restore_argv(opencloud_root, missing_archive))
        assert system_exit.value.code == EXIT_ERROR
        assert "no existe" in capsys.readouterr().err


def test_restore_invalid_archive_extension_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive = Path(temporary_directory) / "backup.tgz"
        archive.write_bytes(b"x")
        with pytest.raises(SystemExit) as system_exit:
            main(_restore_argv(opencloud_root, archive))
        assert system_exit.value.code == EXIT_ERROR
        assert "Formato de archivo no soportado" in capsys.readouterr().err


def test_restore_happy_path_exit_0(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        snapshot_path = opencloud_root / "snapshots" / "pre-restore-2026-06-16_120000"
        result = RestoreJobResult(
            snapshot_path=snapshot_path,
            archive_path=archive_path.resolve(),
            staging_path=None,
        )
        with (
            patch("opencloud_backup.cli.run_restore_job", return_value=result) as mock_job,
            pytest.raises(SystemExit) as system_exit,
        ):
            main(_restore_argv(opencloud_root, archive_path))
        assert system_exit.value.code == EXIT_OK
        captured = capsys.readouterr()
        assert "Restore completado correctamente" in captured.out
        assert "stack está en marcha" in captured.out
        assert str(snapshot_path) in captured.out
        assert str(archive_path.resolve()) in captured.out
        assert "US-023" not in captured.out
        assert "US-022" not in captured.out
        mock_job.assert_called_once()


def test_restore_prereqs_fail_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
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
            main(_restore_argv(opencloud_root, archive_path))
        assert system_exit.value.code == EXIT_ERROR
        assert "Missing binaries: rsync" in capsys.readouterr().err


def test_restore_compose_fail_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        with (
            patch(
                "opencloud_backup.cli.run_restore_job",
                side_effect=ComposeCommandError("docker compose down", 1, "compose failed"),
            ),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(_restore_argv(opencloud_root, archive_path))
        assert system_exit.value.code == EXIT_ERROR
        standard_error = capsys.readouterr().err
        assert "Restore error: docker compose down (exit 1)" in standard_error
        assert "compose failed" in standard_error


def test_restore_rsync_fail_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        with (
            patch(
                "opencloud_backup.cli.run_restore_job",
                side_effect=RsyncCommandError("rsync apply data", 23, "disk full"),
            ),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(_restore_argv(opencloud_root, archive_path))
        assert system_exit.value.code == EXIT_ERROR
        standard_error = capsys.readouterr().err
        assert "Error de restore: falló rsync" in standard_error
        assert "rsync apply data" in standard_error
        assert "disk full" in standard_error


def test_restore_archive_command_error_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        with (
            patch(
                "opencloud_backup.cli.run_restore_job",
                side_effect=ArchiveCommandError("restore archive extract", 1, "extract boom"),
            ),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(_restore_argv(opencloud_root, archive_path))
        assert system_exit.value.code == EXIT_ERROR
        standard_error = capsys.readouterr().err
        assert "restore archive extract" in standard_error
        assert "extract boom" in standard_error


def test_restore_layout_validation_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        with (
            patch(
                "opencloud_backup.cli.run_restore_job",
                side_effect=ValidationError("El archivo de backup no contiene los directorios requeridos"),
            ),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(_restore_argv(opencloud_root, archive_path))
        assert system_exit.value.code == EXIT_ERROR
        assert "directorios requeridos" in capsys.readouterr().err


def test_restore_invalid_stop_timeout_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        with pytest.raises(SystemExit) as system_exit:
            main(_restore_argv(opencloud_root, archive_path, "--stop-timeout", "0"))
        assert system_exit.value.code == EXIT_USAGE
        assert "stop-timeout" in capsys.readouterr().err


def test_restore_invalid_snapshot_timeout_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        with pytest.raises(SystemExit) as system_exit:
            main(_restore_argv(opencloud_root, archive_path, "--snapshot-timeout", "0"))
        assert system_exit.value.code == EXIT_USAGE
        assert "snapshot-timeout" in capsys.readouterr().err


def test_restore_invalid_extract_timeout_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        with pytest.raises(SystemExit) as system_exit:
            main(_restore_argv(opencloud_root, archive_path, "--extract-timeout", "0"))
        assert system_exit.value.code == EXIT_USAGE
        assert "extract-timeout" in capsys.readouterr().err


def test_restore_invalid_apply_timeout_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        with pytest.raises(SystemExit) as system_exit:
            main(_restore_argv(opencloud_root, archive_path, "--apply-timeout", "0"))
        assert system_exit.value.code == EXIT_USAGE
        assert "apply-timeout" in capsys.readouterr().err


def test_restore_disk_check_path_defaults_to_snapshot_base_parent() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        with (
            patch("opencloud_backup.cli.run_restore_job") as mock_job,
            pytest.raises(SystemExit),
        ):
            main(_restore_argv(opencloud_root, archive_path))
        expected_disk_path = (opencloud_root / "snapshots").parent.resolve()
        assert mock_job.call_args.kwargs["disk_check_path"] == expected_disk_path


def test_restore_custom_snapshot_dir_disk_check_uses_its_parent() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        custom_snapshot_base = Path(temporary_directory) / "mnt" / "snapshots"
        with (
            patch("opencloud_backup.cli.run_restore_job") as mock_job,
            pytest.raises(SystemExit),
        ):
            main(
                _restore_argv(
                    opencloud_root,
                    archive_path,
                    "--snapshot-dir",
                    str(custom_snapshot_base),
                )
            )
        assert mock_job.call_args.kwargs["disk_check_path"] == custom_snapshot_base.parent.resolve()


def test_restore_config_error_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "missing"
        archive_path = _archive_file(temporary_directory)
        with pytest.raises(SystemExit) as system_exit:
            main(_restore_argv(opencloud_root, archive_path))
        assert system_exit.value.code == EXIT_ERROR
        assert "Configuration error" in capsys.readouterr().err


def test_restore_passes_stop_timeout_to_job() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        result = RestoreJobResult(snapshot_path=Path("/snap"), archive_path=archive_path, staging_path=None)
        with (
            patch("opencloud_backup.cli.run_restore_job", return_value=result) as mock_job,
            pytest.raises(SystemExit),
        ):
            main(_restore_argv(opencloud_root, archive_path, "--stop-timeout", "300"))
        assert mock_job.call_args.kwargs["stop_timeout_seconds"] == 300


def test_restore_passes_start_timeout_to_job() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        result = RestoreJobResult(snapshot_path=Path("/snap"), archive_path=archive_path, staging_path=None)
        with (
            patch("opencloud_backup.cli.run_restore_job", return_value=result) as mock_job,
            pytest.raises(SystemExit),
        ):
            main(_restore_argv(opencloud_root, archive_path, "--start-timeout", "300"))
        assert mock_job.call_args.kwargs["start_timeout_seconds"] == 300


def test_restore_env_start_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        monkeypatch.setenv("OCB_START_TIMEOUT", "240")
        result = RestoreJobResult(snapshot_path=Path("/snap"), archive_path=archive_path, staging_path=None)
        with (
            patch("opencloud_backup.cli.run_restore_job", return_value=result) as mock_job,
            pytest.raises(SystemExit),
        ):
            main(_restore_argv(opencloud_root, archive_path))
        assert mock_job.call_args.kwargs["start_timeout_seconds"] == 240


def test_restore_invalid_start_timeout_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        with pytest.raises(SystemExit) as system_exit:
            main(_restore_argv(opencloud_root, archive_path, "--start-timeout", "0"))
        assert system_exit.value.code == EXIT_USAGE
        assert "start-timeout" in capsys.readouterr().err


def test_restore_passes_snapshot_flags_to_job() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        custom_snapshot = Path(temporary_directory) / "custom-snapshots"
        result = RestoreJobResult(snapshot_path=Path("/snap"), archive_path=archive_path, staging_path=None)
        with (
            patch("opencloud_backup.cli.run_restore_job", return_value=result) as mock_job,
            pytest.raises(SystemExit),
        ):
            main(
                _restore_argv(
                    opencloud_root,
                    archive_path,
                    "--snapshot-dir",
                    str(custom_snapshot),
                    "--keep-previous-snapshot",
                    "--no-env",
                    "--snapshot-timeout",
                    "600",
                )
            )
        kwargs = mock_job.call_args.kwargs
        assert kwargs["snapshot_base_dir"] == custom_snapshot.resolve()
        assert kwargs["keep_previous_snapshot"] is True
        assert kwargs["include_env"] is False
        assert kwargs["snapshot_timeout_seconds"] == 600


def test_restore_passes_verify_hash_and_timeouts() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        result = RestoreJobResult(snapshot_path=Path("/snap"), archive_path=archive_path, staging_path=None)
        with (
            patch("opencloud_backup.cli.run_restore_job", return_value=result) as mock_job,
            pytest.raises(SystemExit),
        ):
            main(
                _restore_argv(
                    opencloud_root,
                    archive_path,
                    "--verify-hash",
                    "--extract-timeout",
                    "120",
                    "--apply-timeout",
                    "180",
                )
            )
        kwargs = mock_job.call_args.kwargs
        assert kwargs["verify_hash"] is True
        assert kwargs["extract_timeout_seconds"] == 120
        assert kwargs["apply_timeout_seconds"] == 180


def test_restore_snapshot_dir_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        env_snapshot = Path(temporary_directory) / "env-snapshots"
        monkeypatch.setenv("OCB_SNAPSHOT_DIR", str(env_snapshot))
        result = RestoreJobResult(snapshot_path=Path("/snap"), archive_path=archive_path, staging_path=None)
        with (
            patch("opencloud_backup.cli.run_restore_job", return_value=result) as mock_job,
            pytest.raises(SystemExit),
        ):
            main(_restore_argv(opencloud_root, archive_path))
        assert mock_job.call_args.kwargs["snapshot_base_dir"] == env_snapshot.resolve()


def test_restore_snapshot_timeout_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        monkeypatch.setenv("OCB_SNAPSHOT_TIMEOUT", "900")
        result = RestoreJobResult(snapshot_path=Path("/snap"), archive_path=archive_path, staging_path=None)
        with (
            patch("opencloud_backup.cli.run_restore_job", return_value=result) as mock_job,
            pytest.raises(SystemExit),
        ):
            main(_restore_argv(opencloud_root, archive_path))
        assert mock_job.call_args.kwargs["snapshot_timeout_seconds"] == 900


def test_restore_verify_hash_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        monkeypatch.setenv("OCB_VERIFY_HASH", "true")
        result = RestoreJobResult(snapshot_path=Path("/snap"), archive_path=archive_path, staging_path=None)
        with (
            patch("opencloud_backup.cli.run_restore_job", return_value=result) as mock_job,
            pytest.raises(SystemExit),
        ):
            main(_restore_argv(opencloud_root, archive_path))
        assert mock_job.call_args.kwargs["verify_hash"] is True


def test_restore_stop_phase_logs_on_success(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        with (
            patch(
                "opencloud_backup.jobs.restore.run_prerequisite_checks",
                return_value=_ok_prereq_report(),
            ),
            patch("opencloud_backup.cli.SubprocessComposeRunner", return_value=FakeComposeRunner()),
            patch("opencloud_backup.jobs.restore.SubprocessTreeSyncer", return_value=FakeTreeSyncer()),
            patch(
                "opencloud_backup.jobs.restore.SubprocessArchiveExtractor",
            ) as mock_extractor_class,
            pytest.raises(SystemExit) as system_exit,
        ):
            mock_extractor = mock_extractor_class.return_value
            mock_extractor.list_members.return_value = ("opencloud/config/", "opencloud/data/file")
            main(_restore_argv(opencloud_root, archive_path))
        assert system_exit.value.code == EXIT_OK
        standard_error = capsys.readouterr().err
        assert "restore: stop phase started" in standard_error
        assert "restore: stop phase finished" in standard_error
        assert "restore: snapshot phase started" in standard_error
        assert "restore: snapshot phase finished" in standard_error
        assert "restore: extract phase started" in standard_error
        assert "restore: apply phase started" in standard_error
        assert "restore: up phase started" in standard_error
        assert "restore: up phase finished" in standard_error
        assert "restore: ps phase finished" in standard_error
        assert "ps-ok" in standard_error


def test_restore_non_tty_without_flag_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        stdin_patch, stdout_patch = _tty_patches(stdin_tty=False, stdout_tty=True)
        with (
            stdin_patch,
            stdout_patch,
            patch("opencloud_backup.cli.run_restore_job") as mock_job,
            pytest.raises(SystemExit) as system_exit,
        ):
            main(_restore_argv(opencloud_root, archive_path, confirm=False))
        assert system_exit.value.code == EXIT_USAGE
        standard_error = capsys.readouterr().err
        assert "--i-know-what-im-doing" in standard_error
        assert "confirmación explícita" in standard_error
        mock_job.assert_not_called()


def test_restore_non_tty_stdout_only_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        stdin_patch, stdout_patch = _tty_patches(stdin_tty=True, stdout_tty=False)
        with (
            stdin_patch,
            stdout_patch,
            patch("opencloud_backup.cli.run_restore_job") as mock_job,
            pytest.raises(SystemExit) as system_exit,
        ):
            main(_restore_argv(opencloud_root, archive_path, confirm=False))
        assert system_exit.value.code == EXIT_USAGE
        assert "confirmación explícita" in capsys.readouterr().err
        mock_job.assert_not_called()


def test_restore_flag_skips_prompt() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        result = RestoreJobResult(snapshot_path=Path("/snap"), archive_path=archive_path, staging_path=None)
        stdin_patch, stdout_patch = _tty_patches()
        with (
            stdin_patch,
            stdout_patch,
            patch("opencloud_backup.cli.run_restore_job", return_value=result) as mock_job,
            patch("builtins.input") as mock_input,
            pytest.raises(SystemExit),
        ):
            main(_restore_argv(opencloud_root, archive_path))
        mock_input.assert_not_called()
        mock_job.assert_called_once()


def test_restore_env_skips_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        monkeypatch.setenv("OCB_I_KNOW_WHAT_IM_DOING", "1")
        result = RestoreJobResult(snapshot_path=Path("/snap"), archive_path=archive_path, staging_path=None)
        stdin_patch, stdout_patch = _tty_patches()
        with (
            stdin_patch,
            stdout_patch,
            patch("opencloud_backup.cli.run_restore_job", return_value=result) as mock_job,
            patch("builtins.input") as mock_input,
            pytest.raises(SystemExit),
        ):
            main(_restore_argv(opencloud_root, archive_path, confirm=False))
        mock_input.assert_not_called()
        mock_job.assert_called_once()


def test_restore_interactive_correct_basename(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        result = RestoreJobResult(snapshot_path=Path("/snap"), archive_path=archive_path, staging_path=None)
        stdin_patch, stdout_patch = _tty_patches()
        with (
            stdin_patch,
            stdout_patch,
            patch("opencloud_backup.cli.run_restore_job", return_value=result) as mock_job,
            patch("builtins.input", return_value=archive_path.name),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(_restore_argv(opencloud_root, archive_path, confirm=False))
        assert system_exit.value.code == EXIT_OK
        assert "Confirmación de restore" in capsys.readouterr().err
        mock_job.assert_called_once()


def test_restore_interactive_wrong_basename_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        stdin_patch, stdout_patch = _tty_patches()
        with (
            stdin_patch,
            stdout_patch,
            patch("opencloud_backup.cli.run_restore_job") as mock_job,
            patch("builtins.input", return_value="wrong.tar.zst"),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(_restore_argv(opencloud_root, archive_path, confirm=False))
        assert system_exit.value.code == EXIT_ERROR
        assert "Restore cancelado" in capsys.readouterr().err
        mock_job.assert_not_called()


def test_restore_interactive_empty_input_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        stdin_patch, stdout_patch = _tty_patches()
        with (
            stdin_patch,
            stdout_patch,
            patch("opencloud_backup.cli.run_restore_job") as mock_job,
            patch("builtins.input", return_value=""),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(_restore_argv(opencloud_root, archive_path, confirm=False))
        assert system_exit.value.code == EXIT_ERROR
        assert "Restore cancelado" in capsys.readouterr().err
        mock_job.assert_not_called()


def test_restore_interactive_eof_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        stdin_patch, stdout_patch = _tty_patches()
        with (
            stdin_patch,
            stdout_patch,
            patch("opencloud_backup.cli.run_restore_job") as mock_job,
            patch("builtins.input", side_effect=EOFError),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(_restore_argv(opencloud_root, archive_path, confirm=False))
        assert system_exit.value.code == EXIT_ERROR
        assert "Restore cancelado" in capsys.readouterr().err
        mock_job.assert_not_called()


def test_restore_confirmation_summary_includes_compose_dir_when_different(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        compose_dir = Path(temporary_directory) / "compose_project"
        compose_dir.mkdir()
        (compose_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        archive_path = _archive_file(temporary_directory)
        result = RestoreJobResult(snapshot_path=Path("/snap"), archive_path=archive_path, staging_path=None)
        stdin_patch, stdout_patch = _tty_patches()
        with (
            stdin_patch,
            stdout_patch,
            patch("opencloud_backup.cli.run_restore_job", return_value=result),
            patch("builtins.input", return_value=archive_path.name),
            pytest.raises(SystemExit),
        ):
            main(
                _restore_argv(
                    opencloud_root,
                    archive_path,
                    "--compose-dir",
                    str(compose_dir),
                    confirm=False,
                )
            )
        assert f"compose_dir: {compose_dir.resolve()}" in capsys.readouterr().err


def test_restore_confirmation_summary_omits_compose_dir_when_same(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        result = RestoreJobResult(snapshot_path=Path("/snap"), archive_path=archive_path, staging_path=None)
        stdin_patch, stdout_patch = _tty_patches()
        with (
            stdin_patch,
            stdout_patch,
            patch("opencloud_backup.cli.run_restore_job", return_value=result),
            patch("builtins.input", return_value=archive_path.name),
            pytest.raises(SystemExit),
        ):
            main(_restore_argv(opencloud_root, archive_path, confirm=False))
        assert "compose_dir:" not in capsys.readouterr().err


def test_restore_confirmation_summary_lists_active_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        archive_path = _archive_file(temporary_directory)
        result = RestoreJobResult(snapshot_path=Path("/snap"), archive_path=archive_path, staging_path=None)
        stdin_patch, stdout_patch = _tty_patches()
        with (
            stdin_patch,
            stdout_patch,
            patch("opencloud_backup.cli.run_restore_job", return_value=result),
            patch("builtins.input", return_value=archive_path.name),
            pytest.raises(SystemExit),
        ):
            main(
                _restore_argv(
                    opencloud_root,
                    archive_path,
                    "--no-env",
                    "--keep-previous-snapshot",
                    "--verify-hash",
                    confirm=False,
                )
            )
        standard_error = capsys.readouterr().err
        assert "  --no-env" in standard_error
        assert "  --keep-previous-snapshot" in standard_error
        assert "  --verify-hash" in standard_error
