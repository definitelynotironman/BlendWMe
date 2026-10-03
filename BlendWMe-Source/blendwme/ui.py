"""The BlendWMe sidebar (3D Viewport > N > BlendWMe) and its buttons."""

import os
import tempfile
import textwrap

import bpy

from . import prefs, setup_checks, socket_bridge, workspace
from .relay import relay
from .tool_host import label as brain_label
from .state import (ASKING, DONE, ERROR, IDLE, PAUSED, STATUS_TEXT, THINKING,
                    WAIT_NEXT, WAIT_USER, WORKING, session)


# ----------------------------------------------------------------------
# Operators
# ----------------------------------------------------------------------

class BWM_OT_send(bpy.types.Operator):
    bl_idname = "blendwme.send"
    bl_label = "Send"
    bl_description = "Send your message. While BlendWMe works, this steers it or answers its question"

    new_task: bpy.props.BoolProperty(default=False)

    def execute(self, context):
        s = context.window_manager.blendwme
        text = s.prompt.strip()
        if not text:
            self.report({"WARNING"}, "Type what you want first.")
            return {"CANCELLED"}
        config = prefs.gather_config(context)
        if self.new_task and relay.is_running():
            relay.stop()
        if self.new_task:
            relay.reset()
        relay.send(text, config, new_task=self.new_task)
        s.prompt = ""
        return {"FINISHED"}


class BWM_OT_paste(bpy.types.Operator):
    bl_idname = "blendwme.paste_prompt"
    bl_label = "Paste"
    bl_description = "Paste a long prompt from the clipboard"

    def execute(self, context):
        context.window_manager.blendwme.prompt = context.window_manager.clipboard[:12000]
        return {"FINISHED"}


class BWM_OT_pause(bpy.types.Operator):
    bl_idname = "blendwme.pause"
    bl_label = "Pause and Take Control"
    bl_description = "BlendWMe stops after the current action. Edit freely, then Resume"

    def execute(self, context):
        session.pause_flag.set()
        session.add("info", "Pausing after the current action. The scene is yours.")
        session.wake()
        return {"FINISHED"}


class BWM_OT_resume(bpy.types.Operator):
    bl_idname = "blendwme.resume"
    bl_label = "Resume"
    bl_description = "Hand back to BlendWMe. It will look at what you changed and continue from your version"

    def execute(self, context):
        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception:
                pass
        if not relay.is_running() and relay.has_conversation():
            # Resume after an error, a limit or a finished turn: continue the same conversation.
            relay.send("Continue.", prefs.gather_config(context))
            return {"FINISHED"}
        session.pause_flag.clear()
        session.wake()
        return {"FINISHED"}


class BWM_OT_stop(bpy.types.Operator):
    bl_idname = "blendwme.stop"
    bl_label = "Stop"
    bl_description = "Stop the current task. Everything already built stays"

    def execute(self, context):
        relay.stop()
        return {"FINISHED"}


class BWM_OT_next(bpy.types.Operator):
    bl_idname = "blendwme.next"
    bl_label = "Next Step"
    bl_description = "Continue to the next step"

    def execute(self, context):
        session.wake()
        return {"FINISHED"}


class BWM_OT_teach(bpy.types.Operator):
    bl_idname = "blendwme.teach"
    bl_label = "Teach Answer"

    choice: bpy.props.EnumProperty(items=[("done", "I did it", ""), ("show_me", "Do it for me", ""),
                                          ("skip", "Skip this step", ""), ("hint", "Hint", "")])

    @classmethod
    def description(cls, context, properties):
        return {"done": "Tell BlendWMe you finished this step",
                "show_me": "Let BlendWMe demonstrate this step for you",
                "skip": "Skip this step; its action remains unpractised", "hint": "Request a hint without marking the step done"}[properties.choice]

    def execute(self, context):
        session.wake(self.choice)
        return {"FINISHED"}


class BWM_OT_undo_step(bpy.types.Operator):
    bl_idname = "blendwme.undo_step"
    bl_label = "Undo AI Step"
    bl_description = "Undo the last BlendWMe step (same as Ctrl+Z). BlendWMe notices the change"

    def execute(self, context):
        if relay.is_running() and not session.pause_flag.is_set():
            self.report({"WARNING"}, "Pause first, then undo.")
            return {"CANCELLED"}
        try:
            bpy.ops.ed.undo()
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


class BWM_OT_new(bpy.types.Operator):
    bl_idname = "blendwme.new_task"
    bl_label = "New Conversation"
    bl_description = "Forget this conversation and start fresh (your scene is not changed)"

    def execute(self, context):
        relay.reset()
        return {"FINISHED"}


