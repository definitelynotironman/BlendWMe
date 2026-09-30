"""Settings (saved in Blender's preferences) and per-session options (mode, prompt, references).

The settings are drawn with Blender's own widgets everywhere (rule R7): in the
panel's Settings popover, in the sidebar with the "Blender UI" theme, and in
Preferences > Add-ons > BlendWMe. One function draws them all (draw_settings).

- the brain, the references and the theme (Graphite, Red, Blender Grey, 2000s, Blender UI)
- the panel's size, text size and icon size, and where it sits
- the relay options (offer a hand-over at a limit, hand over by itself in Auto,
  the returning brain reviews the other's work first), the Auto pace and the pointers

Everything else sits behind a closed "Advanced" section. BlendWMe never asks
for an API key: the brains sign in inside the official Claude Code and Codex
programs, and BlendWMe never sees those logins.
"""

import os

import bpy

from .state import session

THEMES = [("GRAPHITE", "Graphite", "Graphite Paper: pencil on paper. Red only when you need to look", 0),
          ("RED", "Red", "Red on Paper: bold and warm", 1),
          ("BLENDER", "Blender Grey", "Sits quietly in Blender's dark interface, orange indicators", 2),
          ("Y2K", "2000s", "Made for Blender's Theme 2000: Luna blue, a navy shadow, orange when it's your turn", 3),
          ("NATIVE", "Blender UI", "No drawn panel: BlendWMe lives in the sidebar (N) with Blender's own buttons, "
                                   "like other extensions", 4)]
CORNERS = [("TR", "Top right", ""), ("TL", "Top left", ""), ("BR", "Bottom right", ""), ("BL", "Bottom left", "")]


