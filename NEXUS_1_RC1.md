# Actualización de captura (2026-10-08)

Para la instalación ya operativa, consultar [NEXUS_CAPTURE.md](NEXUS_CAPTURE.md): actualiza sólo la unidad, exige detener el servicio antes de la validación y comprueba Ctrl+C real. Los resultados de release descritos abajo corresponden a la preparación anterior.

# Nexus 1.0.0rc1 — implementación y validación

Fecha: 8 de octubre de 2026. Estado: **candidato preparado; no es todavía Nexus 1.0 validado para uso diario**.

## Estado de la entrega

| Área | Estado comprobado |
|---|---|
| Historial y Pinboards | Ventana horizontal existente conservada; filtros, tarjetas virtualizadas, gestión integrada y pruebas Qt offscreen |
| Tipografía | Títulos 18–19 px, sección 15 px, contenido 14 px, controles 13 px y metadatos 11–12 px; sin textos de 9–10 px |
| Distribución | Filtros alineados a la izquierda; tarjetas entre 248 y 360 px según espacio y cantidad; desplazamiento horizontal; colecciones de 240 px |
| Stack | Activación idempotente, sesión independiente de visibilidad, FIFO/LIFO persistente, conservación ante fallos y política automática de envío con recuperación |
| Apariencia | Muestras de paleta, cambio inmediato, claro/oscuro/automático, cinco paletas originales conservadas/adaptadas y preferencias atómicas |
| Instalación | Instalador/desinstalador por usuario implementados y probados en staging; instalación real bloqueada antes de escribir |
| Servicio real | No instalado ni iniciado por esta ejecución; systemd --user inaccesible desde el sandbox |
| Atajos reales | No instalados ni modificados; consulta a Hyprland bloqueada |
| Captura real | **Desactivada**; validación separada implementada, intento real bloqueado antes de crear datos |
| Historial original/backups | No abiertos para migración, restauración, eliminación ni captura; sin cambios efectuados |
| Reinicio de sesión | Pendiente; no se reinició ni cerró la sesión del usuario |

No se recibió la captura anunciada por el usuario. Las imágenes de `docs/verification/` son renders reales de Qt con **datos sintéticos en un perfil temporal**, no capturas del escritorio del usuario: [Historial](docs/verification/history.png), [Pinboards](docs/verification/pinboards.png), [Stack](docs/verification/stack.png), [Apariencia](docs/verification/appearance.png).

## Ciclo de Stack y límite de Wayland

La actualización actual está documentada en [NEXUS_STACK_SEQUENTIAL.md](NEXUS_STACK_SEQUENTIAL.md). El usuario confirmó servicio, atajos, captura y acumulación; los resultados históricos de preparación que aparecen más abajo no sustituyen esas comprobaciones.

Stack alterna visibilidad con el mismo atajo. Ocultar conserva sesión y pendientes; cancelar conserva pendientes y termina captura/secuencia; vaciar elimina únicamente pendientes de Stack. El reinicio recupera la cola sin sobrescribir el portapapeles.

Ctrl+V usa un binding temporal de Hyprland mientras Stack está activo. Prepara la cabeza y envía al destino exacto; el avance automático usa una política explícita de envío aceptado, con registro durable recuperable, no un acuse de inserción de la aplicación. Los fallos detectados conservan pendientes; los resultados inciertos bloquean el avance hasta revisión/reintento. El último envío aceptado oculta Stack y retira el binding propio. El modo manual permanece disponible al pausar la integración.

La prueba real de diez Ctrl+V y FIFO/LIFO está pendiente: el compositor sigue bloqueado desde Codex. Consultar la guía actual para el preflight, reinicio, prueba A/B/C, limitaciones y recuperación. No ejecutar de nuevo la validación sintética si la captura ya quedó habilitada y validada por el usuario.

## Conflictos de atajos

Se leyó `~/.config/foot/foot.ini`; no redefine copiar/pegar. Los valores predeterminados documentados en `/etc/xdg/foot/foot.ini:207–208` son **Control+Shift+C** y **Control+Shift+V**. Instalar los atajos solicitados globalmente impediría usarlos normalmente en Foot.

