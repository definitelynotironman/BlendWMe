# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""Small helpers the AI can use inside run_python as `bwm`.

They exist for the jobs open models most often get wrong in Blender's API
(materials, context for operators, putting things on the ground). The AI can
still use the full bpy API for everything else.

Structure: ``StepLedger`` tracks what one run_python step replaced so it can
be committed or rolled back; ``MaterialKit``, ``ObjectKit`` and ``CameraKit``
hold the helpers. The kit in shapes.py and furniture.py is imported at the
bottom, so every helper is reachable as ``bwm.<name>`` from one namespace.
The module-level names are bound methods of the shared objects.
"""

import contextlib
import math
from itertools import islice

import bmesh
import bpy
from mathutils import Vector

__author__ = "definitelynotironman"


# ----------------------------------------------------------------------
# Forgiving lookups: every helper accepts an object OR its name
# ----------------------------------------------------------------------
class Lookup:
    """Resolves a datablock given as itself or by name, with "did you mean" hints."""

    @staticmethod
    def get(collection, value, kind):
        """collection[value] for a name (KeyError with similar names), else value itself."""
        if isinstance(value, str):
            found = collection.get(value)
            if found is None:
                close = [n for n in collection.keys() if value.lower() in n.lower()][:5]
                raise KeyError(f"No {kind} named {value!r}." + (f" Similar: {close}" if close else ""))
            return found
        return value

    def obj(self, value):
        """D.objects[name] or the object itself."""
        return self.get(bpy.data.objects, value, "object")

    def col(self, value):
        """D.collections[name] or the collection itself."""
        return self.get(bpy.data.collections, value, "collection")

    def mat(self, value):
        """D.materials[name] or the material itself."""
        return self.get(bpy.data.materials, value, "material")


class ViewportContext:
    """Finds a 3D viewport so bpy.ops calls that need one can run from a tool call."""

    @staticmethod
    def find_view3d():
        """Return (window, area, region) of the biggest 3D viewport, or Nones."""
        best = (None, None, None)
        if bpy.app.background:
            # Background Blender keeps screen layouts but cannot draw them. Borrowing
            # one for an operator crashes Blender in transform macros (extrude + move).
            return best
        best_size = -1
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != "VIEW_3D":
                    continue
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                size = area.width * area.height
                if region and size > best_size:
                    best, best_size = (window, area, region), size
        return best

    @contextlib.contextmanager
    def view3d(self):
        """Context for bpy.ops calls that need a 3D viewport."""
        window, area, region = self.find_view3d()
        if window is None:
            yield
            return
        with bpy.context.temp_override(window=window, area=area, region=region, screen=window.screen):
            yield


# ----------------------------------------------------------------------
# Replacing the AI's own earlier objects (so re-running a step updates it)
# ----------------------------------------------------------------------
class StepLedger:
    """What one run_python step replaced, so the step can be committed or undone.

    ``step_start`` holds pointers of objects that existed when the step began;
    ``replaced`` holds (object, original name) set aside during the step;
    ``notes`` are short messages for the model about what the helpers did.
    The three containers are mutated in place (never rebound), because other
    modules hold references to them.
    """

    def __init__(self):
        """Empty ledger."""
        self.step_start = set()
        self.replaced = []
        self.notes = []

    def begin_step(self):
        """Called by the executor before a step runs."""
        self.step_start.clear()
        self.step_start.update(o.as_pointer() for o in bpy.data.objects)
        self.replaced.clear()
        self.notes.clear()

    def end_step(self, ok):
        """Called after a step. On success old versions are deleted, on failure restored."""
        for old, name in reversed(self.replaced):
            try:
                if ok:
                    self.delete_tree(old)
                else:
                    old.name = name
                    old.hide_viewport = False
                    try:
                        old.hide_set(False)
                    except RuntimeError:
                        pass
            except ReferenceError:
                pass
        self.replaced.clear()
        self.step_start.clear()

    def delete_tree(self, old):
        """Delete an AI object and its AI children; keep user/protected children where they are."""
        for child in list(old.children):
            if child.get("blendwme_author") != "ai" or child.get("blendwme_protected", False):
                world = child.matrix_world.copy()
                child.parent = None
                child.matrix_world = world
            else:
                self.delete_tree(child)
        data = old.data
        bpy.data.objects.remove(old)
        if data is not None and data.users == 0:
            for pool in (bpy.data.meshes, bpy.data.curves, bpy.data.lights, bpy.data.cameras):
                if pool.get(data.name) == data:
                    pool.remove(data)
                    break

    def claim_name(self, name):
        """Make `name` free for a new object.

        An object of that name made by the AI in an EARLIER step is set aside and
        deleted when this step succeeds (restored if it fails). Objects made in
        this same step, and anything the user made or edited, are never touched:
        the new object then simply gets Blender's usual .001 name.
        """
        old = bpy.data.objects.get(name)
        if (old is None or old.as_pointer() not in self.step_start or old.get("blendwme_author") != "ai" or
                old.get("blendwme_protected", False)):
            return
        self.replaced.append((old, name))
        old.name = name + ".replaced"
        try:
            old.hide_set(True)      # fails for objects outside the active view layer (seen live)
        except RuntimeError:
            pass
        old.hide_viewport = True
        note = f"Replaced your earlier {name!r} with the new one."
        if note not in self.notes:
            self.notes.append(note)


# ----------------------------------------------------------------------
# Colours and materials
# ----------------------------------------------------------------------
COLOR_NAMES = {
    "white": (0.9, 0.9, 0.9), "black": (0.02, 0.02, 0.02), "grey": (0.5, 0.5, 0.5), "gray": (0.5, 0.5, 0.5),
    "red": (0.7, 0.08, 0.06), "green": (0.15, 0.5, 0.15), "blue": (0.1, 0.25, 0.7), "yellow": (0.9, 0.75, 0.1),
    "orange": (0.9, 0.4, 0.08), "brown": (0.4, 0.24, 0.12), "wood": (0.55, 0.35, 0.18), "pink": (0.9, 0.5, 0.6),
    "purple": (0.4, 0.15, 0.55), "cream": (0.92, 0.85, 0.7), "beige": (0.8, 0.7, 0.55), "stone": (0.5, 0.48, 0.45),
    "glass": (0.05, 0.08, 0.12), "gold": (0.85, 0.65, 0.2), "silver": (0.75, 0.75, 0.78),
}


class MaterialKit:
    """Colours and Principled BSDF materials that are safe to edit.

    A material the user made, or one shared by several users, is never
    overwritten in place: ``material`` copies it first (clone on write), so an
    AI recolour of "Wood" cannot change the user's own wooden objects.
    """

    def __init__(self, lookup):
        """Use lookup to resolve objects given by name."""
        self.lookup = lookup

    @staticmethod
    def color(value, default=(0.8, 0.8, 0.8)):
        """(r, g, b, a) in 0..1 from (r, g, b), (r, g, b, a), 0-255 values, '#rrggbb' or a name."""
        if value is None:
            value = default
        if isinstance(value, str):
            text = value.strip().lower()
            if text.startswith("#") and len(text) in (7, 9):
                vals = [int(text[i:i + 2], 16) / 255 for i in range(1, len(text), 2)]
                return tuple(vals + [1.0] * (4 - len(vals)))[:4]
            if text in COLOR_NAMES:
                return tuple(COLOR_NAMES[text]) + (1.0,)
            value = [p for p in text.replace("(", " ").replace(")", " ").replace(",", " ").split() if p]
        if isinstance(value, (int, float)):
            value = (value,) * 3
        vals = [float(v) for v in value][:4]
        if len(vals) < 3:
            vals = (vals + [vals[-1] if vals else 0.8] * 3)[:3]
        if any(v > 1.0 for v in vals[:3]):        # 0-255 colours
            vals = [v / 255.0 for v in vals[:3]] + vals[3:]
        if len(vals) == 3:
            vals.append(1.0)
        return tuple(vals)

    def as_material(self, value, fallback_name="Material", fallback_color=(0.8, 0.8, 0.8)):
        """A material from a material, a material name, a colour or None (-> fallback)."""
        if value is None:
            return material(fallback_name, fallback_color)
        if isinstance(value, bpy.types.Material):
            return value
        if isinstance(value, str) and value in bpy.data.materials:
            return bpy.data.materials[value]
        rgba = self.color(value, fallback_color)
        return material("Color_" + "".join(f"{int(round(c * 255)):02x}" for c in rgba[:3]), rgba)

    @staticmethod
    def _protected(m):
        """True when m is marked protected or used by a user-made or protected object."""
        return m.get("blendwme_protected", False) or any(
            slot.material == m and (o.get("blendwme_author") != "ai" or o.get("blendwme_protected", False))
            for o in bpy.data.objects for slot in o.material_slots)

    def material(self, name, color=(0.8, 0.8, 0.8), roughness=0.5, metallic=0.0,
                 emission=None, emission_strength=1.0, alpha=1.0):
        """Get or create a Principled BSDF material.

        color can be (r, g, b) or (r, g, b, a) in 0..1, 0-255 values, '#rrggbb' or a name like 'red'.
        """
        rgba_in = self.color(color)
        roughness, metallic = float(roughness), float(metallic)
        m = bpy.data.materials.get(name)
        if m is None or not m.get("blendwme_owned", False) or m.users > 1 or self._protected(m):
            m = m.copy() if m is not None else bpy.data.materials.new(name)
            m["blendwme_owned"] = True
        m.use_nodes = True
        bsdf = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            bsdf = m.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
            out = next((n for n in m.node_tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
            if out is None:
                out = m.node_tree.nodes.new("ShaderNodeOutputMaterial")
            m.node_tree.links.new(bsdf.outputs[0], out.inputs[0])
        alpha = min(float(alpha), rgba_in[3])     # RGBA accepted too; the 4th value becomes alpha
        rgba = (rgba_in[0], rgba_in[1], rgba_in[2], 1.0)
        bsdf.inputs["Base Color"].default_value = rgba
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
        if emission is not None:
            bsdf.inputs["Emission Color"].default_value = self.color(emission)
            bsdf.inputs["Emission Strength"].default_value = float(emission_strength)
        if alpha < 1.0:
            bsdf.inputs["Alpha"].default_value = alpha
            if hasattr(m, "surface_render_method"):      # smooth glass instead of dithered dots
                m.surface_render_method = "BLENDED"
        m.diffuse_color = rgba  # so Solid view colours match
        return m

    def assign(self, target, material_, replace=True):
        """Give an object (or its name) a material (or its name, or just a colour)."""
        target = self.lookup.obj(target)
        material_ = self.as_material(material_, target.name + "_Mat")
        if target.data is None or not hasattr(target.data, "materials"):
            return
        if target.data.users > 1:
            target.data = target.data.copy()
        if replace:
            target.data.materials.clear()
        target.data.materials.append(material_)


# ----------------------------------------------------------------------
# Objects, collections and selection
# ----------------------------------------------------------------------
class ObjectKit:
    """Creating meshes and moving objects between collections, selection and shading."""

    def __init__(self, lookup, ledger, materials, viewport):
        """Wire the kit to the shared lookup, step ledger, material kit and viewport context."""
        self.lookup = lookup
        self.ledger = ledger
        self.materials = materials
        self.viewport = viewport

    def place(self, new_obj, collection_, material_):
        """Link a new object into its collection (or the scene) and assign its material."""
        target = self.lookup.col(collection_) if collection_ else bpy.context.scene.collection
        target.objects.link(new_obj)
        if material_ is not None:
            assign(new_obj, self.materials.as_material(material_, new_obj.name + "_Mat"))
        return new_obj

    def box(self, name, size=(1, 1, 1), location=(0, 0, 0), collection=None, material=None, bevel=0.0):
        """A box with real dimensions (scale stays 1, so bevels and modifiers are even).

        size is (x, y, z) in metres, location is the CENTRE. bevel adds a Bevel modifier of that width.
        """
        from .shapes import size3, vec
        sx, sy, sz = (v / 2 for v in size3(size))
        location = vec(location)
        verts = [(-sx, -sy, -sz), (sx, -sy, -sz), (sx, sy, -sz), (-sx, sy, -sz),
                 (-sx, -sy, sz), (sx, -sy, sz), (sx, sy, sz), (-sx, sy, sz)]
        faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
        new_obj = mesh(name, verts, faces, location=location, collection=collection, material=material)
        if bevel:
            m = new_obj.modifiers.new("Bevel", "BEVEL")
            m.width = float(bevel)
            m.segments = 2
            m.limit_method = "ANGLE"
        return new_obj

    def mesh(self, name, verts, faces, location=(0, 0, 0), collection=None, material=None):
        """A mesh object from vertex positions and faces (lists of vertex indices).

        Faces should wind counter-clockwise seen from outside. Normals are fixed automatically.
        """
        from .shapes import MAX_HELPER_VERTICES, MAX_MODIFIER_FACES, _geometry_budget
        verts = list(islice(iter(verts), MAX_HELPER_VERTICES + 1))
        faces = list(islice(iter(faces), MAX_MODIFIER_FACES + 1))
        _geometry_budget(len(verts))
        if len(faces) > MAX_MODIFIER_FACES:
            raise ValueError("This helper mesh exceeds the face budget. Split it into smaller steps.")
        self.ledger.claim_name(name)
        data = bpy.data.meshes.new(name)
        data.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in faces])
        data.validate()
        data.update()
        bm = bmesh.new()
        bm.from_mesh(data)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(data)
        bm.free()
        new_obj = bpy.data.objects.new(name, data)
        new_obj.location = tuple(float(v) for v in location)
        return self.place(new_obj, collection, material)

    def collection(self, name, parent=None):
        """Get or create a collection, and make sure it is part of THIS scene.

        A collection can still exist in the file after it was removed from the
        scene. Objects put there are invisible, so we link it back.
        """
        found = bpy.data.collections.get(name)
        if found is None:
            found = bpy.data.collections.new(name)
        scene = bpy.context.scene
        if found != scene.collection and found not in scene.collection.children_recursive:
            (self.lookup.col(parent) if parent else scene.collection).children.link(found)
        return found

    def move_to(self, target, destination):
        """Move an object (or name) into a collection (or name)."""
        target, destination = self.lookup.obj(target), self.lookup.col(destination)
        for c in list(target.users_collection):
            c.objects.unlink(target)
        destination.objects.link(target)

    def select(self, *objs, active=None):
        """Select exactly these objects (by object or name); the first one becomes active."""
        objs = [self.lookup.obj(o) for o in objs]
        active = self.lookup.obj(active) if active else None
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        for o in objs:
            o.select_set(True)
        if active or objs:
            bpy.context.view_layer.objects.active = active or objs[0]

    def ground(self, target, z=0.0):
        """Move an object so its lowest point sits at height z."""
        target = self.lookup.obj(target)
        bpy.context.view_layer.update()
        low = min((target.matrix_world @ Vector(c)).z for c in target.bound_box)
        target.location.z += z - low

    def apply_transforms(self, target, location=False, rotation=False, scale=True):
        """Apply (bake) an object's scale, and optionally rotation and location, into its data."""
        target = self.lookup.obj(target)
        if target.mode != "OBJECT":
            with self.viewport.view3d():
                bpy.ops.object.mode_set(mode="OBJECT")
        self.select(target)
        with self.viewport.view3d():
            bpy.ops.object.transform_apply(location=location, rotation=rotation, scale=scale)

    def shade_smooth(self, target, angle_deg=None):
        """Smooth shading. With angle_deg, edges sharper than that angle stay crisp."""
        target = self.lookup.obj(target)
        if target.type != "MESH":
            return
        target.data.polygons.foreach_set("use_smooth", [True] * len(target.data.polygons))
        target.data.update()
        if angle_deg is not None:
            self.smooth_by_angle(target, angle_deg)

    def smooth_by_angle(self, target, angle_deg=30):
        """Mark edges sharper than angle_deg as sharp, so smooth shading keeps hard corners."""
        target = self.lookup.obj(target)
        if target.type != "MESH":
            return
        try:
            target.data.set_sharp_from_angle(angle=math.radians(float(angle_deg)))
        except (AttributeError, TypeError):   # older Blender: the operator does the same job
            self.select(target)
            with self.viewport.view3d():
                try:
                    bpy.ops.object.shade_smooth_by_angle(angle=math.radians(float(angle_deg)))
                except Exception:
                    pass

    def frame(self, *objs):
        """Point the viewport at objects (useful while demonstrating)."""
        if objs:
            self.select(*objs)
        with self.viewport.view3d():
            try:
                bpy.ops.view3d.view_selected(use_all_regions=False)
            except Exception:
                pass


