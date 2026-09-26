"""Settings (saved in Blender's preferences) and per-session options (mode, prompt, references).

2.1 keeps settings to the few that matter. They all live on one screen, the
panel's "Brains and settings" screen:

- the theme (Graphite Paper, Red on Paper, Blender Grey)
- the relay options (offer a hand-over at a limit, hand over by itself in Auto,
  the returning brain reviews the other's work first)
- the Auto pace and whether pointers show in the viewport

Everything else sits behind a closed "Advanced" section. BlendWMe never asks
for an API key: the brains sign in inside the official Claude Code and Codex
programs, and BlendWMe never sees those logins.
"""

import bpy

from .state import session

THEMES = [("GRAPHITE", "Graphite", "Graphite Paper: pencil on paper. Red only when you need to look"),
          ("RED", "Red", "Red on Paper: bold and warm"),
          ("BLENDER", "Blender", "Blender Grey: sits quietly in Blender's dark interface, orange indicators")]


def addon_prefs(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def _redraw(self, context):
    session.changed_flag = True


class BWM_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    # -- the settings screen -------------------------------------------------
    theme: bpy.props.EnumProperty(name="Theme", items=THEMES, default="GRAPHITE", update=_redraw)
    offer_handover: bpy.props.BoolProperty(
        name="Offer a hand-over when a limit is reached", default=True,
        description="When Claude or Codex reaches its usage limit, show a card that offers the other one")
    auto_relay: bpy.props.BoolProperty(
        name="Hand over by itself in Auto", default=True,
        description="In Auto, when one brain reaches its usage limit, the other takes over without asking. "
                    "In Together and Teach you are always asked")
    returning_reviews: bpy.props.BoolProperty(
        name="Returning brain reviews the other's work first", default=True,
        description="A brain that takes the work back first looks at what the other brain changed and fixes "
                    "its open problems, before anything new")
    pace: bpy.props.FloatProperty(
        name="Pace", default=1.0, min=0.0, max=10.0, precision=1, step=50,
        description="Auto: seconds each new part stays in focus before the next one starts. 0 is fastest",
        update=_redraw)
    show_pointers: bpy.props.BoolProperty(
        name="Show pointers in the viewport", default=True,
        description="Radar rings, pencil circles and notes on the viewport and the editors", update=_redraw)

    # -- Advanced (closed by default) -----------------------------------------
    show_advanced: bpy.props.BoolProperty(name="Advanced", default=False)
    level: bpy.props.EnumProperty(
        name="Your level",
        items=[("BEGINNER", "Beginner", "Explain the idea and give exact menus, shortcuts and values"),
               ("INTERMEDIATE", "Intermediate", "Short technique cues"),
               ("EXPERT", "Expert", "Be direct. Advanced techniques")],
        default="BEGINNER")
    reduce_motion: bpy.props.EnumProperty(
        name="Motion",
        items=[("SYSTEM", "Follow the system", "Use your computer's 'reduce motion' setting"),
               ("REDUCE", "Reduce motion", "No blinking or moving indicators: show their still state"),
               ("FULL", "Full motion", "Always animate the indicators")],
        default="SYSTEM", update=_redraw)
    ask_handoff_note: bpy.props.BoolProperty(
        name="Ask for a handoff note when handing over", default=True,
        description="Before a switch, the current brain writes a short note (uses a little of its usage). "
                    "The journal is complete without it")
    claude_path: bpy.props.StringProperty(
        name="Claude Code program", subtype="FILE_PATH",
        description="Path to claude(.exe). Empty: found automatically")
    claude_model: bpy.props.StringProperty(
        name="Claude model", default="",
        description="Optional model alias (e.g. 'sonnet' or 'opus'). Empty: your Claude Code default")
    claude_effort: bpy.props.EnumProperty(
        name="Claude effort",
        items=[("", "Default", "Claude Code's own setting"), ("low", "Low", ""), ("medium", "Medium", ""),
               ("high", "High", ""), ("xhigh", "Extra high", ""), ("max", "Max", "")],
        default="")
    codex_path: bpy.props.StringProperty(
        name="Codex program", subtype="FILE_PATH",
        description="Path to codex(.cmd/.exe). Empty: found automatically")
    codex_model: bpy.props.StringProperty(
        name="Codex model", default="", description="Optional model name. Empty: your Codex default")
    workspace_folder: bpy.props.StringProperty(
        name="Workspace folder", subtype="DIR_PATH",
        description="Where the brains run and read BlendWMe's instructions. Empty: Documents/BlendWMe Workspace")
    mcp_python: bpy.props.StringProperty(
        name="Python for the tools server", subtype="FILE_PATH",
        description="A Python with the 'mcp' package. Empty: the one 'Install' made, or a Python on PATH")
    log_folder: bpy.props.StringProperty(
        name="Log folder", subtype="DIR_PATH",
        description="Where conversation and crash logs go. Empty: Documents/BlendWMe Logs")
    auto_save_logs: bpy.props.BoolProperty(
        name="Save a log after every task", default=True,
        description="Save the whole conversation (every message, all code and results) to the log folder")
    # One-time note after the first enable (see register_note in __init__.py)
    prefs_note_done: bpy.props.BoolProperty(default=False, options={"HIDDEN"})

    def draw(self, context):
        draw_settings(self.layout, self, context)


def draw_settings(layout, p, context=None):
    """The same settings as the panel's settings screen, in Blender's own widgets."""
    col = layout.column()
    col.label(text="Theme")
    col.row().prop(p, "theme", expand=True)
    col.separator()
    col.label(text="Relay")
    col.prop(p, "offer_handover")
    col.prop(p, "auto_relay")
    col.prop(p, "returning_reviews")
    col.separator()
    col.prop(p, "pace", text="Pace (s a step)")
    col.prop(p, "show_pointers")
    col.separator()
    box = col.box()
    box.prop(p, "show_advanced", icon="TRIA_DOWN" if p.show_advanced else "TRIA_RIGHT", emboss=False)
    if p.show_advanced:
        draw_advanced(box, p)


def draw_advanced(layout, p):
    col = layout.column()
    col.prop(p, "level")
    col.prop(p, "reduce_motion")
    col.prop(p, "ask_handoff_note")
    col.separator()
    col.prop(p, "claude_path")
    row = col.row(align=True)
    row.prop(p, "claude_model")
    row.prop(p, "claude_effort", text="")
    col.prop(p, "codex_path")
    col.prop(p, "codex_model")
    col.separator()
    col.prop(p, "workspace_folder")
    col.prop(p, "mcp_python")
    col.prop(p, "log_folder")
    col.prop(p, "auto_save_logs")


# ----------------------------------------------------------------------
# Session options (not saved into your .blend)
# ----------------------------------------------------------------------

MODES = [("WATCH", "Together", "You and the AI work on the same scene. It builds step by step and you can "
                               "take over any time"),
         ("TEACH", "Teach", "You do the work. BlendWMe guides, points and checks"),
         ("AUTO", "Auto", "BlendWMe carries the brief through. Pause any time to take over")]


def _sync(self, context):
    session.mode = self.mode
    session.changed_flag = True


class BWM_Session(bpy.types.PropertyGroup):
    prompt: bpy.props.StringProperty(name="", description="Tell BlendWMe what to make, or ask it something")
    brain: bpy.props.EnumProperty(
        name="Brain",
        items=[("claude", "Claude", "Claude Code with your Claude plan"),
               ("codex", "Codex", "Codex CLI with your ChatGPT plan")],
        default="claude", update=_redraw)
    mode: bpy.props.EnumProperty(name="Mode", items=MODES, default="WATCH", update=_sync)
    reference_1: bpy.props.StringProperty(name="Reference 1", subtype="FILE_PATH")
    reference_2: bpy.props.StringProperty(name="Reference 2", subtype="FILE_PATH")


def sync_session(context=None):
    """Copy the live settings the brains' tools read into the shared session state."""
    context = context or bpy.context
    p = addon_prefs(context)
    s = context.window_manager.blendwme
    session.mode = s.mode
    if p is not None:
        session.level = p.level
        session.pace = p.pace
        session.show_pointers = p.show_pointers


def gather_config(context=None):
    context = context or bpy.context
    p = addon_prefs(context)
    s = context.window_manager.blendwme
    sync_session(context)
    refs = [bpy.path.abspath(r) for r in (s.reference_1, s.reference_2) if r.strip()]

    def path(value):
        return bpy.path.abspath(value) if value and value.strip() else ""

    if p is None:     # loaded as a plain module (tests): the defaults
        return {"brain": s.brain, "references": refs, "blender_version": bpy.app.version_string,
                "offer_handover": True, "auto_relay": True, "returning_reviews": True, "ask_handoff_note": True,
                "auto_save_logs": True, "log_folder": "", "claude_path": "", "claude_model": "",
                "claude_effort": "", "codex_path": "", "codex_model": ""}
    return {
        "brain": s.brain,
        "claude_path": path(p.claude_path),
        "claude_model": p.claude_model.strip(),
        "claude_effort": p.claude_effort,
        "codex_path": path(p.codex_path),
        "codex_model": p.codex_model.strip(),
        "offer_handover": p.offer_handover,
        "auto_relay": p.auto_relay,
        "returning_reviews": p.returning_reviews,
        "ask_handoff_note": p.ask_handoff_note,
        "references": refs,
        "auto_save_logs": p.auto_save_logs,
        "log_folder": path(p.log_folder),
        "blender_version": bpy.app.version_string,
    }


classes = (BWM_Preferences, BWM_Session)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.blendwme = bpy.props.PointerProperty(type=BWM_Session)


def unregister():
    if hasattr(bpy.types.WindowManager, "blendwme"):
        del bpy.types.WindowManager.blendwme
    for c in reversed(classes):
        if getattr(c, "is_registered", False):
            bpy.utils.unregister_class(c)
