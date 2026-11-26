# Nexus 1.0.0rc1: captura real en la instalación existente

**Corrección posterior del fallo sensible:** consultar
[sensitive-capture.md](docs/verification/sensitive-capture.md). El monitor ahora
vincula estado y bytes a la misma oferta; el validador exige la prueba nueva
`sensitive-offer-bound`. Producción sigue deshabilitada hasta superarla en Wayland.

Actualización de captura, 2026-10-08. No requiere sudo ni reinstalar atajos.
El servicio y los atajos están operativos según la comprobación del usuario.
Desde el entorno de Codex no se pudo acceder a systemd, Hyprland ni Wayland:
los sockets responden `Operation not permitted`. **La captura real aún no está
aprobada ni se ha activado desde este entorno.**

## Cambios

- El generador elimina las comillas de `WorkingDirectory`. `update-service`
  actualiza solamente la unidad, hace backup y conserva los atajos actuales.
  Reconoce exclusivamente la corrección manual exacta de esas comillas; otras
  personalizaciones no reconocidas se conservan y bloquean la sobrescritura.
- La unidad fija los directorios XDG de producción y permite la captura sólo
  si preferencias y validación la autorizan. No cambia Ctrl+C ni Ctrl+V.
- Se reutiliza `ClipboardMonitor` con `wl-paste --watch` y lectura en trabajador.
  El arranque registra en journal si la captura está habilitada y el perfil usado.
  Se procesan correctamente notificaciones divididas entre lecturas de QProcess.
- La validación exige servicio detenido, destino aislado e íntegro, dependencias
  y acceso real a la sesión. Prueba texto, PNG, URI, exclusión sensible,
  deduplicación, supresión interna y acumulación sin sobrescribir el portapapeles.
  Toda prueba sintética usa una SQLite temporal. Al finalizar vacía el
  portapapeles de prueba para que el servicio no recapture su último clip.
- Activar/reabrir Stack, añadir clips, cambiar FIFO/LIFO y retirar elementos
  ya no prepara automáticamente otro clip. El portapapeles conserva la copia
  normal del usuario; preparar un pegado sigue siendo una acción explícita.
- `capture_status` consulta producción en modo sólo lectura. Puede comprobar
  una copia nueva y comparar su hash con el portapapeles actual sin mostrar texto.

## Destino y estado comprobado

Para esta instalación:

```text
/home/manuel/.local/share/nexus/production/nexus.sqlite
/home/manuel/.config/nexus/production/settings.json
/home/manuel/.config/nexus/production/capture-validated.json
```

La base de producción existente se consultó en modo sólo lectura: **0 clips,
`PRAGMA quick_check = ok`**. No existían preferencias ni marcador de captura.
No se abrió, migró, borró ni escribió la SQLite histórica ni ningún respaldo.
Los 5.005 clips sintéticos no se incorporan a producción. Una base poblada sin
validación reconocida bloquea la activación: no se vacía ni adopta automáticamente.

La retención del proceso principal sigue forzada a `False`; la activación guarda
también `allow_retention=false` y `evdev_watcher=false`.

## Aplicar desde una terminal de la sesión Hyprland

Ejecutar los pasos por separado y avanzar **sólo si el anterior termina sin error**.
Estos comandos son compatibles con fish. El validador reemplaza y finalmente
vacía el portapapeles actual; guarda antes cualquier contenido que necesites.

1. Actualizar la unidad existente y detener Nexus:

```sh
cd /home/manuel/Projects/Nexus/linux
env XDG_CONFIG_HOME=/home/manuel/.config XDG_DATA_HOME=/home/manuel/.local/share /home/manuel/.local/share/nexus-runtime/venv/bin/python scripts/integrate.py update-service
systemctl --user stop nexus.service
```

`update-service` hace `daemon-reload`, deja los bindings intactos y guarda la
unidad anterior en `~/.config/nexus/integration/backups/<fecha UTC>/`.
No ejecutar el instalador completo para esta corrección.

2. Validar y habilitar captura únicamente si todas las pruebas pasan:

```sh
env XDG_CONFIG_HOME=/home/manuel/.config XDG_DATA_HOME=/home/manuel/.local/share PYTHONPATH=/home/manuel/Projects/Nexus/linux/src /home/manuel/.local/share/nexus-runtime/venv/bin/python -m nexus_linux.capture_validation --test-clipboard --enable-production
```

Debe terminar con código 0 y mostrar `Captura habilitada para el próximo reinicio`.
Si falla, **no crear el marcador a mano** ni forzar `capture_enabled`.
Las preferencias previas, si existen, se respaldan en
`~/.config/nexus/production/backups/<timestamp>/` antes de habilitar.

3. Arrancar con las variables de tu sesión gráfica e inspeccionar:

```sh
/home/manuel/.local/bin/nexus session-start
systemctl --user status nexus.service --no-pager
journalctl --user -u nexus.service -n 80 --no-pager
env XDG_CONFIG_HOME=/home/manuel/.config XDG_DATA_HOME=/home/manuel/.local/share PYTHONPATH=/home/manuel/Projects/Nexus/linux/src /home/manuel/.local/share/nexus-runtime/venv/bin/python -m nexus_linux.capture_status
```

En journal deben aparecer `capture_enabled=True`, `production=True`,
`retention=False` y el inicio de `wl-paste --watch`. El estado debe indicar
`capture_enabled: true`, el destino correcto y, antes de la primera copia real,
cero clips. Los mensajes de inicio por sí solos no prueban una captura.

Si falló la validación, `nexus session-start` permite recuperar la interfaz;
la captura nueva continúa deshabilitada. Si habías habilitado captura previamente,
una nueva validación fallida no revoca las preferencias anteriores.

