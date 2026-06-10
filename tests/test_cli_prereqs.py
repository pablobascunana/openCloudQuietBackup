from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import make_valid_stack_tree

from opencloud_backup.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main
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
        assert "Prerequisitos OK" in capsys.readouterr().out


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
        assert "Error de prerequisitos" in standard_error
        assert "rsync" in standard_error


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
        assert "excluyentes" in capsys.readouterr().err


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


def test_prereqs_config_error_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "missing"
        with pytest.raises(SystemExit) as system_exit:
            main(["prereqs", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_ERROR
        assert "Error de configuración" in capsys.readouterr().err
