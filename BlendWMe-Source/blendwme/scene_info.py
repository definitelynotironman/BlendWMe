"""Reading the scene: summaries for the AI, and snapshots to spot changes.

Snapshots are how BlendWMe notices YOUR edits. Before you take control we
take a snapshot. When you hand back, we compare and tell the AI exactly
what you changed, so it builds on your version instead of undoing it.
"""

import math

import bpy
from mathutils import Vector

AUTHOR_KEY = "blendwme_author"   # "ai" or "user", stored on each object
BRAIN_KEY = "blendwme_brain"     # which brain made an AI object: "claude", "codex" or "api"
MAX_OBJECTS = 200


def _r(v, n=3):
    return [round(float(x), n) for x in v]


def world_bounds(obj):
    try:
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    except Exception:
        p = obj.matrix_world.translation
        return _r(p), _r(p)
    lo = [min(c[i] for c in corners) for i in range(3)]
    hi = [max(c[i] for c in corners) for i in range(3)]
    return _r(lo), _r(hi)


def _mesh_counts(obj):
    if obj.type == "MESH" and obj.data:
        return len(obj.data.vertices), len(obj.data.polygons)
    return None


def object_summary(obj):
    lo, hi = world_bounds(obj)
    info = {
        "name": obj.name,
        "type": obj.type,
        "loc": _r(obj.location),
        "rot_deg": [round(math.degrees(a), 1) for a in obj.rotation_euler],
        "scale": _r(obj.scale),
        "dims": _r(obj.dimensions),
        "bounds_min": lo,
        "bounds_max": hi,
    }
    if obj.parent:
        info["parent"] = obj.parent.name
    counts = _mesh_counts(obj)
    if counts:
        info["verts"], info["faces"] = counts
    mats = [s.material.name for s in getattr(obj, "material_slots", []) if s.material]
    if mats:
        info["materials"] = mats
    if getattr(obj, "modifiers", None) and len(obj.modifiers):
        info["modifiers"] = [f"{m.type}:{m.name}" for m in obj.modifiers]
    if obj.hide_get() or obj.hide_viewport:
        info["hidden"] = True
    if obj.animation_data and obj.animation_data.action:
        info["action"] = obj.animation_data.action.name
    author = obj.get(AUTHOR_KEY)
    if author:
        info["made_by"] = author
        brain = obj.get(BRAIN_KEY)
        if author == "ai" and brain:
            info["made_by_brain"] = brain
    return info


def get_scene_info():
    scene = bpy.context.scene
    objs = list(scene.objects)
    view_layer = bpy.context.view_layer
    active = view_layer.objects.active
    info = {
        "blender": bpy.app.version_string,
        "file": bpy.data.filepath or "(unsaved)",
        "scene": scene.name,
        "mode": bpy.context.mode,
        "unit_system": scene.unit_settings.system,
        "render_engine": scene.render.engine,
        "camera": scene.camera.name if scene.camera else None,
        "frame": [scene.frame_start, scene.frame_current, scene.frame_end],
        "active_object": active.name if active else None,
        "selected": [o.name for o in bpy.context.selected_objects][:30],
        "collections": [c.name for c in bpy.data.collections][:40],
        "object_count": len(objs),
        "objects": [object_summary(o) for o in objs[:MAX_OBJECTS]],
    }
    if len(objs) > MAX_OBJECTS:
        info["note"] = f"Only the first {MAX_OBJECTS} objects are listed."
    cube = default_startup_cube()
    if cube:
        info["default_startup_cube"] = (f"'{cube}' is Blender's untouched default startup cube, not the user's work. "
                                        "Delete it first if it would get in the way of the build "
                                        "(D.objects.remove(D.objects['" + cube + "'])).")
    return info


def inventory(limit=30):
    """One line per top-level object the AI made: size, position and materials.

    Shown with every art director review. A leftover from an abandoned first
    attempt (a 1.5 m sphere next to a 12 cm ice cream) is obvious in a list even
    when it dominates the picture (seen live).
    """
    from . import helpers
    lines = []
    for o in bpy.context.scene.objects:
        if o.parent is not None or o.type in {"CAMERA", "LIGHT"} or o.get(AUTHOR_KEY) != "ai":
            continue
        try:
            b = helpers.bounds(o)
        except Exception:
            continue
        mats = sorted({s.material.name for c in [o] + list(o.children_recursive)
                       for s in getattr(c, "material_slots", []) if s.material})
        lines.append(f"- {o.name}: {b.size.x:.2f} x {b.size.y:.2f} x {b.size.z:.2f} m, centre "
                     f"({b.center.x:.2f}, {b.center.y:.2f}, {b.center.z:.2f}), "
                     + (f"materials {', '.join(mats[:4])}" if mats else "NO MATERIAL"))
        if len(lines) >= limit:
            lines.append("- ...")
            break
    return lines


def default_startup_cube():
    """Name of Blender's untouched default cube (2 m, at the origin, 8 vertices), or ''."""
    o = bpy.data.objects.get("Cube")
    if o is None or o.type != "MESH" or o.get(AUTHOR_KEY) or o.name not in bpy.context.scene.objects:
        return ""
    if len(o.data.vertices) != 8 or o.modifiers or o.parent or o.children:
        return ""
    if o.location.length > 1e-4 or any(abs(a) > 1e-4 for a in o.rotation_euler):
        return ""
    if any(abs(d - 2.0) > 1e-4 for d in o.dimensions):
        return ""
    return o.name


