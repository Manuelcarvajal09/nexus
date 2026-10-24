# Informe de consolidación Git — Nexus Windows

Fecha: 2026-10-24. Rama: `windows-native`. **Sin push y sin reescribir
historial.** Este informe documenta la ejecución del plan de
`GIT_CONSOLIDATION_PROPOSAL.md`.

## Commits creados

| Hash | Commit | Responsabilidad |
| --- | --- | --- |
| `ff1da6c` | `feat(windows): add native visual system and themes` | Tokens, QSS, paletas, material, backdrop, movimiento y tarjetas |
| `9e76e1d` | `feat(windows): add native clipboard, keyboard and paste runtime` | Monitor, hook, pegado y primitivas Win32 |
| `24ae070` | `fix(windows): harden stack lifecycle and sequential paste` | Stack, FIFO/LIFO, gate de sesión y consumo transaccional |
| `909e65a` | `feat(windows): simplify pinboard navigation and fix chip labels` | Navegación, administrador y chips sin icono duplicado |
| `459b740` | `feat(windows): wire native runtime, stack and composition root` | Integración de `windows_app.py`, panel, Stack y DWM |
| `9152af0` | `feat(windows): route theme changes through the native design system` | Menú de temas y QSS nativo |
| `d50d9aa` | `chore(tools): add Windows diagnostic probes` | Probes de composición, layout, benchmark y Stack |

`windows_app.py` se separó con staging selectivo: el commit `459b740` contiene
el cableado de runtime/Stack/composición y `9152af0` el resto del menú de temas.
Los hunks de inicialización del backdrop y del panel están acoplados en el
mismo bloque; separarlos más habría producido un commit intermedio no
ejecutable. Se revisó el diff staged antes de cada commit.

## Lo que no se versionó

- `install.sh` y `uninstall.sh`: siguen modificados solo por finales de línea
  CRLF (diff lógico de 0 líneas). Se conservan sin commit para no añadir ruido.
- `tools/__pycache__`: ignorado/no versionado.
- Respaldos SQLite, capturas, logs y artefactos de auditoría: fuera del repo.
- Sin `git push`, sin `git rebase`, sin `git commit --amend` y sin cambios de
  historial.

## Verificación

- Suite completa después de la corrección del chip: **614 passed**.
- Chip de Pinboard: cinco temas aislados, sin icono genérico duplicado,
  `Mercadolibre`/`Prompts` completos en negrita, una sola silueta.
- Datos auditados en solo lectura:
  - `clip_items = 1566`, IDs contiguos 1–1566.
  - `pinboards = 2` (Mercadolibre id 1, Prompts id 2).
  - `pinboard_items = 0` (relación vacía legítima; ningún clip asignado).
  - `stack_state = 0` en la auditoría final (actividad externa del usuario).
  - `PRAGMA integrity_check = ok`.
- Respaldo previo al reinicio: 1545 clips, 2 Pinboards, 0 relaciones, Stack
  vacío. No se restauró ni se borró nada.

## Estado final esperado

- Rama `windows-native` con los siete commits anteriores y el commit de
  documentación inmediatamente posterior.
- `install.sh`/`uninstall.sh` preservados como modificaciones locales de CRLF.
- Sin staging pendiente después del commit de documentación.
- Sin remoto actualizado.

## Pendiente conocido

La corrección del chip está validada en una instancia aislada y en pruebas,
pero la instancia habitual PID 12304 sigue ejecutando el código anterior hasta
que se autorice un reinicio adicional. No se reinició dentro de esta fase.
