# Nexus Windows 0.2.0rc1 — paquete candidato (local, sin distribuir)

Este paquete **no sustituye ni modifica tu instalación actual**. Instalarlo es una acción tuya, explícita,
con `--root` obligatorio; por defecto solo se instala en carpetas temporales.

## Contenido
| Archivo | Para qué |
|---|---|
| `nexus_windows-0.2.0rc1-py3-none-any.whl` | La aplicación (compilada desde el commit indicado en `manifest.json`) |
| `wheelhouse/` + `requirements-rc1.lock` | Dependencias de ejecución (PySide6-Essentials, shiboken6) fijadas por hash; instalación **sin red** |
| `tools/rc1_manage.py` | Instalar / actualizar / revertir / desinstalar |
| `tools/rc1_verify.py` | Verificar la integridad del paquete |
| `tools/windows_db_tools.py` | Copia de seguridad verificada de la SQLite (API de backup en línea) |
| `manifest.json`, `SHA256SUMS` | Versión, commit, hashes |
| `CHANGELOG_RC1.md` | Cambios |

## 1. Verificar antes de instalar
```powershell
python tools\rc1_verify.py .          # debe terminar en: PACKAGE VERIFIED
```
Comprueba SHA256SUMS, que no haya archivos de más, los hashes del `RECORD` del wheel, que la única dependencia
de ejecución sea PySide6-Essentials, que no se envíen datos (`.sqlite`, `settings.json`, `.lnk`, `.log`, `__pycache__`) y que
cada wheel de `wheelhouse/` coincida con el hash fijado en `requirements-rc1.lock`.

## 2. Instalar (no toca tus datos ni tus accesos directos)
```powershell
python tools\rc1_manage.py install --package . --root C:\Ruta\Nexus-RC
```
* Crea `versions\0.2.0rc1\venv`, instala **sin red** y con `--require-hashes`, y prueba un `import` real antes de darla por buena.
* Escribe `Nexus.cmd` (lanzador sin ventana de consola). **No** arranca la app, **no** crea inicio automático ni accesos directos.
* Fuera de la carpeta temporal del sistema exige `--allow-real-install`.
* Tus datos siguen en `%LOCALAPPDATA%\Nexus` (nunca dentro de la carpeta de instalación).

