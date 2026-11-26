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
Capturas en `capturas-ux/` (sin commitear). Las capturas con fondo real se descartaron: el Acrylic y los márgenes mostraban ventanas privadas del escritorio; las vigentes son renders offscreen/widget.

### No validado
- Interacción nativa real (foco entre apps, pegado real, Ctrl+V del Stack, atajos globales): no se ejecutó para no tocar tu portapapeles ni tu instancia; cubierto solo por tests con dobles.
- Menús contextuales nativos, notificaciones y movimiento: no capturados (el menú bloquea con `exec`); motion revisado solo por código (90–150 ms, OutCubic, reduced-motion) sin cambios.
- Resolución distinta de 3440×1440 solo vía `--screen` offscreen en la herramienta; no probado en un segundo monitor.
- La prueba de recorte del Stack no se comprobó fallando contra el código previo.

### Pendientes concretos
- Barra de pinboards visible dentro de Configuración (ruido; ocultarla exige tocar tests de geometría).
- Contadores por pinboard en el gestor; estado vacío del gestor más guiado.
- Estado vacío de búsqueda solo con texto (sin ícono).


---
## Tercera pasada: fase de producto (2026-11-06)

| ID | Superficie / heurística | Problema | Cambio |
|----|------------------------|----------|--------|
| UX-06 | Configuración · H08 | Mostraba la barra de pinboards (irrelevante) | Se oculta; la ventana conserva su tamaño y la página gana esa altura |
| UX-07 | Gestor de Pinboards · H06 | No decía cuánto contiene cada pinboard | Contador por pinboard («Nombre · N», tooltip) y resumen en el panel derecho |
| UX-08 | Gestor vacío · H10 | Sin pinboards, solo una frase | Texto de orientación + botón «+ Nuevo pinboard» |
| UX-09 | Estados vacíos · H10/H06 | Solo texto (icono pendiente del informe anterior = icono en el estado vacío de la búsqueda) | Icono + una acción: «Borrar búsqueda» / «Ir al historial»; texto del pinboard vacío explica cómo llenarlo |
| UX-10 | Eliminar · H03/H05 | Borrar un clip era irreversible | `Supr` borra la tarjeta seleccionada; aviso «Eliminado · Deshacer» (6 s) y `Ctrl+Z`; `storage.restore_item` restaura contenido, título, fechas y pinboards vigentes |
| UX-11 | Menú de tarjeta · H06/H07 | Atajos frecuentes invisibles | «Pegar ⇥ Enter», «Texto plano ⇥ Mayús+Enter», «Eliminar ⇥ Supr» |
| UX-12 | Motion · criterio Emil | Fade al cambiar de pinboard con Ctrl+←/→ (uso repetido por teclado) | Sin animación por teclado; clics conservan los 90 ms |
| UX-13 | Foco · H03 (hallazgo de la validación nativa) | Al cerrar con Esc/atajo/× el foco dependía del orden Z de Windows: 1 de 5 ejecuciones reales terminó en otra app (`warp.exe`) | Nexus recuerda la app previa al abrir y vuelve a ella al cerrar (salvo que otra ventana de Nexus, p. ej. el Stack, tome el foco) |

Componentes conservados (y por qué): tarjetas (jerarquía tipo → título/contenido → app/tiempo, selección con borde acento y reflejo de vidrio coherente en 5 temas), cabecera, Stack, tiempos de motion (90–150 ms, OutQuint, movimiento reducido soportado).

### Validación nativa aislada (`tools/windows_native_e2e.py`)
Escritorio real, proceso Nexus en sandbox (temp LOCALAPPDATA, mutex y atajos propios Ctrl+Shift+9/7, captura pausada, sin hook), ventana Tk como app destino, entrada real con `keybd_event`/`mouse_event`:
hotkey abre el panel · el foco pasa a Nexus · clic derecho abre el menú nativo · Esc cierra menú, luego panel · el foco vuelve a la app destino · el hotkey reabre · hotkey del Stack abre/cierra · reinicio del proceso: el hotkey sigue funcionando y los datos persisten (6 clips, 1 pinboard, 1 título) · el Nexus personal conserva los mismos PID.
Resultado: tras la corrección de foco, 18/18 en las ejecuciones limpias; en 14 ejecuciones posteriores 3 tuvieron un fallo intermitente de la propia prueba (espera del menú; una no se diagnosticó) y el foco volvió a la app destino en todas.

**No ejecutado a propósito (usa tu portapapeles real):** captura real de copias, pegado en otra app, Ctrl+V del Stack. Propuesta segura, a falta de tu autorización: guardar el portapapeles de texto actual, ejecutar la prueba con textos sintéticos en el sandbox y restaurarlo; si tienes imágenes/archivos en el portapapeles, no se pueden restaurar con garantías.

### Pruebas y rendimiento
995 tests OK (+14). Benchmark sintético: apertura mediana 12.3 ms, 81.6 MiB, CPU reposo ≈0.

