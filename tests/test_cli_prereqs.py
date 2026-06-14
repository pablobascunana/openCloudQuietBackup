from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import make_valid_stack_tree

from opencloud_backup.cli import DOCKER_PS_FAILURE_HINT, EXIT_ERROR, EXIT_OK, EXIT_USAGE, main
from opencloud_backup.domain.prereqs import DiskCheckResult, DiskThreshold, JobMode, PrerequisiteReport


def _ok_report(mode: JobMode = JobMode.ALL) -> PrerequisiteReport:
    return PrerequisiteReport(
        ok=True,
        mode=mode,
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


def _failed_report() -> PrerequisiteReport:
    return PrerequisiteReport(
        ok=False,
        mode=JobMode.ALL,
        missing_binaries=("rsync",),
        failed_commands=(),
        disk=DiskCheckResult(
            path=Path("/data"),
            total_bytes=100 * 1024**3,
            free_bytes=50 * 1024**3,
            threshold=None,
            ok=True,
        ),
    )


def _docker_ps_failed_report() -> PrerequisiteReport:
    return PrerequisiteReport(
        ok=False,
        mode=JobMode.ALL,
        missing_binaries=(),
        failed_commands=("docker ps",),
        disk=DiskCheckResult(
            path=Path("/data"),
            total_bytes=100 * 1024**3,
            free_bytes=50 * 1024**3,
            threshold=None,
            ok=True,
        ),
    )


def test_prereqs_missing_root_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as system_exit:
        main(["prereqs"])
    assert system_exit.value.code == EXIT_USAGE
    assert "OCB_OPENCLOUD_ROOT" in capsys.readouterr().err


def test_prereqs_happy_path_exit_0(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        with (
            patch("opencloud_backup.cli.run_prerequisite_checks", return_value=_ok_report()),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["prereqs", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_OK
        output = capsys.readouterr().out
        assert "Prerequisites OK" in output
        assert "Docker daemon: OK" in output
        assert "Stack path access: OK" in output


def test_prereqs_failure_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        with (
            patch("opencloud_backup.cli.run_prerequisite_checks", return_value=_failed_report()),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["prereqs", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_ERROR
        standard_error = capsys.readouterr().err
        assert "Prerequisite error" in standard_error
        assert "Missing binaries" in standard_error
        assert "rsync" in standard_error


def test_prereqs_docker_ps_failure_shows_hint(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        with (
            patch(
                "opencloud_backup.cli.run_prerequisite_checks",
                return_value=_docker_ps_failed_report(),
            ),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["prereqs", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_ERROR
        standard_error = capsys.readouterr().err
        assert "Failed commands: docker ps" in standard_error
        assert DOCKER_PS_FAILURE_HINT in standard_error


def test_prereqs_passes_stack_paths_to_checks() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        with (
            patch("opencloud_backup.cli.run_prerequisite_checks", return_value=_ok_report()) as mock_checks,
            pytest.raises(SystemExit),
        ):
            main(["prereqs", "--opencloud-root", str(opencloud_root)])
        call_kwargs = mock_checks.call_args.kwargs
        assert call_kwargs["stack_paths"].opencloud_root == opencloud_root.resolve()
        assert call_kwargs["stack_paths"].config_dir == (opencloud_root / "config").resolve()


def test_prereqs_mutually_exclusive_thresholds_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        with pytest.raises(SystemExit) as system_exit:
            main(
                [
                    "prereqs",
                    "--opencloud-root",
                    str(opencloud_root),
                    "--min-free-bytes",
                    "1000",
                    "--min-free-percent",
                    "10",
                ]
            )
        assert system_exit.value.code == EXIT_USAGE
        assert "mutually exclusive" in capsys.readouterr().err


def test_prereqs_env_min_free_bytes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        monkeypatch.setenv("OCB_OPENCLOUD_ROOT", str(opencloud_root))
        monkeypatch.setenv("OCB_MIN_FREE_BYTES", "1073741824")
        with (
            patch("opencloud_backup.cli.run_prerequisite_checks", return_value=_ok_report()) as mock_checks,
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["prereqs"])
        assert system_exit.value.code == EXIT_OK
        mock_checks.assert_called_once()
        call_kwargs = mock_checks.call_args.kwargs
        assert call_kwargs["disk_threshold"] == DiskThreshold(kind="bytes", value=1073741824)
        assert "stack_paths" in call_kwargs


def test_prereqs_config_error_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "missing"
        with pytest.raises(SystemExit) as system_exit:
            main(["prereqs", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_ERROR
        assert "Configuration error" in capsys.readouterr().err


def test_prereqs_passes_compose_flags_to_load_stack_paths() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        compose_dir = opencloud_root / "compose"
        make_valid_stack_tree(opencloud_root)
        compose_dir.mkdir()
        custom_compose = compose_dir / "stack.yml"
        custom_compose.write_text("services: {}\n", encoding="utf-8")
        with (
            patch("opencloud_backup.cli.run_prerequisite_checks", return_value=_ok_report()) as mock_checks,
            pytest.raises(SystemExit),
        ):
            main(
                [
                    "prereqs",
                    "--opencloud-root",
                    str(opencloud_root),
                    "--compose-dir",
                    str(compose_dir),
                    "--compose-file",
                    "stack.yml",
                ]
            )
        stack_paths = mock_checks.call_args.kwargs["stack_paths"]
        assert stack_paths.compose_dir == compose_dir.resolve()
        assert stack_paths.compose_file == custom_compose.resolve()


def test_prereqs_compose_file_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        monkeypatch.setenv("OCB_OPENCLOUD_ROOT", str(opencloud_root))
        monkeypatch.setenv("OCB_COMPOSE_DIR", str(opencloud_root))
        with (
            patch("opencloud_backup.cli.load_stack_paths") as mock_load,
            patch("opencloud_backup.cli.run_prerequisite_checks", return_value=_ok_report()),
            pytest.raises(SystemExit),
        ):
            main(["prereqs"])
        mock_load.assert_called_once()
        call_kwargs = mock_load.call_args.kwargs
        assert call_kwargs["opencloud_root"] == opencloud_root
        assert call_kwargs["compose_dir"] == opencloud_root