El instalador rechaza esas combinaciones cuando Foot está configurado/instalado. Prepara **Super+Ctrl+Shift+C/V** mediante `--alternate-shortcuts`; esta alternativa también debe superar la consulta de bindings activos. No se declaró libre: `hyprctl -j binds` devolvió `Couldn't set socket timeout (2)` dentro del sandbox. No se auditó exhaustivamente cada atajo interno de cada aplicación.

El fragmento gestionado solo contiene los dos atajos de Nexus y el arranque de sesión. No contiene Ctrl+C/V, cambios de otros bindings ni reescritura de la configuración completa. La carga se añade al archivo de personalización de Caelestia con marcadores identificables. No se recarga el compositor durante la instalación; se carga en el próximo inicio de sesión.

## Instalación y operación desde una terminal de la sesión real

Todos los comandos siguientes funcionan sin sudo y usan rutas explícitas. **No se ejecutaron contra la integración real cuando los permisos lo impedían.**

```sh
cd /home/manuel/Projects/Nexus/linux
python3 scripts/integrate.py check --alternate-shortcuts
./install.sh --alternate-shortcuts
```

El instalador verifica el runtime privado, dependencias disponibles, el bus de systemd y los bindings activos antes de escribir. No instala paquetes ni modifica el runtime. Los archivos ajenos o gestionados que hayan cambiado se conservan y bloquean la operación antes de modificar configuración. Las actualizaciones de archivos propios generan respaldos.

Genera:

- `~/.local/bin/nexus`, con runtime y código resueltos de forma explícita; no depende de PATH ni de una terminal abierta.
- `~/.local/share/applications/mx.com.storem.nexus.desktop`, nombre Nexus, categoría Utility e icono original de 1254×1254 px.
- `~/.config/systemd/user/nexus.service`, un servicio estático iniciado por la sesión; no es necesario `systemctl enable` ni activar linger.
- `~/.config/nexus/integration/hyprland.lua` y manifest de propiedad.
- Un bloque gestionado en `~/.config/caelestia/hypr-user.lua`, con copia previa si el archivo ya existe.

El evento `hyprland.start` importa únicamente las variables necesarias de la sesión y arranca el servicio. `ExecCondition` comprueba Wayland y Hyprland antes de lanzar Qt. El servicio usa `PartOf=graphical-session.target`, reinicia ante fallos y dispone de un guardián que termina al desaparecer el socket de esa instancia de Hyprland. La captura permanece desactivada; no se abre ninguna ventana al comenzar la sesión.

Para iniciar el proceso en la sesión actual después de instalar, sin recargar Hyprland:

```sh
/home/manuel/.local/bin/nexus session-start
/home/manuel/.local/bin/nexus panel
/home/manuel/.local/bin/nexus stack
systemctl --user status nexus.service --no-pager
journalctl --user -u nexus.service -n 100 --no-pager
systemctl --user restart nexus.service
```

Los bindings empezarán a funcionar después de volver a iniciar sesión. La instancia se protege mediante un bloqueo de proceso, con recuperación automática del bloqueo al morir el propietario. El endpoint D-Bus de producción es `mx.com.storem.nexus.production`, separado del endpoint de pruebas/legacy.

Actualizar después de revisar los cambios de código:

```sh
cd /home/manuel/Projects/Nexus/linux
./install.sh --alternate-shortcuts
systemctl --user restart nexus.service
```

Deshabilitar/restituir el arranque automático, sin eliminar datos:

```sh
/home/manuel/.local/bin/nexus disable-autostart
systemctl --user stop nexus.service
/home/manuel/.local/bin/nexus enable-autostart
/home/manuel/.local/bin/nexus session-start
```

`systemctl disable` no es el mecanismo de este diseño: el servicio se arranca desde la sesión, y el marcador `~/.config/nexus/integration/autostart-disabled` controla ese comportamiento.

Desinstalar la integración:

