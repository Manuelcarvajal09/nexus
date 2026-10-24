# Nexus Windows — revisión de layout de Historial y Pinboards

> **Revisión posterior:** este informe describe la intervención de layout, no
> demuestra la eliminación del doble marco exterior. Su cierre visual del
> contorno fue rechazado por el usuario. La auditoría de composición vigente
> se registra en `WINDOWS_COMPOSITION_AUDIT.md`; no debe confundirse padding
> interior mejorado con transparencia o clipping del HWND exterior.

## Causas verificadas

1. **Margen útil menor que el configurado.** `WindowsGlassSurface` pinta su
   contorno 8 px dentro del widget. Los márgenes anteriores 28/24/28/22 dejaban
   aproximadamente 20/16/20/14 px entre el contenido y el borde pintado.
   El espacio raíz era 12 px y el chrome heredado 9 px; había jerarquías densas
   y contenedores administrativos con márgenes cero.
2. **Título en el lugar equivocado.** `_open_editor()` escribía “Nuevo pinboard”
   en `_context_label`, un QLabel del footer global alineado a la izquierda.
   El formulario era un `QDialog` convertido en widget, sin título visible propio,
   reconstruido mediante índices de elementos de su layout heredado.
3. **Altura de formulario impropia.** El administrador reutilizaba la altura de
   una fila compacta de tarjetas de Historial. En la ventana real, su detalle
   tenía solo 164 px de altura. Ello mezclaba navegación, edición y scrolling.

## Organización nueva

```text
Ventana Windows / superficie Acrylic existente
  Chrome: marca + navegación de vistas (+ buscador solo en Historial)
  Administrador de Pinboards
    Volver
    QHBoxLayout (separación 20)
      pinboardNavigation: PINBOARDS, listado existente, + Nuevo, opciones
      pinboardDetailScroll / pinboardDetail:
        pinboardDetailHeading: Nuevo pinboard / nombre seleccionado
        formulario QWidget: Nombre + Emoji/Color + selector de identidad
        acciones: Guardar / Cancelar (y Eliminar en edición)
  Footer: contexto de la vista, nunca título del formulario
```

Sin selección, el detalle muestra “Tus pinboards” con instrucciones y no crea
filas anticipadamente. `+ Nuevo` limpia la selección lateral y abre un borrador
en el panel derecho. Guardar crea/actualiza y sincroniza selección/encabezado;
Cancelar no crea datos. El sidebar administrativo ya no incluye Historial.

## Espaciado y adaptación

- Token Windows `SURFACE_MARGIN=36`: compensa los 8 px de sombra y deja **28 px
  útiles**. Una única fuente de margen; sin un segundo padding del chrome que
  desalineara buscador, tarjetas y detalle.
- `BLOCK_GAP=20`; `INTERNAL_GAP=12`. Paletas compactas mantienen sus celdas y
  espaciado propios; no se aplicaron números indiscriminadamente.
- Despeje Windows del work-area: 24 px laterales mínimos y 28 px inferiores.
- El administrador reserva un viewport de 440 px, incluyendo su fila Volver;
  detalle real medido: 390 px. Pantallas cortas conservan scroll y acciones
  alcanzables; formulario refluye de dos columnas a una y paleta de 10 a 5 columnas.
- Historial mantiene su presentación compacta de tarjetas. Stack comparte
  margen/espaciado y aumenta su altura inicial para no recortar las filas.
- No se modificaron paleta, pintura, radios, Acrylic ni lógica de Stack.

## Archivos de esta intervención

- `src/nexus_linux/windows_timeline.py`: `_InlineEditor`, `_InlineSidebar`,
  `WindowsTimelineWindow` y su navegación, detalle, geometría y espaciado.
- `src/nexus_linux/windows_design.py`: únicamente tokens de espaciado/despeje.
- `src/nexus_linux/windows_stack.py`: únicamente márgenes, gap y tamaño visual.
- `tests/test_windows_panel.py`: arquitectura, creación/cancelación/guardado,
  estados, posiciones, tamaños estrechos, fuentes grandes, scrolling y búsqueda.
