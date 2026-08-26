# Nexus 2.0 — Design System

**Fecha:** 2026-08-26. Este documento fija los tokens que ya existían (los cuatro temas reales en `styles.py`, sin inventar valores nuevos donde ya hay uno) y añade los que faltan para la Timeline/Stack 2.0. No se toca el sistema de temas existente — se extiende.

## Por qué no se rediseña la paleta desde cero

Nexus ya tiene 4 temas completos y coherentes (`midnight_teal`, `minimalist`, `storem`, `tech_neon`), cada uno con su propia identidad tipográfica y de color, migrados y ajustados en la sesión de migración Mac→Linux. Rehacerlos sería descartar trabajo ya validado por Manuel en uso diario, y contradice el principio de "identidad propia, no clon" — Nexus 2.0 usa esos mismos temas, extendidos con los tokens nuevos que la Timeline necesita.

## Typography

Definida por tema (`Theme.font_family`), no global — cada tema ya tiene su propia familia:

| Tema | Familia |
|---|---|
| midnight_teal | Inter / Inter Display / Cantarell |
| minimalist | Inter / Cantarell |
| storem | Noto Serif / Georgia |
| tech_neon | Noto Sans Mono / JetBrains Mono |

Escala tipográfica (nueva, antes solo existían tamaños sueltos en QSS — se documentan aquí como referencia única):

| Rol | Tamaño | Peso | Uso |
|---|---|---|---|
| `title` | 19px | 600 | Encabezado de ventana |
| `cardTitle` | 13px | 500 | Título de una card |
| `cardMeta` | 10.5px | 400 | Metadata secundaria (app, tiempo, tipo) |
| `subtitle` | 11.5px | 400 | Subtítulos, contadores |
| `badge` | 10.5px | 600 | Chips numéricos/contadores |
| `mono` (código) | 12px | 400 | Preview de snippets — siempre monoespaciado sin importar el tema activo |

## Spacing Scale

Nuevo, extraído de los valores ya usados de forma consistente en el QSS existente (no inventado): **4 / 8 / 12 / 16 / 24px**. La Timeline nueva usa esta escala explícitamente en vez de números sueltos por widget.

## Corner Radii

Ya existían por tema (`radius_panel`, `radius_card`, `radius_chip`) — se añade uno nuevo para las cards horizontales de la Timeline:

| Token | midnight_teal | minimalist | storem | tech_neon |
|---|---|---|---|---|
| `radius_panel` | 18 | 10 | 12 | 6 |
| `radius_card` | 12 | 8 | 10 | 6 |
| `radius_chip` | 9 | 6 | 8 | 6 |
| `radius_timeline_card` (nuevo) | = `radius_card` | = `radius_card` | = `radius_card` | = `radius_card` |

No se introduce un radio distinto para la Timeline: usa el mismo `radius_card` que ya tienen las filas del historial vertical, por consistencia entre las dos vistas (Timeline y Library).

## Surfaces / Elevation

Ya existían dos mecanismos, ambos se mantienen tal cual:
- `apply_glow()` — halo de acento (usado en temas con `glow=True`).
- `apply_card_shadow()` — sombra de elevación tipo macOS.

**Regla nueva, explícita** (causa raíz del hallazgo de performance de Fase 1): ninguno de los dos se aplica de forma eager a más widgets de los que el usuario puede ver a la vez. En la Timeline virtualizada, el delegate pinta la sombra/glow directamente con `QPainter` dentro de `paint()` (no crea un `QGraphicsDropShadowEffect` por card) — el costo de un `QGraphicsEffect` por widget fue exactamente lo que causó los ~800ms de latencia medidos en `PERFORMANCE_BASELINE.md`, y una lista virtualizada por definición no tiene "un widget por card" al que adjuntarle un efecto.

## Borders

`card_border` / `card_border_hover` por tema, ya existentes — sin cambios.

## Transparency