def get_object_info(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        close = [o.name for o in bpy.data.objects if name.lower() in o.name.lower()][:10]
        return {"error": f"No object named {name!r}.", "similar": close}
    info = object_summary(obj)
    info["children"] = [c.name for c in obj.children]
    info["collections"] = [c.name for c in obj.users_collection]
    if obj.type == "MESH":
        me = obj.data
        info["mesh"] = {
            "name": me.name,
            "verts": len(me.vertices), "edges": len(me.edges), "faces": len(me.polygons),
            "shade_smooth": any(p.use_smooth for p in me.polygons[:200]),
            "uv_layers": [uv.name for uv in me.uv_layers],
            "shape_keys": [k.name for k in me.shape_keys.key_blocks] if me.shape_keys else [],
            "vertex_groups": [g.name for g in obj.vertex_groups][:40],
        }
    if obj.modifiers:
        mods = []
        for m in obj.modifiers:
            entry = {"name": m.name, "type": m.type, "show_viewport": m.show_viewport}
            for prop in ("levels", "width", "segments", "count", "thickness", "operation",
                         "use_mirror_merge", "angle_limit"):
                if hasattr(m, prop):
                    val = getattr(m, prop)
                    entry[prop] = val if isinstance(val, (int, float, str, bool)) else str(val)
            if getattr(m, "object", None):
                entry["object"] = m.object.name
            mods.append(entry)
        info["modifiers"] = mods
    mats = []
    for slot in getattr(obj, "material_slots", []):
        mat = slot.material
        if not mat:
            continue
        entry = {"name": mat.name}
        if mat.use_nodes and mat.node_tree:
            bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
            if bsdf:
                col = bsdf.inputs["Base Color"].default_value
                entry["base_color"] = _r(col[:3])
                entry["roughness"] = round(bsdf.inputs["Roughness"].default_value, 3)
                entry["metallic"] = round(bsdf.inputs["Metallic"].default_value, 3)
            entry["nodes"] = [n.type for n in mat.node_tree.nodes][:25]
        mats.append(entry)
    if mats:
        info["material_details"] = mats
    if obj.type == "ARMATURE":
        info["bones"] = [b.name for b in obj.data.bones][:80]
    return info


# ----------------------------------------------------------------------
# Snapshots and diffs
# ----------------------------------------------------------------------

def snapshot():
    snap = {}
    for obj in bpy.context.scene.objects:
        if obj.mode == "EDIT":
            try:
                obj.update_from_editmode()  # edit-mode geometry is not synced otherwise
            except Exception:
                pass
        mats = tuple(s.material.name for s in getattr(obj, "material_slots", []) if s.material)
        mods = tuple(f"{m.type}:{m.name}" for m in getattr(obj, "modifiers", []))
        snap[obj.name] = {
            "type": obj.type,
            "loc": tuple(_r(obj.location, 3)),
            "rot": tuple(round(math.degrees(a), 1) for a in obj.rotation_euler),
            "scale": tuple(_r(obj.scale, 3)),
            "dims": tuple(_r(obj.dimensions, 3)),
            "mesh": _mesh_counts(obj),
            "materials": mats,
            "modifiers": mods,
            "parent": obj.parent.name if obj.parent else None,
            "hidden": bool(obj.hide_get() or obj.hide_viewport),
        }
    return snap


_LABELS = {"loc": "moved", "rot": "rotated", "scale": "scaled", "dims": "resized",
           "mesh": "geometry edited", "materials": "materials changed",
           "modifiers": "modifiers changed", "parent": "parent changed",
           "hidden": "visibility changed", "type": "type changed"}


def diff(before, after):
    """Return a list of readable change lines and the names that changed."""
    lines, changed = [], {"added": [], "removed": [], "modified": []}
    for name in after:
        if name not in before:
            changed["added"].append(name)
            a = after[name]
            lines.append(f"+ added {name} ({a['type']}) at {list(a['loc'])}")
    for name in before:
        if name not in after:
            changed["removed"].append(name)
            lines.append(f"- removed {name}")
    for name in after:
        if name not in before:
            continue
        b, a = before[name], after[name]
        parts = []
        for key, label in _LABELS.items():
            if b.get(key) != a.get(key):
                if key in ("loc", "rot", "scale", "dims"):
                    parts.append(f"{label} {list(b[key])} -> {list(a[key])}")
                elif key == "mesh":
                    parts.append(f"{label} (verts/faces {b[key]} -> {a[key]})")
                else:
                    parts.append(f"{label} {b.get(key)} -> {a.get(key)}")
        if parts:
            changed["modified"].append(name)
            lines.append(f"~ {name}: " + "; ".join(parts))
    return lines, changed


def tag_author(names, author):
    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            obj[AUTHOR_KEY] = author
            if author != "ai" and BRAIN_KEY in obj:
                del obj[BRAIN_KEY]


def tag_brain(names, brain):
    """Sign AI objects with the brain that made them (the journal shows who made what)."""
    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is not None and obj.get(AUTHOR_KEY) == "ai":
            obj[BRAIN_KEY] = brain


def makers():
    """{object name: "claude" | "codex" | "api" | "ai" | "user"} for every tagged object in the scene."""
    out = {}
    for obj in bpy.context.scene.objects:
        author = obj.get(AUTHOR_KEY)
        if author:
            out[obj.name] = obj.get(BRAIN_KEY, author) if author == "ai" else author
    return out