class BWM_OT_notebook(bpy.types.Operator):
    bl_idname = "blendwme.notebook"
    bl_label = "Construction Notebook"
    bl_description = "Put every step BlendWMe ran (with its code) into a Text block you can read and learn from"

    def execute(self, context):
        text = bpy.data.texts.get("BlendWMe Notebook") or bpy.data.texts.new("BlendWMe Notebook")
        out = ["# BlendWMe construction notebook", "# Every step the AI ran, in order.", ""]
        for i, step in enumerate(session.steps, 1):
            out.append(f"# --- Step {i}: {step['title']} {'(failed)' if not step['ok'] else ''}")
            for line in textwrap.wrap(step["explain"], 90):
                out.append("# " + line)
            out.append(step["code"])
            out.append("")
        text.from_string("\n".join(out))
        for area in context.screen.areas:
            if area.type == "TEXT_EDITOR":
                area.spaces.active.text = text
                break
        else:
            self.report({"INFO"}, "Open a Text Editor and choose 'BlendWMe Notebook'.")
        return {"FINISHED"}


class BWM_OT_copy_log(bpy.types.Operator):
    bl_idname = "blendwme.copy_log"
    bl_label = "Copy Log"
    bl_description = "Copy the conversation and activity for debugging"

    def execute(self, context):
        lines = [f"BlendWMe | brain {brain_label(relay.active)} | steps {len(session.steps)}"]
        for e in session.log:
            lines.append(f"[{e['who']}] {e['text']}" + (f" -- {e['detail']}" if e.get("detail") else ""))
        context.window_manager.clipboard = "\n".join(lines)
        self.report({"INFO"}, "Log copied.")
        return {"FINISHED"}


class BWM_OT_save_log(bpy.types.Operator):
    bl_idname = "blendwme.save_log"
    bl_label = "Save Full Log"
    bl_description = "Save the whole conversation (every message, all code, results and timings) as .md and .json files"

    def execute(self, context):
        if not relay.has_conversation():
            self.report({"WARNING"}, "Nothing to save yet.")
            return {"CANCELLED"}
        path = relay.save_log()
        if path:
            self.report({"INFO"}, f"Saved {path}")
            session.add("info", "Full log saved: " + path)
        return {"FINISHED"}


class BWM_OT_open_logs(bpy.types.Operator):
    bl_idname = "blendwme.open_logs"
    bl_label = "Open Logs Folder"
    bl_description = "Open the folder with the saved conversation logs"

    def execute(self, context):
        folder = prefs.log_folder(context)
        os.makedirs(folder, exist_ok=True)
        bpy.ops.wm.path_open(filepath=folder)
        return {"FINISHED"}


class BWM_OT_crash_report(bpy.types.Operator):
    bl_idname = "blendwme.crash_report"
    bl_label = "Make Crash Report"
    bl_description = ("Zip the latest BlendWMe logs with Blender's crash file into the logs folder, "
                      "ready to share. Check it first: it can contain file names and folders")

    def execute(self, context):
        from . import logs
        temp_dirs = [tempfile.gettempdir(), bpy.path.abspath(context.preferences.filepaths.temporary_directory)]
        try:
            path = logs.make_crash_report(prefs.log_folder(context), temp_dirs)
        except OSError as e:
            self.report({"ERROR"}, f"Could not make the crash report: {e}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Crash report saved: {os.path.basename(path)}")
        bpy.ops.wm.path_open(filepath=os.path.dirname(path))
        return {"FINISHED"}


class BWM_OT_save_prefs(bpy.types.Operator):
    bl_idname = "blendwme.save_prefs"
    bl_label = "Save Preferences"
    bl_description = "Save Blender's preferences now, so BlendWMe and its shortcut stay on after a restart or a crash"

    def execute(self, context):
        p = prefs.addon_prefs(context)
        if p:
            p.prefs_note_done = True
        bpy.ops.wm.save_userpref()
        self.report({"INFO"}, "Preferences saved. BlendWMe stays on.")
        return {"FINISHED"}


class BWM_OT_dismiss_prefs_note(bpy.types.Operator):
    bl_idname = "blendwme.dismiss_prefs_note"
    bl_label = "Not Now"
    bl_description = "Hide this note"

    def execute(self, context):
        p = prefs.addon_prefs(context)
        if p:
            p.prefs_note_done = True
        return {"FINISHED"}


def sidebar_region(area):
    return next((r for r in area.regions if r.type == "UI"), None)


def sidebar_shows_blendwme(area):
    """True when this 3D Viewport's sidebar is open on the BlendWMe tab ("Blender UI" theme)."""
    try:
        if not area.spaces.active.show_region_ui:
            return False
        region = sidebar_region(area)
        return region is not None and region.width > 1 and region.active_panel_category == "BlendWMe"
    except Exception:
        return False


