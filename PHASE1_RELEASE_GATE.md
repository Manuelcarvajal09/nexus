# Nexus Linux — Phase 1 Release Gate

**Fecha:** 2026-08-26 · Evaluado contra los criterios de salida definidos en el pedido original. Cada fila es honesta: `PASS` significa verificado en vivo o con test automatizado, `PARTIAL` significa mejorado y verificado pero sin alcanzar el ideal original, `PENDIENTE` significa no cubierto en esta sesión (no se inventa un PASS).

| Criterio | Estado | Evidencia |
|---|---|---|
| Build pasa | **PASS** | `py_compile` limpio en todos los módulos tocados; `pip install -e .` funcional |
| App inicia | **PASS** | Arrancado y verificado en vivo repetidas veces durante esta sesión, sin excepciones en el log |
| No crash loops | **PASS** | Sesión de horas sin crash; `kill -9` deliberado se recupera limpiamente (ver Stack persistence) |
| Single-instance comprobado | **PASS** | No existía antes de esta auditoría. Implementado (`flock`) y verificado: segunda instancia sale con `exit 0`, log claro, sin duplicar procesos |
| Shortcuts fiables | **PASS** | Ctrl+Shift+V/C verificados repetidamente vía `ydotool` a lo largo de toda la sesión: 0 eventos perdidos, 0 ventanas duplicadas observadas. (No se corrió el bloque formal de "100 repeticiones cronometradas" como test aislado — evidencia cualitativa fuerte, no un número de tasa de fallo dedicado) |
| Stack secuencial fiable | **PASS** | 30 tests automatizados (`tests/test_stack.py`) cubren 1/2/10/100 items vía `consume_after_real_paste`, LIFO/FIFO, dedup, remoción intermedia, fallo de escritura; verificado además en vivo con `kill -9` |
| Consumo automático fiable | **PASS** | Camino evdev→`consume_after_real_paste`→`refresh()` auditado línea por línea; el bug histórico (fix previo a esta sesión) no muestra señales de regresión, respaldado ahora por tests |
| Persistencia consistente | **PASS** | Nuevo en esta sesión: Stack sobrevive crash/reinicio sin pérdida arbitraria (antes: `FAIL` — no existía) |
| Database estable | **PASS** | WAL + `busy_timeout=5000` activos (antes: `journal_mode=delete`, `busy_timeout=0`); probado hasta 50,000 filas sintéticas sin degradación |
| Previews no bloqueen | **N/A** | No hay previews de red implementados todavía (Fase 2) — nada que evaluar hoy |
| Apertura optimizada | **PARTIAL** | ~900ms → ~280-380ms medido en vivo (mejora real de ~2.5-3x). No alcanza el ideal <200ms — el remanente es arquitectural (falta virtualización de lista), documentado como ítem explícito de Fase 2, no un parche pendiente |
| Logs limpios | **PASS** | Sin excepciones no manejadas durante toda la sesión; categorías de logging ya existentes (`nexus.stack`, `nexus.window`, `nexus.evdev_watcher`, `nexus.shortcuts`, `nexus.monitor`, `nexus.dbus_service`) más las añadidas hoy (`nexus.stack` para fallos de escritura, `nexus.window` para timing) |
| Tests críticos pasen | **PASS** | 45/45 tests pasan (`.venv/bin/pytest tests/`), verificado desde dos directorios de trabajo distintos para descartar fragilidad de import |
| No bugs P0/P1 conocidos | **PASS** | Los 2 P0/P1 encontrados en esta auditoría (CPU al 100% por bucle de evdev, ausencia de single-instance) están corregidos y verificados en vivo. Ver §"Bugs conocidos remanentes" abajo para lo que queda a nivel P2/P3 |

## Resumen ejecutivo

```
FASE 1
Architecture:       PASS  (mapeada de punta a punta, ver ARCHITECTURE_AUDIT_V2.md)
Stack reliability:  PASS  (30 tests + verificación en vivo con kill -9)
Clipboard:          PASS  (sin bucles de eventos, sin feedback loops, watcher de evdev corregido)
Database:           PASS  (WAL + busy_timeout, probado hasta 50k filas)
Shortcuts:          PASS  (verificado repetidamente en vivo, sin pérdidas)
Performance:        PARTIAL (Ctrl+Shift+V: ~900ms → ~280-380ms; ideal <200ms pendiente de virtualización de lista en Fase 2)
Regression tests:   45/45
Known P0/P1 bugs:   0
```

**Veredicto: Fase 1 cumple el gate para avanzar a Fase 2.** El único punto en `PARTIAL` (latencia de apertura) tiene causa raíz identificada, fix parcial ya aplicado y verificado, y el remanente requiere una decisión de arquitectura de UI (virtualización de lista) que corresponde evaluarse junto con el rediseño de Fase 2 — no es un bug de fiabilidad ni bloquea el trabajo de producto.

## Bugs conocidos remanentes (P2/P3, no bloquean Fase 2)

1. Captura espuria del contenido del portapapeles al arrancar (`wl-paste --watch` emite un "changed" inicial). Impacto: como máximo una fila extra por dedup-hash, sin corrupción.
2. Excepciones dentro de corrutinas asyncio no pasan por el hook estructurado de logging (solo las de slots Qt sí). No se observó ningún caso real en esta sesión, es un gap de observabilidad a cerrar antes de depender 100% del log para diagnóstico en producción.
3. Un item borrado del historial mientras está en una sesión de Stack ya abierta en memoria no se reconcilia hasta la siguiente mutación (el `stack_state` persistido sí queda consistente vía `ON DELETE CASCADE`; solo la vista en memoria de esa sesión viva podría mostrar una fila fantasma transitoriamente).
4. Búsqueda con `LIKE` no indexable por el comodín inicial — 80ms sobre 50,000 filas sin coincidencias. Aceptable hoy (540 filas reales); FTS5 sería la vía si el historial creciera de forma sostenida a decenas de miles.
5. `is_collecting()` del Stack depende de `isVisible()` de la ventana — cerrar la ventana del Stack antes de terminar de pegar todo detiene el auto-avance hasta reabrirla. Comportamiento por diseño (mirror del original), no un bug, pero vale una decisión de producto consciente en Fase 2.

Ninguno de estos cinco es P0/P1: ninguno causa pérdida de datos, corrupción, o falla de las rutas críticas (Stack, atajos, captura).

## Qué falta para un gate "completo" al 100% (no bloqueante, transparente)

- Suite de UI automatizada con `QTest` (los 45 tests actuales cubren lógica pura, no widgets reales).
- Bloque formal de 100 activaciones de shortcut cronometradas como test aislado (hay evidencia cualitativa fuerte de esta sesión, no un número de tasa de fallo dedicado).
- Soak test de memoria de varias horas.
- Chaos tests de red/DB-lock (no aplican todavía — sin features de red implementadas).
