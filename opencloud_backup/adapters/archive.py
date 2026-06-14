from __future__ import annotations

import subprocess
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from opencloud_backup.config import StackPaths
from opencloud_backup.domain.archive import (
    TAR_TRANSFORM,
    CompressionFormat,
    archive_output_path,
    resolve_tar_members,
)
from opencloud_backup.domain.errors import ArchiveCommandError

ARCHIVE_CREATE_COMMAND_LABEL = "backup archive create"

PopenFactory = Callable[..., subprocess.Popen[bytes]]
RunCommand = Callable[..., subprocess.CompletedProcess[str]]


class ArchiveBuilder(Protocol):
    def create_backup_archive(
        self,
        stack_paths: StackPaths,
        *,
        output_dir: Path,
        compression: CompressionFormat,
        include_env: bool,
        pack_timeout_seconds: int | None = None,
        archive_timestamp: datetime | None = None,
    ) -> Path: ...


def build_tar_create_argv(
    stack_paths: StackPaths,
    *,
    include_env: bool,
    output_file: str | None = None,
) -> list[str]:
    tar_output_target = output_file if output_file is not None else "-"
    return [
        "tar",
        "-C",
        str(stack_paths.opencloud_root),
        "--xattrs",
        "--acls",
        "--numeric-owner",
        "--transform",
        TAR_TRANSFORM,
        "-cf",
        tar_output_target,
        *resolve_tar_members(stack_paths.opencloud_root, include_env=include_env),
    ]


def build_zstd_argv(archive_path: Path) -> list[str]:
    return ["zstd", "-T0", "-o", str(archive_path)]


def build_gzip_argv() -> list[str]:
    return ["gzip", "-c"]


def _remove_partial_archive(archive_path: Path) -> None:
    with suppress(OSError):
        archive_path.unlink(missing_ok=True)


def _raise_archive_error(
    command_label: str,
    return_code: int,
    stderr: str,
    archive_path: Path,
) -> None:
    _remove_partial_archive(archive_path)
    raise ArchiveCommandError(command_label, return_code, stderr)


