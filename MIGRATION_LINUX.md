# Migración de Nexus a Linux (Erwin)

Estado: 2026-08-24. Core funcional, probado por Manuel en Erwin real (CachyOS, KDE Plasma 6, Wayland). Nexus Stack con UI propia. Rediseño visual aplicado (tema "midnight teal", ver `styles.py`).

## Origen

- `Nexus-original` (macOS): copia íntegra e intocada de `~/Documents/Nexus`, Swift + SwiftUI/AppKit + Core Data. Ver ese repo para el diseño original.
- `Nexus-linux`: reescritura independiente, Python 3 + PySide6 (Qt6). No comparte código fuente con el original (SwiftUI/AppKit/Core Data no existen en Linux), pero replica su diseño: modelo de datos, dedup SHA-256, retención de 30 días, Stack FIFO/LIFO.

## Por qué no se "portó" código literalmente

23 de 24 archivos Swift del original importan AppKit, SwiftUI, Core Data o Carbon/CGEvent. Código Swift reutilizable tal cual: ~0%. Lo que se reutilizó es el diseño (ver `storage.py`, que replica el esquema y la regla de captura de `StorageService.swift`).

## Arquitectura Linux

| Pieza | macOS (original) | Linux (esta versión) |
|---|---|---|
| UI | SwiftUI/AppKit | PySide6 (Qt6 widgets) |
| Persistencia | Core Data → SQLite | SQLite directo (`sqlite3` stdlib) |
| Ruta de datos | hardcode `~/Documents/Nexus/data` | XDG: `~/.local/share/nexus/nexus.sqlite` |
| Monitoreo de portapapeles | `NSPasteboard` polling 0.5s | `wl-paste --watch` (ver hallazgo #1) |
| Escritura al portapapeles | `NSPasteboard` | `wl-copy` (ver hallazgo #2) |
| Paste sintético | `CGEvent` (Cmd-V) | `ydotool` (Ctrl+V) vía `ydotoold` |
| Tray | `NSStatusItem` | `QSystemTrayIcon` |
| Shortcuts globales | Carbon hotkeys | `kglobalaccel` vía D-Bus (ver hallazgo #3) |
| Autostart | `SMAppService` | XDG autostart (`~/.config/autostart/*.desktop`) |
| Launcher | `.app` bundle | `.desktop` en `~/.local/share/applications` |

## Hallazgos técnicos (verificados en vivo contra Erwin, no supuestos)

### 1. QClipboard no recibe cambios hechos por otras apps en Wayland

Wayland, por diseño de seguridad, solo entrega eventos de `wl_data_device` (selección de portapapeles) al cliente que tiene el foco de teclado en ese momento. Un gestor de portapapeles en segundo plano casi nunca tiene foco, así que `QClipboard.dataChanged()` de Qt6 simplemente nunca dispara para copias hechas en otras apps. Verificado: copié texto con `wl-copy` mientras Nexus corría con `QClipboard` + polling de respaldo cada 2s — cero capturas.

**Solución**: `wl-paste --watch` usa el protocolo `wlr-data-control-v1`/`ext-data-control-v1`, diseñado explícitamente para herramientas privilegiadas de portapapeles (gestores de historial), sin depender del foco. Es la vía correcta y sancionada — no hay que desactivar ninguna protección de Wayland. `ClipboardMonitor` ahora lanza `wl-paste --watch echo changed` como subproceso y, en cada tick, hace `wl-paste --list-types` + `wl-paste -t <mime>` para clasificar y leer el contenido.

### 2. QClipboard tampoco escribe correctamente en este Qt6.11/PySide6

Aislado con tres pruebas progresivas: `QApplication` sin ventana, con ventana visible, con ventana visible+activada — en ningún caso `clipboard.setText()`/`setMimeData()` propagó al compositor (`wl-paste` seguía devolviendo el contenido anterior). Esto no es un problema de foco/serial de nuestro código; parece un hueco real en el backend Wayland de Qt 6.11 para escritura.

**Solución**: `PasteService.write_to_clipboard` usa `wl-copy` (subproceso) en vez de `QClipboard`. Verificado funcionando: escribir vía `wl-copy` y leer inmediatamente con `wl-paste` refleja el cambio correctamente.

### 3. La API D-Bus de kglobalaccel no es la que sugiere la documentación superficial

Reverse-verificado con `busctl --user introspect org.kde.kglobalaccel /kglobalaccel` y `/component/<id>`:
- Los ids de componente se vuelven segmentos de ruta D-Bus → rechazan `-`; usar solo `_`.
- `setShortcut` vive en `/kglobalaccel` (firma `as ai u → ai`, tomando enteros Qt de `QKeySequence`), no en el objeto del componente — ese solo expone `globalShortcutPressed/Released/Repeated` y helpers de introspección.
- `QDBusInterface.call()` de PySide6 auto-serializa una lista de enteros Python como `av` (variant array) y un entero plano como `i`, no como `ai`/`u` que exige el método real — no hay forma limpia de forzar esos tipos con `.call()` genérico, así que se usa `busctl` (subprocess) para las dos llamadas tipadas.
- Para conectar la señal `globalShortcutPressed`, `QDBusConnection.connect(..., receiver, "slot(Types)")` requiere el string envuelto con el helper `PySide6.QtCore.SLOT(...)` — un string crudo pierde el primer carácter (Qt lo interpreta como el prefijo de código de tipo del macro `SLOT()`/`SIGNAL()` de Qt clásico).

**Estado real tras prueba humana (2026-08-24): NO funciona.** Manuel probó `Meta+Shift+V` (la tecla Meta es la tecla Windows/⊞) y varias combinaciones más — ninguna abrió el panel. Investigación adicional:
- El archivo `~/.config/kglobalshortcutsrc` sí queda con la entrada correcta (`toggle-main-panel=Meta+Shift+V,none,...`), o sea el *binding* se guarda bien.
- Pero `busctl --user introspect org.kde.kglobalaccel /component/nexus_linux` falla con "No such object path", incluso *mientras* el proceso que acaba de registrarlo sigue vivo y conectado — probado con `busctl` (proceso de un solo uso, se desconecta al terminar) y también con una conexión D-Bus persistente propia vía `dbus-next` (librería Python pura, sin necesidad de headers de sistema) que se queda corriendo con un loop `asyncio` — mismo resultado en ambos casos. `allComponents` sí lista `/component/nexus_linux`, pero cualquier llamada directa a ese path individual falla. No se logró aislar la causa exacta en el tiempo disponible; podría ser una particularidad de esta versión de `kglobalaccel` en CachyOS.
- **No bloqueante**: se comprobó que el ícono de bandeja SÍ funciona (confirmado por Manuel, prueba manual 1) y es la vía de apertura confiable. Se agregó además un atajo local `Ctrl+Shift+C` (solo funciona con una ventana de Nexus ya enfocada, no es un atajo global de sistema) para saltar directo a Nexus Stack.
- **Alternativa recomendada si Manuel quiere un atajo de teclado real**: usar el editor nativo de KDE (Ajustes del Sistema → Atajos → Atajos personalizados) para crear un atajo que ejecute un comando — esa vía sí es 100% confiable porque pasa por la UI real de KDE en vez de la API D-Bus de bajo nivel. Pendiente de implementar si lo pide.

## Dependencias instaladas en Erwin

Todo sin `sudo` (ver Fase 12 del encargo — se evitó bloquear la migración):
- `PySide6` 6.11.2 — instalado en un venv dedicado (`Nexus-linux/.venv`), no a nivel de sistema. `qt6-base`/`qt6-tools` del sistema ya estaban presentes; PySide6 trae sus propios plugins Qt (incluido `libqwayland.so`), así que ni siquiera hizo falta el paquete `qt6-wayland` del sistema.
- `ydotoold` (servicio de usuario, ya venía instalado como paquete `ydotool`): habilitado con `systemctl --user enable --now ydotool.service`. No necesitó sudo — el ACL de `/dev/uinput` ya daba `rw-` a `manuelcarvajal` (probablemente vía regla udev de CachyOS con tag `uaccess` para la sesión activa).
- `wl-clipboard` (`wl-copy`/`wl-paste`) — ya venía instalado.

Nada quedó pendiente de sudo.

## Estado funcional verificado (código + prueba humana real de Manuel, 2026-08-24)

- ✅ Arranca sin crash, estable, sobrevive reinicios (autostart confirmado: el log muestra arranques automáticos en sesiones nuevas).
- ✅ Captura de texto/imagen en tiempo real.
- ✅ Escritura al portapapeles + historial persistente — **confirmado por Manuel** (prueba manual 1: copiar "uno"/"dos", reabrir Nexus, ambos aparecen).
- ✅ Paste real (`ydotool`) dentro de una app enfocada — **confirmado por Manuel** (prueba manual 2: pegar funciona).
- ✅ Ícono de bandeja + lanzador en el menú de aplicaciones de KDE — **confirmado por Manuel** (prueba manual 4). Verificado también por D-Bus: el tray expone correctamente `org.kde.StatusNotifierItem` con `Activate` funcional.
- ✅ Nexus Stack: lógica FIFO/LIFO + ventana propia (`StackWindow`), abre desde el menú de la bandeja ("Nexus Stack") o `Ctrl+Shift+C` con Nexus enfocado. Recolecta capturas mientras está abierta, botón "Pegar siguiente", clic derecho para eliminar, toggle FIFO/LIFO.
- ✅ Rediseño visual: tema oscuro "midnight teal" (adaptado de `Material3Theme.swift` del original) vía `styles.py` — fondo azul-marino, cards redondeadas, acento cian, tipografía consistente. Verificado visualmente con captura de pantalla de la ventana real.
- ❌ Atajo global `Meta+Shift+V` — **confirmado roto por Manuel** (prueba manual 3). Ver hallazgo #3 arriba para el detalle de la investigación.
- ⏳ Purga de retención (30 días) — implementada, corre cada 6h, no probada aún con datos viejos reales (no ha pasado suficiente tiempo).
- ✅ **Pinboards** (`pinboard_sidebar.py`): tableros con nombre + color/emoji, un mismo clip puede vivir en varios a la vez (igual que `Pinboard.swift` — no es un simple booleano "pinned"), exentos de la purga de 30 días. Sidebar de 160px con "Historial" + lista de boards, botón "+" para crear, clic derecho para renombrar/cambiar ícono/eliminar. Desde un clip: clic derecho → "Agregar a pinboard" (checkbox por board). Probado en vivo: creación, membresía, filtrado por board, y exención de retención — los 4 verificados por código, más confirmación visual de la UI.
- **2026-08-24, segunda vuelta tras feedback de Manuel**: el "+" original era un botón de 24px casi invisible (estilo `flat`, texto gris apagado) y todo dependía de clic derecho, sin ninguna pista de que existiera. Rehecho: botón "+ Nuevo pinboard" grande y con color de acento al fondo del sidebar, texto de ayuda visible ("Clic derecho en un pinboard para editar, mover o eliminar"), y en la lista principal dos botones explícitos "📌 Agregar a pinboard" / "Eliminar" (se habilitan al seleccionar un item) — así ya no depende solo de saber hacer clic derecho. Se agregó también reordenar boards (Mover arriba/abajo en el menú contextual), que antes no existía en absoluto.
- **Simplificado vs. el original** (documentado, no pendiente): sin drag-and-drop para arrastrar clips a un board (se usa el botón/menú "Agregar a pinboard" en su lugar), y el editor de ícono usa una lista de emojis sugeridos en vez del selector completo de 20 colores + grid. Si los quieres, se agregan después.

## Seguridad / datos

- El historial de clips de macOS (`Nexus-original/data`, 182M) **no se transfirió** a Erwin — es información potencialmente sensible (Fase 10/11 del encargo). Erwin arranca con una base de datos vacía y construye su propio historial desde cero.
- Ningún contenido de portapapeles se envió a servicios externos ni se procesó con IA en este trabajo — solo código y metadatos de longitud/tipo se usaron para depuración (ej. `length(image_data)`), nunca el contenido en texto plano de nada sensible.
- **Nota 2026-08-24**: para verificar visualmente el diseño se usan capturas de pantalla remotas (`spectacle -a`, ventana activa) en Erwin. Manuel confirmó que esto no es un problema — su historial real de portapapeles puede aparecer en esas capturas durante la verificación. Aun así, cada captura se borra del scratchpad inmediatamente después de revisarla (no se conservan ni se comparten).

## Rollback

Ver `uninstall.sh` — elimina el launcher, el autostart y el venv. **Nunca** toca `~/.local/share/nexus` (tu historial) salvo que pases `--purge-data` explícitamente. El código fuente en `~/Projects/Nexus-linux` tampoco se toca; para quitarlo del todo, `rm -rf ~/Projects/Nexus-linux` en Erwin.

## 2026-08-24 — Sesión de paridad funcional: atajos globales, Stack, terminales

### Atajos globales: causa raíz encontrada y arreglada de verdad

La sesión anterior dejó los atajos globales sin resolver. Esta vez se aisló la causa raíz real observando tráfico D-Bus en vivo (`busctl --user monitor org.kde.kglobalaccel`) mientras Nexus arrancaba:

- `doRegister` llegaba desde la conexión `:1.416`, `setShortcut` desde `:1.417` — **dos conexiones D-Bus distintas**, ambas cerradas milisegundos después (cada una era un proceso `busctl` de un solo uso). kglobalaccel ata la vida del objeto del componente a la conexión que lo registró; al cerrarse, el atajo deja de disparar aunque la config quede escrita en `kglobalshortcutsrc`.
- **Fix**: se reescribió `global_shortcuts.py` para usar una conexión D-Bus persistente (librería `dbus_next`, pura Python, con tipado explícito de argumentos D-Bus — `PySide6.QtDBus` no permite enviar `ai`/`u` correctamente desde Python) que vive mientras la app corre, integrada al loop de Qt vía `qasync` (ver `app.py`).
- Al probar el fix, el componente `nexus_linux` (nombre reciclado de sesiones de depuración anteriores) **seguía inalcanzable** incluso con la conexión persistente viva. Prueba de control: un componente con nombre nuevo (`nexus_probe_test`), registrado exactamente igual, funcionó al instante. Conclusión: el nombre `nexus_linux` tenía estado corrupto acumulado (varios crashes/reconexiones de sesiones previas). **Se renombró el componente a `nexus_linux_app`** para partir limpio, en vez de pelear con el estado interno de kglobalaccel.
- Verificado con `busctl --user call .../component/nexus_linux_app ... allShortcutInfos`: ambos atajos (`Ctrl+Shift+C`→Stack, `Ctrl+Shift+V`→Historial) quedan correctamente registrados y kglobalaccel los reporta como "no disponibles" para nadie más (es decir, reservados por Nexus).

### El registro funciona; el disparo real depende de qué app tiene el foco

Prueba de control con `ydotool`: simular `Ctrl+Alt+Space` (atajo real de tu asistente ERWIN, ya verificado por ti físicamente) SÍ generó la señal `globalShortcutPressed` de kglobalaccel — confirma que `ydotool` puede disparar atajos globales reales en este sistema. La misma prueba con `Ctrl+Shift+C`/`Ctrl+Shift+V` **no generó ningún tráfico hacia kglobalaccel en absoluto** — ni siquiera llegó a evaluarse como candidato a atajo global.

Conclusión: **Warp (o la app que tenga el foco) está consumiendo esas combinaciones localmente antes de que el compositor las ofrezca como atajo global** — exactamente el escenario que describiste. Esto no es un bug de Nexus ni de kglobalaccel; es el comportamiento esperado de cualquier terminal que define Ctrl+Shift+C/V como sus propios atajos de Copiar/Pegar. La solución es la que pediste: reasignar esos atajos en las terminales (ver abajo).

### Terminales: Warp y Konsole

- **Warp**: no se encontró ningún archivo de configuración de teclado editable de forma segura (`~/.config/warp-terminal/settings.toml` y `user_preferences.json` no tienen sección de keybindings; su estado vive parcialmente en `~/.local/state/warp-terminal/warp.sqlite`, sin tabla de atajos). No hay una vía documentada/segura para automatizar este cambio sin arriesgar corromper su configuración. **Requiere que lo cambies tú desde Warp → Settings → Keyboard Shortcuts** (ver instrucciones en la respuesta final).
- **Konsole**: nunca ha sido personalizado en esta cuenta (no existe `~/.local/share/kxmlgui5/konsole/`, ni sección `[Shortcuts]` en `~/.config/konsolerc`). Los atajos de copiar/pegar del **contenido de la terminal** (distintos de los atajos del menú de la ventana) pertenecen a la parte KPart de la sesión, no expuesta de forma introspectable por D-Bus de forma confiable. Adivinar el nombre interno de la acción y escribirlo a mano arriesgaba corromper la config (instrucción explícita tuya de no hacerlo) — **mismo criterio: requiere que lo cambies tú desde Konsole → Configurar Konsole → Atajos de teclado**.

### Nexus Stack: consumo secuencial

Ver `PARITY_MAC_LINUX.md` para el detalle completo (por qué el monitor global de teclado de macOS no tiene equivalente seguro en Wayland, y la solución práctica implementada: `Ctrl+Shift+C` repetido pega-y-avanza cuando el Stack tiene items). Cambios de código en esta sesión:

- `ClipStack.add()` ahora deduplica por `content_hash` **dentro de la misma sesión de recolección**, igual que `collectLatestCopy()` en `PasteStack.swift` — y arma automáticamente el primer item en el portapapeles al agregarlo (antes había que pulsar "Pegar siguiente" incluso para el primero).
- Nuevo `ClipStack.paste_next_and_advance()`: arma → pega sintéticamente (`ydotool`) → consume → arma el siguiente, todo en una llamada.
- `StackWindow`: botón "Eliminar del Stack" ahora visible (antes solo en clic derecho), "Vaciar Stack" explícito, la ventana se cierra sola al vaciarse (igual que `consumeAfterPaste()` → `close()` en el original).
- **Bug de condición de carrera encontrado y corregido**: `wl-copy` tarda un instante en registrarse como dueño del portapapeles vía su proceso en segundo plano; llamarlo dos veces seguidas sin esperar (exactamente lo que hace `paste_next_and_advance`) podía dejar el portapapeles con contenido viejo. `PasteService.write_to_clipboard` ahora verifica con `wl-paste` (reintentando ~180ms máx) que el contenido realmente cambió antes de continuar.
- **Hallazgo relacionado, no resuelto en código**: Klipper (gestor de portapapeles nativo de KDE) está activo en Erwin y puede competir por la propiedad del portapapeles Wayland. Ver instrucciones de desactivación en `PARITY_MAC_LINUX.md`.

### La causa real: faltaba el flag `SetPresent` en `setShortcut`

Tras reportar que ni siquiera en el escritorio (sin ninguna app interceptando) funcionaban los atajos, se investigó más a fondo con una prueba sistemática: registrar el mismo atajo de prueba (`Ctrl+Shift+F9`, garantizado sin conflicto) variando el último parámetro (`flags`) de `setShortcut` — `0, 1, 2, 4` — y verificar con un listener de la señal `globalShortcutPressed` más una pulsación real vía `ydotool` cuál realmente disparaba.

**Resultado**: solo `flags=2` (`SetPresent`) activa el atajo para despacho real. `flags=0` — lo que usaban todos los intentos anteriores, incluida la "corrección" de la sesión pasada — escribe la combinación en `kglobalshortcutsrc` y el componente la reporta correctamente vía `allShortcutInfos`, pero KWin nunca la enruta ante una tecla real: es un "default" completamente inerte. Esta era la causa raíz de verdad, no la corrupción de nombre de componente que se diagnosticó antes (esa corrupción era real y también se corrigió, pero no era lo que bloqueaba el disparo).

Con el fix (`global_shortcuts.py` ahora usa `flags=2`), **verificado en vivo**: `ydotool` simulando `Ctrl+Shift+V` y `Ctrl+Shift+C` genera en el log de Nexus `Atajo global disparado: toggle-main-panel` / `toggle-stack` de forma consistente y repetible.

### Limpieza de residuos de depuración

Durante esta investigación se crearon varios componentes de prueba (`nexus_probe_test`, `nexus_probe2`, `nexus_flagtest_*`, `nexus_final_test`) y quedó un componente viejo y roto (`nexus_linux`, previo al renombre a `nexus_linux_app`). Se intentaron limpiar de `~/.config/kglobalshortcutsrc`, pero kglobalaccel (que corre embebido en el propio proceso `kwin_wayland`, no como demonio aparte — reiniciarlo reiniciaría toda la sesión gráfica, así que no se hizo) periódicamente vuelve a escribir su estado en memoria sobre el archivo, resucitando las entradas. **Esto es cosmético, no funcional**: como se confirmó arriba, una entrada con `flags=0` no interfiere con el despacho real de otro componente, así que estos residuos no afectan a Nexus. Si quieres una lista más limpia en Ajustes del Sistema → Atajos, cada uno se puede eliminar manualmente ahí; no es necesario para que Nexus funcione.

### Verificado en vivo esta sesión

- ✅ Registro de ambos atajos globales con `flags=2`, confirmado disparo real end-to-end vía `ydotool` + log de la app (no solo "sin error" — se vio literalmente `Atajo global disparado: toggle-main-panel/toggle-stack` en el log tras cada pulsación simulada).
- ✅ `ydotool` puede disparar atajos globales reales en este sistema (prueba de control con `Ctrl+Alt+Space`, y ahora también con los propios atajos de Nexus).
- ✅ Recolección del Stack con dedup: 3 copias (A, B, B duplicado, C) → A, B, C en el stack, duplicado correctamente ignorado.
- ✅ Ciclo completo `paste_next_and_advance` × 3: consume en orden FIFO correcto, arma el siguiente cada vez, cierra la ventana sola al vaciarse.
- ⏳ **Pendiente de tu confirmación física**: aunque el disparo del atajo ya está probado extremo a extremo con teclas simuladas, falta que confirmes con una pulsación física real, y que la ventana quede al frente con foco (una activación por atajo real de teclado debería tener más legitimidad ante KWin para robar foco que mi prueba sintética, pero no se verificó visualmente todavía).

## 2026-08-24 (segunda vuelta) — toggle mostrar/esconder, auto-avance real, rediseño visual

- **Toggle mostrar/esconder**: `Ctrl+Shift+C`/`Ctrl+Shift+V` ya no tienen doble función — son un toggle puro (abre si está oculto, esconde si está visible). El "pegar y avanzar" que antes vivía en el mismo atajo se separó (ver siguiente punto).
- **Eliminar del Stack**: solo por clic derecho (se quitó el botón visible, por pedido explícito — así es más parecido al original).
- **Auto-avance real del Stack** (`evdev_stack_watcher.py`): Manuel autorizó explícitamente (tras que se le explicara el trade-off de seguridad) agregar su usuario al grupo `input` para poder leer eventos de teclado a nivel de kernel — la única forma real de detectar un `Ctrl+V` físico en cualquier app sobre Wayland, equivalente al monitor global de Accesibilidad de macOS. Implementado con `python-evdev`, corre en un hilo de fondo multiplexando todos los dispositivos de teclado con `selectors`, y se suprime a sí mismo (`PasteService.on_synthetic_paste`) para no confundir sus propios pastes sintéticos con uno real del usuario. Falla con gracia (usa el botón manual como respaldo) mientras el grupo no esté aplicado — requiere `sudo usermod -aG input manuelcarvajal` + cerrar sesión y volver a entrar.
- **Rediseño visual completo**: tipografía Inter (confirmada instalada, la alternativa abierta más cercana a San Francisco), iconos nativos de KDE Breeze recoloreados (`styles.icon()`) en vez de emoji para las insignias de tipo de contenido, filas de lista reconstruidas como widgets reales (`row_widget.py`: insignia circular con icono/miniatura + título + línea de metadata en gris) en vez de texto plano con emoji embebido, tarjetas con bordes/fondo más definidos, botones con más aire.
  - **Bug encontrado y corregido en el camino**: `QIcon.fromTheme()` resolvía contra el tema vacío "hicolor" en vez de Breeze (Qt no detecta automáticamente el tema de iconos del escritorio sin integración de plataforma completa) — se fuerza `QIcon.setThemeName("breeze-dark")` al arrancar.
  - **Segundo bug encontrado y corregido**: un `QWidget` genérico no pinta su `background-color`/`border` de QSS a menos que se le active `WA_StyledBackground` — sin este flag las tarjetas y las insignias de icono eran invisibles pese a tener estilos correctos. Corregido en `row_widget.py`.
  - Ícono de "Enlace" cambiado de `internet-web-browser` (un logo de navegador a todo color que, recoloreado, se veía como un círculo sólido) a `link` (glyph de línea fina, consistente con los demás iconos de tipo).
- Verificado visualmente con capturas reales del panel principal y de Nexus Stack (con datos de prueba, no el historial real) antes/después de cada fix.

## 2026-08-24 (tercera vuelta) — auditoría de calidad profesional: bugs reales, rendimiento, pulido

Manuel pidió una auditoría honesta comparando contra una app profesional real, no un checklist de features. Encontrados y corregidos:

### Bugs reales

- **Crash de arranque introducido y atrapado en el momento**: al agregar la columna `search_text`, el script de `CREATE INDEX` se ejecutaba antes de que la migración `ALTER TABLE` la hubiera agregado a una base de datos ya existente (la de Manuel, con 141 items reales) — `sqlite3.OperationalError: no such column: search_text`. Se movió la creación del índice al método de migración, después de confirmar que la columna existe. El propio hook de excepciones nuevo (ver abajo) fue lo que lo hizo visible en el log en vez de fallar en silencio.
- **Selección de pinboard se perdía en cada edición**: renombrar, cambiar ícono o mover un pinboard regresaba la vista a "Historial" porque `PinboardSidebar.refresh()` siempre hacía `setCurrentRow(0)` sin memoria de qué estaba seleccionado. Corregido para preservar la selección across refresh.
- **`wl-paste --watch` sin vigilancia**: si el proceso muere (reinicio del compositor, bug del binario), la captura de portapapeles se detenía para siempre sin ningún aviso — ni en el log ni en la UI. Ahora se reinicia automáticamente con backoff (hasta 10 intentos, luego deja de insistir y lo deja registrado en el log).
- **Filas de la lista sin estado hover**: las tarjetas custom (`ClipRowWidget`) no reaccionaban al pasar el mouse por encima — solo cambiaban al hacer clic. Se sentía "muerto"/genérico. Agregado `enterEvent`/`leaveEvent` + estado visual.
- **N+1 queries en cada refresh()**: `recent()` hacía **una consulta SQL extra por cada fila** para traer sus pinboards (hasta 200 consultas adicionales en un solo refresh, disparado en cada tecla de búsqueda). Verificado con instrumentación: 0 consultas extra ahora para 133 resultados reales (antes: hasta 133).
- **Búsqueda sensible a mayúsculas/acentos**: buscar "reunion" no encontraba "Reunión". SQLite `LIKE` es insensible a mayúsculas solo para ASCII, nunca para acentos. Se agregó una columna `search_text` normalizada (NFKD + strip de marcas combinantes + lowercase) poblada en cada captura, con migración/backfill para el historial existente. Verificado: `reunion`/`REUNION`/`reunión`/`diseño`/`DISEÑO`/`área`/`area` — los 8 encuentran el mismo resultado.

### Rendimiento

- **Debounce de búsqueda** (150ms): antes cada tecla presionada disparaba una consulta SQL completa + reconstrucción de hasta 200 widgets custom. Ahora espera a que el usuario deje de teclear.
- **Límite de tamaño de imágenes**: una captura de pantalla 5K podía guardarse sin comprimir (varios MB por fila). Ahora se reduce a un máximo de 1600px por lado antes de llegar a SQLite, sin tocar imágenes que ya son pequeñas.
- **Índice SQL en `search_text`** además del existente en `updated_at`.

### Pulido / UX

- **Estado "sin resultados" distinto de "historial vacío"**: buscar algo que no existe ahora dice "Sin resultados para «x»" en vez de mostrarse igual que un historial realmente vacío.
- **Manejo global de excepciones**: cualquier excepción no capturada en un slot de Qt (antes: se imprimía a stderr y se perdía) ahora se registra con traceback completo en el mismo log — encontró el bug de `search_text` de arriba en el primer arranque tras el cambio.
- **Notificación cuando el paste sintético falla**: si `ydotool` no está disponible o falla, antes el clip se copiaba al portapapeles pero no pasaba nada más, sin explicación. Ahora se muestra una notificación nativa de escritorio ("Nexus no pudo pegar... pégalo con Ctrl+V").
- Efecto colateral detectado y corregido: las pruebas de los 4 temas dejaron "Tech Neon" activo en `theme.json` sobre la instalación real de Manuel — se restauró a "midnight_teal" (el que él ya estaba usando) antes de entregar.

### No abordado esta vuelta (fuera de alcance por tiempo)

- Undo/deshacer para "Eliminar" (ni el original en macOS lo tiene, así que no es una regresión, pero sería una mejora real sobre ambos).
- Rotación del archivo de log (`~/.cache/nexus/nexus.log` crece indefinidamente).
- Hot-plug de teclados nuevos para `evdev_stack_watcher.py` (detecta dispositivos solo al arrancar).
- Fuente serif de Storem no siempre se aplica (limitación de Qt con el selector `*{font-family}` en QSS) — cosmético.

## 2026-08-24 (cuarta vuelta) — Animaciones

Manuel pidió movimiento sutil, estilo apps premium (Paste/Raycast), sin exagerar. Regla aplicada: duraciones cortas (bajo ~200ms — más lento que eso se siente lento, no premium), y nada de animación a nivel de ventana completa salvo que se demuestre segura.

### Lo que se intentó y no funcionó (dos límites reales de plataforma, no supuestos)

1. **`QWidget.windowOpacity()`** para hacer fade de la ventana completa al abrir/cerrar: el plugin QPA de Wayland de Qt en este sistema **no lo soporta en absoluto** — cada frame de la animación imprimía `"This plugin does not support setting window opacity"` y no había ningún efecto visual. Confirmado en vivo.
2. **`QGraphicsOpacityEffect` aplicado directamente a la ventana de nivel superior** (el workaround típico cuando `windowOpacity` no está disponible): renderizaba la ventana **completamente negra** — confirmado con captura de pantalla real. Aplicar un `QGraphicsEffect` a un widget top-level parece no ser seguro con este plugin QPA/Qt 6.11 concreto.

**Decisión**: no forzar ninguna de las dos. `MainWindow.fade_hide()` / `StackWindow.fade_hide()` existen como métodos con nombre (en vez de `.hide()` suelto en cada sitio) para que, si en el futuro se prueba una tercera vía más segura (por ejemplo animar un widget contenedor interno en vez de la ventana misma), solo haya que cambiarla en un lugar. Por ahora son idénticos a `.hide()` — mostrar/esconder ventana sigue siendo instantáneo, que es preferible a una animación rota o que ensucia el log.

### Lo que sí se implementó y es seguro

- **Elevación suave en hover de cada fila del historial y del Stack** (`row_widget.py`): un `QGraphicsDropShadowEffect` con `blurRadius` animado de 0 a 16px (140ms, `OutCubic`) al entrar el mouse, y de vuelta a 0 al salir. A diferencia del caso anterior, este efecto se aplica a un **widget hijo** dentro de la lista (no a la ventana de nivel superior), que es el uso estándar y bien soportado de `QGraphicsEffect` en Qt — no se observó el problema de renderizado negro. Empieza invisible (alpha 0) para no dejar una sombra fija cuando el mouse no está encima.
- El hover de fondo/borde (ya existente, vía QSS) se mantiene instantáneo a propósito — es el único elemento de la fila que cambia de color, así que no compite visualmente con la sombra.

### Pendiente de tu confirmación

No pude verificar visualmente el hover de las filas con una captura remota (necesitaría posicionar el cursor exactamente sobre una fila, y no tengo coordenadas de pantalla reales desde SSH). Pásale el mouse por encima de un elemento del historial o del Stack y dime si se ve bien la elevación sutil.
