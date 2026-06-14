from __future__ import annotations

from opencloud_backup.domain.errors import (
    COMMAND_STDERR_MAX_LENGTH,
    ArchiveCommandError,
    ComposeCommandError,
)


def test_archive_command_error_truncates_long_stderr() -> None:
    long_stderr = "x" * (COMMAND_STDERR_MAX_LENGTH + 100)
    error = ArchiveCommandError("tar", 1, long_stderr)
    assert len(error.stderr) == COMMAND_STDERR_MAX_LENGTH
    assert error.stderr == "x" * COMMAND_STDERR_MAX_LENGTH


def test_compose_command_error_truncates_long_stderr() -> None:
    long_stderr = "y" * (COMMAND_STDERR_MAX_LENGTH + 50)
    error = ComposeCommandError("docker compose down", 1, long_stderr)
    assert len(error.stderr) == COMMAND_STDERR_MAX_LENGTH


def test_archive_command_error_keeps_short_stderr() -> None:
    short_stderr = "tar failed"
    error = ArchiveCommandError("tar", 1, short_stderr)
    assert error.stderr == short_stderr
