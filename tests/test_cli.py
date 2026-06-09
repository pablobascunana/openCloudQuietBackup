import argparse
import tempfile
from pathlib import Path

import pytest
from conftest import make_valid_stack_tree

from opencloud_backup.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, build_parser, cmd_validate, main


def test_validate_missing_root_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["validate"])
    assert exc.value.code == EXIT_USAGE
    assert "OCB_OPENCLOUD_ROOT" in capsys.readouterr().err


def test_validate_happy_path_exit_0(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "oc"
        make_valid_stack_tree(root)
        with pytest.raises(SystemExit) as exc:
            main(["validate", "--opencloud-root", str(root)])
        assert exc.value.code == EXIT_OK
        out = capsys.readouterr().out
        assert "Configuración válida" in out
        assert str(root.resolve()) in out


def test_validate_config_error_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "oc"
        root.mkdir()
        with pytest.raises(SystemExit) as exc:
            main(["validate", "--opencloud-root", str(root)])
        assert exc.value.code == EXIT_ERROR
        assert "Error de configuración" in capsys.readouterr().err


def test_validate_reads_env_vars(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "oc"
        make_valid_stack_tree(root)
        monkeypatch.setenv("OCB_OPENCLOUD_ROOT", str(root))
        with pytest.raises(SystemExit) as exc:
            main(["validate"])
        assert exc.value.code == EXIT_OK
        assert "Configuración válida" in capsys.readouterr().out


def test_build_parser_validate_help() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["validate", "--help"])


def test_cmd_validate_directly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "oc"
        make_valid_stack_tree(root)
        args = argparse.Namespace(
            opencloud_root=root,
            compose_dir=None,
            compose_file=None,
        )
        assert cmd_validate(args) == EXIT_OK
