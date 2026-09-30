"""A selectable formatted reader inside Blender, independent of panel theme."""
import bpy

from . import draw2d as d, richtext, theme


def content(context, kind="all"):
    from . import panel
    from .state import session
    if kind == "draft":
        return context.window_manager.blendwme.prompt if theme.native() else panel.state.text
    parts = []
    if session.card:
        parts += [session.card.get("title", ""), session.card.get("text", ""),
                  session.card.get("why", "")]
    parts += [f"{e['who'].upper()}\n{e.get('text', '')}\n{e.get('detail', '')}"
              for e in session.log]
    if session.plan:
        parts.append("PLAN\n" + "\n".join(p["name"] + (" (skipped: " + p.get("reason", "") + ")"
                          if p.get("skipped") else "") for p in session.plan))
    return "\n\n".join(p for p in parts if p)


class BWM_OT_reader(bpy.types.Operator):
    bl_idname = "blendwme.reader"
    bl_label = "Read Full Text"
    bl_description = "Read complete text; Ctrl+A selects all, Ctrl+C copies rendered text, Shift+Ctrl+C copies original"
    kind: bpy.props.EnumProperty(items=[("all", "Conversation and current details", ""),
                                        ("draft", "Current draft", "")], default="all")

    def invoke(self, context, event):
        if bpy.app.background:
            return {"CANCELLED"}
        self.source = content(context, self.kind)
        self.plain = richtext.plain(self.source)
        self.scroll = 0
        self.maximum = 0
        self.selection = (0, 0)
        self.dragging = False
        self.glyphs = []
        self.area = context.area
        self.window = context.window
        self.handler = bpy.types.SpaceView3D.draw_handler_add(self.draw_reader, (), "WINDOW", "POST_PIXEL")
        context.window_manager.modal_handler_add(self)
        self.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def close(self):
        bpy.types.SpaceView3D.draw_handler_remove(self.handler, "WINDOW")
        self.area.tag_redraw()

    def modal(self, context, event):
        if context.window != self.window or context.area != self.area:
            return {"PASS_THROUGH"}
        if event.type in {"ESC", "RIGHTMOUSE"}:
            self.close()
            return {"CANCELLED"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            self.selection = (self.index_at(event),) * 2
            self.dragging = True
        elif event.type == "MOUSEMOVE" and self.dragging:
            self.selection = (self.selection[0], self.index_at(event))
        elif event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self.dragging = False
        if event.value == "PRESS":
            if event.ctrl and event.type == "A":
                self.selection = (0, len(self.plain))
            elif event.ctrl and event.type == "C":
                a, b = sorted(self.selection)
                if a != b or event.shift:
                    context.window_manager.clipboard = self.source if event.shift else self.plain[a:b]
            elif event.type == "WHEELUPMOUSE":
                self.scroll = max(0, self.scroll - 3)
            elif event.type == "WHEELDOWNMOUSE":
                self.scroll = min(self.maximum, self.scroll + 3)
            elif event.type == "HOME":
                self.scroll = 0
            elif event.type == "END":
                self.scroll = self.maximum
        self.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def index_at(self, event):
        if not self.glyphs:
            return 0
        # Cached drawing coordinates keep mouse selection in the same font and
        # scale as wrapping, including bold spans and scrolled content.
        _, _, _, index = min(self.glyphs,
                             key=lambda item: abs(item[1] - event.mouse_y) * 10000 + abs(item[0] - event.mouse_x))
        return index

    def draw_reader(self):
        if bpy.context.area != self.area or bpy.context.region.type != "WINDOW":
            return
        import gpu
        gpu.state.blend_set("ALPHA")
        p = theme.current()
        region = bpy.context.region
        x, y = 20, 20
        w, h = max(100, region.width - 40), max(100, region.height - 40)
        d.box(x, y, w, h, fill=p.panel, border=p.ink, width=2, radius=10)
        size = 18 * d.system_scale()
        lh = max(d.line_height(role, size) for role in ("BODY", "BLACK", "CODE"))
        lines = richtext.layout(self.source, size, w - 32, d.width_of)
        visible = max(1, int((h - 70) // lh))
        self.maximum = max(0, len(lines) - visible)
        self.scroll = min(self.maximum, self.scroll)
        offsets, cursor = [], 0
        for line in lines:
            value = "".join(run[1] for run in line)
            index = self.plain.find(value, cursor) if value else cursor
            index = cursor if index < 0 else index
            offsets.append(index)
            cursor = index + len(value)
        self.glyphs = []
        d.text("BODY", 14, x + 16, y + h - 24,
               "Full text | Wheel / Home / End | Ctrl+A, Ctrl+C | Shift+Ctrl+C original | Esc closes", p.ink)
        with d.clip(x + 8, y + 8, w - 16, h - 48):
            for index, line in enumerate(lines[self.scroll:self.scroll + visible]):
                ly = y + h - 60 - index * lh
                a, b = sorted(self.selection)
                offset = offsets[self.scroll + index]
                gx = x + 16
                for role, text, advance in line:
                    for char_index in range(len(text) + 1):
                        dx = d.width_of(role, size, text[:char_index])
                        self.glyphs.append((gx + dx + region.x, ly + region.y, lh, offset + char_index))
                    start, end = max(0, a - offset), min(len(text), b - offset)
                    if start < end:
                        sx = d.width_of(role, size, text[:start])
                        sw = d.width_of(role, size, text[:end]) - sx
                        gpu.state.blend_set("ALPHA")
                        d.rect(gx + sx, ly - 4, sw, lh, p.with_alpha(p.accent, 0.2))
                    gx += advance
                    offset += len(text)
                richtext.draw_line(line, size, x + 16, ly, p.ink)
        gpu.state.blend_set("NONE")
