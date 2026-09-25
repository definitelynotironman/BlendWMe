"""bwm shapes: a forgiving, high-level modelling kit.

The model should express artistic intent ("a cone 1.2 m tall", "a lofted
body from these sections", "put the chimney on the roof") instead of
fighting bmesh and operator details. Everything here:

- returns the new object, with real dimensions and scale 1,
- takes `location` as the CENTRE of the shape unless the docstring says otherwise,
- accepts numbers as numbers or strings, vectors as lists, tuples or Vectors,
  colours with 3 or 4 values, 0-255, hex ("#c0392b") or simple names ("red"),
- accepts objects, materials and collections as the thing itself OR its name,
- replaces an object of the same name that the AI made in an EARLIER step
  (so re-running a step updates it instead of making "Roof.001"). Objects
  the user made or edited are never replaced.

All of it is exposed inside run_python as bwm.<name>.
"""

import math
from collections import namedtuple

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import helpers as _h

__all__ = [
    "cylinder", "cone", "sphere", "torus", "prism", "wedge", "gable_roof", "extrude_profile",
    "loft", "lathe", "tube", "limb",
    "bevel", "mirror", "array", "subdivide", "solidify", "boolean",
    "bounds", "top", "place_on", "align", "duplicate", "parent", "group", "rotate",
]

# ----------------------------------------------------------------------
# Tolerant argument readers
# ----------------------------------------------------------------------


def num(value, default=0.0):
    """A float from 2, 2.0, "2", "2.0 m" or None (-> default)."""
    if value is None:
        return float(default)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().rstrip("m").strip())
    except ValueError:
        return float(default)


def vec(value, n=3, default=0.0):
    """A tuple of n floats from a number, list, tuple, Vector or "1, 2, 3"."""
    if value is None:
        return (float(default),) * n
    if isinstance(value, str):
        value = [p for p in value.replace("(", " ").replace(")", " ").replace(",", " ").split() if p]
    if isinstance(value, (int, float)):
        return (float(value),) * n
    items = [num(v) for v in value]
    if len(items) < n:
        items += [float(default)] * (n - len(items))
    return tuple(items[:n])


def size3(value, default=1.0):
    """(x, y, z) size: one number makes a cube."""
    if value is None or isinstance(value, (int, float)) or (isinstance(value, str) and "," not in value):
        return (num(value, default),) * 3
    return vec(value, 3, default)


AXES = {"X": Vector((1, 0, 0)), "Y": Vector((0, 1, 0)), "Z": Vector((0, 0, 1))}


def axis_name(value, default="Z"):
    text = str(value or default).upper().lstrip("+-")
    return text if text in AXES else default


def _axis_matrix(axis):
    """Rotation that turns +Z into the given axis."""
    axis = axis_name(axis)
    if axis == "X":
        return Matrix.Rotation(math.radians(90), 4, "Y")
    if axis == "Y":
        return Matrix.Rotation(math.radians(-90), 4, "X")
    return Matrix.Identity(4)


# ----------------------------------------------------------------------
# Making objects
# ----------------------------------------------------------------------


def _finish(name, bm, location=(0, 0, 0), collection=None, material=None, smooth=False,
            rot_deg=None, auto_smooth=40):
    """Turn a bmesh (built around the origin) into a placed object."""
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    _h.claim_name(name)
    data = bpy.data.meshes.new(name)
    bm.to_mesh(data)
    bm.free()
    if smooth:
        data.polygons.foreach_set("use_smooth", [True] * len(data.polygons))
    data.update()
    obj = bpy.data.objects.new(name, data)
    obj.location = vec(location)
    if rot_deg is not None:
        obj.rotation_euler = [math.radians(a) for a in vec(rot_deg)]
    _h._place(obj, collection, material)
    if smooth and auto_smooth:
        _h.smooth_by_angle(obj, auto_smooth)
    return obj