---
## Cuarta pasada: estabilización y calidad verificable (2026-11-06)

### Fallos intermitentes: causas
| Síntoma | Causa identificada | Evidencia | Corrección |
|---|---|---|---|
| E2E: foco acababa en `warp.exe` al cerrar con Esc (1 de 5) | **Defecto real**: Nexus no recordaba la app previa; el foco dependía del orden Z de Windows | 6/6 y luego 20/20 con la corrección, foco de vuelta en <0,09 s | `_return_target` + `_return_focus` (ya commiteado antes) |
| E2E: menú no abierto / Esc no cerraba el panel / foco en otra app (≈4 de 50 ejecuciones) | **Mecanismo de automatización**: otra app (`warp.exe`, `Ueli.exe`) toma el foco durante la prueba; el menú se cierra al perder la activación y las teclas Esc iban a esa otra app | Traza de la ejecución 17: menú abierto con foco Nexus, 1,1 s después foco = `warp.exe` sin acción de Nexus | La prueba ya no envía teclas si el foco no es de Nexus ni de la ventana de prueba y clasifica la ejecución como **INCONCLUSIVA por interferencia externa** |
| Test unitario `test_sendinput_failure_…` falló 1 vez en ≈55 ejecuciones | **Dependencia del entorno**: lee el estado físico del teclado (`GetAsyncKeyState`) y el contador real del portapapeles; con Mayús/Alt/Win/V pulsadas el reintento recursa | Reproducido de forma determinista: `RecursionError` antes, pasa después | `tests/conftest.py`: fixture autouse que fija ambos a un estado constante (los tests que los necesitan los parchean y su parche prevalece) |

No se aumentaron tiempos de espera: las esperas fijas de la prueba nativa se sustituyeron por esperas condicionales (foco, rectángulo estable, ventanas presentes) y se añadió traza de cambios de estado (`e2e-results.json`).
Latencias medidas en 20 ejecuciones: menú tras clic derecho 0,05–0,12 s; foco en Nexus tras el atajo 0,19–0,22 s; foco de vuelta a la app previa tras Esc 0,065–0,082 s; rectángulo de la ventana estable (1 valor distinto).

### Repeticiones de la prueba nativa (código final)
| Lote | Ejecuciones | Resultado |
|---|---|---|
| Con trazas, tras el arreglo de foco | 20 | 20 limpias |
| Tras contadores y fixture | 20 | 19 limpias, 1 fallo real de foco (`warp.exe`, anterior a la guarda) |
| Con guarda + esperas condicionales | 30 | 29 limpias, 1 inconclusa por interferencia externa (`Ueli.exe`) |
No se detectó ningún fallo atribuible a Nexus tras la corrección de foco.

### Contadores de Pinboards
Antes: texto «Nombre · N» dentro del propio texto, la elipsis cortaba el número con nombres largos.
Ahora: columna reservada con **píldora** a la derecha (`_BoardRowDelegate`); solo el nombre se recorta, el contador nunca; tooltip con nombre completo + cantidad. Pruebas: 8 nombres extremos (muy largo, acentos, CJK, emojis, 200 caracteres, HTML, 1 carácter) × 4 contadores (0–99999) × 4 escalas × 4 anchos, más pintado real del contador en 4 escalas. Se intentó ensanchar la barra (184 px) y se revirtió: rompía la geometría portrait estrecha (scroll horizontal).

### Validación final
* 1015 tests OK (+20), repetidos 7 veces seguidas sin fallos tras el fixture.
* Auditoría de layout en plataforma Windows real, 5 temas × 4 escalas (100/125/150/200 %), 6 superficies (historial, pinboard, gestor, gestor con nombres extremos, Configuración, Stack): 120 combinaciones, **0 problemas** (ventana dentro de pantalla, sin desbordes, sin texto recortado, sin scroll horizontal). La auditoría no se probó con un fallo sembrado.
* Rendimiento (3 mediciones): apertura mediana 10,1–10,9 ms (línea previa 11,9–15,4), 81,9–82,4 MiB, CPU reposo 0.
* Portapapeles real: contador de secuencia sin cambios durante la suite (3142 → 3142; reposo y suite completa: 0 cambios en muestreo de 50 ms).

### Pendiente
Pruebas de captura real, pegado entre apps y Ctrl+V del Stack: ver `CLIPBOARD_TEST_ISOLATION.md` (requiere Windows Sandbox o VM con portapapeles desactivado; no disponible hoy).

---
## Quinta pasada: auditoría integral (2026-11-26) — rama `audit/fiabilidad-ux-2026-11`

Motivo: «a veces funciona y a veces no». Evidencia: log real (3427 líneas, 499 pegados `INPUT_ACCEPTED`, 17 `target.reject`), lectura de código y capturas de los 5 temas.
Instalada hoy: `v3-d0ff159` (25-oct), un mes por detrás de esta rama.