def _pick_tab(area):
    region = sidebar_region(area)
    if region is None:
        return False
    try:
        region.active_panel_category = "BlendWMe"
        return region.active_panel_category == "BlendWMe"
    except Exception:
        return False


def show_sidebar(area, show=True):
    """Open the sidebar on the BlendWMe tab (or close it). The tab only exists after the
    sidebar has drawn once with the "Blender UI" theme, so it is picked again just after."""
    area.spaces.active.show_region_ui = show
    area.tag_redraw()
    if not show or _pick_tab(area):
        return
    pointer = area.as_pointer()
    tries = [0]

    def again():
        tries[0] += 1
        try:
            for window in bpy.context.window_manager.windows:
                for a in window.screen.areas:
                    if a.as_pointer() == pointer and _pick_tab(a):
                        a.tag_redraw()
                        return None
        except Exception:
            return None
        return 0.05 if tries[0] < 20 else None
    bpy.app.timers.register(again, first_interval=0.05)


def _view3d(context):
    if context.area and context.area.type == "VIEW_3D":
        return context.area
    if context.window is None:
        return None
    return next((a for a in context.window.screen.areas if a.type == "VIEW_3D"), None)


_theme_seen = {"last": None}


def theme_switched(context):
    """The theme moved between the drawn panel and "Blender UI": take BlendWMe along."""
    from . import panel, panel_ops, theme
    now = theme.current_name()
    before = _theme_seen["last"]
    _theme_seen["last"] = now
    if context is None or getattr(context, "window", None) is None:
        return
    if now == "NATIVE" and before != "NATIVE":
        area = None
        if panel.state.open:
            area = next((a for a in context.window.screen.areas if a.as_pointer() == panel.state.area_ptr), None)
            panel_ops.close_panel()
            area = area or _view3d(context)
            if area is not None:
                show_sidebar(area, True)
    elif before == "NATIVE" and now != "NATIVE":
        area = next((a for a in context.window.screen.areas if a.type == "VIEW_3D" and sidebar_shows_blendwme(a)),
                    None)
        if area is not None:
            show_sidebar(area, False)
            panel_ops.open_panel(context, area)


class BWM_OT_open(bpy.types.Operator):
    bl_idname = "blendwme.open"
    bl_label = "BlendWMe"
    bl_description = "Open BlendWMe in the 3D Viewport (Ctrl+Alt+M). Again: focus the input; again: minimize"

    close: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})

    def execute(self, context):
        from . import panel, panel_ops, theme
        if theme.native():
            # "Blender UI": BlendWMe is the sidebar's BlendWMe tab. This shows or hides it.
            area = _view3d(context)
            if area is None:
                self.report({"WARNING"}, "Open a 3D Viewport first: BlendWMe lives there.")
                return {"CANCELLED"}
            show_sidebar(area, not self.close and not sidebar_shows_blendwme(area))
            return {"FINISHED"}
        if self.close:
            panel_ops.close_panel()
            return {"FINISHED"}
        if panel.state.open and panel_ops.panel_alive() and panel.is_open_in(context.area):
            if panel.state.minimized:
                panel_ops.restore()           # minimized: open it again
            elif panel.state.focused:
                panel_ops.minimize()          # open and typing: fold it away
            else:
                panel.state.focused = True    # open here: focus the input
                panel_ops._redraw()
            return {"FINISHED"}
        if not panel_ops.open_panel(context):
            self.report({"WARNING"}, "Open a 3D Viewport first: BlendWMe lives there.")
            return {"CANCELLED"}
        return {"FINISHED"}


class BWM_OT_export_journal(bpy.types.Operator):
    bl_idname = "blendwme.export_journal"
    bl_label = "Export Journal"
    bl_description = "Save the baton journal and the handoff brief as a text file in the logs folder"

    def execute(self, context):
        import json
        import time
        from . import journal, scene_info
        folder = prefs.log_folder(context)
        os.makedirs(folder, exist_ok=True)
        stem = os.path.splitext(os.path.basename(bpy.data.filepath))[0] if bpy.data.filepath else "unsaved"
        path = os.path.join(folder, time.strftime(f"journal_{stem}_%Y-%m-%d_%H%M%S.md"))
        brief = journal.journal.handoff_brief(relay.other(relay.active), scene_info.makers())
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# BlendWMe journal: {stem}\n\n## Handoff brief\n\n```\n{brief}\n```\n\n## Full journal\n\n"
                    f"```json\n{json.dumps(journal.journal.data, indent=1, ensure_ascii=False)}\n```\n")
        self.report({"INFO"}, f"Journal saved: {os.path.basename(path)}")
        bpy.ops.wm.path_open(filepath=folder)
        return {"FINISHED"}


