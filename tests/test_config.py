import os
import tempfile
from pathlib import Path

import pytest
from conftest import make_valid_stack_tree

from opencloud_backup.config import ValidationError, load_stack_paths, resolve_compose_file


def test_prefers_yml_over_yaml_when_both_exist() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
        (d / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        got = resolve_compose_file(d, None)
        assert got.name == "docker-compose.yml"


def test_uses_yaml_if_only_yaml() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
        got = resolve_compose_file(d, None)
        assert got.name == "docker-compose.yaml"


def test_explicit_file_wins() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        f = d / "compose.custom.yml"
        f.write_text("services: {}\n", encoding="utf-8")
        got = resolve_compose_file(d, f)
        assert got == f.resolve()


def test_relative_compose_file_resolved_from_compose_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        f = d / "subdir" / "compose.yml"
        f.parent.mkdir()
        f.write_text("services: {}\n", encoding="utf-8")
        got = resolve_compose_file(d, Path("subdir/compose.yml"))
        assert got == f.resolve()


def test_explicit_compose_file_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        with pytest.raises(ValidationError, match="no existe"):
            resolve_compose_file(d, d / "missing.yml")


def test_explicit_compose_file_not_a_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "not-a-file").mkdir()
        with pytest.raises(ValidationError, match="no es un fichero"):
            resolve_compose_file(d, d / "not-a-file")


def test_no_compose_file_in_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        with pytest.raises(ValidationError, match="No se encontró"):
            resolve_compose_file(d, None)


def test_happy_path_default_compose_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "oc"
        make_valid_stack_tree(root)
        p = load_stack_paths(root)
        assert p.opencloud_root == root.resolve()
        assert p.compose_dir == root.resolve()
        assert p.compose_file == (root / "docker-compose.yml").resolve()


def test_separate_compose_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "oc"
        compose = Path(tmp) / "compose_project"
        make_valid_stack_tree(root)
        compose.mkdir()
        (compose / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        p = load_stack_paths(root, compose_dir=compose)
        assert p.compose_dir == compose.resolve()
        assert p.compose_file == (compose / "docker-compose.yml").resolve()


def test_opencloud_root_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "does-not-exist"
        with pytest.raises(ValidationError, match="no existe"):
            load_stack_paths(missing)


def test_requires_config_and_data() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "oc"
        root.mkdir()
        (root / "docker-compose.yml").write_text("x", encoding="utf-8")
        (root / "config").mkdir()
        with pytest.raises(ValidationError, match="data"):
            load_stack_paths(root)


def test_readable_check() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "oc"
        make_valid_stack_tree(root)
        os.chmod(root / "config", 0)
        try:
            if os.access(root / "config", os.R_OK):
                pytest.skip("root puede leer directorios sin permiso en este entorno")
            with pytest.raises(ValidationError, match="permiso"):
                load_stack_paths(root)
        finally:
            os.chmod(root / "config", 0o755)
