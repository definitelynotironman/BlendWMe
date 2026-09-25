"""Small helpers the AI can use inside run_python as `bwm`.

They exist for the jobs open models most often get wrong in Blender's API
(materials, context for operators, putting things on the ground). The AI can
still use the full bpy API for everything else.
"""

import contextlib
import math

import bpy
from mathutils import Vector


# ----------------------------------------------------------------------
# Forgiving lookups: every helper accepts an object OR its name
# ----------------------------------------------------------------------

def _get(collection, value, kind):
    if isinstance(value, str):
        found = collection.get(value)
        if found is None:
            close = [n for n in collection.keys() if value.lower() in n.lower()][:5]
            raise KeyError(f"No {kind} named {value!r}." + (f" Similar: {close}" if close else ""))
        return found
    return value


def obj(value):
    """D.objects[name] or the object itself."""
    return _get(bpy.data.objects, value, "object")


def col(value):
    """D.collections[name] or the collection itself."""
    return _get(bpy.data.collections, value, "collection")


def mat(value):
    """D.materials[name] or the material itself."""
    return _get(bpy.data.materials, value, "material")


def find_view3d():
    """Return (window, area, region) of the biggest 3D viewport, or Nones."""
    best = (None, None, None)
    if bpy.app.background:
        # Background Blender keeps screen layouts but cannot draw them. Borrowing
        # one for an operator crashes Blender in transform macros (extrude + move).
        return best
    best_size = -1
    wm = bpy.context.window_manager
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            size = area.width * area.height
            if region and size > best_size:
                best, best_size = (window, area, region), size
    return best


@contextlib.contextmanager
def view3d():
    """Context for bpy.ops calls that need a 3D viewport."""
    window, area, region = find_view3d()
    if window is None:
        yield
        return
    with bpy.context.temp_override(window=window, area=area, region=region,
                                   screen=window.screen):
        yield


# ----------------------------------------------------------------------
# Replacing the AI's own earlier objects (so re-running a step updates it)
# ----------------------------------------------------------------------
STEP_START = set()   # pointers of objects that existed when the current step started
_REPLACED = []       # (object, original name) set aside during the current step
NOTES = []           # short notes for the model about what the helpers did


def begin_step():
    """Called by the executor before a step runs."""
    STEP_START.clear()
    STEP_START.update(o.as_pointer() for o in bpy.data.objects)
    _REPLACED.clear()
    NOTES.clear()


def end_step(ok):
    """Called after a step. On success old versions are deleted, on failure restored."""
    for old, name in reversed(_REPLACED):
        try:
            if ok:
                _delete_tree(old)
            else:
                old.name = name
                old.hide_set(False)
                old.hide_viewport = False
        except ReferenceError:
            pass
    _REPLACED.clear()
    STEP_START.clear()


def _delete_tree(old):
    for child in list(old.children):
        if child.get("blendwme_author") == "user":
            world = child.matrix_world.copy()
            child.parent = None
            child.matrix_world = world
        else:
            _delete_tree(child)
    data = old.data
    bpy.data.objects.remove(old)
    if data is not None and data.users == 0:
        for pool in (bpy.data.meshes, bpy.data.curves, bpy.data.lights, bpy.data.cameras):
            if pool.get(data.name) == data:
                pool.remove(data)
                break


def claim_name(name):
    """Make `name` free for a new object.

    An object of that name made by the AI in an EARLIER step is set aside and
    deleted when this step succeeds (restored if it fails). Objects made in
    this same step, and anything the user made or edited, are never touched:
    the new object then simply gets Blender's usual .001 name.
    """
    old = bpy.data.objects.get(name)
    if old is None or old.as_pointer() not in STEP_START or old.get("blendwme_author") != "ai":
        return
    _REPLACED.append((old, name))
    old.name = name + ".replaced"
    old.hide_set(True)
    old.hide_viewport = True
    note = f"Replaced your earlier {name!r} with the new one."
    if note not in NOTES:
        NOTES.append(note)


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


def as_material(value, fallback_name="Material", fallback_color=(0.8, 0.8, 0.8)):
    """A material from a material, a material name, a colour or None (-> fallback)."""
    if value is None:
        return material(fallback_name, fallback_color)
    if isinstance(value, bpy.types.Material):
        return value
    if isinstance(value, str) and value in bpy.data.materials:
        return bpy.data.materials[value]
    rgba = color(value, fallback_color)
    return material("Color_" + "".join(f"{int(round(c * 255)):02x}" for c in rgba[:3]), rgba)