class BWM_OT_show_capture(bpy.types.Operator):
    bl_idname = "blendwme.show_capture"
    bl_label = "What the AI Saw"
    bl_description = "Load the last picture BlendWMe looked at as an image (open it in an Image Editor)"

    def execute(self, context):
        path = session.last_capture
        if not path:
            self.report({"WARNING"}, "BlendWMe has not looked yet.")
            return {"CANCELLED"}
        img = bpy.data.images.get("BlendWMe View")
        if img:
            bpy.data.images.remove(img)
        img = bpy.data.images.load(path)
        img.name = "BlendWMe View"
        for area in context.screen.areas:
            if area.type == "IMAGE_EDITOR":
                area.spaces.active.image = img
                break
        else:
            self.report({"INFO"}, "Loaded as 'BlendWMe View'. Open it in an Image Editor.")
        return {"FINISHED"}


class BWM_OT_hand_over(bpy.types.Operator):
    bl_idname = "blendwme.hand_over"
    bl_label = "Hand Over"
    bl_description = ("Hand the work to the other brain. The current brain writes a handoff note (if it can), "
                      "and the other one continues from the project journal and a fresh look at the scene")

    target: bpy.props.StringProperty(default="codex")

    def execute(self, context):
        s = context.window_manager.blendwme
        if not relay.has_conversation():
            s.brain = self.target
            self.report({"INFO"}, f"{brain_label(self.target)} will answer your next message.")
            return {"FINISHED"}
        s.brain = self.target
        relay.hand_over(self.target, prefs.gather_config(context))
        return {"FINISHED"}


class BWM_OT_journal(bpy.types.Operator):
    bl_idname = "blendwme.open_journal"
    bl_label = "Project Journal"
    bl_description = "Show the project journal (the baton the brains pass to each other) in a Text Editor"

    def execute(self, context):
        from . import journal
        journal.journal.dirty = True
        journal.save()
        text = bpy.data.texts.get(journal.TEXT_NAME)
        brief = bpy.data.texts.get("BlendWMe Handoff Brief") or bpy.data.texts.new("BlendWMe Handoff Brief")
        from . import scene_info
        brief.from_string(journal.journal.handoff_brief(relay.other(relay.active), scene_info.makers()))
        for area in context.screen.areas:
            if area.type == "TEXT_EDITOR":
                area.spaces.active.text = brief or text
                break
        else:
            self.report({"INFO"}, "Open a Text Editor and choose 'BlendWMe Handoff Brief' or 'BlendWMe Journal'.")
        return {"FINISHED"}


class BWM_OT_setup_check(bpy.types.Operator):
    bl_idname = "blendwme.setup_check"
    bl_label = "Setup Check"
    bl_description = ("Check that Claude Code and Codex are installed and signed in, and that the MCP server "
                      "starts. Uses each program's own status command; your sign-in is never read")

    which: bpy.props.EnumProperty(items=[("ALL", "All", ""), ("claude", "Claude", ""), ("codex", "Codex", ""),
                                         ("mcp", "MCP server", ""), ("install", "Install MCP server", "")])

    def execute(self, context):
        names = ["claude", "codex", "mcp"] if self.which == "ALL" else [self.which]
        if self.which == "install":
            names = ["install", "mcp"]
        setup_checks.run_in_background(names, prefs.gather_config(context))
        return {"FINISHED"}


class BWM_OT_bridge(bpy.types.Operator):
    bl_idname = "blendwme.bridge"
    bl_label = "MCP Bridge"
    bl_description = "Start or stop BlendWMe's local socket for the brains (127.0.0.1 only)"

    action: bpy.props.EnumProperty(items=[("START", "Start", ""), ("STOP", "Stop", "")])

    def execute(self, context):
        if self.action == "START":
            message = socket_bridge.start()
        else:
            socket_bridge.stop()
            message = "The MCP bridge is stopped. The brains cannot use BlendWMe's tools until it starts again."
        session.add("info", message)
        return {"FINISHED"}


class BWM_OT_copy_terminal(bpy.types.Operator):
    bl_idname = "blendwme.copy_terminal_command"
    bl_label = "Copy Terminal Command"
    bl_description = ("Copy a command that starts Claude Code in a terminal with BlendWMe's tools, "
                      "to try the tools outside the panel")

    def execute(self, context):
        socket_bridge.start()
        config = workspace.write_mcp_config("claude")
        folder = workspace.folder()
        # Two lines, so it works in both cmd and PowerShell (Windows PowerShell 5 has no "&&").
        context.window_manager.clipboard = (f'cd "{folder}"\nclaude --mcp-config "{config}" '
                                            '--allowedTools mcp__blendwme\n')
        self.report({"INFO"}, "Copied. Paste it in a terminal, then ask Claude to build something.")
        return {"FINISHED"}


