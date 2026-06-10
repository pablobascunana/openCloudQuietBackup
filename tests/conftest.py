from pathlib import Path


def make_valid_stack_tree(opencloud_root: Path) -> None:
    (opencloud_root / "config").mkdir(parents=True)
    (opencloud_root / "data").mkdir(parents=True)
    (opencloud_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
