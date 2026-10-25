# Nexus Windows — Aceptación, actualización y commits

Estado de este documento: **preparado, nada de la Parte 4 está ejecutado.** Tu Nexus habitual (procesos 13352 y 11288 al preparar esto) no se ha cerrado, reiniciado, sustituido ni migrado, y tu base real solo se ha leído (copia de seguridad en modo solo lectura).

Contenido: 1) revisión de Git y commits propuestos · 2) prueba de aceptación aislada · 3) checklist manual · 4) procedimiento de actualización y reversión.

---

## 1. Git: qué hay y qué se propone

Rama `windows-native`, base `d089279`. **Nada se ha subido (sin push) ni reescrito.**

### 1.1 Ya commiteado en local (aprobado entrega a entrega)

| Entrega | Commit | Contenido |
|---|---|---|
| E1 títulos + sandbox | `ac94902` | `storage.py` (columnas `title`, búsqueda, purga), tarjeta, F2, editor en línea, Detalles, Stack, `windows_sandbox.py`, `tools/windows_sandbox_run.py`, `NEXUS_PLAN_MAESTRO.md`, 3 archivos de tests |
| E2 limpieza UI | `5894cfb` | sin leyenda ni Expandir/Contraer, cabecera, Stack con tooltips, foco de tarjeta, menús; tests y herramientas de captura ajustados |
| E3 Configuración y atajos | `cce66dd` | `windows_shortcuts.py`, `windows_settings_page.py`, registro global seguro (`GlobalHotkeys`), `save_settings` atómico, 5 archivos de tests |
| E4 íconos y bandeja | `0ce0a99` | `windows_icons.py`, `assets/icons/*` (PNG + `.ico`), `TrayController`, `gui-scripts`, `build_windows_icons.py`, `windows_make_shortcut.ps1`, 2 archivos de tests |

### 1.2 Pendiente de commit (propuesta, **no ejecutada**)

| # | Mensaje propuesto | Archivos |
|---|---|---|
| 5 | `feat(windows): brief content fades and final motion contract` (E5) | `src/nexus_linux/windows_design.py`, `src/nexus_linux/windows_motion.py`, `src/nexus_linux/windows_timeline.py`, `tests/test_windows_motion_final.py` |
| 6 | `chore(tools): isolated acceptance run and upgrade/rollback data tools` | `src/nexus_linux/windows_app.py` (solo una línea de log de arranque), `tools/windows_acceptance.py`, `tools/windows_db_tools.py`, `tools/windows_sandbox_run.py`, `tools/windows_make_shortcut.ps1` (`-CodeRoot`), `tests/test_windows_upgrade_tools.py`, `ACEPTACION_Y_ACTUALIZACION.md` |
| 7 | `chore: ignore local data, backups and screenshots` | `.gitignore`: añadir `capturas-entrega-*/`, `*.sqlite`, `*.sqlite-wal`, `*.sqlite-shm`, `*.lnk`, `backups/`, `settings.json` |

