# Propuesta de consolidación Git — Nexus Windows

Estado: **ejecutada**. El plan se conserva como referencia; los commits y el
estado final están en `GIT_CONSOLIDATION_REPORT.md`. No se ha hecho push ni se
ha reescrito historial.

Rama actual: `windows-native`. Árbol de trabajo ya sucio antes de esta
iteración; hay cambios previos que no deben atribuirse a la navegación.

## Principios

1. No mezclar cambios de navegación con reparaciones de Stack ni con el
   sistema visual.
2. Revisar primero los cambios preexistentes de la base Windows; no son de esta
   tarea y pueden pertenecer a otro bloque.
3. Separar por comportamiento y por pruebas, no solo por archivo.
4. Cada commit propuesto debe pasar la suite completa (`614 passed`) antes de
   existir.
5. No incluir `__pycache__`, capturas temporales ni credenciales.
6. No tocar `install.sh`/`uninstall.sh` si el diff es solo CRLF (0 líneas
   reales); normalizar aparte si se decide.

## Grupos propuestos

### 0. Base runtime Windows (preexistente — revisar aparte)

Archivos ya modificados antes de la navegación:

- `src/nexus_linux/windows_keyboard.py`
- `src/nexus_linux/windows_clipboard.py`
- `src/nexus_linux/windows_paste.py`
- `src/nexus_linux/windows_platform.py`
- `src/nexus_linux/windows_app.py` (hunks de bootstrap, retención, atajos,
  bandeja y portapapeles; **no** los de Stack ni los de composición)
- `tests/test_windows_native.py`
- `tests/test_windows_app_smoke.py`
- `tests/test_windows_clipboard_safety.py`
- `tests/test_windows_payload_write.py`

Motivo: son la base que permite que la capa Windows arranque, capture, pegue y
registre atajos. Deben revisarse como un bloque propio antes de mezclarlos con
la navegación. Si ya estaban validados en otro bloque, se pueden commitear
primero; si no, quedan fuera de esta consolidación.

### 1. Sistema visual y composición

- `src/nexus_linux/styles.py` (paletas Midnight Teal y Tech Neon; 12/12 líneas)
- `src/nexus_linux/windows_design.py` (QSS, paletas, iconos, `navigationOptions`;
  ver nota de co-locación)
- `src/nexus_linux/windows_material.py`
- `src/nexus_linux/windows_backdrop.py`
- `src/nexus_linux/windows_motion.py`
- `src/nexus_linux/windows_card_delegate.py`
- `src/nexus_linux/windows_app.py` (solo hunks de `desktop_acrylic`,
  `attach_backdrop`, `native_surface_matches_probe`, superficies y temas)
- `tests/test_windows_material_motion.py`
- `tests/test_windows_backdrop.py`
- `tests/test_windows_premium_cards.py`
- `tests/test_windows_premium_integration.py`
- `WINDOWS_COMPOSITION_AUDIT.md`
- `WINDOWS_LAYOUT_REVIEW.md`

Orden sugerido: primero `styles.py` + `windows_design.py` (fundación), luego
material/backdrop/motion/cards, luego los hunks de `windows_app.py` y pruebas.
Si se quiere que la navegación no dependa del commit visual, adelantar
`windows_design.py` completo aquí y dejar la navegación solo con
`windows_timeline.py`; el QSS de `navigationOptions` queda en la fundación.

### 2. Reparaciones de Stack

- `src/nexus_linux/windows_stack.py`
- `src/nexus_linux/windows_app.py` (solo hunks de Stack):
  - `StackCaptureGate`
  - `toggle_stack_window`
  - `WindowsClipStack` / `WindowsStackWindow`
  - `load_persisted`, `start_collecting`, `stop_collecting`
  - señales `collecting_changed`, `paste_detected`, `hook_failed`
  - guardas de `captured_for_stack`
  - acción de bandeja `Nexus Stack`
  - ocultación de Stack al abrir Pinboards (`show_pinboards`)
- `tests/test_windows_stack_safety.py`
- `tests/test_windows_stack_transactions.py`
- `tests/test_windows_lifecycle_safety.py`
- `WINDOWS_STACK_DEEP_AUDIT.md`
- `WINDOWS_STACK_PASTE.md`

Motivo: el motor y el ciclo de vida de Stack son independientes de la barra.
No mezclar con temas ni con `windows_timeline.py`.

