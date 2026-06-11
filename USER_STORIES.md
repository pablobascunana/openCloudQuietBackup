# Historias de usuario — OpenCloud Quiet Backup

Aplicación orientada a backups **coherentes** de OpenCloud en Docker (parada del stack, preservación de metadatos, restauración predecible). Formato: **Como** [rol] **quiero** [acción] **para** [beneficio].

**Convenciones**

- IDs: `US-XXX` (trazabilidad con backlog y PRs).
- **MVP**: historias imprescindibles para un primer release usable en un NAS Linux.
- Prioridad sugerida: **P0** crítico, **P1** alto, **P2** medio, **P3** bajo.

---

## Épica A — Configuración y requisitos del entorno

### US-001 — Definir rutas del stack OpenCloud

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador del sistema |
| **Quiero** | indicar la ruta raíz de OpenCloud (`config/`, `data/`), el directorio del `docker-compose` y el fichero compose si no es el predeterminado |
| **Para** | que la aplicación sepa qué parar y qué copiar sin ambigüedad |

**Criterios de aceptación**

- [x] Se valida que existan `config/` y `data/` bajo la raíz indicada.
- [x] Se detecta o se permite especificar `docker-compose.yml` / `docker-compose.yaml`.
- [x] Los errores de configuración son mensajes claros (ruta inexistente, permisos).

**Prioridad:** P0 · **MVP:** sí · **Implementación:** `opencloud_backup.config.load_stack_paths`, CLI `opencloud-quiet-backup validate`.

---

### US-002 — Comprobar prerequisitos antes de un job

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | que la aplicación verifique Docker, `docker compose`, espacio libre mínimo y herramientas necesarias (`tar`, compresión, `rsync` en restore) |
| **Para** | no iniciar un backup o restore que vaya a fallar a mitad de proceso |

**Criterios de aceptación**

- [x] Comprobación ejecutable en modo “solo validar” (dry-run de prerequisitos).
- [x] Umbral de espacio libre configurable (absoluto o % del volumen).
- [x] Lista explícita de binarios o rutas faltantes.

**Prioridad:** P0 · **MVP:** sí · **Estado:** implementada (`prereqs` CLI, US-002)

---

### US-003 — Autenticación / contexto de ejecución

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | documentación y comprobación de que el proceso tiene acceso a `docker` (socket o grupo) y permisos de lectura/escritura sobre las rutas de datos |
| **Para** | evitar fallos silenciosos por permisos |

**Criterios de aceptación**

- [x] Mensaje claro si `docker ps` falla (usuario sin grupo `docker`, etc.).
- [x] En documentación: matriz recomendada usuario root vs usuario en grupo `docker`.

**Prioridad:** P1 · **MVP:** sí (mínimo comprobación + docs)

---

## Épica B — Backup coherente (core)

### US-010 — Parar OpenCloud antes de copiar

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | que la aplicación ejecute `docker compose down` (o equivalente documentado) en el proyecto configurado antes de leer `config/` y `data/` |
| **Para** | obtener una copia consistente a nivel de aplicación |

**Criterios de aceptación**

- [ ] Uso de `docker compose` con `--project-directory` y `-f` al fichero correcto.
- [ ] Timeout configurable para la fase de parada; si falla, el job aborta sin dejar el estado indeterminado (log + código de salida ≠ 0).
- [ ] Log con marca de tiempo de inicio y fin de parada.

**Prioridad:** P0 · **MVP:** sí

---

### US-011 — Crear archivo de backup canónico con metadatos

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | generar un archivo (p. ej. `opencloud-YYYY-MM-DD_HHMMSS.tar.zst`) que incluya `opencloud/config`, `opencloud/data` y opcionalmente `opencloud/.env`, preservando xattrs, ACLs y propietarios numéricos |
| **Para** | poder restaurar de forma fiable en el mismo u otro host compatible |

**Criterios de aceptación**

- [ ] Empaquetado con las mismas garantías semánticas acordadas (`tar` con `--xattrs --acls --numeric-owner` o equivalente documentado).
- [ ] Opción configurable: incluir o excluir `.env`.
- [ ] Compresión configurable (`zstd` por defecto, `gzip`, sin comprimir).
- [ ] Nombre de fichero y estructura interna del tar documentados y estables entre versiones de app (o versión de formato en metadatos).

**Prioridad:** P0 · **MVP:** sí

---

### US-012 — Arrancar OpenCloud tras el backup

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | que tras finalizar el empaquetado se ejecute `docker compose up -d` |
| **Para** | minimizar tiempo de indisponibilidad y dejar el servicio operativo |

