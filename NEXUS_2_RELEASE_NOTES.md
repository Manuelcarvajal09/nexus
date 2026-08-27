# Nexus 2.0 — Release Notes

**Fecha:** 2026-08-26 · **Rama:** `nexus-2.0` (checkpoint estable previo: tag `nexus-linux-stable-1.x` en `main`; checkpoint Fase 2: `nexus-2.0-phase2-complete`; **release candidate actual: `nexus-2.0-rc1`**) · **Tests:** 230/230

Este documento resume todo lo entregado en Fase 1 (auditoría/cimientos), Fase 2 (producto/diseño) y Fase 2.5 (pulido de release candidate). Todo lo descrito aquí fue verificado en vivo contra el proceso real en Erwin — no es una lista de intenciones. Ver `NEXUS_2_RC1_REPORT.md` para el reporte final con evidencia condensada.

---

## Resumen ejecutivo

Nexus pasó de "migrado de Mac a Linux, con un bug de CPU activo ahora mismo" a una base con:

- **0 bugs P0/P1 conocidos** (había 2 activos al empezar: bucle infinito de CPU y ausencia de instancia única).
- **Ctrl+Shift+V: de ~900ms a ~3-4ms internos / ~26ms end-to-end real** (shortcut físico → ventana lista, medido con `ydotool` + timestamps de alta precisión, no solo el trabajo interno de Qt).
- **230 tests automatizados** (0 al empezar la sesión de Fase 1).
- Persistencia real del Stack ante un crash (antes: pérdida silenciosa y total) — reverificada en Fase 2.5 con `kill -9` real.
- Una experiencia principal nueva (Nexus Timeline) inspirada en el análisis de producto de Paste, con identidad propia: barra anclada al borde inferior, geometría responsive multi-monitor determinista, previews web reales (favicon/OG image/YouTube) con protección SSRF completa.

## Fase 2.5 — Release Candidate Polish (resumen; detalle completo en `NEXUS_2_RC1_REPORT.md`)

Sobre la base de Fase 2 (ya aceptada, no reescrita), esta ronda entregó:

1. **Geometría de panel determinista y responsive** (`panel_geometry.py`, 58 tests): reemplaza las coordenadas fijas (`x=0, y=900, width=1920`) por un cálculo recomputado en cada apertura a partir de `QScreen.availableGeometry()` real del monitor bajo el cursor — nunca una posición recordada/persistida. Breakpoints por ancho lógico (STANDARD/WIDE/ULTRAWIDE) más un modo COMPACT para orientación portrait, probados contra las resoluciones sintéticas 16:9, 2560×1440, 3440×1440, 3840×1600, portrait 1080×1920 y 1440×2560.
2. **`FramelessWindowHint` restaurado** en la Timeline (sin barra de título → sin affordance de arrastre/resize) tras confirmar en vivo que el hallazgo de "pierde foco" de la sesión anterior era en realidad una carrera de temporización (pedir foco antes de que Wayland completara la activación real), no el flag frameless en sí — ambos hallazgos quedan documentados en el código, no solo en este archivo.
3. **Previews web reales con seguridad SSRF completa** (`web_preview.py`, `preview_service.py`, `url_preview_cache`): favicon + Open Graph image + título para URLs normales, thumbnail + título + canal vía oEmbed público (sin API key) para YouTube. DNS resuelto y validado antes de conectar, conexión anclada a esa IP (protección contra DNS rebinding), redirects revalidados uno por uno, límite de tamaño de respuesta, whitelist de Content-Type, timeouts estrictos. Nunca bloquea la captura ni la apertura de Nexus — corre en `QThreadPool`, cache-first, con fallback a solo-dominio.
4. **Pinboards**: corregidos dos hallazgos reales — rename/ícono/mover/eliminar solo vivían en clic derecho (ahora también un botón "⋮" visible), y los chips de filtro de pinboard en la Timeline no tenían estilo `:checked` (se veían idénticos seleccionados o no).
5. **Búsqueda enriquecida**: los títulos obtenidos de forma asíncrona (YouTube, OG) ahora se indexan para búsqueda — antes solo el contenido crudo del portapapeles era buscable.
6. **Blindaje defensivo**: `TimelineWindow`/`StackWindow` ya no crashean si el compositor reporta temporalmente cero pantallas (condición real observada en vivo durante esta sesión — ver `PERFORMANCE_BASELINE.md` §0).
7. **Re-verificación en vivo del criterio "MUY IMPORTANTE"**: Stack A,B,C consumido secuencialmente vía Ctrl+V real sin botón manual, confirmado de nuevo contra el proceso real (no solo los 30 tests unitarios ya existentes).

## Fase 1 — Core / Reliability (gate: PASS)

Ver `ARCHITECTURE_AUDIT_V2.md`, `STACK_STATE_MACHINE.md`, `PERFORMANCE_BASELINE.md`, `TEST_PLAN_V2.md`, `PHASE1_RELEASE_GATE.md` para el detalle completo. Resumen de hallazgos corregidos:

