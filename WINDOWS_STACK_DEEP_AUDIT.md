# Nexus Stack Windows — auditoría del recorrido habitual

## Línea base (antes de los cambios de esta auditoría)

- Proyecto: `C:\Users\Admin\Projects\Nexus\windows-reference`.
- Rama: `windows-native`; HEAD `58952b5`. Árbol sucio previo: ocho archivos
  versionados modificados, módulos visuales Windows y pruebas/tools sin commit.
  No se revierten los cambios previos ni los modos de `install.sh`/`uninstall.sh`.
- Inicio: `Nexus.lnk` → `cmd.exe /c run-nexus-windows.cmd` →
  `.venv\Scripts\pythonw.exe -m nexus_linux.windows_app`.
- La pareja de procesos 7228/21560 correspondía al redirector del entorno virtual
  y a su intérprete hijo, no a dos monitores independientes. La ventana real era
  del hijo. El `.pth` editable apuntaba al `src` de este proyecto.
- SQLite: `%LOCALAPPDATA%\Nexus\data\nexus.sqlite`; `integrity_check=ok`.
  Primera lectura: History 1416, Pinboards 2, relaciones 0, Stack 2 pendientes
  de tipo texto; orden persistido LIFO. Los conteos pueden crecer por uso normal.
- Respaldo transaccional + fuentes + diff previo:
  `C:\Users\Admin\AppData\Local\Temp\opencode\nexus-deep-audit-20261024T015714Z`.
  Antes de la ventana autorizada se creó además `nexus-safe-window-20261024T021345Z`
  con `user-before.sqlite` íntegro (1420 clips, 2 pinboards, 2 pendientes).

## I. Captura en inactivo frente a restauración

`WindowsClipboardMonitor` se crea siempre: su responsabilidad es History.
`captured_versioned` llega a `WindowsStackWindow.add_capture`, que comprueba
`is_collecting`; el modelo también comprueba `active`. `load_persisted` restaura
IDs/orden con captura desactivada. La visibilidad de un pendiente no prueba que
fuera capturado en esa sesión.

Hay evidencia histórica concreta: los respaldos
`nexus-before-restart-20261023-192849.sqlite` y
`nexus-userdb-before-update-20261023-190911.sqlite` ya contenían la imagen
pendiente ID 1327 antes del reinicio. No se puede identificar universalmente la
imagen vista por el usuario con esos metadatos; sí se demuestra que existía una
imagen persistida susceptible de reaparecer. No hay trazas históricas suficientes
para demostrar una captura nueva mientras estaba inactivo. No se borra nada para
ocultar esta diferencia.

## II. Causa demostrada del pegado repetido

La ruta anterior de Ctrl+V era **observadora**, no transaccional:

```text
Copy externo → QClipboard.dataChanged → History.capture → captured_versioned
  → Stack.add(active) → available=(última copia, secuencia, armed=False)
Ctrl+V → WH_KEYBOARD_LL → CallNextHookEx (Windows pega portapapeles actual)
  → V-up → señal Qt → schedule_real_paste → 180 ms
  → consume available → SOLO arma siguiente si available.armed=True
```

Las capturas siempre usaban `armed=False`. Tras el primer consumo no había
ninguna escritura al portapapeles; el próximo Ctrl+V repetía el contenido antiguo.
Además la tarjeta FIFO podía mostrar A mientras el portapapeles contenía C.

Reproducción aislada de la implementación previa: copiar tres fixtures con IDs
1/2/3 dejó cabeza FIFO=1, portapapeles=3, available=3 y armed=False. Tras la primera
notificación quedaron [1,2], portapapeles=3 y cero escrituras. La segunda
notificación fue rechazada. Esto no depende de la hipótesis del tamaño de INPUT.

## III. Por qué el cierre anterior fue falso

1. Las 565 pruebas cubrían componentes, mocks, secuencias y timers inyectados;
   no demostraban la cadena de producción con eventos del teclado.
2. Un test incluso exigía `test_paste_consumes_last_copy_not_fifo_and_does_not_arm`:
   codificaba el comportamiento erróneo como contrato.
3. El runner Notepad invocaba `window.advance()` directamente. Esa ruta escribía
   el payload explícitamente antes de cada Ctrl+V sintético interno.
