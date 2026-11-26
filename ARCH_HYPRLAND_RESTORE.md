# Nexus en Arch + Hyprland + Caelestia

## Estado de esta fase

Código Linux base: HEAD 879c1d0. install.sh y uninstall.sh ya contenían cambios
antes de esta fase y no se han modificado. La aplicación no se ha iniciado con
historial original. No se accedió al snapshot cifrado ni a SC740.

La base de Linux y la referencia Windows comparten el esquema principal:
clip_items, pinboards, pinboard_items, stack_state, stack_meta y url_preview_cache.
La apertura normal puede aplicar migraciones/backfill; nunca abrir la copia
maestra mediante StorageService. La igualdad del esquema de código no reemplaza
una comprobación de integridad sobre una copia de la SQLite real.

## Funciones recuperables y límites

- Historial de texto, imágenes, archivos y enlaces mediante wl-clipboard.
- Quick Shelf, biblioteca, pinboards, temas y Stack persistente mediante Qt.
- Atajos externos por D-Bus en Hyprland, sin depender de kglobalaccel de KDE.
- Pegado sintético mediante Lua de Hyprland dirigido a la ventana previamente
  identificada. No pegar si el destino desaparece o wl-copy falla.
- Autoavance mediante evdev permanece desactivado: no conceder grupo input ni
  habilitar ydotoold por defecto. Stack puede usarse manualmente.
- Previews de red desactivados inicialmente; la caché existente sigue disponible.
- Funciones Windows, binarios, venv y lanzamiento al login no se transplantan.

## Protección del historial

settings.json utiliza allow_retention=false por defecto. Una configuración vieja
con retention_days=30 no habilita borrados. La API de purga y su temporizador
requieren opt-in explícito. Mantener esta protección durante toda la migración.

NEXUS_PROFILE_ROOT selecciona data/config/cache/runtime privados e independiza
el nombre del servicio D-Bus. NEXUS_CAPTURE_DISABLED=1 evita capturar el clipboard
real durante pruebas. No pasar configuraciones Windows sin revisar cada campo.

## Runtime privado y comprobaciones

Usar Python 3.13 porque qasync declara compatibilidad inferior a Python 3.14.
El helper instala Python y dependencias únicamente en
~/.local/share/nexus-runtime; no reemplaza Python global ni inicia Nexus.

Desde la terminal real, SIN sudo:

```sh
python /home/manuel/migration-reports/helpers/nexus-prepare-runtime.py
python /home/manuel/migration-reports/helpers/nexus-test-runtime.py
```

El segundo helper ejecuta pytest y dos arranques Qt offscreen con un clip
sintético antiguo. Los logs quedan privados en el perfil de prueba; no valida
visibilidad, foco ni pegado reales en Wayland. El suite de previews utiliza un
servidor HTTP de prueba local; no se consultan servicios de clientes.

Para una SQLite de snapshot FRÍO ya extraído al SSD interno, sin Nexus escribiendo:

```sh
python /home/manuel/migration-reports/helpers/nexus-check-history-copy.py /RUTA/INTERNA/AL/SNAPSHOT.sqlite
```

El helper conserva bytes originales y sidecars, comprueba SHA-256, verifica
integridad sobre copia, crea una maestra de solo lectura y prueba migraciones
sobre otra copia. Compara conteos de todas las tablas originales y comprueba que
la maestra y el origen no cambiaron. No sustituye el historial operativo.
No borrar estas copias hasta verificar restauración y disponer de recuperación.

## Integración pendiente de autorización/verificación

Los archivos deploy/arch/nexus.service y nexus-hyprland.lua son propuestas,
no configuración activa. Antes de cargarlos:

1. Validar la copia del historial y revisar/restaurar selectivamente temas/config.
2. Abrir un perfil de prueba en Wayland y verificar clase de ventana, geometría,
   foco, bandeja, D-Bus, captura/pegado en editor vacío y persistencia al reiniciar.
3. Consultar binds reales de Hyprland y aprobar Ctrl+Shift+V / Ctrl+Shift+C.
4. Instalar la integración aprobada; probar inicio manual del servicio.
5. Habilitar autostart únicamente tras verificar estabilidad.

QApplication usa desktopFileName mx.com.storem.nexus. Verificar su clase efectiva
antes de activar la regla float. No modificar atajos Caelestia ya existentes.
El comando de control es python -m nexus_linux.control panel|stack, desde el venv.

## Evidencia y recuperación

Nueve regresiones sin Qt pasan: retención protegida, persistencia de clips
antiguos y guardas de pegado. Las llamadas Hyprland se prueban con mocks;
no constituyen evidencia de pegado real. La validación Qt/Wayland queda pendiente
hasta ejecutar los helpers y la prueba gráfica en el host.