class BWM_OT_example(bpy.types.Operator):
    bl_idname = "blendwme.example"
    bl_label = "Example"
    bl_description = "Put this example in the message box"
    bl_options = {"INTERNAL"}

    name: bpy.props.StringProperty()

    def execute(self, context):
        from .panel import EXAMPLE_PROMPTS
        context.window_manager.blendwme.prompt = EXAMPLE_PROMPTS.get(self.name, self.name)
        return {"FINISHED"}


class BWM_OT_read_more(bpy.types.Operator):
    bl_idname = "blendwme.read_more"
    bl_label = "Read More"
    bl_description = "Show the whole message (or fold it again)"
    bl_options = {"INTERNAL"}

    entry: bpy.props.IntProperty()

    def execute(self, context):
        from . import panel
        panel.state.expanded ^= {self.entry}
        return {"FINISHED"}


class BWM_OT_earlier(bpy.types.Operator):
    bl_idname = "blendwme.earlier"
    bl_label = "Show Earlier Messages"
    bl_description = "Show more of the conversation"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        _native["shown"] += 20
        return {"FINISHED"}


class BWM_OT_close_card(bpy.types.Operator):
    bl_idname = "blendwme.close_card"
    bl_label = "Close"
    bl_description = "Close this card"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        from . import panel_ops
        panel_ops.close_top_card()
        return {"FINISHED"}


class BWM_OT_take_control(bpy.types.Operator):
    bl_idname = "blendwme.take_control"
    bl_label = "Take Control"
    bl_description = "BlendWMe pauses after the current action and the scene is yours. Resume when you are done"

    def execute(self, context):
        from . import panel_ops, theme
        if theme.native():
            bpy.ops.blendwme.pause()
        else:
            panel_ops.take_control(context)
        return {"FINISHED"}


class BWM_OT_pointers_again(bpy.types.Operator):
    bl_idname = "blendwme.pointers_again"
    bl_label = "Move"
    bl_description = "Play the pointer again so your eye finds it"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        from . import overlay
        overlay.restart_pointers()
        return {"FINISHED"}


class BWM_OT_settings(bpy.types.Operator):
    bl_idname = "blendwme.settings"
    bl_label = "BlendWMe Settings"
    bl_description = "Brains, references, theme, panel size and relay options"

    def execute(self, context):
        from . import panel_ops
        if not setup_checks.results:
            bpy.ops.blendwme.setup_check(which="ALL")
        panel_ops.open_settings(context)
        return {"FINISHED"}


# ----------------------------------------------------------------------
# Panels: the "Blender UI" theme (the sidebar), the Settings popover and the
# BlendWMe button in the 3D Viewport's header
# ----------------------------------------------------------------------

class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlendWMe"

    @classmethod
    def poll(cls, context):
        # One BlendWMe at a time: the sidebar tab exists only with the "Blender UI" theme.
        from . import theme
        return theme.native()


_STATUS_ICON = {IDLE: "CHECKBOX_DEHLT", THINKING: "SORTTIME", WORKING: "MODIFIER",
                WAIT_NEXT: "PLAY", WAIT_USER: "HAND", ASKING: "QUESTION", PAUSED: "PAUSE",
                DONE: "CHECKMARK", ERROR: "ERROR"}
_native = {"shown": 20}          # how many conversation entries the sidebar shows


def _wrap_width(context):
    """Characters per line for the current region's width (labels do not wrap by themselves)."""
    region = context.region
    width = region.width if region else 300
    return max(20, int(width / (7.0 * context.preferences.system.ui_scale)))


def _wrapped(layout, text, width, icon="NONE", limit=None):
    """Labels that wrap. Returns (column, True when lines were left out)."""
    col = layout.column(align=True)
    lines = []
    for para in str(text).split("\n"):
        lines += textwrap.wrap(para, width) or [""]
    shown = lines if limit is None else lines[:limit]
    if limit is not None and len(lines) > limit and shown:
        shown[-1] = shown[-1].rstrip(" .,;:") + "…"
    for i, line in enumerate(shown):
        col.label(text=line, icon=icon if i == 0 else ("BLANK1" if icon != "NONE" else "NONE"))
    return col, len(lines) > len(shown)


def _draw_safety_notes(layout, p, width=44):
    """Parts that failed to start, and the one-time 'Save Preferences' note."""
    from . import lifecycle
    if lifecycle.problems:
        box = layout.box()
        box.alert = True
        box.label(text="Part of BlendWMe did not start:", icon="ERROR")
        for part, message in lifecycle.problems[:4]:
            _wrapped(box, f"{part}: {message}", width)
        box.operator("blendwme.crash_report", icon="PACKAGE")
    if lifecycle.needs_prefs_note(p):
        box = layout.box()
        _wrapped(box, "Save Preferences so BlendWMe stays on after restarts.", width)
        row = box.row(align=True)
        row.operator("blendwme.save_prefs", icon="FILE_TICK")
        row.operator("blendwme.dismiss_prefs_note")


