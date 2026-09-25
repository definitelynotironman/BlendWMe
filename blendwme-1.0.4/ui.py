"""The BlendWMe sidebar (3D Viewport > N > BlendWMe) and its buttons."""

import os
import textwrap
import threading

import bpy

from . import bridge, credentials, llm, look, prefs
from .agent import agent
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
        if not config["api_key"]:
            self.report({"ERROR"}, "Add your Nebius key: BlendWMe > Connection > Paste Key.")
            return {"CANCELLED"}
        if self.new_task and agent.is_running():
            agent.stop()
            agent.thread.join(timeout=5)
        if self.new_task:
            agent.messages = []
            session.reset_conversation()
        agent.send(text, config, new_task=self.new_task)
        s.prompt = ""
        return {"FINISHED"}


class BWM_OT_paste(bpy.types.Operator):
    bl_idname = "blendwme.paste_prompt"
    bl_label = "Paste"
    bl_description = "Paste a long prompt from the clipboard"

    def execute(self, context):
        context.window_manager.blendwme.prompt = context.window_manager.clipboard[:12000]
        return {"FINISHED"}


class BWM_OT_paste_key(bpy.types.Operator):
    bl_idname = "blendwme.paste_key"
    bl_label = "Paste Key"
    bl_description = ("Read your Nebius key from the clipboard, store it safely, then clear the clipboard. "
                      "The key is never saved in Blender files")

    def execute(self, context):
        p = prefs.addon_prefs(context)
        text = context.window_manager.clipboard.strip()
        if not credentials.looks_like_key(text):
            self.report({"ERROR"}, "The clipboard does not look like an API key. Copy the whole key first.")
            return {"CANCELLED"}
        try:
            message = credentials.set_key(text, p.key_storage)
        except OSError as e:
            credentials.set_key(text, "SESSION")
            self.report({"WARNING"}, f"{e} Kept it for this session only.")
            return {"FINISHED"}
        context.window_manager.clipboard = ""   # do not leave the key lying around
        self.report({"INFO"}, message + " Clipboard cleared.")
        return {"FINISHED"}


class BWM_OT_forget_key(bpy.types.Operator):
    bl_idname = "blendwme.forget_key"
    bl_label = "Forget Key"
    bl_description = "Remove the key from memory and from the password store"

    def execute(self, context):
        credentials.forget(prefs.addon_prefs(context).key_storage)
        self.report({"INFO"}, "Key forgotten.")
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
        if not agent.is_running() and agent.messages:
            # Resume after an error or after it finished: continue the same conversation.
            config = prefs.gather_config(context)
            agent.send("Continue.", config)
            return {"FINISHED"}
        session.pause_flag.clear()
        session.wake()
        return {"FINISHED"}


class BWM_OT_stop(bpy.types.Operator):
    bl_idname = "blendwme.stop"
    bl_label = "Stop"
    bl_description = "Stop the current task. Everything already built stays"

    def execute(self, context):
        agent.stop()
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
        if agent.is_running() and not session.pause_flag.is_set():
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
        agent.reset()
        return {"FINISHED"}


def _background(fn):
    threading.Thread(target=fn, daemon=True).start()


def _match_id(wanted, available):
    """Case-insensitive match of a model ID against the provider's list."""
    low = wanted.lower()
    for mid in available:
        if mid.lower() == low:
            return mid
    for mid in available:
        if low.split("/")[-1] in mid.lower():
            return mid
    return None


