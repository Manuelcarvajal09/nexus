# Nexus Windows 0.2.0rc1 — informe de pruebas (2026-11-06)

Paquete evaluado: construido desde el commit `50da205` (tag local `nexus-windows-0.2.0rc1`).
Wheel SHA-256: `c0e4f74305e496da054b4ba3c9bded5609222fa7b0330dd100d88916587cad48`
(el mismo hash en dos ejecuciones independientes de `rc1_build.py`; cada una compila dos veces).

## Resumen por nivel
| Nivel | Qué se ejecutó | Resultado |
|---|---|---|
| Automático | `pytest` offscreen, venv de desarrollo (PySide6 completo) | **1055 pasan** |
| Automático | La misma suite en un venv limpio con **solo** PySide6-Essentials 6.12.0 + pytest | **1055 pasan** |
| Windows real (host, carpetas temporales) | `rc1_lifecycle_check.py`: 25 comprobaciones × 3 ejecuciones | **75/75 pasan** |
| Windows real (host), antes de RC1 | `windows_native_e2e.py` (atajos de prueba, foco, menú, reinicio) | 29/30 limpias + 1 inconclusa por robo de foco ajeno (fase anterior) |
| Windows real (host) | Auditoría de layout 5 temas × 4 escalas × 6 superficies | 120 combinaciones, 0 problemas (fase anterior) |
| Sandbox | — | **Pendiente: Windows Sandbox no está activado** |

## Lo que cubre el ciclo de vida (Windows real, aislado en temp, datos sintéticos, sin portapapeles)
Integridad del paquete · paquete manipulado rechazado por «hash mismatch» sin instalar nada · raíz fuera de temp rechazada sin
`--allow-real-install` · instalación limpia sin red con hashes fijados · lanzador sin consola · el acceso directo de Inicio del
usuario no cambia · la app **instalada** arranca en la plataforma Windows real ejecutando el código instalado (no el repositorio) ·
datos intactos tras ejecutar y parar · paquete de actualización dañado rechazado sin tocar la versión actual · actualización con copia
de seguridad verificada (6 clips, 1 pinboard, 1 título) · datos intactos tras actualizar · la versión nueva arranca sobre los datos
existentes · reversión y avance entre versiones · la versión revertida arranca sobre los mismos datos · desinstalación que conserva los
datos · reinstalación posterior que ve los datos antiguos · tus procesos Nexus con los mismos PID antes y después (`3276, 12516`).

## Hallazgos de esta fase y su corrección
| Hallazgo | Cómo se encontró | Corrección |
|---|---|---|
| Ejecutar `rc1_manage.py` desde el paquete creaba `tools/__pycache__` **dentro** del paquete e invalidaba `SHA256SUMS` (en Sandbox, solo lectura) | Primera ejecución del ciclo de vida: la instalación fallaba | `sys.dont_write_bytecode = True` + regresión (`test_running_the_shipped_tools_never_writes_into_the_package`) |
| Una prueba de rechazo pasaba por un motivo equivocado (solo miraba el código de salida) | Revisión tras el fallo anterior | Las comprobaciones exigen el motivo («hash mismatch», «allow-real-install») |
| Instalación interrumpida dejaba una carpeta de versión que bloqueaba el reintento («already installed») | Revisión del flujo de recuperación | Una versión sin `INSTALLED.json` se considera incompleta y se rehace; una completa nunca se sobrescribe (2 pruebas) |
| `test_windows_app_smoke` asumía una instalación editable | Primera suite en venv limpio: 2 fallos | El subproceso recibe `PYTHONPATH=src` |
| La dependencia declarada (`PySide6`) arrastraba WebEngine/Addons/Pdf sin usarse | Revisión de imports (solo QtCore/QtGui/QtWidgets/QtDBus) | `PySide6-Essentials`; validado con la suite completa en venv limpio |

## No ejecutado (y por qué)
* **Captura de texto/imagen, pegado en otra app, Ctrl+V del Stack, duplicados reales, atajos por defecto, recuperación con
  conflicto de atajo y cierre forzado en entorno real**: escritas como `release/sandbox/guest/functional_tests.py` (T01–T10) pero
  **no ejecutadas en ninguna máquina**; solo se probó su `--dry-run` y que se **niegan** a correr fuera de Windows Sandbox
  (`tests/test_rc1_sandbox.py`). Su resultado es desconocido: podrían fallar al ejecutarse (p. ej. temporización de hotkeys, Tk,
  runtime de VC++ en el sandbox).
* **Cierre limpio desde la bandeja**: la comprobación de ciclo de vida termina el proceso a la fuerza; el cierre ordenado solo
  está cubierto por la prueba automática de arranque/parada.
* **Inicio automático**: no implementado (el ajuste `launch_at_login` no lo lee ningún código).
* El `.wsb` se generó y su validación de protecciones pasa, pero **no se ha abierto**.

## Estado exacto
* Rama `ux-transformation`; paquete construido desde `50da205`; este informe va en un commit posterior.
* Sin cambios en tu instalación, tus datos ni tus procesos; sin push, merge ni publicación.
* Sin commitear desde antes: `install.sh` y `uninstall.sh` (solo difiere el modo 755→644; son instaladores de **Linux**), `work/` (laboratorio de capturas) y `capturas-ux/`.
