# Nexus Windows — arquitectura y operación

Documento canónico de la capa Windows. Los detalles de auditorías concretas se
conservan en `WINDOWS_NAVIGATION.md`, `WINDOWS_STACK_PASTE.md`,
`WINDOWS_STACK_DEEP_AUDIT.md`, `WINDOWS_COMPOSITION_AUDIT.md` y
`WINDOWS_LAYOUT_REVIEW.md`.

## 1. Propósito y estado

Nexus Windows es la implementación nativa para Windows del mismo núcleo de
History, Pinboards y Stack que usa Nexus en Linux. La lógica compartida
(`timeline_window.py`, `storage.py`, `pinboard_sidebar.py`, `stack.py`,
`stack_window.py`, `preview_service.py`) no se duplica ni se modifica para
Windows: la capa Windows sustituye la presentación, el host nativo, la captura,
los atajos y el ciclo de vida de Stack.

Estado estable de esta base (2026-10-24):

- Rama de trabajo: `windows-native`.
- Suite completa: **614 passed**.
- Instancia habitual validada con PID **12304** en la última prueba de
  navegación; no se reinició para la corrección final del chip.
- Los datos de usuario permanecen en
  `%LOCALAPPDATA%\Nexus\data\nexus.sqlite`.
- Linux, SQLite y los motores de History, Stack y Pinboards no se modificaron
  para esta capa.

## 2. Arquitectura

```text
Nexus Windows
├─ windows_app.py            arranque, instancia única, bandeja, atajos,
│                            retención, gate de captura, composición raíz
├─ windows_timeline.py       panel, Historial, navegación y administrador
├─ windows_design.py         tokens, paletas, QSS, iconos, cinco temas
├─ windows_material.py       superficie/material y upgrade_surface
├─ windows_backdrop.py       host nativo, DWM/Acrylic, borde y fallback
├─ windows_motion.py         movimiento y foco de búsqueda
├─ windows_card_delegate.py  tarjetas de History
├─ windows_stack.py          motor/ciclo de vida/ventana de Stack
├─ windows_clipboard.py      monitor de portapapeles
├─ windows_keyboard.py       hook WH_KEYBOARD_LL y solicitudes de pegado
├─ windows_paste.py          servicio de pegado y watcher
├─ windows_platform.py       APIs Win32 (hotkeys, foco, DWM, clipboard)
└─ windows_settings.py       defaults y validación de atajos
```

Reglas de separación:

- `storage.py` es la fuente de verdad; la UI nunca mantiene datos de usuario
  fuera de SQLite salvo la cola transitoria de Stack.
- History captura siempre que el monitor esté activo.
- Stack solo acepta capturas durante una sesión explícita.
- Pinboards son vistas/relaciones; no copian clips.
- La composición DWM no cambia la lógica de datos ni los motores.

## 3. History

History es la lista persistente de elementos capturados (`clip_items`). La
captura la realiza `WindowsClipboardMonitor` y se apoya en el monitor compartido:

- Formatos soportados: texto/URL Unicode, imágenes PNG y un archivo local como
  URL. No se capturan formatos arbitrarios ni selecciones múltiples que el
  esquema no represente.
- Cada elemento tiene ID, hash, tipo, contenido, app de origen, timestamps y
  metadatos de búsqueda.
- La retención efectiva mínima es de **30 días** (`effective_retention_days`);
  el valor configurado nunca puede reducirla por debajo de 30.
- La UI muestra hasta 200 elementos y ofrece búsqueda incremental.
- La tarjeta muestra tipo, app de origen, antigüedad y vista previa cuando
  corresponde.
- Pegar desde History no elimina el elemento. Stack consume su propia cola;
  History conserva el original.

La captura y el pegado están separados: el monitor nunca escribe en el
portapapeles; las escrituras de Nexus se identifican por ownership y no se
recapturan.

## 4. Pinboards

Un Pinboard es una etiqueta persistente (`pinboards`) y una relación
muchos-a-muchos con clips (`pinboard_items`):

