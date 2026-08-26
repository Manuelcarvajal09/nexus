# Nexus Linux — Test Plan v2

**Fecha:** 2026-08-26. Antes de esta auditoría no existía infraestructura de tests (`0` tests, sin `pytest` instalado, sin directorio `tests/`). Este documento describe lo que existe ahora y lo que queda pendiente, sin inflar lo ya cubierto.

## 1. Suite automatizada (`tests/`, pytest, 45 tests, todos pasando)

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

Esto se lista explícitamente en vez de omitirse, para que el gate de Fase 1 sea honesto:

- **Suite de UI automatizada** (abrir/cerrar ventanas, clicks, `QTest`) — los 45 tests actuales cubren la lógica de `ClipStack`/`StorageService` sin Qt real; no hay tests que instancien `MainWindow`/`StackWindow` de verdad.
- **100 activaciones consecutivas cronometradas** de cada atajo como prueba formal aislada (hay evidencia cualitativa de esta sesión, no un número de tasa de fallo sobre una muestra grande dedicada).
- **Soak test de memoria** (horas de uso simulado, verificar que no crece de forma anormal).
- **Chaos tests explícitos**: red offline (no aplica todavía, no hay features de red implementadas), "base de datos bloqueada temporalmente" simulado a propósito, endpoint de preview inválido (no aplica, sin previews de red todavía).
- **Prueba de logout/login y suspend/resume** real del sistema.
- **Reordenar/eliminar pinboards bajo estrés** (la lógica de storage está cubierta; el flujo de UI de `pinboard_sidebar.py` con drag&drop no tiene test automatizado).

## 4. Cómo correr la suite

```bash
cd /home/manuelcarvajal/Projects/Nexus-linux
.venv/bin/pip install -e ".[dev]"   # instala pytest si falta
.venv/bin/pytest tests/ -v
```

Los tests usan SQLite `:memory:` — no tocan `~/.local/share/nexus/nexus.sqlite` (la base de datos real del usuario) bajo ninguna circunstancia.
