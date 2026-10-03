# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""Reading the scene: summaries for the AI, and snapshots to spot changes.

Snapshots are how BlendWMe notices YOUR edits. Before you take control we
take a snapshot. When you hand back, we compare and tell the AI exactly
what you changed, so it builds on your version instead of undoing it.

Structure: ``SceneReader`` writes the summaries the AI reads;
``SceneSnapshots`` takes identity-aware snapshots and diffs them;
``Authorship`` records who made and last edited each object.
"""

import hashlib
import math
import uuid

import bpy
from mathutils import Vector

__author__ = "definitelynotironman"

AUTHOR_KEY = "blendwme_author"   # "ai" or "user", stored on each object
BRAIN_KEY = "blendwme_brain"     # which brain made an AI object: "claude", "codex" or "api"
MAX_OBJECTS = 200
_CHANGE_LIMITS = ("Large mesh domains and animation curves are sampled. Custom attributes, drivers, simulations "
                  "and external asset edits are not fully tracked; inspect them explicitly before changing them.")


def _r(v, n=3):
    """A list of floats rounded to n places."""
    return [round(float(x), n) for x in v]


class SceneReader:
    """Readable facts about the scene and its objects, for the AI."""

    @staticmethod
    def world_bounds(obj):
        """(min, max) world corners of an object's bounding box, rounded."""
        try:
            corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        except Exception:
            p = obj.matrix_world.translation
            return _r(p), _r(p)
        lo = [min(c[i] for c in corners) for i in range(3)]
        hi = [max(c[i] for c in corners) for i in range(3)]
        return _r(lo), _r(hi)

    @staticmethod
    def mesh_counts(obj):
        """(vertices, faces) of a mesh object, else None."""
        if obj.type == "MESH" and obj.data:
            return len(obj.data.vertices), len(obj.data.polygons)
        return None

    def object_summary(self, obj):
        """One object's transform, size, materials, modifiers and authorship."""
        lo, hi = self.world_bounds(obj)
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
        counts = self.mesh_counts(obj)
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

    def get_scene_info(self):
        """The scene overview the AI reads before it works (get_scene_info tool)."""
        scene = bpy.context.scene
        objs = list(scene.objects)
        active = bpy.context.view_layer.objects.active
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
            "objects": [self.object_summary(o) for o in objs[:MAX_OBJECTS]],
            "change_detection_limits": _CHANGE_LIMITS,
        }
        if len(objs) > MAX_OBJECTS:
            info["note"] = f"Only the first {MAX_OBJECTS} objects are listed."
        cube = self.default_startup_cube()
        if cube:
            info["default_startup_cube"] = (f"'{cube}' is Blender's untouched default startup cube, not the user's "
                                            "work. Delete it first if it would get in the way of the build "
                                            "(D.objects.remove(D.objects['" + cube + "'])).")
        return info

    @staticmethod
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

    @staticmethod
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

    def get_object_info(self, name):
        """Detailed facts about one object: mesh, modifiers, materials, bones."""
        obj = bpy.data.objects.get(name)
        if obj is None:
            close = [o.name for o in bpy.data.objects if name.lower() in o.name.lower()][:10]
            return {"error": f"No object named {name!r}.", "similar": close}
        info = self.object_summary(obj)
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
            info["modifiers"] = [self._modifier_entry(m) for m in obj.modifiers]
        mats = [self._material_entry(slot.material) for slot in getattr(obj, "material_slots", []) if slot.material]
        if mats:
            info["material_details"] = mats
        if obj.type == "ARMATURE":
            info["bones"] = [b.name for b in obj.data.bones][:80]
        return info

    @staticmethod
    def _modifier_entry(m):
        """The main settings of one modifier as plain values."""
        entry = {"name": m.name, "type": m.type, "show_viewport": m.show_viewport}
        for prop in ("levels", "width", "segments", "count", "thickness", "operation",
                     "use_mirror_merge", "angle_limit"):
            if hasattr(m, prop):
                val = getattr(m, prop)
                entry[prop] = val if isinstance(val, (int, float, str, bool)) else str(val)
        if getattr(m, "object", None):
            entry["object"] = m.object.name
        return entry

    @staticmethod
    def _material_entry(mat):
        """A material's Principled values and node types."""
        entry = {"name": mat.name}
        if mat.use_nodes and mat.node_tree:
            bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
            if bsdf:
                col = bsdf.inputs["Base Color"].default_value
                entry["base_color"] = _r(col[:3])
                entry["roughness"] = round(bsdf.inputs["Roughness"].default_value, 3)
                entry["metallic"] = round(bsdf.inputs["Metallic"].default_value, 3)
            entry["nodes"] = [n.type for n in mat.node_tree.nodes][:25]
        return entry