Cada commit termina con la línea `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Los commits se hacen añadiendo **archivos concretos** (`git add <ruta>`), nunca `git add .`.

### 1.3 Se excluye a propósito

| Qué | Por qué |
|---|---|
| `install.sh`, `uninstall.sh` | Solo cambió el modo de archivo (755 → 644, 0 líneas), con fecha del 6 de octubre: **anterior a este trabajo** y ajeno. No se tocan. |
| `capturas-entrega-1` … `-5` (125 PNG) | Capturas de revisión; tu arquitectura dice que no se versionan. Contienen clips reales de tu copia (ver 2.4). |
| `.venv/`, `__pycache__/`, `.pytest_cache/`, `*.egg-info/` | Ya ignorados. |

### 1.4 Revisión de contenido sensible (sobre todo lo cambiado desde la base)

- Extensiones tocadas: `.py` (33), `.png` (10) y `.ico` (1, solo los íconos de E4), `.sh` (2, solo modo), `.ps1`, `.md`, `.toml`. **Ninguna** `.sqlite`, `.db`, `.env`, `.json`, `.lnk`, `.bak`, `.log`, `.key`, `.pem`.
- Patrones de secretos (`api_key`, `password`, `token`, `bearer`, `sk-`, `ghp_`, `AKIA`, claves privadas, `@gmail`, tu apellido): 0 coincidencias reales. Las palabras `secret`/`password`/`token` aparecen solo como «tokens» de diseño y como la regla «no imprimir secretos».
- Archivos sensibles ya rastreados en el repositorio: ninguno.
- Única mención personal: la ruta `C:\Users\Admin\Projects\Nexus\windows-reference` en `NEXUS_PLAN_MAESTRO.md` (ya en `ac94902`). No es un secreto.
- Las herramientas imprimen **solo conteos e integridad**, nunca contenido de clips (hay un test que lo comprueba).

---

## 2. Prueba de aceptación aislada

### 2.1 Cómo abrirla

```powershell
cd C:\Users\Admin\Projects\Nexus\windows-reference
.\.venv\Scripts\python.exe tools\windows_acceptance.py start --open
```

Opciones (todas opcionales):

| Opción | Para qué |
|---|---|
| `--open` | Abre el panel al arrancar. |
| `--reduced-motion` | Arranca con movimiento reducido **solo en ese proceso** (no cambia ningún ajuste de Windows). |
| `--allow-hook` | Permite el hook de `Ctrl+V` (solo para la prueba de Stack, ver 3.J). |
| `--refresh` | Rehace la copia de la base desde la real (solo lectura). |
| `--code CARPETA` | Ejecuta el código de otra carpeta (p. ej. una copia `git archive` del release). |

Al terminar `start` imprime los PID de la prueba, los de tu Nexus habitual («no se toca»), la plataforma Qt (`windows`), la carpeta de datos y el código usado. Si algo no cuadra, **aborta y cierra lo que arrancó**.

### 2.2 Cómo cerrarla

1. Preferido: clic derecho en el ícono de bandeja cuyo tooltip dice **«Nexus (sandbox)»** → **Salir de Nexus**.
2. O bien:
   ```powershell
   .\.venv\Scripts\python.exe tools\windows_acceptance.py stop
   .\.venv\Scripts\python.exe tools\windows_acceptance.py status
   ```
   `stop` solo termina los procesos que **esta herramienta arrancó** (identificados por PID **y** hora de creación). Después muestra que tus procesos siguen vivos.

### 2.3 Qué queda aislado (y cómo está comprobado)

| Requisito | Cómo se cumple | Evidencia |
|---|---|---|
| Base SQLite independiente | `LOCALAPPDATA` apunta a `%TEMP%\nexus-sandbox`; la base es una copia (API de backup, solo lectura) | El proceso se niega a arrancar si alguna ruta de datos cae fuera de `%TEMP%` o dentro de tu carpeta real; tests |
| Atajos que no interfieren | `Ctrl+Shift+9` (panel) y `Ctrl+Shift+7` (Stack) en lugar de `Ctrl+Shift+V/C`; «Restablecer» vuelve a esos, nunca a los reales | Tests de `ShortcutStore` con valores por defecto del sandbox |
| Monitor del portapapeles desactivado | Arranca en **pausa** (el monitor ignora el portapapeles); sin hook de `Ctrl+V` salvo `--allow-hook` | Tests; tooltip y punto ámbar en el ícono |
| No modifica tu configuración | `settings.json`, `theme.json` y registro quedan en la carpeta temporal | El arranque comprobado no tocó `%LOCALAPPDATA%\Nexus` |
| Ícono y bandeja reales | Se ejecuta con la plataforma `windows`; ícono propio con tooltip «Nexus (sandbox)» y otra identidad de barra de tareas (`StoreM.Nexus.Sandbox`) | Ventanas con ícono grande y pequeño confirmadas en un proceso real |
| **Sin `QT_QPA_PLATFORM=offscreen`** | La herramienta **elimina** esa variable del entorno hijo y exige que el hijo escriba `platform=windows` en su propio log; si no, aborta | Lo probé arrancando **con `QT_QPA_PLATFORM=offscreen` puesto a propósito en mi entorno**: el hijo informó `windows` |
| No puede cerrar ni modificar tu instancia | Mutex propio (`Local\NexusWindowsSandbox`); `stop` solo actúa sobre PID+hora registrados; ninguna parte de `src/` contiene `taskkill`, `TerminateProcess`, `os.kill` ni `Stop-Process` (test) | Tras varios arranques y cierres de prueba, tus procesos 13352 y 11288 siguieron intactos |

### 2.4 Lo que SÍ puede rozar tu Nexus habitual (léelo antes de probar)

1. **Pegar desde la prueba** escribe en el portapapeles de Windows: tu Nexus real lo ve como una *copia* y reordena ese clip arriba en **tu** historial real. No se pierde ni se altera nada.
2. **Si activas la captura en la prueba**, lo que copies se guarda en las dos bases (la real, como siempre, y la de prueba).
3. `Ctrl+Shift+9` y `Ctrl+Shift+7` son globales mientras la prueba esté abierta. Si otra aplicación los usa, cámbialos en Configuración de la prueba.
4. **`--allow-hook`**: mientras el Stack de la prueba esté activo, `Ctrl+V` se intercepta en todo Windows. Úsalo solo en la prueba J y cierra el Stack (`Esc`) al terminar.
5. La copia de la base contiene **tus clips reales**: verás tu contenido en pantalla. No compartas capturas sin revisarlas.
6. La prueba tiene su propia identidad en la barra de tareas: no se mezcla con un posible anclaje de tu Nexus.

### 2.5 Cómo habilitar temporalmente lo que algunas pruebas necesitan

- **Captura del portapapeles** (pruebas I y J): Configuración (menú `⋯` → Configuración) → «Captura del portapapeles» → **Captura activa**. Vuelve a **Captura en pausa** al terminar o cierra la prueba.
- **Atajo global** (pruebas D y J): ya están asignados `Ctrl+Shift+9` / `Ctrl+Shift+7`; no hay que habilitar nada. Tu `Ctrl+Shift+V` seguirá abriendo **tu** Nexus.
- **Hook de `Ctrl+V`** (solo J): cierra con `stop` y vuelve a abrir con `start --allow-hook`.

---

## 3. Checklist de aceptación manual

Marca cada casilla. Si algo falla, anota el número (p. ej. «F3») y no sigas con esa sección.

**Antes de empezar:** `start --open`. Abre Bloc de notas para las pruebas de pegado. Confirma en el mensaje de `start`: plataforma `windows` y tus PID «no se toca».

### A. Íconos y bandeja (visual)
- [ ] A1. En la bandeja (puede estar en «Mostrar íconos ocultos») hay un ícono con la **N de contorno azul claro**; su tooltip dice «Nexus (sandbox) · captura en pausa» y lleva un **punto ámbar**.
- [ ] A2. Con la captura activa (ver H) el punto ámbar desaparece y el tooltip es «Nexus (sandbox)».
- [ ] A3. Con el panel abierto, `Alt+Tab` muestra la N (no el ícono de Python ni el de CMD).
- [ ] A4. No aparece ninguna ventana de consola al iniciar.
- [ ] A5. El ícono se ve legible sobre barra de tareas clara y oscura (cámbialo en Windows si quieres comprobarlo).

### B. Menú de bandeja
- [ ] B1. Clic izquierdo en el ícono abre/cierra el panel.
- [ ] B2. Clic derecho muestra: Abrir Nexus · Administrar pinboards y biblioteca… · Nexus Stack · Pausar/Reanudar captura · Configuración… · Atajos de teclado… · Estilo ▸ · Salir de Nexus.
- [ ] B3. «Configuración…» y «Atajos de teclado…» abren la página de Configuración.
- [ ] B4. «Estilo ▸» cambia el tema y la marca de verificación sigue al tema activo.

### C. Panel limpio
- [ ] C1. No hay botón **Expandir/Contraer** en el Historial ni en ninguna otra vista.
- [ ] C2. No hay leyenda «Ctrl+F Buscar · Enter Pegar · Esc Cerrar» en Historial, Pinboards, Configuración ni Stack.
- [ ] C3. La cabecera muestra: N · Nexus · PORTAPAPELES … contador · Estilo ▾ · ×.
- [ ] C4. Debajo de las tarjetas no queda una franja vacía.
- [ ] C5. `Tab` alterna el foco entre la búsqueda y las tarjetas (no cambia el tamaño del panel).

### D. Atajos globales y teclado
- [ ] D1. `Ctrl+Shift+9` abre/cierra el panel de la prueba. `Ctrl+Shift+V` abre **tu** Nexus habitual, no la prueba.
- [ ] D2. `Ctrl+F` enfoca la búsqueda · `Esc` cierra · `Ctrl+←/→` cambia de Pinboard · `Ctrl+1…9` pega la tarjeta N (ver F) · `F2` renombra.
- [ ] D3. Escribir cualquier letra con la lista enfocada escribe en la búsqueda.

### E. Títulos (Historial y Pinboards)
- [ ] E1. Selecciona una tarjeta y pulsa **F2**: aparece un campo sobre el título. Escribe «Auditoría completa de ciberseguridad» y pulsa **Enter**: el título queda en negrita arriba y el contenido, atenuado, debajo. **No se pegó nada ni se cerró el panel.**
- [ ] E2. **Esc** durante la edición cancela y no guarda. Clic derecho → «Renombrar…» hace lo mismo que F2.
- [ ] E3. «Detalles» (clic derecho) muestra el título editable.
- [ ] E4. Un título de más de 80 caracteres se corta; uno vacío quita el título; un título largo se ve con «…».
- [ ] E5. Abre el Pinboard «Prompts»: sus tarjetas muestran los títulos (el mismo que en Historial).
- [ ] E6. Eliminar un clip **con título** pide confirmación; uno sin título, no.
- [ ] E7. Cierra y vuelve a abrir el panel: los títulos siguen.

### F. Búsqueda y pegado del contenido original
- [ ] F1. Escribe en la búsqueda una palabra **solo del título** (p. ej. «ciberseguridad», sin acento o con mayúsculas): aparece ese clip, el primero.
- [ ] F2. Una palabra del contenido también lo encuentra.
- [ ] F3. En Bloc de notas: enfócalo, pulsa `Ctrl+Shift+9`, elige la tarjeta titulada y **Enter**: se pega el **contenido original**, jamás el título. Repite con `Ctrl+3` (tarjeta 3) y con «Pegar como texto plano» (`Mayús+Enter`).

### G. Configuración de atajos y conflictos
- [ ] G1. Configuración lista cada atajo con su descripción y su etiqueta **Global** o **En Nexus**; los fijos (Enter, Esc, Tab, Supr, Ctrl+V del Stack) aparecen con línea discontinua y un motivo.
- [ ] G2. Click en el campo de «Buscar», pulsa `Ctrl+K`: se guarda («Guardado: Ctrl+K»); ahora `Ctrl+K` busca y `Ctrl+F` ya no.
- [ ] G3. Conflicto interno: en «Renombrar» pulsa `Ctrl+K` → mensaje «ya la usa «Buscar»» y el valor anterior se conserva.
- [ ] G4. Reservado: en «Mostrar u ocultar Nexus» pulsa `Alt+F4` → «está reservada por Windows». Sin modificador (p. ej. `H`) → pide Ctrl o Alt.
- [ ] G5. **Conflicto real con otra aplicación**: en «Mostrar u ocultar Nexus» pulsa `Ctrl+Shift+V` (el de tu Nexus habitual) → «Otra aplicación ya usa esa combinación» y el atajo de la prueba **no cambia** (tu Nexus habitual lo sigue usando).
- [ ] G6. Cambia el modificador de «Pegado rápido» a `Alt+1`: ahora `Alt+1…9` pegan y `Ctrl+1…9` no.
- [ ] G7. «Restablecer» (por atajo) y «Restablecer todos» devuelven los valores de la prueba (`Ctrl+Shift+9/7`, `Ctrl+F`, …). Ninguno vuelve a `Ctrl+Shift+V`.
- [ ] G8. Cierra la prueba con `stop`, ábrela de nuevo: los atajos que cambiaste **persisten**.

### H. Pausar y reanudar captura (solo en la prueba)
- [ ] H1. Ícono con punto ámbar = en pausa. Copia texto en Bloc de notas: **no** aparece en la prueba.
- [ ] H2. Bandeja → «Reanudar captura…»: el punto desaparece y Configuración muestra «Captura activa» (los dos están sincronizados).
- [ ] H3. Copia una frase nueva: aparece arriba en el historial de la prueba.
- [ ] H4. Configuración → «Captura en pausa»: la bandeja vuelve a «Reanudar captura…» y el ámbar reaparece.
- [ ] H5. Cierra y reabre: el estado de pausa se conserva (pero `start` siempre arranca la prueba en pausa).

### I. Animaciones y movimiento reducido
- [ ] I1. Al abrir el panel, el contenido aparece con un fundido muy breve; la ventana en sí no parpadea ni se vuelve gris.
- [ ] I2. La página Configuración/Pinboards entra con un fundido corto; el campo de título aparece suave; «Guardado» también.
- [ ] I3. Nada se siente lento ni se repite: hover y selección de tarjetas son suaves.
- [ ] I4. `stop` y `start --open --reduced-motion`: todo aparece **al instante**, sin fundidos.

### J. Stack y navegación por teclado
*(Requiere captura activa y `--allow-hook`; ver 2.5. Tu Stack habitual debe estar inactivo.)*
- [ ] J1. `Ctrl+Shift+7` abre Stack y empieza a recoger. Copia A, B y C (desde Bloc de notas).
- [ ] J2. Las filas muestran título (si lo tienen) o contenido. Alterna **FIFO/LIFO**.
- [ ] J3. En Bloc de notas, `Ctrl+V` pega el siguiente elemento cada vez, en el orden elegido.
- [ ] J4. `Supr` o clic derecho quita un elemento; `Esc` cierra Stack y **libera `Ctrl+V`** (pégalo en otro lugar para comprobar que vuelve a ser normal).
- [ ] J5. No hay texto largo de ayuda en el Stack; al pasar el ratón sobre «Pegar siguiente» aparece un tooltip.

### K. Los cinco temas
Cambia con «Estilo» (panel o bandeja) y en cada tema revisa: Historial con títulos, edición, Configuración, Stack y menú de bandeja.
- [ ] K1. Liquid Glass · [ ] K2. Midnight Teal · [ ] K3. Minimalista · [ ] K4. Storem · [ ] K5. Tech Neon
- Para cada uno: texto legible (también el aviso rojo de errores y el verde «Guardado»), bordes y foco visibles, sin elementos cortados.

### L. Cierre limpio
- [ ] L1. «Salir de Nexus» del ícono de la prueba cierra solo la prueba; **tu Nexus habitual sigue funcionando** (`Ctrl+Shift+V` lo abre).
- [ ] L2. `status` muestra «Prueba aislada: cerrada».

**Criterio:** apto para actualizar si A–L están marcadas. Cualquier fallo en E, F, G (G5 incluido) o L bloquea la actualización.

---

## 4. Procedimiento de actualización (NO ejecutado)

**Principio:** nada se borra ni se sobrescribe; cada paso tiene un punto de retorno; ningún comando imprime contenido de clips. Los comandos asumen una ventana nueva de PowerShell en el proyecto. **Requisito previo:** los commits 5–7 de la sección 1.2 hechos, para que el release salga de un commit exacto.

### Lo que ya ensayé (en una copia, con el código anterior y el nuevo reales)

| Ensayo | Resultado |
|---|---|
| Respaldo por API de backup + `verify` (checksum y conteos) | Correcto |
| Versión anterior usa la base después del respaldo (2 clips nuevos) | Correcto |
| Versión nueva abre la base y **migra** (columna `title`): ningún clip perdido, integridad `ok` | Correcto |
| Uso con la nueva (3 títulos, 2 clips) y **vuelta a la versión anterior sobre la base migrada** | La versión anterior **funciona** con la base migrada |
| Sin protección, la versión anterior **purga los clips con título** (0 de 3 sobreviven) | Por eso existe `protect-titled`, que los guarda en un Pinboard |
| `protect-titled` + versión anterior | 3 de 3 sobreviven; sigue capturando |
| Restaurar el respaldo **y fusionar lo posterior** (`merge-newer`) | Cero clips perdidos, integridad `ok`, Pinboards y membresías incluidos |

*No ensayé* el arranque real sobre tu base real, ni el acceso directo en tu carpeta de inicio, ni la barra de tareas.

### U0. Preparar variables y comprobar
```powershell
cd C:\Users\Admin\Projects\Nexus\windows-reference
$py    = ".\.venv\Scripts\python.exe"
$nexus = "$env:LOCALAPPDATA\Nexus"
$stamp = Get-Date -Format yyyyMMddTHHmmss
$bk    = "$nexus\backups\pre-upgrade-$stamp"
$rel   = "$nexus\app"
$newId = (git rev-parse --short HEAD)
$old   = "$rel\v1-d089279"
$new   = "$rel\v2-$newId"

