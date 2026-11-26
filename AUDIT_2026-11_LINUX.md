# Auditoría 2026-11 — port a Linux

Rama `linux/auditoria-2026-11`, creada desde `respaldo/wip-20261118` (el WIP sin commitear de `linux/`, marcado «No publicar sin revisar»). Origen de los arreglos: auditoría de Windows (`ux-transformation`, commits `3057c5d` y `f570095`, ver `UX_AUDIT_2026-11.md` en esa rama).

## Portado
| Área | Cambio |
|---|---|
| Vista previa de enlaces (`web_preview.py`) | Solo IP globales (CGNAT/Tailscale e IPv4 mapeadas rechazadas); errores de red → `PreviewError` (sin reintentos en cada refresh); tope total de 6 s; redirect https→http rechazado; solo png/jpeg/gif/webp/ico; enlaces con token/código/firma/credenciales o rutas de login/reset no se visitan; URL de oEmbed escapada |
| `preview_service.py` | `network_enabled` además del `allow_network` existente (perfil `network_previews`) |
| `storage.py` | `secure_delete=ON`; `VACUUM` automático tras la purga si lo libre supera 32 MB y 40 %; índice FTS5 trigram sobre `search_text` con triggers (búsqueda literal: `_`/`%` ya no son comodines; LIKE para <3 letras o consultas que FTS no entiende); listado sin blobs de imagen (`_LazyImageClipItem`); orden determinista ante empates (`id DESC`) |
| Miniaturas | `thumbnails.rounded_rect_for` / `circular_for`: si la miniatura está en caché no se lee la imagen; usado en `timeline_delegate`, `history_delegate`, `shelf_delegate` y `row_widget` |
| Pintado | `type_detection.detect` en caché en `timeline_delegate` |
| Stack | «FIFO/LIFO» → «En orden» / «Inverso» (botón, tooltip, contador) |
| Contraste | `text_tertiary` (hora y app de cada tarjeta) subido a ≥4,5:1 en midnight_teal, minimalist/dark, storem y liquid_glass (era 2,5–3,7:1). `danger` ya cumplía en todos |
| Avisos de pegado | Mensaje de bandeja según la causa (falta ydotool, ventana destino cerrada, Hyprland) sin texto técnico; máx. 1 cada 8 s |
| Log | Rotación (1 MB + 1 copia); ya tenía hora |

## No aplica en Linux
- Exclusión de contraseñas: ya existía (`x-kde-passwordManagerHint` y tope de tamaño).
- Codificar PNG fuera del hilo de UI: `wl-paste` entrega la imagen ya codificada.
- Hook de teclado de Windows, modificadores atascados, barra de tareas como destino, UIPI/administrador: son exclusivos de Win32 (Linux usa evdev/Hyprland).
- Interruptor de vista previa en la UI: Linux ya lo controla con el ajuste `network_previews`.

## Verificación
- 360 tests OK (+33 nuevos en `tests/test_audit_2026_11.py`), ejecutados en Windows con el venv de `windows-reference`.
- 6 fallos preexistentes, iguales antes y después, por depender del entorno Linux (unidad systemd, `wl-paste`, instalador `.sh`, runtime privado).
- `app.py` solo compilado (importa `fcntl`, no ejecutable en Windows).
- No probado en una sesión Linux real.
