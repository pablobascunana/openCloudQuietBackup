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
| US-002 — Prerequisites | Implemented |
| US-003 — Execution context | Implemented |
| US-010 — Stop stack before backup | Implemented |
| US-011 — Canonical tar archive | Implemented (prereqs + down + pack; no `up` yet — US-012) |
| US-012 onwards | Pending |
| US-013 — Archive integrity (SHA-256 sidecar) | Implemented |

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
- Read access to `config/`, `data/`, and `.env` (if present).
- Write access to `config/` and `data/` when running restore jobs (`prereqs --mode restore` or `all`).

#### Root vs `docker` group (US-003)

| Execution context | Docker daemon (`docker ps`) | Stack paths | Typical use |
|-------------------|----------------------------|-------------|-------------|
| **root** | Works if the socket is reachable | Full access if files are owned by root or world-readable/writable | Quick tests; not recommended for cron |
| **User in `docker` group** | Works after re-login once added to the group | Needs read on backup; read+write on restore | **Recommended** for scheduled backups on a NAS |
| **Regular user (no `docker`)** | `prereqs` fails with `docker ps` | May read stack paths but cannot stop/start the stack | Not supported |

Add a dedicated user to the `docker` group on Linux:

```bash
sudo usermod -aG docker opencloud-backup
# Log out and back in (or newgrp docker) before running prereqs
```

Verify with:

```bash
uv run opencloud-quiet-backup prereqs --opencloud-root /path/to/opencloud
```

Environment variables supported today:

| Variable | Description |
|----------|-------------|
| `OCB_OPENCLOUD_ROOT` | Root containing `config/` and `data/`. |
| `OCB_COMPOSE_DIR` | Compose project directory (default: root). |
| `OCB_COMPOSE_FILE` | Explicit path to `docker-compose.yml` / `.yaml`. |
| `OCB_MIN_FREE_BYTES` | Minimum free disk space (bytes) for `prereqs`. |
| `OCB_MIN_FREE_PERCENT` | Minimum free disk space (percent 1–100) for `prereqs`. |
| `OCB_STOP_TIMEOUT` | Timeout in seconds for `docker compose down` in `backup` and `restore` (default 180). |
| `OCB_START_TIMEOUT` | Timeout in seconds for `docker compose up -d` in `backup` and `restore` (default 180). |
| `OCB_OUTPUT_DIR` | Directory for backup archives (default: `{opencloud_root}/backups`). |
| `OCB_COMPRESSION` | Default compression for `backup` (`zstd`, `gzip`, or `none`). |
| `OCB_PACK_TIMEOUT` | Timeout in seconds for the pack phase in `backup` (default: unlimited). |
| `OCB_WRITE_HASH` | When `1`, `true`, or `yes` (case-insensitive), enable SHA-256 sidecar write on `backup` (same as `--write-hash`). |
| `OCB_VERIFY_HASH` | When `1`, `true`, or `yes`, verify SHA-256 sidecar before restore extract (same as `--verify-hash`). |
| `OCB_I_KNOW_WHAT_IM_DOING` | When `1`, `true`, or `yes`, skip interactive restore confirmation (same as `--i-know-what-im-doing`). |

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

### Check host prerequisites (US-002, US-003)

Dry-run check for Docker daemon access (`docker ps`), Compose v2, required binaries (`tar`, `zstd` or `gzip`, `rsync` for restore), stack path permissions, and optional disk space thresholds:

```bash
uv run opencloud-quiet-backup prereqs \
  --opencloud-root /volume1/docker/opencloud

# Restore mode (requires rsync)
uv run opencloud-quiet-backup prereqs \
  --opencloud-root /volume1/docker/opencloud \
  --mode restore

# Require at least 10 GiB free on the OpenCloud root volume
uv run opencloud-quiet-backup prereqs \
  --opencloud-root /volume1/docker/opencloud \
  --min-free-bytes 10737418240
```

`--min-free-bytes` and `--min-free-percent` are mutually exclusive. Default disk check path is the resolved `opencloud_root` (override with `--disk-check-path`).

`prereqs` accepts the same compose flags as `validate` (`--compose-dir`, `--compose-file`, `OCB_COMPOSE_DIR`, `OCB_COMPOSE_FILE`).

### Backup (US-010, US-011, US-012)

Runs prerequisite checks for backup mode, `docker compose down`, creates a canonical tar archive under the output directory (default `{opencloud_root}/backups` — the directory must already exist and be writable), then starts the stack again with `docker compose up -d` and dumps its status with `docker ps` (best-effort).