git status --short                      # solo deben verse install.sh, uninstall.sh y capturas-entrega-*
$env:QT_QPA_PLATFORM='offscreen'; & $py -m pytest -q -p no:cacheprovider; Remove-Item Env:QT_QPA_PLATFORM
Get-Process pythonw | Select-Object Id, StartTime          # anota los PID de tu Nexus (hoy 13352 y 11288)
(Get-Item "$nexus\data\nexus.sqlite").Length               # tamaño de la base
```
Detente si algún test falla o `git status` muestra algo más.

### U1. Respaldo de la SQLite real (con Nexus aún en marcha; la API es de solo lectura)
```powershell
New-Item -ItemType Directory $bk | Out-Null
& $py tools\windows_db_tools.py backup "$nexus\data\nexus.sqlite" $bk --label pre-upgrade
$b1 = (Get-ChildItem $bk -Filter *.sqlite | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
& $py tools\windows_db_tools.py verify $b1        # exige "ok": true, sha256_matches true, counts_match_manifest true
Copy-Item "$nexus\config\settings.json", "$nexus\config\theme.json" $bk
Copy-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Nexus.lnk" $bk
git rev-parse HEAD | Out-File "$bk\code-commit.txt"
```
**Conserva `$bk`** (ruta: `%LOCALAPPDATA%\Nexus\backups\pre-upgrade-<fecha>`). No lo borres en 30 días.

### U2. Copias del código (releases inmutables, desde commits exactos)
```powershell
New-Item -ItemType Directory $old, $new | Out-Null
git archive --format=tar d089279  -o "$bk\v1.tar"; tar -xf "$bk\v1.tar" -C $old
git archive --format=tar HEAD     -o "$bk\v2.tar"; tar -xf "$bk\v2.tar" -C $new
(Get-ChildItem "$old\src" -Recurse -File).Count; (Get-ChildItem "$new\src" -Recurse -File).Count   # ambos > 0
Test-Path "$new\src\nexus_linux\assets\icons\nexus.ico"                                              # True
```
Así tu app habitual deja de depender del árbol de trabajo; seguirá usando el intérprete y PySide6 de `.venv`.

### U3. Detener tu Nexus de forma controlada
1. Clic derecho en su ícono de bandeja → **Salir de Nexus** (cierre limpio: libera atajos y cierra SQLite).
2. Comprueba:
   ```powershell
   Start-Sleep 5; Get-Process pythonw -ErrorAction SilentlyContinue | Select-Object Id    # no deben estar tus PID
   ```
3. Solo si tras 15 s siguen vivos (SQLite en WAL es duradero y Stack guarda su estado en cada cambio): `Stop-Process -Id <PID de tu Nexus>`.
4. **Punto de retorno de datos** (lo último que capturó tu Nexus):
   ```powershell
   & $py tools\windows_db_tools.py backup "$nexus\data\nexus.sqlite" $bk --label post-stop
   $b2 = (Get-ChildItem $bk -Filter *post-stop.sqlite).FullName
   & $py tools\windows_db_tools.py verify $b2
   ```

### U4. Migrar la base (controlado, con Nexus detenido)
```powershell
$env:PYTHONPATH = "$new\src"
& $py -c "from nexus_linux.storage import StorageService; s=StorageService(); s.close(); print('migración aplicada')"
Remove-Item Env:PYTHONPATH
& $py tools\windows_db_tools.py verify "$nexus\data\nexus.sqlite"
```
Esperado: `integrity: ok`, **los mismos clips que `post-stop`**, y la columna `title` creada (aditiva: no reescribe ninguna fila). Si algo difiere: **no sigas**, ve a R-B.

### U5. Aplicar el código y el acceso directo/ícono
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\windows_make_shortcut.ps1 `
  -Path "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Nexus.lnk" -CodeRoot $new -Force
```
(El `.lnk` anterior ya está respaldado en `$bk`.) Ese acceso directo ejecuta `pythonw.exe` con el código de `$new`, sin consola y con el ícono de Nexus: el ícono de CMD desaparece también del inicio automático.

### U6. Iniciar y comprobar
```powershell
Start-Process "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Nexus.lnk"
Start-Sleep 8
Select-String 'run platform=' "$nexus\cache\nexus.log" | Select-Object -Last 1     # sandbox=False, data=...\Nexus, code=...\app\v2-<id>\src\nexus_linux
```
Comprobación funcional (5 minutos): `Ctrl+Shift+V` abre el panel con tus clips y Pinboards; ícono de la N en bandeja y sin ventana de consola; pon un título (F2), búscalo, pega y confirma que se pega el contenido; Configuración abre y muestra tus atajos; **Pausar/Reanudar captura** funciona; copia algo nuevo y aparece; tu Stack abre con `Ctrl+Shift+C`. Si algo falla: R-A.

### U7. Barra de tareas
Hoy no tienes Nexus anclado. Con Nexus abierto: clic derecho en su botón de la barra de tareas → «Anclar a la barra de tareas» (guarda ícono e identidad).

### U8. Después
Mantén `$bk` y `$old` al menos 30 días y observa 24–48 h. Nada de lo anterior se borra automáticamente.

---

### R-A. Revertir solo el código (los datos se conservan)
Cuándo: algo del código nuevo falla pero la base está bien. La versión anterior **funciona con la base migrada** (ensayado).
```powershell
# 1. Cierra Nexus (bandeja → Salir). No arranques la versión anterior mientras la nueva corre.
& $py tools\windows_db_tools.py backup "$nexus\data\nexus.sqlite" $bk --label pre-rollback
& $py tools\windows_db_tools.py protect-titled "$nexus\data\nexus.sqlite"      # protege los clips con título de la purga de la versión anterior
powershell -NoProfile -ExecutionPolicy Bypass -File tools\windows_make_shortcut.ps1 `
  -Path "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Nexus.lnk" -CodeRoot $old -Force
Start-Process "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Nexus.lnk"
```
Con la versión anterior los títulos no se ven, pero siguen en la base, dentro del Pinboard «Con título (protegidos)». Si luego actualizas otra vez, puedes borrar ese Pinboard (solo borra la relación, no los clips). Para volver al `.lnk` original: `Copy-Item "$bk\Nexus.lnk" "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Nexus.lnk" -Force`.

### R-B. Volver a los datos del respaldo **sin perder los clips nuevos**
Cuándo: la base nueva se dañó o quieres volver al estado de un respaldo. **Nunca restaures copiando el respaldo encima:** perderías lo capturado después.
```powershell
# 1. Cierra Nexus. Elige el respaldo ($b2 = post-stop, o $b1 = pre-upgrade).
$fixed = "$bk\restaurada-$stamp.sqlite"
& $py tools\windows_db_tools.py merge-newer --backup $b2 --current "$nexus\data\nexus.sqlite" --out $fixed
& $py tools\windows_db_tools.py verify $fixed
# 2. Solo si verify da ok: aparta la base actual (no se borra) y pon la fusionada.
$sideline = "$bk\base-con-problema-$stamp"; New-Item -ItemType Directory $sideline | Out-Null
Move-Item "$nexus\data\nexus.sqlite*" $sideline
Copy-Item $fixed "$nexus\data\nexus.sqlite"
```
`merge-newer` construye la base a partir del respaldo y le **añade** todo lo que la actual tiene de más (clips nuevos, marcas de tiempo más recientes, títulos, Pinboards y membresías). Después arranca la versión que prefieras (R-A o U6).

**Si la base actual no se puede leer en absoluto** (corrupción grave): `merge-newer` no puede abrirla. Aparta la base dañada igual que arriba, copia `$b2` como `nexus.sqlite` (pierdes solo lo capturado desde `post-stop`, que es poco: lo hiciste con Nexus ya cerrado) y, si necesitas lo posterior, intenta recuperarlo de la base dañada con `sqlite3 <dañada> ".recover"` hacia un archivo nuevo y fusiónalo con `merge-newer`.

### Reglas de seguridad de todo el procedimiento
- Nunca dos instancias sobre la misma base; nunca arrancar la versión anterior con la nueva abierta (no tiene modo sandbox y compartiría el mutex y los atajos reales).
- Cada herramienta se niega a sobrescribir archivos existentes.
- Los comandos imprimen solo conteos, integridad y rutas.
- No se hace `git push`, ni se reescribe historial, ni se toca `install.sh`/`uninstall.sh`.

---

## 5. Resumen de herramientas nuevas

| Herramienta | Uso |
|---|---|
| `tools/windows_acceptance.py` | `start` / `status` / `stop` de la prueba aislada |
| `tools/windows_db_tools.py` | `backup`, `verify`, `merge-newer`, `protect-titled` |
| `tools/windows_make_shortcut.ps1` | `.lnk` sin consola con el ícono de Nexus; `-CodeRoot` para un release concreto |
| `tools/windows_sandbox_run.py` | Preparación de la copia de la base (la usa la herramienta de aceptación) |
