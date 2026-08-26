# Paste — Análisis de Producto (para informar Nexus 2.0)

**Fecha:** 2026-08-26. Fuente: documentación pública oficial de pasteapp.io (home, `/use-cases`, `/updates`, y los artículos de `/help` citados en cada sección). Todo lo que aparece entre comillas es cita textual de esas páginas, verificada durante esta sesión — no se inventó ni se extrapoló nada más allá de lo documentado. Donde Paste no documenta un detalle (p. ej. paleta de colores exacta, radios de esquina), se dice explícitamente "no documentado" en vez de inventarlo.

**Aviso de propiedad intelectual:** este documento describe patrones de interacción y arquitectura de producto con fines de estudio comparativo. Nexus no copia nombre, logo, marca, código, assets, ilustraciones, ni textos comerciales de Paste — ver §16/§17 para lo que se adopta como *patrón* (no como copia) y lo que se descarta explícitamente.

---

## 1. Product Philosophy

Paste se posiciona con la promesa central: **"Anything you copied, back in seconds."** Tres pilares explícitos:

- **Nunca perder nada**: "Nothing you copy is ever lost" — historial sin límite de tamaño.
- **Privacidad local-first**: "Your clipboard stays on your device and in your private iCloud. It never touches our servers." La sincronización entre dispositivos es una decisión de arquitectura (iCloud privado del usuario), no un servidor propio de Paste.
- **IA como extensión opcional, no automática**: integración vía MCP con Claude/Codex/Cursor — el usuario decide exponer contenido a una IA, no ocurre por defecto. Esto valida directamente la restricción que ya nos dieron para Nexus ("privacidad primero, sin envío automático a IA").

Producto maduro: "10 Years on AppStore", 4.5★ con 16K+ reseñas — la propuesta de valor no es una función nueva, es *pulido acumulado* sobre una idea simple.

## 2. Core Workflows

Tres modos de uso, todos accesibles por teclado:

1. **Historial pasivo**: todo lo que copias queda disponible, buscable, sin que el usuario haga nada.
2. **Colección activa de corto plazo — Paste Stack**: el usuario activa explícitamente una sesión, copia varias cosas a propósito, y las pega en orden — mapea 1:1 con Nexus Stack.
3. **Colección persistente — Pinboards**: curaduría deliberada de items que importan más allá del historial cronológico.

## 3. Information Architecture

- **Un solo nivel de agrupación por Pinboard**: "each item can belong to one pinboard at a time" — a diferencia de Nexus actual, donde un item puede pertenecer a *varios* pinboards (many-to-many). Esto es una decisión de producto explícita de Paste, no una limitación técnica — **Nexus debería decidir conscientemente si mantiene su many-to-many actual (más flexible) o simplifica a "un pinboard por item" (más simple de entender, más fácil de visualizar con un solo color/badge por card)**. Ver §18.
- El historial es la vista por defecto ("Historial"); los Pinboards son una navegación paralela, no un filtro oculto — accesibles con `Cmd+Left/Right` para saltar entre ellos sin usar el mouse.
- La búsqueda no es una pantalla separada: vive *dentro* de la misma vista de timeline, con filtros que aparecen en línea a medida que se escribe.

## 4. Visual Language

Lo que Paste documenta explícitamente:

- **"Paste 6 in Liquid Glass" (sept. 2025)**: rediseño completo ("every item, pinboard, and interaction has been rebuilt") apoyado en el lenguaje visual nativo más reciente de Apple (materiales translúcidos tipo vidrio). La lección de fondo, no la textura literal: **apostar por el lenguaje visual nativo de la plataforma anfitriona**, no por una estética propia desconectada del sistema. Para Nexus, el equivalente correcto es apoyarse en Breeze/KDE (translucidez soportada por KWin, radios y espaciados consistentes con Plasma 6), no imitar el "glass" de Apple.
- Cards identificables **visualmente, sin necesidad de leer texto** — principio explícito ya recogido en el pedido original.
- No se documentan valores exactos de tipografía, paleta de color, radios de esquina, ni curvas de animación — Paste no publica su design system públicamente. **Todo lo que Nexus define en `DESIGN_SYSTEM.md` es una decisión propia**, informada por el *tipo* de decisiones que Paste toma (nativo, sobrio, denso en información visual) pero no una copia de valores concretos que ni siquiera están publicados.

## 5. Timeline

Este es el hallazgo más importante para la arquitectura de ventana de Nexus 2.0, y viene de dos fuentes independientes que coinciden (`/help/paste-on-mac` y la descripción general del historial):

> "The clipboard history displays as **a visual timeline, with the most recent items first**."
> "The interface sits **as a bar across the bottom of your screen**."
> "As you make the window smaller, Paste switches to **Compact Mode**, a condensed view that focuses on content and previews."
> "Use the **left and right arrow keys** to move between items, or scroll with a trackpad or mouse."