def cylinder(name, radius=0.5, depth=1.0, location=(0, 0, 0), segments=24, axis="Z",
             collection=None, material=None, smooth=True, caps=True, bevel=0.0):
    """A cylinder centred on location. axis is the long direction: 'X', 'Y' or 'Z'."""
    return cone(name, num(radius, 0.5), num(radius, 0.5), depth, location, segments, axis,
                collection, material, smooth, caps, bevel)


def cone(name, radius_bottom=0.5, radius_top=0.0, depth=1.0, location=(0, 0, 0), segments=24, axis="Z",
         collection=None, material=None, smooth=True, caps=True, bevel=0.0):
    """A cone or frustum centred on location. radius_top=0 makes a point.

    axis 'Z' stands it up (bottom at location.z - depth/2). Use axis='-Z' to flip it
    (point down, like an ice cream cone).
    """
    bm = bmesh.new()
    r1, r2, d = num(radius_bottom, 0.5), num(radius_top, 0.0), num(depth, 1.0)
    bmesh.ops.create_cone(bm, cap_ends=bool(caps), cap_tris=False, segments=max(3, int(num(segments, 24))),
                          radius1=r1, radius2=r2, depth=d)
    turn = _axis_matrix(axis)
    if str(axis).startswith("-"):
        turn = turn @ Matrix.Rotation(math.pi, 4, "X")
    bmesh.ops.transform(bm, matrix=turn, verts=bm.verts)
    obj = _finish(name, bm, location, collection, material, smooth)
    if bevel:
        _h_bevel(obj, bevel)
    return obj


def sphere(name, radius=0.5, location=(0, 0, 0), segments=32, rings=16, collection=None,
           material=None, smooth=True):
    """A UV sphere. radius can be (rx, ry, rz) for an ellipsoid (egg, head, scoop)."""
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=max(3, int(num(segments, 32))),
                              v_segments=max(3, int(num(rings, 16))), radius=1.0)
    rx, ry, rz = vec(radius, 3, 0.5) if not isinstance(radius, (int, float, str)) else (num(radius, 0.5),) * 3
    bmesh.ops.scale(bm, vec=(rx, ry, rz), verts=bm.verts)
    return _finish(name, bm, location, collection, material, smooth, auto_smooth=0)


def torus(name, major_radius=1.0, minor_radius=0.25, location=(0, 0, 0), segments=32, ring_segments=12,
          axis="Z", collection=None, material=None, smooth=True):
    """A ring (donut, tyre, rim). axis is the hole's direction."""
    R, r = num(major_radius, 1.0), num(minor_radius, 0.25)
    n, m = max(3, int(num(segments, 32))), max(3, int(num(ring_segments, 12)))
    bm = bmesh.new()
    rings = []
    for i in range(n):
        a = 2 * math.pi * i / n
        ring = []
        for j in range(m):
            b = 2 * math.pi * j / m
            ring.append(bm.verts.new(((R + r * math.cos(b)) * math.cos(a),
                                      (R + r * math.cos(b)) * math.sin(a), r * math.sin(b))))
        rings.append(ring)
    for i in range(n):
        for j in range(m):
            a, b = rings[i], rings[(i + 1) % n]
            bm.faces.new((a[j], b[j], b[(j + 1) % m], a[(j + 1) % m]))
    bmesh.ops.transform(bm, matrix=_axis_matrix(axis), verts=bm.verts)
    return _finish(name, bm, location, collection, material, smooth, auto_smooth=0)