```sql
CREATE TABLE pinboard_items (
    pinboard_id INTEGER NOT NULL REFERENCES pinboards(id) ON DELETE CASCADE,
    item_id INTEGER NOT NULL REFERENCES clip_items(id) ON DELETE CASCADE,
    PRIMARY KEY (pinboard_id, item_id)
)
```

Consecuencias importantes:

- Crear un Pinboard **no** copia ni mueve clips; solo crea la etiqueta.
- Añadir un clip crea una fila en `pinboard_items`; no duplica el clip.
- Eliminar un Pinboard borra sus relaciones, no los clips de History.
- Eliminar un clip borra sus relaciones por cascada.
- `pinboard_items = 0` significa que ningún clip está asignado a ningún
  Pinboard. Es un estado vacío legítimo, no corrupción ni pérdida.

Estado auditado el 2026-10-24:

| Pinboard | ID | Color | Emoji | Orden |
| --- | ---: | --- | --- | ---: |
| Mercadolibre | 1 | `#FFCC00` | ⭐ | 0 |
| Prompts | 2 | `#FF2D55` | 📌 | 1 |

En la auditoría, `pinboards = 2` y `pinboard_items = 0`; el respaldo previo al
reinicio también tenía `pinboard_items = 0`. Los clips de History son
independientes y están intactos.

## 5. Stack

Stack es una cola persistente de clips para pegar de forma secuencial. No es
una segunda History: sus elementos se consumen al pegarlos y se persisten en
`stack_state`.

### Activación y estados

- Al arrancar, Stack está **inactivo**; puede mostrar pendientes recuperados,
  pero no captura ni intercepta Ctrl+V.
- `Ctrl+Shift+C` muestra y activa/reanuda Stack. Es la única ruta que habilita
  la captura de una sesión nueva.
- Pausar, cerrar/Esc o volver a pulsar el atajo desactiva la captura y la
  secuencia sin borrar pendientes.
- Ocultar por un pegado manual conserva la sesión; la bandeja indica
  “Stack activo · oculto”.
- Activo pero vacío: Ctrl+V se bloquea para no repetir el último payload.
- Un fallo del hook o de SQLite detiene Stack y se comunica en la UI; no se
  reintenta silenciosamente.

### FIFO y LIFO

La cola se puede mostrar en dos órdenes:

- **FIFO**: el primero copiado se pega primero.
- **LIFO**: el último copiado se pega primero.

El control `FIFO/LIFO` de la ventana alterna el orden y lo persiste en
`stack_meta`; el valor recuperado se aplica al arrancar. La selección de la
cabeza se hace en el momento de cada solicitud, no con un `available` antiguo.
Consumir Stack elimina el ID exacto de `stack_state` y de la memoria, pero
conserva el clip en History y sus relaciones de Pinboards.

### Altura adaptable

`WindowsStackWindow._adapt_height()` calcula la altura con las filas visibles:

- Hasta 6 filas visibles.
- Altura mínima 280 px.
- Alto máximo limitado por el área de trabajo menos 80 px.
- Si hay más de 6 filas, aparece scroll vertical.
- La ventana re-centra dentro del área de trabajo al cambiar de tamaño.

## 6. Captura y pegado

Flujo resumido:

```text
Copia externa
  → QClipboard.dataChanged (hilo GUI)
  → snapshot/version
  → SQLite History
  → señal versionada
  → gate de sesión
  → Stack.add si la sesión está activa y aceptando capturas

Ctrl+V con Stack activo
  → WH_KEYBOARD_LL bloquea V-down/V-up externos
  → PasteRequest(event_id, foco, pressed_at)
  → cola Qt acotada (16 pendientes + una en ejecución)
  → selección FIFO/LIFO en ese momento
  → escribir payload tipado y verificar marcador
  → SendInput marcado NEXUS_INPUT_MARKER
  → el hook ignora el evento propio
  → comprobar foco/versión/ventana de estabilización de 180 ms
  → consumir el ID exacto y refrescar la misma cola
```

