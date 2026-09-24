"""Settings: connection and models (saved in Blender preferences) and
per-session options (mode, level, prompt, references)."""

import bpy

from . import credentials, llm
from .state import session

# Model choices. NVIDIA Nemotron is the default so the build meets the
# hackathon rule (at least one NVIDIA open model on Nebius).
AGENT_PRESETS = [
    ("SUPER", "Nemotron 3 Super", "nvidia/nemotron-3-super-120b-a12b"),
    ("ULTRA", "Nemotron 3 Ultra", "nvidia/Nemotron-3-Ultra-550b-a55b"),
    ("KIMI", "Kimi K2.6", "moonshotai/Kimi-K2.6"),
    ("CUSTOM", "Custom model ID", ""),
]
VISION_CANDIDATES = [
    "nvidia/Nemotron-3-Nano-Omni",
    "MiniMaxAI/MiniMax-M3",
    "Qwen/Qwen2.5-VL-72B-Instruct",
    "google/gemma-3-27b-it",
]


def addon_prefs(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


class BWM_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    # The key itself is NEVER stored here. Only where to keep it.
    key_storage: bpy.props.EnumProperty(
        name="Keep my key in",
        items=[("KEYCHAIN", "Password store (recommended)",
                "Your system's encrypted password store, e.g. Windows Credential Manager. Survives restarts"),
               ("SESSION", "This session only", "Memory only. Paste it again after restarting Blender"),
               ("ENV", "Environment variable", "Read NEBIUS_API_KEY from your system environment")],
        default="KEYCHAIN")
    base_url: bpy.props.StringProperty(name="Endpoint", default=llm.DEFAULT_BASE_URL)
    agent_preset: bpy.props.EnumProperty(
        name="Agent model",
        items=[(k, label, mid or "Type any model ID from your Nebius catalogue") for k, label, mid in AGENT_PRESETS],
        default="SUPER")
    custom_model: bpy.props.StringProperty(name="Custom model ID")
    vision_enabled: bpy.props.BoolProperty(
        name="Vision (the AI can see)", default=True,
        description="Let BlendWMe look at the viewport with a vision model")
    vision_model: bpy.props.StringProperty(name="Vision model", default=VISION_CANDIDATES[0])
    reasoning: bpy.props.EnumProperty(
        name="Nemotron reasoning",
        items=[("LOW", "Low effort", "Think briefly. Good balance of speed and quality"),
               ("ON", "Full", "Think fully. Slower, sometimes better plans"),
               ("OFF", "Off", "No thinking. Fastest"),
               ("DEFAULT", "Provider default", "Send no reasoning settings")],
        default="LOW")
    temperature: bpy.props.FloatProperty(name="Temperature", default=0.4, min=0.0, max=1.5)
    max_tokens: bpy.props.IntProperty(name="Max output tokens", default=8192, min=1024, max=32768)
    max_steps: bpy.props.IntProperty(name="Steps before check-in", default=60, min=5, max=400,
                                     description="BlendWMe pauses after this many tool calls so costs never run away")

    def model_id(self):
        if self.agent_preset == "CUSTOM":
            return self.custom_model.strip()
        return next(mid for k, _, mid in AGENT_PRESETS if k == self.agent_preset)

    def key(self):
        return credentials.get_key(self.key_storage)

    def draw(self, context):
        draw_connection(self.layout, self)


def draw_connection(layout, p):
    col = layout.column(align=False)
    col.prop(p, "key_storage")
    key = p.key()
    if p.key_storage == "ENV":
        col.label(text=("NEBIUS_API_KEY found " + credentials.masked(key)) if key
                  else "NEBIUS_API_KEY is not set. Set it, then restart Blender.",
                  icon="CHECKMARK" if key else "ERROR")
    else:
        if p.key_storage == "KEYCHAIN" and not credentials.keychain_name():
            col.label(text="No password store on this system. Use 'This session only'.", icon="ERROR")
        row = col.row(align=True)
        row.label(text="Key: " + credentials.masked(key), icon="LOCKED" if key else "UNLOCKED")
        row.operator("blendwme.paste_key", text="Paste Key", icon="PASTEDOWN")
        if key:
            row.operator("blendwme.forget_key", text="", icon="TRASH")
        if not key:
            col.label(text="Copy your Nebius key, then press Paste Key.", icon="INFO")
    col.prop(p, "agent_preset")
    if p.agent_preset == "CUSTOM":
        col.prop(p, "custom_model")
    else:
        col.label(text=p.model_id(), icon="BLANK1")
    col.prop(p, "reasoning")
    col.separator()
    col.prop(p, "vision_enabled")
    sub = col.column()
    sub.enabled = p.vision_enabled
    sub.prop(p, "vision_model")
    col.separator()
    row = col.row(align=True)
    row.operator("blendwme.test_connection", icon="LINKED")
    row.operator("blendwme.test_vision", icon="HIDE_OFF")
    box = col.box()
    box.use_property_split = True
    box.prop(p, "base_url")
    box.prop(p, "max_steps")
    box.prop(p, "max_tokens")
    box.prop(p, "temperature")


# ----------------------------------------------------------------------
# Session options (not saved into your .blend)
# ----------------------------------------------------------------------

def _sync(self, context):
    session.mode = self.mode
    session.level = self.level
    session.watch_auto = self.watch_auto


class BWM_Session(bpy.types.PropertyGroup):
    prompt: bpy.props.StringProperty(name="", description="Tell BlendWMe what to make, or ask it something")
    mode: bpy.props.EnumProperty(
        name="Mode",
        items=[("AUTO", "Auto", "BlendWMe builds at full speed. Pause any time to take over"),
               ("WATCH", "Watch", "BlendWMe builds step by step like a person would, and explains each step"),
               ("TEACH", "Teach", "You do the work. BlendWMe guides, points and checks")],
        default="AUTO", update=_sync)
    level: bpy.props.EnumProperty(
        name="Level",
        items=[("BEGINNER", "Beginner", ""), ("INTERMEDIATE", "Intermediate", ""), ("EXPERT", "Expert", "")],
        default="BEGINNER", update=_sync)
    watch_auto: bpy.props.FloatProperty(
        name="Auto-advance", default=0.0, min=0.0, max=30.0, subtype="TIME_ABSOLUTE", unit="TIME_ABSOLUTE",
        description="Watch mode: seconds before the next step starts by itself. 0 waits for Next",
        update=_sync)
    reference_1: bpy.props.StringProperty(name="Reference 1", subtype="FILE_PATH")
    reference_2: bpy.props.StringProperty(name="Reference 2", subtype="FILE_PATH")
    show_log: bpy.props.BoolProperty(default=True)


def gather_config(context):
    p = addon_prefs(context)
    s = context.window_manager.blendwme
    _sync(s, context)
    refs = [bpy.path.abspath(r) for r in (s.reference_1, s.reference_2) if r.strip()]
    return {
        "api_key": p.key(),
        "base_url": p.base_url,
        "model": p.model_id(),
        "vision_model": p.vision_model.strip() if p.vision_enabled else "",
        "reasoning": p.reasoning,
        "temperature": p.temperature,
        "max_tokens": p.max_tokens,
        "max_steps": p.max_steps,
        "references": refs,
        "blender_version": bpy.app.version_string,
    }


classes = (BWM_Preferences, BWM_Session)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.blendwme = bpy.props.PointerProperty(type=BWM_Session)


def unregister():
    del bpy.types.WindowManager.blendwme
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
