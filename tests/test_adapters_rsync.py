from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from opencloud_backup.adapters.rsync import (
    RSYNC_COMMAND_LABEL,
    SubprocessTreeSyncer,
    build_rsync_argv,
)
from opencloud_backup.domain.errors import RsyncCommandError


def test_build_rsync_argv_directory_trailing_slashes() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        source = Path(temporary_directory) / "config"
        destination = Path(temporary_directory) / "snap" / "config"
        source.mkdir()
        destination.mkdir(parents=True)
        argv = build_rsync_argv(source, destination)
        assert argv[0] == "rsync"
        assert argv[1] == "-aHAX"
        assert argv[2] == f"{source.resolve()}/"
        assert argv[3] == f"{destination.resolve()}/"


def test_build_rsync_argv_delete_true() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        source = Path(temporary_directory) / "config"
        destination = Path(temporary_directory) / "snap" / "config"
        source.mkdir()
        destination.mkdir(parents=True)
        argv = build_rsync_argv(source, destination, delete=True)
        assert argv[1:4] == ["-aHAX", "--delete", f"{source.resolve()}/"]


def test_build_rsync_argv_delete_false_no_flag() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        source = Path(temporary_directory) / "config"
        destination = Path(temporary_directory) / "snap" / "config"
        source.mkdir()
        destination.mkdir(parents=True)
        argv = build_rsync_argv(source, destination, delete=False)
        assert "--delete" not in argv


def test_build_rsync_argv_file_no_trailing_slashes() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        source = Path(temporary_directory) / ".env"
        destination = Path(temporary_directory) / "snap" / ".env"
        source.write_text("KEY=value\n", encoding="utf-8")
        destination.parent.mkdir(parents=True)
        argv = build_rsync_argv(source, destination)
        assert argv[2] == str(source.resolve())
        assert argv[3] == str(destination.resolve())
        assert not argv[2].endswith("/")
        assert not argv[3].endswith("/")


def test_subprocess_tree_syncer_success() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        source = Path(temporary_directory) / "config"
        destination = Path(temporary_directory) / "snap" / "config"
        source.mkdir()
        destination.mkdir(parents=True)
        mock_run = MagicMock(return_value=MagicMock(returncode=0, stderr=""))
        syncer = SubprocessTreeSyncer(run_command=mock_run)
        syncer.sync_tree(source, destination, delete=True)
        mock_run.assert_called_once()
        assert mock_run.call_args.args[0] == build_rsync_argv(source, destination, delete=True)


def test_subprocess_tree_syncer_nonzero_exit() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        source = Path(temporary_directory) / "config"
        destination = Path(temporary_directory) / "snap" / "config"
        source.mkdir()
        destination.mkdir(parents=True)
        long_stderr = "x" * 600
        mock_run = MagicMock(return_value=MagicMock(returncode=23, stderr=long_stderr))
        syncer = SubprocessTreeSyncer(run_command=mock_run)
        with pytest.raises(RsyncCommandError) as error_info:
            syncer.sync_tree(source, destination, command_label="rsync snapshot config")
        assert error_info.value.command_label == "rsync snapshot config"
        assert error_info.value.return_code == 23
        assert len(error_info.value.stderr) == 500


def test_subprocess_tree_syncer_timeout() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        source = Path(temporary_directory) / "config"
        destination = Path(temporary_directory) / "snap" / "config"
        source.mkdir()
        destination.mkdir(parents=True)

        def raise_timeout(*_args: object, **_kwargs: object) -> None:
            raise subprocess.TimeoutExpired(cmd="rsync", timeout=30)

        syncer = SubprocessTreeSyncer(run_command=raise_timeout)
        with pytest.raises(RsyncCommandError) as error_info:
            syncer.sync_tree(source, destination, timeout_seconds=30)
        assert error_info.value.return_code == -1
        assert "timed out" in error_info.value.stderr
