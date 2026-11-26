# Corrección de captura sensible — 2026-10-08

## Hallazgos y alcance de la evidencia

El fallo comunicado ocurrió antes de habilitar producción. La comprobación
actual de producción en modo sólo lectura confirma **0 clips, integridad correcta,
captura deshabilitada y ninguna validación reconocida**. No se abrió la SQLite
histórica ni ningún respaldo. El validador anterior importaba `paths` después de
establecer `NEXUS_PROFILE_ROOT` temporal, y abría ese destino: la persistencia que
comprobaba el mensaje de error correspondía al perfil de pruebas, no a producción.
Ese mensaje comparaba eventos y cantidades totales; por sí solo no demuestra
que la fila inesperada contuviera precisamente el texto sensible. La base temporal
se eliminó al salir, por lo que no se inventa un análisis forense de esa fila.

La revisión identifica una carrera real capaz de almacenar contenido sensible:
el trabajador obtenía los MIME en una conexión `wl-paste --list-types` y leía bytes
en otra conexión `wl-paste --type ...`. No existía identidad de oferta que uniera
ambas operaciones. Una selección pública podía proporcionar los MIME y una nueva
selección sensible proporcionar los bytes. Además, descartar la notificación
`sensitive` no invalidaba una lectura pendiente ni su persistencia posterior.
Las pruebas nuevas reproducen ese orden de operaciones con transporte controlado.
La reproducción en el compositor real sigue pendiente.

El validador usa correctamente `wl-copy --sensitive`. En wl-clipboard 2.3.0 esa
opción anuncia `x-kde-passwordManagerHint`, junto con los formatos de texto. El
watcher examina la oferta y pasa `CLIPBOARD_STATE=sensitive` al hijo que recibe su
contenido por stdin. **2.3.0 no proporciona `CLIPBOARD_TYPE`**: se revisó la etiqueta
de esa versión, además de las cadenas del binario instalado. No se depende de
una variable presente únicamente en código posterior.

Fuentes primarias:
[wl-copy 2.3.0](https://github.com/bugaevc/wl-clipboard/blob/v2.3.0/src/wl-copy.c),
[wl-paste 2.3.0](https://github.com/bugaevc/wl-clipboard/blob/v2.3.0/src/wl-paste.c).

## Corrección

Se conserva `ClipboardMonitor`, `wl-paste --watch` y el trabajador existente.
La lista MIME sólo ayuda a escoger el formato. El trabajador solicita una
instantánea mediante una conexión corta de `wl-paste --watch --type <MIME>`:
la oferta inicial entrega su estado y sus bytes al mismo hijo. El formato se
solicita exactamente, incluyendo charset cuando corresponda. Se termina esa
conexión y sus hijos después de recibir el primer resultado o alcanzar el límite.
No hay otro servicio de captura, bindings nuevos ni acceso global a teclas.

El hijo `clipboard_offer` **no lee ni exporta bytes sensibles**. Sólo `data` permite
leer, con límite de 16 MiB; los estados sensibles, vacíos, desconocidos, errores
y transferencias incompletas se rechazan. El receptor tiene timeout y límites de
marco. Las notificaciones incrementan una generación; una selección sensible
invalida las lecturas pendientes y elimina cualquier recaptura en cola. Una lectura
antigua no puede persistir al llegar después de una notificación más reciente.
La presencia del MIME sensible sigue siendo motivo de rechazo aunque su valor
sea `public`; no se relajó la política anterior.

El validador ahora:

- Comprueba la ruta temporal antes de construir `StorageService`, y se niega
  a abrir una base si `paths` estaba importado con otro perfil.
- Imprime la ruta temporal y los MIME de prueba; nunca imprime secretos reales.
- Exige observar tanto el MIME sensible como una notificación sensible del watcher.
  Si otro gestor reemplaza la oferta y pierde la marca, bloquea la activación.
- Espera a que terminen las lecturas pendientes y consulta SQLite por el hash
  exacto del texto sensible. Exige también que no crezcan eventos ni filas.
- Copia el mismo texto como público y exige su captura: un watcher muerto o una
  exclusión accidental no pueden dar una aprobación falsa.
- Mantiene el servicio detenido y limpia el portapapeles sintético al finalizar.
- Sólo después de todas las pruebas guarda preferencias y marcador de producción.
  El marcador debe incluir la nueva prueba `sensitive-offer-bound`.

## Pruebas y estado real

`tests/test_sensitive_capture.py` comprueba transferencia pública→sensible,
invalidación de lecturas en vuelo, estados desconocidos, MIME exactos, formatos
compatibles, tamaño máximo, aislamiento de rutas y el ejecutable hijo mediante
procesos y pipes reales. El transporte Wayland se sustituye en esas pruebas:
**no constituyen una aprobación de Hyprland**.

Resultados finales en `sensitive-regression.txt`. El intento en la sesión real
está en `sensitive-session.txt`: systemd no es accesible desde este entorno por
`Operation not permitted`, y se bloquea antes de generar pruebas o activar captura.
No se arrancó Nexus ni se habilitó producción desde Codex.

## Repetir desde la sesión del usuario

Guarda el portapapeles actual antes: se reemplaza y vacía durante la prueba.
El servicio ya está detenido; detenerlo de nuevo es idempotente. No es necesario
reinstalar ni modificar la unidad o los atajos para esta corrección.

```sh
cd /home/manuel/Projects/Nexus/linux
systemctl --user stop nexus.service
env XDG_CONFIG_HOME=/home/manuel/.config XDG_DATA_HOME=/home/manuel/.local/share PYTHONPATH=/home/manuel/Projects/Nexus/linux/src /home/manuel/.local/share/nexus-runtime/venv/bin/python -m nexus_linux.capture_validation --test-clipboard --enable-production
```

**Sólo si el validador termina con código 0 y confirma la habilitación:**

```sh
/home/manuel/.local/bin/nexus session-start
systemctl --user status nexus.service --no-pager
journalctl --user -u nexus.service -n 80 --no-pager
```

Después, completar las pruebas físicas Ctrl+C de texto/PNG, segundo plano y Stack
descritas en [NEXUS_CAPTURE.md](../../NEXUS_CAPTURE.md). Hasta entonces no se
declara protección sensible ni captura de producción verificadas en Wayland.
