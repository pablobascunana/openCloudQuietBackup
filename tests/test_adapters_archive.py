from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from opencloud_backup.adapters.archive import (
    ARCHIVE_CREATE_COMMAND_LABEL,
    ARCHIVE_EXTRACT_COMMAND_LABEL,
    ARCHIVE_LIST_COMMAND_LABEL,
    SubprocessArchiveBuilder,
    SubprocessArchiveExtractor,
    build_gzip_argv,
    build_gzip_decompress_argv,
    build_tar_create_argv,
    build_tar_extract_argv,
    build_tar_list_argv,
    build_zstd_argv,
    build_zstd_decompress_argv,
)
from opencloud_backup.config import StackPaths
from opencloud_backup.domain.archive import TAR_TRANSFORM, CompressionFormat
from opencloud_backup.domain.errors import ArchiveCommandError


def _stack_paths(root: Path) -> StackPaths:
    return StackPaths(
        opencloud_root=root,
        config_dir=root / "config",
        data_dir=root / "data",
        compose_dir=root,
        compose_file=root / "docker-compose.yml",
    )


def test_build_tar_create_argv_pipeline() -> None:
    root = Path("/data/opencloud")
    stack_paths = _stack_paths(root)
    assert build_tar_create_argv(stack_paths, include_env=False) == [
        "tar",
        "-C",
        str(root),
        "--xattrs",
        "--acls",
        "--numeric-owner",
        "--transform",
        TAR_TRANSFORM,
        "-cf",
        "-",
        "config",
        "data",
    ]


def test_build_tar_create_argv_with_env_file(tmp_path: Path) -> None:
    root = tmp_path / "oc"
    root.mkdir()
    (root / ".env").write_text("A=1\n", encoding="utf-8")
    stack_paths = _stack_paths(root)
    argv = build_tar_create_argv(stack_paths, include_env=True)
    assert ".env" in argv


def test_build_tar_create_argv_to_file() -> None:
    root = Path("/data/opencloud")
    stack_paths = _stack_paths(root)
    argv = build_tar_create_argv(stack_paths, include_env=False, output_file="/backups/x.tar")
    assert argv[-3] == "/backups/x.tar"


def test_build_zstd_argv() -> None:
    archive_path = Path("/backups/opencloud.tar.zst")
    assert build_zstd_argv(archive_path) == ["zstd", "-T0", "-o", str(archive_path)]


def test_build_gzip_argv() -> None:
    assert build_gzip_argv() == ["gzip", "-c"]


def test_build_tar_list_argv_stdin() -> None:
    assert build_tar_list_argv() == ["tar", "-tf", "-"]


def test_build_tar_list_argv_file() -> None:
    assert build_tar_list_argv(archive_file="/backups/x.tar") == ["tar", "-tf", "/backups/x.tar"]


def test_build_tar_extract_argv() -> None:
    dest = Path("/staging")
    argv = build_tar_extract_argv(dest)
    assert argv == [
        "tar",
        "--xattrs",
        "--acls",
        "--numeric-owner",
        "-xf",
        "-",
        "-C",
        str(dest),
    ]


def test_build_zstd_decompress_argv() -> None:
    archive_path = Path("/backups/opencloud.tar.zst")
    assert build_zstd_decompress_argv(archive_path) == ["zstd", "-d", "-c", str(archive_path)]


def test_build_gzip_decompress_argv() -> None:
    archive_path = Path("/backups/opencloud.tar.gz")
    assert build_gzip_decompress_argv(archive_path) == ["gzip", "-dc", str(archive_path)]


def test_list_members_uncompressed_success(tmp_path: Path) -> None:
    archive_path = tmp_path / "opencloud.tar"
    archive_path.write_bytes(b"tar")

    def run_command(command_argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=command_argv,
            returncode=0,
            stdout="opencloud/config/\nopencloud/data/file\n",
            stderr="",
        )

    extractor = SubprocessArchiveExtractor(run_command=run_command)
    members = extractor.list_members(archive_path, compression=CompressionFormat.NONE)
    assert members == ("opencloud/config/", "opencloud/data/file")


def test_list_members_uncompressed_failure(tmp_path: Path) -> None:
    archive_path = tmp_path / "opencloud.tar"
    archive_path.write_bytes(b"tar")

    def run_command(command_argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=command_argv, returncode=1, stdout="", stderr="list failed")

    extractor = SubprocessArchiveExtractor(run_command=run_command)
    with pytest.raises(ArchiveCommandError) as error_info:
        extractor.list_members(archive_path, compression=CompressionFormat.NONE)
    assert error_info.value.command_label == ARCHIVE_LIST_COMMAND_LABEL