1. **CPU al 100% sostenido** (17h45min acumuladas) por un bucle infinito en `evdev_stack_watcher.py` cuando el teclado Bluetooth se reconectaba. Corregido con detección de desconexión + re-escaneo periódico.
2. **Sin instancia única** — dos procesos de Nexus podían correr a la vez. Corregido con `flock()`.
3. **SQLite sin WAL ni busy_timeout** — corregido.
4. **Fallo silencioso al armar un item en el portapapeles** — ahora se loguea.
5. **Stack sin persistencia** — un crash con items pendientes los perdía sin rastro. Corregido: `stack_state`/`stack_meta`, restaurado al arrancar, verificado con `kill -9` real.
6. **Ctrl+Shift+V lento** (~900ms → ~280-380ms en Fase 1, vía cache de miniaturas + sombra perezosa).

## Fase 2 — Product Experience

### Investigación previa (obligatoria antes de diseñar)

`PASTE_PRODUCT_ANALYSIS.md`: investigación exhaustiva de Paste basada en documentación pública oficial (home, `/use-cases`, y una decena de artículos de `/help`), no en suposiciones. Hallazgos que informaron directamente el diseño:

- El panel principal de Paste es **una barra horizontal anclada al borde inferior de la pantalla**, no un panel centrado — verificado como técnicamente viable en Erwin con una prueba real de posicionamiento antes de construir nada.
- **Paste Stack se consume con el Ctrl+V normal del sistema**, no con un atajo especial — confirma que el diseño que Nexus ya tenía desde Fase 1 (evdev detectando el Ctrl+V real) es el mismo que usa el producto de referencia, no una solución de compromiso.
- Quick Paste es puramente posicional (Cmd+1-9 sobre la lista visible actual), sin atajos personalizados por snippet.

Ningún nombre, logo, asset, ilustración ni texto comercial de Paste fue copiado — ver la sección de propiedad intelectual en ese documento.

### Nexus Timeline (la pieza central)

`Ctrl+Shift+V` ahora abre **Nexus Timeline**: una barra horizontal de cards en la parte inferior de la pantalla, construida sobre `QAbstractListModel` + `QStyledItemDelegate` (`timeline_model.py`, `timeline_delegate.py`, `timeline_window.py`) en vez del `QListWidget` con un widget real por fila que usaba el panel anterior.

**Resultado medido:** ~280-380ms → **3-4ms**. Qt solo pinta las ~7-8 cards realmente visibles; el costo ya no depende de cuántos items tiene el historial. Ver `PERFORMANCE_BASELINE.md` §2.1 para el desglose completo.

**Hallazgo real de Wayland/KDE, documentado y resuelto en el camino** (no un detalle menor): una primera versión de esta ventana usaba `FramelessWindowHint` para lograr el aspecto de barra sin bordes. En vivo, esa ventana fallaba de forma intermitente en retener el foco real de teclado de Wayland tras activarse — confirmado con instrumentación de `changeEvent`/`focusWidget`, no adivinado. Se revirtió a una ventana decorada normal (mismas flags que `MainWindow`/`StackWindow`, ya probadas en semanas de uso real) y se corrigió además una condición de carrera real: pedir el foco inmediatamente después de `show()` (incluso diferido un tick de evento vía `QTimer.singleShot(0, ...)`) llega antes de que Wayland complete la activación real de la ventana; la solución robusta es atar la petición de foco al evento `ActivationChange` real. Ambos hallazgos quedan documentados en el docstring del módulo para que no se reintroduzcan.

**Funciona, verificado en vivo (no solo por tests):**
- Apertura instantánea (~3-4ms), con cards, chips de pinboard, badge de conteo.
- Cards adaptativas por tipo (`type_detection.py`): texto, URL, YouTube, imagen, color, código, JSON, archivo, email, teléfono, ruta, comando — con badges de Quick Paste (1-9) visibles.
- Tiempo relativo en cada card (`relative_time.py`: "Ahora", "N min", "Hoy, HH:MM", "Ayer", "12 ago") — Nexus no mostraba ningún timestamp antes de esta sesión.
- Teclado: escribir filtra en vivo; `Return` mueve el foco a la lista; flechas navegan (confirmado visualmente, la card seleccionada cambia); `Ctrl+1..9` pega directamente esa posición y cierra; `Escape` cierra; `Ctrl+Izq/Der` cicla pinboards.
- Cache de miniaturas compartido (`thumbnails.py`, extraído de `row_widget.py`) entre la Timeline y las listas verticales antiguas.

**`MainWindow` no desapareció** — sigue existiendo como `library_window`, la vista de gestión de pinboards (renombrar, ícono, reordenar, eliminar) que no cabe bien en una barra horizontal corta. Es una decisión de arquitectura deliberada (documentada en `app.py`), no una regresión: separa "recall rápido, uso diario" de "gestión ocasional", el mismo patrón que Paste usa (barra rápida vs. acciones de menú/ajustes).

### Nexus Stack 2.0