# ----------------------------------------------------------------------
# Camera help (Teach "do it for me", and any brief that needs a shot)
# ----------------------------------------------------------------------
_FRAMEABLE = {"MESH", "CURVE", "SURFACE", "META", "FONT", "GPENCIL", "GREASEPENCIL", "CURVES", "POINTCLOUD", "VOLUME"}


class CameraKit:
    """Aims and frames the scene camera the way an artist would."""

    def __init__(self, lookup, viewport):
        """Use lookup for names and viewport to read the user's current view."""
        self.lookup = lookup
        self.viewport = viewport

    def camera(self, camera=None):
        """The camera to work with: the one named, else the scene camera, else a new one."""
        scene = bpy.context.scene
        if camera is not None:
            return self.lookup.obj(camera)
        if scene.camera is not None:
            return scene.camera
        cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
        scene.collection.objects.link(cam)
        cam.location = (7.4, -6.9, 5.0)
        cam.rotation_euler = (math.radians(63), 0.0, math.radians(47))
        scene.camera = cam
        bpy.context.view_layer.update()      # so matrix_world is current
        return cam

    @staticmethod
    def half_angle(cam):
        """Half of the camera's narrower field of view, in radians."""
        render = bpy.context.scene.render
        aspect = (render.resolution_x * render.pixel_aspect_x) / max(1e-6, render.resolution_y * render.pixel_aspect_y)
        wide = cam.data.angle
        if cam.data.sensor_fit == "VERTICAL":
            horizontal, vertical = 2 * math.atan(math.tan(wide / 2) * aspect), wide
        elif aspect >= 1.0 or cam.data.sensor_fit == "HORIZONTAL":
            horizontal, vertical = wide, 2 * math.atan(math.tan(wide / 2) / aspect)
        else:
            horizontal, vertical = 2 * math.atan(math.tan(wide / 2) * aspect), wide
        return min(horizontal, vertical) / 2

    def camera_to_view(self, camera=None):
        """Put the camera exactly where the user is looking in the 3D Viewport (like Ctrl+Alt+Numpad 0).
        Without a viewport (background Blender) it frames the whole scene instead."""
        cam = self.camera(camera)
        window, area, region = self.viewport.find_view3d()
        if area is None:
            return self.frame_camera(camera=cam)
        rv3d = area.spaces.active.region_3d
        if rv3d.view_perspective != "CAMERA":
            cam.matrix_world = rv3d.view_matrix.inverted()
        bpy.context.scene.camera = cam
        rv3d.view_perspective = "CAMERA"
        bpy.context.view_layer.update()
        return cam

    def frame_camera(self, *objs, camera=None, margin=1.15):
        """Aim the camera at objects (default: everything visible) and move it back until they fit,
        keeping the direction it already looks from. margin 1.15 leaves a little air around them."""
        cam = self.camera(camera)
        scene = bpy.context.scene
        targets = [self.lookup.obj(o) for o in objs] or [o for o in scene.objects
                                                          if o.type in _FRAMEABLE and o.visible_get()]
        if not targets:
            raise ValueError("Nothing to frame: the scene has no visible objects. Pass the objects to frame.")
        points = [o.matrix_world @ Vector(c) for o in targets for c in o.bound_box]
        low = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
        high = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
        centre = (low + high) / 2
        radius = max(0.05, max((p - centre).length for p in points))
        back = cam.matrix_world.to_quaternion() @ Vector((0.0, 0.0, 1.0))    # the camera looks along its -Z
        if back.length < 1e-6:
            back = Vector((0.6, -0.65, 0.45))
        distance = radius / math.sin(self.half_angle(cam)) * float(margin)
        cam.location = centre + back.normalized() * distance
        cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
        cam.data.clip_end = max(cam.data.clip_end, distance + radius * 4)
        scene.camera = cam
        bpy.context.view_layer.update()
        return cam

    def focal_length(self, mm, *objs, camera=None, reframe=True):
        """Set the lens in millimetres (35 wide, 50 natural, 85 portrait) and, by default,
        move the camera so the subject still fits the frame."""
        cam = self.camera(camera)
        cam.data.lens = max(1.0, float(mm))
        if reframe:
            self.frame_camera(*objs, camera=cam)
        return cam


