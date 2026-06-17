from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from conftest import make_backup_output_dir, make_valid_stack_tree
from opencloud_backup.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, build_argument_parser, main
from opencloud_backup.jobs.retention import RetentionResult


def _setup_opencloud_root(temporary_directory: str) -> Path:
    opencloud_root = Path(temporary_directory) / "oc"
    make_valid_stack_tree(opencloud_root)
    make_backup_output_dir(opencloud_root)
    return opencloud_root


def test_retention_missing_keep_limits_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with pytest.raises(SystemExit) as system_exit:
            main(["retention", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_USAGE
        assert "debe indicar --keep-days y/o --keep-count" in capsys.readouterr().err


def test_retention_success_stdout_spanish(capsys: pytest.CaptureFixture[str]) -> None:
    deleted_archive = Path("/backups/opencloud-2026-05-01_120000.tar.zst")
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with (
            patch(
                "opencloud_backup.cli.run_retention_job",
                return_value=RetentionResult((deleted_archive,), ()),
            ),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["retention", "--opencloud-root", str(opencloud_root), "--keep-count", "5"])
        assert system_exit.value.code == EXIT_OK
        captured = capsys.readouterr()
        assert "Retención completada." in captured.out
        assert "archivos eliminados: 1" in captured.out
        assert str(deleted_archive) in captured.out


def test_retention_ocb_keep_days_env(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OCB_KEEP_DAYS", "30")
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with (
            patch(
                "opencloud_backup.cli.run_retention_job",
                return_value=RetentionResult((), ()),
            ) as mock_retention,
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["retention", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_OK
        mock_retention.assert_called_once()
        assert mock_retention.call_args.kwargs["policy"].max_age_days == 30


def test_retention_keep_days_zero_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with pytest.raises(SystemExit) as system_exit:
            main(["retention", "--opencloud-root", str(opencloud_root), "--keep-days", "0"])
        assert system_exit.value.code == EXIT_ERROR
        captured = capsys.readouterr()
        assert "Error de configuración:" in captured.err
        assert "keep-days debe ser al menos 1" in captured.err


def test_retention_missing_opencloud_root_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as system_exit:
        main(["retention", "--keep-count", "5"])
    assert system_exit.value.code == EXIT_USAGE
    assert "OCB_OPENCLOUD_ROOT" in capsys.readouterr().err


def test_retention_delete_failure_exit_1_spanish(capsys: pytest.CaptureFixture[str]) -> None:
    from opencloud_backup.domain.errors import RetentionError

    failed_path = Path("/backups/opencloud-2026-05-01_120000.tar.zst")
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = _setup_opencloud_root(temporary_directory)
        with (
            patch(
                "opencloud_backup.cli.run_retention_job",
                side_effect=RetentionError(failed_path),
            ),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["retention", "--opencloud-root", str(opencloud_root), "--keep-count", "1"])
        assert system_exit.value.code == EXIT_ERROR
        assert "Error de retención: no se pudo eliminar" in capsys.readouterr().err


def test_retention_missing_output_dir_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        with pytest.raises(SystemExit) as system_exit:
            main(["retention", "--opencloud-root", str(opencloud_root), "--keep-count", "5"])
        assert system_exit.value.code == EXIT_ERROR
        standard_error = capsys.readouterr().err
        assert "does not exist" in standard_error
        assert "--create-output-dir" not in standard_error


def test_build_argument_parser_includes_retention_subcommand() -> None:
    argument_parser = build_argument_parser()
    parsed_arguments = argument_parser.parse_args(
        ["retention", "--opencloud-root", "/data/oc", "--keep-count", "5"]
    )
    assert parsed_arguments.command == "retention"
    assert parsed_arguments.keep_count == 5