```bash
# Create the output directory once (not auto-created)
mkdir -p /volume1/docker/opencloud/backups

uv run opencloud-quiet-backup backup \
  --opencloud-root /volume1/docker/opencloud

# Custom output dir, compression, exclude .env, pack timeout
uv run opencloud-quiet-backup backup \
  --opencloud-root /volume1/docker/opencloud \
  --output-dir /volume1/backups/opencloud \
  --compression zstd \
  --no-env \
  --pack-timeout 3600 \
  --stop-timeout 300 \
  --start-timeout 300
```

**Canonical archive format** (format version `1` in code — `ARCHIVE_FORMAT_VERSION`):

| Item | Value |
|------|--------|
| Filename | `opencloud-YYYY-MM-DD_HHMMSS.tar.zst` (or `.tar.gz` / `.tar` for `gzip` / `none`) |
| Internal paths | `opencloud/config`, `opencloud/data`, optional `opencloud/.env` |
| Tar flags | `--xattrs --acls --numeric-owner --transform 's,^,opencloud/,'` |
| `.env` | Included by default when the file exists; use `--no-env` to exclude |

Phase timestamps are written to stderr in UTC ISO format, for example:

```text
[2026-06-14T10:15:30.123456+00:00] backup: stop phase started
[2026-06-14T10:16:45.789012+00:00] backup: stop phase finished
[2026-06-14T10:16:46.000000+00:00] backup: pack phase started
[2026-06-14T10:18:00.000000+00:00] backup: pack phase finished
[2026-06-14T10:18:05.000000+00:00] backup: up phase started
[2026-06-14T10:18:20.000000+00:00] backup: up phase finished
[2026-06-14T10:18:20.500000+00:00] backup: ps phase started
[2026-06-14T10:18:20.600000+00:00] backup: ps phase finished
```

On success, stdout reports `Backup completed successfully.` and the archive path.

If prerequisites fail, neither `down` nor pack runs. On compose or pack failure, the command exits with code 1 and logs the failed phase. Partial archive files are removed on pack failure (best-effort).

US-012 policy: if `docker compose down` succeeds, the job always attempts `docker compose up -d` (even if `pack` fails). If pack fails but `up` succeeds, the command exits with code 1 but the stack ends up online.

### Archive integrity (US-013)

Optional SHA-256 integrity via a **sidecar file** next to the archive: `{archive_filename}.sha256` (literal suffix append, e.g. `opencloud-2026-06-14_101530.tar.zst.sha256`).

**Sidecar format** (UTF-8, GNU-compatible line 1 + comment metadata):

```text
a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3  opencloud-2026-06-14_101530.tar.zst
# format_version=1
# size_bytes=1048576
# recorded_at=2026-06-14T10:15:30+00:00
# archive_format_version=1
```

Line 1 uses exactly **two spaces** between the lowercase hex digest and the archive basename. GNU `sha256sum -c` accepts the file when run from the directory containing the archive:

```bash
cd /volume1/docker/opencloud/backups
sha256sum -c opencloud-2026-06-14_101530.tar.zst.sha256
```

**Opt-in on backup** — hash runs after successful pack while the stack is still stopped (extends downtime on large archives):

```bash
uv run opencloud-quiet-backup backup \
  --opencloud-root /volume1/docker/opencloud \
  --write-hash
```

Or set `OCB_WRITE_HASH=1` (or `true` / `yes`). On success, stdout includes `sidecar: {path}` in addition to the archive path. Phase logs on stderr: `backup: hash phase started` / `finished` / `failed` (English, UTC timestamps).

If the hash phase fails, the archive file is kept, `up` is still attempted (US-012), and the command exits with code 1.

**Verify** an existing archive against its sidecar (no Docker or stack paths required):

```bash
uv run opencloud-quiet-backup verify \
  --archive /volume1/docker/opencloud/backups/opencloud-2026-06-14_101530.tar.zst

# Optional explicit sidecar path
uv run opencloud-quiet-backup verify \
  --archive /path/to/archive.tar.zst \
  --sidecar /path/to/custom.sha256
```

**Retention (US-030):** when deleting old backups, remove the paired sidecar `{archive}.sha256` together with the archive file.

### Restore (US-020–US-024)

Stops the stack with `docker compose down`, copies the current live `config/`, `data/`, and optional `.env` into a timestamped security snapshot under `{opencloud_root}/snapshots/pre-restore-YYYY-MM-DD_HHMMSS/` using `rsync -aHAX` (no `--delete`), then extracts the backup archive and applies it to the live tree with `rsync -aHAX --delete` on `config/` and `data/`. The snapshot base directory is created automatically if missing.