Sin blur real disponible en Qt Widgets sin pelear con el compositor por ventana (ya documentado en `styles.py`). Los cuatro temas usan `rgba()` con alpha para simular profundidad en bordes/dim states — se mantiene ese enfoque, no se introduce blur nuevo.

## Accent / Focus / Selection / Hover

Ya existentes, reutilizados sin cambios:
- **Focus** (teclado): `card_border` → `accent`, ancho 1px, mismo patrón que `QLineEdit:focus`.
- **Selection**: `accent_dim` de fondo + borde `accent` — ya usado en `clipRow[selected="true"]` y `sidebarList::item:selected`.
- **Hover**: `card_bg_hover` + `card_border_hover`.

**Nuevo, para la Timeline (navegación horizontal)**: el item con foco de teclado (no necesariamente "seleccionado" en el sentido de click) se marca con el mismo tratamiento de `selected` — en una lista horizontal navegada por flechas no existe una diferencia útil entre "seleccionado" y "con foco", a diferencia de una lista con selección múltiple.

## Animation Durations

Ya fijado por el pedido original y ya usado en `row_widget.py` (sombra de hover, 140ms `OutCubic`): **100–200ms** para toda microinteracción. Se documenta como regla dura para todo lo nuevo en Fase 2:

| Interacción | Duración | Easing |
|---|---|---|
| Hover de card (elevación) | 140ms | OutCubic |
| Selección al navegar con flechas | 120ms | OutCubic |
| Consumo de un item del Stack (NEXT → siguiente) | 160ms | OutCubic |
| Apertura/cierre de ventana | 0ms (instantáneo) | — |

**Por qué la apertura/cierre sigue sin animación**: ya documentado en `MIGRATION_LINUX.md` — tanto `QWidget.windowOpacity()` como `QGraphicsOpacityEffect` sobre una ventana de nivel superior fallan en este Qt/Wayland (el segundo renderiza negro sólido). Cambiar esto requeriría resolver ese bug de Qt/Wayland primero, no es una decisión de diseño — se mantiene `fade_hide()`/`show()` instantáneos, tal como están nombrados para permitir cambiarlo en un solo lugar si algún día se resuelve.

## Card Types (nuevo — Timeline 2.0)

Cada tipo detectado (ver `type_detection.py`) tiene su propio layout dentro del mismo tamaño de card base, no un tamaño de card por tipo (mantiene la grilla de la Timeline visualmente regular):

| Tipo | Elemento dominante | Elementos secundarios |
|---|---|---|
| `text` | Extracto del contenido (2-3 líneas) | app origen + tiempo |
| `url` | Dominio + preview (favicon cuando exista) | tiempo |
| `youtube` | Miniatura (cuando exista cache) | "YouTube" + tiempo |
| `image` | Miniatura grande (ocupa la card) | tiempo |
| `color` | Swatch de color a todo ancho | valor HEX/RGB |
| `code` | Preview monoespaciado, 3-4 líneas | lenguaje detectado (si hay) |
| `json` | Preview estructurado compacto | tamaño/cantidad de claves |
| `file` | Icono de tipo de archivo + nombre | tamaño/extensión |

## Icon Size / Thumbnails

Ya existente: `BADGE_SIZE = 34` (chip circular de tipo). La Timeline usa un tamaño mayor para el elemento dominante de cards visuales (imagen/color/YouTube): **96×96 lógicos**, tope compartido con el cache de miniaturas ya construido en Fase 1 (`_cached_circular_thumbnail`), extendido para aceptar tamaños no solo de 34px.

## Empty States

Ya existía un patrón (`emptyState` QSS class, usado en `MainWindow`/`StackWindow`). La Timeline reutiliza el mismo patrón visual, con copy propio por contexto (historial vacío / sin resultados de búsqueda / pinboard vacío) — ya cubierto conceptualmente en `main_window.py`, replicado sin reinventar.
