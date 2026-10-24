# Nexus Stack Windows: contrato de captura y pegado

## Causas y evidencia

La auditoría completa está en [WINDOWS_STACK_DEEP_AUDIT.md](WINDOWS_STACK_DEEP_AUDIT.md).
El tamaño de `INPUT` ya era correcto al comenzar esta auditoría (40 bytes x64).
El fallo secuencial vigente era otro: Ctrl+V se propagaba como observador;
`available.armed=False` impedía preparar el siguiente, y la UI podía mostrar una
cabeza distinta del clipboard. Las pruebas anteriores del botón manual no
recorrían ese hook.

## Activación y recuperación

- Al arrancar: History activo; Stack **inactivo**, aunque tenga pendientes.
- `Ctrl+Shift+C`: muestra y activa/reanuda Stack explícitamente. No agrega el
  clipboard antiguo. Los pendientes persistidos se identifican como recuperados.
- Pausar, cerrar/escape o volver a alternar un panel visible: desactiva captura y
  secuencia sin borrar pendientes. Ctrl+V vuelve al comportamiento de Windows.
- Un hide interno por el botón manual conserva la sesión; el tray indica
  “Stack activo · oculto”. Reabrir no debe apagar una sesión oculta por error.
- Activo pero vacío: Ctrl+V se bloquea para no repetir el último payload. Copia
  nuevos elementos o desactiva Stack para usar el clipboard convencional.

## Operación secuencial

El hook `WH_KEYBOARD_LL` bloquea V-down y V-up del Ctrl+V externo activo, agrupa
autorepeat en una única intención y emite `PasteRequest` al liberar V. Fuera de
Stack no bloquea la pulsación. Los eventos internos con `NEXUS_INPUT_MARKER` se
propagan sin generar otra solicitud ni alterar el estado físico del gesto.

En el hilo Qt, una cola de 16 solicitudes pendientes más una en ejecución
selecciona la cabeza FIFO/LIFO en cada operación. Valida sesión, generación y
destino; escribe el payload tipado y verifica propiedad, marcador único y versión
de clipboard; envía input marcado; comprueba destino/versión y espera 180 ms de
estabilización antes de persistir el consumo. La siguiente solicitud selecciona
la nueva cabeza y **escribe su propio contenido**, sin depender del clipboard
anterior. No se restaura el primer elemento ni se prearma el siguiente.

Ctrl puede mantenerse pulsado: Nexus inyecta solo V-down/up y no suelta el Ctrl
del usuario. Con Ctrl neutral inyecta el lote de cuatro eventos. Shift/Alt/Win o
un V aún pulsado se esperan con deadline de 1,5 segundos; al agotarse se cancela,
se conserva el elemento y se requiere una acción nueva. Input parcial no consume;
la limpieza libera únicamente teclas inyectadas, con reintentos acotados.

## Política automática, no acuse universal de inserción

`INPUT_ACCEPTED` significa que Windows aceptó todos los eventos esperados, no que
la aplicación haya insertado contenido. La política automática exige además
payload verificado, foco exacto, misma sesión/generación y estabilidad de versión
y foco hasta el control post-dispatch. Si una condición falla, el elemento queda
en Stack y se descartan solicitudes incompatibles, sin reintentar silenciosamente.

Los 180 ms **no prueban inserción**: son una ventana acotada para no reemplazar un
payload antes de que el destino lo lea. Un control que difiera su lectura más allá
de ella o ignore Ctrl+V no tiene un acuse universal accesible a Nexus. En caso de
dispatch aceptado sin inserción, el original permanece en History como recuperación.
Consumir Stack no borra History ni Pinboards. Un fallo de SQLite conserva tanto
la cola en memoria como el estado persistido; una inserción ya realizada antes de
ese fallo puede requerir que el usuario evite duplicarla al reintentar.

Referencia: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput
UIPI limita input hacia procesos de mayor integridad; no se desactivan protecciones
ni se elevan privilegios. Si Windows rechaza incluso los key-up de limpieza, se
registra `sendinput.release_unconfirmed`; puede ser necesario soltar Ctrl/V
físicamente. No se puede prometer recuperación de input bloqueado por el sistema.

## Formatos y observabilidad

Se mantienen los formatos implementados: texto/URL Unicode, imágenes PNG como
imagen, un archivo local como URL. Nexus no captura formatos arbitrarios/múltiples
archivos que su esquema no soporte; esa limitación preexistente no se oculta.
El monitor nunca escribe clipboard y descarta escrituras propias por ownership.

`NEXUS_STACK_DIAGNOSTICS=1` habilita session/op/event/item/kind/order/state/sequence/
hwnd/result sin payload. Los hashes de módulos/PID y la línea `stack.baseline`
permiten verificar versión cargada y arranque inactivo incluso sin diagnóstico.

## Verificación

`tools/windows_stack_hook_e2e.py` ejecuta la composición real de la aplicación con
`LOCALAPPDATA` temporal. Usa RegisterHotKey y WH_KEYBOARD_LL reales y compara
texto receptor y IDs restantes. Su input es **inyectado, no físico**. Se niega a
ejecutar si Nexus habitual está activo: un SQLite temporal no aísla el clipboard
global. Requiere una ventana autorizada y `--allow-global-clipboard`. Preserva
los formatos expuestos por Qt, detiene el monitor de prueba antes de restaurar,
comprueba datos restaurados y usa `OleFlushClipboard`.

El runner anterior `windows_stack_e2e.py` prueba solo la ruta manual: se conserva
como evidencia y no debe usarse para aceptar el flujo físico habitual.
