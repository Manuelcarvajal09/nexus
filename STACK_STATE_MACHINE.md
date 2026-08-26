# Nexus Stack — Máquina de estados

**Fecha:** 2026-08-26. Documenta el comportamiento real de `ClipStack` (`src/nexus_linux/stack.py`) tras la auditoría v2, incluida la persistencia añadida en esta sesión.

## Por qué no hay un enum `State` explícito

El pedido original sugiere estados nombrados (`EMPTY`, `READY`, `ACTIVE`, `PASTE_PENDING`, ...). Tras auditar el código, **el estado real de `ClipStack` se reduce a dos variables ortogonales**, no a una máquina de estados con transiciones complejas entre "modos":

```python
_items: list[ClipItem]      # la cola de items pendientes, en orden de llegada
reversed_order: bool        # FIFO (False) o LIFO (True)
```

Todo lo demás — si el Stack está "abierto"/"recolectando", si hay un paste en curso — es **derivado**, no estado propio:

- **"¿Está recolectando?"** = `StackWindow.isVisible()` (vive en la UI, no en `ClipStack`).
- **"¿Hay un paste en curso?"** — no existe como estado observable: `paste_next_and_advance()` y `consume_after_real_paste()` son funciones síncronas de Python sin ningún `await`/yield en medio. No hay ventana temporal en la que el sistema esté "a mitad de un paste": o la llamada no ha empezado, o ya terminó. Introducir un estado `PASTE_PENDING` explícito añadiría complejidad sin eliminar ninguna carrera real, porque no hay ninguna carrera real ahí.

Forzar una máquina de estados con más nodos que transiciones reales sería la clase de complejidad accidental que esta auditoría busca eliminar, no añadir. En su lugar, este documento fija el **contrato de transiciones** que si se rompe, sí sería una regresión del bug histórico.

## Estados observables (derivados, no un campo)

| Estado observable | Condición | Quién lo consulta |
|---|---|---|
| `EMPTY` | `len(_items) == 0` | `StackWindow.has_pending_items()`, controla si el botón "Pegar siguiente" está habilitado y si la ventana se autocierra |
| `READY` | `len(_items) > 0` y la cabeza está armada en el portapapeles del sistema | Implícito: `arm_next()` se llama tras cada mutación que cambia la cabeza, así que si hay items, la cabeza *debería* estar armada (ver limitación abajo) |
| `COLLECTING` | `StackWindow.isVisible()` | Controla si `on_captured()` añade capturas nuevas y si `on_real_paste_detected()` reacciona a un Ctrl+V real |

**Limitación conocida y documentada** (no un bug, un caso límite real): si `paste_service.write_to_clipboard()` falla (ver `ClipStack.arm_next()`), el Stack sigue creyendo que la cabeza está "armada" — el item sigue siendo lógicamente la cabeza — pero el portapapeles del sistema puede no reflejarlo. Antes de esta auditoría esto era completamente silencioso; ahora queda un `log.warning`. No se cambió el contrato de retorno de `arm_next()` (sigue devolviendo el item aunque la escritura falle) porque hacerlo cambiaría el comportamiento de `paste_next_and_advance()` de forma que confundiría "Stack vacío" con "escritura falló" — una distinción que merece resolverse con una señal de UI dedicada en Fase 2 (p. ej. un aviso "no se pudo copiar, reintenta"), no un cambio de contrato apurado.

## Transiciones válidas

```
                     add(item)
                  ┌─────────────────────┐
                  │                     ▼
   ┌───────┐  add()/  ┌────────────────────────┐
   │ EMPTY │ ────────▶│  items = [A, B, ...]    │
   └───────┘          │  cabeza armada en el     │
       ▲              │  portapapeles del sistema│
       │              └────────────┬─────────────┘
       │                           │
       │        consume_after_real_paste()  /  paste_next_and_advance()
       │                           │
       │                 pop cabeza (FIFO/LIFO) + persistir + re-armar
       │                           │
       │                           ▼
       │              ┌────────────────────────┐
       └──────────────│  items = [] ?           │──── no ──▶ (vuelve arriba, nueva cabeza armada)
        sí, vacío      └────────────────────────┘
```

### Reglas de consumo (verificadas con tests, `tests/test_stack.py`)

Dado Stack = `[A, B, C]` (FIFO, `reversed_order=False`):

