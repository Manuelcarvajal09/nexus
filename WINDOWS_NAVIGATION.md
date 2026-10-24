# Nexus Windows — navegación simplificada

## Arquitectura

La barra se reconstruye en `WindowsTimelineWindow._rebuild_pinboard_chips()`.

- La base compartida sigue creando `Historial`, los Pinboards reales y un
  `+ Pinboard` heredado.
- La capa Windows elimina de la barra `+ Pinboard` y la píldora genérica
  `Pinboards`.
- Conserva `Historial` y todos los Pinboards reales como pestañas.
- Añade al final un `QToolButton` discreto `⋯` con menú:
  - `Administrar Pinboards`.

No se crea ninguna pestaña permanente nueva. El acceso secundario sigue
emitiendo `manage_pinboards_requested`, que abre el administrador existente.
Dentro del administrador se mantienen `+ Nuevo`, edición, color/emoji, orden y
eliminación.

El footer deja de mostrar `Historial`/`Pinboards` porque duplicaba la
navegación; se conservan los atajos contextuales.

## Temas y responsive

`QToolButton#navigationOptions` tiene estilo propio en
`windows_design.qss()` para los cinco temas, con hover/focus/checked discretos.
En History, el scroll horizontal solo aparece si el ancho es inferior a 900 px;
en tamaños normales las tarjetas caben sin una barra innecesaria.

La composición DWM mantiene una sola silueta. El fallback de diagnóstico es
`NEXUS_SINGLE_CONTOUR=0`.

## Validación en la instancia habitual

Reinicio autorizado y ordenado. Antes de reiniciar se verificó el respaldo de
navegación `nexus-nav-backup-20261024T145338Z` (tres archivos + hashes en
`restore-manifest.json`) y se creó un respaldo SQLite en
`nexus-nav-restart-20261024T150836Z/user-before.sqlite`.

- PID anterior: **9568**. PID actual: **12304**.
- Módulos cargados verificados por hash: `windows_app.py`,
  `windows_stack.py`; `active=False` y `purged=0` en el arranque.
- Tema final: **Tech Neon**.

### Barra y contenidos

Estado final real (`history-final-clean.png`): **Historial · ⭐ Mercadolibre ·
📌 Prompts · ⋯**. No aparecen `Pinboards` ni `+ Pinboard`.

- Mercadolibre abrió su contenido real (`nav-mercadolibre.png`).
- Prompts abrió su contenido real (`nav-prompts.png`).
- Ambos Pinboards reales siguen existiendo: id 1 `Mercadolibre` `#FFCC00` ⭐,
  id 2 `Prompts` `#FF2D55` 📌, con `pinboard_items = 0` en el momento de la
  comprobación.
- El footer no muestra títulos redundantes; solo los atajos
  `Ctrl+F Buscar · Enter Pegar · Esc Cerrar`.

### Menú y administrador

- `⋯` abre el menú real **Administrar Pinboards** (`uia-window-0.png`).
- El administrador muestra Mercadolibre, Prompts, `+ Nuevo`, `Volver` y
  `Esc Volver al historial` (`admin-real.png`).
- Creación: el formulario `Nuevo pinboard` conserva nombre, emoji, color,
  paleta, `Cancelar` y `Guardar` (`admin-create-real.png`); no se guardó nada.
- Edición: Mercadolibre conserva nombre, emoji, color, `Eliminar`,
  `Cancelar` y `Guardar` (`admin-edit-real.png`); no se guardó nada.
- Acciones: `Renombrar / color / emoji…`, `Mover arriba`, `Mover abajo` y
  `Eliminar…` (`admin-actions-menu-0.png`). No se ejecutó ningún movimiento.
- Eliminación: se abrió la confirmación
  «¿Eliminar «Mercadolibre»? Los clips se conservarán en Historial.»
  (`delete-confirm-0.png`) y se pulsó **Cancel**, no **Yes**. Mercadolibre
  sigue existiendo.

### Temas

Los cinco temas se cargaron en la instancia real y se restauró Tech Neon:

| Tema | Captura |
| --- | --- |
| Liquid Glass | `theme-liquid_glass.png` |
| Midnight Teal | `theme-midnight_teal.png` |
| Minimalista | `theme-minimalist.png` |
| Storem | `theme-storem.png` |
| Tech Neon | `theme-tech_neon.png` |

En los cinco se mantiene la misma barra, el footer limpio y un único contorno
visual; no reaparece el doble marco. Las capturas se hicieron con
`PrintWindow` para evitar el z-order de Chrome; `history-nav-fg.png` es la
captura de pantalla con Nexus al frente.

