# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""look: the AI's eyes.

Renders the 3D viewport offscreen (works even when Blender is behind other
windows) and saves a PNG. "angles" mode frames the target from several sides
and tiles them into one contact sheet, which is how an artist checks
silhouette and proportions.

Structure: ``Framing`` decides what to look at and from where; ``ContactSheet``
turns pixel tiles into one saved PNG; ``ViewportCamera`` draws offscreen
through a real 3D Viewport; ``RenderCamera`` renders with Workbench when
there is no viewport (background Blender). ``capture`` is the entry point.

The offscreen and angle technique is adapted from MCP for Blender
(MIT licence, Copyright (c) 2025 Siddharth Ahuja). See LICENSE-THIRD-PARTY.
"""

import math
import os
import tempfile
import time

import bpy
import gpu
import numpy as np
from mathutils import Matrix, Vector

from . import helpers

__author__ = "definitelynotironman"

FOV = math.radians(35)
ANGLES = {
    "front": (0, -1, 0), "back": (0, 1, 0), "right": (1, 0, 0), "left": (-1, 0, 0),
    "top": (0, -0.001, 1), "three_quarter": (1, -1, 0.7),
}
DEFAULT_VIEWS = ["front", "right", "top", "three_quarter"]
LAYOUT = "tiles left-to-right, top-to-bottom in that order"


class Framing:
    """Chooses the subject of a picture and the camera positions around it."""

    @staticmethod
    def out_path(tag):
        """A fresh PNG path in the temporary BlendWMe folder."""
        folder = os.path.join(tempfile.gettempdir(), "blendwme")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, f"{tag}_{int(time.time() * 1000)}.png")

    def targets(self, names):
        """Objects to frame. Names can be objects (children included) or collections."""
        scene = bpy.context.scene
        out = []

        def add(o):
            """Add o (not cameras or lights) and its children once."""
            if o not in out and o.type not in {"CAMERA", "LIGHT"}:
                out.append(o)
            for c in o.children:
                add(c)
        for n in names or []:
            if n in bpy.data.objects:
                add(bpy.data.objects[n])
            elif n in bpy.data.collections:
                for o in bpy.data.collections[n].all_objects:
                    add(o)
        if not out:
            out = [o for o in scene.objects if o.visible_get() and o.type not in {"CAMERA", "LIGHT"}]
        return self.drop_ground(out)

    @staticmethod
    def footprint(objs):
        """The larger of the X and Y extents of objs in world space."""
        xs, ys = [], []
        for o in objs:
            for c in o.bound_box:
                p = o.matrix_world @ Vector(c)
                xs.append(p.x)
                ys.append(p.y)
        return max(max(xs) - min(xs), max(ys) - min(ys)) if xs else 0.0

    def drop_ground(self, objs):
        """A huge flat ground plane makes the subject tiny. Frame the subject instead."""
        def flat(o):
            """True for a mesh much flatter than it is wide (a ground plane)."""
            d = o.dimensions
            return o.type == "MESH" and d.z <= 0.02 * max(d.x, d.y, 1e-6)
        rest = [o for o in objs if not flat(o)]
        if not rest:
            return objs
        size = self.footprint(rest)
        keep = [o for o in objs if not flat(o) or max(o.dimensions.x, o.dimensions.y) <= 2.0 * size]
        return keep or objs

    @staticmethod
    def bounds(objs):
        """(centre, radius) of the evaluated bounding boxes of objs."""
        depsgraph = bpy.context.evaluated_depsgraph_get()
        pts = []
        for o in objs:
            try:
                ev = o.evaluated_get(depsgraph)
                pts.extend(ev.matrix_world @ Vector(c) for c in ev.bound_box)
            except Exception:
                pts.append(o.matrix_world.translation.copy())
        if not pts:
            pts = [Vector((0, 0, 0))]
        lo = Vector([min(p[i] for p in pts) for i in range(3)])
        hi = Vector([max(p[i] for p in pts) for i in range(3)])
        return (lo + hi) / 2, max((hi - lo).length / 2, 0.05)

    @staticmethod
    def view_names(views):
        """The requested known angle names (at most six), or a sensible default."""
        names = [v for v in (views or DEFAULT_VIEWS) if v in ANGLES][:6]
        return names or ["front", "three_quarter"]

    @staticmethod
    def perspective(aspect, near, far):
        """An OpenGL-style perspective matrix for FOV."""
        f = 1 / math.tan(FOV / 2)
        return Matrix(((f / aspect, 0, 0, 0), (0, f, 0, 0),
                       (0, 0, (far + near) / (near - far), 2 * far * near / (near - far)),
                       (0, 0, -1, 0)))

    def orbit(self, center, radius, direction, aspect):
        """(view, projection) matrices that fit a sphere of radius around center from direction."""
        direction = Vector(direction).normalized()
        fit = FOV / 2 if aspect >= 1 else math.atan(math.tan(FOV / 2) * aspect)
        dist = radius / math.sin(fit) * 1.1
        eye = center + direction * dist
        rot = (center - eye).to_track_quat("-Z", "Y").to_matrix().to_4x4()
        view = (Matrix.Translation(eye) @ rot).inverted()
        return view, self.perspective(aspect, max(dist - radius * 3, dist * 0.01), dist + radius * 3)


class ContactSheet:
    """Tiles pixel arrays into one sheet and saves it as a PNG."""

    @staticmethod
    def tile(tiles, cols):
        """One float RGBA array with tiles laid out left-to-right, top-to-bottom."""
        h, w = tiles[0].shape[:2]
        rows = math.ceil(len(tiles) / cols)
        gap = 4
        out = np.ones((rows * h + (rows - 1) * gap, cols * w + (cols - 1) * gap, 4), dtype=np.float32)
        out[..., :3] = 0.9
        for i, t in enumerate(tiles):
            r, c = divmod(i, cols)
            y = (rows - 1 - r) * (h + gap)
            x = c * (w + gap)
            out[y:y + h, x:x + w] = t
        return out

    @staticmethod
    def save(pixels, path):
        """Write pixels to path through a temporary Image (removed again). Returns (w, h)."""
        h, w = pixels.shape[:2]
        img = bpy.data.images.new("blendwme_look", w, h, alpha=True)
        try:
            img.pixels.foreach_set(pixels.ravel())
            img.filepath_raw = path
            img.file_format = "PNG"
            img.save()
        finally:
            bpy.data.images.remove(img)
        return w, h


class ViewportCamera:
    """Pictures drawn offscreen through the user's real 3D Viewport (windowed Blender)."""

    def __init__(self, framing, sheet):
        """Use framing for subjects and angles, sheet for tiling and saving."""
        self.framing = framing
        self.sheet = sheet

    @staticmethod
    def draw(space, region, view, win, w, h):
        """Draw the scene offscreen with these matrices; float RGBA pixels (h, w, 4)."""
        off = gpu.types.GPUOffScreen(w, h)
        try:
            off.draw_view3d(bpy.context.scene, bpy.context.view_layer, space, region, view, win,
                            do_color_management=True)
            buf = off.texture_color.read()
        finally:
            off.free()
        buf.dimensions = w * h * 4
        return np.asarray(buf, dtype=np.float32).reshape(h, w, 4) / 255.0

    def capture(self, area, region, mode, targets, views, shading, max_size):
        """Capture viewport / camera / angles. Viewport settings changed for the shot are restored."""
        space = area.spaces.active
        scene = bpy.context.scene
        saved = []

        def set_attr(obj, attr, value):
            """Set obj.attr for this capture and remember the old value."""
            if hasattr(obj, attr):
                saved.append((obj, attr, getattr(obj, attr)))
                try:
                    setattr(obj, attr, value)
                except Exception:
                    saved.pop()

        shading_map = {"solid": "SOLID", "material": "MATERIAL", "wireframe": "SOLID"}
        if shading in shading_map:
            set_attr(space.shading, "type", shading_map[shading])
        if shading == "wireframe":
            set_attr(space.overlay, "show_wireframes", True)
            set_attr(space.overlay, "wireframe_threshold", 1.0)
        info = {"mode": mode}
        try:
            if mode == "viewport":
                w, h = region.width, region.height
                s = min(1.0, max_size / max(w, h))
                w, h = max(1, int(w * s)), max(1, int(h * s))
                r3d = space.region_3d
                pixels = self.draw(space, region, r3d.view_matrix, r3d.window_matrix, w, h)
            elif mode == "camera":
                cam = scene.camera
                if cam is None:
                    return {"error": "The scene has no camera. Use mode='angles' instead."}
                rx, ry = scene.render.resolution_x, scene.render.resolution_y
                s = max_size / max(rx, ry)
                w, h = max(1, int(rx * s)), max(1, int(ry * s))
                win = cam.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(), x=w, y=h,
                                             scale_x=scene.render.pixel_aspect_x,
                                             scale_y=scene.render.pixel_aspect_y)
                set_attr(space.overlay, "show_overlays", False)
                pixels = self.draw(space, region, cam.matrix_world.inverted(), win, w, h)
                info["camera"] = cam.name
            else:  # angles
                objs = self.framing.targets(targets)
                center, radius = self.framing.bounds(objs)
                names = self.framing.view_names(views)
                for overlay in ("show_cursor", "show_extras", "show_relationship_lines", "show_floor",
                                "show_axis_x", "show_axis_y", "show_ortho_grid"):
                    set_attr(space.overlay, overlay, False)
                cols = 2 if len(names) > 1 else 1
                tile = max(160, int(max_size / cols))
                tiles = []
                for n in names:
                    view, win = self.framing.orbit(center, radius, ANGLES[n], 1.0)
                    tiles.append(self.draw(space, region, view, win, tile, tile))
                pixels = self.sheet.tile(tiles, cols)
                info.update({"views": names, "layout": LAYOUT, "target_center": [round(v, 2) for v in center]})
            path = self.framing.out_path(mode)
            w, h = self.sheet.save(pixels, path)
            info.update({"path": path, "width": w, "height": h})
            return info
        except Exception as e:
            return {"error": f"Capture failed: {e}"}
        finally:
            for obj, attr, value in reversed(saved):
                try:
                    setattr(obj, attr, value)
                except Exception:
                    pass


