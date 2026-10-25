# Nexus Windows — Plan maestro (5 entregas)

## 0. Condiciones vinculantes del usuario
**C1 · Protección de tu Nexus actual**
- Nunca se usa la base de datos personal ni la instancia habitual para desarrollar o probar. Todo en el worktree `windows-reference`, con instancia aislada y base de **copia independiente** (o `:memory:`/`tmp_path` en tests).
- No se cierra, reinicia ni sustituye la instancia habitual (PID 12304 documentado); no se instala ni reemplaza tu Nexus sin tu autorización explícita después de comprobar la entrega.
- **Entorno aislado (Fase 0 de la Entrega 1).** Hallazgos que lo hacen necesario: las rutas salen de `LOCALAPPDATA` al importar `paths.py` (se puede redirigir); el mutex de instancia única es fijo (`Local\NexusWindowsErwin`); los atajos globales por defecto (`Ctrl+Shift+V/C`) ya los tiene tu instancia y hoy un fallo de registro aborta la app; el monitor del portapapeles de una segunda instancia capturaría lo mismo que la tuya.
  Solución mínima y aditiva: un lanzador `tools/windows_sandbox_run.py` (no toca el flujo normal) que, antes de importar Nexus: (a) fija `LOCALAPPDATA` a `%TEMP%\nexus-sandbox\` con una copia de la base; (b) usa un nombre de mutex distinto; (c) usa atajos globales distintos y poco comunes; (d) arranca con captura **pausada** y sin hook de Ctrl+V (solo se activan de forma explícita en una prueba concreta); (e) **se niega a arrancar** si `paths.DB_PATH` apunta a la ruta real o si el mutex real está libre/ocupado de forma inesperada. Esto requiere un pequeño gancho por variable de entorno (p. ej. `NEXUS_SANDBOX=1`) para el nombre del mutex y los atajos en `windows_app.py`/`windows_platform.py`, sin cambiar el comportamiento sin esa variable.
  Verificación antes de usarlo: tests que demuestran que con `NEXUS_SANDBOX=1` el mutex, la base, el log y los atajos difieren de los reales, y que sin la variable todo es idéntico a hoy. Comprobación manual: tu instancia sigue respondiendo a `Ctrl+Shift+V` mientras la aislada está abierta.
- **Copia de la base:** solo con la API de backup de SQLite (lectura), nunca copiando el archivo abierto; se verifica con `PRAGMA integrity_check` y conteos; no se imprime contenido de clips.
- Respaldos verificables (en `%LOCALAPPDATA%\Nexus\backups\` y en el sandbox) antes de cualquier migración o cambio que afecte datos.

**C2 · Entregas con aceptación individual**
- Autorizada **solo la Entrega 1**. Las demás son hoja de ruta y requieren tu aprobación individual; no se avanza automáticamente.
- Al terminar cada entrega: pruebas ejecutadas con resultados registrados; rendimiento y regresiones comprobados; capturas de los **5 temas** si hay cambios visuales; informe de qué cambió, qué queda pendiente y cómo revertir.
- **Sin commits ni push** sin tu autorización.

**C3 · Íconos e inicio (Entrega 4)**
- La corrección de íconos no debe romper: el arranque (`run-nexus-windows.cmd`, entry point), los accesos directos, el inicio automático ni el anclaje a la barra de tareas; se verifica cada uno antes y después, en el entorno aislado.
- Se elimina el ícono de CMD y la identidad oficial es la **N azul** de la interfaz (se confirma cuál es la fuente real).

**Calidad transversal:** todo cambio visual respeta los 5 temas, el movimiento reducido y el rendimiento; app nativa, sencilla, sin dependencias innecesarias.

## 1. Contexto
Nexus Windows (Python/PySide6 + SQLite, rama `windows-native`, worktree `C:\Users\Admin\Projects\Nexus\windows-reference`) es una app real en uso con datos importantes. Objetivo: convertirla en un gestor de portapapeles **premium, rápido, intuitivo y bien integrado en Windows**, inspirado en las buenas prácticas de Paste pero con identidad propia, **sobre la arquitectura existente**: sin reescritura, sin cambiar de tecnología, sin dependencias web, con cambios **aditivos** compatibles con Linux.

Este plan **conserva íntegra** la especificación de títulos editables ya aceptada (Entrega 1) y añade: limpieza y rediseño UX/UI, configuración con atajos personalizables, menú de bandeja, íconos de Windows correctos y animaciones sobrias.

## 2. Estado real verificado (solo lectura)
**Datos y base**
- SQLite en `%LOCALAPPDATA%\Nexus\data\nexus.sqlite`; `clip_items` sin título; `content_hash UNIQUE`; Pinboards M:N; búsqueda por `search_text LIKE`; purga de 30 días excepto anclados (`storage.py`).
- Ajustes en `%LOCALAPPDATA%\Nexus\config\settings.json` (`panel_hotkey`, `stack_hotkey`, `paused`, `retention_days`, `launch_at_login`, `hide_on_focus_loss`, `bottom_gap`, `excluded_apps`) y `theme.json`. **No existe `save_settings`**; `load_settings` sobrescribe sin respaldo un JSON roto. `launch_at_login`, `hide_on_focus_loss` y `bottom_gap` no se usan en la capa Windows.

**Bandeja** (`windows_app.py` L242-280): `QSystemTrayIcon` con `nexus-tray.png` de 18 px; menú: Abrir Nexus, Nexus Stack (texto dinámico), Administrar pinboards, submenú Estilo, Salir. Clic izquierdo no hace nada. No se comprueba `isSystemTrayAvailable()`. La pausa de captura existe a medias: `WindowsClipboardMonitor` lee `settings["paused"]` en cada lectura, pero nada lo cambia ni lo persiste.

**Atajos**
- Globales (`RegisterHotKey`): `Ctrl+Shift+V` panel, `Ctrl+Shift+C` Stack. Modificadores fijos; `vk_for` (`windows_settings.py`) solo admite `Ctrl+Shift+<1 carácter>` y usa `ord()`, que solo es correcto para A-Z/0-9. **Si el registro falla, la app lanza `RuntimeError` al arrancar sin mensaje al usuario** (`windows_app.py` L59-66).
- Hook `WH_KEYBOARD_LL`: intercepta Ctrl+V solo con Stack activo; no es configurable (es el pegado del sistema).
- Locales (`timeline_window.py`, `windows_timeline.py`, `stack_window.py`): `Ctrl+1…9` (pegado rápido, **ya existe**), `Ctrl+←/→` (pinboard, **ya existe**), `Ctrl+F`, `Esc`, `Tab` (hoy alterna vista expandida), `Enter`, `Shift+Enter`, escribir para buscar, `Supr/Retroceso` en Stack. *(Corrección: antes dije que 1-9 y ←/→ no existían; sí existen.)*

**Íconos y arranque**
- No hay `.ico`, `setWindowIcon`, `AppUserModelID` ni empaquetado. `nexus-icon.png` (1254×1254) solo se usa en Stack; la bandeja usa 18 px (+36 px `@2x`); los íconos "brand" de ventana se dibujan con `QPainter` en `windows_design.icon()`.
- Causa probable del ícono de CMD: el entry point `nexus-windows.exe` es de **subsistema consola** (`[project.scripts]`, verificado en la cabecera PE); `run-nexus-windows.cmd` evita la consola con `pythonw.exe`, pero sin ícono propio. Causa probable del ícono antiguo/ilegible: sin `AppUserModelID` Windows agrupa bajo `pythonw.exe`, y la bandeja usa un PNG de 18 px. La caché de íconos de Windows **no se comprobó** (exigiría ejecutar cosas).
- `RESOURCES_DIR` apunta a la carpeta del repo: solo funciona en instalación editable.

**UI**
- Leyenda "Ctrl+F Buscar · Enter Pegar · Esc Cerrar": solo en `windows_timeline.py` (`_keyboard_hint`, L344-349; textos en `_sync_header_density`, `_navigate_board`, `show/close_pinboard_manager`). Comparte footer con `_feedback_label` ("Guardado") y `_context_label` (siempre oculto). `_apply_geometry` suma la altura del footer. Ningún test referencia la leyenda; sí los de geometría.
- Expandir: `_expand_btn` y `_toggle_expanded` (`timeline_window.py` L195-199, 383-396; Windows L720-728); **Tab también lo activa**; `_expanded` interviene en altura y ancho (`_apply_geometry` L761-772). Tests: `test_windows_panel.py` L168-169, 238-243; `test_windows_premium_integration.py` L82-90, 190; `test_panel_geometry.py` L210-236; herramientas `windows_visual_preview.py`, `windows_nexus_material_probe.py`. El Stack no tiene Expandir/Contraer.
- Cabecera: marca, "Nexus", eyebrow, contador, **Estilo**, Expandir, × ; fila de navegación Historial · Pinboards · `⋯`; búsqueda.
- Detalles: `QMessageBox.information` en la base compartida (`timeline_window.py` L348-349); Windows no lo sobrescribe.
- Motion: constantes 90/130/110/150 ms con `OutCubic`; soporte de movimiento reducido ya existe (`windows_design.motion_enabled()`, `SPI_GETCLIENTAREAANIMATION`, `NEXUS_REDUCED_MOTION`). **Con Acrylic activo, el fade de mostrar/ocultar el panel está desactivado** a propósito para no activar `WS_EX_LAYERED`; los tests lo fijan. Tarjetas: una sola `QVariantAnimation` de 150 ms para hover/selección.
- Tests: fixtures offscreen en `tests/conftest.py`; `test_windows_app_smoke.py` usa un `Tray` falso con solo `setToolTip/setContextMenu/show/hide` (**hay que ampliarlo** al tocar la bandeja); `test_windows_panel.py` L184 parchea `timeline_window.QMenu`.
- Referencias visuales: `work\theme-*-verified.png`, `liquid-glass-recovered-*.png` (antiguas; muestran "Expandir"/"+ Pinboard"). No hay capturas del estado actual de la barra Historial · Pinboards · ⋯.

## 3. Paste: qué está verificado y qué no
| Categoría | Contenido |
|---|---|
| **Verificado** (docs oficiales pasteapp.io/help) | Renombrar con `Cmd-R`, menú o clic en el título; Quick Paste `Cmd-1…9` con número visible; `Cmd-←/→` entre pinboards; `Tab` alterna búsqueda/resultados; `Shift-Return` texto plano; `Espacio` vista previa; búsqueda al escribir con filtros combinables en chips; retención por presets; un ítem = un pinboard; multiselección; modo compacto |
| **No documentado por Paste** | Cómo se ve el título, si se indexa o afecta al pegado; alcance de `Cmd-Z`; paleta, radios, curvas de animación |
| **Referencias visuales** | Solo se leyó la documentación `/help`; **no se analizaron capturas públicas ni el sitio de marketing**. Paso opcional de la Entrega 2: revisar capturas oficiales en el navegador integrado solo como inspiración de jerarquía, sin copiar recursos ni marca |
| **Decisiones propias de Nexus** | Título como encabezado de la tarjeta; Pinboards M:N; panel conservado (no barra inferior); configuración como página del panel; `Tab` alterna foco; atajos configurables; identidad "N" azul; 5 temas |
| **Futuro** | Deshacer borrado, vista previa con `Espacio`, filtros por tipo/app/fecha, retención con presets, multiselección, tipo color, "Mostrar en…", crear/editar contenido, barra inferior |

## 4. Orden y dependencias
Se mantiene el orden propuesto, con dos matices técnicos:
1. **E1 títulos** — base de datos y tarjeta; todo lo demás se apoya en ello.
2. **E2 limpieza + UX/UI** — antes de animar, y antes de añadir Configuración (la cabecera se reorganiza una sola vez).
3. **E3 Configuración + atajos** — requiere persistencia de ajustes (`save_settings`) y la página de Configuración; sin esto no hay "Abrir Configuración" en la bandeja.
4. **E4 bandeja + íconos** — la bandeja depende de E3 (Configuración, atajos, pausa persistente). **Matiz:** la parte de íconos (4A) es independiente y de riesgo bajo; se puede adelantar justo tras E2 si quieres arreglar el ícono de CMD cuanto antes. La bandeja (4B) va después de E3.
5. **E5 animaciones, refinamiento y pruebas finales** — al final, para medir sobre la UI definitiva.

**Reglas para todas:** respaldo SQLite verificado antes de cualquier migración; copia de `settings.json`/`theme.json` antes del primer cambio de formato; cambios aditivos en archivos compartidos (permitido un gancho mínimo si no hay otra vía, documentado); un commit por entrega en `windows-native` **solo cuando lo apruebes**, sin push; los cambios sin commitear de `install.sh`/`uninstall.sh` quedan fuera. Cada entrega termina con la suite completa (614 + nuevos), captura en los 5 temas y comprobación manual en la instancia real.

**Skills y herramientas realistas:** `replica-skill:replica-architect` y `replica-design` (validar diseño; comprobar primero su encaje con PySide, ya que pueden asumir web), `replica-test` y `replica-diff` para regresión; `emil-design-eng`, `apple-design` y `premium-creative-direction` para criterio visual; `animate`, `animation-performance` y `premium-motion-design` (solo sus principios: duración, interrupción, reducido; se implementa con Qt, nunca con React/Remotion); `agent-skills:test-driven-development`, `incremental-implementation`, `security-and-hardening` (ajustes y archivos); `break-ui` como idea (títulos largos, emojis, 200 clips) aplicada con tests de PySide; `ponytail:ponytail-review` manual solo en revisión; `done` y `code-review` al cerrar cada entrega. No se usa `remotion-*` ni nada web.

---

## 5. ENTREGA 1 — Títulos editables (especificación aceptada, íntegra)

**Decisiones**
1. Un título por **clip**, global (Historial, Pinboards, Stack). Cualquier clip.
2. Reglas: recorte de espacios; saltos de línea → espacio; emojis y acentos permitidos; máx. 80 caracteres (se corta sin error); duplicados permitidos; vacío = quitar.
3. Tarjeta: título en una línea (semibold, elipsis) + fragmento del contenido atenuado debajo; altura de tarjeta sin cambios (184/160/148); tokens de color de los 5 temas; sin título = idéntica a hoy.
4. También en Detalles (editable), vistas de Pinboards y filas de Stack; no en bandeja ni notificaciones. Pegar/Copiar nuevamente usan siempre el contenido original.
5. Edición: **F2**, "Renombrar…" en el menú contextual y editor en línea (sin modal). Enter guarda **sin pegar**; Esc cancela; flechas y Ctrl+F actúan sobre el campo; al terminar el foco vuelve a la misma tarjeta.
6. Búsqueda: título + contenido, sin acentos ni mayúsculas, con coincidencias de título primero.
7. Retención: un clip con título cuenta como curado → exento de la purga; confirmar al eliminarlo.
8. Datos: columnas nuevas opcionales, migración aditiva idempotente, respaldo previo con la API de backup.

**Cambios concretos**
- `storage.py`: `ALTER TABLE clip_items ADD COLUMN title TEXT` y `title_search TEXT` (al estilo de `_migrate_search_text`); `ClipItem.title` y `display_title`; `set_title(item_id, title)` con normalización; `recent()` con `OR title_search LIKE ?` y `ORDER BY (title_search LIKE ?) DESC, updated_at DESC` solo con consulta; `purge_older_than()` añade `AND title IS NULL`.
- `windows_card_delegate.py`: pintar título + fragmento sin tocar `sizeHint`.
- `windows_timeline.py`: F2, acción "Renombrar…" (sobrescribir `_show_item_menu`; cuidar `test_windows_panel.py` L184 que parchea `timeline_window.QMenu`), editor en línea (patrón de `_InlineEditor`), Detalles propio con campo de título (`QDialog` temado; hoy es `QMessageBox`, precedente válido).
- `windows_stack.py`: título en la fila.
- `windows_design.py`: estilos del título con tokens existentes.

**Dependencias:** ninguna previa. **Riesgos y pruebas:** Enter en edición pega o cierra el panel; migración sobre la base real (probar sobre **copia**); purga; tarjeta cortada; búsqueda lenta (`tools/windows_benchmark.py` antes/después); Linux roto (solo cambios aditivos; correr su suite si es posible); título que altere el pegado (test de payload idéntico para texto, URL, imagen y archivo).
**Aceptación:** 614 tests siguen pasando; tests nuevos de migración, persistencia tras reinicio, búsqueda con acentos, límites del título, F2/Enter/Esc, purga, pegado idéntico, Stack y Pinboards; captura en 5 temas; sin regresión de benchmark.
**Respaldo y reversión:** backup SQLite verificado (`integrity_check`) en `%LOCALAPPDATA%\Nexus\backups\`; revertir restaurando los archivos tocados (commit) — las columnas extra son inofensivas.

---

## 6. ENTREGA 2 — Limpieza de interfaz y UX/UI

**Cambios**
1. **Eliminar la leyenda** inferior: ocultar/retirar `_keyboard_hint` y dejar de reescribirla en `_sync_header_density`, `_navigate_board`, `show/close_pinboard_manager`. Quitar `_context_label` (siempre oculto). El footer solo vive mientras haya contenido: reubicar "Guardado" como confirmación breve en la cabecera/junto al contador y condicionar en `_apply_geometry` la suma de la altura del footer. Los atajos siguen funcionando; su documentación pasa a Configuración (E3).
2. **Eliminar Expandir/Contraer** del Historial: retirar `_expand_btn` y su QSS; neutralizar `_toggle_expanded`/`_expanded` en la capa Windows (altura y ancho fijos según `_apply_geometry` en estado contraído); la rama `expanded` de `panel_geometry.py` queda sin uso desde Windows y **no se toca** (Linux). **`Tab` hoy expande:** pasa a alternar foco búsqueda ↔ lista (patrón verificado de Paste). Actualizar `tools/windows_visual_preview.py` y `windows_nexus_material_probe.py`.
3. **Cabecera limpia:** `[N · Nexus]  ……  [contador] [Estilo ▾] [×]`; navegación `Historial · Pinboards · ⋯` y búsqueda debajo. "Administrar Pinboards" y (en E3) "Configuración" viven en el menú `⋯`. Se conserva el selector de tema.
4. **Tarjetas:** mejor jerarquía (tipo/número, título, vista previa, pie con app y tiempo), estados diferenciados y coherentes en los 5 temas: reposo, hover, selección (borde acento 1.5 px), foco de teclado (contorno), edición del título (campo con acento y caret), sin cambiar el tamaño de tarjeta.
5. **Menús contextuales:** QSS más cuidado en `windows_design.py` (padding, radio, separadores, atajos a la derecha, acción destructiva diferenciada); menú de tarjeta reorganizado (Pegar · Texto plano · Copiar · Renombrar · Pinboards ▸ · Detalles · Eliminar).
6. **Búsqueda y Stack:** campo de búsqueda con estados claros; Stack con filas y cabecera en el mismo lenguaje (el texto de ayuda largo del Stack se reduce al mínimo útil).
7. **Referencias:** paso opcional de revisar capturas públicas de Paste solo como inspiración de jerarquía; capturar el estado actual de Nexus en los 5 temas como línea base antes de cambiar.

**Archivos:** `windows_timeline.py`, `windows_card_delegate.py`, `windows_design.py`, `windows_stack.py`, `tools/windows_visual_preview.py`, tests de panel/geometría/integración premium.
**Dependencias:** E1 (tarjeta con título). **Skills:** `emil-design-eng`, `apple-design`, `premium-creative-direction`, `replica-design`, `break-ui`.
**Riesgos:** geometría (tests exactos de alturas), foco y Tab, tamaño y monitores, fallos de la composición Acrylic (no tocar `windows_backdrop`/`windows_material`).
**Pruebas:** actualizar `test_windows_panel.py` L168-169, 238-243, 357, 387; `test_windows_premium_integration.py` L82-90, 190; `test_panel_geometry.py` solo si aplica; tests nuevos: ausencia de leyenda y de botón Expandir, Tab alterna foco, tamaño estable entre mostrar/ocultar, multi-monitor por cursor, título largo/emoji/200 clips sin cortes, capturas en 5 temas.
**Aceptación:** sin leyenda ni Expandir en ninguna vista; ventana mismo tamaño y posición que antes en reposo; foco y pegado intactos; capturas aprobadas por ti.
**Respaldo/reversión:** commit de E1 como punto de retorno; copia de `windows_timeline.py`/`windows_design.py` con manifiesto SHA-256 (procedimiento ya documentado en `WINDOWS_ARCHITECTURE.md` §10).

---

## 7. ENTREGA 3 — Configuración y atajos personalizables

**Diseño:** la Configuración es **una página más del panel existente** (como el administrador de Pinboards, página 1 de `_pages`): sin segundo HWND (descartado en la arquitectura), compacta y temada. Secciones: **Atajos de teclado**, **Apariencia** (tema), **Captura** (pausar/reanudar). Acceso desde `⋯ → Configuración`, y desde la bandeja en E4. Fuera de alcance: `launch_at_login` (clave sin uso), retención, exclusión de apps (futuro).

**Atajos — modelo**
- Registro central `shortcuts` (nuevo módulo `windows_shortcuts.py`): id, descripción, ámbito (**Global de Windows** / **Con foco en Nexus**), valor por defecto, ¿editable?
- Editables globales: mostrar panel (`Ctrl+Shift+V`), Stack (`Ctrl+Shift+C`).
- Editables locales: buscar (`Ctrl+F`), cerrar (`Esc`), alternar foco búsqueda↔lista (`Tab`), pegar (`Enter`), texto plano (`Shift+Enter`), renombrar (`F2`, nuevo), pegado rápido (`Ctrl+1…9` → un solo modificador editable), Pinboard anterior/siguiente (`Ctrl+←/→`), quitar del Stack (`Supr`).
- **No editables** (informativos): `Ctrl+V` con Stack activo (es el pegado del sistema interceptado por el hook).
- Persistencia: clave `shortcuts` en `settings.json`; escritura atómica con copia `.bak`; JSON roto → se respalda en vez de sobrescribirse; añadir `save_settings`.
- Captura de teclas con `QKeySequenceEdit` (con cancelar y limpiar), botón "Restablecer" por atajo y "Restablecer todos".
- Validación: duplicados internos; conflicto entre ámbitos solapados (p. ej. un global igual a un local es válido pero se avisa); globales exigen modificador (Ctrl/Alt/Shift/Win según permita `RegisterHotKey`) y tecla mapeable; se sustituye `ord()` por mapeo VK correcto (incluye F-teclas, flechas, signos). **Prueba de registro real** antes de guardar: registrar/desregistrar y leer `GetLastError` (1409 = ya registrado por otra app) → mensaje claro. Aplicación **en caliente** (re-registro sin reiniciar); si falla, se conserva el valor anterior.
- **Arranque seguro:** si un atajo guardado ya no se puede registrar, Nexus arranca igualmente, usa el predeterminado y lo avisa (hoy aborta en silencio).
- `Ctrl+1…9` y `Ctrl+←/→` ya existentes se registran en este sistema; las `QShortcut` locales se construyen desde el registro.

**Archivos:** nuevo `windows_shortcuts.py`; `windows_settings.py` (save/validate/mapeo VK); `windows_platform.py` (registro con código de error); `windows_app.py` (re-registro en caliente); `timeline_window.py`/`windows_timeline.py`/`stack_window.py` (construcción de `QShortcut` desde el registro — con gancho aditivo en la base compartida si es imprescindible); nueva página de Configuración en `windows_timeline.py` (o `windows_settings_page.py`); `windows_design.py` (estilos).
**Dependencias:** E2 (cabecera/menú `⋯` ya reorganizados). **Skills:** `agent-skills:test-driven-development`, `security-and-hardening`, `replica-architect`, `emil-design-eng` para la página.
**Riesgos:** romper búsqueda/pegado/Stack por cambiar un atajo (el tipeo para buscar no debe depender de atajos configurables); teclas reservadas de Windows; reentrada del hook de teclado; foco al capturar teclas (mientras se captura, los atajos globales y locales se suspenden).
**Pruebas:** `vk` correcto para teclas especiales; duplicados y conflictos; registro fallido (mock) conserva el valor anterior; persistencia tras reinicio; restablecer individual/total; arranque con atajo inválido; Enter/Esc/F2/Tab/Ctrl+1…9 siguen funcionando tras cambiar otro atajo; `test_windows_lifecycle_safety` y smoke intactos; el hook Ctrl+V no cambia.
**Aceptación:** puedes cambiar, restablecer y persistir todos los atajos editables; los conflictos se explican en pantalla; nada de lo existente se rompe con valores por defecto.
**Respaldo/reversión:** copia de `settings.json` antes del primer guardado de formato nuevo; las claves nuevas son ignoradas por versiones anteriores.

---

## 8. ENTREGA 4 — Íconos de Windows (4A) y menú de bandeja (4B)

### 4A. Íconos
1. **Identificar la fuente real** (primer paso): comparar `resources\nexus-icon.png` (1254 px) y el glifo "brand" de `windows_design.icon()` con la "N minimalista con contorno azul claro" que quieres. Se renderiza y se compara visualmente; solo si no hay coincidencia clara te pregunto. Si existe vector, se usa como fuente.
2. Generar `resources\nexus.ico` multirresolución (16, 20, 24, 32, 40, 48, 64, 256) y PNG por tamaño (bandeja 16/24/32 con variante clara/oscura si hace falta legibilidad), con script reproducible en `tools\` (sin binarios opacos).
3. En `windows_app.py`: `SetCurrentProcessExplicitAppUserModelID("StoreM.Nexus")` antes de crear ventanas; `QApplication.setWindowIcon(QIcon(nexus.ico))`; ícono de bandeja multi-resolución con `QIcon.addFile`; ícono en diálogos.
4. **Causa del CMD:** pasar el entry point a `[project.gui-scripts]` (subsistema GUI) y mantener `run-nexus-windows.cmd` con `pythonw`; añadir script de acceso directo (`.lnk` con ícono y AppUserModelID) en `tools\`. **Crear accesos directos en el escritorio/menú Inicio es una acción externa: se hará solo con tu confirmación.**
5. Empaquetado: mover `resources` a datos de paquete (`package-data`) para que funcione sin instalación editable; ícono preparado para una futura distribución. Un `.exe` empaquetado (PyInstaller) **no existe hoy y queda fuera** salvo que lo pidas; solo se deja el `.ico` listo.
6. **Caché de íconos:** diferenciar fallo de compilación de acceso directo viejo comprobando que `nexus.ico` y el recurso embebido son correctos y probando con un acceso directo nuevo; no se borra `IconCache.db` ni se reinicia el Explorador sin tu confirmación.
7. Comprobar anclado a la barra de tareas (AUMID estable; reanclar si el pin anterior apunta a `pythonw.exe`).
**Archivos:** `windows_app.py`, `pyproject.toml`, `resources\`, `tools\`.
**Pruebas:** el `.ico` contiene todos los tamaños; `QIcon` no vacío para cada tamaño; `setWindowIcon` y AUMID invocados (mock de shell32); `pyproject` declara `gui-scripts`; los recursos se resuelven desde el paquete.
**Aceptación:** al iniciar no aparece CMD; la barra de tareas, la ventana y la bandeja muestran la "N" azul; verificado manualmente.
**Reversión:** el ícono y AUMID son aditivos; restaurar `pyproject.toml` y `windows_app.py` desde commit.

### 4B. Menú de bandeja
**Menú** (solo funciones reales): Abrir Nexus (historial) · Pinboards · Nexus Stack (texto de estado) · **Pausar/Reanudar captura** (persistida; el nombre evita confundirla con la pausa del Stack; tooltip "Nexus · captura en pausa") · Configuración · Atajos de teclado (abre Configuración en esa sección) · Estilo ▸ · Salir. Clic izquierdo: mostrar/ocultar el panel (`activated`). Comprobar `isSystemTrayAvailable()`; indicador visual de pausa (variante del ícono).
**Distinción:** bandeja (acciones rápidas), ventana/panel (contenido), ícono de la barra de tareas (identidad AUMID).
**Archivos:** `windows_app.py`, `windows_settings.py` (persistir `paused`), `windows_clipboard.py` (ya lo lee), tests.
**Dependencias:** E3 (Configuración, `save_settings`) y 4A (íconos).
**Riesgos:** pausar por error y perder copias (indicador visible); hilos/estado entre bandeja y monitor.
**Pruebas:** ampliar el `Tray` falso del smoke (`activated`, `setIcon`, `setToolTip`); acciones del menú; pausar → no captura y reanudar → captura; la pausa persiste tras reiniciar; Stack independiente.
**Aceptación:** todas las entradas funcionan en la instancia real; la pausa es visible y persistente.
**Reversión:** commit previo; `settings["paused"]` por defecto `false`.

---

## 9. ENTREGA 5 — Animaciones, refinamiento y pruebas finales
**Principios:** breves (≤ 150 ms), discretas, solo `QPropertyAnimation`/`QVariantAnimation` nativas, `OutCubic`, interrumpibles, solo propiedades baratas (opacidad y color; no animar geometría de lista completa), sin animación si `motion_enabled()` es falso.
**Cambios**
- Mantener fades de página (90 ms) y foco de búsqueda; **no** reactivar el fade de mostrar/ocultar del panel con Acrylic (rompe composición; decisión previa de arquitectura). La "aparición" se resuelve dentro del contenido: leve fade de las primeras tarjetas visibles al abrir.
- Selección/hover (ya 150 ms): afinar curva y borde acento.
- Edición del título: transición breve entre etiqueta y campo (opacidad + borde acento).
- Menús contextuales y submenús: efecto nativo de Qt (`setEffectEnabled`) coherente con el tema.
- Página de Configuración: fade de 90 ms al entrar/salir; el aviso "Guardado" aparece/desaparece suavemente.
- Revisión final de tipografía, espaciado y contraste en los 5 temas.
**Pruebas:** movimiento reducido desactiva todo; ninguna animación bloquea el hilo (benchmark `windows_benchmark.py` antes/después, apertura del panel y desplazamiento con 200 clips); sin fugas de efectos (`QGraphicsOpacityEffect` se retira); regresión completa; checklist final: integridad de datos, títulos, atajos, bandeja, íconos, foco/teclado, pegado, Stack, Pinboards, reinicio, 5 temas.
**Skills:** `animate`, `animation-performance`, `emil-design-eng`, `premium-motion-design` (principios), `replica-test`, `done`.
**Aceptación:** sensación fluida sin medir peor que la línea base; nada decorativo que reste rendimiento.
**Reversión:** cada animación tras una constante/bandera en `windows_design.py`; desactivable sin tocar datos.

---

## 10. Respaldos, seguridad y datos (transversal)
- Antes de cualquier migración: backup con la API de SQLite a `%LOCALAPPDATA%\Nexus\backups\nexus-<fecha>-pre-E<n>.sqlite` + `PRAGMA integrity_check` + conteos (clips, Pinboards, `pinboard_items`, `stack_state`). Nunca se modifica la base personal con pruebas: todas usan copias o `:memory:`.
- No se imprimen valores de secretos ni contenido de clips en logs ni informes.
- Suite completa tras cada entrega; compatibilidad Linux: solo cambios aditivos y, si es posible, ejecutar su suite.
- No hay push ni reescritura de historial; commits solo bajo tu aprobación.

## 11. Verificación end-to-end (cada entrega)
1. `pytest` completo en `windows-reference`.
2. Instancia real (`run-nexus-windows.cmd`): flujos de la entrega, reinicio y persistencia.
3. Capturas en los 5 temas contra la línea base.
4. Benchmark antes/después.
5. Cierre con `done` (evidencia) antes de proponer el commit.

## 12. Primeros pasos al aprobar (solo Entrega 1)
1. Guardar esta especificación como `windows-reference\NEXUS_PLAN_MAESTRO.md` (archivo nuevo, sin commitear).
2. **Fase 0 — entorno aislado (C1):** crear `tools/windows_sandbox_run.py` + gancho `NEXUS_SANDBOX`, con sus tests de aislamiento; crear la copia de la base por la API de backup y verificarla.
3. **Línea base:** `pytest` completo (esperado 614), `tools/windows_benchmark.py` y capturas de los 5 temas en el sandbox.
4. **Entrega 1** en este orden: tests de migración y de `set_title` → `storage.py` → tarjeta (`windows_card_delegate.py`) → F2/menú/editor en línea/Detalles (`windows_timeline.py`) → Stack → búsqueda y purga → pruebas de regresión.
5. Cierre: informe (resultados de tests, benchmark, capturas, pendientes, reversión) y **parada**: sin commit ni push, sin pasar a la Entrega 2 hasta que lo apruebes.

## 13. Supuestos que no pude verificar sin ejecutar
- Si `nexus-icon.png` o el glifo `QPainter` es exactamente la "N azul" que quieres (se confirma en 4A).
- Estado de la caché de íconos y de accesos directos/pin existentes en tu equipo.
- Que `QSystemTrayIcon` resuelva el `@2x` de forma automática.
- Si el menú contextual compartido admite la extensión Windows sin un gancho en `timeline_window.py`.