def addon_prefs(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def log_folder(context=None):
    """The one folder for every BlendWMe log, crash trace and crash report.

    BLENDWME_LOG_FOLDER overrides it (the tests use this, so they never write
    into your real logs folder).
    """
    from . import logs
    if os.environ.get("BLENDWME_LOG_FOLDER"):
        return os.environ["BLENDWME_LOG_FOLDER"]
    try:
        p = addon_prefs(context)
    except AttributeError:
        p = None
    if p and p.log_folder.strip():
        return bpy.path.abspath(p.log_folder.strip())
    return logs.default_folder()


def _redraw(self, context):
    session.changed_flag = True


def _theme_changed(self, context):
    """Moving between the drawn panel and "Blender UI" moves BlendWMe with it."""
    session.changed_flag = True
    try:
        from . import ui
        ui.theme_switched(context)
    except Exception as e:
        print("BlendWMe: theme switch:", e)


class BWM_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    # -- the settings screen -------------------------------------------------
    theme: bpy.props.EnumProperty(name="Theme", items=THEMES, default="GRAPHITE", update=_theme_changed)
    offer_handover: bpy.props.BoolProperty(
        name="Offer a hand-over when a limit is reached", default=True,
        description="When Claude or Codex reaches its usage limit, show a card that offers the other one")
    auto_relay: bpy.props.BoolProperty(
        name="Hand over by itself in Auto", default=True,
        description="In Auto, when one brain reaches its usage limit, the other takes over without asking. "
                    "In Teach you are always asked")
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

    # -- the panel's size and place ---------------------------------------------
    panel_size: bpy.props.IntProperty(
        name="Panel size", default=75, min=50, max=150, subtype="PERCENTAGE", update=_redraw,
        description="The size of the whole BlendWMe panel: its width, writing, buttons and icons")
    text_size: bpy.props.IntProperty(
        name="Text size", default=100, min=70, max=150, subtype="PERCENTAGE", update=_redraw,
        description="The writing in the panel, on top of the panel size")
    icon_size: bpy.props.IntProperty(
        name="Icon size", default=100, min=70, max=160, subtype="PERCENTAGE", update=_redraw,
        description="The icons and marks in the panel, on top of the panel size")
    minimize_on_take_control: bpy.props.BoolProperty(
        name="Minimize when I take control", default=True, update=_redraw,
        description="Take control folds the panel into a small bar, so the viewport is yours. "
                    "The session keeps going; click the bar to open it again")
    avoid_gizmo: bpy.props.BoolProperty(
        name="Keep clear of the navigation gizmo", default=True, update=_redraw,
        description="The panel never covers the navigation gizmo in the top right corner. Turn this off "
                    "(or hide the gizmo) to let the panel sit right against the edge")
    # Where the panel sits (rule R9): the nearest corner of the free viewport and the
    # distance from it, in design units (pixels / scale), so it stays put when the
    # viewport, the sidebar or the scale changes. Set by dragging the header and the
    # corner grip; "Put the panel back" resets them.
    panel_corner: bpy.props.EnumProperty(name="Panel corner", items=CORNERS, default="TR", update=_redraw)
    panel_off_x: bpy.props.FloatProperty(default=0.0, min=0.0, options={"HIDDEN"})
    panel_off_y: bpy.props.FloatProperty(default=0.0, min=0.0, options={"HIDDEN"})
    panel_width: bpy.props.FloatProperty(default=0.0, min=0.0, options={"HIDDEN"})     # 0: the design width
    panel_height: bpy.props.FloatProperty(default=0.0, min=0.0, options={"HIDDEN"})    # 0: follows the content
    panel_max: bpy.props.BoolProperty(default=False, options={"HIDDEN"})               # full height

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


def _heading(layout, words, icon="NONE"):
    row = layout.row()
    row.label(text=words, icon=icon)
    return row


def draw_brains(layout, context):
    """Who works with you: Claude or Codex, and whether each is installed and signed in."""
    from . import setup_checks
    s = getattr(context.window_manager, "blendwme", None)
    col = layout.column()
    _heading(col, "Who works with you", "COMMUNITY")
    if s is not None:
        col.row(align=True).prop(s, "brain", expand=True)
    for brain, words in (("claude", "Claude Code"), ("codex", "Codex CLI")):
        level, message = setup_checks.results.get(brain, (None, ""))
        if level == setup_checks.OK:
            text, icon = f"{words}: installed and signed in", "CHECKMARK"
        elif level == setup_checks.RUNNING:
            text, icon = f"{words}: checking...", "SORTTIME"
        elif level is None:
            text, icon = f"{words}: not checked yet", "DOT"
        else:
            text, icon = message, "ERROR"
        wrapped(col, text, icon=icon, width=46)
    row = col.row(align=True)
    row.operator("blendwme.setup_check", text="Check again", icon="FILE_REFRESH").which = "ALL"
    level = setup_checks.results.get("mcp", (None, ""))[0]
    if level in (setup_checks.ERROR, setup_checks.WARN):
        row.operator("blendwme.setup_check", text="Install the tools server", icon="IMPORT").which = "install"


def draw_references(layout, context):
    s = getattr(context.window_manager, "blendwme", None)
    if s is None:
        return
    col = layout.column(align=True)
    _heading(col, "References for this task", "IMAGE_REFERENCE")
    col.prop(s, "reference_1", text="")
    col.prop(s, "reference_2", text="")


def draw_theme(layout, p):
    col = layout.column()
    _heading(col, "Theme", "COLOR")
    grid = col.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
    for key, name, _, _ in THEMES:
        grid.prop_enum(p, "theme", key, text=name)


def draw_panel_options(layout, p):
    col = layout.column()
    _heading(col, "Panel", "WINDOW")
    if p.theme == "NATIVE":
        wrapped(col, "Blender UI: BlendWMe is here in the sidebar. Pick a drawn theme for the floating panel.",
                width=46)
        return
    sub = col.column(align=True)
    sub.prop(p, "panel_size")
    sub.prop(p, "text_size")
    sub.prop(p, "icon_size")
    col.prop(p, "minimize_on_take_control")
    col.prop(p, "avoid_gizmo")
    col.operator("blendwme.panel_reset", text="Put the panel back on the right", icon="LOOP_BACK")


def draw_relay(layout, p):
    col = layout.column()
    _heading(col, "Relay, pace and pointers", "UV_SYNC_SELECT")
    col.prop(p, "offer_handover")
    col.prop(p, "auto_relay")
    col.prop(p, "returning_reviews")
    col.prop(p, "pace", text="Auto pace (s a step)")
    col.prop(p, "show_pointers")


def wrapped(layout, text, icon="NONE", width=46):
    """A few labels that wrap long text (Blender's labels do not wrap by themselves)."""
    import textwrap
    col = layout.column(align=True)
    for i, line in enumerate(textwrap.wrap(text, width) or [""]):
        col.label(text=line, icon=icon if i == 0 else "BLANK1" if icon != "NONE" else "NONE")
    return col


def draw_settings(layout, p, context=None):
    """Every setting, in Blender's own widgets: the Settings popover, the sidebar and the
    add-on preferences all draw this (rule R7: native where Blender is best)."""
    context = context or bpy.context
    col = layout.column()
    draw_brains(col.box(), context)
    draw_references(col.box(), context)
    draw_theme(col.box(), p)
    draw_panel_options(col.box(), p)
    draw_relay(col.box(), p)
    box = col.box()
    box.prop(p, "show_advanced", icon="TRIA_DOWN" if p.show_advanced else "TRIA_RIGHT", emboss=False)
    if p.show_advanced:
        draw_advanced(box, p)
        draw_tools(box)


def draw_tools(layout):
    col = layout.column(align=True)
    col.label(text="Tools")
    col.operator("blendwme.notebook", icon="TEXT")
    col.operator("blendwme.show_capture", icon="IMAGE_DATA")
    col.operator("blendwme.open_logs", icon="FILE_FOLDER")
    col.operator("blendwme.save_log", icon="FILE_TICK")
    col.operator("blendwme.copy_log", icon="COPYDOWN")
    col.operator("blendwme.crash_report", icon="PACKAGE")
    col.operator("blendwme.setup_check", text="Install the Tools Server", icon="IMPORT").which = "install"
    col.operator("blendwme.copy_terminal_command", icon="CONSOLE")


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

MODES = [("AUTO", "Auto", "Build together; pause, edit and resume any time", 2),
         ("TEACH", "Teach", "You do the work. BlendWMe guides, points and checks", 1)]


def _sync(self, context):
    if session.mode != self.mode:
        # Mode changes take effect between operations. Wake an obsolete lesson
        # wait so it cannot keep polling or authorize a later demonstration.
        session.set_card(None)
        session.set_pointers([])
        session.wake("mode_changed")
    session.mode = self.mode
    session.changed_flag = True


class BWM_Session(bpy.types.PropertyGroup):
    prompt: bpy.props.StringProperty(name="", description="Tell BlendWMe what to make, or ask it something")
    brain: bpy.props.EnumProperty(
        name="Brain",
        items=[("claude", "Claude", "Claude Code with your Claude plan"),
               ("codex", "Codex", "Codex CLI with your ChatGPT plan")],
        default="claude", update=_redraw)
    mode: bpy.props.EnumProperty(name="Mode", items=MODES, default="AUTO", update=_sync)
    reference_1: bpy.props.StringProperty(name="Reference 1", subtype="FILE_PATH")
    reference_2: bpy.props.StringProperty(name="Reference 2", subtype="FILE_PATH")


def sync_session(context=None):
    """Copy the live settings the brains' tools read into the shared session state."""
    context = context or bpy.context
    p = addon_prefs(context)
    s = context.window_manager.blendwme
    if s.mode not in {"AUTO", "TEACH"}:
        s.mode = "AUTO"       # legacy numeric WATCH (0), never reinterpret Teach (1)
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
