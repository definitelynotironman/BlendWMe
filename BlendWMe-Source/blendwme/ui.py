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

    choice: bpy.props.EnumProperty(items=[("done", "I did it", ""), ("show_me", "Show me", ""),
                                          ("skip", "Skip", "")])

    @classmethod
    def description(cls, context, properties):
        return {"done": "Tell BlendWMe you finished this step",
                "show_me": "Let BlendWMe demonstrate this step for you",
                "skip": "Skip this step"}[properties.choice]

    def execute(self, context):
        if context.mode != "OBJECT" and self.choice == "done":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception:
                pass
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


class BWM_OT_open(bpy.types.Operator):
    bl_idname = "blendwme.open"
    bl_label = "Open BlendWMe"
    bl_description = "Open or focus BlendWMe in the 3D Viewport (Ctrl+Alt+M)"

    close: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})

    def execute(self, context):
        from . import panel, panel_ops
        if self.close:
            panel_ops.close_panel()
            return {"FINISHED"}
        if panel.state.open and panel_ops.panel_alive() and panel.is_open_in(context.area):
            panel.state.focused = True        # already open here: focus the input
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


# ----------------------------------------------------------------------
# Panels
# ----------------------------------------------------------------------

class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlendWMe"


_STATUS_ICON = {IDLE: "CHECKBOX_DEHLT", THINKING: "SORTTIME", WORKING: "MODIFIER",
                WAIT_NEXT: "PLAY", WAIT_USER: "HAND", ASKING: "QUESTION", PAUSED: "PAUSE",
                DONE: "CHECKMARK", ERROR: "ERROR"}


def _wrap_width(context):
    return max(20, int(context.region.width / (6.5 * context.preferences.system.ui_scale)))


def _wrapped(layout, text, width):
    col = layout.column(align=True)
    for line in textwrap.wrap(text, width) or [""]:
        col.label(text=line)


def _draw_safety_notes(layout, p):
    """Parts that failed to start, and the one-time 'Save Preferences' note."""
    from . import lifecycle
    if lifecycle.problems:
        box = layout.box()
        box.alert = True
        box.label(text="Part of BlendWMe did not start:", icon="ERROR")
        for part, message in lifecycle.problems[:4]:
            _wrapped(box, f"{part}: {message}", 44)
        box.operator("blendwme.crash_report", icon="PACKAGE")
    if lifecycle.needs_prefs_note(p):
        box = layout.box()
        _wrapped(box, "Save Preferences so BlendWMe stays on after restarts.", 44)
        row = box.row(align=True)
        row.operator("blendwme.save_prefs", icon="FILE_TICK")
        row.operator("blendwme.dismiss_prefs_note")


class BWM_PT_main(_Base, bpy.types.Panel):
    """The sidebar is only a launcher: BlendWMe itself is the panel drawn in the viewport."""
    bl_label = "BlendWMe"

    def draw(self, context):
        from . import panel
        layout = self.layout
        p = prefs.addon_prefs(context)
        row = layout.row()
        row.scale_y = 1.6
        if panel.is_open_in(context.area):
            row.operator("blendwme.open", text="Close BlendWMe", icon="PANEL_CLOSE").close = True
        else:
            row.operator("blendwme.open", text="Open BlendWMe", icon="WINDOW")
        layout.label(text="Shortcut: Ctrl+Alt+M")
        status = session.status
        layout.label(text=STATUS_TEXT.get(status, status), icon=_STATUS_ICON.get(status, "DOT"))
        _draw_safety_notes(layout, p)


class BWM_PT_references(_Base, bpy.types.Panel):
    bl_label = "References"
    bl_parent_id = "BWM_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = context.window_manager.blendwme
        self.layout.label(text="Images BlendWMe compares its work to.")
        self.layout.prop(s, "reference_1", text="")
        self.layout.prop(s, "reference_2", text="")


class BWM_PT_more(_Base, bpy.types.Panel):
    bl_label = "More"
    bl_parent_id = "BWM_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.operator("blendwme.notebook", icon="TEXT")
        col.operator("blendwme.show_capture", icon="IMAGE_DATA")
        col.operator("blendwme.open_logs", icon="FILE_FOLDER")
        col.operator("blendwme.save_log", icon="FILE_TICK")
        col.operator("blendwme.copy_log", icon="COPYDOWN")
        col.operator("blendwme.crash_report", icon="PACKAGE")
        col = layout.column(align=True)
        col.operator("blendwme.setup_check", text="Install the Tools Server", icon="IMPORT").which = "install"
        col.operator("blendwme.copy_terminal_command", icon="CONSOLE")


classes = (
    BWM_OT_send, BWM_OT_paste, BWM_OT_pause, BWM_OT_resume, BWM_OT_stop, BWM_OT_next,
    BWM_OT_teach, BWM_OT_undo_step, BWM_OT_new,
    BWM_OT_notebook, BWM_OT_copy_log, BWM_OT_save_log, BWM_OT_open_logs, BWM_OT_show_capture,
    BWM_OT_crash_report, BWM_OT_save_prefs, BWM_OT_dismiss_prefs_note, BWM_OT_open,
    BWM_OT_bridge, BWM_OT_copy_terminal, BWM_OT_hand_over, BWM_OT_journal, BWM_OT_setup_check,
    BWM_OT_export_journal,
    BWM_PT_main, BWM_PT_references, BWM_PT_more,
)


def register():
    # One class at a time: a class that fails is reported, the rest still work.
    failed = []
    for c in classes:
        try:
            bpy.utils.register_class(c)
        except Exception as e:
            failed.append(f"{c.__name__}: {e}")
    if failed:
        raise RuntimeError("; ".join(failed))


def unregister():
    for c in reversed(classes):
        if getattr(c, "is_registered", False):
            bpy.utils.unregister_class(c)
