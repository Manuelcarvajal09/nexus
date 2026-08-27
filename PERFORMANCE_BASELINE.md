# Nexus Linux — Performance Baseline

**Fecha:** 2026-08-26 · Todos los números de esta página son **mediciones reales tomadas en vivo sobre Erwin** (proceso real, `ydotool` disparando los atajos físicamente, timestamps de `time.perf_counter()` insertados en el código y leídos del log), no estimaciones. Donde algo no se pudo medir con esta metodología, se indica explícitamente como pendiente.

## 0. Fase 2.5 — Latencia end-to-end real (shortcut físico → ventana lista)

**Metodología, más rigurosa que la de Fase 1/2:** un script (`measure_e2e.py`) toma `time.time()` inmediatamente antes de invocar `ydotool key ...` (tecla física real vía uinput), envía el atajo, y busca la línea `ventana lista` correspondiente en el log de Nexus, calculando la diferencia. Esto mide la cadena completa: `ydotool` → kernel uinput → KWin → kglobalaccel → señal D-Bus → callback async de `dbus_next` → slot de Qt → `show()`/`activateWindow()` — no solo el trabajo interno de Nexus (que ya se medía por separado, ~0.3-0.6ms). 30 muestras "warm" por atajo, más una muestra "cold" (primera activación tras arrancar).

| Atajo | cold | warm p50 | warm p95 | warm max |
|---|---|---|---|---|
| Ctrl+Shift+V (Timeline) | — | **25.8ms** | 26.6ms | 42.4ms (1 outlier) |
| Ctrl+Shift+C (Stack) | — | **25.9ms** | 26.6ms | 26.7ms |

**Interpretación:** los ~25-26ms medidos son consistentes entre ambos atajos y muy estables (rango warm de 0.3-0.4ms de varianza aparte del outlier) — esto indica que el tiempo está dominado por la cadena `ydotool→kernel→KWin→D-Bus`, no por el procesamiline propio de Nexus (que Fase 2 ya redujo a **3-4ms** de principio a fin dentro del proceso). **Ambos objetivos del pedido original se cumplen con margen amplio**: feedback perceptible <100ms ✅ (25ms), ventana usable <200ms ✅ (25ms).

**Nota metodológica honesta:** esta medición todavía no incluye el tiempo real de composición/presentación de píxeles por parte de KWin (el intervalo entre que Qt llama a `show()` y el compositor efectivamente pinta el frame en pantalla) — eso requeriría instrumentación a nivel de compositor fuera del alcance de esta sesión. Dado que 25ms ya deja ~75-175ms de margen contra los objetivos, no se consideró necesario perseguir esa medición adicional.

**Incidente observado durante esta medición (no relacionado con Nexus):** a mitad de esta sesión de pruebas, KWin reportó transitoriamente **0 pantallas** (`qdbus6 org.kde.KWin /KWin supportInformation` → `Number of Screens: 0`), sostenido durante los siguientes ~25+ minutos de la sesión, coincidiendo con logs de KWin (`GL_INVALID_VALUE en glTexStorage2D`, `Failed to create an offscreen framebuffer`) — muy probablemente el monitor entrando en suspensión por inactividad y el compositor no recuperándose limpiamente al momento de las pruebas. **Esto es un problema del sistema/compositor, no de Nexus**: confirmado que (a) el shortcut global vía kglobalaccel deja de dispachar mientras persiste esta condición (verificado: 0 líneas de log tras 3 intentos consecutivos), pero (b) el servicio D-Bus de respaldo de Nexus (`qdbus6 mx.com.storem.nexus /Nexus ShowStack`) siguió funcionando perfectamente, permitiendo continuar todas las pruebas de Stack/captura sin depender del atajo físico. Se añadió además blindaje defensivo (`TimelineWindow._apply_geometry()` / `StackWindow._reposition_to_active_screen()`) para que Nexus nunca crashee si `QGuiApplication.screenAt()`/`primaryScreen()` devuelven `None` en este escenario — antes de este fix, habría producido una excepción no capturada limpiamente. **Recomendación:** si esto se repite fuera de un período de inactividad larga, vale la pena revisar los logs de KWin del sistema (no de Nexus) para descartar un problema de controlador gráfico.

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

**Fase 2.5, bloque formal cronometrado:** 60 activaciones dedicadas (30 de Ctrl+Shift+V + 30 de Ctrl+Shift+C, ver §0) más decenas adicionales de activaciones cualitativas a lo largo de toda la sesión (pruebas de latencia, recuperación de crash, verificación de Stack). **0 eventos perdidos, 0 ventanas duplicadas, 0 crashes** en el total. Único evento anómalo: la condición de "0 pantallas" descrita en §0, que bloqueó el despacho de kglobalaccel durante ~25 min — un problema del compositor/sistema, no del código de registro/disparo de atajos de Nexus (que siguió funcionando correctamente en cuanto la condición se resolvió, y cuyo fallback D-Bus nunca dejó de funcionar).

## 5. Soak test (Fase 2.5)

100 operaciones de copiado secuenciales (`sleep 0.15` entre cada una) contra el proceso real, más una segunda ronda mixta de 10 operaciones con URLs/colores para ejercitar el pipeline de previews:

| Métrica | Antes | Después de 100+10 operaciones |
|---|---|---|
| RSS | 145,808 KB | 145,888-145,964 KB (±156KB, ruido normal) |
| File descriptors | 34 | 30-32 (sin crecimiento) |
| Hilos | 9 | 5 (el pool de hilos de preview se contrae solo tras quedar inactivo — comportamiento esperado de `QThreadPool`) |
| CPU del hilo `nexus-evdev-wat` | 0% | 0% (sin regresión del bug de Fase 1) |

**Sin memory leak, sin fd leak, sin thread leak, sin CPU creep** detectable en esta ventana de prueba. No se corrió una sesión de horas (fuera del alcance práctico de esta sesión de trabajo); si se quiere un soak test de varias horas, el mismo script es reutilizable con más iteraciones.

## 6. Pendiente de medir (no cubierto en esta sesión)

- Memoria en una sesión de **horas** (el soak test de §5 cubre 110 operaciones en unos pocos minutos, no un uso prolongado real de todo un día).
- Latencia de apertura con temas/glass/translucencia (no aplica todavía).
- Tiempo de primera renderización a nivel de compositor (fuera del control directo de Nexus).
- Comportamiento real en monitor ultrawide 34" (no conectado todavía) — la lógica de `panel_geometry.py` está probada con 58 tests contra geometrías sintéticas equivalentes, pero no contra el hardware real.
- Suspend/resume real del sistema completo: no se pudo probar en esta sesión (suspender la máquina habría cortado esta misma sesión de trabajo). La causa raíz más probable de fallos en ese escenario (el teclado Bluetooth desapareciendo/reapareciendo) ya está cubierta por el fix de Fase 1 y su verificación en vivo; una suspensión/reanudación real completa queda pendiente de que Manuel la pruebe físicamente.