# ----------------------------------------------------------------------
# Snapshots and diffs
# ----------------------------------------------------------------------
_LABELS = {"loc": "moved", "rot": "rotated", "scale": "scaled", "dims": "resized",
           "domains": "geometry, material, modifier, shape-key or pose values edited",
           "mesh": "geometry edited", "materials": "materials changed",
           "modifiers": "modifiers changed", "parent": "parent changed",
           "hidden": "visibility changed", "type": "type changed"}


class SceneSnapshots:
    """Identity-aware snapshots of the scene and readable diffs between two of them.

    Every object gets a stable ``blendwme_id`` (duplicates from copy/paste are
    re-issued), so a rename is reported as a rename rather than a removal plus
    an addition. A SHA-256 fingerprint of sampled domain data (geometry,
    shape keys, modifiers, materials, pose, animation keys) catches edits a
    transform comparison would miss.
    """

    def __init__(self, reader):
        """Use reader for mesh counts."""
        self.reader = reader

    @staticmethod
    def animation_values(owner):
        """(action name, curve count, sampled keys) of an owner's action, or None."""
        animation = getattr(owner, "animation_data", None)
        action = animation.action if animation else None
        if action is None:
            return None
        curves = list(getattr(action, "fcurves", ()))
        # Blender 4.2 uses action.fcurves; newer layered actions expose channelbags.
        if not curves:
            for layer in getattr(action, "layers", ()):
                for strip in layer.strips:
                    for bag in getattr(strip, "channelbags", ()):
                        curves.extend(bag.fcurves)
        values = []
        for curve in curves[:1000]:
            keys = curve.keyframe_points
            stride = max(1, (len(keys) + 999) // 1000)
            values.append((curve.data_path, curve.array_index, len(keys),
                           [(tuple(key.co), tuple(key.handle_left), tuple(key.handle_right), key.interpolation)
                            for key in keys[::stride]]))
        return (action.name, len(curves), values)

    def fingerprint(self, obj):
        """Domain evidence at tool boundaries, never on every redraw.

        Geometry above 100k vertices is sampled; this is disclosed in scene facts.
        """
        values = []
        if obj.type == "MESH":
            verts = obj.data.vertices
            stride = max(1, (len(verts) + 99999) // 100000)
            values.append([(v.index, tuple(v.co)) for v in verts[::stride]])
            for domain in (obj.data.edges, obj.data.polygons):
                domain_stride = max(1, (len(domain) + 99999) // 100000)
                values.append([(item.index, tuple(item.vertices)) for item in domain[::domain_stride]])
            keys = obj.data.shape_keys
            if keys:
                values.append([(k.name, k.value, [tuple(v.co) for v in k.data[::stride]]) for k in keys.key_blocks])
                values.append(self.animation_values(keys))
        values.append(self.animation_values(obj))
        if obj.data:
            values.append(self.animation_values(obj.data))
        for mod in obj.modifiers:
            values.append((mod.type, [(p.identifier, str(getattr(mod, p.identifier, "")))
                                     for p in mod.bl_rna.properties if p.type in {"INT", "FLOAT", "BOOLEAN", "ENUM"}]))
        for slot in obj.material_slots:
            mat = slot.material
            if mat:
                values.append((mat.name, tuple(mat.diffuse_color)))
                if mat.node_tree:
                    values.append([(n.name, [(s.name, str(s.default_value)) for s in n.inputs
                                            if hasattr(s, "default_value")]) for n in mat.node_tree.nodes])
                    values.append([(link.from_node.name, link.from_socket.name, link.to_node.name, link.to_socket.name)
                                   for link in mat.node_tree.links])
        if obj.pose:
            values.append([(b.name, tuple(v for row in b.matrix_basis for v in row)) for b in obj.pose.bones])
        return hashlib.sha256(repr(values).encode()).hexdigest()

    def snapshot(self):
        """{object name: comparable facts} for every object in the scene."""
        snap = {}
        identities = set()
        for obj in bpy.context.scene.objects:
            identity = obj.get("blendwme_id")
            if not identity or identity in identities:
                identity = obj["blendwme_id"] = str(uuid.uuid4())
            identities.add(identity)
            if obj.mode == "EDIT":
                try:
                    obj.update_from_editmode()  # edit-mode geometry is not synced otherwise
                except Exception:
                    pass
            mats = tuple(s.material.name for s in getattr(obj, "material_slots", []) if s.material)
            mods = tuple(f"{m.type}:{m.name}" for m in getattr(obj, "modifiers", []))
            snap[obj.name] = {
                "identity": identity,
                "domains": self.fingerprint(obj),
                "type": obj.type,
                "loc": tuple(_r(obj.location, 3)),
                "rot": tuple(round(math.degrees(a), 1) for a in obj.rotation_euler),
                "scale": tuple(_r(obj.scale, 3)),
                "dims": tuple(_r(obj.dimensions, 3)),
                "mesh": self.reader.mesh_counts(obj),
                "materials": mats,
                "modifiers": mods,
                "parent": obj.parent.name if obj.parent else None,
                "hidden": bool(obj.hide_get() or obj.hide_viewport),
            }
        return snap

    @staticmethod
    def diff(before, after):
        """Return a list of readable change lines and the names that changed."""
        lines, changed = [], {"added": [], "removed": [], "modified": []}
        before = dict(before)
        old_names = {v.get("identity"): k for k, v in before.items() if v.get("identity")}
        for name, value in after.items():
            old = old_names.get(value.get("identity"))
            if old and old != name and name not in before:
                before[name] = before.pop(old)
                lines.append(f"~ renamed {old} -> {name}")
                changed["modified"].append(name)
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


class Authorship:
    """Who made, last edited and protects each object.

    ``blendwme_author`` is the current owner ("ai" or "user"); the original
    creator and the last editor are kept separately, so a brain editing a
    user's object never takes over the user's ownership or protection.
    """

    @staticmethod
    def tag_author(names, author):
        """Mark objects as made by author ("ai" or "user"); user objects become protected."""
        for name in names:
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            if not obj.get("blendwme_creator"):
                obj["blendwme_creator"] = obj.get(BRAIN_KEY, obj.get(AUTHOR_KEY, author))
            obj["blendwme_last_editor"] = author
            if author == "user":
                obj["blendwme_protected"] = True
            obj[AUTHOR_KEY] = author
            if author != "ai" and BRAIN_KEY in obj:
                del obj[BRAIN_KEY]

    @staticmethod
    def tag_brain(names, brain):
        """Record the editor without transferring user ownership or protection."""
        for name in names:
            obj = bpy.data.objects.get(name)
            if obj is not None:
                obj["blendwme_last_editor"] = brain
            if obj is not None and obj.get(AUTHOR_KEY) == "ai":
                obj[BRAIN_KEY] = brain
                if obj.get("blendwme_creator") in (None, "ai"):
                    obj["blendwme_creator"] = brain

    @staticmethod
    def makers():
        """{object name: "claude" | "codex" | "api" | "ai" | "user"} for every tagged object in the scene."""
        out = {}
        for obj in bpy.context.scene.objects:
            author = obj.get(AUTHOR_KEY)
            if author:
                out[obj.name] = obj.get(BRAIN_KEY, author) if author == "ai" else author
        return out


reader = SceneReader()
snapshots = SceneSnapshots(reader)
authorship = Authorship()

world_bounds = SceneReader.world_bounds
_mesh_counts = SceneReader.mesh_counts
object_summary = reader.object_summary
get_scene_info = reader.get_scene_info
inventory = SceneReader.inventory
default_startup_cube = SceneReader.default_startup_cube
get_object_info = reader.get_object_info
_animation_values = SceneSnapshots.animation_values
_fingerprint = snapshots.fingerprint
snapshot = snapshots.snapshot
diff = SceneSnapshots.diff
tag_author = Authorship.tag_author
tag_brain = Authorship.tag_brain
makers = Authorship.makers
