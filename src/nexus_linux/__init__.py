"""Compatibility package: the code moved to `nexus` (core/ui/platform).

Every old module name is an alias of the very same module object, so
`import nexus_linux.windows_paste`, `python -m nexus_linux.app` and launchers,
autostart entries, Hyprland binds or systemd units written by earlier
versions keep working unchanged.
"""
import importlib
import importlib.abc
import importlib.util
import sys

from nexus import __version__  # noqa: F401

ALIASES = {
    "app": "nexus.platform.linux.app",
    "appearance": "nexus.platform.linux.appearance",
    "board_panel": "nexus.platform.linux.board_panel",
    "caelestia_theme": "nexus.platform.linux.caelestia_theme",
    "capture_status": "nexus.platform.linux.capture_status",
    "capture_validation": "nexus.platform.linux.capture_validation",
    "clipboard_monitor": "nexus.platform.linux.clipboard_monitor",
    "clipboard_offer": "nexus.platform.linux.clipboard_offer",
    "control": "nexus.platform.linux.control",
    "dbus_service": "nexus.platform.linux.dbus_service",
    "desktop": "nexus.platform.linux.desktop",
    "evdev_stack_watcher": "nexus.platform.linux.evdev_stack_watcher",
    "glass_surface": "nexus.ui.glass_surface",
    "global_shortcuts": "nexus.platform.linux.global_shortcuts",
    "history_delegate": "nexus.platform.linux.history_delegate",
    "i18n": "nexus.core.i18n",
    "i18n_en": "nexus.core.i18n_en",
    "linux_shared": "nexus.platform.linux.shared",
    "linux_stack": "nexus.platform.linux.stack",
    "main_window": "nexus.platform.linux.main_window",
    "panel_geometry": "nexus.ui.panel_geometry",
    "paste_service": "nexus.platform.linux.paste_service",
    "paths": "nexus.core.paths",
    "pinboard_editor": "nexus.ui.pinboard_editor",
    "pinboard_sidebar": "nexus.ui.pinboard_sidebar",
    "preview_service": "nexus.core.preview_service",
    "product_icons": "nexus.ui.product_icons",
    "product_ui": "nexus.platform.linux.product_ui",
    "production_capture": "nexus.platform.linux.production_capture",
    "relative_time": "nexus.core.relative_time",
    "row_widget": "nexus.ui.row_widget",
    "sequential_stack": "nexus.platform.linux.sequential_stack",
    "session": "nexus.platform.linux.session",
    "settings": "nexus.platform.linux.settings",
    "shelf_delegate": "nexus.platform.linux.shelf_delegate",
    "stack": "nexus.core.stack",
    "stack_hyprland": "nexus.platform.linux.stack_hyprland",
    "stack_window": "nexus.ui.stack_base",
    "stack_window_linux": "nexus.platform.linux.stack_window",
    "storage": "nexus.core.storage",
    "styles": "nexus.ui.styles",
    "thumbnails": "nexus.core.thumbnails",
    "timeline_delegate": "nexus.ui.timeline_delegate",
    "timeline_model": "nexus.core.timeline_model",
    "timeline_window": "nexus.ui.timeline_base",
    "timeline_window_linux": "nexus.platform.linux.timeline_window",
    "type_detection": "nexus.core.type_detection",
    "web_preview": "nexus.core.web_preview",
    "win32dll": "nexus.platform.windows.dll",
    "windows_app": "nexus.platform.windows.app",
    "windows_autostart": "nexus.platform.windows.autostart",
    "windows_backdrop": "nexus.platform.windows.backdrop",
    "windows_card_delegate": "nexus.ui.card_delegate",
    "windows_clipboard": "nexus.platform.windows.clipboard",
    "windows_design": "nexus.ui.design",
    "windows_icons": "nexus.ui.icons",
    "windows_keyboard": "nexus.platform.windows.keyboard",
    "windows_material": "nexus.ui.material",
    "windows_motion": "nexus.ui.motion",
    "windows_optical": "nexus.ui.optical",
    "windows_paste": "nexus.platform.windows.paste",
    "windows_platform": "nexus.platform.windows.win32",
    "windows_sandbox": "nexus.platform.windows.sandbox",
    "windows_settings": "nexus.core.preferences",
    "windows_settings_page": "nexus.ui.settings_page",
    "windows_shortcuts": "nexus.ui.shortcuts",
    "windows_stack": "nexus.platform.windows.stack",
    "windows_system_theme": "nexus.platform.windows.system_theme",
    "windows_timeline": "nexus.ui.timeline"
}


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, target):
        self.target = target

    def create_module(self, spec):
        return importlib.import_module(self.target)

    def exec_module(self, module):
        pass

    def get_code(self, fullname):   # `python -m nexus_linux.<module>` runs the new module
        return importlib.util.find_spec(self.target).loader.get_code(self.target)


class _AliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        package, _, name = fullname.rpartition(".")
        if package != __name__ or name not in ALIASES:
            return None
        target_spec = importlib.util.find_spec(ALIASES[name])
        # origin matters: `python -m` copies it into sys.argv[0] (None crashes QApplication).
        return importlib.util.spec_from_loader(fullname, _AliasLoader(ALIASES[name]),
                                               origin=target_spec.origin if target_spec else None)


if not any(isinstance(finder, _AliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())