`INPUT_ACCEPTED` significa que Windows aceptó los eventos, no que la aplicación
destino haya insertado el contenido. La política automática exige además
payload verificado, foco exacto, misma sesión/generación y estabilidad hasta el
control posterior. Si falla, el elemento permanece en Stack.

Limitaciones del sistema: UIPI puede bloquear la inyección hacia procesos de
mayor integridad; un control puede ignorar Ctrl+V o leer de forma asíncrona; el
original permanece en History como recuperación.

## 7. Atajos

| Atajo | Acción |
| --- | --- |
| `Ctrl+Shift+V` | Mostrar/ocultar el panel principal |
| `Ctrl+Shift+C` | Mostrar/activar o cerrar Stack |
| `Ctrl+F` | Buscar en History |
| `Enter` | Pegar el elemento seleccionado de History |
| `Ctrl+V` | Con Stack activo, pegar el siguiente elemento de su cola |
| `Esc` | Cerrar/volver al historial |
| Clic derecho | Menú de acciones/eliminación según contexto |

Los atajos globales se validan: solo se aceptan `Ctrl+Shift+<tecla>` y no
pueden coincidir entre panel y Stack. Si el registro falla, la aplicación lo
informa y no continúa con un estado ambiguo.

## 8. Composición DWM y temas

La capa Windows solicita **Desktop Acrylic** para los cinco temas y deja que
DWM posea el contorno exterior. La superficie Qt pinta el contenido y el
material de respaldo; no se crea un segundo HWND ni se recorta el contorno con
`SetWindowRgn`.

- Una sola silueta exterior; sin marco Qt interior duplicado.
- El fallback de diagnóstico es `NEXUS_SINGLE_CONTOUR=0`.
- El perfil nativo se valida contra la build/Qt esperados; otras combinaciones
  usan el fallback de satén en lugar de fingir una captura no verificada.
- La inactividad puede caer a una superficie opaca/gris según el comportamiento
  de DWM observado; no se afirma Acrylic activo en inactivo. Es una limitación
  conocida, no un fallo de la navegación.

Temas disponibles:

| Clave | Nombre visible | Carácter |
| --- | --- | --- |
| `liquid_glass` | Liquid Glass | Translúcido, azul frío, radios amplios |
| `midnight_teal` | Midnight Teal | Verde azulado profundo, contraste suave |
| `minimalist` | Minimalista | Neutro oscuro, acentos contenidos |
| `storem` | Storem | Cálido, dorado, lectura sobria |
| `tech_neon` | Tech Neon | Cian/magenta, alto contraste |

El tema activo se guarda en `%LOCALAPPDATA%\Nexus\config\theme.json`. Cambiar
de tema no recrea el HWND ni altera datos.

## 9. Navegación simplificada

La barra Windows muestra exclusivamente:

```text
Historial · ⭐ Mercadolibre · 📌 Prompts · ⋯
```

- Los chips de Pinboards personalizados muestran **solo el emoji del nombre y
  el texto**, sin un icono genérico duplicado.
- El ancho reserva el texto en negrita seleccionada; `Mercadolibre` y `Prompts`
  se ven completos en los cinco temas.
- `⋯` abre `Administrar Pinboards`; no crea una pestaña permanente.
- El administrador conserva `+ Nuevo`, edición, color/emoji, orden y
  eliminación.
- El footer no repite el título de la vista; conserva los atajos contextuales.
- La base compartida conserva su comportamiento para Linux.

### Altura adaptable del panel

`WindowsTimelineWindow._apply_geometry()` calcula una sola vez la posición y el
tamaño en píxeles lógicos:

- Ancho objetivo 950 px, con mínimo 760 px; 744 px en pantallas verticales.
- Altura de tarjeta 184/160/148 px según el área de trabajo.
- El administrador reserva 440 px de viewport; el detalle tiene scroll si la
  pantalla es corta.
- Márgenes de trabajo laterales mínimos y 24 px inferiores.
- El número de columnas se ajusta al ancho útil; no se desperdicia una columna
  completa por unos pocos píxeles.

## 10. Respaldos y recuperación

### Datos