class BWM_OT_test_connection(bpy.types.Operator):
    bl_idname = "blendwme.test_connection"
    bl_label = "Test Connection"
    bl_description = "Check your key, find the exact model IDs, and test tool calling"

    def execute(self, context):
        config = prefs.gather_config(context)
        p = prefs.addon_prefs(context)
        session.add("info", f"Testing connection with {config['model']} ...")

        def work():
            client = llm.Client(config["api_key"], config["base_url"], timeout=120)
            try:
                models = client.list_models()
            except llm.LLMError as e:
                session.add("error", f"Connection failed: {e}")
                return
            session.add("info", f"Key works. {len(models)} models available.")
            nvidia = [m for m in models if "nemotron" in m.lower() or m.lower().startswith("nvidia/")]
            if nvidia:
                session.add("info", "NVIDIA models: " + ", ".join(nvidia[:12]))
            model = _match_id(config["model"], models) or config["model"]
            if model != config["model"]:
                def fix():
                    p.agent_preset = "CUSTOM"
                    p.custom_model = model
                bridge.call(fix)
                session.add("info", f"Using exact ID {model}.")
            elif model not in models:
                session.add("error", f"{model} is not in your catalogue. Pick another model.")
                return
            probe = [{"type": "function", "function": {
                "name": "add_numbers", "description": "Add two numbers",
                "parameters": {"type": "object", "properties": {
                    "a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]}}}]
            try:
                import time as _t
                t0 = _t.time()
                msg, usage = client.chat(model, [{"role": "user", "content": "Use the tool to add 2 and 3."}],
                                         probe, max_tokens=1024, reasoning=config["reasoning"])
                took = _t.time() - t0
            except llm.LLMError as e:
                session.add("error", f"Model test failed: {e}")
                return
            if msg.get("tool_calls"):
                session.add("info", f"Tool calling works with {model} ({took:.1f}s). You are ready.")
            else:
                session.add("error", f"{model} answered without calling the tool. It may struggle as an agent. "
                                     "Try another model.")

        _background(work)
        return {"FINISHED"}


class BWM_OT_test_vision(bpy.types.Operator):
    bl_idname = "blendwme.test_vision"
    bl_label = "Test Vision"
    bl_description = "Capture the viewport and find a vision model on your account that can describe it"

    def execute(self, context):
        config = prefs.gather_config(context)
        p = prefs.addon_prefs(context)
        shot = look.capture("viewport", max_size=512)
        if shot.get("error"):
            self.report({"ERROR"}, shot["error"])
            return {"CANCELLED"}
        session.last_capture = shot["path"]
        session.add("info", "Testing vision models ...")

        def work():
            client = llm.Client(config["api_key"], config["base_url"], timeout=120)
            try:
                models = client.list_models_detailed()
            except llm.LLMError as e:
                session.add("error", f"Could not list models: {e}")
                return
            seeing = [m["id"] for m in models if m["vision"]]
            if seeing:
                session.add("info", "Models on your account that accept images: " + ", ".join(seeing[:10]))
            current = p.vision_model.strip()
            order = []
            first = llm.pick_vision_model(models, current)
            for cand in [first] + llm.VISION_PREFERENCE + seeing:
                match = _match_id(cand, [m["id"] for m in models]) if cand else None
                if match and match not in order and "nemotron" not in match.lower():
                    order.append(match)
            for model in order[:5]:
                try:
                    import time as _t
                    t0 = _t.time()
                    text = client.describe_image(model, "In one sentence, what is in this Blender viewport?",
                                                 [shot["path"]], max_tokens=200)
                    took = _t.time() - t0
                except llm.LLMError as e:
                    session.add("info", f"{model}: no ({str(e)[:80]})")
                    continue
                if text:
                    def fix(m=model):
                        p.vision_model = m
                    bridge.call(fix)
                    session.add("info", f"Vision works with {model} ({took:.1f}s): \"{text[:160]}\"")
                    return
            session.add("error", "No vision model worked. BlendWMe will still build, using scene data. "
                                 "Type a vision model ID from your catalogue and test again.")

        _background(work)
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
    bl_description = "Copy the conversation and activity (no API key) for debugging"

    def execute(self, context):
        lines = [f"BlendWMe 1.0.4 | model {prefs.gather_config(context)['model']} | "
                 f"calls {session.calls} | tokens {session.tokens}"]
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
        if not agent.messages:
            self.report({"WARNING"}, "Nothing to save yet.")
            return {"CANCELLED"}
        agent.config.update(prefs.gather_config(context))
        path = agent.save_log()
        if path:
            self.report({"INFO"}, f"Saved {path}")
            session.add("info", "Full log saved: " + path)
        return {"FINISHED"}


class BWM_OT_open_logs(bpy.types.Operator):
    bl_idname = "blendwme.open_logs"
    bl_label = "Open Logs Folder"
    bl_description = "Open the folder with the saved conversation logs"

    def execute(self, context):
        from . import logs
        p = prefs.addon_prefs(context)
        folder = bpy.path.abspath(p.log_folder) if p and p.log_folder.strip() else logs.default_folder()
        os.makedirs(folder, exist_ok=True)
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
        if session.calls:
            box.label(text=f"{len(session.steps)} steps  |  {session.calls} calls  |  "
                           f"{session.tokens:,} tokens", icon="BLANK1")

        if session.plan:
            plan_box = layout.box()
            done = sum(1 for item in session.plan if item["done"])
            plan_box.label(text=f"Plan  {done}/{len(session.plan)}", icon="CHECKBOX_HLT" if done == len(session.plan) else "PRESET")
            col = plan_box.column(align=True)
            next_shown = False
            for item in session.plan[:14]:
                row = col.row()
                if not item["done"] and not next_shown:
                    row.alert = agent.is_running()      # the part being built now, in red
                    next_shown = True
                elif item["done"]:
                    row.enabled = False
                row.label(text=item["name"][:48], icon="CHECKMARK" if item["done"] else "DOT")

        if p and not p.vision_enabled:
            eye = layout.row()
            eye.alert = True
            eye.prop(p, "vision_enabled", text="Vision is OFF: the AI builds blind. Turn on", icon="HIDE_ON")

        if p and not p.key():
            warn = layout.box()
            warn.alert = True
            warn.label(text="Add your key: Connection > Paste Key.", icon="ERROR")

        layout.row().prop(s, "mode", expand=True)
        if s.mode == "AUTO":
            layout.row().prop(s, "build_style", expand=True)
        layout.prop(s, "level")
        if s.mode == "WATCH":
            layout.prop(s, "watch_auto")

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
        running = agent.is_running()
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
        elif agent.messages and status in (ERROR, IDLE):
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
                head.label(text="BLENDWME", icon="GREASEPENCIL")
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
    bl_label = "Connection"
    bl_parent_id = "BWM_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        p = prefs.addon_prefs(context)
        if p:
            prefs.draw_connection(self.layout, p)


class BWM_PT_activity(_Base, bpy.types.Panel):
    bl_label = "Learn and Debug"
    bl_parent_id = "BWM_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.operator("blendwme.notebook", icon="TEXT")
        col.operator("blendwme.show_capture", icon="IMAGE_DATA")
        col.operator("blendwme.save_log", icon="FILE_TICK")
        col.operator("blendwme.open_logs", icon="FILE_FOLDER")
        col.operator("blendwme.copy_log", icon="COPYDOWN")
        col.operator("blendwme.new_task", icon="TRASH")
        if session.last_latency:
            layout.label(text=f"Last model reply: {session.last_latency}s")
        if session.last_log:
            layout.label(text="Last log: " + os.path.basename(session.last_log), icon="FILE_TEXT")
        if session.last_error:
            box = layout.box()
            box.alert = True
            for line in textwrap.wrap(session.last_error, _wrap_width(context))[:8]:
                box.label(text=line)


classes = (
    BWM_OT_send, BWM_OT_paste, BWM_OT_paste_key, BWM_OT_forget_key, BWM_OT_pause, BWM_OT_resume, BWM_OT_stop, BWM_OT_next,
    BWM_OT_teach, BWM_OT_undo_step, BWM_OT_new, BWM_OT_test_connection, BWM_OT_test_vision,
    BWM_OT_notebook, BWM_OT_copy_log, BWM_OT_save_log, BWM_OT_open_logs, BWM_OT_show_capture,
    BWM_PT_main, BWM_PT_conversation, BWM_PT_references, BWM_PT_connection, BWM_PT_activity,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
