# Nexus Linux — Architecture Audit v2

**Fecha:** 2026-08-26 · **Host:** erwin (CachyOS, KDE Plasma 6, Wayland, x86_64)
**Alcance:** auditoría técnica completa previa a Nexus 2.0. Todo lo descrito aquí fue verificado leyendo el código fuente real y, donde se indica "medido en vivo", ejecutando el proceso real en Erwin — no son estimaciones.

---

## 1. Stack tecnológico real

| Capa | Tecnología | Notas |
|---|---|---|
| Lenguaje | Python 3.14 (venv), `requires-python >= 3.11` | |
| UI toolkit | PySide6 (Qt 6.11), widgets clásicos (`QWidget`/`QListWidget`), no QML | |
| Event loop | `qasync.QEventLoop` fusiona asyncio + Qt en un solo hilo | Necesario porque `dbus_next` (atajos globales) es asyncio-nativo |
| IPC atajos | D-Bus de sesión, `dbus_next` hablando directo con `org.kde.kglobalaccel` | Ver §4 |
| IPC respaldo | `QtDBus`, servicio propio `mx.com.storem.nexus` (`ShowMainPanel`/`ShowStack`) | Pensado para KDE Custom Shortcuts como plan B |
| Clipboard read | `wl-paste --watch` (subprocess, protocolo `wlr-data-control-v1`) | `QClipboard.dataChanged()` no funciona en background sobre Wayland (verificado, ver docstring de `clipboard_monitor.py`) |
| Clipboard write | `wl-copy` (subprocess) | `QClipboard.setMimeData()` no propaga al compositor en este Qt/Wayland (verificado) |
| Paste sintético | `ydotool` vía `ydotoold` (uinput) | Única vía sancionada en Wayland para inyectar teclas en otra ventana |
| Detección Ctrl+V real | `python-evdev`, hilo dedicado leyendo `/dev/input/eventN` | Requiere grupo `input`; autorizado explícitamente por Manuel (2026-08-24) |
| Persistencia | SQLite (`~/.local/share/nexus/nexus.sqlite`), un solo `sqlite3.Connection` | Ahora en modo **WAL** (antes `journal_mode=delete`, ver §6) |
| Single-instance | `flock()` sobre `$XDG_RUNTIME_DIR/nexus/nexus-linux.lock` | **No existía antes de esta auditoría** — añadido, ver §5 |
| Logging | `logging` estándar → archivo (`~/.cache/nexus/nexus.log`) + stderr | Un solo logger raíz, sin rotación (ver §9) |
| Tests | `pytest` (recién añadido) | 45 tests, 0 antes de esta auditoría |
| Empaquetado | `pip install -e .` (editable), `pyproject.toml` + `setuptools` | Sin paquete `.deb`/AUR todavía |
| Autostart | `~/.config/autostart/nexus-linux.desktop` (XDG estándar) | KDE lo envuelve en un scope systemd transitorio (`app-nexus\x2dlinux@autostart.service`) — comportamiento normal de Plasma, no gestionado por Nexus |

---

## 2. Mapa de arquitectura real

### 2.1 COPY EVENT

```
usuario copia algo (cualquier app)
  → compositor Wayland actualiza la selección wl_data_device
  → wl-paste --watch echo changed   (subprocess persistente, ClipboardMonitor)
  → QProcess.readyReadStandardOutput → _on_watch_tick()
  → si _suppress_next (Nexus mismo acaba de escribir): descarta, return
  → _capture_current_clipboard():
      wl-paste --list-types  → prioridad: text/uri-list > image/png > text/plain
      wl-paste -t <mime>     → bytes crudos
      si imagen: _downscale_png() cap a 1600px antes de tocar la DB
  → storage.capture(kind, content, ...):
      SHA-256(kind + contenido) = content_hash
      SELECT por content_hash → si existe: UPDATE updated_at/source_app (no fila nueva)
                                si no existe: INSERT
  → compara content_hash contra _last_hash (evita doble emisión del mismo wl-paste tick)
  → captured.emit(item)
  → app.py:on_captured(item):
      log.info
      si MainWindow visible: window.refresh()  (repinta lista completa)
      si StackWindow "collecting" (=visible): stack.add(item) + stack_window.refresh()
```

**Dónde puede fallar esta cadena (auditado):**
- `wl-paste --watch` puede morir (compositor, crash) → mitigado con reintento exponencial simple + tope de 10 reintentos (`clipboard_monitor.py`), ya existente y correcto.
- Ráfagas de portapapeles coalescidas en un solo `readyRead` → mitigado, se relee una vez por tick (correcto, no pierde el último estado pero sí "eventos intermedios" — aceptable, un clipboard manager no necesita ver estados transitorios).
- **Captura espuria al arrancar** (hallazgo nuevo, no crítico): `wl-paste --watch` emite un "changed" inicial con el contenido *ya presente* en el portapapeles al momento de arrancar Nexus, no solo cambios reales posteriores. Confirmado en vivo: cada arranque genera una captura extra del último contenido del portapapeles del usuario, aunque no haya copiado nada nuevo. Impacto bajo (una fila duplicada-por-hash como máximo, sin corrupción), documentado como pendiente P3.