class _Words:
    """Just enough of a panel Painter to share its words (status, card labels) with the sidebar."""
    relay = relay


def _status_words():
    from .panel import Painter
    try:
        return Painter.status_words(_Words())[0]
    except Exception:
        return STATUS_TEXT.get(session.status, session.status)


def _card_words(card):
    kind = card.get("kind")
    body = card.get("text") or ""
    title = card.get("title") or ""
    if kind in ("teach", "ask"):
        return body or title
    return f"{title}. {body}" if (title and body) else (title or body)


def draw_card(layout, context, card):
    """The card for what is happening now, with its buttons."""
    from .panel import Painter
    kind = card.get("kind")
    yours = kind in ("watch", "teach", "ask", "pause", "limit")
    width = _wrap_width(context)
    box = layout.box()
    head = box.row()
    head.alert = yours
    head.label(text=Painter.card_label(_Words(), card), icon="HAND" if yours else "INFO")
    head.operator("blendwme.close_card", text="", icon="X", emboss=False)
    _wrapped(box, _card_words(card), width, limit=8)
    if card.get("why"):
        col, _ = _wrapped(box, "Why: " + card["why"], width, limit=4)
        col.enabled = False
    if kind == "teach" and session.pointers:
        row = box.row()
        targets = [ptr.get("label") or ptr.get("tab") or ptr.get("object") or "" for ptr in session.pointers]
        row.label(text=", ".join(t.replace("_", " ").title() for t in targets if t) or "Pointer",
                  icon="LIGHT_POINT")
        row.operator("blendwme.pointers_again", text="Move")
    if kind == "limit":
        target = card.get("target") or relay.other(card.get("brain") or relay.active)
        row = box.row(align=True)
        row.scale_y = 1.5
        row.alert = True
        row.operator("blendwme.hand_over", text=f"Hand over to {brain_label(target)}",
                     icon="FILE_REFRESH").target = target
        box.operator("blendwme.close_card", text="Wait")
        p = prefs.addon_prefs(context)
        if p:
            box.prop(p, "auto_relay", text="Next time, hand over by itself in Auto")


def draw_actions(layout):
    """The buttons for right now: Check my work / Show me, Next, Pause, Resume / Stop."""
    status = session.status
    running = relay.is_running()
    if (session.card or {}).get("kind") == "limit":
        return
    row = layout.row(align=True)
    row.scale_y = 1.5
    if status == WAIT_USER:
        row.operator("blendwme.teach", text="Check my work", icon="CHECKMARK").choice = "done"
        row.operator("blendwme.teach", text="Do it for me", icon="HIDE_OFF").choice = "show_me"
        hints = layout.row(align=True)
        hints.operator("blendwme.teach", text="Hint").choice = "hint"
        hints.operator("blendwme.teach", text="Skip this step").choice = "skip"
    elif status == WAIT_NEXT:
        row.operator("blendwme.next", text="Next", icon="PLAY")
        row.operator("blendwme.take_control", icon="HAND")
    elif status == PAUSED:
        row.operator("blendwme.resume", icon="PLAY")
        row.operator("blendwme.stop", icon="CANCEL")
    elif running and status in (THINKING, WORKING):
        row.operator("blendwme.pause", text="Pause", icon="PAUSE")
        row.operator("blendwme.take_control", icon="HAND")
    elif relay.has_conversation() and status in (ERROR, IDLE):
        row.scale_y = 1.0
        row.operator("blendwme.resume", text="Continue", icon="PLAY")