Es decir: **una barra horizontal anclada al borde inferior de la pantalla**, navegación horizontal por flechas, redimensionable arrastrando el borde superior, con un modo compacto para cuando se reduce. No es un panel centrado tipo Spotlight/Albert, ni una ventana de aplicación tradicional.

**Verificación técnica hecha en esta sesión (no asumida):** probé en vivo, en Erwin, si PySide6 puede posicionar una ventana sin decoración anclada al borde inferior de la pantalla bajo KWin/Wayland (una duda real, dado que Wayland por protocolo no informa a un cliente su propia posición en pantalla). Resultado: **sí funciona** — un `QWidget` sin bordes con `WindowStaysOnTopHint` y `setGeometry()` explícito se renderizó exactamente en la posición y tamaño solicitados (confirmado con una captura de `spectacle`), aunque la propia API de Qt (`geometry()`) reporta mal la posición después (limitación conocida y ya documentada de Qt sobre Wayland: el cliente no puede *consultar* su posición real, pero **sí puede establecerla** en este KWin). Esto significa que el layout de barra horizontal de Paste es implementable en Nexus con PySide6 puro, sin necesitar bindings de `layer-shell-qt` (que existe instalado en el sistema pero no tiene binding para PySide6/Python).

## 6. Cards

Cada entrada del historial: **preview + app de origen + timestamp**. Sin más detalle público sobre el layout interno de la card (tipografía, padding exactos). El principio de fondo, ya confirmado por el hallazgo de `/help/what-paste-captures`, es que el tipo de contenido determina qué preview se muestra: texto, enlaces, imágenes, archivos, colores — cada uno con su propia forma de "verse" en la card, no una plantilla única forzada para todos.

## 7. Pinboards

- Creación: nombre + color ("**The color helps you quickly recognize and visually differentiate pinboards**").
- Alta de items: acción "Pin" desde el menú contextual, arrastrar directamente al pinboard, o multi-selección + Pin en batch.
- **Un item pertenece a un solo pinboard a la vez** (ver §3 — divergencia consciente a resolver para Nexus).
- Persistentes: "don't expire or disappear" — exentos de cualquier retención automática (Nexus ya replica esto: `purge_older_than()` excluye items anclados).
- Reordenables por drag&drop.
- Compartibles con equipo/familia con permisos — fuera de alcance para Nexus hoy (no hay sincronización multi-dispositivo ni backend), mencionado aquí solo por completitud.

## 8. Stack

Documentado con total precisión en `/help/using-paste-stack` y `/help/keyboard-shortcuts`:

- Activación: **Shift-Cmd-C** (equivalente exacto de la letra que Nexus ya usa: Ctrl+Shift+**C**).
- Recolección: "everything you copy will get into the stack **in order**" mientras la sesión está activa.
- Consumo: **no hay un atajo especial de "pegar siguiente"** — se usa el **Cmd-V normal**, uno tras otro, y cada paste consume el siguiente item en orden. Esta es la validación más importante de esta investigación: **Nexus ya implementa exactamente este mismo modelo** (Ctrl+V real, detectado a nivel de kernel vía evdev, consume y avanza) — no es una idea nuestra improvisada, es el mismo diseño que usa el producto de referencia, y ya está probado y estabilizado en Fase 1.
- Dirección: un botón de flechas para invertir el orden — Nexus ya tiene esto (`_toggle_order`, ícono `go-up`/`go-down`).
- Remoción: menú contextual, o gesto de swipe de dos dedos (específico de trackpad macOS, sin equivalente directo en escritorio KDE — Nexus puede mantener su clic derecho + tecla Supr, que ya cubre el mismo caso de uso).
- Auto-limpieza: **"Re-used items will automatically disappear from the stack"** — exactamente el comportamiento que Fase 1 blindó con 30 tests.
- Se describe a sí mismo como **"a temporary ad hoc pinboard"** — un pinboard efímero de un solo uso. Encaje conceptual útil para cómo presentarlo visualmente en Nexus 2.0 (ver §16).

## 9. Search

De `/help/search-and-filters`:

- **Search-as-you-type real**: "As soon as you start typing, results update instantly."
- Indexa texto, enlaces, imágenes (con OCR: "Paste also recognizes text inside images") y archivos.
- Filtros por **origen (app), tipo de contenido, fecha, dispositivo** — presentados como *chips inline dentro del propio campo de búsqueda* a medida que se escribe, con un botón para ver todos los filtros disponibles de una vez.
- Navegación: `Return` mueve el foco a resultados, flechas ←/→ navegan entre items, `Return` de nuevo pega. `Cmd-G` salta al lugar original del item (historial completo o el pinboard donde vive).

