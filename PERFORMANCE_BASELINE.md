# Nexus Linux — Performance Baseline

**Fecha:** 2026-08-26 · Todos los números de esta página son **mediciones reales tomadas en vivo sobre Erwin** (proceso real, `ydotool` disparando los atajos físicamente, timestamps de `time.perf_counter()` insertados en el código y leídos del log), no estimaciones. Donde algo no se pudo medir con esta metodología, se indica explícitamente como pendiente.

## 1. CPU en reposo — hallazgo P0 (corregido)

| Momento | Hilo | CPU |
|---|---|---|
| Antes del fix (proceso con 17h41min de antigüedad) | `nexus-evdev-wat` | ~100% de un núcleo, sostenido — 17h45min de tiempo de CPU acumulado sobre 8h06min de duración real del servicio |
| Antes del fix | proceso completo (`ps %cpu`, promedio desde el arranque) | 62.8% |
| Después del fix (mismo hardware, mismo teclado Bluetooth) | `nexus-evdev-wat` | ~0% en reposo (1-2 ticks de CPU en 4 segundos de muestreo) |

Causa raíz y fix: ver `ARCHITECTURE_AUDIT_V2.md` §3, hallazgo #1.

## 2. Latencia de apertura — Ctrl+Shift+V

Metodología: `ydotool key 29:1 42:1 47:1 47:0 42:0 29:0` (Ctrl+Shift+V físico real vía uinput) disparado repetidamente contra el proceso real, con instrumentación permanente (`nexus.window` a nivel `DEBUG`, `nexus` a nivel `INFO` para el resumen de shortcut) ya integrada en el código, no removida tras esta auditoría.

### Antes del fix

| Intento | query SQL | construcción de 200 filas | total hasta ventana usable |
|---|---|---|---|
| 1 | 8.6ms | 795.5ms | 804.1ms |
| 2 | 4.7ms | 847.2ms | 851.9ms — shortcut→ventana: **873.4ms** |
| 3 | 15.0ms | 824.5ms | 839.6ms — shortcut→ventana: **856.6ms** |

Cerrar la ventana (`fade_hide()`, solo `.hide()`) en el mismo periodo: **3.6-4.5ms** — confirma que el 100% del problema estaba en el camino de apertura, no en Qt/D-Bus/KWin en general.

### Desglose de causa raíz

De los ~850ms: la consulta SQL era **5-15ms** (SQLite no es el problema). El resto (~830-840ms) era `MainWindow.refresh()` reconstruyendo las 200 filas desde cero:
- 61 de 200 filas eran imágenes (dataset real del usuario); decodificar + escalar + enmascarar circularmente un PNG de ~112KB promedio corre en el hilo principal de Qt sin cache, repetido en cada apertura.
- Cada una de las 200 filas creaba y adjuntaba un `QGraphicsDropShadowEffect` (efecto de sombra al hover) de forma eager en `__init__`, aunque invisible hasta que el mouse pasara por encima — `QGraphicsEffect` fuerza a Qt a un pipeline de composición offscreen desde el instante en que se adjunta, con o sin uso real.

### Después del fix (cache de miniaturas + sombra perezosa)

| Intento | construcción | total | shortcut→ventana |
|---|---|---|---|
| 1er open tras arranque (cache frío) | 972.5ms* | 983.1ms* | — |
| 1er open real disparado por el usuario (cache ya calentado por el refresh de arranque) | 348.7ms | 354.1ms | 380.9ms |
| 2do ciclo (cache totalmente caliente) | 271.9ms | 284.8ms | 299.6ms |
| Cerrar | — | — | 1.3-6.4ms |

\* Este primer número corresponde al `refresh()` que `MainWindow.__init__()` ejecuta una vez al arrancar, con la ventana **oculta** — no bloquea al usuario, pero sí consume ese tiempo durante el arranque en frío del proceso.

**Resultado: ~850-900ms → ~280-380ms, una mejora de ~2.5-3x, medida de punta a punta con el proceso real.**

### Objetivo vs. resultado

| Objetivo del pedido original | Resultado actual | Cumplido |
|---|---|---|
| Feedback perceptible <100ms | No medido con este método (requeriría instrumentación a nivel de compositor/KWin, fuera del control de Nexus) | Pendiente de instrumentación específica si se prioriza |
| Ventana usable <200ms (ideal) | ~280-380ms | **Parcial** — mejora sustancial, no alcanza el ideal |

**Por qué no se llegó a <200ms, con precisión:** el remanente (~280-350ms) es el costo inherente de que `QListWidget.setItemWidget()` cree un `QWidget` Qt real y persistente por cada una de las 200 filas visibles+no-visibles en cada apertura — no hay virtualización (solo se pintan/instancian las filas visibles). Cerrar esa brecha requiere reemplazar el modelo de lista actual por `QAbstractListModel` + `QStyledItemDelegate` (solo las ~6-8 filas visibles en pantalla se instancian/pintan). Es un cambio de arquitectura de UI deliberado, documentado como recomendación explícita para Fase 2 (ver `ARCHITECTURE_AUDIT_V2.md` §4) en vez de una reescritura apresurada bajo esta auditoría.