def extrude_profile(name, points, depth=1.0, location=(0, 0, 0), axis="Y", collection=None,
                    material=None, bevel=0.0, smooth=False):
    """A solid from a 2D outline, pushed `depth` along `axis` (centred on location).

    points are 2D in the plane across the axis:
      axis 'Y' (default): points are (x, z)  - draw what you see in Front view
      axis 'X':           points are (y, z)  - draw what you see in Side view
      axis 'Z':           points are (x, y)  - draw what you see in Top view
    The outline may be concave (an L, a house front, a roof chevron).
    """
    axis = axis_name(axis, "Y")
    pts = [vec(p, 2) for p in points]
    if len(pts) < 3:
        raise ValueError("extrude_profile needs at least 3 points.")
    half = num(depth, 1.0) / 2

    def place(u, v, w):
        if axis == "Y":
            return (u, w, v)
        if axis == "X":
            return (w, u, v)
        return (u, v, w)
    bm = bmesh.new()
    front = [bm.verts.new(place(u, v, -half)) for u, v in pts]
    back = [bm.verts.new(place(u, v, half)) for u, v in pts]
    bm.faces.new(front)
    bm.faces.new(list(reversed(back)))
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((front[i], front[j], back[j], back[i]))
    obj = _finish(name, bm, location, collection, material, smooth)
    if bevel:
        _h_bevel(obj, bevel)
    return obj


def prism(name, size=(2, 2, 1), location=(0, 0, 0), ridge_axis="X", collection=None, material=None):
    """A triangular prism (gable end, ridge cap, simple roof block).

    size is the bounding box (x, y, z); the ridge runs along ridge_axis at the top.
    """
    sx, sy, sz = size3(size)
    if axis_name(ridge_axis, "X") == "X":
        pts = [(-sy / 2, -sz / 2), (sy / 2, -sz / 2), (0, sz / 2)]
        return extrude_profile(name, pts, sx, location, "X", collection, material)
    pts = [(-sx / 2, -sz / 2), (sx / 2, -sz / 2), (0, sz / 2)]
    return extrude_profile(name, pts, sy, location, "Y", collection, material)


def wedge(name, size=(1, 1, 1), location=(0, 0, 0), high_side="+Y", collection=None, material=None):
    """A ramp: full height on high_side ('+Y', '-Y', '+X', '-X'), zero on the other side."""
    sx, sy, sz = size3(size)
    side = str(high_side).upper()
    if side.endswith("Y"):
        sign = -1 if side.startswith("-") else 1
        pts = [(-sy / 2, -sz / 2), (sy / 2, -sz / 2), (sign * sy / 2, sz / 2)]
        return extrude_profile(name, pts, sx, location, "X", collection, material)
    sign = -1 if side.startswith("-") else 1
    pts = [(-sx / 2, -sz / 2), (sx / 2, -sz / 2), (sign * sx / 2, sz / 2)]
    return extrude_profile(name, pts, sy, location, "Y", collection, material)


def gable_roof(name, width=4.0, depth=3.0, rise=1.2, location=(0, 0, 2.4), overhang=0.3, thickness=0.15,
               ridge_axis="X", collection=None, material=None, gable=True, gable_material=None):
    """A pitched (gable) roof that sits exactly on walls of width x depth.

    location = (x, y, z) where x, y is the centre of the walls and z is the TOP of the walls.
    width runs along ridge_axis, depth across it. rise = height of the ridge above the
    wall tops. The underside passes exactly through the wall top edges, so there is no gap.
    gable=True also fills the two triangular wall ends (name + '_Gables') with gable_material
    (default: a warm plaster colour). Use the walls' material to match them.
    Returns the roof object (the gables are parented to it).
    """
    W, D, R = num(width, 4), num(depth, 3), num(rise, 1.2)
    o, t = num(overhang, 0.3), num(thickness, 0.15)
    cx, cy, z0 = vec(location)
    d = D / 2
    k = R / d                                   # slope
    tv = t * math.sqrt(1 + k * k)               # vertical thickness of a sloped slab
    edge = d + o
    eave = -k * o                               # underside height at the eave (relative to wall top)
    chevron = [(-edge, eave), (0, R), (edge, eave), (edge, eave + tv), (0, R + tv), (-edge, eave + tv)]
    axis = axis_name(ridge_axis, "X")
    roof = extrude_profile(name, chevron, W + 2 * o, (cx, cy, z0), "X" if axis == "X" else "Y",
                           collection, material if material is not None else _h.material("Roof_Tiles", (0.62, 0.2, 0.12), 0.7))
    if gable:
        tri = [(-d, 0.0), (d, 0.0), (0, R)]
        mat = gable_material if gable_material is not None else _h.material("Plaster", (0.88, 0.8, 0.66), 0.8)
        g = extrude_profile(name + "_Gables", tri, W, (cx, cy, z0), "X" if axis == "X" else "Y", collection, mat)
        parent(g, roof)
    return roof