4. No creaba `WindowsPasteWatcher` ni `WindowsClipboardMonitor`.
5. Sus inputs tenían el marcador interno que el hook debe ignorar.
6. Creaba filas directamente en un SQLite temporal, no mediante copia externa.
7. Construir `WindowsStackWindow` desactivaba el modelo antes del loop: probaba
   pegados manuales one-shot inactivos, no una sesión secuencial habitual.
8. El test correcto faltante era: activar vía RegisterHotKey, copiar externamente
   A/B/C, enfocar receptor y enviar tres Ctrl+V por WH_KEYBOARD_LL sin llamar APIs
   internas, verificando texto y pendientes después de cada uno.

## IV. Concurrencia y límites de Windows

- Hook en hilo dedicado con message loop; nunca escribir clipboard/SQLite desde
  el callback. Entregar trabajo al hilo Qt mediante señales encoladas.
- La implementación anterior dejaba pasar autorepeat/pegados durante los 180 ms,
  antes de cambiar el portapapeles. Un temporizador mayor no arregla esa carrera.
- `SendInput` cuenta eventos encolados; no confirma inserción. UIPI puede bloquear
  inyección hacia mayor integridad sin diagnóstico específico del motivo.
- El callback de bajo nivel se ejecuta antes de actualizar el estado asíncrono
  de la tecla que está cambiando; debe considerar eventos de modificadores.
- Un control puede ignorar Ctrl+V o leer datos de forma asíncrona. La política de
  consumo debe ser automática y explícita, no prometer un acuse universal.

Fuentes oficiales consultadas:

- https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc
- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput
- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getasynckeystate

## V. Validación y estado de entrega

La nueva validación se registra por separado en los resultados del runner
`tools/windows_stack_hook_e2e.py`. Este inicia la composición **real**
`nexus_linux.windows_app` con `LOCALAPPDATA` temporal, usa los atajos registrados
y un marcador de input distinto de Nexus. No invoca `advance`, no escribe filas
de Stack directamente y no confunde inyección con teclado físico.

La ventana de prueba fue autorizada explícitamente. La instancia habitual se
detuvo mediante WM_QUIT al hilo Qt, tras backup íntegro, sin terminación forzada.
El monitor habitual debe permanecer detenido hasta cerrar el proceso de prueba
y restaurar el portapapeles, para no añadir fixtures a History real.

## VI. Arquitectura corregida y estados

```text
History: copia externa → QClipboard.dataChanged (GUI) → snapshot/version
  → SQLite History → señal versionada → gate de sesión → Stack.add si activo

Stack: Ctrl+V externo activo → hook dedicado bloquea V-down/up original
  → PasteRequest(event_id, FocusTarget, pressed_at) al liberar V
  → señal Qt encolada → cola acotada (16 esperando + una ejecutando)
  → seleccionar cabeza FIFO/LIFO AHORA, no usar available/clipboard anterior
  → validar sesión/generación/foco → escribir payload tipado y verificar marcador
  → esperar modificadores incompatibles con deadline → SendInput marcado Nexus
  → hook ignora ese marcador (sin recursión) → comprobar dispatch/foco/secuencia
  → estabilización acotada → persistir consumo del ID exacto → refrescar misma cola
```

No hay restauración del primer payload ni prearmado anticipado del siguiente.
Cada solicitud escribe su propio elemento antes del dispatch. La interfaz lee la
misma lista del modelo; el consumo no borra `clip_items` ni relaciones Pinboards.
Ante un fallo de persistencia se restaura también la membresía en memoria.

Estados derivados, con `WindowsClipStack.active` como fuente de verdad:

| Estado | Condición/transición |
| --- | --- |
| Inactivo/recuperado | Arranque restaura IDs/orden; captura e interceptación deshabilitadas. Pendientes recuperados se indican en el subtítulo. |
| Activo/capturando | Activación explícita/hotkey; nueva sesión y frontera de captura. No importa el clipboard antiguo. |
| Encolado | Solicitud física o inyectada externa válida; IDs repetidos/replays y cola excedida se rechazan. |
| Dispatch | Una sola operación GUI; valida destino sin forzar foco a una ventana anterior. |
| Estabilización | Mantiene el payload escrito hasta el control post-dispatch acotado. |
| Consumido | Mismo ID eliminado de Stack en SQLite y memoria; History permanece. Próximo request selecciona la nueva cabeza. |
| Fallo recuperable | No consume; vacía solicitudes incompatibles. Reintento solo por nueva acción del usuario. |
| Desactivado | Pausa, cierre/escape/toggle visible; invalida generación/cola. Ctrl+V vuelve a Windows. |
| Activo/vacío | Bloquea Ctrl+V secuencial para no repetir el último payload. Copiar nuevos elementos o desactivar restaura la acción esperada. |

