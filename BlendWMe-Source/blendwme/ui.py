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
    bl_description = "Open or focus BlendWMe in the 3D Viewport"

    def execute(self, context):
        for area in context.window.screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.show_region_ui = True
                area.tag_redraw()
                return {"FINISHED"}
        self.report({"WARNING"}, "Open a 3D Viewport first: BlendWMe lives there.")
        return {"CANCELLED"}


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
    bl_label = "BlendWMe"

    def draw_header(self, context):
        self.layout.label(text="", icon="GREASEPENCIL")

    def draw(self, context):
        layout = self.layout
        s = context.window_manager.blendwme
        p = prefs.addon_prefs(context)
        status = session.status

        box = layout.box()
        row = box.row()
        row.alert = status in (WAIT_USER, ASKING, ERROR, WAIT_NEXT)
        row.label(text=STATUS_TEXT.get(status, status), icon=_STATUS_ICON.get(status, "DOT"))
        _draw_safety_notes(layout, p)

        if session.plan:
            plan_box = layout.box()
            done = sum(1 for item in session.plan if item["done"])
            plan_box.label(text=f"Plan  {done}/{len(session.plan)}", icon="CHECKBOX_HLT" if done == len(session.plan) else "PRESET")
            col = plan_box.column(align=True)
            next_shown = False
            for item in session.plan[:14]:
                row = col.row()
                if not item["done"] and not next_shown:
                    row.alert = relay.is_running()      # the part being built now, in red
                    next_shown = True
                elif item["done"]:
                    row.enabled = False
                row.label(text=item["name"][:48], icon="CHECKMARK" if item["done"] else "DOT")

        row = layout.row(align=True)
        row.label(text="Brain", icon="OUTLINER_OB_LIGHT")
        row.prop(s, "brain", expand=True)
        if relay.has_conversation():
            other = relay.other(relay.running or relay.active)
            big = layout.row()
            big.alert = bool(relay.limit)       # red when a usage limit is reached
            big.scale_y = 1.5 if relay.limit else 1.0
            big.enabled = not relay.handing_over
            big.operator("blendwme.hand_over", text=f"Hand over to {brain_label(other)}",
                         icon="FILE_REFRESH").target = other
        if relay.handing_over:
            layout.label(text="Handing over ...", icon="SORTTIME")
        elif relay.is_running() and relay.running != s.brain:
            layout.label(text=f"{brain_label(relay.running)} is working. The switch applies to your next message.",
                         icon="INFO")

        layout.row().prop(s, "mode", expand=True)

        # What to do right now
        if status == WAIT_NEXT:
            big = layout.row()
            big.scale_y = 1.8
            big.operator("blendwme.next", icon="PLAY")
        elif status == WAIT_USER:
            big = layout.row(align=True)
            big.scale_y = 1.6
            big.operator("blendwme.teach", text="I did it", icon="CHECKMARK").choice = "done"
            big.operator("blendwme.teach", text="Show me", icon="HIDE_OFF").choice = "show_me"
            layout.operator("blendwme.teach", text="Skip", icon="FORWARD").choice = "skip"
        elif status == ASKING:
            layout.label(text="Type your answer below and press Send.", icon="QUESTION")

        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(s, "prompt", text="", icon="GREASEPENCIL")
        row.operator("blendwme.paste_prompt", text="", icon="PASTEDOWN")
        row = col.row(align=True)
        row.scale_y = 1.4
        running = relay.is_running()
        row.operator("blendwme.send", text="Tell BlendWMe" if running else "Send",
                     icon="EXPORT").new_task = False
        if not running:
            row.operator("blendwme.send", text="New Task", icon="ADD").new_task = True

        if running:
            row = layout.row(align=True)
            if session.pause_flag.is_set():
                row.operator("blendwme.resume", icon="PLAY")
            else:
                row.operator("blendwme.pause", icon="PAUSE")
            row.operator("blendwme.stop", text="", icon="CANCEL")
        elif relay.has_conversation() and status in (ERROR, IDLE):
            layout.operator("blendwme.resume", text="Continue", icon="PLAY")
        layout.operator("blendwme.undo_step", icon="LOOP_BACK")


class BWM_PT_conversation(_Base, bpy.types.Panel):
    bl_label = "Conversation"
    bl_parent_id = "BWM_PT_main"

    def draw(self, context):
        layout = self.layout
        entries = [e for e in session.log][-16:]
        if not entries:
            layout.label(text="Ask for anything: 'a cosy low poly cabin',")
            layout.label(text="'teach me to bevel', 'rig this arm'...")
            return
        width = _wrap_width(context)
        for e in entries:
            who = e["who"]
            box = layout.box()
            col = box.column(align=True)
            head = col.row()
            if who == "you":
                head.label(text="YOU", icon="USER")
            elif who == "ai":
                head.alert = True      # BlendWMe speaks in red
                brain = e.get("brain")
                head.label(text="BLENDWME" + (f"  ·  {brain_label(brain).upper()}" if brain else ""),
                           icon="GREASEPENCIL")
            elif who == "step":
                head.label(text=("STEP" if e.get("ok", True) else "STEP FAILED"),
                           icon="MODIFIER" if e.get("ok", True) else "ERROR")
            elif who == "look":
                head.label(text="LOOKED", icon="HIDE_OFF")
            elif who == "retry":
                head.enabled = False
                head.label(text="SELF-CORRECTING", icon="FILE_REFRESH")
            elif who == "plan":
                head.label(text="PLAN", icon="PRESET")
            elif who == "error":
                head.alert = True
                head.label(text="PROBLEM", icon="ERROR")
            else:
                head.enabled = False
                head.label(text="NOTE", icon="INFO")
            text = e["text"] + (("\n" + e["detail"]) if e.get("detail") else "")
            lines = []
            for para in text.split("\n"):
                lines += textwrap.wrap(para, width) or [""]
            for line in lines[:10]:
                col.label(text=line)
            if len(lines) > 10:
                col.label(text="...")