### Stack

Nexus Stack abrió con **4 elementos** (`stack-open.png`), mostró LIFO,
`Pausar`, `Pegar siguiente`, `Vaciar Stack` y cerró con su atajo. No se modificó
el motor de Stack ni se vació su contenido.

### Datos y logs

Auditoría final en solo lectura: **1566 clips**, **2 Pinboards**,
**0 `pinboard_items`**, **0 `stack_state`**, `PRAGMA integrity_check = ok`.
Los IDs de History son contiguos (1–1566), sin huecos que sugieran borrados.
Respecto al respaldo previo al reinicio (1545 clips), hay 21 clips nuevos
capturados externamente; el portapapeles sigue vivo y los números pueden variar.

`pinboard_items = 0` es legítimo: esa tabla solo contiene relaciones
Pinboard↔clip. Ningún clip se ha añadido a Mercadolibre o Prompts; crear un
Pinboard no copia clips. El respaldo previo también tenía 0 relaciones, así que
no es una pérdida introducida por la navegación.

Durante la validación Stack llegó a mostrar 4 elementos; al hacer la auditoría
final estaba vacío por actividad externa del usuario. Stack no borra History:
su consumo solo elimina la entrada de `stack_state`.

`nexus.log` no contiene `Traceback`, `ERROR`, `CRITICAL` ni excepciones desde
el reinicio. La línea `visual_verified=False reason=api_ack_visual_capture_required`
es un diagnóstico conocido de la solicitud DWM, no un fallo de la navegación.

Suite completa: **614 passed** (`pytest -q`).

## Chip de Pinboard corregido

La etiqueta larga `Mercadolibre` ya no se recorta al seleccionarse. Los chips de
Pinboards personalizados muestran únicamente el emoji del nombre y el texto, sin
el icono genérico duplicado, y reservan ancho para el estado activo en negrita.
`Mercadolibre` y `Prompts` se ven completos en los cinco temas, manteniendo
alineación, espaciado y contorno único.

La comprobación se hizo con una instancia aislada, base SQLite temporal y los
cinco temas; no se reinició la instancia habitual para esta corrección.

## Documentación canónica

La arquitectura completa, History, Pinboards, Stack, FIFO/LIFO, captura y
pegado, atajos, DWM, temas, navegación, altura adaptable, respaldos,
limitaciones y experimentos descartados están en `WINDOWS_ARCHITECTURE.md`.


## Recuperación

### Si falla la navegación o la interfaz

1. Cerrar Nexus de forma ordenada (menú de bandeja → Salir, o `WM_CLOSE`).
2. Restaurar los tres archivos desde
   `C:/Users/Admin/AppData/Local/Temp/opencode/nexus-nav-backup-20261024T145338Z`:
   - `src/nexus_linux/windows_timeline.py`
   - `src/nexus_linux/windows_design.py`
   - `tests/test_windows_panel.py`
3. Verificar los SHA-256 contra `restore-manifest.json`.
4. Ejecutar `run-nexus-windows.cmd` y confirmar PID, barra anterior y que
   Mercadolibre/Prompts siguen abriendo.
5. Si el problema es solo visual, no restaurar SQLite.

### Si SQLite estuviera corrupto

El respaldo `nexus-nav-restart-20261024T150836Z/user-before.sqlite` es de antes
del reinicio: **1545 clips, 2 Pinboards, 0 `pinboard_items`, Stack vacío**.
Restaurarlo descartaría los clips y elementos de Stack creados después de las
15:08 UTC. Solo debe usarse tras confirmar corrupción, con Nexus cerrado,
guardando antes una copia del SQLite actual. No es un paso rutinario.

### Fallback de composición

`set NEXUS_SINGLE_CONTOUR=0` antes de arrancar desactiva la composición de una
sola silueta si un driver DWM vuelve a mostrar doble marco.

## Evidencia

Directorio base:
`C:/Users/Admin/AppData/Local/Temp/opencode/nexus-nav-restart-20261024T150836Z/habitual/`.
Incluye capturas de barra, Pinboards, menú, administrador, creación, edición,
acciones, confirmación de borrado, los cinco temas y Stack.

La propuesta de consolidación Git, separada por runtime, visual, Stack,
navegación y herramientas, está en `GIT_CONSOLIDATION_PROPOSAL.md`. No se han
ejecutado `git add` ni commits.
