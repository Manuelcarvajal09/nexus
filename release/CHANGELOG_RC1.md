# Nexus Windows — Registro de cambios

## 0.2.0rc1 (release candidate, local, no distribuida)

Primera versión candidata de la capa nativa de Windows (PySide6). La versión anterior del paquete
era `0.1.0`. Los cambios se agrupan por entrega; entre paréntesis, el commit.

### Empaquetado y dependencias (RC1)
* Versión `0.2.0rc1` (pyproject y paquete coinciden; hay un test que lo vigila).
* Dependencia de ejecución: **`PySide6-Essentials`** en lugar del metapaquete `PySide6`
  (el código solo importa QtCore/QtGui/QtWidgets, y QtDBus en Linux). Evita instalar WebEngine,
  Addons y Pdf. Toda la suite pasa en un venv limpio con solo Essentials.
* Build reproducible desde el commit (`git archive`), con herramientas de build fijadas por hash;
  dos compilaciones consecutivas deben dar un wheel idéntico byte a byte.
* Dependencias de ejecución fijadas por hash (`requirements-rc1.lock`) y empaquetadas en `wheelhouse/`:
  la instalación no necesita red.
* Gestor `rc1_manage.py`: instalar, actualizar, revertir y desinstalar sin tocar los datos del usuario.
* Verificador de integridad `rc1_verify.py` (SHA256SUMS, RECORD del wheel, wheelhouse fijado).
* Archivos de Windows Sandbox para pruebas aisladas (portapapeles desactivado, repositorio en solo lectura).

### Experiencia de usuario (ux-transformation)
* Stack: «Vaciar Stack» pide un segundo clic en línea; la lista ya no corta la última fila (5926bf9, f00cfe8).
* Buscador contextual («Buscar en «Pinboard»…»); Configuración con título propio y sin barra de pinboards (5926bf9, 7a11d24).
* Gestor de Pinboards: contadores en una columna reservada, resumen y estado vacío con acción (7a11d24, 75a1dcb).
* Estados vacíos con ícono y una acción siguiente (7a11d24).
* **Deshacer eliminar**: `Supr` borra la tarjeta seleccionada; «Eliminado · Deshacer» y `Ctrl+Z` restauran
  contenido, título, fechas y pinboards vigentes (7a11d24, 7676b14).
* Menú de tarjeta con los atajos de las acciones frecuentes (7676b14).
* Sin animación al cambiar de pinboard por teclado (09e067b).
* **Foco**: al cerrar con Esc, atajo o ×, Nexus vuelve a la aplicación en la que estaba el usuario (09e067b).
* Etiqueta legible «Imagen» en lugar de `[imagen]` (f00cfe8).

### Base de la rama `windows-native` (ya existente antes de esta fase)
Títulos editables (ac94902), cabecera limpia sin leyenda ni Expandir (5894cfb), Configuración y atajos
personalizables (cce66dd), íconos, identidad de barra de tareas y bandeja (0ce0a99), movimiento
contenido (074fdf6), Liquid Glass adaptable (2103a8f, d0ff159), herramientas de aceptación aislada
y de copia/restauración de datos (2780bbc).

### Calidad
* 1019 pruebas automatizadas (offscreen); aisladas del estado real del teclado y del portapapeles del sistema.
* Herramienta de validación nativa (`tools/windows_native_e2e.py`) y de auditoría de layout.

### Cambios de datos
Ninguno nuevo en RC1. El esquema solo tiene columnas **aditivas** (`title`, `title_search`) añadidas por
la entrega de títulos; la migración es idempotente.

### Limitaciones conocidas
Ver `README_RC1.md`, sección «Limitaciones».
