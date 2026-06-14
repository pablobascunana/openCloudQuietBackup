from __future__ import annotations

from opencloud_backup.domain.prereqs import PrerequisiteReport


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
        self.stderr = stderr
        super().__init__(f"{command_label} failed with exit code {return_code}")
