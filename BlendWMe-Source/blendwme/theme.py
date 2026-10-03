"""The five themes and "reduce motion".

Four themes are drawn by BlendWMe in the viewport (docs/design/UI_SPEC.md, section 2):
Graphite Paper, Red on Paper, Blender Grey and 2000s (made to sit with Blender's
"Theme 2000": Luna blue, a navy hard shadow and orange for "your turn"). The fifth,
"Blender UI", draws nothing of its own: BlendWMe lives in the sidebar with Blender's
own widgets, like other extensions, and the viewport pointers take Blender's colours.

Colour rule: `hot` means YOU (do this now, look here). `ink` means the AI is
working. The AI's own indicators are never hot.
"""

import sys

# The exact tokens, as hex.
#   band / band_ink  an optional title band behind the header (the 2000s window bar)
#   tag / tag_ink    the tilted "WMe" sticker in the logo (default: accent / on_accent)
TOKENS = {
    "GRAPHITE": {"bg": "FFFFFF", "panel": "F1F1EF", "card": "FFFFFF", "ink": "222222", "accent": "222222",
                 "on_accent": "FFFFFF", "shadow": "9A9A9A", "hot": "D71920", "muted": "5E5E5E"},
    "RED": {"bg": "FFFFFF", "panel": "FFFFFF", "card": "FBE8EC", "ink": "141414", "accent": "D71920",
            "on_accent": "FFFFFF", "shadow": "141414", "hot": "D71920", "muted": "5B4A4E"},
    "BLENDER": {"bg": "232323", "panel": "3A3A3A", "card": "2B2B2B", "ink": "EDEDED", "accent": "EDEDED",
                "on_accent": "232323", "shadow": "111111", "hot": "F5A623", "muted": "B8B8B8"},
    # From Theme 2000 (Blender extension "theme_2000"): panel CBD8EE, title blue 5070D3 / 2D4395,
    # editor border 1A3389, orange F3963D for what is active.
    "Y2K": {"bg": "CBD8EE", "panel": "DCE6F7", "card": "FFFFFF", "ink": "1C2C66", "accent": "2D4395",
            "on_accent": "FFFFFF", "shadow": "1A3389", "hot": "EE7A1A", "muted": "4A5C96",
            "band": "5070D3", "band_ink": "FFFFFF", "tag": "F3963D", "tag_ink": "FFFFFF"},
}
NAMES = {"GRAPHITE": "Graphite", "RED": "Red", "BLENDER": "Blender Grey", "Y2K": "2000s", "NATIVE": "Blender UI"}
DRAWN = ("GRAPHITE", "RED", "BLENDER", "Y2K")
# All drawn themes keep the same handwriting and text metrics.
FONT_ROLES = {}

# Shapes, in design pixels at 100 % scale. Rounded to whole pixels when drawn (rule R3).
BORDER_PANEL = 3.0
BORDER_CARD = 2.0
RADIUS = 10.0
SHADOW_PANEL = 6.0
SHADOW_BUTTON = 3.0


def rgba(hex_value, alpha=1.0):
    """'D71920' -> (r, g, b, a). Overlay drawing takes display (sRGB) values as they are."""
    h = hex_value.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)) + (alpha,)


def _blender_tokens():
    """The "Blender UI" theme: the pointers and cards take the colours of Blender's own
    tooltips and buttons, so they look like part of whatever theme the user runs."""
    try:
        import bpy
        ui = bpy.context.preferences.themes[0].user_interface
        tip, reg = ui.wcol_tooltip, ui.wcol_regular

        def hexof(c):
            return "".join(f"{max(0, min(255, round(v * 255))):02X}" for v in c[:3])
        return {"bg": hexof(tip.inner), "panel": hexof(tip.inner), "card": hexof(tip.inner),
                "ink": hexof(tip.text), "accent": hexof(reg.inner_sel), "on_accent": hexof(reg.text_sel),
                "shadow": "000000", "hot": "E2322F", "muted": hexof(tip.text)}
    except Exception:
        return {"bg": "1D1D1D", "panel": "1D1D1D", "card": "1D1D1D", "ink": "D9D9D9", "accent": "4772B3",
                "on_accent": "FFFFFF", "shadow": "000000", "hot": "E2322F", "muted": "A6A6A6"}


class Palette:
    """The current theme's colours as RGBA tuples: palette.ink, palette.hot ..."""

    def __init__(self, name):
        self.name = name if (name in TOKENS or name == "NATIVE") else "GRAPHITE"
        tokens = _blender_tokens() if self.name in {"NATIVE", "BLENDER"} else TOKENS[self.name]
        for key, value in tokens.items():
            setattr(self, key, rgba(value))
        self.band = rgba(tokens["band"]) if "band" in tokens else None
        self.band_ink = rgba(tokens.get("band_ink", tokens["ink"]))
        self.tag = rgba(tokens.get("tag", tokens["accent"]))
        self.tag_ink = rgba(tokens.get("tag_ink", tokens["on_accent"]))
        self.roles = FONT_ROLES.get(self.name, {})

    def with_alpha(self, colour, alpha):
        return colour[:3] + (alpha,)


def current_name():
    from . import prefs
    p = None
    try:
        p = prefs.addon_prefs()
    except Exception:
        pass
    return p.theme if p else "GRAPHITE"


def current():
    return Palette(current_name())


def native():
    """True when BlendWMe uses Blender's own interface (the sidebar) instead of its panel."""
    return current_name() == "NATIVE"


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
