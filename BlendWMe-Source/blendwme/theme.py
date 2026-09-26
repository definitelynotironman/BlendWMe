"""The three themes (docs/design/UI_SPEC.md, section 2) and "reduce motion".

Colour rule: `hot` means YOU (do this now, look here). `ink` means the AI is
working. The AI's own indicators are never hot.
"""

import sys

# The exact tokens from the design spec, as hex.
TOKENS = {
    "GRAPHITE": {"bg": "FFFFFF", "panel": "F1F1EF", "card": "FFFFFF", "ink": "222222", "accent": "222222",
                 "on_accent": "FFFFFF", "shadow": "9A9A9A", "hot": "D71920", "muted": "5E5E5E"},
    "RED": {"bg": "FFFFFF", "panel": "FFFFFF", "card": "FBE8EC", "ink": "141414", "accent": "D71920",
            "on_accent": "FFFFFF", "shadow": "141414", "hot": "D71920", "muted": "5B4A4E"},
    "BLENDER": {"bg": "232323", "panel": "3A3A3A", "card": "2B2B2B", "ink": "EDEDED", "accent": "EDEDED",
                "on_accent": "232323", "shadow": "111111", "hot": "F5A623", "muted": "B8B8B8"},
}

# Shapes, in CSS pixels at 100 % scale.
BORDER_PANEL = 3.0
BORDER_CARD = 2.5
RADIUS = 10.0
SHADOW_PANEL = 6.0
SHADOW_BUTTON = 3.0


def rgba(hex_value, alpha=1.0):
    """'D71920' -> (r, g, b, a). Overlay drawing takes display (sRGB) values as they are."""
    h = hex_value.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)) + (alpha,)


class Palette:
    """The current theme's colours as RGBA tuples: palette.ink, palette.hot ..."""

    def __init__(self, name):
        self.name = name if name in TOKENS else "GRAPHITE"
        for key, value in TOKENS[self.name].items():
            setattr(self, key, rgba(value))

    def with_alpha(self, colour, alpha):
        return colour[:3] + (alpha,)


def current():
    from . import prefs
    p = None
    try:
        p = prefs.addon_prefs()
    except Exception:
        pass
    return Palette(p.theme if p else "GRAPHITE")


# ----------------------------------------------------------------------
# Reduce motion
# ----------------------------------------------------------------------
_system_cache = {"value": None}


def system_reduce_motion():
    """The operating system's "reduce motion" / "show animations" setting (best effort)."""
    if _system_cache["value"] is not None:
        return _system_cache["value"]
    value = False
    try:
        if sys.platform == "win32":
            import ctypes
            SPI_GETCLIENTAREAANIMATION = 0x1042
            enabled = ctypes.c_bool(True)
            if ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0,
                                                          ctypes.byref(enabled), 0):
                value = not enabled.value
        elif sys.platform == "darwin":
            import subprocess
            out = subprocess.run(["defaults", "read", "com.apple.universalaccess", "reduceMotion"],
                                 capture_output=True, text=True, timeout=2)
            value = out.stdout.strip() == "1"
        else:
            import subprocess
            out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"],
                                 capture_output=True, text=True, timeout=2)
            value = out.stdout.strip() == "false"
    except Exception:
        value = False
    _system_cache["value"] = value
    return value


def reduce_motion():
    """True when nothing should move: the user's choice, or the system's when set to System."""
    from . import prefs
    try:
        p = prefs.addon_prefs()
    except Exception:
        p = None
    choice = p.reduce_motion if p else "SYSTEM"
    if choice == "REDUCE":
        return True
    if choice == "FULL":
        return False
    return system_reduce_motion()
