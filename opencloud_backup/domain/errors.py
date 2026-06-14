from __future__ import annotations

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
