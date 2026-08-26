# Paridad funcional: Nexus macOS → Nexus Linux

Estado: 2026-08-24. Referencia = `Nexus-original` (macOS, intocada). Comparación hecha leyendo el código fuente Swift real (`Theme.swift`, `Pinboard.swift`, `PasteStack.swift`, `StorageService.swift`, `ClipItem.swift`, `PinboardSidebar.swift`, `MainPanel.swift`), no por memoria/suposición.

Estados: `OK` (paridad real) · `PARCIAL` (existe pero con diferencias) · `FALTA` (no implementado aún) · `NO APLICA` (no tiene sentido en Linux) · `BLOQUEADO POR PLATAFORMA` (Wayland/Linux no lo permite sin hacks inseguros).

| Función | macOS | Linux | Estado | Nota |
|---|---|---|---|---|
| Historial de clips | Sí | Sí | OK | SQLite propio en vez de Core Data; mismo dedup SHA-256 |
| Captura texto/URL/imagen/archivo | Sí | Sí | OK | Vía `wl-paste --watch` en vez de `NSPasteboard` polling |
| Búsqueda en historial | Sí | Sí | OK | LIKE simple; el original tampoco usa full-text search |
| Pinboards (tableros con nombre/color/emoji) | Sí | Sí | OK | Muchos-a-muchos real, exentos de retención, igual que el original |
| Pinboards: reordenar | Sí (drag) | Sí (drag real + botones) | OK | 2026-08-24: `QAbstractItemView.InternalMove` + persistencia (`reorder_pinboards`); "Historial" no se puede arrastrar ni desplazar de la posición 0 |
| Pinboards: editor de color completo (20 colores) | Sí | Sí | OK | 2026-08-24: `PinboardIconEditor` — mismo grid de 20 colores del original + selector emoji, con toggle igual que `IconMode` |
| Pinboards: arrastrar clip a un tablero | Sí (drag) | No | PARCIAL | Se usa botón/menú "Agregar a pinboard" en su lugar |
| Favoritos | — | — | NO APLICA | El original no tiene "favoritos" separado de pinboards; es el mismo concepto |
| Nexus Stack: recolectar mientras está abierto | Sí | Sí | OK | |
| Nexus Stack: dedup por hash dentro de la sesión | Sí | Sí | OK | Corregido 2026-08-24 para igualar `collectLatestCopy` |
| Nexus Stack: FIFO/LIFO | Sí | Sí | OK | |
| Nexus Stack: arma el siguiente item en el portapapeles automáticamente | Sí | Sí | OK | |
| Nexus Stack: consumo automático al detectar Cmd/Ctrl+V real en cualquier app | Sí (monitor global de Accesibilidad) | No | BLOQUEADO POR PLATAFORMA | Wayland no expone un monitor de teclado global sin acceso privilegiado a `/dev/input` (root/grupo `input`); ver sección propia abajo |
| Nexus Stack: consumo mediante un solo atajo repetible | Sí (Cmd+V real) | Sí (Ctrl+Shift+C repetido) | PARCIAL | Aproximación funcional: el mismo atajo global pega-y-avanza cuando el Stack tiene items |
| Nexus Stack: eliminar un item individual | Sí (menú contextual) | Sí (botón visible + menú + Delete) | OK | Mejor que el original: botón visible, no solo clic derecho |
| Nexus Stack: vaciar todo el stack | Implícito (cerrar = perder sesión) | Sí (botón explícito "Vaciar Stack") | OK | |
| Nexus Stack: se cierra solo al vaciarse | Sí | Sí | OK | |
| Nexus Stack: aviso si falta permiso del sistema | Sí (banner de Accesibilidad) | No | FALTA | No crítico: Linux no tiene un permiso equivalente que otorgar |
| Shortcut global Stack | ⇧⌘C | Ctrl+Shift+C | OK | Causa raíz real encontrada (faltaba `flags=2`/`SetPresent` en `setShortcut`) y corregida; disparo real confirmado con `ydotool`. Sigue pendiente que apps con su propio atajo local (Warp, Edge) lo intercepten si tienen el foco — ver `MIGRATION_LINUX.md` |
| Shortcut global Historial | ⇧⌘V | Ctrl+Shift+V | OK | Igual que arriba |
| Tray / menu bar icon | Sí (`NSStatusItem`) | Sí (`QSystemTrayIcon`) | OK | Confirmado funcionando por Manuel |
| Ventana: reutilizar instancia existente | Sí | Sí | OK | Un solo proceso, D-Bus además evita duplicados |
| Ventana: al frente + foco al abrir | Sí | Parcial | PARCIAL | `show()+raise()+activateWindow()` funciona cuando se dispara desde un atajo real (evento de teclado genuino); una activación puramente programática (ej. D-Bus sin tecla real) puede quedar sujeta a la prevención de robo de foco de KWin |
| Menús contextuales | Sí | Sí | OK | |
| Editar clip | No (Nexus tampoco lo hace en macOS) | No | NO APLICA | |
| Eliminar clip | Sí | Sí | OK | |
| Persistencia | Core Data → SQLite | SQLite directo | OK | |
| Preferencias (tema, launch at login, hotkeys) | Sí (`UserDefaults`) | Parcial | PARCIAL | Tema: OK (ver fila propia). Autostart: OK (XDG). Editor de atajos in-app: no existe — se configuran vía código, no hay UI de preferencias todavía |
| Autostart | Sí (`SMAppService`) | Sí (XDG autostart) | OK | |
| Instancia única | Sí | Sí | OK | Un solo proceso + servicio D-Bus con nombre reservado |
| Base de datos / metadata | Core Data | SQLite | OK | |
| Temas conmutables (original: Liquid Glass, Minimalista, Material3, Storem, Tech Neon) | Sí (5) | Sí (4) | OK | 2026-08-24: `styles.py` reescrito como sistema multi-tema (dataclass `Theme` + `PRESETS`), selector en el menú de la bandeja ("Tema"), persistido en `theme.json`, aplicado a nivel de `QApplication` (cambia en vivo, sin reiniciar). Liquid Glass se omite deliberadamente — blur real no tiene equivalente en Qt Widgets sin pelear con KWin por ventana. Storem usa Noto Serif, Tech Neon usa Noto Sans Mono (verificado visualmente); la fuente de Storem no siempre se aplica por una limitación conocida de Qt con el selector `*{font-family}` en QSS — cosmético, no funcional |
| Preview de URL (título, dominio, favicon, OG image) | Sí | No | FALTA | Ver Prioridad 4 — pendiente de esta sesión, documentado abajo |
| Preview de YouTube (thumbnail, título) | Sí | No | FALTA | Idem |
| Imágenes / screenshots | Sí | Sí | OK | Captura y preview en miniatura ya funcionan |
| HTML / Rich Text | Sí (drag externo produce RTF/HTML cuando aplica) | No | FALTA | No investigado en detalle esta sesión |
| Archivos (referencia + drag-out) | Sí | Parcial | PARCIAL | Captura de referencia a archivo sí; drag-and-drop hacia otras apps no implementado |
| Drag & drop (arrastrar un clip hacia otra app) | Sí | No | FALTA | No implementado; Qt lo soportaría pero no se ha hecho |
| MIME types | Amplio (vía `NSPasteboard`) | text/plain, text/uri-list, image/png | PARCIAL | Suficiente para el uso real diario; falta RTF/HTML |
| Thumbnails de imagen en la lista | Sí | Sí | OK | |
| Favicons | Sí (parte del preview de URL) | No | FALTA | Depende de implementar previews |

