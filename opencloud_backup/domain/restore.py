from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RestoreJobResult:
    snapshot_path: Path
    archive_path: Path
    staging_path: Path | None