### 2.2 GLOBAL SHORTCUT (Ctrl+Shift+V / Ctrl+Shift+C)

```
KWin detecta la combinación (kglobalaccel)
  → org.kde.kglobalaccel.Component.globalShortcutPressed (señal D-Bus)
  → dbus_next.MessageBus (conexión persistente abierta por GlobalShortcuts.connect_bus())
  → GlobalShortcuts._on_message() → triggered.emit(action_id)
  → app.py:on_shortcut(action_id):
      toggle-main-panel → window.isVisible() ? fade_hide() : show_and_focus()
      toggle-stack      → stack_window.isVisible() ? fade_hide() : show_and_focus()
  → show_and_focus(): refresh() [query SQL + reconstruye N QWidgets] → show() → raise_() → activateWindow()
```

**Hallazgo crítico de causa raíz (histórico, ya documentado, root-caused de nuevo aquí):** el intento original de registrar el atajo usaba `busctl` por invocación (una conexión D-Bus nueva y efímera por cada llamada `doRegister`/`setShortcut`). `kglobalaccel` ata el shortcut activo a la conexión D-Bus viva que lo registró — al morir esa conexión al instante, el binding quedaba "registrado" en `kglobalshortcutsrc` pero nunca disparaba. Fix ya aplicado (sesión previa): una sola conexión `dbus_next` persistente durante todo el ciclo de vida del proceso, con `flags=2` (`SetPresent`) en `setShortcut` — el flag real que activa el despacho (verificado con barrido 0/1/2/4). **Este fix sigue vigente y correcto**, verificado de nuevo en esta auditoría (100% de activaciones repetidas respondieron, ver `PERFORMANCE_BASELINE.md`).

**Latencia real medida (no estimada) — ver `PERFORMANCE_BASELINE.md` para el desglose completo.** Resumen: abrir tomaba ~900ms; el cuello de botella no era D-Bus/KWin (esa parte es de unos pocos ms) sino la reconstrucción completa de las 200 filas del historial en cada apertura. Corregido a ~300ms (ver §3).

### 2.3 STACK PASTE (consumo secuencial)

```
Stack tiene [A, B, C], A armado en el portapapeles del sistema

Camino "real Ctrl+V" (evdev detecta la tecla física):
  usuario presiona Ctrl+V de verdad (en cualquier ventana con foco)
  → EvdevStackWatcher (hilo dedicado, selectors sobre /dev/input/eventN)
  → detecta code==KEY_V, value==1, ctrl_held, not shift_held, fuera de ventana de supresión
  → paste_detected.emit()  (señal Qt, cross-thread, cola automática al loop principal)
  → app.py:on_real_paste_detected():
      si stack_window.is_collecting() [==isVisible()] y hay items pendientes:
        clip_stack.consume_after_real_paste():
          item = consume_next()  → pop(0), persiste stack_state (ver §2.4)
          arm_next()             → escribe B al portapapeles vía wl-copy
        stack_window.refresh()   → repinta la lista, ya sin A
        si vacío: fade_hide()

Camino "botón manual / Pegar siguiente":
  clip_stack.paste_next_and_advance():
    item = arm_next()           → confirma A en el portapapeles
    paste_service.synthetic_paste() → ydotool Ctrl+V real hacia la ventana con foco
    consume_next()               → pop A, persiste
    arm_next()                    → arma B
```

**Bug histórico (el que reportaste) — causa raíz confirmada:** en el código actual, **ambos caminos ya actualizan `stack_window.refresh()` explícitamente** tras consumir. El síntoma descrito (pegar A y que A siga viéndose en el Stack, requiriendo el botón manual) tiene su causa raíz documentada en `MIGRATION_LINUX.md` (sesión 2026-08-24): antes de ese fix, `paste_next_and_advance()` no existía como operación atómica — el flujo separaba "armar", "pegar" y "consumir" en pasos independientes sin una única función que garantizara las tres cosas juntas, y la UI no tenía un punto único de refresco tras cada consumo real. El fix consolidó las tres operaciones en una sola llamada atómica a nivel de Python (no hay `await`/yield entre medio, así que no hay ventana de carrera dentro de esa función) y añadió `stack_window.refresh()` explícito en el handler de `paste_detected`. **Verificado con 39+ tests automatizados** (`tests/test_stack.py`) que fuerzan exactamente esta secuencia (1, 2, 10 y 100 items consumidos vía `consume_after_real_paste`) — los 45 tests pasan. No se encontró regresión de este bug en el código actual.