- SQLite: `%LOCALAPPDATA%\Nexus\data\nexus.sqlite`.
- La copia de seguridad se hace con la API de backup de SQLite, no copiando el
  archivo abierto.
- No se debe restaurar una base antigua solo por un cambio visual: perdería
  clips y estados de Stack creados después.
- Ante corrupción confirmada: cerrar Nexus, guardar una copia del SQLite actual
  y restaurar el respaldo elegido; verificar `PRAGMA integrity_check`.

### Navegación/código

Para revertir solo la capa de navegación:

1. Cerrar Nexus de forma ordenada.
2. Restaurar `windows_timeline.py`, `windows_design.py` y
   `tests/test_windows_panel.py` desde el respaldo correspondiente.
3. Verificar SHA-256 del manifiesto.
4. Arrancar con `run-nexus-windows.cmd` y comprobar la barra y los Pinboards.

### Respaldos recientes

- Navegación: `nexus-nav-backup-20261024T145338Z`.
- SQLite previo al reinicio: `nexus-nav-restart-20261024T150836Z/user-before.sqlite`.
- Auditorías de layout/composición/Stack: directorios `nexus-layout-*`,
  `nexus-composition-*`, `nexus-deep-audit-*` en el temp aprobado.

Los respaldos y capturas no se versionan en Git.

## 11. Funcionalidades estables y experimentos descartados

### Estable

- History con captura, búsqueda, retención y tarjetas.
- Pinboards con relaciones, administrador y edición.
- Stack con activación explícita, persistencia, FIFO/LIFO y pegado secuencial.
- Captura/pegado con hook dedicado y marcador de input propio.
- Atajos globales y bandeja.
- Cinco temas y navegación simplificada.
- Composición DWM de una sola silueta con fallback.
- Altura adaptable de panel y Stack.

### Experimental / descartado

- **Windows App SDK + `DesktopAcrylicController.SetTarget`**: el bridge y el
  clon pasaron pruebas, pero en Nexus real la composición reemplazó el
  contenido Qt (0 muestras de contenido tras el attach). **Rechazado**; no se
  integra en producción.
- **Dos HWND / puente multi-HWND**: se probó la viabilidad y se rechazó como
  arquitectura de producto por complejidad, ciclo de vida y riesgo de
  activación/composición. No está integrado.
- **`SetWindowRgn`**: produjo material gris e artefactos inactivos; **rechazado**.
- **Acrylic siempre activo**: no se resolvió con las rutas DWM probadas; queda
  como limitación, no como funcionalidad prometida.
- **Aplicar `SetWindowCompositionAttribute` legacy**: no mostró Acrylic
  dependiente del fondo en la prueba; no es la ruta de producción.

## 12. Verificación

- Suite completa: **614 passed**.
- Navegación: barra y administrador verificados en la instancia habitual.
- Chip corregido: probado en los cinco temas con instancia aislada; sin icono
  genérico, texto completo y un solo contorno.
- Datos: `PRAGMA integrity_check = ok`; IDs de History contiguos; Pinboards 1 y
  2 intactos.
- Logs: sin errores desde el reinicio de validación.
- No se hace push ni se reescribe historial; la consolidación Git se documenta
  en `GIT_CONSOLIDATION_PROPOSAL.md`.

## 13. Archivos clave

- `src/nexus_linux/windows_app.py`
- `src/nexus_linux/windows_timeline.py`
- `src/nexus_linux/windows_design.py`
- `src/nexus_linux/windows_stack.py`
- `src/nexus_linux/windows_keyboard.py`
- `src/nexus_linux/windows_paste.py`
- `src/nexus_linux/windows_clipboard.py`
- `src/nexus_linux/windows_platform.py`
- `src/nexus_linux/windows_backdrop.py`
- `src/nexus_linux/windows_material.py`
- `src/nexus_linux/windows_motion.py`
- `src/nexus_linux/windows_card_delegate.py`
- `tests/test_windows_panel.py`
- `tests/test_windows_stack_safety.py`
- `tests/test_windows_stack_transactions.py`
