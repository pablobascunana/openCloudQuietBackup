# OpenCloud Quiet Backup

Herramienta especializada para **backups coherentes** de [OpenCloud](https://opencloud.eu/) desplegado con **Docker Compose** en un NAS Linux. A diferencia de soluciones genéricas (Kopia, Duplicati), el producto orquesta el flujo completo que ya ha demostrado ser fiable en producción: **parar el stack → copiar con metadatos → arrancar de nuevo**.

El backlog funcional está en [USER_STORIES.md](./USER_STORIES.md).

---

## Objetivos del producto

### Problema que resolvemos

Los backups de carpetas en caliente suelen producir copias **internamente inconsistentes** (bases de datos, índices, locks). Además, herramientas genéricas no garantizan la preservación de **xattrs, ACLs y propietarios numéricos**, críticos en despliegues Docker con `PUID`/`PGID`.

### Objetivo principal

Ofrecer una aplicación **opinionada para OpenCloud** que:

1. **Garantice copias coherentes** parando el stack antes de leer `config/` y `data/`.
2. **Preserve metadatos del sistema de ficheros** (equivalente a `tar --xattrs --acls --numeric-owner` y `rsync -aHAX`).
3. **Unifique backup y restore** en flujos predecibles, con logs y comprobaciones claras.
4. **Opcionalmente** suba el archivo resultante a destinos remotos (fase posterior al MVP), sin sustituir la lógica local correcta.

### Principios de diseño

| Principio | Descripción |
|-----------|-------------|
| Orquestación primero | La unidad de trabajo es un *job* (`down` → empaquetado → `up`), no “copiar carpetas”. |
| Formato canónico | Un único esquema de archivo (p. ej. `opencloud-YYYY-MM-DD_HHMMSS.tar.zst` con `opencloud/config`, `opencloud/data`, `opencloud/.env`). |
| Metadatos fijos | Mismas flags de empaquetado y restauración en todas las versiones compatibles. |
| Auditoría | Log estructurado por job: duración, tamaño, hash, errores, tiempo con el servicio parado. |

### Alcance del MVP

- CLI en el host (o contenedor con `docker.sock` y rutas montadas).
- Backup y restore local con retención por días.
- Validación de rutas y prerequisitos antes de cada job.
- Sin UI web ni destinos remotos en la primera entrega (ver historias US-050+).

### Estado actual

| Historia | Estado |
|----------|--------|
| US-001 — Rutas del stack | Implementada |
| US-002 en adelante | Pendiente |

---

## Stack tecnológico

### Implementación (aplicación)

| Capa | Tecnología | Notas |
|------|------------|--------|
| Lenguaje | **Python ≥ 3.10** | Tipado, `dataclasses`, `pathlib`; sin dependencias runtime en el núcleo actual. |
| Gestión de entorno | **[uv](https://docs.astral.sh/uv/)** | Entorno virtual, lockfile y dependencias de desarrollo. |
| Empaquetado | **Hatchling** | `pyproject.toml`, instalación editable con `uv sync`. |
| CLI | **`argparse`** (stdlib) | Entrada: `opencloud-quiet-backup`; módulo: `python -m opencloud_backup`. |
| Tests | **pytest** | Ejecución: `uv run pytest`. |
| Lint y formato | **Ruff** | `uv run ruff check` / `uv run ruff format`. |
| Tipado estático | **mypy** | `uv run mypy opencloud_backup`. |
| Hooks Git | **pre-commit** | Ruff + mypy antes de cada commit. |
| Metadatos de jobs (futuro) | **SQLite** | Solo para historial de ejecuciones, no para datos de OpenCloud. |
| UI web (post-MVP) | Por definir | Probable: API mínima + UI estática; autenticación vía reverse proxy. |

### Runtime en el NAS (objetivo de despliegue)

La aplicación **invoca herramientas del sistema**; en el host donde corre el backup deben estar disponibles:

| Herramienta | Uso |
|-------------|-----|
| **Docker** + **Docker Compose v2** | Parar y levantar el stack OpenCloud. |
| **`tar`** | Empaquetado con xattrs/ACLs. |
| **`zstd`** (recomendado) o **`gzip`** | Compresión del archivo canónico. |
| **`rsync`** | Restauración con `-aHAX`. |

Sistemas operativos objetivo: **Linux** (NAS UGREEN, Synology con Docker, etc.). macOS puede usarse para desarrollo; la validación final es en el entorno NAS.

### Referencia de flujo validado

El diseño se basa en los scripts que ya funcionan en el ecosistema del proyecto (`backup-opencloud-tar.sh` / `restore-opencloud-tar.sh`).

---

## Requisitos para desarrollar

### En tu máquina de desarrollo

- **Python 3.10 o superior** (`python3 --version`; `uv` puede instalarlo si falta).
- **Git** (clonar el repositorio).
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** instalado en el PATH.

Opcional pero útil:

- **Docker** local, para pruebas de integración futuras (US-071).

### Permisos en el NAS (cuando pruebes contra OpenCloud real)

- Usuario en el grupo **`docker`** o ejecución con permisos equivalentes (`docker ps` debe funcionar).
- Lectura/escritura sobre la raíz de OpenCloud (`config/`, `data/`, `.env`).

Variables de entorno soportadas hoy (US-001):

| Variable | Descripción |
|----------|-------------|
| `OCB_OPENCLOUD_ROOT` | Raíz con `config/` y `data/`. |
| `OCB_COMPOSE_DIR` | Directorio del proyecto Compose (por defecto = raíz). |
| `OCB_COMPOSE_FILE` | Ruta explícita al `docker-compose.yml` / `.yaml`. |

---

## Instalación para empezar a desarrollar

```bash
# 1. Entrar en el proyecto
cd openCloudQuietBackup

# 2. Crear .venv, instalar el paquete en editable y deps de desarrollo
uv sync --dev

# 3. Comprobar la CLI
uv run opencloud-quiet-backup --help
uv run opencloud-quiet-backup validate --help

# 4. Ejecutar tests
uv run pytest

# 5. Instalar hooks de pre-commit (una vez por clon)
uv run pre-commit install
```

### Calidad de código

```bash
# Lint (opencloud_backup + tests)
uv run ruff check opencloud_backup tests

# Formato (aplica cambios)
uv run ruff format opencloud_backup tests

# Comprobar formato sin escribir
uv run ruff format --check opencloud_backup tests

# Tipado estático del paquete
uv run mypy opencloud_backup

# Ejecutar todos los hooks manualmente (sin commit)
uv run pre-commit run --all-files
```

### Desarrollo sin usar el entry point instalado

```bash
cd openCloudQuietBackup
uv run python -m opencloud_backup validate \
  --opencloud-root /ruta/a/opencloud
```

### Validar configuración contra un despliegue real

```bash
uv run opencloud-quiet-backup validate \
  --opencloud-root /volume1/docker/opencloud \
  --compose-dir /volume1/docker/opencloud
```

Salida esperada en éxito: rutas absolutas resueltas de `opencloud_root`, `config_dir`, `data_dir`, `compose_dir` y `compose_file`.

---

## Estructura del repositorio

```
openCloudQuietBackup/
├── README.md                 # Este documento
├── USER_STORIES.md           # Backlog e historias de usuario
├── pyproject.toml            # Metadatos, pytest, Ruff, mypy, pre-commit y consola
├── .pre-commit-config.yaml   # Hooks Git (Ruff + mypy)
├── uv.lock                   # Lockfile de dependencias (commitear en el repo)
├── .gitignore
├── opencloud_backup/
│   ├── __init__.py
│   ├── __main__.py           # python -m opencloud_backup
│   ├── cli.py                # Subcomandos CLI
│   └── config.py             # US-001: rutas y validación
└── tests/
    ├── conftest.py           # Fixtures compartidas
    ├── test_cli.py
    └── test_config.py
```

---

## Próximos pasos de desarrollo

1. **US-002** — Comprobación de prerequisitos (`docker`, `tar`, espacio libre, etc.).
2. **US-010–US-012** — Flujo de backup (parada, tar canónico, arranque).
3. **US-020–US-023** — Flujo de restore con snapshot previo y confirmación.

Consulta el [resumen MVP](./USER_STORIES.md#resumen-mvp-sugerido) en `USER_STORIES.md` para la lista completa.

---

## Licencia

Por definir.
