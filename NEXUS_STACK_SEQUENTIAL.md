# Stack secuencial — actualización incremental de 1.0.0rc1

Revisión: `2026-10-08-sequential-dispatch-1`. El runtime, servicio, atajos de
Historial, preferencias de captura y datos históricos conservan su integración.
La comprobación actual en producción fue sólo lectura: 8 clips de texto,
captura habilitada y marcador de seguridad reconocido. No se volvió a ejecutar
la validación sintética ni se modificó su autorización.

## Diagnóstico

El código anterior **no conectaba Ctrl+V al avance**. `prepare_manual_paste`
escribía el elemento; `paste_next_and_advance` enviaba un atajo pero dejaba
`pending_confirmation`. Sólo `confirm_pending`, invocado desde el botón de
confirmación, consumía la cabeza. El watcher evdev estaba intencionalmente
deshabilitado porque una tecla no demuestra inserción en otra aplicación.
No era un fallo del contador: faltaba una integración automática autorizada.
Además, `ShowStack` siempre mostraba y activaba la ventana; no alternaba visibilidad.

## Integración implementada

Al activar Stack, Nexus consulta los bindings reales de Hyprland. Si existe
otro Ctrl+V, incluso en un submap, se niega a instalar el suyo e informa del
conflicto. Antes de cambiar el compositor guarda un snapshot privado de bindings
en `~/.config/nexus/production/stack-binding-backups/<timestamp>.json`.
No edita `hyprland.lua`, `hypr-user.lua` ni el fragmento de los atajos existentes.

Instala **un binding temporal de Ctrl+V al soltar la tecla**, sin repetición.
El callback Lua captura la dirección de la ventana activa y un identificador
único de petición. Sólo ejecuta un comando externo: no bloquea el compositor con
portapapeles, D-Bus o esperas. Ese comando llama `StackPaste` en el servicio Nexus
existente; no inicia otra instancia ni otro servicio.

Por petición:

1. Comprueba la ventana destino y el siguiente clip FIFO/LIFO.
2. Registra una entrega pendiente en SQLite antes de escribir o enviar.
3. Escribe el clip mediante el `PasteService` existente y verifica el contenido.
4. Recomprueba el foco; una evaluación Lua comprueba el destino y envía Ctrl+V
   a esa dirección sin robar el foco a otra ventana.
5. Si Hyprland acepta el envío, guarda **en una transacción** el avance de la
   cola y el registro `dispatched`. Retira una sola tarjeta y actualiza la vista.
6. Al agotarse, desactiva la sesión, retira exclusivamente su handle del binding
   y oculta Stack. No usa `hl.unbind("CTRL + V")`, que podría borrar binds ajenos.

**La siguiente cabeza queda preparada lógicamente. Sus bytes se escriben al
recibir el siguiente Ctrl+V**, no inmediatamente después del envío anterior.
Sobrescribirlos enseguida puede hacer que una aplicación lenta lea B cuando
todavía está procesando el pegado de A. No se usa un temporizador para adivinar
cuándo ocurrió el pegado.

La supresión de escrituras internas sigue usando hashes exactos. Ahora conserva
la identidad del último payload interno hasta observar otro contenido, aunque
la preparación/envío tarde más de tres segundos; invalida lecturas anteriores
al escribir. Una copia externa distinta se sigue capturando. Una recopia idéntica
al payload interno no se puede distinguir de su autocaptura mientras permanezca
como selección actual; se excluye conservadoramente.

## Política explícita y garantías

**Avance por envío aceptado por Hyprland, con recuperación duradera. No hay
acuse de recibo de la aplicación.** Se muestra esta política en Stack y en
`StackStatus`; los logs dicen «envío aceptado», no «pegado confirmado».

| Alternativa | Límite y decisión |
|---|---|
| evdev / hooks de teclado | Requieren acceso adicional y sólo detectan teclas. No se activan. |
| Observar cambios del portapapeles | Copiar no implica pegar. No se usa para consumir. |
| Observar peticiones de data-control | Pueden venir del monitor o de otros gestores; la transferencia no prueba inserción ni identifica universalmente al editor. No se usa como acuse. |
| Confirmación manual | Disponible al pausar la integración; requiere interacción por clip. |
| Binding temporal + envío dirigido + journal | Permite Ctrl+V sucesivos sin tocar Stack y conserva la recuperación de cada envío. Es la política implementada. |

Protecciones:

- Preparación fallida o foco cambiado antes de enviar: se conserva el pendiente.
  El siguiente Ctrl+V puede reintentar una operación que se sabe que no se envió.
