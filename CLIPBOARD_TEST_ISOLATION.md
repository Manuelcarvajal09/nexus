# Pruebas de portapapeles: qué falta y qué se necesita para hacerlas sin riesgo

Estado: **pendientes**. Captura real de copias, pegado en otra aplicación y Ctrl+V del Stack
no se han ejecutado, porque escriben o leen el portapapeles de Windows de tu sesión.

## Qué se comprobó (2026-11-06, solo lectura del sistema)

| Mecanismo | Resultado | ¿Aísla el portapapeles? |
|---|---|---|
| Carpeta temporal / segunda instancia de Nexus (sandbox actual) | Funciona para datos, mutex y atajos | **No**: el portapapeles es del sistema; la instancia aislada lo comparte con tu sesión |
| Escritorio alternativo (`CreateDesktop`) | No probado | **No garantizado**: el portapapeles pertenece a la *window station*, no al escritorio; dos escritorios de la misma window station comparten portapapeles |
| Otro usuario local (`CodexSandboxOffline/Online` existen) | No usado | Solo si tuviera una sesión interactiva propia (otra window station); requiere credenciales y una sesión iniciada. No verificable sin cambiar tu sesión |
| WSL | Instalado | No sirve: ejecuta Linux, no la app de Windows |
| Windows Sandbox / Hyper-V / VirtualBox / VMware | **No instalados**; `HypervisorPresent=False`; la cuenta no es administradora | — |
| Hardware | Virtualización habilitada en firmware y extensiones de monitor de VM: Sí; 15,8 GB RAM; Windows 11 **Pro** | Compatible con Windows Sandbox / Hyper-V |

Conclusión: **no puedo garantizar aislamiento real con lo que hay instalado hoy.**
No he activado ninguna característica del sistema (requiere administrador y reinicio).

## Lo que se necesita (una de las dos opciones; yo recomiendo la A)

**A. Windows Sandbox (la más simple; desechable).**
1. Tú, como administrador: activar la característica opcional *Windows Sandbox*
   (`Disable/Enable-WindowsOptionalFeature -FeatureName Containers-DisposableClientVM`) y reiniciar.
2. Yo preparo un archivo `.wsb` con `<ClipboardRedirection>Disable</ClipboardRedirection>`,
   `<Networking>Disable</Networking>` salvo la instalación inicial, el repositorio en modo
   **solo lectura** (`<ReadOnly>true</ReadOnly>`) y una carpeta de salida aparte.
3. Dentro del sandbox: instalar Python y PySide6 (necesita ruedas descargadas de antemano o red
   temporal), ejecutar `tools/windows_native_e2e.py` ampliado con captura → pegado en la ventana
   de prueba → Ctrl+V del Stack. El sandbox se destruye al cerrarse.
4. Verificación de aislamiento antes de cualquier prueba: copiar un texto centinela distinto en el
   host y en el sandbox y comprobar que **no** se ven entre sí (en ambas direcciones).

**B. Máquina virtual Hyper-V con integración de portapapeles desactivada** (misma idea, más pesada,
persistente). Requiere habilitar Hyper-V (administrador + reinicio) y una imagen de Windows.

## Qué NO es aceptable como sustituto
* Guardar y restaurar el portapapeles: no restaura de forma fiable imágenes, archivos u otros
  formatos privados, y otras aplicaciones (gestores de portapapeles) lo observarían.
* Ejecutar en tu sesión «con cuidado».

## Evidencia de que la suite de pruebas no toca tu portapapeles
`GetClipboardSequenceNumber` (solo lectura) medido con muestreo cada 50 ms:
reposo 60 s → 0 cambios; durante una pasada completa de la suite (51 s) → 0 cambios.
(Las pruebas corren con la plataforma Qt `offscreen`, cuyo portapapeles es interno al proceso.)
Con el arreglo de `tests/conftest.py` además no leen el contador real ni el estado físico del teclado.