## 10. Quick Paste

De `/help/quick-paste-with-numbered-shortcuts` y `/help/keyboard-shortcuts`:

- Con la ventana principal abierta (`Shift-Cmd-V`), mantener `Cmd` + un número **1–9** pega directamente ese item de la lista actual, sin navegar con flechas ni presionar Enter.
- `Shift-Cmd-1..9` hace lo mismo pero forzando texto plano.
- Explícitamente **no hay atajos personalizables por snippet** todavía en Paste — es puramente posicional (el 1er-9º item visible), no un sistema de "favoritos numerados" fijos.

## 11. Keyboard Navigation

Lista completa capturada de `/help/keyboard-shortcuts` (mapeo de teclas Mac, no para copiar literal sino para diseñar el equivalente Linux con las mismas *categorías* de acción):

| Categoría | Acción | Tecla Mac |
|---|---|---|
| Ventana | Mostrar/ocultar | Shift-Cmd-V |
| Ventana | Activar Stack | Shift-Cmd-C |
| Ventana | Cerrar | Escape |
| Navegación | Item siguiente/anterior | ←/→ |
| Navegación | Primer/último item | Cmd-↑ / Cmd-↓ |
| Navegación | Extender selección | Shift-← / Shift-→ |
| Navegación | Seleccionar todo | Cmd-A |
| Pegar | Pegar seleccionado | Return |
| Pegar | Pegar como texto plano | Shift-Return |
| Pegar | Quick Paste 1-9 | Cmd-1..9 |
| Pegar | Quick Paste texto plano 1-9 | Shift-Cmd-1..9 |
| Pegar | Copiar al portapapeles del sistema | Cmd-C |
| Preview | Quick Look | Space |
| Edición | Abrir enlace/archivo | Cmd-O |
| Edición | Renombrar | Cmd-R |
| Edición | Editar contenido | Cmd-E |
| Edición | Nuevo item de texto | Cmd-N |
| Edición | Eliminar | Delete |
| Edición | Deshacer | Cmd-Z |
| Búsqueda | Buscar / mostrar filtros | Cmd-F |
| Búsqueda | Alternar foco búsqueda↔resultados | Tab |
| Pinboards | Nuevo pinboard | Shift-Cmd-N |
| Pinboards | Pinboard siguiente/anterior | Cmd-→ / Cmd-← |
| Otro | Pausar captura | Cmd-T |
| Otro | Ajustes | Cmd-, |
| Otro | Salir | Cmd-Q |

**Nota de traducción a Linux/KDE** (no se debe copiar Cmd→Ctrl mecánicamente sin revisar): `Ctrl+Shift+V`/`Ctrl+Shift+C` ya están tomados por Nexus (correctos, mismas letras que Paste). `Ctrl+F` para buscar podría chocar con el foco nativo del campo de búsqueda que ya existe siempre visible en Nexus (a diferencia de Mac, donde el campo de búsqueda no está siempre a la vista) — decisión de diseño en §16, no una copia automática. `Ctrl+,` para ajustes es una convención GNOME/KDE también común, sin conflicto esperado.

## 12. Previews

- **Enlaces**: metadata web (Open Graph), con Quick Look. Nexus ya tiene un pipeline de captura sin bloqueo (`wl-paste` async) — el trabajo de Fase 2 es añadir el *fetch* de metadata OG de forma asíncrona, con timeout y cache, nunca bloqueando el hilo de UI (ya identificado como requisito explícito).
- **Imágenes**: rotación y extracción de texto (OCR) directamente en el panel de preview — funcionalidad de edición avanzada, fuera del alcance inmediato de Nexus 2.0 (candidato de fase futura, no bloqueante).
- No se documentan previews específicos de código, JSON o color en las páginas públicas revisadas — esas cards son una **oportunidad de diferenciación propia de Nexus** (ver §18), no un patrón a imitar porque Paste no publica cómo las resuelve.

## 13. Use Cases (resumen de `/use-cases`, con foco en decisiones funcionales, no en el texto de marketing)

| Perfil | Dolor real que resuelve | Función concreta que lo resuelve |
|---|---|---|
| **General ("Everyone")** | Perder un link/mensaje que ya se había encontrado antes | Historial sin límite + búsqueda cruzada |
| **Developers** | El portapapeles sobreescribe el snippet anterior a mitad de una tarea | Stack (pegado secuencial) + colecciones etiquetadas de snippets |
| **Designers** | Repetir la búsqueda del mismo color/asset varias veces al día | Pinboards por proyecto/marca para colores, assets, referencias |
| **Content/Marketing** | Perder una reescritura a mitad de edición, o desorganizar research | Pinboards de investigación + plantillas de copy reutilizables |