# ----------------------------------------------------------------------
# The shared objects and the bwm names
# ----------------------------------------------------------------------
lookup = Lookup()
viewport = ViewportContext()
ledger = StepLedger()
materials = MaterialKit(lookup)
objects = ObjectKit(lookup, ledger, materials, viewport)
cameras = CameraKit(lookup, viewport)

obj, col, mat = lookup.obj, lookup.col, lookup.mat
_get = Lookup.get
find_view3d, view3d = ViewportContext.find_view3d, viewport.view3d
STEP_START, _REPLACED, NOTES = ledger.step_start, ledger.replaced, ledger.notes
begin_step, end_step, claim_name = ledger.begin_step, ledger.end_step, ledger.claim_name
_delete_tree = ledger.delete_tree
color, as_material, material, assign = MaterialKit.color, materials.as_material, materials.material, materials.assign
_place, box, mesh = objects.place, objects.box, objects.mesh
collection, move_to, select, ground = objects.collection, objects.move_to, objects.select, objects.ground
apply_transforms, shade_smooth, smooth_by_angle, frame = (objects.apply_transforms, objects.shade_smooth,
                                                          objects.smooth_by_angle, objects.frame)
_camera, _half_angle = cameras.camera, CameraKit.half_angle
camera_to_view, frame_camera, focal_length = cameras.camera_to_view, cameras.frame_camera, cameras.focal_length


def public_names():
    """The bwm functions the model should know about (the kit, without internals)."""
    hidden = {"begin_step", "end_step", "claim_name", "find_view3d", "view3d", "obj", "col", "mat",
              "subdivide_mod", "num", "vec", "size3", "axis_name", "public_names", "smooth_by_angle"}
    package = __name__.rsplit(".", 1)[0]
    out = []
    for n, fn in globals().items():
        if n.startswith("_") or not n[0].islower() or n in hidden or not callable(fn) or isinstance(fn, type):
            continue
        if getattr(fn, "__module__", "").startswith(package):
            out.append(n)
    return sorted(out)


# ----------------------------------------------------------------------
# The modelling kit (see shapes.py and furniture.py), all available as bwm.<name>
# ----------------------------------------------------------------------
from .shapes import *     # noqa: E402,F401,F403
from .furniture import *  # noqa: E402,F401,F403

from .shapes import forgiving as _forgiving  # noqa: E402

box = _forgiving(box)
mesh = _forgiving(mesh)
material = _forgiving(material)
