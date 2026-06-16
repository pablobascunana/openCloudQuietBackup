from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from opencloud_backup.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main
from opencloud_backup.domain.errors import HashMismatchError, SidecarNotFoundError


def test_verify_missing_archive_arg_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as system_exit:
        main(["verify"])
    assert system_exit.value.code == EXIT_USAGE
    assert "--archive" in capsys.readouterr().err


def test_verify_missing_archive_file_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        missing_archive = Path(temporary_directory) / "missing.tar.zst"
        with pytest.raises(SystemExit) as system_exit:
            main(["verify", "--archive", str(missing_archive)])
        assert system_exit.value.code == EXIT_ERROR
        assert "el archivo de backup no existe o no es accesible" in capsys.readouterr().err


def test_verify_happy_path_exit_0(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        archive_path = Path(temporary_directory) / "opencloud-2026-06-14_101530.tar.zst"
        archive_path.write_bytes(b"archive")
        sidecar_path = Path(f"{archive_path}.sha256")
        with (
            patch("opencloud_backup.cli.verify_archive_integrity", return_value=None),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["verify", "--archive", str(archive_path)])
        assert system_exit.value.code == EXIT_OK
        captured = capsys.readouterr()
        assert "Integridad verificada correctamente." in captured.out
        assert f"archive: {archive_path.resolve()}" in captured.out
        assert f"sidecar: {sidecar_path.resolve()}" in captured.out


def test_verify_missing_sidecar_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        archive_path = Path(temporary_directory) / "opencloud-2026-06-14_101530.tar.zst"
        archive_path.write_bytes(b"archive")
        with (
            patch(
                "opencloud_backup.cli.verify_archive_integrity",
                side_effect=SidecarNotFoundError(Path(f"{archive_path}.sha256")),
            ),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["verify", "--archive", str(archive_path)])
        assert system_exit.value.code == EXIT_ERROR
        assert "no se encontró el fichero sidecar .sha256" in capsys.readouterr().err


def test_verify_hash_mismatch_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        archive_path = Path(temporary_directory) / "opencloud-2026-06-14_101530.tar.zst"
        archive_path.write_bytes(b"archive")
        with (
            patch(
                "opencloud_backup.cli.verify_archive_integrity",
                side_effect=HashMismatchError(
                    archive_path=archive_path,
                    expected_hex="a" * 64,
                    actual_hex="b" * 64,
                ),
            ),
            pytest.raises(SystemExit) as system_exit,
        ):
            main(["verify", "--archive", str(archive_path)])
        assert system_exit.value.code == EXIT_ERROR
        standard_error = capsys.readouterr().err
        assert "el hash del archivo no coincide con el sidecar" in standard_error
        assert "a" * 64 not in standard_error
        assert "b" * 64 not in standard_error