class RenderCamera:
    """Pictures without a 3D Viewport (background Blender, live tests).

    The offscreen viewport draw needs a real viewport. When there is none we
    render the same views with the fast Workbench engine instead, using
    material colours so the critic still sees the palette. Every render
    setting is restored and the temporary camera is removed afterwards.
    """

    def __init__(self, framing, sheet):
        """Use framing for subjects and angles, sheet for tiling and saving."""
        self.framing = framing
        self.sheet = sheet

    def render_view(self, scene, cam, eye, center, size):
        """Render one tile with the Workbench engine through a temporary camera."""
        cam.location = eye
        cam.rotation_euler = (center - eye).to_track_quat("-Z", "Y").to_euler()
        scene.render.resolution_x = scene.render.resolution_y = size
        scene.render.filepath = self.framing.out_path("tile")
        bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(scene.render.filepath)
        try:
            pixels = np.array(img.pixels[:], dtype=np.float32).reshape(img.size[1], img.size[0], 4)
        finally:
            bpy.data.images.remove(img)
        try:
            os.remove(scene.render.filepath)
        except OSError:
            pass
        return pixels

    def capture(self, mode, targets, views, max_size):
        """Capture viewport / camera / angles by rendering."""
        scene = bpy.context.scene
        render, shading = scene.render, scene.display.shading
        saved = [(scene, "camera", scene.camera), (render, "engine", render.engine),
                 (render, "resolution_x", render.resolution_x), (render, "resolution_y", render.resolution_y),
                 (render, "resolution_percentage", render.resolution_percentage),
                 (render, "filepath", render.filepath), (shading, "color_type", shading.color_type),
                 (shading, "light", shading.light)]
        cam = None
        info = {"mode": mode, "rendered": True}
        try:
            render.engine = "BLENDER_WORKBENCH"
            render.resolution_percentage = 100
            shading.light = "STUDIO"
            shading.color_type = "MATERIAL"
            if mode == "camera":
                if scene.camera is None:
                    return {"error": "The scene has no camera. Use mode='angles' instead."}
                render.filepath = self.framing.out_path("camera")
                s = max_size / max(render.resolution_x, render.resolution_y)
                render.resolution_percentage = max(1, min(100, int(s * 100)))
                bpy.ops.render.render(write_still=True)
                info.update({"path": render.filepath, "camera": scene.camera.name})
                return info
            cam = bpy.data.objects.new("blendwme_look_cam", bpy.data.cameras.new("blendwme_look_cam"))
            cam.data.angle = FOV
            scene.collection.objects.link(cam)
            scene.camera = cam
            objs = self.framing.targets(targets)
            center, radius = self.framing.bounds(objs)
            # No viewport to copy in "viewport" mode: show the classic three-quarter view.
            names = ["three_quarter"] if mode == "viewport" else self.framing.view_names(views)
            cols = 2 if len(names) > 1 else 1
            tile = max(160, int(max_size / cols))
            tiles = []
            for n in names:
                direction = Vector(ANGLES[n]).normalized()
                dist = radius / math.sin(FOV / 2) * 1.1
                cam.data.clip_start = max(dist - radius * 3, dist * 0.01)
                cam.data.clip_end = dist + radius * 3
                tiles.append(self.render_view(scene, cam, center + direction * dist, center, tile))
            pixels = self.sheet.tile(tiles, cols)
            path = self.framing.out_path(mode)
            w, h = self.sheet.save(pixels, path)
            info.update({"path": path, "width": w, "height": h, "views": names, "layout": LAYOUT,
                         "target_center": [round(v, 2) for v in center]})
            return info
        except Exception as e:
            return {"error": f"Capture failed: {e}"}
        finally:
            for owner, attr, value in reversed(saved):
                try:
                    setattr(owner, attr, value)
                except Exception:
                    pass
            if cam is not None:
                data = cam.data
                bpy.data.objects.remove(cam)
                bpy.data.cameras.remove(data)