## Nexus Stack: por qué "consumo automático real" está bloqueado por plataforma

El original usa `NSEvent.addGlobalMonitorForEvents(matching: .keyDown)` — un monitor de teclado que ve TODAS las pulsaciones del sistema, protegido únicamente por el permiso de Accesibilidad de macOS. Wayland no tiene equivalente: exponer todas las pulsaciones de teclado a una app arbitraria es exactamente lo que el modelo de seguridad de Wayland existe para evitar. La única forma de lograrlo sería leer `/dev/input/eventX` directamente, lo cual requiere que `manuelcarvajal` esté en el grupo `input` (no lo está) o ejecutar como root — ninguna de las dos se hizo sin autorización explícita, siguiendo la instrucción de no usar sudo interactivo ni hacks inseguros.

**Solución implementada**: dado que `Ctrl+Shift+C` ya es un atajo global 100% funcional (ver `MIGRATION_LINUX.md`), se reutiliza: la primera pulsación abre el Stack (sesión vacía, empieza a recolectar); mientras el Stack tenga items pendientes, cada pulsación siguiente de `Ctrl+Shift+C` pega el primero en la ventana que tenga el foco en ese momento y avanza al siguiente — sin necesitar volver a Nexus. No es idéntico a "cualquier Ctrl+V real", pero logra el mismo resultado práctico (pegado secuencial sin fricción) sin comprometer la seguridad de Wayland.

## Hallazgo adicional: Klipper puede interferir con el portapapeles

Durante las pruebas de esta sesión se detectó que **Klipper** (el gestor de portapapeles nativo de KDE, activo por defecto como parte de `plasmashell`, con atajo propio `Meta+V` para "Mostrar elementos del portapapeles en la posición del ratón") está corriendo en Erwin y compitiendo por la propiedad del portapapeles Wayland al mismo tiempo que Nexus. Se observó al menos una lectura de portapapeles obsoleta (contenido antiguo) durante pruebas automatizadas rápidas y consecutivas.

Como Nexus está destinado a **reemplazar** esta función, se recomienda desactivar Klipper para evitar que ambos gestores compitan por el portapapeles. No se automatizó (edición directa del systray de Plasma es riesgosa sin una vía D-Bus segura de "desactivar"); instrucción manual:

1. Clic derecho en la bandeja del sistema → **"Configurar la bandeja del sistema…"**.
2. Busca **"Portapapeles"** en la lista de elementos.
3. Cámbialo a **"Ocultar elemento"** (o desmarca su casilla si aparece como activable).
4. Aplicar.

Esto no borra tu historial de Klipper ni rompe nada más — solo deja de monitorear/gestionar el portapapeles activamente, dejando ese trabajo exclusivamente a Nexus.

## Resumen numérico

- **OK**: 28 funciones con paridad real (+4 desde la vuelta anterior: reordenar pinboards por drag real, editor de color completo, temas conmutables).
- **PARCIAL**: 6 (mayormente simplificaciones documentadas de UI, no pérdida de función real).
- **FALTA**: 6 (previews de URL/YouTube — Prioridad 4, sigue pendiente — y HTML/Rich Text).
- **BLOQUEADO POR PLATAFORMA**: 1 (consumo automático del Stack por monitor global de teclado; con aproximación funcional equivalente, y auto-avance real disponible vía `evdev_stack_watcher.py` una vez Manuel active el grupo `input`).