def loft(name, sections, location=(0, 0, 0), segments=24, axis="Z", caps=True, collection=None,
         material=None, smooth=True, subdivide=0):
    """A smooth body through cross-sections (bottle, torso, limb, scoop, vase, boat hull...).

    Each section is one of:
      (z, r)                  a circle of radius r at height z
      (z, rx, ry)             an ellipse
      (z, rx, ry, dx, dy)     an ellipse shifted sideways by (dx, dy)
      [(x, y, z), ...]        your own ring of points (all rings need the same count)
    Heights are along `axis` ('Z' up by default) and relative to location.
    A radius of 0 closes the body to a point. subdivide adds a Subdivision modifier.
    """
    n = max(3, int(num(segments, 24)))
    rings = []
    for s in sections:
        s = list(s)
        if s and isinstance(s[0], (list, tuple, Vector)):
            rings.append([Vector(vec(p)) for p in s])
            continue
        z = num(s[0])
        rx = num(s[1], 0.5) if len(s) > 1 else 0.5
        ry = num(s[2], rx) if len(s) > 2 else rx
        dx = num(s[3]) if len(s) > 3 else 0.0
        dy = num(s[4]) if len(s) > 4 else 0.0
        rings.append([Vector((dx + rx * math.cos(2 * math.pi * i / n), dy + ry * math.sin(2 * math.pi * i / n), z))
                      for i in range(n)])
    if len(rings) < 2:
        raise ValueError("loft needs at least 2 sections.")
    count = len(rings[0])
    if any(len(r) != count for r in rings):
        raise ValueError("All point rings in loft need the same number of points.")
    bm = bmesh.new()
    vrings = []
    for ring in rings:
        size = max((p - sum(ring, Vector()) / count).length for p in ring)
        if size < 1e-6:     # a point: one vertex closes the body
            vrings.append([bm.verts.new(ring[0])] * count)
        else:
            vrings.append([bm.verts.new(p) for p in ring])
    for a, b in zip(vrings, vrings[1:]):
        for i in range(count):
            j = (i + 1) % count
            quad = [a[i], a[j], b[j], b[i]]
            unique = []
            for v in quad:
                if v not in unique:
                    unique.append(v)
            if len(unique) >= 3:
                try:
                    bm.faces.new(unique)
                except ValueError:
                    pass
    if caps:
        for ring, flip in ((vrings[0], True), (vrings[-1], False)):
            if len(set(ring)) == count:
                try:
                    bm.faces.new(list(reversed(ring)) if flip else ring)
                except ValueError:
                    pass
    bmesh.ops.transform(bm, matrix=_axis_matrix(axis), verts=bm.verts)
    obj = _finish(name, bm, location, collection, material, smooth, auto_smooth=0)
    if num(subdivide):
        subdivide_mod(obj, int(num(subdivide)))
    return obj


def lathe(name, profile, location=(0, 0, 0), segments=32, axis="Z", collection=None, material=None,
          smooth=True, caps=True):
    """Spin a side profile around the axis (vase, cup, cone, bottle, lamp shade, scoop).

    profile is a list of (radius, height) points from bottom to top. Radius 0 makes a point.
    """
    return loft(name, [(num(h), num(r)) for r, h in (vec(p, 2) for p in profile)], location, segments,
                axis, caps, collection, material, smooth)