- `tools/windows_layout_probe.py`: captura real segura de ventanas de prueba.

Los cambios locales anteriores se conservaron. Material, backend DWM, fuentes
compartidas/Linux, portapapeles, persistencia e inicio automático no se alteraron.
Respaldo de fuentes previo:
`C:\Users\Admin\AppData\Local\Temp\opencode\nexus-layout-backup-20261024T030321Z`.

## Verificación visual real, no solo render offscreen

Se mostraron ventanas nativas con el backend de producción y `desktop_acrylic`
habilitado a escala Windows **125 %**, con datos SQLite y fondos sintéticos.
Las capturas usan `QScreen.grabWindow(0, ...)`: píxeles de pantalla, no `render()`
ni `widget.grab()`. Se verifica el orden Z de panel/fondo propios y se rechaza la
captura si una ventana ajena puede quedar incluida. No se usó el portapapeles,
hooks ni hotkeys en el probe; el foco se restauró.

Se inspeccionaron visualmente Historial, contenido de un Pinboard, administrador
sin selección, creación, edición y Stack. Se observó el contorno existente sin
añadir un marco exterior duplicado y se comprobaron alineaciones y controles.

Evidencia de posición en píxeles lógicos de la ventana:

| Estado | Borde derecho sidebar | X del título | Ancestro del título |
| --- | ---: | ---: | --- |
| Antes, creación | 204 | 34 | footer, fuera del editor |
| Después, creación | 192 | 212 | editor derecho |
| Después, edición | 192 | 212 | editor derecho |

“Nuevo pinboard” aparece una sola vez como QLabel visible en creación,
`in_editor=true`, `in_sidebar=false`. El footer dice “Pinboards”.

Capturas finales (seis vistas) y mediciones:
`C:\Users\Admin\AppData\Local\Temp\opencode\nexus-layout-final-neutral`.
Las capturas con fondo de patrón se conservan en `nexus-layout-after-02` para
comparar la respuesta del Acrylic; el color del fondo sintético no cambia la
paleta de Nexus.

## Pruebas y aplicación a la instancia habitual

- Suite completa: **609 passed**, 15,06 s. Compilación y `git diff --check` correctos.
- Cubiertos anchos 480/900, fuentes 11/15 pt, pantallas cortas/verticales,
  creación/edición/guardado/cancelación y regresiones funcionales existentes.
- Con autorización adicional, se respaldó SQLite mediante online backup antes
  de reiniciar. Backup y registro:
  `C:\Users\Admin\AppData\Local\Temp\opencode\nexus-layout-restart-20261024T031533Z`.
- Reinicio ordenado desde `run-nexus-windows.cmd` existente. GUI nueva PID **13252**;
  hashes de startup coincidentes; Stack arrancó inactivo; retención `purged=0`.
- **Comprobación adicional en la instancia habitual:** se abrió Historial por su
  atajo, se accionó Administrar Pinboards y `+ Nuevo` mediante UI Automation.
  “Nuevo pinboard” quedó en X físico **1391**, a la derecha del listado lateral,
  cuyo borde derecho era **1366** (25 px físicos = 20 lógicos a 125 %).
  Se canceló el borrador, sin guardar, y se ocultó el panel por su atajo habitual.
- Después del reinicio y de crear/cancelar el borrador, todas las filas de
  History, Pinboards, relaciones, Stack y su metadata eran **idénticas** a las
  del backup: 1436 clips, 2 Pinboards, 0 relaciones y 2 pendientes. Integridad ok.
  No se borraron datos ni se sustituyó la base.

Las primeras automatizaciones de comprobación encontraron límites del propio
harness: un guard de privacidad rechazó una captura y UIA expone el botón
Pinboards checkable como `CheckBox`, no `Button`. Se corrigieron los verificadores,
sin ocultar resultados ni alterar el producto para hacerlos pasar.