class BWM_PT_main(_Base, bpy.types.Panel):
    """BlendWMe in Blender's own interface (the "Blender UI" theme)."""
    bl_label = "BlendWMe"

    def draw_header(self, context):
        self.layout.label(text="", icon="GREASEPENCIL")

    def draw(self, context):
        import time
        from . import panel
        layout = self.layout
        s = context.window_manager.blendwme
        p = prefs.addon_prefs(context)
        width = _wrap_width(context)
        _draw_safety_notes(layout, p, width)
        status = session.status

        # The status (what is happening, live) and the brain
        box = layout.box()
        brain = relay.running or relay.active or s.brain
        row = box.row()
        row.alert = panel.your_turn()
        if relay.is_running() and status in (THINKING, WORKING):
            seconds = int(time.time() - (panel.state.turn_started or time.time()))
            verb = "building" if status == WORKING else "thinking"
            row.label(text=f"{brain_label(brain)} is {verb} · {seconds} s", icon=_STATUS_ICON.get(status, "DOT"))
            title = (session.card or {}).get("title") or ""
            if title:
                _wrapped(box, title, width, icon="GREASEPENCIL", limit=2)
        else:
            row.label(text=_status_words(), icon=_STATUS_ICON.get(status, "DOT"))
        sub = box.row(align=True)
        sub.label(text=brain_label(brain), icon="OUTLINER_OB_LIGHT")
        sub.operator("blendwme.hand_over", text="Hand over", icon="FILE_REFRESH").target = relay.other(brain)

        layout.row().prop(s, "mode", expand=True)
        readers = layout.row(align=True)
        readers.operator("blendwme.reader", text="Full text", icon="TEXT").kind = "all"
        readers.operator("blendwme.reader", text="Read draft").kind = "draft"

        card = session.card
        if card and not card.get("hidden") and card.get("kind") != "work":
            draw_card(layout, context, card)
        draw_actions(layout)

        # The message box
        if not relay.has_conversation():
            hint = "What shall we make?"
        else:
            hint = {"TEACH": "Ask a question...", "AUTO": "Describe what to build..."}.get(
                s.mode, f"Tell {brain_label(relay.active)} what to change...")
        col = layout.column(align=True)
        row = col.row(align=True)
        row.scale_y = 1.3
        row.prop(s, "prompt", text="", placeholder=hint)
        row.operator("blendwme.paste_prompt", text="", icon="PASTEDOWN")
        row = col.row(align=True)
        row.scale_y = 1.4
        row.operator("blendwme.send", text="Send", icon="EXPORT").new_task = False
        if relay.has_conversation() and not relay.is_running():
            row.operator("blendwme.send", text="New Task", icon="ADD").new_task = True
        if s.mode == "AUTO" and p:
            layout.prop(p, "pace", text="Pace (s a step)")
        if not relay.has_conversation() and not session.log:
            from .panel import EXAMPLES
            col = layout.column(align=True)
            col.label(text="Or start from an example:")
            for name in EXAMPLES:
                col.operator("blendwme.example", text=name, icon="LIGHT").name = name


class BWM_PT_plan(_Base, bpy.types.Panel):
    bl_label = "Plan"
    bl_parent_id = "BWM_PT_main"

    @classmethod
    def poll(cls, context):
        return _Base.poll(context) and bool(session.plan)

    def draw_header(self, context):
        done = sum(1 for item in session.plan if item["done"])
        self.layout.label(text=f"{done} of {len(session.plan)}")

    def draw(self, context):
        col = self.layout.column(align=True)
        current = next((i for i, item in enumerate(session.plan) if not item["done"]), None)
        for i, item in enumerate(session.plan):
            row = col.row()
            if i == current and relay.is_running():
                row.alert = True                 # the part being built now
            elif item["done"]:
                row.enabled = False
            name = item["name"] + (" (skipped)" if item.get("skipped") else "")
            row.label(text=name, icon="CHECKBOX_HLT" if item["done"] else "CHECKBOX_DEHLT")


class BWM_PT_conversation(_Base, bpy.types.Panel):
    bl_label = "Conversation"
    bl_parent_id = "BWM_PT_main"

    def draw(self, context):
        from . import panel
        layout = self.layout
        log = [e for e in session.log if e.get("t", 0) >= panel.state.cleared_at]
        if not log:
            layout.label(text="Ask for anything: a cosy cabin, teach me to bevel...", icon="INFO")
            return
        shown = _native["shown"]
        if len(log) > shown:
            layout.operator("blendwme.earlier", text=f"Show earlier ({len(log) - shown} more)", icon="TRIA_UP")
        width = _wrap_width(context)
        for e in log[-shown:]:
            who = e["who"]
            text = e.get("text") or ""
            if who == "you":
                box = layout.box()
                head = box.row()
                head.alignment = "RIGHT"
                head.label(text="YOU", icon="USER")
                col, _ = _wrapped(box, text, width - 4)
                col.alignment = "RIGHT"
            elif who == "ai":
                from . import richtext
                text = richtext.plain(text)
                box = layout.box()
                brain = e.get("brain") or "claude"
                box.label(text=brain_label(brain).upper(),
                          icon="KEYTYPE_KEYFRAME_VEC" if brain == "codex" else "RADIOBUT_OFF")
                eid = panel.Painter.entry_id(None, e)
                full = eid in panel.state.expanded
                _, more = _wrapped(box, text, width, limit=None if full else 12)
                if more or full:
                    box.operator("blendwme.read_more", text="Show less" if full else "Read more",
                                 emboss=False).entry = eid
            elif who in ("step", "look", "retry", "done", "plan"):
                icon = {"look": "HIDE_OFF", "retry": "GREASEPENCIL", "plan": "PRESET"}.get(
                    who, "CHECKMARK" if e.get("ok", True) else "ERROR")
                col, _ = _wrapped(layout, text, width, icon=icon, limit=3)
                col.enabled = who != "retry"
            else:
                col, _ = _wrapped(layout, text, width, icon="ERROR" if who == "error" else "INFO", limit=6)
                col.alert = who == "error"