def tube(name, points, radius=0.05, segments=12, location=(0, 0, 0), closed=False, caps=True,
         collection=None, material=None, smooth=True):
    """A round tube along a path of 3D points (pipe, cable, handle, branch, tapered limb).

    radius can be one number or one radius per point (taper it for limbs and horns).
    closed=True joins the end back to the start (a ring).
    """
    pts = [Vector(vec(p)) for p in points]
    if len(pts) < 2:
        raise ValueError("tube needs at least 2 points.")
    radii = [num(r) for r in radius] if isinstance(radius, (list, tuple)) else [num(radius, 0.05)] * len(pts)
    radii += [radii[-1]] * (len(pts) - len(radii))
    m = max(3, int(num(segments, 12)))
    bm = bmesh.new()
    rings = []
    # Parallel transport frames: no twisting along the path.
    tangent = (pts[1] - pts[0]).normalized()
    normal = tangent.orthogonal().normalized()
    for i, p in enumerate(pts):
        if closed:
            t = (pts[(i + 1) % len(pts)] - pts[i - 1]).normalized()
        elif i == 0:
            t = (pts[1] - pts[0]).normalized()
        elif i == len(pts) - 1:
            t = (pts[-1] - pts[-2]).normalized()
        else:
            t = ((pts[i + 1] - p).normalized() + (p - pts[i - 1]).normalized()).normalized()
        if t.length < 1e-6:
            t = tangent
        normal = (normal - t * normal.dot(t))
        normal = normal.normalized() if normal.length > 1e-6 else t.orthogonal().normalized()
        binormal = t.cross(normal)
        rings.append([bm.verts.new(p + radii[i] * (math.cos(2 * math.pi * j / m) * normal
                                                   + math.sin(2 * math.pi * j / m) * binormal))
                      for j in range(m)])
        tangent = t
    pairs = list(zip(rings, rings[1:])) + ([(rings[-1], rings[0])] if closed else [])
    for a, b in pairs:
        for j in range(m):
            k = (j + 1) % m
            bm.faces.new((a[j], a[k], b[k], b[j]))
    if caps and not closed:
        bm.faces.new(list(reversed(rings[0])))
        bm.faces.new(rings[-1])
    return _finish(name, bm, location, collection, material, smooth, auto_smooth=0)


def limb(name, start, end, radius_start=0.08, radius_end=0.05, bend=(0, 0, 0), segments=12,
         collection=None, material=None):
    """A tapered limb from start to end (arm, leg, finger, horn, branch).

    bend pushes the middle sideways by that vector (a bent knee or elbow).
    """
    a, b, k = Vector(vec(start)), Vector(vec(end)), Vector(vec(bend))
    steps = 8
    pts, radii = [], []
    r1, r2 = num(radius_start, 0.08), num(radius_end, 0.05)
    for i in range(steps + 1):
        t = i / steps
        pts.append(a.lerp(b, t) + k * math.sin(math.pi * t))
        radii.append(r1 + (r2 - r1) * t)
    return tube(name, pts, radii, segments, collection=collection, material=material)


# ----------------------------------------------------------------------
# Modifiers (kept live, so everything stays editable)
# ----------------------------------------------------------------------


def _h_bevel(obj, width, segments=2):
    return bevel(obj, width, segments)


def bevel(target, width=0.02, segments=2, angle=30):
    """Add (or update) a Bevel modifier: rounded edges that catch the light."""
    target = _h.obj(target)
    m = target.modifiers.get("Bevel") or target.modifiers.new("Bevel", "BEVEL")
    m.width = num(width, 0.02)
    m.segments = int(num(segments, 2))
    m.limit_method = "ANGLE"
    m.angle_limit = math.radians(num(angle, 30))
    return m