def test_extract_uncompressed_success(tmp_path: Path) -> None:
    archive_path = tmp_path / "opencloud.tar"
    archive_path.write_bytes(b"tar")
    dest_dir = tmp_path / "staging"
    dest_dir.mkdir()

    def run_command(command_argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert "-C" in command_argv
        assert str(dest_dir) in command_argv
        return subprocess.CompletedProcess(args=command_argv, returncode=0, stdout="", stderr="")

    extractor = SubprocessArchiveExtractor(run_command=run_command)
    extractor.extract_archive(archive_path, dest_dir, compression=CompressionFormat.NONE)


def test_extract_uncompressed_timeout(tmp_path: Path) -> None:
    archive_path = tmp_path / "opencloud.tar"
    archive_path.write_bytes(b"tar")
    dest_dir = tmp_path / "staging"
    dest_dir.mkdir()

    def run_command(_command_argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="tar", timeout=30)

    extractor = SubprocessArchiveExtractor(run_command=run_command)
    with pytest.raises(ArchiveCommandError) as error_info:
        extractor.extract_archive(
            archive_path,
            dest_dir,
            compression=CompressionFormat.NONE,
            timeout_seconds=30,
        )
    assert error_info.value.command_label == ARCHIVE_EXTRACT_COMMAND_LABEL
    assert error_info.value.return_code == -1


def test_list_members_zstd_pipeline(tmp_path: Path) -> None:
    archive_path = tmp_path / "opencloud.tar.zst"
    archive_path.write_bytes(b"zst")

    class FakeProcess:
        def __init__(self, returncode: int = 0) -> None:
            self.returncode = returncode
            self.stdin = MagicMock()
            self.stdout = MagicMock()
            self.stdout.read.return_value = b"opencloud/config/\n"
            self.stderr = MagicMock()
            self.stderr.read.return_value = b""

        def wait(self, timeout: int | None = None) -> int:
            return self.returncode

    def popen(command_argv: list[str], **_kwargs: object) -> FakeProcess:
        if command_argv[0] == "zstd":
            assert command_argv == build_zstd_decompress_argv(archive_path)
        return FakeProcess()

    extractor = SubprocessArchiveExtractor(popen=popen)
    members = extractor.list_members(archive_path, compression=CompressionFormat.ZSTD)
    assert members == ("opencloud/config/",)


def test_create_uncompressed_success(tmp_path: Path) -> None:
    root = tmp_path / "oc"
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    output_dir = tmp_path / "backups"
    output_dir.mkdir()
    stack_paths = _stack_paths(root)

    def run_command(command_argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        Path(command_argv[command_argv.index("-cf") + 1]).write_bytes(b"tarbytes")
        return subprocess.CompletedProcess(args=command_argv, returncode=0, stdout="", stderr="")

    builder = SubprocessArchiveBuilder(run_command=run_command)
    archive_path = builder.create_backup_archive(
        stack_paths,
        output_dir=output_dir,
        compression=CompressionFormat.NONE,
        include_env=False,
    )
    assert archive_path.exists()


def test_create_uncompressed_failure_removes_partial(tmp_path: Path) -> None:
    root = tmp_path / "oc"
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    output_dir = tmp_path / "backups"
    output_dir.mkdir()
    stack_paths = _stack_paths(root)

    def run_command(command_argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        partial_path = Path(command_argv[command_argv.index("-cf") + 1])
        partial_path.write_bytes(b"partial")
        return subprocess.CompletedProcess(args=command_argv, returncode=1, stdout="", stderr="tar failed")

    builder = SubprocessArchiveBuilder(run_command=run_command)
    with pytest.raises(ArchiveCommandError) as error_info:
        builder.create_backup_archive(
            stack_paths,
            output_dir=output_dir,
            compression=CompressionFormat.NONE,
            include_env=False,
        )
    assert error_info.value.command_label == ARCHIVE_CREATE_COMMAND_LABEL
    assert not any(output_dir.glob("opencloud-*.tar"))


def test_create_zstd_pipeline_success(tmp_path: Path) -> None:
    root = tmp_path / "oc"
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    output_dir = tmp_path / "backups"
    output_dir.mkdir()
    stack_paths = _stack_paths(root)

    class FakeProcess:
        def __init__(self, returncode: int = 0) -> None:
            self.returncode = returncode
            self.stdin = MagicMock()
            self.stderr = MagicMock()
            self.stderr.read.return_value = b""

        def wait(self, timeout: int | None = None) -> int:
            return self.returncode

    def popen(command_argv: list[str], **_kwargs: object) -> FakeProcess:
        if command_argv[0] == "zstd":
            archive_path = Path(command_argv[-1])
            archive_path.write_bytes(b"zst")
        return FakeProcess()

    builder = SubprocessArchiveBuilder(popen=popen)
    archive_path = builder.create_backup_archive(
        stack_paths,
        output_dir=output_dir,
        compression=CompressionFormat.ZSTD,
        include_env=False,
    )
    assert archive_path.suffix == ".zst"


def test_create_zstd_tar_failure_removes_partial(tmp_path: Path) -> None:
    root = tmp_path / "oc"
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    output_dir = tmp_path / "backups"
    output_dir.mkdir()
    stack_paths = _stack_paths(root)

    class FakeProcess:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode
            self.stdin = MagicMock()
            self.stderr = MagicMock()
            self.stderr.read.return_value = b"tar boom"

        def wait(self, timeout: int | None = None) -> int:
            return self.returncode

    call_index = {"n": 0}

    def popen(command_argv: list[str], **_kwargs: object) -> FakeProcess:
        call_index["n"] += 1
        if command_argv[0] == "zstd":
            Path(command_argv[-1]).write_bytes(b"partial")
            return FakeProcess(0)
        return FakeProcess(1)

    builder = SubprocessArchiveBuilder(popen=popen)
    with pytest.raises(ArchiveCommandError):
        builder.create_backup_archive(
            stack_paths,
            output_dir=output_dir,
            compression=CompressionFormat.ZSTD,
            include_env=False,
        )
    assert not any(output_dir.glob("opencloud-*.tar.zst"))


def test_create_gzip_pipeline_success(tmp_path: Path) -> None:
    root = tmp_path / "oc"
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    output_dir = tmp_path / "backups"
    output_dir.mkdir()
    stack_paths = _stack_paths(root)

    class FakeProcess:
        def __init__(self, returncode: int = 0) -> None:
            self.returncode = returncode
            self.stdin = MagicMock()
            self.stderr = MagicMock()
            self.stderr.read.return_value = b""

        def wait(self, timeout: int | None = None) -> int:
            return self.returncode

    def popen(command_argv: list[str], **_kwargs: object) -> FakeProcess:
        return FakeProcess()

    builder = SubprocessArchiveBuilder(popen=popen)
    archive_path = builder.create_backup_archive(
        stack_paths,
        output_dir=output_dir,
        compression=CompressionFormat.GZIP,
        include_env=False,
    )
    assert archive_path.suffix == ".gz"


def test_create_gzip_tar_failure_removes_partial(tmp_path: Path) -> None:
    root = tmp_path / "oc"
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    output_dir = tmp_path / "backups"
    output_dir.mkdir()
    stack_paths = _stack_paths(root)

    class FakeProcess:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode
            self.stdin = MagicMock()
            self.stderr = MagicMock()
            self.stderr.read.return_value = b"tar boom"

        def wait(self, timeout: int | None = None) -> int:
            return self.returncode

    def popen(command_argv: list[str], **_kwargs: object) -> FakeProcess:
        if command_argv[0] == "gzip":
            archive_path = output_dir / "opencloud-2026-06-14_101530.tar.gz"
            archive_path.write_bytes(b"partial")
            return FakeProcess(0)
        return FakeProcess(1)

    builder = SubprocessArchiveBuilder(popen=popen)
    with pytest.raises(ArchiveCommandError) as error_info:
        builder.create_backup_archive(
            stack_paths,
            output_dir=output_dir,
            compression=CompressionFormat.GZIP,
            include_env=False,
            archive_timestamp=datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc),
        )
    assert error_info.value.return_code == 1
    assert not any(output_dir.glob("opencloud-*.tar.gz"))


def test_create_uncompressed_timeout_raises_minus_one(tmp_path: Path) -> None:
    root = tmp_path / "oc"
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    output_dir = tmp_path / "backups"
    output_dir.mkdir()
    stack_paths = _stack_paths(root)

    def run_command(_command_argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="tar", timeout=30)

    builder = SubprocessArchiveBuilder(run_command=run_command)
    with pytest.raises(ArchiveCommandError) as error_info:
        builder.create_backup_archive(
            stack_paths,
            output_dir=output_dir,
            compression=CompressionFormat.NONE,
            include_env=False,
            pack_timeout_seconds=30,
        )
    assert error_info.value.return_code == -1
    assert "timed out" in error_info.value.stderr