**Criterios de aceptación**

- [ ] Si el backup falla, la política es explícita: ¿reintentar `up` siempre? (recomendado sí) y registrar el error del backup.
- [ ] Log del estado del stack tras `up` (p. ej. `docker compose ps`).

**Prioridad:** P0 · **MVP:** sí

---

### US-013 — Integridad del archivo generado

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | opcionalmente calcular y guardar un hash (SHA-256) del archivo de backup junto con metadatos (tamaño, fecha) |
| **Para** | verificar integridad antes de un restore o tras copia a otro medio |

**Criterios de aceptación**

- [ ] Fichero lado a lado `.sha256` o registro en base de datos / JSON de manifiesto.
- [ ] Comando o acción “verificar hash” sobre un archivo existente.

**Prioridad:** P1 · **MVP:** no (deseable pronto)

---

## Épica C — Restauración

### US-020 — Parar stack antes de restaurar

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | la misma secuencia de parada que en backup antes de sobrescribir datos |
| **Para** | no mezclar escrituras con datos en restauración |

**Criterios de aceptación**

- [ ] Misma lógica de compose que US-010.
- [ ] Abortar si la parada falla.

**Prioridad:** P0 · **MVP:** sí

---

### US-021 — Snapshot de seguridad previo al restore

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | que antes de sobrescribir se copie el estado actual de `config/` y `data/` (y `.env` si existe) a un directorio de snapshot con `rsync -aHAX` o equivalente |
| **Para** | poder revertir manualmente si el restore falla |

**Criterios de aceptación**

- [ ] Directorio de snapshot configurable; nombre con marca de tiempo.
- [ ] Opción “conservar snapshot anterior” vs “sustituir”.
- [ ] Documentación del espacio adicional requerido.

**Prioridad:** P1 · **MVP:** sí

---

### US-022 — Extraer y aplicar datos restaurados

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | extraer el tar al mismo esquema interno y sincronizar hacia las rutas reales con `rsync -aHAX --delete` (o equivalente documentado) |
| **Para** | preservar hard links, xattrs y ACLs al restaurar |

**Criterios de aceptación**

- [ ] Validación de que el archivo contiene `opencloud/config` y `opencloud/data`.
- [ ] Soporte de extensiones `.tar.zst`, `.tar.gz`, `.tar`.
- [ ] Si falta `.env` en el archivo, comportamiento documentado (no borrar el existente sin confirmación).

**Prioridad:** P0 · **MVP:** sí

---

### US-023 — Arrancar OpenCloud tras restore

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | `docker compose up -d` al finalizar y una salida legible del estado |
| **Para** | confirmar que el stack levanta tras la restauración |

**Criterios de aceptación**

- [ ] Log de `ps` o equivalente.
- [ ] Opción futura: comprobación HTTP (historia aparte) — no bloquea MVP.

**Prioridad:** P0 · **MVP:** sí

---

### US-024 — Confirmación fuerte en UI/CLI para restore

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | que un restore destructivo requiera confirmación explícita (p. ej. escribir nombre del archivo o `--i-know-what-im-doing`) |
| **Para** | evitar borrados accidentales |

**Criterios de aceptación**

- [ ] En CLI: flag obligatorio o prompt interactivo.
- [ ] En UI: segundo paso de confirmación con resumen de rutas afectadas.

**Prioridad:** P1 · **MVP:** sí (mínimo en CLI)

---

## Épica D — Retención y almacenamiento local

### US-030 — Política de retención local

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | conservar copias solo N días y/o un número máximo de archivos en el directorio de backups |
| **Para** | controlar espacio en disco |

**Criterios de aceptación**

- [ ] Borrado solo de archivos que coincidan con el patrón de backups de la aplicación (no borrar otros ficheros).
- [ ] Log de qué archivos se eliminaron.

**Prioridad:** P1 · **MVP:** sí

---

### US-031 — Directorio de salida configurable

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | definir el directorio destino de los `.tar.zst` |
| **Para** | guardar en un volumen dedicado |

**Criterios de aceptación**

- [ ] Creación del directorio si no existe (opcional, configurable).
- [ ] Comprobación de espacio previa (enlace con US-002).

**Prioridad:** P0 · **MVP:** sí

---

## Épica E — Programación y notificaciones

### US-040 — Programar backups

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | definir horarios (p. ej. crontab o expresiones cron) para ejecutar backups automáticamente |
| **Para** | no depender de ejecución manual |

**Criterios de aceptación**

- [ ] Documentación clara para alternativa systemd timer si no hay daemon integrado en MVP.
- [ ] Si hay scheduler integrado: zona horaria configurable y “no solapar jobs” o cola.