La lección estructural, no textual: **cada perfil no necesita una función nueva — necesita que las mismas 3 piezas (historial, Stack, Pinboards) se organicen distinto según su flujo de trabajo**. Nexus no debe construir "modos" separados por profesión (rechazado explícitamente por el pedido original como feature creep) — debe hacer que Pinboards + detección de tipo + búsqueda sean lo bastante buenos como para que cada perfil los use a su manera.

## 14. Performance / Perceived Performance

No hay números públicos de latencia. Lo que sí es explícito y es una lección de arquitectura, no de marketing: **"All-New Paste for Mac (junio 2023): ... major performance boost"** fue un hito de rediseño propio, separado del hito puramente visual de 2025 — es decir, Paste trató el rendimiento como un proyecto explícito, no un efecto secundario del diseño. Esto valida el orden que ya seguimos: Fase 1 (cimientos/rendimiento) antes de Fase 2 (producto/visual), en vez de mezclar ambos.

## 15. Microinteractions

No documentado públicamente con detalle (duraciones exactas, curvas de easing). El pedido original ya fija el rango correcto a usar en Nexus (100-200ms) independientemente de lo que Paste use puertas adentro.

## 16. What Nexus Should Adopt (patrones, no assets)

1. **Barra horizontal anclada al borde inferior**, timeline con lo más reciente primero, navegación con flechas ←/→, modo compacto al reducir tamaño — verificado como técnicamente viable en Erwin (ver §5).
2. **El Stack se consume con el paste normal (Ctrl+V real)**, no con un atajo especial — Nexus ya lo hace así; esta investigación lo confirma como la decisión correcta, no una casualidad.
3. **Quick Paste posicional (1-9) sobre la vista actual**, sin necesidad de atajos personalizados por snippet — alcance acotado y ya probado en producción por Paste durante años.
4. **Filtros de búsqueda como chips dentro del campo de búsqueda**, visibles y editables, no un modal aparte.
5. **Pinboards con color propio, reordenables, con navegación directa por teclado** (siguiente/anterior pinboard) — hoy en Nexus viven en una barra lateral secundaria; deben ganar protagonismo real.
6. **Apostar por el lenguaje visual nativo de la plataforma** (Breeze/KDE) en vez de una estética desconectada del sistema — el principio detrás de "Liquid Glass", no la textura literal.
7. **Mostrar primero, enriquecer después** — confirmado como filosofía deliberada de Paste (ver §14), no solo un pedido nuestro.

## 17. What Nexus Should NOT Copy

- Nombre, logo, marca, iconografía propietaria, capturas de pantalla, ilustraciones, textos de marketing — ninguno de estos se reprodujo en este documento ni se reproducirá en el código.
- El modelo "un item = un solo pinboard" **no se copia automáticamente** — es una divergencia consciente a decidir (§18), porque Nexus ya tiene un modelo many-to-many funcionando y probado; cambiarlo sería una regresión de funcionalidad sin una razón de UX suficientemente fuerte todavía.
- Sincronización multi-dispositivo / iCloud / equipos — fuera de alcance de Nexus (aplicación de escritorio Linux local, sin backend propio); no se simula ni se promete.
- Cualquier valor visual concreto (colores, tipografía, radios) que Paste no publica — no existen para copiar, y no se inventan atribuyéndoselos a Paste.

## 18. Nexus-Specific Opportunities (no presentes en Paste, o donde Nexus puede diferenciarse)

- **Cards de tipo color/JSON/código con preview real** (Paste no documenta públicamente cómo las resuelve) — espacio abierto para que Nexus tenga una identidad visual propia y más rica en estos tres tipos específicamente.
- **Integración nativa KDE/Wayland de primera clase** (KGlobalAccel ya resuelto en Fase 1, tray nativo, iconografía Breeze) — Paste es un producto Apple-first; Nexus puede ser genuinamente "Linux-first", no un port.
- **Decisión pendiente, no tomada todavía**: ¿mantener items en múltiples pinboards a la vez (más flexible, ya funciona) o simplificar a uno-por-item (más fácil de visualizar con un solo acento de color por card, como hace Paste)? Recomendación: **mantener many-to-many** — es una funcionalidad ya construida, probada, y estrictamente más flexible; renunciar a ella por parecerse más a Paste sería exactamente el "clon barato" que el pedido original quiere evitar. Se revisita solo si el diseño visual de la card lo exige de forma insalvable.
- **Undo de remociones del Stack**: Paste no lo documenta como función explícita del Stack — si Nexus lo construye bien (ver Fase 2, sección Stack 2.0), sería una mejora real sobre el original, no una imitación.