- Timeout/error durante envío, o fallo de persistencia después de enviar:
  resultado incierto; se conserva la cola y se bloquea el avance automático.
  «Reintentar pendiente» requiere revisar antes el destino, pues puede duplicar
  una inserción que sí ocurrió. No se reenvía automáticamente después de un crash.
- Peticiones repetidas con el mismo identificador no consumen ni envían otra vez.
  Mantener Ctrl+V pulsado no crea entregas periódicas.
- Cada elemento retirado por esta política permanece en Historial y en el journal
  como referencia recuperable. «Recuperar último enviado» lo devuelve a la cabeza;
  puede repetirse para recuperar varios. No implica deshacer texto en el destino.
- Si el servicio desaparece, el comando del próximo Ctrl+V comprueba que ya no
  posee el nombre D-Bus, retira el binding propio y reenvía el Ctrl+V normal a la
  ventana capturada. Si el resultado D-Bus es ambiguo y el servicio sigue vivo,
  no envía una segunda copia.

El journal es la tabla nueva `stack_deliveries` en la **base actual de producción**.
Se crea de forma aditiva e idempotente, sin migrar clips ni alterar tablas
históricas originales. Guarda ID de clip, petición, dirección y estado; no copia
secretos ni texto a logs. No hay limpieza o retención automática.

Limitaciones materiales: un campo que ignora Ctrl+V o una aplicación que no pega
con esa combinación puede rechazar la inserción aunque Hyprland acepte el envío.
En ese caso se necesita «Recuperar último enviado». Las pulsaciones muy rápidas
frente a un cliente que lee con retraso tampoco tienen un acuse universal que
permita demostrar recepción individual. Se conserva el payload hasta la siguiente
petición, pero no se afirma fiabilidad universal de inserción. Probar primero
las aplicaciones reales; en terminales Ctrl+V puede tener otra función.

