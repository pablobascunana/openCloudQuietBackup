"""Tests para la CLI (subcomando validate)."""

import argparse
import tempfile
from pathlib import Path

import pytest

from opencloud_backup.cli import build_parser, cmd_validate, main


def _make_valid_tree(root: Path) -> None:
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir(parents=True)
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")


def test_validate_missing_root_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["validate"])
    assert exc.value.code == 2
    assert "OCB_OPENCLOUD_ROOT" in capsys.readouterr().err


def test_validate_happy_path_exit_0(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "oc"
        _make_valid_tree(root)
        with pytest.raises(SystemExit) as exc:
            main(["validate", "--opencloud-root", str(root)])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "Configuración válida" in out
        assert str(root.resolve()) in out


def test_validate_config_error_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "oc"
        root.mkdir()
        with pytest.raises(SystemExit) as exc:
            main(["validate", "--opencloud-root", str(root)])
        assert exc.value.code == 1
        assert "Error de configuración" in capsys.readouterr().err


def test_validate_reads_env_vars(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "oc"
        _make_valid_tree(root)
        monkeypatch.setenv("OCB_OPENCLOUD_ROOT", str(root))
        with pytest.raises(SystemExit) as exc:
            main(["validate"])
        assert exc.value.code == 0
        assert "Configuración válida" in capsys.readouterr().out


def test_build_parser_validate_help() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["validate", "--help"])


def test_cmd_validate_directly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "oc"
        _make_valid_tree(root)
        args = argparse.Namespace(
            opencloud_root=root,
            compose_dir=None,
            compose_file=None,
        )
        assert cmd_validate(args) == 0