## Comprobar Ctrl+C real, segundo plano y Stack

Estas pruebas requieren interacción con aplicaciones en tu sesión. Los comandos
de comprobación no generan copias, no insertan clips ni interceptan teclas.

1. Oculta Historial y Stack. Inicia el comando siguiente, cambia a un editor,
   escribe y selecciona exactamente `NEXUS-REAL-COPY-A` y pulsa **Ctrl+C**:

```sh
env XDG_CONFIG_HOME=/home/manuel/.config XDG_DATA_HOME=/home/manuel/.local/share PYTHONPATH=/home/manuel/Projects/Nexus/linux/src /home/manuel/.local/share/nexus-runtime/venv/bin/python -m nexus_linux.capture_status --watch-seconds 60 --expect-text NEXUS-REAL-COPY-A
```

   Sólo devuelve `PASS` si la copia se registró después de iniciar la comprobación
   y coincide con el portapapeles actual. Abre Historial mediante el atajo ya
   instalado y confirma la tarjeta. Esto comprueba el monitor en segundo plano.

2. Vuelve a copiar el mismo texto: la cantidad total no debe crecer. Una recopia
   puede actualizar la fecha de la tarjeta existente. Copia un texto distinto:
   debe aumentar exactamente una fila. Para observar cantidades sin exponer texto:

```sh
env XDG_CONFIG_HOME=/home/manuel/.config XDG_DATA_HOME=/home/manuel/.local/share PYTHONPATH=/home/manuel/Projects/Nexus/linux/src /home/manuel/.local/share/nexus-runtime/venv/bin/python -m nexus_linux.capture_status --watch-seconds 60
```

3. Activa Stack mediante tu atajo ya operativo. Copia textos distintos con
   Ctrl+C; deben aumentar las tarjetas pendientes y `stack_pending`. El texto
   normal recién copiado debe seguir disponible para pegar en otra aplicación.
   Ocultar/reabrir Stack no debe perder esos elementos. Cambiar FIFO/LIFO no
   debe sustituir el portapapeles durante la acumulación.

4. Con Stack activo o Nexus oculto, ejecuta y copia **una imagen PNG** desde una
   aplicación que anuncie `image/png` (no copiar sólo su ruta en el explorador):

```sh
env XDG_CONFIG_HOME=/home/manuel/.config XDG_DATA_HOME=/home/manuel/.local/share PYTHONPATH=/home/manuel/Projects/Nexus/linux/src /home/manuel/.local/share/nexus-runtime/venv/bin/python -m nexus_linux.capture_status --watch-seconds 60 --expect-image
```

   Debe dar `PASS`, aparecer la miniatura en Historial y añadirse a Stack si
   sigue activo. El límite actual es 16 MiB. `wl-paste --list-types` permite
   comprobar qué formatos anuncia la aplicación.

5. Observa cantidades y usa la acción de Nexus para preparar un clip ya existente.
   No debe crear una fila nueva ni añadir otro elemento a Stack. No se verifica
   recepción del pegado por interpretar Ctrl+V. La actualización de
   [Stack secuencial](NEXUS_STACK_SEQUENTIAL.md) utiliza un binding temporal
   mientras está activo y una política explícita de envío con recuperación;
   no lo presenta como confirmación de inserción en la aplicación.

6. Reinicia con `nexus session-start`, repite una copia nueva y comprueba que
   las tarjetas previas sobreviven. Cierra e inicia sesión y repite la prueba
   con otro texto. **La persistencia después de reiniciar sesión sigue pendiente
   hasta completar este paso; no se declara Nexus 1.0 completamente validado.**

## Recuperación y pausa reversible

Reiniciar y consultar logs:

```sh
/home/manuel/.local/bin/nexus session-start
journalctl --user -u nexus.service -f
```

Para pausar la captura, desmarca Captura en Apariencia. Se conservan clips y
preferencias; no se borra historial. Detener todo el proceso temporalmente:

```sh
systemctl --user stop nexus.service
```

Desactivar autoinicio: `~/.local/bin/nexus disable-autostart`.
Reactivarlo: `~/.local/bin/nexus enable-autostart`.
Retirar integración sin borrar datos: `./uninstall.sh` desde este repositorio.

## Verificación ejecutada y límites

Suite final: **285 aprobadas, 9 errores**, todos los errores por permisos al
crear sockets HTTP locales, sin fallos de aserción. Evidencia:
`docs/verification/capture-regression.txt`. La instalación en staging y la
actualización exclusiva de la unidad se verificaron, incluida la corrección
manual de `WorkingDirectory` y la conservación de los bindings.

En el equipo están presentes `wl-copy`/`wl-paste` 2.3.0 y el runtime privado.
Los intentos reales de actualizar unidad y activar captura se bloquearon al
consultar systemd, antes de modificar la instalación o habilitar captura.
No se reinició ni detuvo la sesión real desde Codex.

Soporte actual: texto, URL, PNG y una URI de archivo por clip. No se garantiza
HTML, imágenes en otros formatos ni archivos múltiples. Las selecciones sensibles
marcadas se excluyen; un secreto como texto ordinario puede necesitar exclusiones
literales en Apariencia. No hay cifrado nuevo del historial local. Las copias muy
rápidas pueden coalescer antes de leer la selección. La supresión por hash dura
tres segundos y puede omitir una recopia externa idéntica durante ese intervalo.
No existe confirmación universal de que otra aplicación recibió un pegado Wayland.

Fuentes revisadas para la integración: [implementación oficial de wl-paste](https://github.com/bugaevc/wl-clipboard/blob/master/src/wl-paste.c),
[versiones de wl-clipboard](https://github.com/bugaevc/wl-clipboard/releases) y
[documentación de ejecución de systemd](https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml).
