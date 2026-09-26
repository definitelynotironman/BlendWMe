"""BlendWMe's keyboard shortcuts, in Blender's add-on keyconfig.

They show up (and can be changed) in Preferences > Keymap, under Window.
They are registered and removed with the add-on, so a disabled BlendWMe
leaves no shortcut behind.

    Ctrl+Alt+M   open or focus BlendWMe (free in Blender 4.2's default keymap)
    the panel keys (Enter, Shift+Enter, Esc, Up, Down, Shift+Tab, Ctrl+L) as
    "BlendWMe Panel Key" items: the panel reads them from here, and they act only
    while the panel has focus (panel_ops.py)
"""

import bpy

GLOBAL_KEY = {"type": "M", "value": "PRESS", "ctrl": True, "alt": True}

_items = []      # [(keymap, keymap item)] to remove again


def register():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:          # background Blender has no key configuration
        return
    km = kc.keymaps.new(name="Window", space_type="EMPTY")
    kmi = km.keymap_items.new("blendwme.open", **GLOBAL_KEY)
    _items.append((km, kmi))
    from .panel_ops import PANEL_KEYS
    for action, key in PANEL_KEYS:
        kmi = km.keymap_items.new("blendwme.panel_key", **key)
        kmi.properties.action = action
        _items.append((km, kmi))


def unregister():
    for km, kmi in _items:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _items.clear()
