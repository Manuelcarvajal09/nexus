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