def mirror(target, axis="X", mirror_object=None, clip=True):
    """Add a Mirror modifier: model one half, get both (faces, characters, cars)."""
    target = _h.obj(target)
    m = target.modifiers.get("Mirror") or target.modifiers.new("Mirror", "MIRROR")
    axes = str(axis).upper()
    m.use_axis = ("X" in axes, "Y" in axes, "Z" in axes)
    m.use_clip = bool(clip)
    if mirror_object is not None:
        m.mirror_object = _h.obj(mirror_object)
    return m


def array(target, count=3, offset=(1.0, 0, 0), relative=False):
    """Add an Array modifier: count copies, each moved by offset (metres, or a factor of
    the object's size when relative=True). Planks, tiles, fence posts, steps."""
    target = _h.obj(target)
    m = target.modifiers.new("Array", "ARRAY")
    m.count = max(1, int(num(count, 3)))
    off = vec(offset)
    if relative:
        m.use_relative_offset, m.use_constant_offset = True, False
        m.relative_offset_displace = off
    else:
        m.use_relative_offset, m.use_constant_offset = False, True
        m.constant_offset_displace = off
    return m


def subdivide(target, levels=2, render_levels=None):
    """Add a Subdivision Surface modifier: smooth, organic forms from a simple cage."""
    return subdivide_mod(target, levels, render_levels)


def subdivide_mod(target, levels=2, render_levels=None):
    target = _h.obj(target)
    m = target.modifiers.get("Subdivision") or target.modifiers.new("Subdivision", "SUBSURF")
    m.levels = int(num(levels, 2))
    m.render_levels = int(num(render_levels, m.levels)) if render_levels is not None else m.levels
    if target.type == "MESH":
        target.data.polygons.foreach_set("use_smooth", [True] * len(target.data.polygons))
        target.data.update()
    return m


def solidify(target, thickness=0.05, offset=-1.0):
    """Add a Solidify modifier: give a surface real thickness (walls, cloth, shells)."""
    target = _h.obj(target)
    m = target.modifiers.get("Solidify") or target.modifiers.new("Solidify", "SOLIDIFY")
    m.thickness = num(thickness, 0.05)
    m.offset = num(offset, -1.0)
    return m


def boolean(target, cutter, operation="DIFFERENCE", hide_cutter=True):
    """Cut (or join/intersect) with another object, kept live as a Boolean modifier.

    The cutter is hidden and parented to the target, so moving the target moves the hole.
    """
    target, cutter = _h.obj(target), _h.obj(cutter)
    m = target.modifiers.new("Boolean_" + cutter.name, "BOOLEAN")
    m.object = cutter
    m.operation = str(operation).upper() if str(operation).upper() in ("DIFFERENCE", "UNION", "INTERSECT") else "DIFFERENCE"
    m.solver = "EXACT"
    if hide_cutter:
        cutter.display_type = "WIRE"
        cutter.hide_render = True
        cutter.hide_set(True)
        if cutter.parent is None:
            parent(cutter, target)
    return m


# ----------------------------------------------------------------------
# Placement: put things on, against and in line with each other
# ----------------------------------------------------------------------
Bounds = namedtuple("Bounds", "min max size center")


def _world_points(obj, depsgraph):
    """Points whose min/max give the object's exact world bounds (modifiers included).

    Meshes use their real vertices: a rotated object's bounding box corners would
    overstate its size and misplace whatever is put against it.
    """
    try:
        ev = obj.evaluated_get(depsgraph)
    except Exception:
        return [obj.matrix_world.translation.copy()]
    if obj.type == "MESH":
        me = ev.to_mesh()
        try:
            n = len(me.vertices)
            if 0 < n <= 300_000:
                import numpy as np
                co = np.empty(n * 3, dtype=np.float32)
                me.vertices.foreach_get("co", co)
                mw = np.array(ev.matrix_world, dtype=np.float64)
                world = co.reshape(n, 3) @ mw[:3, :3].T + mw[:3, 3]
                return [Vector(world.min(axis=0)), Vector(world.max(axis=0))]
        finally:
            ev.to_mesh_clear()
    return [ev.matrix_world @ Vector(c) for c in ev.bound_box]