El hook permanece instalado pero deshabilitado al pausar, para poder absorber el
key-up de un V previamente bloqueado. Su callback no escribe SQLite/clipboard.
Un fallo de registro desactiva Stack y se comunica a la UI. El input propio tiene
`NEXUS_INPUT_MARKER`; otros inputs inyectados pueden recorrer la misma ruta que
el teclado para accesibilidad y pruebas, sin fingir que sean físicos.

Ocultar por un pegado manual conserva actividad; el tray dice “Stack activo ·
oculto”. Cerrar/escape o el toggle del panel visible desactiva. Minimizar no
reactiva ni borra datos. El siguiente arranque siempre es inactivo.

Diagnósticos opt-in: `NEXUS_STACK_DIAGNOSTICS=1`; session/op/event/item/kind/order/
state/sequence/hwnd/result, sin texto ni imágenes. Startup registra hash de los
cuatro módulos del recorrido y PID para comprobar el código cargado.

## VII. Resultados observados hasta ahora

- Suite completa después de implementar y revisar: **593 passed**.
- Primer runner nuevo: corrigió su inicialización de sandbox (los imports de
  helpers ya creaban rutas). No llegó a copiar ni a arrancar el producto.
- Primera pasada de recorrido real: nueve grupos correctos; el check de archivo
  falló por comparar `C:\...` de pathlib contra `C:/...` de `QUrl.toLocalFile`.
  Se corrigió el assert del harness a identidad de ruta normalizada, no el
  producto, y no se actualizó la instancia habitual con esa ejecución incompleta.
- Pasada `nexus-real-hook-e2e-20261024-03`: **11 grupos PASS**, incluido FIFO en
  Win32 EDIT y Notepad, LIFO en Notepad, LIFO rápido Qt, autorepeat, cambio de foco,
  recuperación/inactividad después de reinicio, imágenes y archivos recibidos
  como MIME nativo por un receptor Qt. Foco y formatos expuestos restaurados;
  `OleFlushClipboard=0`. Datos y logs retenidos exclusivamente en sandbox.
- Entrada de esas pruebas: **SendInput con marcador de test distinto del de
  Nexus**, pasando por RegisterHotKey y WH_KEYBOARD_LL reales. **No equivale a
  validación de Ctrl+V físico por el usuario.**

## VIII. Cierre técnico observado

- Suite final completa (sin exclusiones): **600 passed**, 14,60 s.
- Runner ampliado `nexus-real-hook-e2e-20261024-04`: **13 grupos PASS**.
  El resultado está en
  `C:\Users\Admin\AppData\Local\Temp\opencode\nexus-real-hook-e2e-20261024-04\result.json`;
  DB, log de la aplicación real aislada y stderr se conservan en ese sandbox.
- Además de las pruebas anteriores: Ctrl derecho mantenido durante más de
  1,5 segundos con tres V distintos funciona sin soltar el Ctrl del usuario;
  reinicio preserva los IDs/orden de pendientes imagen+texto y sigue inactivo.
- Las pruebas de fallos ahora cubren rollback visible de add/clear/reorder/consume
  al fallar SQLite y limpieza parcial de input con aceptación 0/1. La limpieza
  se limita a teclas inyectadas y a tres intentos; un bloqueo del sistema puede
  impedir incluso key-up y se informa sin declarar éxito.
- Se verificó después de todas las pruebas que **cada fila original** de
  `clip_items`, `pinboards`, `pinboard_items`, `stack_state` y `stack_meta` seguía
  idéntica al backup anterior a la ventana. No se limpiaron IDs sintéticos
  históricos 1390,1391,1392,1404 ni la fila de origen no determinado.

### Instancia habitual cargada

Se arrancó **desde `run-nexus-windows.cmd` existente**, sin cambiar el acceso de
inicio, intérprete, bases ni configuración. Redirector PID 9416; GUI PID **11688**.
`nexus.log` registró hashes idénticos a los archivos finales:

| Módulo | SHA-256 |
| --- | --- |
| windows_app.py | `49e48b80b5da7a8c8e21f9ce1efe5f538a3184cf597fb2f9665b496899202cc5` |
| windows_keyboard.py | `93692b8ea664517b95f1e4250ff42d529b4a9df58dcad817d2ee88309b4326bb` |
| windows_stack.py | `63d540ad938a186655c6cc6976fbe4de57cbafe6da5c9a507733bdbadf8ebcd1` |
| windows_paste.py | `88c1651cb43689f02b554af328593d173640f58bab79a250502e4dc6591274ed` |

Startup: `active=False restored_ids=[1413,1414] count=2`, LIFO persistido;
retención `purged=0`. Después se envió **el atajo global real** Ctrl+Shift+C a
esa instancia: abrió el panel y UI Automation observó “Sesión activa · 2
elementos · 2 pendiente(s) recuperado(s)”. Repetir el atajo lo ocultó/desactivó;
se restauró el foco. No se copiaron fixtures ni se consumieron esos dos pendientes.

Tras esa comprobación, SQLite continuó `integrity_check=ok` y todas las filas
citadas seguían idénticas al respaldo (1420 clips, 2 pinboards, 0 relaciones,
2 pendientes). La verificación de versión/proceso y estado quedó en
`nexus-safe-window-20261024T021345Z\normal-runtime-verified.json`.

**Distinción de evidencia:** recorrido completo con copia, atajo, hook, clipboard,
receptor y persistencia = proceso real aislado con el mismo código/composición;
post-reinicio habitual = hashes/PID, arranque inactivo, apertura/desactivación
por atajo y preservación de todas las filas. No se afirma que el teclado físico
del usuario haya sido pulsado ni que se hayan consumido datos reales para probar.

## IX. Archivos de esta auditoría (no confundir con cambios previos)

- `windows_keyboard.py`: interceptación habilitable y snapshots de requests,
  V físico/inyectado externo frente a marcador propio, repetición, fallo de hook.
- `windows_stack.py`: estado activo del modelo, generación/sesión, serialización,
  consumo persistido exacto, rollback en memoria y pendientes recuperados en UI.
- `windows_paste.py`: API de request, validación de foco sin robarlo, espera
  acotada de modificadores incompatibles, preservación de Ctrl del usuario,
  despacho completo y limpieza acotada de input parcial.
- `windows_app.py`: wiring real, gate de capturas tardías entre sesiones,
  arranque verificable, estado del hook y etiqueta del tray.
- Pruebas adaptadas: `test_windows_stack_safety.py`,
  `test_windows_lifecycle_safety.py`, `test_windows_premium_integration.py`.
  La prueba visual cambió solo su secuencia de fixture a una copia posterior a
  activación; no se retiraron asserts visuales. Los contratos de observador que
  exigían el bug se sustituyeron por protección contra consumo sin transacción.
- Prueba nueva: `test_windows_stack_transactions.py`.
- Runner nuevo: `tools/windows_stack_hook_e2e.py`.
- Documentos: este informe y `WINDOWS_STACK_PASTE.md`.

No se modificaron en esta auditoría `windows_clipboard.py`, `windows_platform.py`,
las fuentes compartidas/Linux, las superficies Acrylic ni los instaladores.
Sus diffs preexistentes se preservaron. No se creó commit ni se mezcló trabajo
anterior con un supuesto commit de reparación.

## X. Límites y comprobación física pendiente

1. `SendInput` no demuestra inserción universal; el consumo usa dispatch validado
   y estabilidad acotada. Una aplicación puede ignorar el gesto; History conserva
   el original. Aplicaciones de mayor integridad pueden rechazarlo por UIPI.
2. El clipboard restaurado se comprobó formato por formato **de los expuestos
   por Qt** y `OleFlushClipboard` devolvió 0; esto no garantiza reconstruir
   formatos privados no expuestos ni el ownership original.
3. No se probaron formularios de navegador ni una aplicación elevada. No se
   cambió ninguna protección de Windows para intentarlo.
4. La última comprobación con Ctrl+V **físico** corresponde al usuario. Los dos
   pendientes existentes están preservados; no deben borrarse para probar.
   En el modo LIFO actual se pueden añadir tres fixtures nuevos y comprobar
   C/B/A sin consumir los dos antiguos, o utilizar otra sesión Windows/datos
   temporales para verificar FIFO sin tocar la cola personal. Desactivar al
   terminar y confirmar que Ctrl+V vuelve a ser convencional.

La actualización está cargada y la integración técnica está verificada. No se
declara certificada una pulsación física que no se ha observado.