### Fiabilidad (causas del «a veces»)
| Sev | Problema | Arreglo |
|---|---|---|
| 4 | Tras Win+L, UAC o Ctrl+Alt+Supr Windows no entrega la tecla soltada al hook: un Win/Alt quedaba «pulsado» para siempre y el Stack dejaba de interceptar Ctrl+V hasta reiniciar | `modifier_down`: el evento grabado solo manda 250 ms; luego, el estado real de la tecla |
| 4 | Windows retira en silencio un hook lento (`LowLevelHooksTimeout`) y nunca se reinstalaba | Reinstalación cada 15 s (nuevo antes de quitar el viejo, nunca a mitad de un acorde) |
| 3 | Ctrl+V con el Stack activo y vacío se «tragaba»: no pasaba nada | Se reenvía un Ctrl+V nativo (las pulsaciones encoladas tras vaciarse no se repiten) |
| 3 | Abrir Nexus desde la bandeja recordaba la barra de tareas como destino: el pegado iba ahí | Se ignoran barra de tareas, desbordamiento y escritorio; se conserva la última app real |
| 3 | `on_paste_failed` nunca se conectaba: los pegados fallidos eran silenciosos | Aviso en la bandeja con qué pasó y qué hacer (sin cancelaciones, máx. 1 cada 8 s) |
| 2 | Copias perdidas si otra app tenía el portapapeles abierto >370 ms; una escritura propia cancelaba el reintento pendiente | Reintentos hasta ~1,6 s, `capture.dropped` en el log, la escritura propia ya no cancela |
| 2 | Log sin hora ni límite | Marca de tiempo y rotación (1 MB + 1 copia) |

### Seguridad
| Sev | Problema | Arreglo |
|---|---|---|
| 3 | Solo se respetaba `ExcludeClipboardContentFromMonitorProcessing`; contraseñas marcadas con `CanIncludeInClipboardHistory=0` se guardaban | También `Clipboard Viewer Ignore`, `CanIncludeInClipboardHistory=0`, `CanUploadToCloudClipboard=0` |
| 3 | Cada URL copiada recibía un GET: enlaces de un solo uso (reset, magic login, firmados) podían consumirse | Enlaces con token/código/firma/credenciales o rutas de login/reset: tarjeta solo con dominio, sin red |
| 2 | SSRF: 100.64.0.0/10 (Tailscale/CGNAT) y IPv4 mapeadas pasaban el filtro | Solo direcciones globales |
| 2 | Errores de red no capturados → reintento en cada refresh; sin tope total; https→http permitido; imágenes de cualquier formato | `PreviewError`, tope de 6 s, redirect https→http rechazado, solo png/jpeg/gif/webp/ico |
| 2 | Lo borrado seguía en páginas libres (base real: 187 MB, 79 MB libres) | `secure_delete=ON` y `VACUUM` automático cuando lo libre supera 32 MB y 40 % |

### Rendimiento
- `type_detection.detect()` (con `json.loads`) se ejecutaba en cada pintado de cada tarjeta → caché.
- Fuentes creadas ~5 veces por tarjeta y fotograma → caché de `QFont`.
- Temporizador de la bandeja a 250 ms para siempre → 2 s y solo actualiza si cambia algo.

### UX (Cimientos de UX / Nielsen)
| Sev | Heurística | Problema | Arreglo |
|---|---|---|---|
| 2 | H01/H06 | Con 5+ clips, las tarjetas fuera del borde eran invisibles sin pista (sin barra a ≥900 px) | Chevrons «Ver más antiguos/recientes» en los bordes, solo cuando hay más |
| 2 | H02 | «FIFO/LIFO» en el Stack | «En orden» / «Inverso», con explicación en el tooltip |
| 2 | Accesibilidad | Texto de error en Liquid Glass claro: contraste 2,2:1 | `#B42318` en claro (≥4,5:1); test ampliado al tema claro |
| 1 | H06/H10 | El número 1–9 de cada tarjeta no decía para qué sirve | Tooltip «Pegado rápido · Ctrl+N» con el atajo real |
| 1 | H09 | Mensajes de fallo técnicos («recuperar de forma segura el foco destino») | Dicen qué pasó y cómo seguir (el contenido queda copiado: Ctrl+V) |

Contrastes medidos (texto/panel): todos los temas ≥ 5:1 en texto, atenuado y acento.

### No hecho / pendiente
- `recent()` sigue cargando los blobs de imagen (hoy 1,9 MB en las 200 recientes: impacto bajo); mover a miniaturas persistidas si crece.
- Búsqueda con `LIKE '%q%'` sin FTS; codificación PNG en el hilo de UI.
- Apps elevadas (UIPI): ahora el mensaje lo explica, pero no se detecta antes de enviar.
- Validación nativa real (pegado entre apps, Win+L con el Stack activo): no ejecutada; solo tests con dobles y capturas offscreen.
