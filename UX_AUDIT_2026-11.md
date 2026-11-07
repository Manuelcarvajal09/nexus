# Auditoría UX/UI de Nexus (Windows) — 2026-11-06

Punto de recuperación: tag `nexus-pre-ux-transformation-2026-11-06` (rama `windows-native`, d0ff159).
Rama de trabajo: `ux-transformation`. Todo con datos sintéticos; la instancia y base reales no se tocaron.

## Estado de partida
Las entregas E1–E5 del plan maestro (títulos, cabecera limpia, Configuración, íconos/bandeja, motion) ya estaban commiteadas.
Línea base: 976 tests OK; benchmark sintético (1000 clips): apertura mediana 14.75 ms, 82.1 MiB, CPU en reposo 0.0 s.

## Hallazgos corregidos
| ID | Flujo | Heurística | Sev. | Problema | Solución | Prueba |
|----|-------|-----------|------|----------|----------|--------|
| UX-01 | Stack › Vaciar Stack | H05, H03 | 3 | Vaciaba toda la cola pendiente con un clic, sin confirmar ni deshacer | Confirmación en línea en dos pasos ("¿Vaciar? Clic otra vez", 3 s, se desarma al ocultar; sin modal para no robar foco). Un solo elemento se vacía directo | `test_clear_stack_*` |
| UX-02 | Stack/filas › imagen sin título | H02 | 2 | Se mostraba el texto técnico `[imagen]` | Se muestra "Imagen" (`row_widget.py`, aditivo, compatible con Linux) | captura antes/después |
| QA-01 | Suite | — | 2 | `test_natural_completion_leaves_no_animation_or_effect` flaky (espera fija de 220 ms) | Sondeo con tope de 2 s | 3/3 aislado + suite |

## Resultado
978 tests OK (976 + 2 nuevos). Benchmark: apertura 15.4 ms mediana, 82.1 MiB, CPU 0.0 s (sin regresión; el máximo varía por ruido).

## Pendientes / limitaciones
- Sin verificación en pantalla real: solo render offscreen sintético y tests. Protocolo manual: Stack con ≥2 elementos → "Vaciar Stack" una vez (cambia texto) → esperar 3 s (vuelve) → dos clics (vacía).
- Solo se capturó el tema `liquid_glass`; no se revisaron los otros 4 temas ni escalas DPI en esta pasada.
- Auditoría de heurísticas por lectura de código + capturas del historial y Stack; Pinboards, Configuración y bandeja se asumieron correctos por sus tests existentes.

## Reversión
`git checkout windows-native` (o `git reset --hard nexus-pre-ux-transformation-2026-11-06` en la rama de trabajo).

---
## Segunda pasada: validación visual real (misma fecha)

Herramienta nueva: `tools/windows_design_review.py` (datos sintéticos, BD temporal, sin atajos globales ni portapapeles).
- `--real`: plataforma Qt nativa de Windows y captura de **pantalla real** (monitor 3440×1440 al 125 %, Acrylic/DWM reales).
- sin `--real`: render offscreen de widgets (solo composición; no prueba comportamiento nativo).
- `--scale 0.8/1/1.2/1.6` = 100/125/150/200 % efectivo sobre el 125 % del monitor.

### Cambios
| ID | Superficie | Problema (visto en captura real) | Cambio |
|----|-----------|----------------------------------|--------|
| UX-03 | Stack | La lista cortaba la última fila a la mitad (QSS añade ~20 px por ítem que el cálculo ignoraba) en todos los temas y escalas | Altura medida con la geometría real de las filas (`_adapt_height`) |
| UX-04 | Buscador | Decía "Buscar en tu historial…" también dentro de un pinboard | Placeholder contextual: «Buscar en «Nombre»…» |
| UX-05 | Configuración | Sin nombre propio; solo "Atajos de teclado" | Título "Configuración" junto a "Volver" |

Tests: 981 OK (+3). Benchmark sintético: apertura mediana 11.9 ms, 82.0 MiB, CPU reposo 0.0 s.

### Validado en pantalla real (capturas, no interacción)
5 temas (historial, Stack, Configuración, Pinboards) y escalas 100/125/150/200 %: sin textos cortados ni desbordes en el panel; al 200 % el historial muestra 3,5 tarjetas con scroll horizontal (esperado).
Capturas en `capturas-ux/` (ignoradas por git).

### No validado
- Interacción nativa real (foco entre apps, pegado real, Ctrl+V del Stack, atajos globales): no se ejecutó para no tocar tu portapapeles ni tu instancia; cubierto solo por tests con dobles.
- Menús contextuales nativos, notificaciones y movimiento: no capturados (el menú bloquea con `exec`); motion revisado solo por código (90–150 ms, OutCubic, reduced-motion) sin cambios.
- Resolución distinta de 3440×1440 solo vía `--screen` offscreen en la herramienta; no probado en un segundo monitor.
- La prueba de recorte del Stack no se comprobó fallando contra el código previo.

### Pendientes concretos
- Barra de pinboards visible dentro de Configuración (ruido; ocultarla exige tocar tests de geometría).
- Contadores por pinboard en el gestor; estado vacío del gestor más guiado.
- Estado vacío de búsqueda solo con texto (sin ícono).