```sh
cd /home/manuel/Projects/Nexus/linux
./uninstall.sh
```

Detiene el servicio, quita el bloque gestionado y elimina exclusivamente archivos registrados cuyo contenido siga correspondiendo al manifest. Los archivos modificados posteriormente se conservan y se informan. Conserva configuración, historial, runtime, código y respaldos. **No hay opción de purga de datos.** Los bindings ya cargados se descargan al volver a iniciar sesión; no se fuerza una recarga gráfica.

Respaldos: `~/.config/nexus/integration/backups/<fecha UTC>/`. Para recuperar personalizaciones, localizar allí la copia de `hypr-user.lua`, compararla con el archivo actual y restaurar únicamente el contenido pertinente; no reemplazar una configuración que tenga ediciones posteriores sin revisarla. Para revertir Nexus normalmente basta el desinstalador.

## Captura y producción: fase separada

La instalación utiliza una base nueva en:

```text
~/.local/share/nexus/production/nexus.sqlite
~/.config/nexus/production/settings.json
~/.config/nexus/production/theme.json
~/.config/nexus/production/windows.json
~/.config/nexus/production/capture-validated.json
```

Respeta XDG cuando se definan sus variables. El historial legacy `~/.local/share/nexus/nexus.sqlite` no se copia ni mezcla. No se restauró la historia original. La retención automática está deshabilitada explícitamente en el proceso principal.

La captura requiere simultáneamente `capture_enabled=true` y el marcador de validación; una preferencia heredada no basta. Apariencia permite pausarla y configurar exclusiones literales de texto. Se omiten selecciones sensibles que anuncien `x-kde-passwordManagerHint`, estados sensibles/nulos, contenido excluido y lecturas mayores de 16 MiB. No se puede reconocer todo secreto copiado como texto ordinario si la aplicación no lo marca.

La lectura usa `wl-paste --watch`; las transferencias se realizan en un trabajador para no bloquear el hilo gráfico. El monitor tiene reinicio limitado con backoff. Las escrituras internas se excluyen por hash exacto durante tres segundos, en lugar de saltarse ciegamente el siguiente cambio; una copia externa distinta no se descarta por ese mecanismo. PNG conserva sus bytes originales; URI de archivo se escapa/desescapa correctamente. Se soporta **un archivo por selección**, texto/URL y PNG; no se promete conservar HTML, formatos privados, todas las imágenes o selecciones de múltiples archivos.

La secuencia actual requiere actualizar la unidad y detener el servicio antes de probar el portapapeles. Consultar [NEXUS_CAPTURE.md](NEXUS_CAPTURE.md) para los comandos exactos y la comprobación de copias reales. El validador reemplaza el portapapeles con datos sintéticos en una base temporal y lo vacía al terminar, antes de que vuelva a arrancar producción. No ejecutar la validación con Nexus activo.

Solo habilita el destino después de completar texto, PNG, URI, supresión interna, deduplicación e integridad. Rechaza una base de producción con contenido que no tenga validación previa; permite la base vacía creada al arrancar el servicio sin captura. Los elementos sintéticos de la validación no pasan a producción.

Limitaciones que requieren validación: los cambios muy rápidos pueden coalescer antes de leer la selección; la ventana de supresión también omite una recopia idéntica inmediata; la captura usa almacenamiento local sin cifrado nuevo. El backup cifrado original no se toca.

## Verificación ejecutada y pendiente

Resultados y logs reproducibles en `docs/verification/`. No son evidencia de la sesión gráfica real.