### 3. Navegación simplificada

- `src/nexus_linux/windows_timeline.py`
- `tests/test_windows_panel.py`
- `WINDOWS_NAVIGATION.md`

Contenido: eliminación de `Pinboards`/`+ Pinboard` solo en la capa Windows,
`QToolButton("⋯")`, menú `Administrar Pinboards`, footer sin título redundante,
scroll horizontal de History bajo 900 px y conservación de
`manage_pinboards_requested`.

Dependencia: usa `QToolButton#navigationOptions` de `windows_design.py`. Si ese
QSS se dejó en el commit visual, este commit no necesita tocarlo. Si se quiere
un commit de navegación autocontenido, hacer `git add -N` de
`windows_design.py` y seleccionar el hunk de `navigationOptions` antes del
commit visual; es la única parte del diseño que pertenece funcionalmente a la
navegación.

### 4. Herramientas de diagnóstico (opcional)

- `tools/windows_audit_backup.py`
- `tools/windows_benchmark.py`
- `tools/windows_composition_audit.py`
- `tools/windows_dwm_probe.py`
- `tools/windows_layout_probe.py`
- `tools/windows_nexus_material_probe.py`
- `tools/windows_stack_e2e.py`
- `tools/windows_stack_hook_e2e.py`
- `tools/windows_visual_preview.py`

No incluir `tools/__pycache__/` ni capturas. Estos scripts no son necesarios
para ejecutar Nexus; pueden quedar en un commit separado de herramientas o
fuera del repo si son temporales.

## Secuencia de revisión de referencia

```bash
# 0) Revisar estado y cambios preexistentes; no stagear todavía.
git status --short
git diff -- src/nexus_linux/windows_app.py
git diff -- src/nexus_linux/styles.py

# 1) Preparar el índice solo cuando se autorice.
#    Para archivos nuevos, intent-to-add permite revisar hunks de un archivo
#    que aún no está rastreado.
git add -N src/nexus_linux/windows_design.py src/nexus_linux/windows_timeline.py
git add -p src/nexus_linux/windows_app.py   # separar runtime / Stack / composición

# 2) Revisar exactamente lo que entraría; no commitear aún.
git diff --cached --stat
git diff --cached
git diff --cached --check

# 3) Verificar antes de cada commit.
./.venv/Scripts/python.exe -m pytest -q

# 4) Solo con aprobación explícita, crear commits por grupo.
#    Sugerencia de mensajes:
#    feat(windows): add visual system and single-contour composition
#    fix(windows): harden stack lifecycle and capture gating
#    feat(windows): simplify pinboard navigation
#    chore(tools): add windows diagnostic probes
```

No ejecutar los pasos 1–4 sin una revisión previa de los hunks de
`windows_app.py`; es el único archivo que mezcla tres preocupaciones.

## Verificación ya realizada

- Suite: **614 passed**.
- Instancia habitual: PID **12304**, tema **Tech Neon**, navegación nueva
  verificada.
- Pinboards reales intactos: `Mercadolibre` (id 1), `Prompts` (id 2);
  `pinboard_items = 0` al comprobar.
- SQLite `integrity_check = ok`.
- Logs sin `Traceback`/`ERROR`/`CRITICAL` desde el reinicio.
- Capturas y evidencia en
  `C:/Users/Admin/AppData/Local/Temp/opencode/nexus-nav-restart-20261024T150836Z/habitual/`.

## Riesgos y decisiones pendientes

1. **`windows_app.py`**: 193 líneas añadidas y 40 eliminadas; no commitear el
   archivo completo sin separar Stack, composición y bootstrap.
2. **`windows_design.py`**: es nuevo y contiene tanto temas como el QSS de
   navegación. Se propone dejarlo completo en el commit visual; si se exige
   separación estricta, usar `git add -N` + `git add -p`/`e` para aislar el
   hunk de `navigationOptions`.
3. **`install.sh`/`uninstall.sh`**: diff de 0 líneas, solo CRLF; excluir o
   normalizar en un commit aparte.
4. **`tools/__pycache__`**: no versionar; añadir a `.gitignore` si se decide
   conservar `tools/`.
5. **Cambios preexistentes**: no atribuirlos a la navegación. Revisar con el
   autor antes de commitearlos.
