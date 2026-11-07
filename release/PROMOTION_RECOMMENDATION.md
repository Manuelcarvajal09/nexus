# Recomendación: promoción de 0.2.0rc2 a versión estable

**Recomendación actual: NO promover todavía.** La promoción no se ha realizado ni se ha preparado ningún artefacto «estable».

## Por qué
Las funciones críticas de un gestor de portapapeles —**capturar lo copiado, pegarlo en otra aplicación y el Ctrl+V del Stack**— no se han
demostrado en un entorno real: solo con dobles de prueba. Windows Sandbox, el único entorno que garantizaría no tocar el portapapeles
personal, no está disponible en este equipo (ver `RC1_TEST_REPORT.md`, sección 1).

## Criterios de promoción (todos deben cumplirse)
| # | Criterio | Estado hoy |
|---|---|---|
| 1 | T01–T12 ejecutados en Windows Sandbox con aislamiento verificado (`isolation.json: all_ok`) y **todas en PASS** (ninguna `BLOCKED`) | **No cumplido** (no ejecutadas) |
| 2 | Comprobación manual del marcador `sentinel.txt` (el portapapeles del sandbox no aparece en el host) | **No cumplido** |
| 3 | Cualquier fallo reproducible de T01–T12 investigado, corregido y repetido (suite + pruebas afectadas) | No aplicable aún |
| 4 | Suite automática completa en venv de desarrollo y en venv limpio con solo PySide6-Essentials | **Cumplido** (1070/1070 en ambos) |
| 5 | Build reproducible y paquete verificado (SHA256SUMS, RECORD, wheelhouse fijado por hash) | **Cumplido** (hash `98fddf63…`) |
| 6 | Ciclo de vida en Windows real: instalar, actualizar con copia verificada, revertir, desinstalar, reinstalar, paquete dañado | **Cumplido** (26/26 × 3) |
| 7 | Inicio con Windows: desactivado por defecto, respetado al actualizar | Implementado y probado con registro **simulado**; T11 pendiente |
| 8 | Sin modificaciones de la instalación, datos, `Run` ni procesos personales durante las pruebas | **Cumplido** |
| 9 | Decisión de producto sobre distribución (firma de código, instalador gráfico, actualización automática) | **Pendiente de decisión tuya**; hoy no existen |
| 10 | Inventario de limitaciones aceptado por el responsable del producto | Pendiente (ver `README_RC1.md`, «Limitaciones») |

## Riesgos si se promoviera ahora
* Un fallo en captura/pegado/Stack real (los flujos de uso diario) no se habría visto antes de usarlo con datos reales.
* El inicio con Windows no se ha escrito jamás en un registro real.
* Sin firma ni instalador gráfico: Windows SmartScreen/antivirus pueden advertir si se distribuye fuera de este equipo.

## Qué haría falta para recomendar la promoción
1. Activar Windows Sandbox (administrador + reinicio) y ejecutar la validación (instrucciones en `RC1_TEST_REPORT.md`, sección 7).
2. Si T01–T12 pasan: marcar el candidato como «listo para estable» (nuevo informe con las evidencias) y decidir el punto 9.
3. Solo entonces: renombrar la versión (`0.2.0`), repetir build y verificación, crear el tag definitivo — **acción que requiere tu autorización explícita**.
