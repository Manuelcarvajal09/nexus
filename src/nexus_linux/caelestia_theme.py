"""Read generated colours as data, never execute desktop Lua configuration."""
from dataclasses import replace
from pathlib import Path
import re
from . import styles

PALETTE = Path.home() / '.config/hypr/scheme/current.lua'

def read_palette():
    try:
        data = PALETTE.read_text()
    except OSError:
        return {}
    return {key: '#' + colour for key, colour in re.findall(r'^\s*(\w+)\s*=\s*"([0-9a-fA-F]{6})"', data, re.M)}

def apply():
    colours = read_palette()
    required = ('surface', 'surfaceContainer', 'surfaceContainerHigh', 'onSurface', 'onSurfaceVariant', 'primary', 'onPrimary', 'outlineVariant')
    if not all(k in colours for k in required):
        return False
    styles.PRESETS['caelestia'] = replace(styles.PRESETS['midnight_teal'], key='caelestia', display_name='Caelestia · Nexus', bg=colours['surface'], bg_elevated=colours['surfaceContainer'], card_bg=colours['surfaceContainer'], card_bg_hover=colours['surfaceContainerHigh'], card_border=colours['outlineVariant'], card_border_hover=colours['primary'], accent=colours['primary'], accent_ink=colours['onPrimary'], accent_dim=colours['surfaceContainerHigh'], secondary=colours['primary'], text_primary=colours['onSurface'], text_secondary=colours['onSurfaceVariant'], text_tertiary=colours['onSurfaceVariant'], glow=False)
    styles.PRESETS['auto'] = replace(styles.PRESETS['caelestia'], key='auto', display_name='Automático · Caelestia')
    return True