class BWM_PT_references(_Base, bpy.types.Panel):
    bl_label = "References"
    bl_parent_id = "BWM_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = context.window_manager.blendwme
        self.layout.label(text="Images BlendWMe compares its work to.")
        self.layout.prop(s, "reference_1", text="")
        self.layout.prop(s, "reference_2", text="")


class BWM_PT_connection(_Base, bpy.types.Panel):
    bl_label = "Brains"
    bl_parent_id = "BWM_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        p = prefs.addon_prefs(context)
        bridge_box = layout.box()
        running = socket_bridge.get().is_running()
        bridge_box.label(text=("MCP bridge: running on 127.0.0.1:" + str(socket_bridge.get().port)) if running
                         else "MCP bridge: stopped", icon="CHECKMARK" if running else "ERROR")
        row = bridge_box.row(align=True)
        row.operator("blendwme.bridge", text="Start" if not running else "Restart", icon="PLAY").action = "START"
        if running:
            row.operator("blendwme.bridge", text="Stop", icon="PAUSE").action = "STOP"
        bridge_box.operator("blendwme.copy_terminal_command", icon="CONSOLE")

        setup = layout.box()
        setup.label(text="Setup checks", icon="CHECKBOX_HLT")
        row = setup.row(align=True)
        row.scale_y = 1.3
        row.operator("blendwme.setup_check", text="Check All", icon="VIEWZOOM").which = "ALL"
        row = setup.row(align=True)
        row.operator("blendwme.setup_check", text="Claude").which = "claude"
        row.operator("blendwme.setup_check", text="Codex").which = "codex"
        row.operator("blendwme.setup_check", text="MCP server").which = "mcp"
        setup.operator("blendwme.setup_check", text="Install MCP server", icon="IMPORT").which = "install"
        width = _wrap_width(context)
        icons = {setup_checks.OK: "CHECKMARK", setup_checks.WARN: "ERROR", setup_checks.ERROR: "CANCEL",
                 setup_checks.RUNNING: "SORTTIME"}
        for name in ("claude", "codex", "mcp", "install"):
            if name not in setup_checks.results:
                continue
            level, message = setup_checks.results[name]
            col = setup.column(align=True)
            col.alert = level in (setup_checks.ERROR, setup_checks.WARN)
            lines = textwrap.wrap(f"{setup_checks.NAMES[name]}: {message}", width) or [""]
            col.label(text=lines[0], icon=icons.get(level, "DOT"))
            for line in lines[1:6]:
                col.label(text=line, icon="BLANK1")
        if p:
            prefs.draw_settings(layout, p, context)


class BWM_PT_activity(_Base, bpy.types.Panel):
    bl_label = "Learn and Debug"
    bl_parent_id = "BWM_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.operator("blendwme.notebook", icon="TEXT")
        col.operator("blendwme.open_journal", icon="BOOKMARKS")
        col.operator("blendwme.show_capture", icon="IMAGE_DATA")
        col.operator("blendwme.save_log", icon="FILE_TICK")
        col.operator("blendwme.open_logs", icon="FILE_FOLDER")
        col.operator("blendwme.crash_report", icon="PACKAGE")
        col.operator("blendwme.copy_log", icon="COPYDOWN")
        col.operator("blendwme.new_task", icon="TRASH")
        if session.last_log:
            layout.label(text="Last log: " + os.path.basename(session.last_log), icon="FILE_TEXT")
        if session.last_error:
            box = layout.box()
            box.alert = True
            for line in textwrap.wrap(session.last_error, _wrap_width(context))[:8]:
                box.label(text=line)


classes = (
    BWM_OT_send, BWM_OT_paste, BWM_OT_pause, BWM_OT_resume, BWM_OT_stop, BWM_OT_next,
    BWM_OT_teach, BWM_OT_undo_step, BWM_OT_new,
    BWM_OT_notebook, BWM_OT_copy_log, BWM_OT_save_log, BWM_OT_open_logs, BWM_OT_show_capture,
    BWM_OT_crash_report, BWM_OT_save_prefs, BWM_OT_dismiss_prefs_note, BWM_OT_open,
    BWM_OT_bridge, BWM_OT_copy_terminal, BWM_OT_hand_over, BWM_OT_journal, BWM_OT_setup_check,
    BWM_PT_main, BWM_PT_conversation, BWM_PT_references, BWM_PT_connection, BWM_PT_activity,
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