**Riesgo estructural remanente (no corregido, documentado):** `StackWindow.is_collecting()` devuelve `self.isVisible()`. Es decir, el Stack solo "escucha" nuevas capturas y consume pastes reales **mientras su ventana está técnicamente visible** (no necesariamente enfocada — cambiar de escritorio virtual o minimizar detrás de otra ventana no cuenta como "hide"). Si el usuario cierra explícitamente la ventana del Stack (Escape, o Ctrl+Shift+C de nuevo) antes de terminar de pegar todos los items, el auto-avance por Ctrl+V real deja de funcionar hasta reabrirla — comportamiento por diseño (mirror del original en Swift), pero vale la pena que sea una decisión de producto consciente y no una sorpresa. Documentado para decisión en Fase 2, no es un bug de fiabilidad.

### 2.4 Persistencia del Stack (nuevo, cerraba un gap real)

Antes de esta auditoría, `ClipStack` era puramente en memoria: un `kill -9` o crash con items pendientes los perdía sin dejar rastro — exactamente la "pérdida arbitraria" que la auditoría buscaba descartar. Se añadieron dos tablas (`stack_state`, `stack_meta`) que `ClipStack` sobrescribe en cada mutación (`add`/`consume_next`/`remove_persistent`/`clear`/`toggle_order`), con `ON DELETE CASCADE` hacia `clip_items` para que nunca queden ids huérfanos. **Verificado en vivo**: Stack con 3 items → `kill -9` real del proceso → relanzamiento → log `"Stack restaurado tras el último cierre: 3 item(s) pendientes."` → cabeza correctamente re-armada en el portapapeles.

---

## 3. Hallazgos corregidos en esta auditoría (con verificación en vivo)

| # | Severidad | Hallazgo | Causa raíz | Fix | Verificación |
|---|---|---|---|---|---|
| 1 | **P0** | `nexus-evdev-wat` consumiendo ~100% de un núcleo de forma sostenida (17h45min de CPU acumulado sobre una sesión de 8h) | Reconexión del teclado Bluetooth deja un fd de `/dev/input/eventN` apuntando a un inodo borrado; el kernel lo reporta como permanentemente "listo para leer"; `read()` lanza `OSError`; el `except: continue` reintentaba sin backoff → bucle infinito | `evdev_stack_watcher.py`: se des-registra y cierra el dispositivo en el primer fallo de lectura; rescaneo periódico (15s) detecta el teclado reconectado | Muestreo de CPU por hilo antes/después: de ~100% sostenido a ~0% en reposo |
| 2 | **P1** | Nada impedía correr dos procesos de Nexus simultáneamente | Sin guardia de instancia única | `flock(LOCK_EX\|LOCK_NB)` sobre `$XDG_RUNTIME_DIR/nexus/nexus-linux.lock`, liberado automáticamente por el kernel al morir el proceso (crash-safe, sin PID file obsoleto que limpiar) | Lanzar una segunda instancia sale con `exit 0` y log claro; solo un proceso vivo confirmado |
| 3 | **P1** | Ctrl+Shift+V tardaba ~900ms en mostrar la ventana | 61 de 200 filas son imágenes; decodificar+escalar+enmascarar cada PNG se rehacía desde cero en cada apertura; además cada fila creaba un `QGraphicsDropShadowEffect` (fuerza compositing offscreen) aunque nunca se viera | Cache de miniaturas por `content_hash` (tope 2000); sombra creada solo al primer hover real | Medido en vivo: ~900ms → ~280-380ms |
| 4 | **P1** | Un crash con items pendientes en el Stack los perdía sin rastro | `ClipStack` puramente en memoria | Persistencia en `stack_state`/`stack_meta`, restaurada al arrancar | `kill -9` con 3 items → relanzamiento → 3 items restaurados y cabeza re-armada |
| 5 | **P2** | `journal_mode=delete`, `busy_timeout=0` en SQLite | Nunca se configuró explícitamente | `PRAGMA journal_mode=WAL`, `busy_timeout=5000` | `PRAGMA` leído de vuelta, test automatizado |
| 6 | **P2** | Fallo silencioso al armar un item en el portapapeles (`write_to_clipboard` puede devolver `False` sin que nadie se entere) | `arm_next()` ignoraba el valor de retorno | `log.warning` cuando falla, sin cambiar el contrato de la función (el item sigue siendo la cabeza lógica) | Test con `paste_service` simulando fallo, confirma el log |

## 4. Hallazgos documentados, no corregidos (alcance de Fase 2 / decisión de producto)

