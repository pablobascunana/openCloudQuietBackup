import argparse
import tempfile
from pathlib import Path

import pytest
from conftest import make_valid_stack_tree

from opencloud_backup.cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    build_argument_parser,
    main,
    run_validate_command,
)


def test_validate_missing_root_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as system_exit:
        main(["validate"])
    assert system_exit.value.code == EXIT_USAGE
    assert "OCB_OPENCLOUD_ROOT" in capsys.readouterr().err


def test_validate_happy_path_exit_0(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        with pytest.raises(SystemExit) as system_exit:
            main(["validate", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_OK
        standard_output = capsys.readouterr().out
        assert "Valid configuration" in standard_output
        assert str(opencloud_root.resolve()) in standard_output


def test_validate_config_error_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        opencloud_root.mkdir()
        with pytest.raises(SystemExit) as system_exit:
            main(["validate", "--opencloud-root", str(opencloud_root)])
        assert system_exit.value.code == EXIT_ERROR
        assert "Configuration error" in capsys.readouterr().err


def test_validate_reads_env_vars(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        monkeypatch.setenv("OCB_OPENCLOUD_ROOT", str(opencloud_root))
        with pytest.raises(SystemExit) as system_exit:
            main(["validate"])
        assert system_exit.value.code == EXIT_OK
        assert "Valid configuration" in capsys.readouterr().out


def test_build_argument_parser_validate_help() -> None:
    argument_parser = build_argument_parser()
    with pytest.raises(SystemExit):
        argument_parser.parse_args(["validate", "--help"])


def test_run_validate_command_directly() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        parsed_arguments = argparse.Namespace(
            opencloud_root=opencloud_root,
            compose_dir=None,
            compose_file=None,
        )
        assert run_validate_command(parsed_arguments) == EXIT_OK
