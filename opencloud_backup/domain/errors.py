from __future__ import annotations

from pathlib import Path

from opencloud_backup.domain.prereqs import PrerequisiteReport

COMMAND_STDERR_MAX_LENGTH = 500


def _truncate_command_stderr(stderr: str, *, max_length: int = COMMAND_STDERR_MAX_LENGTH) -> str:
    if len(stderr) <= max_length:
        return stderr
    return stderr[:max_length]


class JobError(Exception):
    pass


class PrerequisiteCheckError(JobError):
    def __init__(self, report: PrerequisiteReport) -> None:
        self.report = report
        super().__init__("Prerequisite checks failed")


class ComposeCommandError(JobError):
    def __init__(self, command_label: str, return_code: int, stderr: str) -> None:
        self.command_label = command_label
        self.return_code = return_code
        self.stderr = _truncate_command_stderr(stderr)
        super().__init__(f"{command_label} failed with exit code {return_code}")


class ArchiveCommandError(JobError):
    def __init__(self, command_label: str, return_code: int, stderr: str) -> None:
        self.command_label = command_label
        self.return_code = return_code
        self.stderr = _truncate_command_stderr(stderr)
        super().__init__(f"{command_label} failed with exit code {return_code}")


class RsyncCommandError(JobError):
    def __init__(self, command_label: str, return_code: int, stderr: str) -> None:
        self.command_label = command_label
        self.return_code = return_code
        self.stderr = _truncate_command_stderr(stderr)
        super().__init__(f"{command_label} failed with exit code {return_code}")


class IntegrityError(JobError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class SidecarNotFoundError(IntegrityError):
    def __init__(self, sidecar_path: Path) -> None:
        self.sidecar_path = sidecar_path
        super().__init__(f"Sidecar not found: {sidecar_path}")


class HashMismatchError(IntegrityError):
    def __init__(
        self,
        *,
        archive_path: Path,
        expected_hex: str,
        actual_hex: str,
    ) -> None:
        self.archive_path = archive_path
        self.expected_hex = expected_hex
        self.actual_hex = actual_hex
        super().__init__("Archive SHA-256 does not match sidecar")


class RetentionError(JobError):
    def __init__(self, path: Path, *, cause: OSError | None = None) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"Failed to delete: {path}")
