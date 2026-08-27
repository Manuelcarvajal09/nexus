# Nexus Linux — Test Plan v2

**Fecha:** 2026-08-26 (Fase 1) · **actualizado** misma fecha (Fase 2 y Fase 2.5). Antes de la auditoría de Fase 1 no existía infraestructura de tests (`0` tests). Estado actual: **230/230 tests**. Este documento describe lo que existe ahora y lo que queda pendiente, sin inflar lo ya cubierto.

## 0. Fase 2 / Fase 2.5 — tests nuevos desde el plan original de Fase 1

| Archivo | Tests | Cubre |
|---|---|---|
| `test_type_detection.py` | 40 | Detección de color/email/teléfono/ruta/comando/JSON/código/YouTube, con casos negativos explícitos (falsos positivos evitados a propósito) |
| `test_relative_time.py` | 10 | "Ahora"/"N min"/"Hoy, HH:MM"/"Ayer"/fechas — Nexus no tenía timestamps antes de Fase 2 |
| `test_timeline_model.py` | 13 | Contrato de `QAbstractListModel` de la Timeline virtualizada, sin necesidad de display |
| `test_panel_geometry.py` | 58 | Geometría responsive multi-monitor contra las resoluciones sintéticas pedidas (16:9, 2560×1440, 3440×1440, 3840×1600, portrait 1080×1920/1440×2560) + invariantes (nunca fuera de los límites del monitor, siempre anclado abajo, determinista) |
| `test_web_preview.py` | 41 | Validación SSRF (IPs privadas/loopback/link-local/multicast en IPv4 e IPv6, esquemas peligrosos), parsing HTML, mecánica HTTP (redirects, límite de tamaño, content-type) contra un servidor HTTP local real |
| `test_preview_service.py` | 8 | Cache-first, dedup de fetches en vuelo, limpieza de `_inflight`, y un test end-to-end real a través de `QThreadPool` |
| `test_storage.py` (ampliado) | +15 | Cache de previews (TTL, eviction, YouTube) + enriquecimiento de `search_text` con títulos obtenidos después de la captura |

**Bugs reales encontrados por estos tests antes de llegar a producción** (no hallazgos teóricos — cada uno rompió un test hasta corregirse):

1. `_inflight` nunca se limpiaba tras completar un fetch → bloqueaba reintentos de una URL para siempre tras el primer intento.
2. Conexión SQLite del hilo principal reutilizada desde un hilo de `QThreadPool` → `sqlite3.ProgrammingError` (SQLite prohíbe compartir conexiones entre hilos). Corregido dándole a cada tarea de fondo su propia conexión.
3. El enriquecimiento de `search_text` funcionaba en el camino de fetch nuevo pero no en un cache-hit — una URL ya cacheada, copiada de nuevo, generaba un item nunca buscable por título.

## 2. Verificación en vivo (Fase 2.5, contra el proceso real, no solo tests unitarios)

| Escenario | Resultado |
|---|---|
| Stack A,B,C → Ctrl+V real ×3 | PASS: A→[B,C]→B→[C]→C→vacío, sin botón manual, verificado vía `stack_state` en la DB real tras cada paste |
| Stack A,A,B (duplicado en sesión) | PASS con matiz: el segundo "A" se deduplica al añadirse (comportamiento intencional heredado de Fase 1, no un bug) → el Stack queda [A,B], y ambos se consumen limpiamente hasta vaciar |
| Single-instance (`.venv/bin/nexus-linux` con una instancia ya corriendo) | PASS: sale con `exit 0` y log claro, sigue habiendo una sola instancia |
| Crash recovery (`kill -9` con 1 item pendiente en Stack) | PASS: reinicio restaura el item, `PRAGMA integrity_check` de la DB → `ok` |
| Web preview real (Wikipedia, YouTube, dominio inexistente) | PASS: favicon+OG image+título reales para Wikipedia; título+thumbnail vía oEmbed para YouTube; degradación limpia a "failed" cacheado (sin reintento inmediato) para el dominio roto |
| Soak test (110 operaciones de copiado) | PASS: sin fugas de memoria/fd/hilos — ver `PERFORMANCE_BASELINE.md` §5 |
| Búsqueda enriquecida (título de YouTube buscable) | PASS, en ambos caminos (fetch nuevo y cache-hit) |

## 1. Suite automatizada Fase 1 (`tests/`, pytest, 45 tests originales)

Ejecutar con: `.venv/bin/pip install -e ".[dev]" && .venv/bin/pytest tests/ -v`

### `tests/test_stack.py` (30 tests) — `ClipStack`

