from datetime import datetime, timezone
from pathlib import Path


def make_valid_stack_tree(opencloud_root: Path) -> None:
    (opencloud_root / "config").mkdir(parents=True)
    (opencloud_root / "data").mkdir(parents=True)
    (opencloud_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")


def make_backup_output_dir(opencloud_root: Path) -> Path:
    output_dir = opencloud_root / "backups"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def write_env_file(opencloud_root: Path, content: str = "SECRET=1\n") -> Path:
    env_file_path = opencloud_root / ".env"
    env_file_path.write_text(content, encoding="utf-8")
    return env_file_path


def make_backup_archive_name(timestamp: datetime, *, suffix: str = ".tar.zst") -> str:
    stamp = timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    return f"opencloud-{stamp}{suffix}"
