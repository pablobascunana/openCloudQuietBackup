from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from opencloud_backup.config import StackPaths
from opencloud_backup.domain.errors import ComposeCommandError

COMPOSE_DOWN_COMMAND_LABEL = "docker compose down"


class ComposeRunner(Protocol):
    def down(self, stack_paths: StackPaths, timeout_seconds: int) -> None: ...


def build_compose_down_argv(stack_paths: StackPaths) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-directory",
        str(stack_paths.compose_project_directory),
        "-f",
        str(stack_paths.compose_file),
        "down",
    ]


@dataclass
class SubprocessComposeRunner:
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run

    def down(self, stack_paths: StackPaths, timeout_seconds: int) -> None:
        command_argv = build_compose_down_argv(stack_paths)
        try:
            completed_process = self.run_command(
                command_argv,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as timeout_error:
            raise ComposeCommandError(
                COMPOSE_DOWN_COMMAND_LABEL,
                -1,
                f"command timed out after {timeout_seconds}s",
            ) from timeout_error
        except OSError as operating_system_error:
            raise ComposeCommandError(
                COMPOSE_DOWN_COMMAND_LABEL,
                -1,
                str(operating_system_error),
            ) from operating_system_error
        if completed_process.returncode != 0:
            raise ComposeCommandError(
                COMPOSE_DOWN_COMMAND_LABEL,
                completed_process.returncode,
                completed_process.stderr.strip(),
            )
