# NEXUS 2.0 RC1 — Reporte Final

**Fecha:** 2026-08-26 · **Tag:** `nexus-2.0-rc1` · **Rama:** `nexus-2.0` · Instalado como versión activa en Erwin (editable install existente, sin push a remoto).

```
Tests: 230/230

P0: 0
P1: 0

Ctrl+Shift+V (end-to-end real, ydotool→KWin→D-Bus→ventana):
p50: 25.8ms
p95: 26.6ms
(interno Nexus, sin la cadena KWin/D-Bus: p50 0.40ms / p95 0.60ms)

Ctrl+Shift+C (end-to-end real):
p50: 25.9ms
p95: 26.6ms
(interno Nexus: p50 0.45ms / p95 1.14ms)

CPU idle: 0% (todos los hilos, incluido nexus-evdev-wat — sin regresión del bug de Fase 1)
RAM idle: ~146MB RSS, estable tras 110 operaciones de soak test (±156KB de ruido, sin crecimiento)

Stack sequential: PASS
  A,B,C → Ctrl+V real ×3 → B,C → C → vacío, sin botón manual, verificado contra el proceso real.
  A,A,B → dedup intencional de sesión (documentado desde Fase 1) → [A,B] → ambos consumidos limpiamente.

Web previews: PASS
  Wikipedia real: favicon + OG image + título, ~1-2s, cacheado.
  Dominio inexistente (storem-caddy real del sistema): degradación limpia a "failed" cacheado, sin crash, sin reintento inmediato.

YouTube previews: PASS
  oEmbed público (sin API key): título + canal + thumbnail para una URL real de YouTube, verificado visualmente en la card.

Search: PASS
  Search-as-you-type ya activo desde Fase 2 (debounce 150ms, índice existente de Fase 1).
  Nuevo en Fase 2.5: títulos obtenidos post-captura (YouTube/OG) ahora indexados — verificado en ambos caminos (fetch nuevo y cache-hit).

Pinboards: PASS (tras 2 fixes esta sesión)
  Creación, selección, rename, ícono, mover, eliminar: todos ahora alcanzables sin clic derecho (botón "⋮" agregado).
  Chips de filtro en Timeline: estado seleccionado ahora visualmente distinguible (antes idéntico a no-seleccionado).

Single instance: PASS
  Segunda instancia sale con exit 0 y log claro; verificado de nuevo en Fase 2.5.

Suspend/reconnect resilience: PARTIAL
  Reconexión de teclado (evdev): PASS, verificado en Fase 1 con causa raíz corregida (bucle de CPU) y re-confirmado sin regresión en el soak test de Fase 2.5.
  Suspensión real del sistema completo: NO PROBADA — suspender la máquina habría cortado esta misma sesión de trabajo. Pendiente de prueba física de Manuel.

Known limitations:
- Suite de UI automatizada (QTest con widgets reales) no existe todavía; toda la cobertura de 230 tests es lógica pura o verificación manual en vivo.
- Monitor ultrawide 34" no conectado todavía; panel_geometry.py probado con 58 tests sintéticos equivalentes, no contra el hardware real.
- Suspend/resume real del sistema sin probar (ver arriba).
- Búsqueda con LIKE no indexable por comodín inicial: aceptable hoy (540 filas reales, 80ms medido sobre 50,000 filas sintéticas), FTS5 sería la vía si el historial creciera mucho más.
- Sin drag&drop de cards a pinboards/apps externas (evaluado, no implementado -- ver "qué se evaluó pero no se construyó" en NEXUS_2_RELEASE_NOTES.md).
```

## Incidente observado durante esta sesión (no relacionado con Nexus)

A mitad de las pruebas de Fase 2.5, KWin reportó transitoriamente **0 pantallas** (`Number of Screens: 0` vía `qdbus6 org.kde.KWin /KWin supportInformation`), sostenido durante ~25 minutos, coincidiendo con errores de framebuffer en el log de KWin (`GL_INVALID_VALUE`, `Failed to create an offscreen framebuffer`). Diagnóstico más probable: el monitor entró en suspensión por inactividad (la sesión estuvo trabajando de forma autónoma un tiempo largo) y el compositor no se recuperó limpiamente al momento exacto de las pruebas.

**Impacto verificado, acotado y ya mitigado:**
- El despacho de atajos globales vía `kglobalaccel`/KWin dejó de funcionar mientras persistió la condición (confirmado: 0 disparos tras 3 intentos).
- El servicio D-Bus de respaldo de Nexus (`qdbus6 mx.com.storem.nexus /Nexus Show{MainPanel,Stack}`) **siguió funcionando perfectamente** durante todo el incidente, permitiendo continuar todas las pruebas de Stack/captura sin depender del atajo físico.
- Se añadió blindaje defensivo nuevo (`TimelineWindow._apply_geometry()`, `StackWindow._reposition_to_active_screen()`) para que Nexus nunca crashee si el compositor reporta cero pantallas — verificado que sin este fix habría producido una excepción no controlada.
- Herramientas de captura de pantalla (`spectacle`) también fallaron durante la misma ventana, confirmando que es un problema del sistema/compositor y no específico de Nexus.

**Recomendación para Manuel:** si al volver a la máquina el monitor sigue sin responder o `spectacle`/capturas de pantalla siguen fallando, valdría la pena revisar `journalctl --user -u plasma-kwin_wayland` por los errores de framebuffer — podría indicar un problema de driver gráfico que conviene atender independientemente de Nexus.
