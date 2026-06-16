from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from opencloud_backup.config import StackPaths
from opencloud_backup.domain.errors import ComposeCommandError

COMPOSE_DOWN_COMMAND_LABEL = "docker compose down"
COMPOSE_UP_COMMAND_LABEL = "docker compose up -d"
COMPOSE_PS_COMMAND_LABEL = "docker ps (compose project)"

DOCKER_PS_TIMEOUT_SECONDS = 10


class ComposeRunner(Protocol):
    def down(self, stack_paths: StackPaths, timeout_seconds: int) -> None: ...
    def up(self, stack_paths: StackPaths, timeout_seconds: int) -> None: ...
    def ps(self, stack_paths: StackPaths) -> str: ...


def _compose_project_name(stack_paths: StackPaths) -> str:
    # `docker compose` uses the directory name as the project name by default (unless overridden with --project-name).
    return stack_paths.compose_project_directory.name


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


def build_compose_up_argv(stack_paths: StackPaths) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-directory",
        str(stack_paths.compose_project_directory),
        "-f",
        str(stack_paths.compose_file),
        "up",
        "-d",
    ]


def build_compose_ps_argv(stack_paths: StackPaths) -> list[str]:
    # Filter by compose project label so `ps` output is focused on this stack.
    return [
        "docker",
        "ps",
        "--filter",
        f"label=com.docker.compose.project={_compose_project_name(stack_paths)}",
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

    def up(self, stack_paths: StackPaths, timeout_seconds: int) -> None:
        command_argv = build_compose_up_argv(stack_paths)
        try:
            completed_process = self.run_command(
                command_argv,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as timeout_error:
            raise ComposeCommandError(
                COMPOSE_UP_COMMAND_LABEL,
                -1,
                f"command timed out after {timeout_seconds}s",
            ) from timeout_error
        except OSError as operating_system_error:
            raise ComposeCommandError(
                COMPOSE_UP_COMMAND_LABEL,
                -1,
                str(operating_system_error),
            ) from operating_system_error
        if completed_process.returncode != 0:
            raise ComposeCommandError(
                COMPOSE_UP_COMMAND_LABEL,
                completed_process.returncode,
                completed_process.stderr.strip(),
            )

    def ps(self, stack_paths: StackPaths) -> str:
        command_argv = build_compose_ps_argv(stack_paths)
        try:
            completed_process = self.run_command(
                command_argv,
                capture_output=True,
                text=True,
                timeout=DOCKER_PS_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as timeout_error:
            raise ComposeCommandError(
                COMPOSE_PS_COMMAND_LABEL,
                -1,
                f"command timed out after {DOCKER_PS_TIMEOUT_SECONDS}s",
            ) from timeout_error
        except OSError as operating_system_error:
            raise ComposeCommandError(
                COMPOSE_PS_COMMAND_LABEL,
                -1,
                str(operating_system_error),
            ) from operating_system_error
        if completed_process.returncode != 0:
            raise ComposeCommandError(
                COMPOSE_PS_COMMAND_LABEL,
                completed_process.returncode,
                completed_process.stderr.strip(),
            )
        return completed_process.stdout
