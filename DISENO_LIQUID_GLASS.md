# Liquid Glass en Nexus Windows — diseño, límites y verificación

## 1. Qué es posible en Windows (y qué no)

| Capa | Quién la hace | Se puede controlar |
|---|---|---|
| **Desenfoque del fondo real** | **DWM Desktop Acrylic** (`DWMWA_SYSTEMBACKDROP_TYPE = 3`) sobre una ventana **no estratificada** | Solo claro/oscuro (`DWMWA_USE_IMMERSIVE_DARK_MODE`) y el color del borde. **No** el radio de desenfoque ni la intensidad del tinte |
| Tinte, reflejo superior y línea de luz | Qt, pintado sobre el Acrylic | Totalmente |
| Tarjetas, búsqueda, chips, controles | Qt, rellenos translúcidos (`#AARRGGBB` → `rgba()` en QSS) | Totalmente |
| Sombra y contorno único | DWM (borde nativo + esquinas redondeadas) | Color del borde |

Límites **conocidos y documentados** (no se simulan):
- **Sin refracción tipo lente** ni «líquido» de Apple: Windows no ofrece un material con distorsión. Se aproxima con desenfoque real + translucidez + reflejo.
- **Acrylic se aplana cuando la ventana pierde el foco** (comportamiento de DWM). El panel se activa al mostrarse.
- **El fondo oscuro del Acrylic sobre un fondo muy claro se ve grisáceo**: es el tinte del propio DWM; el modo Light existe para esos escritorios.
- Rechazados por pruebas previas (ver `WINDOWS_ARCHITECTURE.md`): ventanas estratificadas / opacidad de ventana, `SetWindowRgn`, dos HWND, Windows App SDK. **No se reactivan.**

## 2. Dark y Light automáticos (solo Liquid Glass)

- **Fuente:** `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize\AppsUseLightTheme` (1 = claro). Solo se **lee**.
- **Aviso en vivo:** `WM_SETTINGCHANGE` con `"ImmersiveColorSet"` (filtro nativo de la aplicación) y `QStyleHints.colorSchemeChanged` como segundo disparador. Se agrupan en una sola relectura (150 ms) y solo se emite si el valor cambió.
- **Qué cambia:** paleta y QSS, atributo `dark` del Acrylic y color del borde **en el mismo HWND** (sin recrearlo), íconos y tarjetas. El contenido entra con un fundido de 140 ms; la ventana nunca se desvanece.
- Los otros cuatro temas **ignoran** la preferencia de Windows.
- Rendimiento: el valor se cachea (0,14 µs por lectura frente a 36 µs al registro) porque `palette()` se llama en cada pintado.
- **Para probar sin tocar Windows:** `tools/windows_acceptance.py theme dark|light|real` (solo dentro de la prueba aislada).

## 3. Tokens (todos en `windows_design.py`)

| | Dark | Light |
|---|---|---|
| Tinte del panel | `#260B1019` | `#47FFFFFF` |
| Reflejo superior | blanco 8 % → 0 en 72 px | blanco 35 % → 0 |
| Línea de luz (borde superior) | blanco 32 % | blanco 90 % |
| Tarjeta / hover | `#8A0A0F18` / `#9E111826` (oscuro translúcido) | `#99FFFFFF` / `#B8FFFFFF` (blanco translúcido) |
| Texto / secundario / terciario | `#F6F8FC` / `#C9D3E1` / `#B7C2D3` | `#111827` / `#344054` / `#475467` |
| Acento | `#B3CFF6` | `#2563C9` |
| Menús (ventana aparte, casi opacos) | alfa 242 | alfa 245 |

**Contraste:** `tests/test_liquid_glass_adaptive.py` modela el Acrylic con valores medidos en capturas reales (conserva ~35 % del fondo desenfocado) y exige texto ≥ 7:1 y secundarios ≥ 4,5:1 sobre las tarjetas —también seleccionadas— frente a 9 fondos (blanco, grises, negro, rojo, azul, amarillo y oscuro), en ambas variantes.

## 4. Iconografía

Todos los íconos se dibujan con Qt en una cuadrícula de 20 u, trazo 1,5, extremos y uniones redondeados, y se generan a **1×, 2× y 3×** (nítidos de 100 % a 300 %). El «⋯» del menú era el carácter Unicode más la flecha nativa de `QToolButton`; ahora son tres puntos dibujados y la flecha nativa está desactivada por QSS (`::menu-indicator { image: none }`) en botones y menús. «Estilo» usa un chevron dibujado a la derecha. Un test impide que vuelva a haber glifos Unicode como íconos en los botones.

## 5. Movimiento

Curvas **OutQuint** (arranque inmediato, asentamiento largo), nunca ease-in; ≤ 150 ms; solo opacidad y color; interrumpible; sin animar el HWND; inactivo con movimiento reducido. Acciones de teclado frecuentes (abrir con el atajo) solo suavizan el contenido, empezando al 55 % de opacidad.

## 6. Cómo se verificó (y cómo repetirlo)

`tools/windows_glass_probe.py` abre las **clases reales de Nexus** sobre una ventana de fondo **sintética propia** (rayas, bloques y texto nítidos) en una esquina de la pantalla y captura **solo esa región** de la pantalla real —con el Acrylic de DWM—, y solo si ninguna ventana ajena la tapa. Usa una base y clips sintéticos; no toca portapapeles, atajos ni tu instancia.

```powershell
.\.venv\Scripts\python.exe tools\windows_glass_probe.py SALIDA --system light            # o dark
.\.venv\Scripts\python.exe tools\windows_glass_probe.py SALIDA --flip "dark>light"       # cambio en vivo, mismas ventanas
.\.venv\Scripts\python.exe tools\windows_glass_probe.py SALIDA --scale 1.5                # otro escalado
```
