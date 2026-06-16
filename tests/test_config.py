import os
import tempfile
from pathlib import Path

import pytest
from conftest import make_valid_stack_tree

from opencloud_backup.config import (
    ValidationError,
    load_stack_paths,
    resolve_backup_output_dir,
    resolve_compose_file,
    validate_backup_output_dir,
)


def test_prefers_yml_over_yaml_when_both_exist() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        compose_directory = Path(temporary_directory)
        (compose_directory / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
        (compose_directory / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        resolved_compose_file = resolve_compose_file(compose_directory, None)
        assert resolved_compose_file.name == "docker-compose.yml"


def test_uses_yaml_if_only_yaml() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        compose_directory = Path(temporary_directory)
        (compose_directory / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
        resolved_compose_file = resolve_compose_file(compose_directory, None)
        assert resolved_compose_file.name == "docker-compose.yaml"


def test_explicit_file_wins() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        compose_directory = Path(temporary_directory)
        custom_compose_file = compose_directory / "compose.custom.yml"
        custom_compose_file.write_text("services: {}\n", encoding="utf-8")
        resolved_compose_file = resolve_compose_file(compose_directory, custom_compose_file)
        assert resolved_compose_file == custom_compose_file.resolve()


def test_relative_compose_file_resolved_from_compose_dir() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        compose_directory = Path(temporary_directory)
        nested_compose_file = compose_directory / "subdir" / "compose.yml"
        nested_compose_file.parent.mkdir()
        nested_compose_file.write_text("services: {}\n", encoding="utf-8")
        resolved_compose_file = resolve_compose_file(compose_directory, Path("subdir/compose.yml"))
        assert resolved_compose_file == nested_compose_file.resolve()


def test_explicit_compose_file_missing() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        compose_directory = Path(temporary_directory)
        with pytest.raises(ValidationError, match="does not exist"):
            resolve_compose_file(compose_directory, compose_directory / "missing.yml")


def test_explicit_compose_file_not_a_file() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        compose_directory = Path(temporary_directory)
        (compose_directory / "not-a-file").mkdir()
        with pytest.raises(ValidationError, match="not a regular file"):
            resolve_compose_file(compose_directory, compose_directory / "not-a-file")


def test_no_compose_file_in_directory() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        compose_directory = Path(temporary_directory)
        with pytest.raises(ValidationError, match="Neither"):
            resolve_compose_file(compose_directory, None)


def test_happy_path_default_compose_dir() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        stack_paths = load_stack_paths(opencloud_root)
        assert stack_paths.opencloud_root == opencloud_root.resolve()
        assert stack_paths.compose_dir == opencloud_root.resolve()
        assert stack_paths.compose_file == (opencloud_root / "docker-compose.yml").resolve()


def test_separate_compose_dir() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        compose_directory = Path(temporary_directory) / "compose_project"
        make_valid_stack_tree(opencloud_root)
        compose_directory.mkdir()
        (compose_directory / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        stack_paths = load_stack_paths(opencloud_root, compose_dir=compose_directory)
        assert stack_paths.compose_dir == compose_directory.resolve()
        assert stack_paths.compose_file == (compose_directory / "docker-compose.yml").resolve()


def test_opencloud_root_missing() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        missing_opencloud_root = Path(temporary_directory) / "does-not-exist"
        with pytest.raises(ValidationError, match="does not exist"):
            load_stack_paths(missing_opencloud_root)


def test_requires_config_and_data() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        opencloud_root.mkdir()
        (opencloud_root / "docker-compose.yml").write_text("x", encoding="utf-8")
        (opencloud_root / "config").mkdir()
        with pytest.raises(ValidationError, match="data"):
            load_stack_paths(opencloud_root)


def test_readable_check() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        os.chmod(opencloud_root / "config", 0)
        try:
            if os.access(opencloud_root / "config", os.R_OK):
                pytest.skip("root can read directories without permission in this environment")
            with pytest.raises(ValidationError, match="permission"):
                load_stack_paths(opencloud_root)
        finally:
            os.chmod(opencloud_root / "config", 0o755)


def test_validate_backup_output_dir_happy_path() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        output_dir = Path(temporary_directory) / "backups"
        output_dir.mkdir()
        assert validate_backup_output_dir(output_dir) == output_dir.resolve()


def test_validate_backup_output_dir_missing() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        missing_dir = Path(temporary_directory) / "missing"
        with pytest.raises(ValidationError, match="Output directory does not exist:"):
            validate_backup_output_dir(missing_dir)


def test_validate_backup_output_dir_not_a_directory() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        output_file = Path(temporary_directory) / "not-a-dir"
        output_file.write_text("x", encoding="utf-8")
        with pytest.raises(ValidationError, match="Output directory exists but is not a directory:"):
            validate_backup_output_dir(output_file)


def test_validate_backup_output_dir_not_writable() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        output_dir = Path(temporary_directory) / "backups"
        output_dir.mkdir()
        os.chmod(output_dir, 0o555)
        try:
            if os.access(output_dir, os.W_OK):
                pytest.skip("root can write directories without permission in this environment")
            with pytest.raises(ValidationError, match="Output directory is not writable:"):
                validate_backup_output_dir(output_dir)
        finally:
            os.chmod(output_dir, 0o755)


def test_resolve_backup_output_dir_default_under_root() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        backups_dir = opencloud_root / "backups"
        backups_dir.mkdir()
        resolved = resolve_backup_output_dir(opencloud_root.resolve())
        assert resolved == backups_dir.resolve()


def test_resolve_backup_output_dir_explicit_path() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        opencloud_root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(opencloud_root)
        custom_output = Path(temporary_directory) / "custom-backups"
        custom_output.mkdir()
        resolved = resolve_backup_output_dir(opencloud_root.resolve(), custom_output)
        assert resolved == custom_output.resolve()
