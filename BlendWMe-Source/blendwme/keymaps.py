# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
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

__author__ = "definitelynotironman"

GLOBAL_KEY = {"type": "M", "value": "PRESS", "ctrl": True, "alt": True}


class ShortcutRegistry:
    """Owns every keymap item BlendWMe adds, so all of them can be removed again.

    Blender keeps add-on shortcuts in ``keyconfigs.addon``. Items must be removed
    from the same keymap they were added to, so the registry remembers each
    (keymap, item) pair it created.
    """

    def __init__(self):
        """Start with no registered items."""
        self.items = []      # [(keymap, keymap item)] to remove again

    def register(self):
        """Add Ctrl+Alt+M and the editable panel keys to the Window keymap.

        Background Blender has no key configuration; the call is then a no-op.
        """
        kc = bpy.context.window_manager.keyconfigs.addon
        if kc is None:
            return
        km = kc.keymaps.new(name="Window", space_type="EMPTY")
        kmi = km.keymap_items.new("blendwme.open", **GLOBAL_KEY)
        self.items.append((km, kmi))
        from .panel_ops import PANEL_KEYS
        for action, key in PANEL_KEYS:
            kmi = km.keymap_items.new("blendwme.panel_key", **key)
            kmi.properties.action = action
            self.items.append((km, kmi))

    def unregister(self):
        """Remove every item this registry added. Items Blender already freed are skipped."""
        for km, kmi in self.items:
            try:
                km.keymap_items.remove(kmi)
            except (ReferenceError, RuntimeError):
                pass
        self.items.clear()


# One registry per add-on session. The module functions are what lifecycle.py calls.
shortcuts = ShortcutRegistry()
register = shortcuts.register
unregister = shortcuts.unregister
