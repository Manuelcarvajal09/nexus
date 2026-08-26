# Compilar / ejecutar Nexus Linux

## Requisitos

- Python ≥ 3.11
- `wl-clipboard` (`wl-copy`, `wl-paste`)
- `ydotool` + servicio de usuario `ydotoold` habilitado
- Sesión KDE Plasma (para `kglobalaccel`; el resto funciona en cualquier compositor Wayland con soporte de `wlr-data-control`/`ext-data-control`)

En Arch/CachyOS:

```sh
sudo pacman -S wl-clipboard ydotool
systemctl --user enable --now ydotool.service
```

Si `/dev/uinput` no te da acceso (`ydotool` falla), necesitas estar en el grupo `input` o tener una regla udev con `TAG+="uaccess"` para ese dispositivo — pide a alguien con sudo que lo revise, no lo automatices con contraseña.

## Instalación

```sh
git clone/rsync este directorio a, por ejemplo, ~/Projects/Nexus-linux
cd ~/Projects/Nexus-linux
./install.sh              # crea venv, instala PySide6, registra el launcher
./install.sh --autostart  # además registra autostart XDG
```

No usa `sudo` en ningún paso — todo vive en `.venv/` del proyecto y en `$XDG_DATA_HOME`/`$XDG_CONFIG_HOME` del usuario.

## Ejecutar manualmente (sin instalar)

```sh
cd ~/Projects/Nexus-linux
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/nexus-linux
```

## Desinstalar

```sh
./uninstall.sh              # quita launcher, autostart y venv; conserva tu historial
./uninstall.sh --purge-data # además borra ~/.local/share/nexus (tu historial)
```

## Estructura

```text
Nexus-linux/
├── src/nexus_linux/
│   ├── app.py                 punto de entrada, tray, wiring
│   ├── storage.py             SQLite, esquema y dedup SHA-256
│   ├── clipboard_monitor.py   captura vía wl-paste --watch
│   ├── paste_service.py       escritura vía wl-copy + paste sintético vía ydotool
│   ├── stack.py                lógica de Nexus Stack (FIFO/LIFO)
│   ├── stack_window.py         ventana de Nexus Stack
│   ├── pinboard_sidebar.py     sidebar de Pinboards (tableros con nombre/color/emoji)
│   ├── dbus_service.py         servicio D-Bus propio (para atajos KDE personalizados)
│   ├── styles.py                tema visual (QSS + glow)
│   ├── global_shortcuts.py    registro de atajos vía kglobalaccel/D-Bus
│   ├── main_window.py         panel principal (búsqueda, lista, menú contextual)
│   └── paths.py               rutas XDG
├── resources/                  iconos + plantilla .desktop
├── install.sh / uninstall.sh
├── pyproject.toml
├── MIGRATION_LINUX.md          decisiones, hallazgos, estado verificado
└── BUILD_LINUX.md              este archivo
```

## Logs

`~/.cache/nexus/nexus.log` (también se imprime a stderr si corres manualmente).

## Diagnóstico rápido si algo no funciona

```sh
# ¿el proceso vive?
pgrep -af '.venv/bin/nexus-linux'

# ¿ydotoold corriendo?
systemctl --user status ydotool.service

# ¿el portapapeles del sistema responde?
echo -n prueba | wl-copy && wl-paste

# ¿kglobalaccel ve el componente?
busctl --user call org.kde.kglobalaccel /kglobalaccel org.kde.KGlobalAccel allComponents
```
