# Nexus Windows — informe de validación del candidato (actualizado 2026-11-06, 21:50)

Candidato vigente: **0.2.0rc2**, construido desde el commit `719a2f3` (tag local `nexus-windows-0.2.0rc2`).
Wheel SHA-256: `98fddf6391f9b34bd94efb531b2646f7d5433b5bdde876b8d1fecd34e68f6b24`
(dos compilaciones idénticas byte a byte en la misma ejecución; el candidato anterior `0.2.0rc1`, tag `nexus-windows-0.2.0rc1`, queda intacto).

## Estado en una línea
**La validación NO está cerrada**: Windows Sandbox sigue sin estar disponible en este equipo, así que **ninguna de las doce pruebas
funcionales (T01–T12) se ha ejecutado**, y por tanto captura real, pegado entre aplicaciones, Ctrl+V del Stack y cierre limpio desde la bandeja
**no están probados**. No se sustituyeron por pruebas en la sesión personal.

## 1. Comprobación del entorno aislado (lo que se pidió hacer antes de ejecutar)
Lectura del sistema, sin activar nada, a las 21:50:
| Comprobación | Resultado |
|---|---|
| `WindowsSandbox.exe` (PATH y `System32`) | **No existe** |
| Servicios `vmcompute` / `CmService` | No existen |
| `HypervisorPresent` | `False` |
| Último arranque del equipo | 5-nov-2026 10:26 (no ha habido reinicio desde que se preparó la activación) |
| Estado de la característica (`Get-WindowsOptionalFeature`) | No consultable sin elevación |
Conclusión: la característica no está instalada/activa todavía (lo habitual: falta el **reinicio** posterior a habilitarla, o la habilitación no llegó a aplicarse).
Sin Sandbox no se puede verificar el aislamiento ni cumplir ninguna protección de `release/sandbox/README_SANDBOX.md`; **no se abrió el `.wsb`**.
Lo que sí se mantiene verificado: el `.wsb` generado conserva todas las protecciones (clipboard desactivado, sin red, repositorio solo lectura,
una sola carpeta escribible fuera del repositorio, comprobaciones de aislamiento antes de cualquier prueba) — 13 pruebas automáticas lo vigilan.

## 2. Lo ejecutado en esta ronda
| Nivel | Qué | Resultado |
|---|---|---|
| Automático | Suite completa, venv de desarrollo | **1070 pasan** |
| Automático | Suite completa, venv limpio con solo PySide6-Essentials | **1070 pasan** |
| Windows real (host, temporal) | `rc1_lifecycle_check.py` (instalar, arrancar, actualizar con copia, revertir, desinstalar, reinstalar, paquete dañado) × 3 | **26/26 en las 3 ejecuciones (78/78)** |
| Windows real | Tus entradas `HKCU\...\Run` (nombres y valores) antes/después de todo el ciclo y de la suite | Idénticas (4 entradas; no existe `Nexus`) |
| Windows real | Tus procesos Nexus antes/después | Mismos PID (`3276`, `12516`) |
| Sandbox | T01–T12 | **No ejecutado** |

## 3. Inicio con Windows (nueva opción, pedida en esta ronda)
Implementado: Configuración › Inicio; **desactivado por defecto**; solo la elección explícita crea la entrada (`HKCU\...\Run`, por usuario, sin administrador).
* El ajuste antiguo `launch_at_login` **se ignora** a propósito: ningún código lo leía y los `settings.json` existentes lo traen como `true`
  (reutilizarlo habría activado el inicio a quien nunca lo pidió; hay una prueba con ese archivo).
* El arranque solo **mantiene** una entrada ya activada apuntando al intérprete en ejecución; **nunca borra ni crea** cuando la opción está desactivada.
  Quitarla es acto explícito del usuario y solo elimina entradas propias (nunca la de otro programa).
* Actualizar/revertir: si estaba activado, se reapunta a la versión vigente; si estaba desactivado, sigue desactivado; las entradas ajenas no se tocan;
  desinstalar elimina únicamente una entrada que apunte a la carpeta desinstalada.
* Modo sandbox de desarrollo (`NEXUS_SANDBOX=1`): jamás escribe en el registro.
* Probado: 15 pruebas automáticas con registro **simulado** (defaults, archivo antiguo, opt-in/opt-out, fallo del registro, página de Configuración, actualización/reversión/desinstalación).
  **No probado en el registro real**: no se escribió en tu `Run`. Queda como caso T11 del Sandbox.

## 4. Cierre limpio desde la bandeja: intento y bloqueo
Se arrancó una instancia en modo sandbox de desarrollo (carpeta temporal) para accionar su menú de bandeja con UI Automation. El icono
`Nexus (sandbox)` **no apareció** entre los controles de la barra de tareas (probablemente en el menú de iconos ocultos de Windows 11), y
localizarlo exigiría recorrer el árbol de accesibilidad de otras ventanas tuyas. Se **abandonó** ese intento (invasivo y poco fiable),
se cerró solo esa instancia por sus PID y se borró su carpeta temporal. Queda como caso T12 del Sandbox. Lo único probado hoy sobre el cierre
es la prueba automática de arranque/parada (cierre de SQLite y liberación de recursos).

## 5. Fallos reproducibles encontrados
Ninguno nuevo en esta ronda (no se ejecutaron las pruebas que podrían revelarlos). Los hallazgos de rondas anteriores y su corrección están en
`UX_AUDIT_2026-11.md` y en el historial de este archivo (commits `34702fc`, `50da205`).

## 6. Lo que sigue sin demostrarse
* T01–T03 captura de texto/imagen y duplicados · T04–T06 atajos por defecto, búsqueda + pegado en otra app, foco · T07 Ctrl+V del Stack ·
  T08 persistencia tras reinicio con capturas reales · T09 conflicto de atajo · T10 cierre forzado · T11 inicio con Windows en el registro real ·
  T12 cierre desde la bandeja. Los scripts existen (`release/sandbox/guest/functional_tests.py`) pero **jamás se han ejecutado en ninguna máquina**;
  podrían fallar al ejecutarse (temporización de atajos, Tk, runtime de Visual C++ dentro del Sandbox).
* Que Windows Sandbox realmente aísle el portapapeles en este equipo (comprobación manual con el marcador `sentinel.txt` descrita en `README_SANDBOX.md`).

## 7. Cómo completar la validación (acciones tuyas; yo no activo características de Windows ni reinicio)
1. Como administrador: `Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM -All` y **reiniciar**.
2. Comprobar: `Get-Command WindowsSandbox.exe` debe devolver una ruta.
3. Avísame y ejecuto: `rc1_sandbox_prepare.py` (revalida protecciones) → abrir `sandbox-run\nexus-rc1.wsb` → recoger `sandbox-results\<fecha>\`.
   Si falla cualquier comprobación de aislamiento, el sandbox se detiene solo y no se prueba nada con portapapeles.

## 8. Estado exacto
Rama `ux-transformation`, HEAD `719a2f3`; tags `nexus-pre-ux-transformation-2026-11-06`, `nexus-windows-0.2.0rc1`, `nexus-windows-0.2.0rc2`.
Sin push, merge ni publicación; sin cambios en tu instalación, datos, `Run` ni procesos. Sin commitear desde antes: `install.sh`, `uninstall.sh`
(solo cambia el modo; son instaladores de Linux), `work/`, `capturas-ux/`.
