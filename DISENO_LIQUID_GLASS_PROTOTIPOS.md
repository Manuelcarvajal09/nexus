# Liquid Glass Ultra Premium — investigación, diagnóstico y tres prototipos

Estado: **elegido y aprobado visualmente: C (Hybrid), integrado en el código** (`src/nexus_linux/windows_optical.py`; interruptor en Configuración → Apariencia, `NEXUS_GLASS_OPTICAL=0` lo apaga). La actualización de la instancia habitual sigue pendiente de autorización. El resto de este documento es el estudio previo a la elección. Todo vive en `tools/windows_glass_lab.py` (herramienta aislada, sin base de datos, portapapeles, atajos ni instancia única) y en `work/glass-lab/` (capturas e informes). Tu Nexus habitual no se tocó.

> **Falta material tuyo:** mencionabas «las imágenes que te proporcionaré», pero no llegó ninguna. El análisis visual se apoya en la documentación de Apple y en mi conocimiento del material; cuando las envíes, comparo los prototipos contra ellas antes de que elijas.

## 1. Investigación

**Documentado por Apple** (sesiones WWDC25 [Meet Liquid Glass](https://developer.apple.com/videos/play/wwdc2025/219/) y [Get to know the new design system](https://developer.apple.com/videos/play/wwdc2025/356/)):
- *Lensing*: el material **curva y concentra la luz** (no solo la desenfoca); cuanto mayor la superficie, más marcados refracción, sombra y dispersión.
- *Reflejos especulares* que responden a la geometría y al movimiento; el material **adapta tinte, sombra y rango dinámico** a lo que hay detrás.
- Dos variantes: **Regular** (adaptativa, para casi todo) y **Clear** (siempre transparente, necesita capa de atenuación). No mezclarlas. No poner cristal sobre cristal ni en la capa de contenido.
- *Reducir transparencia*, *aumentar contraste* y *reducir movimiento* modifican el material automáticamente.

**Documentado por Microsoft** ([DWMWA_SYSTEMBACKDROP_TYPE](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwm_systembackdrop_type), [System backdrops](https://learn.microsoft.com/en-us/windows/apps/develop/ui/system-backdrops)):
- DWM expone solo el **tipo** de material (Mica, Acrylic, Mica Alt). **No hay radio de desenfoque, ni intensidad de tinte, ni refracción** en la API Win32. Puede degradarse a color sólido (sin transparencia, ahorro de batería, sesión remota, GPU débil).
- Los pinceles de *Composition* (`CompositionBackdropBrush`) y los controles de Windows App SDK permiten materiales personalizados, pero **dentro de WinUI/Composition**; no encajan con una ventana Qt Widgets sin cambiar la arquitectura.

**Aproximación propia (no documentada, mi inferencia):** Windows no da acceso a los píxeles del compositor desde una app. La refracción solo se puede **simular** si la propia app conoce los píxeles de detrás, p. ej. capturándolos justo antes de mostrar el panel. QML/ShaderEffect, Direct2D y DirectComposition no aportan una ventaja comprobable aquí: exigen otro host gráfico y no resuelven el acceso a los píxeles. **No se usan.**

## 2. Diagnóstico: por qué el Nexus actual no parece Liquid Glass

Capturas de referencia: `work/glass-lab/baseline-light|dark/` (diseño actual sobre el mismo tipo de fondo sintético).

| Causa | Evidencia en la captura | Origen técnico |
|---|---|---|
| **Desenfoque ciego y excesivo** | El Acrylic convierte todo en manchas amarillas/rosas; se pierde la estructura del fondo | DWM Acrylic no se regula; no hay forma de bajarlo |
| **Una sola capa de material** | Panel, barra y controles tienen el mismo grado de transparencia | Los controles son rectángulos planos (`control` α bajo) sin geometría propia |
| **Bordes planos** | Contorno uniforme de 1 px | Sin gradiente de luz ni línea interior; sin grosor |
| **Sin profundidad** | Controles «pegados» al panel | Sin sombra de elevación ni luz interior; solo la línea superior |
| **Sin reflejo especular** | Superficies mates | Solo una franja superior de 72 px |
| **Sin lensing** | El borde de un control muestra lo mismo que su interior | Imposible con Acrylic: Qt no ve los píxeles que DWM mezcla |
| **Contraste estático** | Un único tinte para cualquier fondo | Sin conocimiento del fondo, el tinte debe cubrir el peor caso |
| **Cristal grisáceo sobre blanco (Dark)** | Panel oscuro sobre fondo blanco se ve gris opaco | Tinte propio de DWM; el modo Light existe por esto |

## 3. Los tres prototipos

Misma composición en los tres: cabecera, contador/Estilo/cerrar, pestañas, búsqueda y tres tarjetas (texto con título, enlace, imagen). Capturas **reales de pantalla de Windows** (DWM activo; no son renders `offscreen`) sobre un fondo propio y sintético en cuatro variantes: blanco, negro, fotografía y aplicación colorida. Limitación honesta: el fondo es **mío**, no tu escritorio; así controlo qué se captura y puedo medir contraste.

- **A · Native Glass** — Acrylic vivo + tinte ligero, reflejo superior, bordes con luz direccional, sombras de elevación y tarjetas con capa de contraste fija.
- **B · Optical Glass** — Sin Acrylic. Se **captura el fondo antes de mostrar el panel**, se desenfoca (más vivo), y cada superficie lo repinta con un **lente de borde** (el contenido de alrededor se comprime en el rim), luz especular, grosor interior y fleco cromático suave. La capa de contraste **se adapta al fondo real detrás de cada tarjeta**.
- **C · Hybrid** — Panel con Acrylic vivo (como A); controles (cabecera, pestañas, búsqueda) con el vidrio óptico de B; tarjetas con contraste adaptativo.

Comparaciones lado a lado (A | B | C): `work/glass-lab/comparison/compare-{light,dark}-{white,black,photo,app}.png`. Capturas individuales: `work/glass-lab/matrix/`.

## 4. Medición (misma máquina, un solo pase; DPR 1,0 en esta pasada)

| | A Native | B Optical | C Hybrid |
|---|---|---|---|
| Contraste mínimo del texto (8 fondos×variante, zonas de tarjeta) | Light 5,15 · Dark 4,83 | Light 5,56 · Dark 4,74 | Light 6,17 · Dark 6,49 |
| Repintado del panel (mediana; solo en interacción, no en reposo) | 12,9 ms | 26,1 ms | 20,1 ms |
| Apertura extra (captura + desenfoque) | 0 | ≈ 25 + 68 ms | ≈ 38 + 69 ms |
| Memoria del proceso del laboratorio | 97 MB (los tres juntos) | | |
| Fondo detrás | **Vivo** (Acrylic) | **Congelado** al abrir | Panel vivo, controles congelados |
| Fidelidad óptica (lensing, grosor, dispersión) | Baja: solo luz y reflejo | **Alta** en panel y controles | Media-alta: en controles |
| Estabilidad DWM | Ya probada en Nexus | Sin Acrylic: menos riesgo DWM, más pintura propia | Misma que A + pintura propia |
| Mantenimiento | Bajo | Alto (captura, desenfoque, lente) | Medio-alto |
| Privacidad | Sin capturas | **Captura píxeles de pantalla** (solo en memoria, nunca guardados/registrados) | Igual que B |

Notas de medición: contraste calculado sobre las capturas reales (mediana del color real detrás del texto frente a los tokens de texto); **no** he medido CPU en reposo ni batería, y **la comprobación DPI se hizo en 1,25 con una versión anterior del prototipo** (mi pantalla pasó de 125 % a 100 % durante la sesión; en 125 y 150 % las capturas de B/C quedaron bloqueadas por ventanas ajenas). Pendiente repetir DPI y movimiento reducido (el seguimiento de la luz con el ratón se desactiva con `NEXUS_REDUCED_MOTION`) antes de integrar.

## 5. Límites que no se pueden «arreglar»

1. **Sin refracción real.** Windows no entrega los píxeles del compositor; B y C **simulan** el lente comprimiendo el contenido capturado. Es una aproximación convincente en bordes, no óptica física.
2. **Fondo congelado en B/C.** Si debajo hay un vídeo o algo cambia mientras el panel está abierto, el cristal no se actualiza. Habría que recapturar (coste) o aceptarlo para un panel transitorio.
3. **El Acrylic no se regula ni se mantiene inactivo** (A y C): se aplana si la ventana pierde el foco, y sobre blanco el tinte oscuro se ve gris.
4. **Capturar la pantalla** es una capacidad sensible. Para integrarlo habría que acotarlo (solo región del panel, solo en memoria, sin persistir, y desactivable en Configuración).

## 6. Mi lectura (tuya la decisión)

No elijo ganadora por pasar pruebas. Mi inclinación, con las cautelas anteriores, es **C**: mantiene el fondo vivo del panel y la estabilidad ya probada, añade el vidrio óptico donde más se nota (controles) y obtuvo el mejor contraste. **B** es el más cercano a las referencias de Apple y el más «cristal», pero a cambio de fondo congelado, el doble de coste de pintura, captura de pantalla y más mantenimiento. **A** es el más barato y seguro, pero **no** consigue lensing: sería «mejor Acrylic», no Liquid Glass.

Para decidir con ojo: abre primero `compare-light-photo.png` y `compare-dark-photo.png`, y luego `compare-light-app.png` (la prueba más dura del lente).

## 7. Cómo repetirlo

```powershell
.\.venv\Scripts\python.exe tools\windows_glass_lab.py work\glass-lab\matrix --bench     # A,B,C × light,dark × 4 fondos
.\.venv\Scripts\python.exe tools\windows_glass_lab.py OUT --modes C --variants dark --phases photo --scale 1.25
```