def material(name, color=(0.8, 0.8, 0.8), roughness=0.5, metallic=0.0,
             emission=None, emission_strength=1.0, alpha=1.0):
    """Get or create a Principled BSDF material.

    color can be (r, g, b) or (r, g, b, a) in 0..1, 0-255 values, '#rrggbb' or a name like 'red'.
    """
    rgba_in = globals()["color"](color)     # the argument hides the color() function here
    roughness, metallic = float(roughness), float(metallic)
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
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
        bsdf.inputs["Emission Color"].default_value = globals()["color"](emission)
        bsdf.inputs["Emission Strength"].default_value = float(emission_strength)
    if alpha < 1.0:
        bsdf.inputs["Alpha"].default_value = alpha
        if hasattr(m, "surface_render_method"):      # smooth glass instead of dithered dots
            m.surface_render_method = "BLENDED"
    m.diffuse_color = rgba  # so Solid view colours match
    return m


def _place(new_obj, collection_, material_):
    target = col(collection_) if collection_ else bpy.context.scene.collection
    target.objects.link(new_obj)
    if material_ is not None:
        assign(new_obj, as_material(material_, new_obj.name + "_Mat"))
    return new_obj


def box(name, size=(1, 1, 1), location=(0, 0, 0), collection=None, material=None, bevel=0.0):
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


def mesh(name, verts, faces, location=(0, 0, 0), collection=None, material=None):
    """A mesh object from vertex positions and faces (lists of vertex indices).

    Faces should wind counter-clockwise seen from outside. Normals are fixed automatically.
    """
    claim_name(name)
    data = bpy.data.meshes.new(name)
    data.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in faces])
    data.validate()
    data.update()
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(data)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(data)
    bm.free()
    new_obj = bpy.data.objects.new(name, data)
    new_obj.location = tuple(float(v) for v in location)
    return _place(new_obj, collection, material)


def assign(target, material_, replace=True):
    """Give an object (or its name) a material (or its name, or just a colour)."""
    target = obj(target)
    material_ = as_material(material_, target.name + "_Mat")
    if target.data is None or not hasattr(target.data, "materials"):
        return
    if replace:
        target.data.materials.clear()
    target.data.materials.append(material_)


def collection(name, parent=None):
    """Get or create a collection, and make sure it is part of THIS scene.

    A collection can still exist in the file after it was removed from the
    scene. Objects put there are invisible, so we link it back.
    """
    found = bpy.data.collections.get(name)
    if found is None:
        found = bpy.data.collections.new(name)
    scene = bpy.context.scene
    if found != scene.collection and found not in scene.collection.children_recursive:
        (col(parent) if parent else scene.collection).children.link(found)
    return found


def move_to(target, destination):
    """Move an object (or name) into a collection (or name)."""
    target, destination = obj(target), col(destination)
    for c in list(target.users_collection):
        c.objects.unlink(target)
    destination.objects.link(target)


def select(*objs, active=None):
    objs = [obj(o) for o in objs]
    active = obj(active) if active else None
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    if active or objs:
        bpy.context.view_layer.objects.active = active or objs[0]


def ground(target, z=0.0):
    """Move an object so its lowest point sits at height z."""
    target = obj(target)
    bpy.context.view_layer.update()
    low = min((target.matrix_world @ Vector(c)).z for c in target.bound_box)
    target.location.z += z - low


def apply_transforms(target, location=False, rotation=False, scale=True):
    target = obj(target)
    if target.mode != "OBJECT":
        with view3d():
            bpy.ops.object.mode_set(mode="OBJECT")
    select(target)
    with view3d():
        bpy.ops.object.transform_apply(location=location, rotation=rotation, scale=scale)


def shade_smooth(target, angle_deg=None):
    """Smooth shading. With angle_deg, edges sharper than that angle stay crisp."""
    target = obj(target)
    if target.type != "MESH":
        return
    target.data.polygons.foreach_set("use_smooth", [True] * len(target.data.polygons))
    target.data.update()
    if angle_deg is not None:
        smooth_by_angle(target, angle_deg)


def smooth_by_angle(target, angle_deg=30):
    """Mark edges sharper than angle_deg as sharp, so smooth shading keeps hard corners."""
    target = obj(target)
    if target.type != "MESH":
        return
    try:
        target.data.set_sharp_from_angle(angle=math.radians(float(angle_deg)))
    except (AttributeError, TypeError):   # older Blender: the operator does the same job
        select(target)
        with view3d():
            try:
                bpy.ops.object.shade_smooth_by_angle(angle=math.radians(float(angle_deg)))
            except Exception:
                pass


def frame(*objs):
    """Point the viewport at objects (nice in Watch mode)."""
    if objs:
        select(*objs)
    with view3d():
        try:
            bpy.ops.view3d.view_selected(use_all_regions=False)
        except Exception:
            pass


def public_names():
    """The bwm functions the model should know about (the kit, without internals)."""
    hidden = {"begin_step", "end_step", "claim_name", "find_view3d", "view3d", "obj", "col", "mat",
              "subdivide_mod", "num", "vec", "size3", "axis_name", "public_names", "smooth_by_angle"}
    package = __name__.rsplit(".", 1)[0]
    out = []
    for n, fn in globals().items():
        if n.startswith("_") or not n[0].islower() or n in hidden or not callable(fn):
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