Baseline de archivos editados:
/home/manuel/migration-reports/nexus-code-baseline-20261008

Para revertir, detener SOLO Nexus cuando exista y recuperar los archivos del
baseline individualmente tras revisar git diff; no usar git reset --hard ni
sobrescribir install.sh/uninstall.sh. No borrar SQLite, sidecars ni copias
maestras. Una recuperación de datos siempre comienza con una copia nueva de la
maestra verificada, nunca con modificaciones sobre ella.

## Integración nativa: prueba visual

La estrategia es Qt Wayland normal + Hyprland. Layer-shell queda descartado
para esta fase: conservar ventanas, menús y foco de Qt evita añadir un protocolo
y dependencia de shells para una aplicación con biblioteca y cuadros de diálogo.

El módulo desktop selecciona el monitor enfocado y usa QScreen en coordenadas
lógicas. Tras mapear una superficie consulta únicamente PID/título propios,
aplica float, tamaño, posición, elevación y foco con dispatchers Lua. No usa
fullscreen ni pin entre workspaces. Los paneles conservan Escape y el cierre
por pérdida de foco existente. La posición real y escalados mixtos requieren
la prueba host; las pruebas mock no los certifican.

Con NEXUS_FOLLOW_CAELESTIA=1 se leen colores hex de la paleta generada
~/.config/hypr/scheme/current.lua sin ejecutarla. Se mantiene geometría y
tipografía Nexus. Los cambios de paleta se detectan cada tres segundos y solo
repolishan cuando cambian; elegir un tema Nexus desde la bandeja suspende el
seguimiento. Sin paleta válida se conserva el tema Nexus. No se añade animación
opacity de ventana Qt, que falló en la implementación previa.

Ejecutar desde la terminal real, sin sudo:

```sh
python /home/manuel/migration-reports/helpers/nexus-visual-test.py
```

La prueba lanza Nexus con regla float temporal asociada al proceso mediante
hl.dsp.exec_cmd. No edita reglas, binds ni systemd. Se crea un editor de prueba
independiente y botones para abrir historial/Stack. Al seleccionar un clip se
sobrescribe el clipboard con el elemento sintético elegido; no se captura ni
almacena el contenido anterior. No elegir ni pegar información privada.

Verificar:
- Historial aparece flotante, sin fullscreen, legible y sobre ventanas normales.
- Escribir búsqueda, elegir el clip de texto y confirmar su pegado en el editor.
- Escape oculta; el botón vuelve a abrir sin un atajo global.
- Stack, biblioteca y pinboards pueden abrirse desde la bandeja Nexus.
- Repetir desde cada monitor/workspace, especialmente con escalados diferentes.
- Inspeccionar la imagen sintética; el archivo generado pertenece solo al perfil.
- Cerrar el editor de prueba termina Nexus por D-Bus. El perfil y logs permanecen
  privados para diagnóstico, nunca se borran automáticamente bases de datos.

Reporte: ~/migration-reports/nexus-visual-host-result.json. Incluye ventanas
observadas, floating/xwayland/monitor/geometría y recepción del texto sintético.
La aceptación visual queda pendiente de confirmación humana. No confundir
capture_enabled=false con validación de captura: esa función se probará aparte
con consentimiento, después de aprobar foco y pegado.

Los lanzadores deploy/arch/nexus y mx.com.storem.nexus.desktop son preparados,
no instalados. El servicio sigue sin activar. Para actualizar el runtime usar
el helper de preparación, revisar git diff y repetir componentes y prueba
visual sobre una copia antes de cambiar el perfil operativo.

### Corrección tras primera prueba visual

El log real verificó NameError: desktop en StackWindow._reposition_to_active_screen:
la llamada D-Bus llegó pero faltaba importar desktop. Importación corregida y
regresión añadida que instancia StackWindow, abre, oculta y reabre con un clip.
La validación gráfica de Stack sigue pendiente: offscreen no la reemplaza.

Se conservan ahora las coordenadas solicitadas por separado de QWidget.geometry,
que puede devolver origen cero en Wayland. El área útil intersecta QScreen con
las reservas de Hyprland; se añade separación inferior de 32 unidades lógicas
y márgenes laterales de al menos 32 en pantallas normales. Shelf tiene techo
960/1100/1200 según clase de monitor; Stack usa 360x540 y editor 540x360,
siempre limitados al espacio disponible. No se asume resolución ni escala fija.

El helper revisión 2 también lanza el editor con una regla float por proceso,
aplica estilos Nexus y ajuste de texto, y registra los resultados D-Bus y
tracebacks. Se cargan dos clips sintéticos en Stack sin armar automáticamente
el clipboard al inicio del perfil de prueba. La selección/paste manual sigue
siendo la única acción que escribe el portapapeles durante esta prueba.