| Clase | Cubre |
|---|---|
| `TestBasicSequencing` | 1, 2 y 10 items consumidos secuencialmente vía `consume_after_real_paste` (la forma exacta del bug histórico); no-op seguro sobre Stack vacío |
| `TestManualAdvanceButton` | `paste_next_and_advance()` (botón "Pegar siguiente"): sintetiza paste, consume, re-arma |
| `TestDeduplicationWithinSession` | Recopiar contenido idéntico en la misma sesión no duplica; contenido distinto nunca colapsa |
| `TestContentVariety` | Texto multilínea, emoji/unicode, URL, texto grande (500KB) |
| `TestRemovalAndClear` | Borrar item intermedio preserva el orden del resto; borrar id inexistente no falla; `clear()` no toca el historial subyacente |
| `TestOrderToggle` | FIFO ↔ LIFO consume en el orden correcto |
| `TestClipboardWriteFailureResilience` | Fallo simulado de `write_to_clipboard` (pérdida temporal de ownership del portapapeles) no pierde el item y queda logueado; fallo de `synthetic_paste` no bloquea el avance manual |
| `TestCrashRecovery` | Reinicio simulado (misma DB, `ClipStack` nueva) restaura items pendientes, el flag FIFO/LIFO, y descarta ids huérfanos con log de advertencia |
| `TestStressSequential` | 100 ciclos add→consume secuenciales; 100 items añadidos y drenados preservando orden exacto |

### `tests/test_storage.py` (15 tests) — `StorageService`

| Clase | Cubre |
|---|---|
| `TestCaptureAndDedup` | Dedup por hash actualiza fila existente (no duplica); tipos distintos del mismo texto son filas distintas; borrar el único item deja el historial vacío |
| `TestRetention` | Purga solo lo más viejo que el corte; **nunca** purga items anclados a un pinboard aunque sean viejos |
| `TestPinboards` | Un item en múltiples pinboards (many-to-many real); borrar un pinboard no borra sus items; filtro de `recent()` por pinboard |
| `TestSearch` | Normalización de acentos (`Reunión`→`reunion`); búsqueda insensible a mayúsculas; sin resultados no rompe nada |
| `TestConcurrencySettings` | WAL + `busy_timeout=5000` están realmente activos, no solo en el código |
| `TestEdgeCases` | Unicode/emoji ida y vuelta; texto vacío capturable; ids inexistentes no lanzan excepción |

## 2. Pruebas de estrés ejecutadas manualmente en esta sesión (no automatizadas todavía)

| Prueba | Resultado |
|---|---|
| 10,000 y 50,000 filas sintéticas en SQLite, medir `recent()`/búsqueda/purga | Ver `PERFORMANCE_BASELINE.md` §3 — sin degradación catastrófica |
| `kill -9` real del proceso con 3 items pendientes en el Stack, relanzar | Restauración correcta, verificado end-to-end contra el proceso real |
| Lanzar una segunda instancia mientras la primera corre | Rechazada correctamente, `exit 0`, sin duplicar recursos |
| Reconexión de teclado Bluetooth (causa raíz del bug de CPU) | Corregido; hilo evdev vuelve a ~0% tras desregistrar el dispositivo muerto |
| Decenas de activaciones de Ctrl+Shift+V / Ctrl+Shift+C vía `ydotool` a lo largo de la sesión | 0 eventos perdidos, 0 ventanas duplicadas observadas (no se corrió como bloque aislado de 100 repeticiones cronometradas) |

## 3. Pendiente — no cubierto en esta sesión

Actualizado tras Fase 2.5 — lo ya resuelto se quitó de esta lista, no se dejó ahí para inflar el conteo:

- **Suite de UI automatizada** (`QTest` con clicks reales sobre `MainWindow`/`StackWindow`/`TimelineWindow`) — sigue pendiente; toda la cobertura actual es lógica pura o verificación manual en vivo, no interacción de widgets automatizada.
- **100 activaciones consecutivas cronometradas** — ✅ hecho en Fase 2.5 (60 activaciones dedicadas + decenas cualitativas, ver `PERFORMANCE_BASELINE.md` §0/§4).
- **Soak test de memoria** — ✅ hecho en Fase 2.5 para 110 operaciones (ver `PERFORMANCE_BASELINE.md` §5); una sesión de **horas** reales sigue pendiente.
- **Chaos tests de red** — ✅ cubierto para previews web (dominio inexistente, DNS roto, SSRF contra localhost/redes privadas/`169.254.169.254`, todo con degradación limpia, ver `test_web_preview.py`). "Base de datos bloqueada temporalmente" simulado a propósito sigue sin un test dedicado (el `busy_timeout=5000` de Fase 1 lo mitiga, pero no hay un test que fuerce el escenario).
- **Suspend/resume real del sistema completo** — no se pudo probar (suspender la máquina habría cortado esta sesión de trabajo). Pendiente de prueba física de Manuel; la causa raíz más probable (reconexión de teclado Bluetooth) ya tiene su propio fix+verificación de Fase 1.
- **Reordenar/eliminar pinboards bajo estrés** — sigue sin test automatizado de UI; la lógica de storage subyacente sí está cubierta.
- **Monitor ultrawide 34" real** — la lógica de `panel_geometry.py` está probada contra 58 escenarios sintéticos equivalentes; el hardware real no está conectado todavía.

## 4. Cómo correr la suite

```bash
cd /home/manuelcarvajal/Projects/Nexus-linux
.venv/bin/pip install -e ".[dev]"   # instala pytest si falta
.venv/bin/pytest tests/ -v          # 230 tests
```

Los tests usan SQLite `:memory:` — no tocan `~/.local/share/nexus/nexus.sqlite` (la base de datos real del usuario) bajo ninguna circunstancia.