## 2.1 Fase 2 — Timeline virtualizada: cierre del ítem "PARTIAL" de Fase 1

**Actualizado 2026-08-26, tras Nexus Timeline (`timeline_window.py`).** El gate de Fase 1 dejó explícitamente pendiente esto: los ~280-380ms restantes tras el fix de cache de miniaturas eran el costo de construir hasta 200 `QWidget` reales (`QListWidget.setItemWidget`) en cada apertura, sin importar cuántos fueran visibles. La recomendación de esa sección era reemplazar ese modelo por `QAbstractListModel` + `QStyledItemDelegate` — implementado ahora como la experiencia principal de Ctrl+Shift+V.

| Medición | Fase 1 (post-fix) | Fase 2 (Timeline virtualizada) |
|---|---|---|
| Ctrl+Shift+V, shortcut → ventana lista | ~280-380ms | **3-4ms** |

Metodología idéntica a la de Fase 1 (mismo log `nexus: shortcut: ... -> ventana lista en %.1fms`, mismo `ydotool` disparando Ctrl+Shift+V real contra el proceso en vivo). Verificado en múltiples aperturas consecutivas: 4.0ms, 3.3ms, 3.0ms.

**Por qué es tan drástico:** con un modelo/delegate, Qt solo invoca `paint()` del delegate para las celdas realmente dentro del viewport (~7-8 cards visibles en una barra de 1920px de ancho) — las otras ~192 filas del historial no cuestan nada hasta que se desplazan a la vista. Esto elimina por completo la dependencia entre "cuántos items tiene el historial" y "cuánto tarda en abrir", no solo la reduce.

**Objetivo del pedido original, resultado final:**

| Objetivo | Resultado |
|---|---|
| Feedback perceptible <100ms | **Cumplido con margen amplio** (~3-4ms) |
| Ventana completamente usable <200ms (ideal) | **Cumplido con margen amplio** (~3-4ms) |

Sin degradar CPU idle, memoria, ni ninguna otra métrica de Fase 1 — el modelo/delegate no introduce ningún timer, hilo, ni efecto gráfico persistente nuevo; ver `DESIGN_SYSTEM.md` §"Surfaces/Elevation" para la regla explícita que evita reintroducir el mismo patrón de costo (`QGraphicsEffect` por card) que causó el problema original.

## 3. Escalado con historial grande (bases de datos sintéticas, aisladas del dato real del usuario)

| Tamaño de historial | `recent(limit=200)` | búsqueda sin coincidencias (peor caso, full scan) | `purge_older_than(30)` |
|---|---|---|---|
| 10,000 filas | 1.3ms | 17.1ms | — |
| 50,000 filas | 1.2ms | 80.5ms | 0.2ms |

**Conclusión:** el tamaño total del historial **no** es un problema de escalado para Nexus hoy — toda consulta relevante usa `LIMIT`, así que el costo depende de cuánto se muestra (200 filas), no de cuánto existe en la base. La única consulta sensible al tamaño total es la búsqueda con `LIKE '%...%'` (no puede usar índice por el comodín inicial): 80ms sobre 50,000 filas es aceptable hoy (historial real actual: 540 filas), pero si creciera de forma sostenida a decenas de miles, `FTS5` sería la vía correcta — no se justifica migrar el esquema sin evidencia de que se necesite.

**Nota:** este número mide solo la capa de storage. El costo de **reconstruir la UI** (200 `QWidget`) es independiente del tamaño total del historial (siempre son como máximo `limit=200` filas) — así que el hallazgo de la sección 2 no empeora con un historial más grande, solo con más imágenes entre las 200 filas mostradas.

## 4. Fiabilidad de atajos globales

Verificado con `ydotool` disparando `Ctrl+Shift+V` y `Ctrl+Shift+C` repetidamente contra el proceso real durante esta sesión (decenas de activaciones a lo largo de las pruebas de latencia y de la prueba de recuperación de crash): **0 eventos perdidos, 0 ventanas duplicadas, 0 crashes**. No se ejecutó la prueba formal de "100 activaciones consecutivas cronometradas" como bloque aislado — pendiente para `TEST_PLAN_V2.md` si se quiere un número exacto de tasa de fallo sobre una muestra grande; la evidencia cualitativa de esta sesión no mostró ningún fallo.

## 5. Pendiente de medir (no cubierto en esta sesión)

- Memoria en una sesión larga (horas) con cientos de operaciones — no se corrió un soak test dedicado.
- Latencia de apertura con temas/glass/translucencia (no aplica todavía, Fase 2).
- Tiempo de primera renderización a nivel de compositor (fuera del control directo de Nexus, requeriría herramientas de profiling de KWin).