Rediseño puramente visual de `stack_window.py`: el item que un Ctrl+V real va a consumir ahora tiene su propia card destacada bajo la etiqueta "SIGUIENTE" (reutilizando el tratamiento de "seleccionado" ya existente, no un estilo nuevo), separada de la lista numerada de items restantes. **`stack.py` (la máquina de estados) no se tocó**; verificado en vivo que el consumo automático (Alpha→Beta→Gamma) sigue funcionando exactamente igual, y los 30 tests de `tests/test_stack.py` siguen en verde.

### Smart Type Detection

`type_detection.py`: detección conservadora (evita falsos positivos a propósito, con 40 tests incluyendo casos negativos explícitos) de color, email, teléfono, ruta, comando de shell, JSON, código, y YouTube — sobre el `kind` que la base de datos ya guarda, sin cambiar el esquema ni reprocesar el historial existente.

## Qué se evaluó pero NO se implementó (a propósito)

Siguiendo la instrucción explícita de "proponer antes que construir" y evitar feature creep:

- **Workspaces / Smart Pinboards / Profiles**: no implementado. Propuesta: dado que Nexus ya tiene pinboards many-to-many (más flexible que el modelo de Paste, que es uno-por-item), un "Workspace" natural para Nexus sería un filtro guardado (tipo + pinboard + rango de fecha) reutilizando la infraestructura de búsqueda ya existente, no una entidad nueva de base de datos. Vale la pena solo si el uso real de pinboards crece lo suficiente para justificarlo.
- **Integración con IA (Claude/Codex/OpenCode)**: solo evaluado, no implementado, por diseño — cualquier envío de contenido del portapapeles a una IA debe ser una acción explícita del usuario sobre un item específico (p. ej. un futuro "Enviar a Claude" en el menú contextual), nunca automático. No se construyó ningún camino de red hacia una IA en esta sesión.
- **Undo de remociones del Stack**: evaluado. No implementado en esta sesión porque requiere decidir la semántica exacta (¿deshace solo la última remoción manual? ¿también un consumo por Ctrl+V real?) y esa es una decisión de producto, no una obviedad técnica — mejor candidato para un ciclo dedicado con su propio set de tests, en vez de apresurarlo.
- **Drag & drop** (card → pinboard, card → aplicación externa): no implementado. La Timeline nueva ya resuelve la reorganización principal (pinboards vía chips + teclado); drag&drop es una mejora incremental de UX de mouse, no bloqueante.
- **Previews web (OpenGraph/favicon) y thumbnail real de YouTube**: no implementados — requieren red, que Nexus hoy no toca en absoluto. La detección de tipo YouTube/URL ya existe (`type_detection.py`); falta el *fetch* asíncrono con cache/timeout/fallback, correctamente scopeado como su propio trabajo de Fase 2.x dado que toca una superficie nueva (red) que merece su propia auditoría de seguridad (SSRF, límites de tamaño) antes de escribir código.
- **Redimensionado manual / "modo compacto"** de la Timeline (como el de Paste): no implementado; la barra tiene una altura fija hoy. Candidato de pulido visual, no funcional.

## Estado de los tests

```
230/230 passing
- 30 tests:  ClipStack (Fase 1)
- 45 tests:  StorageService (Fase 1: 15 + Fase 2.5: preview cache 9 + enrich_search_text 5 + otros)
- 40 tests:  type_detection (Fase 2)
- 10 tests:  relative_time (Fase 2)
- 13 tests:  TimelineModel (Fase 2)
- 58 tests:  panel_geometry (Fase 2.5)
- 41 tests:  web_preview / SSRF (Fase 2.5)
- 8 tests:   PreviewService (Fase 2.5, incluye un end-to-end real por QThreadPool)
```

Ejecutar con: `.venv/bin/pytest tests/ -v` desde `/home/manuelcarvajal/Projects/Nexus-linux`.

## Git

- `main`: estado estable de Fase 1, tag `nexus-linux-stable-1.x`.
- `nexus-2.0`: todo el trabajo de Fase 2 y 2.5, commits atómicos por cada fix/feature con su justificación y evidencia de verificación en el mensaje.
- Checkpoints: `nexus-2.0-phase2-complete` (fin de Fase 2), **`nexus-2.0-rc1`** (release candidate actual, instalado como versión activa en Erwin vía el `pip install -e .` ya existente).
- Sin push a ningún remoto (no hay remoto configurado).

## Próximos pasos recomendados (no bloqueantes)

1. Merge de `nexus-2.0` a `main` cuando Manuel confirme que la experiencia se siente bien en uso diario real (no solo en las pruebas automatizadas de esta sesión).
2. Previews web asíncronos (OpenGraph, favicon, thumbnail de YouTube) como su propio ciclo, con la auditoría de seguridad de red que corresponde antes de escribir el primer fetch.
3. Suite de UI automatizada con `QTest` para complementar los 108 tests actuales (que cubren lógica pura, no interacción real de widgets).
4. Revisar con Manuel si el modelo many-to-many de pinboards se mantiene o se simplifica — documentado como decisión consciente pendiente en `PASTE_PRODUCT_ANALYSIS.md` §18.