Fuentes primarias revisadas: [bindings de Hyprland](https://wiki.hypr.land/configuring/core/binds/),
[flags de bindings](https://wiki.hypr.land/configuring/core/binds/flags/) y
[protocolo data-control](https://gitlab.freedesktop.org/wlroots/wlr-protocols/-/blob/master/unstable/wlr-data-control-unstable-v1.xml).
También se revisó `/usr/share/hypr/stubs/hl.meta.lua` del equipo para los handles
de bindings, `release`, ventana activa y el dispatcher `send_shortcut`.

## Operaciones independientes de la ventana

| Acción | Resultado |
|---|---|
| Super+Ctrl+Shift+C con Stack oculto | Activa/reanuda sesión y muestra pendientes sin reiniciarlos. |
| Mismo atajo con Stack visible; cerrar; Escape | Sólo oculta. Mantiene cola, acumulación y binding. |
| Pausar acumulación | Detiene sólo la incorporación a Stack; Historial sigue capturando. |
| Pausar pegado secuencial | Retira el binding; conserva cola y acumulación. Ctrl+V normal. |
| Cancelar sesión | Detiene acumulación y secuencia; conserva pendientes para volver a abrir. |
| Vaciar pendientes | Vacía sólo Stack y termina sesión; no borra Historial. |
| Consumir el último mediante envío aceptado | Oculta, termina sesión y restaura Ctrl+V normal. |
| Reiniciar proceso | Recupera pendientes, sin armar portapapeles ni activar binding hasta abrir Stack. |

FIFO/LIFO se puede cambiar cuando no hay entrega incierta. El siguiente elemento
se destaca; pendientes, modo activo y errores se actualizan de inmediato. La
transición de la tarjeta siguiente usa una animación de 120 ms que no gobierna
el envío ni su avance.

## Actualizar la instalación actual

No reinstalar el servicio ni los atajos. Desde tu terminal en Hyprland:

```sh
cd /home/manuel/Projects/Nexus/linux
env PYTHONPATH=/home/manuel/Projects/Nexus/linux/src /home/manuel/.local/share/nexus-runtime/venv/bin/python -m nexus_linux.stack_hyprland check
```

Si falla el preflight, conservar el servicio actual y revisar el conflicto/error.
Si pasa:

```sh
/home/manuel/.local/bin/nexus session-start
systemctl --user status nexus.service --no-pager
journalctl --user -u nexus.service -n 80 --no-pager
```

El reinicio recupera la cola existente y mantiene la autorización de captura.
Para consultar estado, sin leer los contenidos:

```sh
gdbus call --session --dest mx.com.storem.nexus.production --object-path /Nexus --method mx.com.storem.nexus.StackStatus
hyprctl -j binds
```

Con Stack activo debe haber exactamente un Ctrl+V con descripción
`Nexus Stack sequential paste (temporary)`. Al agotarlo o pausarlo debe desaparecer.
Una recarga de configuración puede retirar el binding temporal: ocultar y volver
a abrir Stack lo reinstala después de revisar conflictos.

Recuperación de emergencia, sin tocar clips ni otros bindings:

```sh
env PYTHONPATH=/home/manuel/Projects/Nexus/linux/src /home/manuel/.local/share/nexus-runtime/venv/bin/python -m nexus_linux.stack_hyprland reset
/home/manuel/.local/bin/nexus session-start
```

Para dejar el modo automático desactivado, usa «Pausar pegado secuencial» o
«Cancelar sesión» antes de ocultar. Para detener el proceso: `systemctl --user
stop nexus.service`. El cierre limpio retira el binding. La desinstalación
existente continúa conservando historial, configuración y runtime.

## Prueba práctica obligatoria en tu sesión

No se declara PASS de Hyprland mediante mocks ni por un resultado de D-Bus.

1. Tras el reinicio, abre Stack con **Super+Ctrl+Shift+C**. Si hay pendientes
   anteriores, consérvalos o usa «Vaciar pendientes» conscientemente para esta
   prueba; esto no elimina Historial. Selecciona FIFO.
2. En un editor externo copia exactamente `A`, luego `B`, luego `C` con Ctrl+C.
   Debe haber tres pendientes, A destacada, y el clipboard normal debe contener C.
3. En otro campo editable con Ctrl+V estándar, pulsa y suelta Ctrl+V tres veces,
   sin tocar Stack. Comprueba **ABC** y cantidades 2, 1, 0. Sólo después del
   tercero debe ocultarse; `StackStatus` debe indicar inactivo y binding retirado.
4. Otro Ctrl+V normal debe pegar el último clipboard (C), sin nueva consumición.
   El texto resultante será **ABCC**. Esto también comprueba restauración del atajo.
5. Repite con FIFO apagado/LIFO: el resultado debe ser **CBA**, luego **CBAA**.
6. Repite con diez textos distintos y comprueba los diez en orden. Mantén una
   pulsación para verificar que sólo se entrega una vez al soltar, sin repetición.
7. Durante acumulación, alterna dos veces el atajo: debe ocultar/reabrir sin perder
   clips. Cambia workspace y monitor; no debe vaciarse por foco o visibilidad.
8. Prueba un destino que no sea válido (Stack mismo / foco que cambie durante
   preparación): no debe reducir pendientes ni enviar a otra ventana. Prueba
   también un campo que ignore Ctrl+V: **esto no es detectable como fallo de
   inserción**; verifica que «Recuperar último enviado» permite recuperarlo.
9. Prueba texto y PNG en aplicaciones que acepten esos formatos. Las URI de
   archivo se conservan; su pegado depende de que el destino admita archivos.
   Verifica Historial y copia normal al finalizar.
10. Reinicia proceso/sesión con pendientes y comprueba su recuperación. Si hay
    entrega incierta, revisa el destino antes de autorizar el reintento.

## Evidencia de esta ejecución

Suite completa: **324 aprobadas y 9 errores**, los errores por sockets HTTP
locales bloqueados; ningún fallo de aserción. Pruebas nuevas: diez envíos FIFO/LIFO,
consumo único, fallos de escritura/foco/envío, incertidumbre y recuperación,
transacción de queue+journal, preservación del historial, binding con conflicto,
retirada por handle, ocultación/pause/cancel/clear, cierre al último y supresión
interna con envío lento. Logs en `docs/verification/stack-sequential-*.txt`.
La Lua generada compila con `luajit -b`; eso no prueba la API en el compositor.
Proceso real aislado/offscreen: arranque, rechazo de segunda instancia, cierre
SIGTERM e integridad SQLite aprobados. En cinco segundos sin captura ni integración
activa consumió 0 ticks de CPU; esto no mide el rendimiento de la sesión Wayland.

Los sockets de Hyprland y systemd siguen bloqueados en el entorno de Codex.
El preflight real no pudo ejecutarse y no se reinició el servicio instalado.
**FIFO/LIFO y A→B→C mediante Ctrl+V físico, aplicaciones reales y reinicio de
sesión están pendientes. Nexus no se declara completamente validado.**