| Operación | Resultado | Test que lo fija |
|---|---|---|
| `consume_after_real_paste()` (Ctrl+V real detectado) | Devuelve `A`; Stack pasa a `[B, C]`; `B` queda armado en el portapapeles | `test_two_items_fifo_consumes_in_copy_order`, `test_ten_items_sequential_real_paste_fully_drains_in_order` |
| `paste_next_and_advance()` (botón manual) | Arma `A` → `ydotool` Ctrl+V sintético → Stack pasa a `[B, C]` → arma `B` | `test_paste_next_and_advance_synthesizes_and_consumes` |
| `consume_after_real_paste()` sobre Stack vacío | Devuelve `None`, no lanza excepción, no cambia nada | `test_consume_after_real_paste_on_empty_stack_is_safe_noop` |
| `reversed_order = True` + `toggle_order()` | Próximo consumo devuelve `C` (LIFO), no `A` | `test_reversed_order_consumes_lifo` |
| `add()` de contenido con el mismo `content_hash` que algo ya en el Stack **en esta sesión** | Devuelve `False`, no duplica la fila (mirror intencional de `PasteStack.swift`, no es un bug de deduplicación arbitraria) | `test_recopying_identical_content_in_same_session_is_a_noop` |
| `remove_persistent(id)` de un item intermedio | Se borra de `_items` y de `clip_items` (historial); el orden del resto se preserva | `test_remove_intermediate_item_preserves_order_of_remainder` |
| `clear()` | `_items = []`; el historial subyacente **no** se toca (solo la sesión de Stack, no las filas de `clip_items`) | `test_clear_empties_stack_without_touching_history` |

## Atomicidad (cómo se garantiza, no como aspiración)

La operación *"consumir item + persistir estado + preparar el siguiente + notificar UI"* que pedía el prompt original como "debe tratarse como una operación coherente" se resuelve así, concretamente:

1. **Sin async/await dentro de las funciones de mutación.** `consume_next()`, `add()`, `remove_persistent()`, `clear()`, `toggle_order()` son funciones Python síncronas normales. El GIL garantiza que ninguna otra tarea de Python se intercala a mitad de una de ellas — no hace falta un lock explícito.
2. **Persistencia en el mismo paso que la mutación en memoria**, no en un callback separado ni diferida: `consume_next()` hace `self._items.pop(idx)` y `self._persist()` en las dos líneas siguientes, dentro de la misma función — no hay forma de que el proceso muera *entre* el pop en memoria y el `UPDATE`/`INSERT` en SQLite sin que sea a nivel de sistema operativo (kill -9 en medio de una llamada a `sqlite3`), caso cubierto por el `commit()` de SQLite siendo atómico a nivel de archivo.
3. **La UI se refresca explícitamente después de cada mutación relevante** (`stack_window.refresh()` en `app.py:on_real_paste_detected()` y en cada handler de botón de `StackWindow`) — no hay un mecanismo de "la UI se entera sola" (no hay señal `changed` en `ClipStack` a la que la ventana esté suscrita); cada call site es responsable de llamar `refresh()`. Esto es explícito y auditable, aunque significa que un futuro call site nuevo que mute `ClipStack` sin llamar `refresh()` después reintroduciría el síntoma del bug histórico. **Recomendación de Fase 2**: si se añaden más puntos de mutación del Stack (p. ej. drag&drop de reordenamiento), considerar una señal `Signal()` en `ClipStack` a la que `StackWindow` se suscriba una sola vez, en vez de seguir dependiendo de que cada call site recuerde llamar `refresh()`.

## Recuperación de errores (nuevo en esta auditoría)

Ver `ARCHITECTURE_AUDIT_V2.md` §2.4 y §3 (hallazgo #4) para el detalle completo. Resumen del contrato:

- Cada mutación de `ClipStack` sobrescribe `stack_state`/`stack_meta` en la misma llamada (no diferido, no por timer).
- Al arrancar, `ClipStack.load_persisted()` reconstruye `_items` desde `stack_state`, salta (con `log.warning`) cualquier id cuyo `clip_items` ya no exista, y re-arma la cabeza restaurada en el portapapeles inmediatamente.
- `ON DELETE CASCADE` en el esquema mantiene `stack_state` consistente con `clip_items` automáticamente incluso fuera del código de `ClipStack` (p. ej. si el usuario borra desde Historial un item que está en el Stack).
- Verificado en vivo con `kill -9` real sobre el proceso con 3 items pendientes — ver bitácora de comandos de esta sesión.
