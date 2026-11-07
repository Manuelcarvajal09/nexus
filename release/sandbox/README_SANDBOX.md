# Pruebas aisladas de Nexus RC1 en Windows Sandbox

**Estado: preparado, NO ejecutado.** Windows Sandbox no está activado en este equipo
(`Containers-DisposableClientVM` requiere administrador y reinicio; no se ha tocado).
Hasta que se ejecute, ninguna de las pruebas de portapapeles/pegado/Stack real está aprobada.

## Qué garantiza la configuración (verificado por `tools/rc1_sandbox_prepare.py` y `tests/test_rc1_sandbox.py`)

| Requisito | Cómo |
|---|---|
| Portapapeles no compartido con el host | `<ClipboardRedirection>Disable` (+ `ProtectedClient`) |
| Repositorio solo lectura | `C:\Repo` ← repositorio, `<ReadOnly>true` |
| Datos y configuración temporales | Perfil desechable del sandbox: `%LOCALAPPDATA%\Nexus` nace vacío y desaparece al cerrar |
| Sin red | `<Networking>Disable`; las dependencias salen del `wheelhouse/` empaquetado (offline, `--require-hashes`) |
| Sin tu base personal ni tus procesos | El sandbox no ve tu perfil ni tus procesos; el instalador solo escribe en `C:\Nexus-RC1` (dentro del sandbox) |
| Una sola carpeta escribible | `C:\Results` ← carpeta **vacía y dedicada** (`…\Nexus\sandbox-results`) para logs; es la única excepción a «solo lectura» |
| Python | La carpeta de Python del host (solo lectura) en `C:\HostPython`; no hay instalación ni descarga |

## Requisitos previos (acciones tuyas, con privilegios de administrador)
1. Windows 11 Pro (ya lo es) con virtualización habilitada en firmware (ya lo está).
2. Activar *Windows Sandbox* (PowerShell como administrador) y **reiniciar**:
   `Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM -All`
   (Nada de esto lo hace el proyecto.)

## Preparar (sin privilegios, solo escribe archivos)
```powershell
cd C:\Users\Admin\Projects\Nexus\windows-reference
.\.venv\Scripts\python.exe tools\rc1_build.py --out dist-rc1            # una vez; árbol limpio y commit hecho
.\.venv\Scripts\python.exe tools\rc1_verify.py dist-rc1                 # integridad del paquete
.\.venv\Scripts\python.exe tools\rc1_sandbox_prepare.py                 # genera ..\sandbox-run\nexus-rc1.wsb y valida protecciones
```
El generador **se niega** a escribir el `.wsb` si falta cualquier protección.

## Ejecutar
1. Cierra cualquier otro Windows Sandbox. Doble clic en `C:\Users\Admin\Projects\Nexus\sandbox-run\nexus-rc1.wsb`.
2. El script `guest\bootstrap.ps1` se ejecuta solo, en este orden:
   1. **Comprobaciones de aislamiento** (cuenta WDAGUtilityAccount, sin perfil del host, sin datos previos de Nexus,
      portapapeles inicial vacío, sin red por TCP y DNS, repositorio y Python solo lectura, carpeta de resultados escribible).
      Si falla **cualquiera**, termina: no se copia, pega ni instala nada.
   2. Instalación del paquete (verifica SHA256SUMS, RECORD del wheel y hashes de dependencias antes de crear nada).
   3. `functional_tests.py` (se niega a correr fuera de WDAGUtilityAccount o sin `isolation.json` con `all_ok: true`).
3. Los resultados quedan en `sandbox-results\<fecha>\`: `isolation.json`, `install.log`, `functional_results.json`, `functional.log`.

## Comprobación manual de aislamiento (opcional, recomendada la primera vez)
`bootstrap.ps1` deja un marcador único (`NEXUS-SANDBOX-SENTINEL-…`, también en `sentinel.txt`) en el portapapeles **del sandbox**.
En el host, abre el Bloc de notas y pega: **no debe aparecer** (pega algo que tú mismo hayas copiado, o nada).
Esta prueba solo exige pegar; no escribe nada en tu portapapeles. Si apareciera el marcador, cierra el sandbox y no
continúes: la redirección no está desactivada.

## Qué prueban los diez casos (todos con datos sintéticos `NXFT-…`)
T01 captura de texto · T02 imagen sintética · T03 duplicados · T04 atajo global abre/cierra y devuelve el foco ·
T05 búsqueda en el historial y pegado en otra aplicación (ventana Tk que registra su contenido) · T06 foco tras pegar ·
T07 Ctrl+V del Stack con tres elementos en orden FIFO · T08 persistencia tras reiniciar · T09 conflicto de atajo global
al arrancar · T10 cierre forzado y reinicio (integridad de SQLite).
Un caso que no puede ejecutarse queda `BLOCKED`, nunca `PASS`.

## Posibles bloqueos (documentados, sin rebajar protecciones)
* **Sin Windows Sandbox**: no hay forma de ejecutar esto con aislamiento real. No se debe sustituir por pruebas en la sesión personal.
* **Runtime de Visual C++ / Tcl-Tk**: el sandbox se parte de una imagen limpia; la carpeta de Python del host aporta `vcruntime140`
  y Tcl/Tk. Si PySide6 o Tk no cargan, el fallo aparecerá en `install.log` / `functional.log` (casos `BLOCKED`).
* **vGPU / Acrylic**: el material translúcido puede no renderizar igual en el sandbox; las pruebas son funcionales, no visuales.
* **Atajos globales y hook de teclado dentro del sandbox**: dependen de la sesión de escritorio del sandbox; si no se registran,
  T04/T07/T09 fallarán con evidencia (no se marcan como aprobadas).
