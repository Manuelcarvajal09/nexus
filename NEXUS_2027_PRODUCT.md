# Nexus 2027 — History shelf y Pinboards integrados

## Decisión de producto

Referencia conceptual: https://pasteapp.io/ — biblioteca visual, búsqueda y
colecciones de contenido reutilizable. Nexus conserva su propia identidad,
implementada con widgets Qt nativos y un modelo/delegate virtualizado.
No se introduce WebView ni se copia la interfaz de Paste. La fotografía del
monitor no estaba disponible en este turno; se siguen las proporciones descritas.

History y Pinboards son dos vistas de UNA ventana principal. MainWindow
heredado ya no se instancia; la acción de biblioteca del tray abre Pinboards
integrados. Stack sigue independiente y ambas ventanas pueden coexistir.

## Implementación

- Shelf inferior inicial: ancho 84% del área útil, máximo 1600 lógico; altura
  inicial 380–390, mínimo de ventana 760x380. Margen inicial 32 respecto al área
  útil de Hyprland/Caelestia. Una geometría válida guardada tiene prioridad.
- Encabezado con marca arrastrable, búsqueda, History/Pinboards y cierre.
- Tarjetas horizontales 248x218, previews recortados, miniaturas locales,
  selección/hover, metadatos, iconos SVG consistentes y scroll horizontal.
- Filtros por tipo reales; búsqueda SQL sobre todo el historial, debounce 150ms.
  Se presentan hasta 200 coincidencias. No hay paginación infinita implementada.
- Colecciones dentro del mismo shelf: contador de clips, creación, renombrado,
  color/símbolo opcional, orden mediante drag/drop o flechas, eliminación explícita con confirmación
  sin borrar clips. Edición plegable para dar prioridad al contenido.
- Clic derecho en tarjeta: copiar sin cerrar, pegar, contenido completo y
  agregar/quitar membresía de pinboards. No se introduce borrado de historial.
- Stack inicial 400x500: siguiente clip numerado 01, secuencia #2/#3, FIFO/LIFO
  explícito y estado de captura real. Se conserva el motor y persistencia de cola.
- Tokens compartidos de espaciado, controles, bordes, radios y estados; paleta
  Caelestia opcional con parser de datos y fallback Nexus. No se ejecuta Lua de tema.
- Movimiento/resize con startSystemMove/startSystemResize y eventos locales
  reales. Geometría independiente obtenida de clientes Hyprland propios, guardada
  al ocultar, salir y cada segundo mientras es visible, solo si cambia.
- hide_on_focus_loss sigue false por defecto. Escape/cierre/paste explícito
  pueden ocultar. No se añade refocus periódico.

## Seguridad y límites

Solo perfiles sintéticos. No se abrió SQLite histórica ni snapshot cifrado.
Sin captura, retención, evdev, hotkeys globales o autostart nuevos. Sin sudo,
instalaciones, configuración global Hyprland ni acceso RODAL/VPS/SC740.
Captura y avance automático mediante Ctrl+C/Ctrl+V NO se activan en la prueba.
La secuencia se comprueba con los dos clips precargados y Pegar siguiente.

Always-on-top en Hyprland sigue sin certificación frente a otras FLOTANTES:
float + elevación al abrir + Qt hint no equivalen a pin o una política permanente.
No se introduce pin en todos los workspaces, fullscreen ni bucles que roben foco.
Este criterio requiere verificación real y, si falla, una integración específica
posterior. No se declara el producto listo para comercializar por pruebas offscreen.

Pendientes: validación física de múltiples monitores/escalas, superposición,
latencia/RSS/CPU en sesión real y revisión final de accesibilidad. No se altera esquema.

## Prueba host, revisión 5

Sin sudo, desde terminal de Hyprland:

    python /home/manuel/migration-reports/helpers/nexus-premium-visual-test.py

El editor auxiliar es SOLO destino de pegado, no diseño final de Nexus.
Perfil nuevo privado con 5005 clips y tres colecciones sintéticas.

1. Abrir History: verificar franja inferior, márgenes, filtros y búsqueda 04999.
2. Alternar Pinboards: Trabajo/Ideas/Snippets en la MISMA ventana; probar
   crear/renombrar/color/reordenar y membresía mediante clic derecho.
3. Mover y redimensionar, clicar otra app, Escape/reabrir. Reiniciar Nexus desde
   el helper: debe recuperar geometría en el mismo perfil. Probar ambos monitores.
4. Abrir Stack desde editor; Pegar siguiente, reabrir y repetir para verificar
   FIFO. Invertir a LIFO antes de consumir para probar orden inverso.
5. Mantener Stack abierto al clicar otra ventana flotante: observar superposición.
6. Repetir en otro workspace. Comprobar pin=false y ausencia de fullscreen.
7. Marcar únicamente las verificaciones que viste funcionar. Cerrar editor
   detiene solo el Nexus del perfil de prueba y escribe reporte privado.

Reporte: ~/migration-reports/nexus-visual-host-result.json. Incluye clientes
propios, geometría, D-Bus, tracebacks, consultas SQL, texto sintético recibido y
checks declarados por usuario. Ningún checkbox sustituye evidencia automática.

## Verificación local

85 pruebas de regresión pasan: filtros, SQL, Stack, foco, política de visibilidad,
resize, geometría, integración de colecciones sin segunda ventana y layouts
760x380 / 1200x390 / 1600x420. Arranque y salida de app con perfil vacío offscreen:
exit 0. Renders sintéticos revisados. La prueba física Wayland permanece pendiente.