- Suite completa: **280 aprobadas, 9 errores por restricciones de sockets, ningún fallo de aserción**. Consultar el resultado final en `regression.txt`; las pruebas de red local fallan al crear sockets HTTP por `Operation not permitted`. No se cambiaron esas pruebas para convertir el bloqueo en PASS.
- Pruebas locales: Stack FIFO/LIFO, múltiples copias, consumo confirmado, fallos de escritura/envío, cancelación, ocultación con acumulación, preferencias y geometría, Pinboards, temas y PNG/URI.
- Recuperación: cierre y reapertura de una SQLite temporal real; restaura cola/orden sin escribir el portapapeles al iniciar.
- Ventana persistente: 25 ciclos de acumulación/preparación/confirmación/cancelación procesando eventos de Qt.
- Pruebas Qt: limpieza de widgets y eventos de destrucción en su prueba propietaria, evitando que se finalizaran durante pruebas posteriores. El orden que había producido un fallo nativo volvió a pasar.
- Instalador: instalación repetida en staging, rechazo de archivo ajeno antes de escribir y desinstalación que conserva datos.
- `.desktop`: `desktop-file-validate` sin errores.
- Unidad: `systemd-analyze --user verify` sin errores de sintaxis; emite advertencias de sockets del sandbox. Esto **no prueba que el servicio real arrancó**.
- Proceso aislado: segunda instancia rechazada, SIGTERM con salida 0 e integridad SQLite aprobados. Medición offscreen sin captura: 0 ticks en cinco segundos, RSS aproximado de 102 MiB; no extrapolarlo a captura real.
- Compilación Python y sintaxis bash verificadas.

Pruebas locales reproducibles:

```sh
cd /home/manuel/Projects/Nexus/linux
env NEXUS_PROFILE_ROOT=/tmp/nexus-regression QT_QPA_PLATFORM=offscreen /home/manuel/.local/share/nexus-runtime/venv/bin/python -m pytest -q
/home/manuel/.local/share/nexus-runtime/venv/bin/python scripts/check_background.py
/home/manuel/.local/share/nexus-runtime/venv/bin/python scripts/preview_release.py
```

La prueba de fondo y las previsualizaciones no requieren acceso al portapapeles. Todos sus datos son temporales/sintéticos.

Pendientes antes de liberar 1.0:

1. Ejecutar preflight e instalación fuera de las restricciones del sandbox, verificar la alternativa de atajos y la clase real de las ventanas.
2. Ejecutar la validación Wayland separada y habilitar captura únicamente en producción.
3. Probar en aplicaciones reales cinco Ctrl+C y el flujo conservador de pegado, FIFO y LIFO; probar fallo/destino cerrado, imágenes, archivo, ocultación, cancelación y ausencia de recaptura interna.
4. Validar en Hyprland el binding temporal y el avance automático con recuperación descritos en NEXUS_STACK_SEQUENTIAL.md; no confundir envío con acuse de inserción.
5. Comprobar dimensiones, posiciones guardadas, workspace, foco y todos los monitores/escalas. Ajustar con la captura del usuario cuando esté disponible.
6. Cerrar e iniciar sesión; comprobar servicio silencioso, bindings, preferencias, colecciones y cola persistente. **No declarar aprobado un reinicio de sesión por un reinicio de proceso.**
7. Medir CPU/RSS en reposo y bajo captura en la sesión real; repetir las nueve pruebas de HTTP en un entorno que permita sockets.

## Versión y límites de esta ejecución

Versión Python/paquete: `1.0.0rc1`. Base Git: `879c1d00a4e0597795964ce4bc32a9cd652723d9`. Árbol de trabajo con cambios anteriores conservados; no se creó commit ni tag de lanzamiento. La versión incluye código existente previo a esta tarea.

Se comprobó con `Hyprland --version` que el binario instalado es 0.56.2. Se ajustó el dispatcher de flotación a `action="set"` según la documentación Lua 0.56. No se pudo verificar su ejecución contra el compositor.

Los permisos permitían escribir únicamente dentro del proyecto y `/tmp`. Hyprland, D-Bus/systemd y servidores HTTP locales quedaron inaccesibles. No se solicitó sudo, contraseña, ampliación de permisos ni se eludió el sandbox. No se modificaron configuraciones reales de Hyprland, otros proyectos, bases históricas, backups, RODAL, VPS ni SC740.

La salida es una implementación revisable con instalación reproducible y una ruta de validación segura, **no una declaración de Nexus 1.0 completamente funcional**.
