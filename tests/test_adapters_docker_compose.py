from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from opencloud_backup.adapters.docker_compose import (
    COMPOSE_DOWN_COMMAND_LABEL,
    COMPOSE_PS_COMMAND_LABEL,
    COMPOSE_UP_COMMAND_LABEL,
    DOCKER_PS_TIMEOUT_SECONDS,
    SubprocessComposeRunner,
    build_compose_down_argv,
    build_compose_ps_argv,
    build_compose_up_argv,
)
from opencloud_backup.config import StackPaths
from opencloud_backup.domain.errors import ComposeCommandError


def _stack_paths(root: Path) -> StackPaths:
    return StackPaths(
        opencloud_root=root,
        config_dir=root / "config",
        data_dir=root / "data",
        compose_dir=root,
        compose_file=root / "docker-compose.yml",
    )


def test_build_compose_down_argv() -> None:
    root = Path("/data/opencloud")
    stack_paths = _stack_paths(root)
    assert build_compose_down_argv(stack_paths) == [
        "docker",
        "compose",
        "--project-directory",
        str(root),
        "-f",
        str(root / "docker-compose.yml"),
        "down",
    ]


def test_build_compose_up_argv() -> None:
    root = Path("/data/opencloud")
    stack_paths = _stack_paths(root)
    assert build_compose_up_argv(stack_paths) == [
        "docker",
        "compose",
        "--project-directory",
        str(root),
        "-f",
        str(root / "docker-compose.yml"),
        "up",
        "-d",
    ]


def test_build_compose_ps_argv() -> None:
    root = Path("/data/opencloud")
    stack_paths = _stack_paths(root)
    assert build_compose_ps_argv(stack_paths) == [
        "docker",
        "ps",
        "--filter",
        "label=com.docker.compose.project=opencloud",
    ]


def test_down_success() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))

    def run_command(command_argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command_argv == build_compose_down_argv(stack_paths)
        return subprocess.CompletedProcess(args=command_argv, returncode=0, stdout="", stderr="")

    runner = SubprocessComposeRunner(run_command=run_command)
    runner.down(stack_paths, timeout_seconds=180)


def test_down_nonzero_exit_raises() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))

    def run_command(command_argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=command_argv,
            returncode=1,
            stdout="",
            stderr="permission denied",
        )

    runner = SubprocessComposeRunner(run_command=run_command)
    with pytest.raises(ComposeCommandError) as error_info:
        runner.down(stack_paths, timeout_seconds=180)
    assert error_info.value.command_label == COMPOSE_DOWN_COMMAND_LABEL
    assert error_info.value.return_code == 1
    assert error_info.value.stderr == "permission denied"


def test_down_timeout_raises() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))

    def run_command(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="docker compose down", timeout=180)

    runner = SubprocessComposeRunner(run_command=run_command)
    with pytest.raises(ComposeCommandError) as error_info:
        runner.down(stack_paths, timeout_seconds=180)
    assert error_info.value.return_code == -1
    assert "timed out" in error_info.value.stderr


def test_down_os_error_raises() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))

    def run_command(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("docker not found")

    runner = SubprocessComposeRunner(run_command=run_command)
    with pytest.raises(ComposeCommandError) as error_info:
        runner.down(stack_paths, timeout_seconds=180)
    assert "docker not found" in error_info.value.stderr


def test_down_passes_timeout_to_subprocess() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    captured_timeout: dict[str, int] = {}

    def run_command(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_timeout["value"] = int(kwargs["timeout"])
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    runner = SubprocessComposeRunner(run_command=run_command)
    runner.down(stack_paths, timeout_seconds=42)
    assert captured_timeout["value"] == 42


def test_up_success() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))

    def run_command(command_argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command_argv == build_compose_up_argv(stack_paths)
        return subprocess.CompletedProcess(args=command_argv, returncode=0, stdout="", stderr="")

    runner = SubprocessComposeRunner(run_command=run_command)
    runner.up(stack_paths, timeout_seconds=180)


def test_up_nonzero_exit_raises() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))

    def run_command(command_argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=command_argv,
            returncode=1,
            stdout="",
            stderr="permission denied",
        )

    runner = SubprocessComposeRunner(run_command=run_command)
    with pytest.raises(ComposeCommandError) as error_info:
        runner.up(stack_paths, timeout_seconds=180)
    assert error_info.value.command_label == COMPOSE_UP_COMMAND_LABEL
    assert error_info.value.return_code == 1
    assert error_info.value.stderr == "permission denied"


def test_up_timeout_raises() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))

    def run_command(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="docker compose up -d", timeout=180)

    runner = SubprocessComposeRunner(run_command=run_command)
    with pytest.raises(ComposeCommandError) as error_info:
        runner.up(stack_paths, timeout_seconds=180)
    assert error_info.value.return_code == -1
    assert "timed out" in error_info.value.stderr


def test_up_os_error_raises() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))

    def run_command(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("docker not found")

    runner = SubprocessComposeRunner(run_command=run_command)
    with pytest.raises(ComposeCommandError) as error_info:
        runner.up(stack_paths, timeout_seconds=180)
    assert "docker not found" in error_info.value.stderr


def test_up_passes_timeout_to_subprocess() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    captured_timeout: dict[str, int] = {}

    def run_command(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_timeout["value"] = int(kwargs["timeout"])
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    runner = SubprocessComposeRunner(run_command=run_command)
    runner.up(stack_paths, timeout_seconds=42)
    assert captured_timeout["value"] == 42


def test_ps_success() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))

    def run_command(command_argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command_argv == build_compose_ps_argv(stack_paths)
        return subprocess.CompletedProcess(args=command_argv, returncode=0, stdout="ps-ok", stderr="")

    runner = SubprocessComposeRunner(run_command=run_command)
    assert runner.ps(stack_paths) == "ps-ok"


def test_ps_nonzero_exit_raises() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))

    def run_command(command_argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=command_argv,
            returncode=1,
            stdout="",
            stderr="permission denied",
        )

    runner = SubprocessComposeRunner(run_command=run_command)
    with pytest.raises(ComposeCommandError) as error_info:
        runner.ps(stack_paths)
    assert error_info.value.command_label == COMPOSE_PS_COMMAND_LABEL
    assert error_info.value.return_code == 1
    assert error_info.value.stderr == "permission denied"


def test_ps_timeout_raises() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))

    def run_command(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="docker ps", timeout=DOCKER_PS_TIMEOUT_SECONDS)

    runner = SubprocessComposeRunner(run_command=run_command)
    with pytest.raises(ComposeCommandError) as error_info:
        runner.ps(stack_paths)
    assert error_info.value.return_code == -1
    assert "timed out" in error_info.value.stderr


def test_ps_os_error_raises() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))

    def run_command(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("docker not found")

    runner = SubprocessComposeRunner(run_command=run_command)
    with pytest.raises(ComposeCommandError) as error_info:
        runner.ps(stack_paths)
    assert "docker not found" in error_info.value.stderr


def test_ps_uses_timeout_constant() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    captured_timeout: dict[str, int] = {}

    def run_command(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_timeout["value"] = int(kwargs["timeout"])
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    runner = SubprocessComposeRunner(run_command=run_command)
    runner.ps(stack_paths)
    assert captured_timeout["value"] == DOCKER_PS_TIMEOUT_SECONDS