`--archive` is **required** and must be a `.tar.zst`, `.tar.gz`, or `.tar` file matching the canonical backup layout (`opencloud/config`, `opencloud/data`, optional `opencloud/.env`).

**Confirmation (US-024):** restore is destructive. Before stopping the stack, the CLI requires explicit confirmation:

| Mode | When | Behaviour |
|------|------|-----------|
| Interactive (TTY) | SSH session with stdin and stdout attached | Prints a summary of affected paths to stderr, then prompts you to type the **exact archive basename** (case-sensitive). |
| Non-interactive bypass | Cron, CI, pipes, or scripts | Pass `--i-know-what-im-doing` or set `OCB_I_KNOW_WHAT_IM_DOING=1` (or `true` / `yes`). |
| Non-interactive without bypass | No TTY and no flag/env | Exit code **2** with an error message. |

Cancellation, wrong basename, or EOF during the prompt returns exit code **1** without starting the job.

> **Breaking change (US-024):** `restore` no longer starts immediately in non-interactive environments. Cron jobs, CI pipelines, and scripts must pass `--i-know-what-im-doing` or set `OCB_I_KNOW_WHAT_IM_DOING` after upgrading.

```bash
# Interactive SSH (prompt for archive basename)
uv run opencloud-quiet-backup restore \
  --opencloud-root /volume1/docker/opencloud \
  --archive /volume1/backups/opencloud-2026-06-14_101530.tar.zst

# Cron / non-interactive (explicit bypass required)
uv run opencloud-quiet-backup restore \
  --opencloud-root /volume1/docker/opencloud \
  --archive /volume1/backups/opencloud-2026-06-14_101530.tar.zst \
  --i-know-what-im-doing

# Verify SHA-256 sidecar before extract, custom snapshot base, exclude .env from snapshot only
uv run opencloud-quiet-backup restore \
  --opencloud-root /volume1/docker/opencloud \
  --archive /volume1/backups/opencloud-2026-06-14_101530.tar.zst \
  --i-know-what-im-doing \
  --verify-hash \
  --snapshot-dir /volume1/backups/opencloud-snapshots \
  --keep-previous-snapshot \
  --no-env
```

**`.env` policy:** if the archive contains `opencloud/.env`, it is copied to the live `.env` with `rsync -aHAX` (no `--delete`). If the archive does **not** include `.env`, the existing live `.env` is left unchanged. The `--no-env` flag affects only the US-021 security snapshot, not whether `.env` is applied from the archive.

**Destructive apply:** `config/` and `data/` are synced with `--delete`, so files present on disk but absent in the archive are removed. Plan disk space for both the snapshot and a full extract under `{opencloud_root}/.restore-staging-YYYY-MM-DD_HHMMSS/` during the job (staging is removed on success).

**Disk space:** ensure the volume hosting snapshots and staging has at least as much free space as `config/`, `data/`, `.env`, and the uncompressed archive combined (estimate with `du -sh config data .env` and the archive size). Use `--disk-check-path` (default: parent of the snapshot base), `--min-free-bytes`, or `--min-free-percent` to enforce a threshold during prerequisite checks.

**Rollback:** if extract or apply fails, the US-021 snapshot under `pre-restore-*` remains available for manual rsync rollback. Residual staging directories (`.restore-staging-*`) may remain if cleanup fails. The stack is **not** restarted on extract/apply failure (unlike backup, which always attempts `docker compose up` in a `finally` block after stop — US-012).

On success, the job runs `docker compose up -d` (US-023) and prints `docker compose ps` output to stderr for a quick health check. Configure the start timeout with `--start-timeout` or `OCB_START_TIMEOUT` (default 180 seconds).

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
│   ├── config.py             # US-001: paths and validation
│   ├── domain/               # US-002: prerequisite types
│   └── adapters/             # US-002: host probes
└── tests/
    ├── conftest.py           # Shared fixtures
    ├── test_cli.py
    ├── test_cli_prereqs.py
    ├── test_config.py
    └── test_adapters_prerequisites.py
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

1. **US-012** — Stack start after backup (`docker compose up -d`).
2. **US-020–US-024** — Restore flow with prior snapshot and CLI confirmation.
3. **US-031** — Auto-create output directory (today: must exist).

See the [MVP summary](./USER_STORIES.md#resumen-mvp-sugerido) in `USER_STORIES.md` for the full list.

---

## License

TBD.