def bounds(target, children=True):
    """World bounds of an object (and its children): .min .max .size .center (Vectors).

    Example: b = bwm.bounds('Walls'); roof_z = b.max.z
    """
    target = _h.obj(target)
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    objs = [target] + (list(target.children_recursive) if children else [])
    pts = []
    for o in objs:
        if o.type in {"EMPTY", "CAMERA", "LIGHT"} and o is not target:
            continue
        if o.type == "EMPTY" and len(objs) > 1:
            continue
        if o.hide_render and o is not target:       # hidden boolean cutters
            continue
        pts.extend(_world_points(o, depsgraph))
    if not pts:
        pts = [target.matrix_world.translation.copy()]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    return Bounds(lo, hi, hi - lo, (lo + hi) / 2)


def top(target):
    """Height of the highest point of an object (and its children)."""
    return bounds(target).max.z


def _surface_z(target, x, y):
    """Height of target's surface under (x, y), by casting a ray down. None if it misses."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    hits = []
    objs = [target] + [c for c in target.children_recursive if not c.hide_render]
    for o in objs:
        if o.type != "MESH":
            continue
        ev = o.evaluated_get(depsgraph)
        inv = ev.matrix_world.inverted()
        start = inv @ Vector((x, y, 1e5))
        direction = (inv.to_3x3() @ Vector((0, 0, -1))).normalized()
        ok, loc, _n, _i = ev.ray_cast(start, direction)
        if ok:
            hits.append((ev.matrix_world @ loc).z)
    return max(hits) if hits else None


def place_on(target, host=None, side="top", at=None, gap=0.0, sink=False):
    """Move target so it touches host on one side. host=None means the ground (z = 0).

    side: 'top' (sit on it), 'bottom' (hang under it), 'front' (-Y face), 'back' (+Y),
          'left' (-X), 'right' (+X).
    at: world coordinates for the other two axes: (x, y) for top/bottom, (x, z) for
        front/back, (y, z) for left/right. Default: keep target where it already is in those axes
        (or centre it on the host if it is not over the host).
    gap: distance to leave (negative pushes it INTO the host, e.g. -0.03 to recess a window).
    sink: for 'top' on sloped hosts (roofs): True buries the whole base in the surface
          (chimney on a roof, no gap anywhere); False rests on the highest point.
    """
    target = _h.obj(target)
    side = str(side or "top").lower()
    tb = bounds(target)
    g = num(gap)
    if host is None or (isinstance(host, str) and host.lower() in ("ground", "floor", "world")):
        target.location.z += g - tb.min.z
        if at is not None:
            x, y = vec(at, 2)
            target.location.x += x - tb.center.x
            target.location.y += y - tb.center.y
        return target
    host = _h.obj(host)
    hb = bounds(host)
    move = Vector((0, 0, 0))
    if side in ("top", "bottom"):
        if at is not None:
            x, y = vec(at, 2)
        else:
            inside = hb.min.x <= tb.center.x <= hb.max.x and hb.min.y <= tb.center.y <= hb.max.y
            x, y = (tb.center.x, tb.center.y) if inside else (hb.center.x, hb.center.y)
        move.x, move.y = x - tb.center.x, y - tb.center.y
        if side == "top":
            # Sample the host's real surface under the target's footprint (sloped roofs, domes).
            hx, hy = tb.size.x / 2, tb.size.y / 2
            samples = [(x, y), (x - hx, y - hy), (x + hx, y - hy), (x - hx, y + hy), (x + hx, y + hy)]
            heights = [h for h in (_surface_z(host, sx, sy) for sx, sy in samples) if h is not None]
            surface = (min(heights) if sink else max(heights)) if heights else hb.max.z
            move.z = surface + g - tb.min.z
        else:
            move.z = hb.min.z - g - tb.max.z
    elif side in ("front", "back"):
        x, z = vec(at, 2) if at is not None else (tb.center.x, tb.center.z)
        move.x, move.z = x - tb.center.x, z - tb.center.z
        move.y = (hb.min.y - g - tb.max.y) if side == "front" else (hb.max.y + g - tb.min.y)
    elif side in ("left", "right"):
        y, z = vec(at, 2) if at is not None else (tb.center.y, tb.center.z)
        move.y, move.z = y - tb.center.y, z - tb.center.z
        move.x = (hb.min.x - g - tb.max.x) if side == "left" else (hb.max.x + g - tb.min.x)
    else:
        raise ValueError("side must be top, bottom, front, back, left or right.")
    target.location += move
    return target


def align(target, host, axis="x", how="center"):
    """Line target up with host along one axis: how = 'center', 'min' or 'max'."""
    target, host = _h.obj(target), _h.obj(host)
    i = "xyz".index(str(axis).lower()[0])
    tb, hb = bounds(target), bounds(host)
    key = str(how).lower()
    if key.startswith("min"):
        delta = hb.min[i] - tb.min[i]
    elif key.startswith("max"):
        delta = hb.max[i] - tb.max[i]
    else:
        delta = hb.center[i] - tb.center[i]
    loc = target.location.copy()
    loc[i] += delta
    target.location = loc
    return target


def rotate(target, degrees=(0, 0, 0)):
    """Set rotation in DEGREES (x, y, z). One number turns around Z."""
    target = _h.obj(target)
    if isinstance(degrees, (int, float, str)):
        degrees = (0, 0, num(degrees))
    target.rotation_euler = [math.radians(a) for a in vec(degrees)]
    return target


def duplicate(target, name=None, location=None, offset=None, linked=False):
    """Copy an object (and its children). Give a new location or an offset from the original."""
    target = _h.obj(target)

    def copy(src, new_parent):
        new = src.copy()
        if src.data is not None and not linked:
            new.data = src.data.copy()
        for c in src.users_collection:
            c.objects.link(new)
        if new_parent is not None:
            new.parent = new_parent
        for child in src.children:
            copy(child, new)
        return new
    new = copy(target, None)
    new.parent = target.parent
    if name:
        _h.claim_name(name)
        new.name = name
    if location is not None:
        new.location = vec(location)
    elif offset is not None:
        new.location = target.location + Vector(vec(offset))
    return new


def parent(child, parent_obj, keep_transform=True):
    """Parent child to parent_obj (keeping where the child is in the world)."""
    child, parent_obj = _h.obj(child), _h.obj(parent_obj)
    bpy.context.view_layer.update()       # matrix_world is stale right after setting location
    world = child.matrix_world.copy()
    child.parent = parent_obj
    if keep_transform:
        child.matrix_parent_inverse = parent_obj.matrix_world.inverted()
        bpy.context.view_layer.update()
        child.matrix_world = world
    return child


def group(name, *objs, collection=None):
    """An Empty at the centre-bottom of objs with all of them parented to it.

    Move or rotate the group to move everything together.
    """
    objs = [_h.obj(o) for o in objs]
    _h.claim_name(name)
    root = bpy.data.objects.new(name, None)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 0.3
    if objs:
        pts = [bounds(o) for o in objs]
        lo = Vector([min(b.min[i] for b in pts) for i in range(3)])
        hi = Vector([max(b.max[i] for b in pts) for i in range(3)])
        root.location = ((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z)
    target = _h.col(collection) if collection else (objs[0].users_collection[0] if objs and objs[0].users_collection
                                                    else bpy.context.scene.collection)
    target.objects.link(root)
    bpy.context.view_layer.update()
    for o in objs:
        parent(o, root)
    return root