**Prioridad:** P2 · **MVP:** no (suficiente cron externo documentado en MVP)

---

### US-041 — Notificación de éxito o fallo

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | recibir una notificación (webhook, email o canal único configurable) al terminar un job con éxito o error |
| **Para** | enterarme sin revisar logs a mano |

**Criterios de aceptación**

- [ ] Payload mínimo: nombre del job, duración, resultado, enlace o ruta a log.
- [ ] No almacenar secretos en logs en claro.

**Prioridad:** P2 · **MVP:** no

---

## Épica F — Destinos remotos (post-MVP)

### US-050 — Subir copia tras generación local

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | subir el archivo de backup a un destino remoto (p. ej. S3 compatible, WebDAV, rsync sobre SSH) después de crearlo localmente |
| **Para** | tener copia fuera del NAS |

**Criterios de aceptación**

- [ ] No eliminar la copia local verificada hasta confirmar subida exitosa (política configurable).
- [ ] Reintentos con backoff configurable.

**Prioridad:** P2 · **MVP:** no

---

### US-051 — Verificar subida remota

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | comparar tamaño y/o hash remoto con el local |
| **Para** | confiar en que el remoto es íntegro |

**Criterios de aceptación**

- [ ] Fallo de verificación marca el job como fallido y deja instrucciones en log.

**Prioridad:** P2 · **MVP:** no

---

## Épica G — Observabilidad y seguridad

### US-060 — Logs estructurados por job

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | logs por ejecución con nivel configurable (info, debug) y salida a archivo rotado |
| **Para** | auditar y depurar problemas |

**Criterios de aceptación**

- [ ] ID de correlación por job.
- [ ] Duración total y duración de ventana con stack parado.

**Prioridad:** P1 · **MVP:** sí

---

### US-061 — No exponer secretos

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | que rutas a `.env` y credenciales remotas no aparezcan en logs en claro cuando sea evitable |
| **Para** | reducir fugas en copias de logs |

**Criterios de aceptación**

- [ ] Enmascarado de contraseñas en parámetros de conexión.
- [ ] Revisión de “verbose” para no volcar entorno completo.

**Prioridad:** P1 · **MVP:** parcial

---

### US-062 — Interfaz web opcional con autenticación

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | una UI para lanzar backup/restore, ver historial y logs (fase posterior al CLI) |
| **Para** | operar sin SSH |

**Criterios de aceptación**

- [ ] Autenticación mínima o despliegue detrás de reverse proxy documentado.
- [ ] Mismas reglas de confirmación que US-024 para restore.

**Prioridad:** P3 · **MVP:** no

---

## Épica H — Calidad y documentación

### US-070 — Documentación de recuperación ante desastres

| Campo | Contenido |
|-------|-----------|
| **Como** | administrador |
| **Quiero** | una guía corta: requisitos, restore en host limpio, comprobaciones post-restore |
| **Para** | recuperarme sin conocimiento interno de la aplicación |

**Criterios de aceptación**

- [ ] Lista de verificación imprimible (o página única).
- [ ] Versión del formato de backup documentada.

**Prioridad:** P1 · **MVP:** sí (documentación mínima)

---

### US-071 — Pruebas automatizadas del flujo crítico

| Campo | Contenido |
|-------|-----------|
| **Como** | desarrollador |
| **Quiero** | pruebas de integración o E2E en entorno Docker de prueba que validen backup+restore de un árbol ficticio |
| **Para** | no romper US-010–US-022 en refactors |

**Criterios de aceptación**

- [ ] CI ejecuta al menos un caso feliz y un caso de error controlado (p. ej. parada fallida simulada).

**Prioridad:** P2 · **MVP:** no

---

## Resumen MVP sugerido

| ID | Historia |
|----|----------|
| US-001 | Rutas del stack |
| US-002 | Prerequisitos |
| US-003 | Autenticación / contexto de ejecución |
| US-010 | Parar antes de backup |
| US-011 | Tar canónico con metadatos |
| US-012 | Arrancar tras backup |
| US-020 | Parar antes de restore |
| US-022 | Extraer y rsync |
| US-023 | Arrancar tras restore |
| US-024 | Confirmación restore (CLI) |
| US-030 | Retención local |
| US-031 | Directorio de salida |
| US-060 | Logs por job |
| US-070 | Documentación mínima |

---

## Notas de trazabilidad

- Las historias están alineadas con el flujo **parada → copia con metadatos → arranque** descrito para OpenCloud en Docker.
- Prioridades y MVP pueden ajustarse según calendario; este documento es la línea base para el backlog.