## 3. Actualizar conservando datos
1. Cierra Nexus desde el menú de la bandeja («Salir de Nexus»). El gestor se niega si detecta que sigue abierto.
2. ```powershell
   python tools\rc1_manage.py install --package <nuevo-paquete> --root C:\Ruta\Nexus-RC --data-dir "$env:LOCALAPPDATA\Nexus"
   ```
   Antes de instalar toma una **copia de seguridad verificada** de `nexus.sqlite` (en `<root>\backups\`) y aborta si no verifica.
   La versión anterior queda intacta en `versions\`.
3. Abre `Nexus.cmd`. El esquema solo tiene cambios **aditivos** (`title`, `title_search`).

## 4. Revertir
```powershell
python tools\rc1_manage.py rollback --root C:\Ruta\Nexus-RC                # a la versión anterior
python tools\rc1_manage.py rollback --root C:\Ruta\Nexus-RC --to 0.2.0rc2  # o a cualquiera instalada (también «hacia delante»)
```
Los datos no se tocan. Si tu base ya tiene títulos y vuelves a una versión **anterior a la entrega de títulos**, esa versión purga por
antigüedad los clips con título: usa antes `windows_db_tools.py protect-titled` o restaura la copia (ver `ACEPTACION_Y_ACTUALIZACION.md`, R-A/R-B).
Para restaurar datos: `windows_db_tools.py verify <copia>` y `merge-newer` (documentado en ese mismo archivo).

## 5. Desinstalar
```powershell
python tools\rc1_manage.py uninstall --root C:\Ruta\Nexus-RC
```
Borra solo la carpeta de instalación (y solo si contiene su marcador `.nexus-rc-install`). **Conserva tus datos.**
Borrarlos exige además `--remove-data <carpeta> --yes`.

## 6. Reproducir la compilación
`python tools\rc1_build.py --out dist-rc1` (desde el repositorio, con el árbol limpio): exporta el commit con `git archive`, usa
`setuptools==68.2.2` y `wheel==0.43.0` fijados por hash, construye el wheel **dos veces** con `SOURCE_DATE_EPOCH` = fecha del commit y
falla si no son idénticos byte a byte; descarga las dependencias con `--require-hashes` y verifica el resultado.

## Estado de validación (qué significa cada nivel)
* **Implementado**: existe en el código/paquete.
* **Automático**: pruebas pytest (offscreen, sin tocar portapapeles ni teclado reales).
* **Windows real (host, aislado)**: ejecutado en este PC, en carpetas temporales, con instancia en modo sandbox de desarrollo
  (mutex y atajos propios `Ctrl+Shift+9/7`), datos sintéticos y **sin usar el portapapeles**.
* **Sandbox**: ejecutado en Windows Sandbox (portapapeles no compartido).
* **Pendiente**: no ejecutado.

| Capacidad | Implementado | Automático | Windows real (host) | Sandbox |
|---|---|---|---|---|
| Instalación limpia sin red, hashes fijados | Sí | Sí | **Sí** | Pendiente |
| Rechazo de paquete manipulado / raíz fuera de temp | Sí | Sí | **Sí** | — |
| Inicio de la app instalada | Sí | Parcial (smoke) | **Sí** (arranca con el código instalado) | Pendiente |
| Atajos globales de prueba, apertura/cierre, foco, menú contextual | Sí | Parcial | **Sí** (repetido; ver informe) | Pendiente |
| Atajos globales **por defecto** (`Ctrl+Shift+V/C`) | Sí | Sí (lógica) | No (chocarían con tu instancia) | Pendiente |
| Persistencia tras reiniciar | Sí | Sí | **Sí** (datos y atajos tras reinicio del proceso) | Pendiente |
| Actualización conservando datos + copia verificada | Sí | Sí | **Sí** | Pendiente |
| Reversión y avance entre versiones | Sí | Sí | **Sí** | — |
| Desinstalación conservando datos | Sí | Sí | **Sí** | — |
| Recuperación de instalación interrumpida/dañada | Sí | Sí | **Sí** (paquete dañado) | — |
| Captura de texto / imagen del portapapeles | Sí | Sí (con dobles) | **No** (usa tu portapapeles) | **Pendiente** (T01–T03) |
| Pegado en otra aplicación | Sí | Sí (con dobles) | **No** | **Pendiente** (T05–T06) |
| Ctrl+V del Stack | Sí | Sí (con dobles) | **No** | **Pendiente** (T07) |
| Manejo de duplicados | Sí | Sí | No | **Pendiente** (T03) |
| Recuperación: conflicto de atajo, cierre forzado | Sí | Sí | Parcial | **Pendiente** (T09–T10) |
| Cierre limpio desde la bandeja | Sí | Parcial (cierre de SQLite en smoke) | **No** (la prueba cierra el proceso a la fuerza) | Pendiente |
| Inicio automático | **No** | — | — | — |

## Limitaciones conocidas
1. **Sin validación de portapapeles real**: captura, pegado entre aplicaciones y Ctrl+V del Stack solo están probados con dobles. Requieren Windows Sandbox
   (ver `release/sandbox/README_SANDBOX.md`), no activado en este equipo (administrador + reinicio).
2. **Inicio automático no implementado.** El ajuste `launch_at_login` existe en `settings.json` (valor por defecto `true`) pero ningún código
   lo lee; el instalador no crea acceso directo en Inicio. Decisión de producto pendiente.
3. **No hay instalador gráfico, ni firma de código, ni actualización automática.** Es un paquete de línea de comandos para evaluación.
4. La dependencia de ejecución cambió a `PySide6-Essentials` (antes el metapaquete `PySide6`). Validado con toda la suite en un venv limpio; WebEngine/Addons/Pdf ya no se instalan.
5. Solo Windows x64 con CPython 3.11 (el wheelhouse es `win_amd64`/`abi3`). Linux y macOS no se han validado en esta rama.
6. Los atajos globales de la prueba nativa (`Ctrl+Shift+9/7`) no son los de producción.
7. Fallos intermitentes de la automatización nativa por robo de foco de otras aplicaciones: clasificados como «inconclusos», no como fallos del producto (ver informe).
8. `install.sh` / `uninstall.sh` son instaladores **de Linux** (bash, `~/.local/share`, autostart XDG); no se usan en Windows y no se han modificado.
