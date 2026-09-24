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


def material(name, color=(0.8, 0.8, 0.8), roughness=0.5, metallic=0.0,
             emission=None, emission_strength=1.0, alpha=1.0):
    """Get or create a Principled BSDF material. Colours are 0..1 RGB."""
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = m.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
        out = next((n for n in m.node_tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
        if out is None:
            out = m.node_tree.nodes.new("ShaderNodeOutputMaterial")
        m.node_tree.links.new(bsdf.outputs[0], out.inputs[0])
    rgba = (color[0], color[1], color[2], 1.0)
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    if emission is not None:
        bsdf.inputs["Emission Color"].default_value = (emission[0], emission[1], emission[2], 1.0)
        bsdf.inputs["Emission Strength"].default_value = emission_strength
    if alpha < 1.0:
        bsdf.inputs["Alpha"].default_value = alpha
    m.diffuse_color = rgba  # so Solid view colours match
    return m


def assign(target, material_, replace=True):
    """Give an object (or its name) a material (or its name)."""
    target, material_ = obj(target), mat(material_)
    if target.data is None or not hasattr(target.data, "materials"):
        return
    if replace:
        target.data.materials.clear()
    target.data.materials.append(material_)


def collection(name, parent=None):
    """Get or create a collection linked under parent (default: scene root)."""
    found = bpy.data.collections.get(name)
    if found is None:
        found = bpy.data.collections.new(name)
        (col(parent) if parent else bpy.context.scene.collection).children.link(found)
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
    target = obj(target)
    if target.type != "MESH":
        return
    target.data.polygons.foreach_set("use_smooth", [True] * len(target.data.polygons))
    target.data.update()
    if angle_deg is not None:
        select(target)
        with view3d():
            try:
                bpy.ops.object.shade_smooth_by_angle(angle=math.radians(angle_deg))
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