@dataclass
class SubprocessArchiveBuilder:
    popen: PopenFactory = subprocess.Popen
    run_command: RunCommand = subprocess.run

    def create_backup_archive(
        self,
        stack_paths: StackPaths,
        *,
        output_dir: Path,
        compression: CompressionFormat,
        include_env: bool,
        pack_timeout_seconds: int | None = None,
        archive_timestamp: datetime | None = None,
    ) -> Path:
        timestamp = archive_timestamp or datetime.now(timezone.utc)
        archive_path = archive_output_path(output_dir, timestamp=timestamp, compression=compression)
        if compression == CompressionFormat.NONE:
            self._create_uncompressed(
                stack_paths,
                include_env=include_env,
                archive_path=archive_path,
                pack_timeout_seconds=pack_timeout_seconds,
            )
        elif compression == CompressionFormat.ZSTD:
            self._create_with_compressor(
                stack_paths,
                include_env=include_env,
                archive_path=archive_path,
                compressor_argv=build_zstd_argv(archive_path),
                pack_timeout_seconds=pack_timeout_seconds,
            )
        else:
            self._create_with_gzip(
                stack_paths,
                include_env=include_env,
                archive_path=archive_path,
                pack_timeout_seconds=pack_timeout_seconds,
            )
        return archive_path.resolve()

    def _create_uncompressed(
        self,
        stack_paths: StackPaths,
        *,
        include_env: bool,
        archive_path: Path,
        pack_timeout_seconds: int | None,
    ) -> None:
        tar_argv = build_tar_create_argv(
            stack_paths,
            include_env=include_env,
            output_file=str(archive_path),
        )
        try:
            completed_process = self.run_command(
                tar_argv,
                capture_output=True,
                text=True,
                timeout=pack_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            _raise_archive_error(
                ARCHIVE_CREATE_COMMAND_LABEL,
                -1,
                f"command timed out after {pack_timeout_seconds}s",
                archive_path,
            )
        except OSError as operating_system_error:
            _raise_archive_error(
                ARCHIVE_CREATE_COMMAND_LABEL,
                -1,
                str(operating_system_error),
                archive_path,
            )
        if completed_process.returncode != 0:
            _raise_archive_error(
                ARCHIVE_CREATE_COMMAND_LABEL,
                completed_process.returncode,
                completed_process.stderr.strip(),
                archive_path,
            )

    def _create_with_compressor(
        self,
        stack_paths: StackPaths,
        *,
        include_env: bool,
        archive_path: Path,
        compressor_argv: list[str],
        pack_timeout_seconds: int | None,
    ) -> None:
        tar_argv = build_tar_create_argv(stack_paths, include_env=include_env)
        try:
            compressor_process = self.popen(
                compressor_argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            tar_process = self.popen(
                tar_argv,
                stdout=compressor_process.stdin,
                stderr=subprocess.PIPE,
            )
            if compressor_process.stdin is not None:
                compressor_process.stdin.close()
            _tar_stderr = tar_process.stderr.read() if tar_process.stderr is not None else b""
            _compressor_stderr = (
                compressor_process.stderr.read() if compressor_process.stderr is not None else b""
            )
            tar_return_code = tar_process.wait(timeout=pack_timeout_seconds)
            compressor_return_code = compressor_process.wait(timeout=pack_timeout_seconds)
        except subprocess.TimeoutExpired:
            _raise_archive_error(
                ARCHIVE_CREATE_COMMAND_LABEL,
                -1,
                f"command timed out after {pack_timeout_seconds}s",
                archive_path,
            )
        except OSError as operating_system_error:
            _raise_archive_error(
                ARCHIVE_CREATE_COMMAND_LABEL,
                -1,
                str(operating_system_error),
                archive_path,
            )

        if tar_return_code != 0:
            _raise_archive_error(
                ARCHIVE_CREATE_COMMAND_LABEL,
                tar_return_code,
                _tar_stderr.decode(errors="replace").strip(),
                archive_path,
            )
        if compressor_return_code != 0:
            _raise_archive_error(
                ARCHIVE_CREATE_COMMAND_LABEL,
                compressor_return_code,
                _compressor_stderr.decode(errors="replace").strip(),
                archive_path,
            )

    def _create_with_gzip(
        self,
        stack_paths: StackPaths,
        *,
        include_env: bool,
        archive_path: Path,
        pack_timeout_seconds: int | None,
    ) -> None:
        tar_argv = build_tar_create_argv(stack_paths, include_env=include_env)
        try:
            with archive_path.open("wb") as archive_file:
                gzip_process = self.popen(
                    build_gzip_argv(),
                    stdin=subprocess.PIPE,
                    stdout=archive_file,
                    stderr=subprocess.PIPE,
                )
                tar_process = self.popen(
                    tar_argv,
                    stdout=gzip_process.stdin,
                    stderr=subprocess.PIPE,
                )
                if gzip_process.stdin is not None:
                    gzip_process.stdin.close()
                tar_stderr = tar_process.stderr.read() if tar_process.stderr is not None else b""
                gzip_stderr = gzip_process.stderr.read() if gzip_process.stderr is not None else b""
                tar_return_code = tar_process.wait(timeout=pack_timeout_seconds)
                gzip_return_code = gzip_process.wait(timeout=pack_timeout_seconds)
        except subprocess.TimeoutExpired:
            _raise_archive_error(
                ARCHIVE_CREATE_COMMAND_LABEL,
                -1,
                f"command timed out after {pack_timeout_seconds}s",
                archive_path,
            )
        except OSError as operating_system_error:
            _raise_archive_error(
                ARCHIVE_CREATE_COMMAND_LABEL,
                -1,
                str(operating_system_error),
                archive_path,
            )

        if tar_return_code != 0:
            _raise_archive_error(
                ARCHIVE_CREATE_COMMAND_LABEL,
                tar_return_code,
                tar_stderr.decode(errors="replace").strip(),
                archive_path,
            )
        if gzip_return_code != 0:
            _raise_archive_error(
                ARCHIVE_CREATE_COMMAND_LABEL,
                gzip_return_code,
                gzip_stderr.decode(errors="replace").strip(),
                archive_path,
            )
