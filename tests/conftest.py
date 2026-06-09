from pathlib import Path


def make_valid_stack_tree(root: Path) -> None:
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir(parents=True)
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
