# OpenCloud Quiet Backup

Specialised tool for **consistent backups** of [OpenCloud](https://opencloud.eu/) deployed with **Docker Compose** on a Linux NAS. Unlike generic solutions (Kopia, Duplicati), this product orchestrates the full workflow already proven reliable in production: **stop the stack → copy with metadata → start again**.

The functional backlog is in [USER_STORIES.md](./USER_STORIES.md).

---

## Product goals

### Problem we solve

Hot folder backups often produce **internally inconsistent** copies (databases, indexes, locks). Generic tools also do not guarantee preservation of **xattrs, ACLs, and numeric owners**, which are critical in Docker deployments with `PUID`/`PGID`.

### Main goal

Provide an **opinionated application for OpenCloud** that:

1. **Guarantees consistent copies** by stopping the stack before reading `config/` and `data/`.
2. **Preserves filesystem metadata** (equivalent to `tar --xattrs --acls --numeric-owner` and `rsync -aHAX`).
3. **Unifies backup and restore** in predictable flows, with clear logs and checks.
4. **Optionally** uploads the resulting archive to remote destinations (post-MVP phase), without replacing correct local logic.

### Design principles

| Principle | Description |
|-----------|-------------|
| Orchestration first | The unit of work is a *job* (`down` → pack → `up`), not “copy folders”. |
| Canonical format | A single archive schema (e.g. `opencloud-YYYY-MM-DD_HHMMSS.tar.zst` with `opencloud/config`, `opencloud/data`, `opencloud/.env`). |
| Fixed metadata | Same pack and restore flags across compatible versions. |
| Auditability | Structured log per job: duration, size, hash, errors, time with the service stopped. |

### MVP scope

- CLI on the host (or container with `docker.sock` and mounted paths).
- Local backup and restore with day-based retention.
- Path and prerequisite validation before each job.
- No web UI or remote destinations in the first release (see US-050+ stories).

### Current status

| Story | Status |
|-------|--------|
| US-001 — Stack paths | Implemented |
| US-002 onwards | Pending |

---

## Technology stack

### Implementation (application)

| Layer | Technology | Notes |
|-------|------------|-------|
| Language | **Python ≥ 3.10** | Typing, `dataclasses`, `pathlib`; no runtime dependencies in the current core. |
| Environment | **[uv](https://docs.astral.sh/uv/)** | Virtual environment, lockfile, and dev dependencies. |
| Packaging | **Hatchling** | `pyproject.toml`, editable install with `uv sync`. |
| CLI | **`argparse`** (stdlib) | Entry point: `opencloud-quiet-backup`; module: `python -m opencloud_backup`. |
| Tests | **pytest** | Run with: `uv run pytest`. |
| Lint and format | **Ruff** | `uv run ruff check` / `uv run ruff format`. |
| Static typing | **mypy** | `uv run mypy opencloud_backup`. |
| Git hooks | **pre-commit** | Ruff + mypy before each commit. |
| Job metadata (future) | **SQLite** | Execution history only — not OpenCloud application data. |
| Web UI (post-MVP) | TBD | Likely: minimal API + static UI; auth via reverse proxy. |

### NAS runtime (deployment target)

The application **invokes system tools**; the host running the backup must have:

| Tool | Use |
|------|-----|
| **Docker** + **Docker Compose v2** | Stop and start the OpenCloud stack. |
| **`tar`** | Packaging with xattrs/ACLs. |
| **`zstd`** (recommended) or **`gzip`** | Compression of the canonical archive. |
| **`rsync`** | Restore with `-aHAX`. |

Target OS: **Linux** (UGREEN NAS, Synology with Docker, etc.). macOS is fine for development; final validation is on the NAS environment.

### Validated flow reference

The design is based on scripts already working in the project ecosystem (`backup-opencloud-tar.sh` / `restore-opencloud-tar.sh`).

---

## Development requirements

### On your development machine

- **Python 3.10 or newer** (`python3 --version`; `uv` can install it if missing).
- **Git** (clone the repository).
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** on the PATH.

Optional but useful:

- Local **Docker**, for future integration tests (US-071).

### NAS permissions (when testing against a real OpenCloud deployment)

- User in the **`docker`** group or equivalent permissions (`docker ps` must work).
- Read/write access to the OpenCloud root (`config/`, `data/`, `.env`).

Environment variables supported today (US-001):

| Variable | Description |
|----------|-------------|
| `OCB_OPENCLOUD_ROOT` | Root containing `config/` and `data/`. |
| `OCB_COMPOSE_DIR` | Compose project directory (default: root). |
| `OCB_COMPOSE_FILE` | Explicit path to `docker-compose.yml` / `.yaml`. |

---

## Installation for development

```bash
# 1. Enter the project
cd openCloudQuietBackup

# 2. Create .venv, install editable package and dev deps
uv sync --dev

# 3. Check the CLI
uv run opencloud-quiet-backup --help
uv run opencloud-quiet-backup validate --help

# 4. Run tests
uv run pytest

# 5. Install pre-commit hooks (once per clone)
uv run pre-commit install
```

### Code quality

```bash
# Lint (opencloud_backup + tests)
uv run ruff check opencloud_backup tests

# Format (applies changes)
uv run ruff format opencloud_backup tests

# Check format without writing
uv run ruff format --check opencloud_backup tests

# Static typing for the package
uv run mypy opencloud_backup

# Run all hooks manually (without commit)
uv run pre-commit run --all-files
```

### Development without the installed entry point

```bash
cd openCloudQuietBackup
uv run python -m opencloud_backup validate \
  --opencloud-root /path/to/opencloud
```

### Validate configuration against a real deployment

```bash
uv run opencloud-quiet-backup validate \
  --opencloud-root /volume1/docker/opencloud \
  --compose-dir /volume1/docker/opencloud
```

On success, expect resolved absolute paths for `opencloud_root`, `config_dir`, `data_dir`, `compose_dir`, and `compose_file`.

---

## Repository structure

```
openCloudQuietBackup/
├── README.md                 # This document
├── USER_STORIES.md           # Backlog and user stories
├── pyproject.toml            # Metadata, pytest, Ruff, mypy, pre-commit, console
├── .pre-commit-config.yaml   # Git hooks (Ruff + mypy)
├── uv.lock                   # Dependency lockfile (commit in repo)
├── .gitignore
├── opencloud_backup/
│   ├── __init__.py
│   ├── __main__.py           # python -m opencloud_backup
│   ├── cli.py                # CLI subcommands
│   └── config.py             # US-001: paths and validation
└── tests/
    ├── conftest.py           # Shared fixtures
    ├── test_cli.py
    └── test_config.py
```

---

## Persistent memory (Engram)

This project uses [Engram](https://github.com/Gentleman-Programming/engram) to retain context across agent sessions (decisions, SDD artifacts, resolved bugs).

**Requirement:** `engram` CLI on the PATH. On macOS with Homebrew:

```bash
brew tap gentleman-programming/tap
brew install engram
```

**Storage (all local, not committed):**

| Location | Contents |
|----------|----------|
| `~/.engram/engram.db` | Database with sessions and observations |
| `.engram/config.json` | Project name (`openCloudQuietBackup`); create manually at clone root |

The `.engram/` directory is in `.gitignore`. Each developer creates it locally when using Engram or the orchestrator SDD workflow.

### Query memories

```bash
# List projects and counts
engram projects list

# Recent context for this project
engram context openCloudQuietBackup

# Search by keyword or SDD phase
engram search "notas-menores" --project openCloudQuietBackup
engram search "sdd/notas-menores" --project openCloudQuietBackup --limit 20

# View a full observation (ID from search or context; varies per machine)
engram timeline 14

# Statistics and database path
engram stats
```

### Interface and export

```bash
# Interactive terminal UI
engram tui

# Export everything to JSON (writes to current directory; do not commit the file)
engram export engram-export.json

# Store diagnostics
engram doctor --project openCloudQuietBackup
```

### SDD artifact keys

When using the orchestrator SDD workflow, memories are usually saved with these topic keys:

| Key | Contents |
|-----|----------|
| `sdd-init/openCloudQuietBackup` | Initial project context |
| `sdd/{change}/explore` | Change exploration |
| `sdd/{change}/proposal` | Proposal |
| `sdd/{change}/spec` | Specification |
| `sdd/{change}/design` | Technical design |
| `sdd/{change}/tasks` | Implementation tasks |
| `sdd/{change}/apply-progress` | Apply progress |
| `sdd/{change}/verify-report` | Verification result |
| `sdd/{change}/archive-report` | Change closure |

Replace `{change}` with the change name (e.g. `notas-menores`).

`engram projects list` shows the name in lowercase (`opencloudquietbackup`); `--project` filters accept both forms.

From Cursor, the **Engram** MCP server exposes read and write (`mem_search`, `mem_context`, `mem_get_observation`, `mem_save`, etc.).

---

## Next development steps

1. **US-002** — Prerequisite checks (`docker`, `tar`, free disk space, etc.).
2. **US-010–US-012** — Backup flow (stop, canonical tar, start).
3. **US-020–US-023** — Restore flow with prior snapshot and confirmation.

See the [MVP summary](./USER_STORIES.md#resumen-mvp-sugerido) in `USER_STORIES.md` for the full list.

---

## License

TBD.