class BWM_PT_journal(_Base, bpy.types.Panel):
    bl_label = "The Baton (Journal)"
    bl_parent_id = "BWM_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        from .journal import journal
        layout = self.layout
        data = journal.data
        width = _wrap_width(context)
        _wrapped(layout, data.get("brief") or "No brief yet. Type what you want to make.", width, icon="TEXT",
                 limit=6)
        plan = data.get("plan") or []
        if plan:
            done = sum(1 for item in plan if item.get("done"))
            layout.label(text=f"Plan: {done} of {len(plan)} done", icon="PRESET")
        steps = [e for e in data.get("events", []) if e.get("kind") == "step" and e.get("ok")]
        layout.label(text=f"{len(steps)} steps, each signed by who made it", icon="CHECKMARK")
        for st in [x for x in data.get("struggles", []) if x.get("open")][-3:]:
            _wrapped(layout, "Open: " + st["text"], width, icon="ERROR", limit=3)
        row = layout.row(align=True)
        row.operator("blendwme.open_journal", text="Open as Text", icon="TEXT")
        row.operator("blendwme.export_journal", text="Export", icon="EXPORT")


class BWM_PT_native_settings(_Base, bpy.types.Panel):
    bl_label = "Settings"
    bl_parent_id = "BWM_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        p = prefs.addon_prefs(context)
        if p:
            prefs.draw_settings(self.layout, p, context)


class BWM_PT_settings(bpy.types.Panel):
    """The drawn panel's Settings: a popover with Blender's own widgets (rule R7)."""
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_label = "BlendWMe Settings"
    bl_ui_units_x = 18

    def draw(self, context):
        layout = self.layout
        p = prefs.addon_prefs(context)
        layout.label(text="BlendWMe Settings", icon="PREFERENCES")
        _draw_safety_notes(layout, p, 52)
        if p:
            prefs.draw_settings(layout, p, context)


def draw_header_button(self, context):
    """The one way in from the 3D Viewport's header. Pressed while BlendWMe is showing."""
    from . import panel, theme
    area = context.area
    try:
        if theme.native():
            on = area is not None and sidebar_shows_blendwme(area)
        else:
            on = area is not None and panel.is_expanded_in(area)
    except Exception:
        on = False
    self.layout.operator("blendwme.open", text="BlendWMe", icon="GREASEPENCIL", depress=on)


from .reader import BWM_OT_reader

classes = (
    BWM_OT_reader,
    BWM_OT_send, BWM_OT_paste, BWM_OT_pause, BWM_OT_resume, BWM_OT_stop, BWM_OT_next,
    BWM_OT_teach, BWM_OT_undo_step, BWM_OT_new,
    BWM_OT_notebook, BWM_OT_copy_log, BWM_OT_save_log, BWM_OT_open_logs, BWM_OT_show_capture,
    BWM_OT_crash_report, BWM_OT_save_prefs, BWM_OT_dismiss_prefs_note, BWM_OT_open,
    BWM_OT_example, BWM_OT_read_more, BWM_OT_earlier, BWM_OT_close_card, BWM_OT_take_control,
    BWM_OT_pointers_again, BWM_OT_settings,
    BWM_OT_bridge, BWM_OT_copy_terminal, BWM_OT_hand_over, BWM_OT_journal, BWM_OT_setup_check,
    BWM_OT_export_journal,
    BWM_PT_main, BWM_PT_plan, BWM_PT_conversation, BWM_PT_journal, BWM_PT_native_settings,
    BWM_PT_settings,
)


def register():
    # One class at a time: a class that fails is reported, the rest still work.
    from . import theme
    failed = []
    for c in classes:
        try:
            bpy.utils.register_class(c)
        except Exception as e:
            failed.append(f"{c.__name__}: {e}")
    try:
        bpy.types.VIEW3D_HT_header.remove(draw_header_button)
    except Exception:
        pass
    bpy.types.VIEW3D_HT_header.append(draw_header_button)
    try:
        _theme_seen["last"] = theme.current_name()
    except Exception:
        pass
    if failed:
        raise RuntimeError("; ".join(failed))


def unregister():
    try:
        bpy.types.VIEW3D_HT_header.remove(draw_header_button)
    except Exception:
        pass
    for c in reversed(classes):
        if getattr(c, "is_registered", False):
            bpy.utils.unregister_class(c)
