from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from opencloud_backup.domain.errors import RsyncCommandError

RSYNC_COMMAND_LABEL = "rsync snapshot"

RunCommand = Callable[..., subprocess.CompletedProcess[str]]


class TreeSyncer(Protocol):
    def sync_tree(
        self,
        source: Path,
        destination: Path,
        *,
        timeout_seconds: int | None = None,
        command_label: str = RSYNC_COMMAND_LABEL,
    ) -> None: ...


def build_rsync_argv(source: Path, destination: Path) -> list[str]:
    resolved_source = source.resolve()
    resolved_destination = destination.resolve()
    if resolved_source.is_file():
        source_arg = str(resolved_source)
        dest_arg = str(resolved_destination)
    else:
        source_arg = f"{resolved_source}/"
        dest_arg = f"{resolved_destination}/"
    return ["rsync", "-aHAX", source_arg, dest_arg]


@dataclass
class SubprocessTreeSyncer:
    run_command: RunCommand = subprocess.run

    def sync_tree(
        self,
        source: Path,
        destination: Path,
        *,
        timeout_seconds: int | None = None,
        command_label: str = RSYNC_COMMAND_LABEL,
    ) -> None:
        argv = build_rsync_argv(source, destination)
        try:
            completed_process = self.run_command(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            raise RsyncCommandError(
                command_label,
                -1,
                f"command timed out after {timeout_seconds}s",
            ) from None
        except OSError as operating_system_error:
            raise RsyncCommandError(command_label, -1, str(operating_system_error)) from None
        if completed_process.returncode != 0:
            raise RsyncCommandError(
                command_label,
                completed_process.returncode,
                completed_process.stderr.strip(),
            )