class Eyes:
    """The capture entry point: true material colours, then viewport or render capture."""

    def __init__(self):
        """Wire the framing, sheet and both camera strategies."""
        self.framing = Framing()
        self.sheet = ContactSheet()
        self.viewport = ViewportCamera(self.framing, self.sheet)
        self.render = RenderCamera(self.framing, self.sheet)

    @staticmethod
    def true_colours():
        """Give every node material its Base Color as viewport colour. Returns what to restore.

        Solid view and the Workbench render show Material.diffuse_color, which only bwm.material
        sets. Materials the model built from nodes looked clay-grey to the critic (seen live).
        """
        saved = []
        for m in bpy.data.materials:
            if not m.use_nodes or not m.node_tree:
                continue
            bsdf = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
            if bsdf is None or bsdf.inputs["Base Color"].is_linked:
                continue
            saved.append((m, tuple(m.diffuse_color)))
            c = bsdf.inputs["Base Color"].default_value
            m.diffuse_color = (c[0], c[1], c[2], m.diffuse_color[3])
        return saved

    def capture(self, mode="viewport", targets=None, views=None, shading=None, max_size=768):
        """Save a picture of the scene. Returns info dict with 'path'."""
        saved = self.true_colours()
        try:
            # Through the module name, so tests can substitute the capture step.
            return _capture(mode, targets, views, shading, max_size)
        finally:
            for m, colour in saved:
                try:
                    m.diffuse_color = colour
                except ReferenceError:
                    pass

    def choose(self, mode, targets, views, shading, max_size):
        """Draw through a viewport when one can draw, else render."""
        window, area, region = helpers.find_view3d()
        if area is None or bpy.app.background:   # background Blender has layouts but cannot draw them
            return self.render.capture(mode, targets, views, max_size)
        return self.viewport.capture(area, region, mode, targets, views, shading, max_size)


eyes = Eyes()
capture = eyes.capture
_capture = eyes.choose
_true_colours = Eyes.true_colours
_targets = eyes.framing.targets
_bounds = Framing.bounds
_out_path = Framing.out_path