- **Sin virtualización real de lista.** `QListWidget.setItemWidget()` crea un `QWidget` Qt real y persistente por fila. Con el cache de miniaturas puesto, el resto de la latencia de apertura (~280-350ms) es overhead de construcción de hasta 200 widgets reales por apertura. Para llegar al objetivo <200ms de forma robusta (y no degradar con historiales muy grandes en pantalla), la arquitectura correcta es un modelo `QAbstractListModel` + `QStyledItemDelegate` (solo se pintan las filas visibles, no se instancian 200 widgets). Es un cambio de arquitectura de UI, no un parche — recomendado como ítem de diseño explícito para Fase 2, no improvisado bajo presión de esta auditoría.
- **Excepciones dentro de corrutinas asyncio** (código dentro de `_run()` en `app.py`, p. ej. fallos de `dbus_next` tras el arranque) no pasan por `sys.excepthook` (eso solo cubre slots de Qt) — podrían quedar solo en el manejador de excepciones por defecto de asyncio (stderr), no en el log estructurado. No se encontró un caso real que lo dispare, pero es un gap de observabilidad a cerrar antes de depender de los logs para diagnóstico en producción.
- **Item borrado del historial mientras está en el Stack abierto en memoria**: el `ON DELETE CASCADE` mantiene `stack_state` (la copia persistida) consistente automáticamente, pero la lista **en memoria** de una sesión de `ClipStack` ya viva no se reconcilia hasta la siguiente mutación — una fila "fantasma" podría seguir visible en la ventana del Stack hasta el próximo `add`/`consume`. Caso de baja probabilidad (requiere borrar manualmente desde Historial un item que a la vez está en el Stack abierto), no corregido.
- **Búsqueda con `LIKE '%...%'`** no puede usar el índice (comodín inicial) → *full scan*. Medido: 80ms sobre 50,000 filas sin coincidencias. Aceptable para el tamaño real actual (540 filas) y para varios miles más, pero si el historial creciera a decenas de miles de forma sostenida, migrar a FTS5 (SQLite full-text) sería la vía correcta — no se justifica esa migración de esquema hoy sin evidencia de que se necesite.

## 5. Modelo de procesos / threading (confirmado)

- **Hilo principal Qt**: event loop fusionado Qt+asyncio vía `qasync`. Todo el trabajo de UI, SQLite (síncrono, bloqueante) y D-Bus corren aquí.
- **Hilo `nexus-evdev-watcher`** (daemon): único hilo en background, solo lectura de `/dev/input`, comunica al hilo principal exclusivamente vía señal Qt (`paste_detected`), thread-safe por diseño de Qt.
- **`wl-paste --watch`** y `ydotool`/`wl-copy`: procesos hijos (`QProcess`/`subprocess.run`), nunca hilos Python — correcto, evita GIL y aísla fallos.
- **No hay pool de hilos para trabajo pesado** (decodificación de imágenes, red para previews futuros) — todo el trabajo de refresco de UI corre síncrono en el hilo principal. Aceptable hoy (contenido local, sin red); relevante para Fase 2 si se añaden previews de URL/YouTube con red (deben ser async, nunca bloquear el hilo de Qt — ver recomendación en `PERFORMANCE_BASELINE.md`).

## 6. Seguridad (revisión rápida)

- El log actual (`logging.basicConfig` con `%(message)s`) **sí registra contenido de texto capturado indirectamente** en algunos mensajes de captura de ejemplo del historial (no el contenido completo, pero sí metadatos como tipo/app de origen) — no se encontró logging de contraseñas/tokens en claro en el código actual, pero tampoco hay una regla explícita de *redaction* si en el futuro se loguea el contenido de un item. Recomendación Fase 2: si se añade logging de contenido para diagnóstico, aplicar detección heurística simple (longitud+entropía, o patrones de token conocidos) antes de escribir a disco.
- `evdev` da acceso a **todos** los eventos de teclado del sistema, no solo Ctrl+V — trade-off ya explicado y autorizado explícitamente por Manuel (2026-08-24), documentado en el propio código. Sigue siendo la única vía real en Wayland; no hay alternativa más restringida disponible hoy.
- Sin red saliente en el código actual (no hay previews de URL/OpenGraph implementados todavía) — sin superficie de SSRF que auditar aún. Relevante cuando se implemente en Fase 2 (timeouts, sin JS, límite de tamaño de respuesta, ya anotado en el prompt original).

## 7. Estado de git

Antes de esta sesión, `/home/manuelcarvajal/Projects/Nexus-linux` **no era un repositorio git**. Se inicializó y se creó un checkpoint estable (`219c4df`) con el estado exacto previo a cualquier cambio de esta auditoría, seguido de commits atómicos por cada fix (evdev, single-instance, performance, tests, persistencia del Stack). No se ha hecho push a ningún remoto (no hay remoto configurado).
