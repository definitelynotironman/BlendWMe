"""bwm architecture and furniture: real, editable pieces built from simple parts.

Each piece is a small hierarchy: an Empty named after the piece (move or
rotate it to move everything) with clearly named, beveled parts parented to
it. That keeps the result editable by hand and readable in the Outliner.

Conventions (the same for every piece):
- location is the point on the FLOOR under the piece's centre (for windows:
  the centre of the window; for monitors and keyboards: the point on the desk).
- at rot_deg=0 the front of the piece faces -Y (Blender's Front view).
  rot_deg=180 faces +Y, 90 faces +X, -90 faces -X.
- materials can be a material, a material name, a colour, or None for a
  sensible default.
"""

import math
import random

import bpy
from mathutils import Vector

from . import helpers as _h
from .shapes import cylinder, extrude_profile, num, sphere, vec

__all__ = [
    "wall", "floor", "opening", "window", "door", "path",
    "table", "desk", "chair", "bed", "wardrobe", "closet", "shelf", "monitor", "keyboard",
    "pc_tower", "rug", "lamp", "plant",
]

FACING_DEG = {"-Y": 0.0, "+Y": 180.0, "Y": 180.0, "+X": 90.0, "X": 90.0, "-X": -90.0}


def _mat(value, name, rgb, roughness=0.6, metallic=0.0):
    if value is None:
        return _h.material(name, rgb, roughness, metallic)
    return _h.as_material(value, name, rgb)


def _target_collection(collection):
    return _h.collection(collection) if isinstance(collection, str) else (collection or bpy.context.scene.collection)


def _root(name, collection, size=0.3):
    _h.claim_name(name)
    root = bpy.data.objects.new(name, None)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = size
    _target_collection(collection).objects.link(root)
    return root


def _part(root, name, size, center, material, bevel=0.0):
    """A box part at local coordinates (the root is still at the origin while building)."""
    coll = root.users_collection[0] if root.users_collection else None
    o = _h.box(f"{root.name}_{name}", size, center, collection=coll, material=material,
               bevel=bevel)
    o.parent = root
    return o


def _attach(root, obj):
    coll = root.users_collection[0] if root.users_collection else bpy.context.scene.collection
    for c in list(obj.users_collection):
        if c != coll:
            c.objects.unlink(obj)
    if coll not in obj.users_collection:
        coll.objects.link(obj)
    obj.parent = root
    return obj


def _place_root(root, location, rot_deg, against=None):
    """Put a finished piece in place.

    against=<wall>: ignore rot_deg, turn the piece so its front faces into the room
    (away from the wall) and slide it so its back touches the wall. location then only
    says where along the wall it goes.
    """
    if against is None:
        root.location = vec(location)
        root.rotation_euler = (0.0, 0.0, math.radians(num(rot_deg)))
        bpy.context.view_layer.update()
        return root
    from .shapes import bounds
    wall_obj = _h.obj(against)
    back = bounds(root).max.y            # the piece is still at the origin, unrotated
    p = Vector(vec(location))
    m = wall_obj.matrix_world
    lo, hi = _local_box(wall_obj)
    thin = _thin_axis(wall_obj)
    centre = m @ ((lo + hi) / 2)
    normal = (m.to_3x3() @ Vector([1.0 if i == thin else 0.0 for i in range(3)]))
    normal.z = 0
    normal.normalize()
    if normal.dot(Vector((p.x - centre.x, p.y - centre.y, 0))) < 0:
        normal = -normal
    face = centre + normal * (wall_obj.dimensions[thin] / 2)
    along = p - normal * (p - face).dot(normal)
    spot = along + normal * (back + 0.01)
    root.location = (spot.x, spot.y, p.z)
    root.rotation_euler = (0.0, 0.0, math.radians(_facing_deg(normal)))
    bpy.context.view_layer.update()
    return root


def _depth_of(host):
    """A desk's front-to-back depth, measured in its own axes."""
    host = _h.obj(host)
    pts = []
    inv = host.matrix_world.inverted()
    for o in [host] + list(host.children_recursive):
        if o.type == "MESH":
            pts += [(inv @ (o.matrix_world @ Vector(c))).y for c in o.bound_box]
    return (max(pts) - min(pts)) if pts else 0.7


def _on_top_of(host, local_offset):
    """(location, rot_deg) for a piece on host (desk, table), offset in the host's own axes."""
    from .shapes import bounds
    host = _h.obj(host)
    rot = host.matrix_world.to_euler()
    off = Vector(vec(local_offset, 2) + (0,))
    off.rotate(rot)
    c = host.matrix_world.translation
    return (c.x + off.x, c.y + off.y, bounds(host).max.z), math.degrees(rot.z)


# ----------------------------------------------------------------------
# Architecture
# ----------------------------------------------------------------------


def wall(name, start=(0, 0), end=(4, 0), height=2.6, thickness=0.15, collection=None, material=None,
         base=0.0, bevel=0.0):
    """A straight wall from start (x, y) to end (x, y) on the floor at height `base`.

    The wall's centre line runs from start to end. Returns the wall object.
    """
    a, b = Vector(vec(start, 2) + (0,)), Vector(vec(end, 2) + (0,))
    length = (b - a).length
    if length < 1e-4:
        raise ValueError("wall start and end are the same point.")
    h, t = num(height, 2.6), num(thickness, 0.15)
    mid = (a + b) / 2
    o = _h.box(name, (length, t, h), (mid.x, mid.y, num(base) + h / 2), collection,
               _mat(material, "Wall_Paint", (0.86, 0.84, 0.8), 0.85), bevel)
    o.rotation_euler.z = math.atan2(b.y - a.y, b.x - a.x)
    return o


def floor(name, size=(4, 4), location=(0, 0, 0), collection=None, material=None, thickness=0.1):
    """A floor slab whose TOP is at location.z, centred on (x, y)."""
    w, d = vec(size, 2, 4)
    x, y, z = vec(location)
    t = num(thickness, 0.1)
    return _h.box(name, (w, d, t), (x, y, z - t / 2), collection, _mat(material, "Floor_Wood", (0.45, 0.3, 0.18), 0.55))


BLOCK = 0.5   # thicker than this, a "wall" is a solid block (a house body), not a thin wall


def _thin_axis(wall_obj):
    """0 if the wall is thin along its local X, else 1 (local Y)."""
    d = wall_obj.dimensions
    return 0 if d.x < d.y else 1


def _local_box(wall_obj):
    return Vector(wall_obj.bound_box[0]), Vector(wall_obj.bound_box[6])


def _wall_frame(wall_obj, location):
    """Where a window/door goes on a wall: (point, outward normal, part depth, is_block).

    Thin wall: the point moves onto the wall's centre plane, the part spans the whole
    thickness and the normal points away from the scene centre.
    Solid block (a house body): the point moves onto the NEAREST side face, the
    part sits half in, half out of that face, and the normal is that face's normal.
    """
    p = Vector(vec(location))
    m = wall_obj.matrix_world
    local = m.inverted() @ p
    lo, hi = _local_box(wall_obj)
    thin = _thin_axis(wall_obj)
    if wall_obj.dimensions[thin] < BLOCK:
        local[thin] = (lo[thin] + hi[thin]) / 2
        point = m @ local
        normal = (m.to_3x3() @ Vector([1.0 if i == thin else 0.0 for i in range(3)])).normalized()
        outward = Vector((point.x, point.y, 0))
        if outward.length > 1e-6:
            if normal.dot(outward) < 0:
                normal = -normal
        elif normal.y > 0:
            normal = -normal
        return point, normal, wall_obj.dimensions[thin] + 0.04, False
    # nearest of the four side faces
    faces = [(abs(local.x - lo.x), 0, -1), (abs(local.x - hi.x), 0, 1),
             (abs(local.y - lo.y), 1, -1), (abs(local.y - hi.y), 1, 1)]
    _d, axis, sign = min(faces)
    local[axis] = lo[axis] if sign < 0 else hi[axis]
    point = m @ local
    normal = (m.to_3x3() @ Vector([float(sign) if i == axis else 0.0 for i in range(3)])).normalized()
    return point, normal, 0.14, True


def _facing_deg(normal):
    """Rotation (degrees) that turns a piece's local front (-Y) to `normal`."""
    return math.degrees(math.atan2(normal.x, -normal.y))


def opening(target_wall, center, width=1.0, height=1.2, name=None):
    """Cut a rectangular hole in a wall (kept live as a Boolean modifier).

    Thin walls get a hole right through. A solid block (house body) gets a 0.2 m deep
    recess on its nearest face, so the window or door sits in it.
    center is the world centre of the hole. Returns the hidden cutter object.
    """
    w = _h.obj(target_wall)
    point, normal, _depth, block = _wall_frame(w, center)
    W, H = num(width, 1.0), num(height, 1.2)
    deep = 0.4 if block else w.dimensions[_thin_axis(w)] + 0.3
    cutter = _h.box(name or f"{w.name}_Hole", (W, deep, H), (0, 0, 0),
                    w.users_collection[0] if w.users_collection else None)
    cutter.rotation_euler = (0.0, 0.0, math.radians(_facing_deg(normal)))
    cutter.location = point
    bpy.context.view_layer.update()
    from .shapes import boolean
    boolean(w, cutter)
    return cutter


def window(name, width=1.0, height=1.2, location=(0, 0, 1.5), wall=None, collection=None,
           frame_material=None, glass_material=None, facing=None, frame=0.07, depth=None, bars=True,
           sill=True, cut=True):
    """A window with frame, glass, cross bars and a sill. location is the window's CENTRE.

    With wall=..., the window snaps into the wall's thickness, cuts a hole through it
    (cut=True) and its sill faces away from the scene centre unless facing is given
    ('-Y', '+Y', '-X', '+X').
    """
    W, H, f = num(width, 1.0), num(height, 1.2), num(frame, 0.07)
    p = Vector(vec(location))
    wall_obj = _h.obj(wall) if wall is not None else None
    if wall_obj is not None:
        p, normal, wall_depth, _block = _wall_frame(wall_obj, p)
        D = num(depth, wall_depth)
        rot = FACING_DEG.get(str(facing).upper(), _facing_deg(normal)) if facing else _facing_deg(normal)
    else:
        D = num(depth, 0.12)
        rot = FACING_DEG.get(str(facing or "-Y").upper(), 0.0)
    fm = _mat(frame_material, "Window_Frame", (0.92, 0.9, 0.85), 0.6)
    gm = _mat(glass_material, "Window_Glass", (0.05, 0.08, 0.12), 0.1)
    root = _root(name, collection)
    _part(root, "Frame_Top", (W, D, f), (0, 0, H / 2 - f / 2), fm, 0.008)
    _part(root, "Frame_Bottom", (W, D, f), (0, 0, -H / 2 + f / 2), fm, 0.008)
    _part(root, "Frame_Left", (f, D, H - 2 * f), (-W / 2 + f / 2, 0, 0), fm, 0.008)
    _part(root, "Frame_Right", (f, D, H - 2 * f), (W / 2 - f / 2, 0, 0), fm, 0.008)
    _part(root, "Glass", (W - 2 * f, 0.012, H - 2 * f), (0, 0, 0), gm)
    if bars:
        b = f * 0.5
        _part(root, "Bar_Vertical", (b, D * 0.6, H - 2 * f), (0, 0, 0), fm)
        _part(root, "Bar_Horizontal", (W - 2 * f, D * 0.6, b), (0, 0, 0), fm)
    if sill:
        _part(root, "Sill", (W + 0.12, 0.14, 0.05), (0, -D / 2 - 0.04, -H / 2 - 0.025), fm, 0.01)
    _place_root(root, p, rot)
    if wall_obj is not None and cut:
        opening(wall_obj, p, W - 0.01, H - 0.01, name=f"{name}_Hole")
    return root


def door(name, width=0.9, height=2.0, location=(0, 0, 0), wall=None, collection=None, material=None,
         frame_material=None, knob_material=None, facing=None, frame=0.08, depth=None, knob=True, planks=4,
         cut=True):
    """A wooden door with frame, plank lines and a knob. location is the BOTTOM CENTRE (on the floor).

    With wall=..., it snaps into the wall, cuts the opening and faces away from the scene centre.
    """
    W, H, f = num(width, 0.9), num(height, 2.0), num(frame, 0.08)
    p = Vector(vec(location))
    wall_obj = _h.obj(wall) if wall is not None else None
    if wall_obj is not None:
        p, normal, wall_depth, _block = _wall_frame(wall_obj, p)
        D = num(depth, wall_depth)
        rot = FACING_DEG.get(str(facing).upper(), _facing_deg(normal)) if facing else _facing_deg(normal)
    else:
        D = num(depth, 0.12)
        rot = FACING_DEG.get(str(facing or "-Y").upper(), 0.0)
    wood = _mat(material, "Door_Wood", (0.42, 0.24, 0.12), 0.65)
    fm = _mat(frame_material, "Door_Frame", (0.3, 0.18, 0.1), 0.65)
    km = _mat(knob_material, "Brass", (0.85, 0.65, 0.25), 0.3, 1.0)
    root = _root(name, collection)
    _part(root, "Frame_Left", (f, D, H), (-W / 2 - f / 2, 0, H / 2), fm, 0.01)
    _part(root, "Frame_Right", (f, D, H), (W / 2 + f / 2, 0, H / 2), fm, 0.01)
    _part(root, "Frame_Top", (W + 2 * f, D, f), (0, 0, H + f / 2), fm, 0.01)
    panel_d = min(0.05, D * 0.6)
    _part(root, "Panel", (W, panel_d, H), (0, -D / 2 + panel_d / 2 + 0.02, H / 2), wood, 0.006)
    n = int(num(planks, 4))
    for i in range(1, n):
        x = -W / 2 + W * i / n
        _part(root, f"Plank_Line{i}", (0.012, 0.01, H * 0.96), (x, -D / 2 + 0.017, H / 2), fm)
    if knob:
        k = sphere(f"{name}_Knob", 0.035, (W / 2 - 0.12, -D / 2 - 0.01, H * 0.48), material=km, segments=16, rings=8)
        _attach(root, k)
    _place_root(root, p, rot)
    if wall_obj is not None and cut:
        opening(wall_obj, p + Vector((0, 0, (H + f) / 2 - 0.05)), W + 2 * f - 0.01, H + f + 0.1, name=f"{name}_Hole")
    return root


def path(name, points=((0, -2), (0, -5)), collection=None, material=None, width=0.8, stones=True,
         thickness=0.04, z=0.0, seed=3):
    """A garden path along (x, y) points on the ground.

    stones=True lays chunky stepping stones (low poly, slightly random); False makes one flat strip.
    """
    pts = [Vector(vec(p, 2) + (0,)) for p in points]
    if len(pts) < 2:
        raise ValueError("path needs at least 2 points.")
    W, T, z0 = num(width, 0.8), num(thickness, 0.04), num(z)
    stone = _mat(material, "Path_Stone", (0.55, 0.52, 0.48), 0.9)
    root = _root(name, collection)
    if not stones:
        left, right = [], []
        for i, p in enumerate(pts):
            a = pts[max(0, i - 1)]
            b = pts[min(len(pts) - 1, i + 1)]
            d = (b - a).normalized()
            side = Vector((-d.y, d.x, 0)) * (W / 2)
            left.append(p + side)
            right.append(p - side)
        outline = [(v.x, v.y) for v in left] + [(v.x, v.y) for v in reversed(right)]
        strip = extrude_profile(f"{name}_Strip", outline, T, (0, 0, z0 + T / 2), material=stone, axis="Z")
        _attach(root, strip)
        return root
    rng = random.Random(int(num(seed, 3)))
    spacing = W * 0.75
    k = 0
    for a, b in zip(pts, pts[1:]):
        seg = b - a
        steps = max(1, int(seg.length / spacing))
        for i in range(steps):
            c = a + seg * ((i + 0.5) / steps)
            r = W * rng.uniform(0.32, 0.42)
            k += 1
            s = cylinder(f"{name}_Stone{k}", r, T, (c.x + rng.uniform(-0.06, 0.06), c.y, z0 + T / 2),
                         segments=7, material=stone, smooth=False)
            s.rotation_euler.z = rng.uniform(0, math.pi)
            s.scale.x = rng.uniform(0.85, 1.15)
            _attach(root, s)
    return root


# ----------------------------------------------------------------------
# Furniture (front faces -Y at rot_deg=0; location = floor point under the centre)
# ----------------------------------------------------------------------


def table(name, size=(1.2, 0.7, 0.75), location=(0, 0, 0), collection=None, material=None, rot_deg=0,
          against=None, top=0.04, leg=0.05, leg_material=None):
    """A table: top + 4 legs. size = (width, depth, height)."""
    w, d, h = vec(size, 3, 0.75)
    wood = _mat(material, "Wood", (0.55, 0.35, 0.18), 0.6)
    lm = _mat(leg_material, wood.name, (0.55, 0.35, 0.18)) if leg_material is not None else wood
    t, L = num(top, 0.04), num(leg, 0.05)
    root = _root(name, collection)
    _part(root, "Top", (w, d, t), (0, 0, h - t / 2), wood, 0.006)
    for i, (sx, sy) in enumerate(((-1, -1), (1, -1), (-1, 1), (1, 1)), 1):
        _part(root, f"Leg{i}", (L, L, h - t), (sx * (w / 2 - L), sy * (d / 2 - L), (h - t) / 2), lm, 0.004)
    return _place_root(root, location, rot_deg, against)


def desk(name, size=(1.4, 0.7, 0.75), location=(0, 0, 0), collection=None, material=None, rot_deg=0,
         against=None, drawers=True):
    """A desk: a table with an optional drawer block on the right."""
    root = table(name, size, (0, 0, 0), collection, material)
    w, d, h = vec(size, 3, 0.75)
    if drawers:
        wood = root.children[0].active_material
        block = _part(root, "Drawers", (w * 0.3, d * 0.9, h * 0.55), (w / 2 - w * 0.17, 0, h - 0.04 - h * 0.275), wood, 0.006)
        for i in range(3):
            _part(root, f"Drawer_Handle{i + 1}", (w * 0.08, 0.02, 0.015),
                  (block.location.x, -d * 0.45 - 0.01, h - 0.1 - i * h * 0.16), _mat(None, "Metal_Dark", (0.12, 0.12, 0.13), 0.35, 1.0))
    return _place_root(root, location, rot_deg, against)


def chair(name, location=(0, 0, 0), collection=None, material=None, rot_deg=0, at=None, seat_height=0.45,
          size=(0.45, 0.45), back_height=0.45, cushion_material=None):
    """A chair facing -Y (its back is on the +Y side). rot_deg=180 makes it face +Y.

    at='Desk' (or a table): ignore location/rot_deg and tuck the chair in front of it, facing it.
    """
    w, d = vec(size, 2, 0.45)
    sh, bh = num(seat_height, 0.45), num(back_height, 0.45)
    wood = _mat(material, "Wood", (0.55, 0.35, 0.18), 0.6)
    cushion = _mat(cushion_material, "Cushion", (0.25, 0.3, 0.38), 0.9)
    L = 0.04
    root = _root(name, collection)
    for i, (sx, sy) in enumerate(((-1, -1), (1, -1), (-1, 1), (1, 1)), 1):
        _part(root, f"Leg{i}", (L, L, sh - 0.04), (sx * (w / 2 - L / 2), sy * (d / 2 - L / 2), (sh - 0.04) / 2), wood, 0.004)
    _part(root, "Seat", (w, d, 0.04), (0, 0, sh - 0.02), wood, 0.006)
    _part(root, "Cushion", (w * 0.92, d * 0.9, 0.04), (0, -0.01, sh + 0.02), cushion, 0.015)
    _part(root, "Back", (w, 0.04, bh), (0, d / 2 - 0.02, sh + bh / 2), wood, 0.008)
    if at is not None:
        from .shapes import bounds
        host = _h.obj(at)
        host_depth = _depth_of(host)
        spot, rz = _on_top_of(host, (0, -(host_depth / 2 + d * 0.15)))
        return _place_root(root, (spot[0], spot[1], host.matrix_world.translation.z), rz + 180)
    return _place_root(root, location, rot_deg)


def bed(name, size=(1.6, 2.1), location=(0, 0, 0), collection=None, frame_material=None, rot_deg=0,
        against=None, height=0.5, headboard=1.0, sheet_material=None, blanket_material=None, pillow_material=None):
    """A bed. size = (width, length) of the mattress. The headboard is on the +Y end
    (against='Wall' puts the headboard on that wall)."""
    w, L = vec(size, 2, 1.6)
    H, hb = num(height, 0.5), num(headboard, 1.0)
    frame_m = _mat(frame_material, "Bed_Frame", (0.42, 0.28, 0.16), 0.6)
    sheet = _mat(sheet_material, "Sheet", (0.92, 0.92, 0.9), 0.9)
    blanket = _mat(blanket_material, "Blanket", (0.3, 0.42, 0.6), 0.95)
    pillow = _mat(pillow_material, "Pillow", (0.95, 0.95, 0.93), 0.9)
    root = _root(name, collection)
    base_h = H * 0.55
    _part(root, "Base", (w + 0.08, L + 0.08, base_h - 0.08), (0, 0, 0.08 + (base_h - 0.08) / 2), frame_m, 0.01)
    for i, (sx, sy) in enumerate(((-1, -1), (1, -1), (-1, 1), (1, 1)), 1):
        _part(root, f"Leg{i}", (0.07, 0.07, 0.08), (sx * (w / 2 - 0.02), sy * (L / 2 - 0.02), 0.04), frame_m)
    mat_h = H - base_h
    _part(root, "Mattress", (w, L, mat_h), (0, 0, base_h + mat_h / 2), sheet, 0.04)
    _part(root, "Blanket", (w + 0.04, L * 0.68, 0.05), (0, -L / 2 + L * 0.34 - 0.01, H + 0.005), blanket, 0.02)
    count = 2 if w >= 1.2 else 1
    for i in range(count):
        x = 0 if count == 1 else (-w / 4 if i == 0 else w / 4)
        _part(root, f"Pillow{i + 1}", (w / count - 0.12, 0.38, 0.12), (x, L / 2 - 0.26, H + 0.06), pillow, 0.05)
    _part(root, "Headboard", (w + 0.08, 0.08, hb), (0, L / 2 + 0.04, hb / 2), frame_m, 0.012)
    return _place_root(root, location, rot_deg, against)


def wardrobe(name, size=(1.2, 0.6, 2.0), location=(0, 0, 0), collection=None, material=None, rot_deg=0,
             against=None, doors=2, handle_material=None):
    """A wardrobe / closet with doors on the -Y side. size = (width, depth, height)."""
    w, d, h = vec(size, 3, 2.0)
    body = _mat(material, "Wardrobe_Wood", (0.72, 0.58, 0.42), 0.6)
    hm = _mat(handle_material, "Metal_Dark", (0.12, 0.12, 0.13), 0.35, 1.0)
    root = _root(name, collection)
    _part(root, "Body", (w, d, h - 0.08), (0, 0, 0.08 + (h - 0.08) / 2), body, 0.01)
    _part(root, "Plinth", (w - 0.06, d - 0.06, 0.08), (0, 0.02, 0.04), hm)
    n = max(1, int(num(doors, 2)))
    gap = 0.006
    dw = (w - 0.04 - gap * (n - 1)) / n
    for i in range(n):
        x = -w / 2 + 0.02 + dw / 2 + i * (dw + gap)
        _part(root, f"Door{i + 1}", (dw, 0.02, h - 0.14), (x, -d / 2 - 0.01, 0.08 + (h - 0.08) / 2), body, 0.004)
        hx = x + (dw / 2 - 0.06) * (1 if i % 2 == 0 else -1) if n > 1 else x + dw / 2 - 0.06
        _part(root, f"Handle{i + 1}", (0.02, 0.025, 0.25), (hx, -d / 2 - 0.035, h * 0.55), hm, 0.004)
    return _place_root(root, location, rot_deg, against)


def closet(name, size=(1.2, 0.6, 2.0), location=(0, 0, 0), collection=None, material=None, rot_deg=0,
           against=None, doors=2, handle_material=None):
    """Same as wardrobe."""
    return wardrobe(name, size, location, collection, material, rot_deg=rot_deg, against=against, doors=doors,
                    handle_material=handle_material)


def shelf(name, size=(0.8, 0.3, 1.8), location=(0, 0, 0), collection=None, material=None, rot_deg=0,
          against=None, shelves=4, books=True, seed=7):
    """An open bookshelf (open side -Y) with optional colourful books."""
    w, d, h = vec(size, 3, 1.8)
    wood = _mat(material, "Wood", (0.55, 0.35, 0.18), 0.6)
    t = 0.025
    root = _root(name, collection)
    _part(root, "Side_L", (t, d, h), (-w / 2 + t / 2, 0, h / 2), wood, 0.003)
    _part(root, "Side_R", (t, d, h), (w / 2 - t / 2, 0, h / 2), wood, 0.003)
    _part(root, "Back", (w, 0.01, h), (0, d / 2 - 0.005, h / 2), wood)
    n = max(2, int(num(shelves, 4)))
    rng = random.Random(int(num(seed, 7)))
    palette = [(0.6, 0.15, 0.12), (0.15, 0.3, 0.5), (0.85, 0.7, 0.3), (0.2, 0.42, 0.28), (0.9, 0.88, 0.82)]
    for i in range(n + 1):
        z = t / 2 + i * (h - t) / n
        _part(root, f"Board{i + 1}", (w - 2 * t, d - 0.01, t), (0, -0.005, z), wood, 0.003)
        if books and i < n:
            x = -w / 2 + t + 0.01
            k = 0
            gap_h = (h - t) / n - t
            while x < w / 2 - t - 0.06 and rng.random() > 0.08:
                bw = rng.uniform(0.025, 0.05)
                bh = gap_h * rng.uniform(0.6, 0.85)
                k += 1
                col = palette[rng.randrange(len(palette))]
                _part(root, f"Book{i + 1}_{k}", (bw, d * 0.75, bh), (x + bw / 2, -0.01, z + t / 2 + bh / 2),
                      _h.material(f"Book_{palette.index(col)}", col, 0.8))
                x += bw + 0.003
    return _place_root(root, location, rot_deg, against)


def monitor(name, location=(0, 0, 0.75), collection=None, material=None, rot_deg=0, on=None,
            size=(0.6, 0.35), screen_material=None):
    """A computer monitor standing on a desk. location = point on the desk under its stand.

    on='Desk': ignore location/rot_deg; stand at the back of that desk, facing its front.
    """
    sw, sh = vec(size, 2, 0.6)
    body = _mat(material, "Plastic_Black", (0.03, 0.03, 0.035), 0.4)
    screen = screen_material if screen_material is not None else _h.material(
        "Screen_Glow", (0.05, 0.1, 0.2), 0.2, emission=(0.2, 0.45, 0.9), emission_strength=1.5)
    screen = _h.as_material(screen)
    root = _root(name, collection)
    _part(root, "Base", (0.22, 0.16, 0.015), (0, 0.03, 0.0075), body, 0.004)
    _part(root, "Neck", (0.04, 0.03, 0.12), (0, 0.06, 0.07), body)
    _part(root, "Bezel", (sw, 0.025, sh), (0, 0.03, 0.12 + sh / 2), body, 0.004)
    _part(root, "Screen", (sw - 0.02, 0.004, sh - 0.02), (0, 0.016, 0.12 + sh / 2), screen)
    if on is not None:
        location, rot_deg = _on_top_of(on, (0, _depth_of(on) * 0.22))
    return _place_root(root, location, rot_deg)


def keyboard(name, location=(0, 0, 0.75), collection=None, material=None, rot_deg=0, on=None,
             size=(0.44, 0.14, 0.025)):
    """A keyboard lying on a desk. location = point on the desk under its centre.

    on='Desk': ignore location/rot_deg; lie near the front edge of that desk.
    """
    w, d, h = vec(size, 3, 0.025)
    body = _mat(material, "Plastic_Black", (0.03, 0.03, 0.035), 0.4)
    keys = _h.material("Keys_Grey", (0.2, 0.2, 0.22), 0.5)
    root = _root(name, collection)
    _part(root, "Base", (w, d, h * 0.6), (0, 0, h * 0.3), body, 0.003)
    _part(root, "Keys", (w - 0.02, d - 0.02, h * 0.4), (0, 0, h * 0.8), keys, 0.002)
    if on is not None:
        location, rot_deg = _on_top_of(on, (0, -_depth_of(on) * 0.2))
    return _place_root(root, location, rot_deg)


def pc_tower(name, location=(0, 0, 0), collection=None, material=None, rot_deg=0, against=None,
             size=(0.2, 0.45, 0.42), glow=True):
    """A PC tower case standing on the floor (or a desk). Front faces -Y."""
    w, d, h = vec(size, 3, 0.42)
    body = _mat(material, "Plastic_Black", (0.03, 0.03, 0.035), 0.4)
    root = _root(name, collection)
    _part(root, "Case", (w, d, h), (0, 0, h / 2 + 0.01), body, 0.006)
    if glow:
        accent = _h.material("RGB_Glow", (0.1, 0.6, 0.9), 0.3, emission=(0.1, 0.6, 1.0), emission_strength=3.0)
        _part(root, "Light_Strip", (0.01, 0.005, h * 0.7), (0, -d / 2 - 0.002, h / 2 + 0.01), accent)
    return _place_root(root, location, rot_deg, against)


def rug(name, size=(2.0, 1.4), location=(0, 0, 0), collection=None, material=None, thickness=0.012,
        round_rug=False):
    """A rug on the floor. round_rug=True makes an oval."""
    w, d = vec(size, 2, 2.0)
    t = num(thickness, 0.012)
    m = _mat(material, "Rug", (0.6, 0.25, 0.2), 0.95)
    x, y, z = vec(location)
    if round_rug:
        o = cylinder(name, 0.5, t, (x, y, z + t / 2), collection, m, segments=48, smooth=False)
        o.scale = (w, d, 1)
        return o
    return _h.box(name, (w, d, t), (x, y, z + t / 2), collection, m, 0.004)


def lamp(name, location=(0, 0, 0), collection=None, material=None, height=1.6, light=True, power=60,
         shade_material=None):
    """A floor lamp with a fabric shade (and a real point light inside when light=True)."""
    H = num(height, 1.6)
    metal = _mat(material, "Metal_Dark", (0.12, 0.12, 0.13), 0.35, 1.0)
    shade = _mat(shade_material, "Lamp_Shade", (0.95, 0.88, 0.7), 0.9)
    root = _root(name, collection)
    _attach(root, cylinder(f"{name}_Base", 0.15, 0.03, (0, 0, 0.015), material=metal, segments=24))
    _attach(root, cylinder(f"{name}_Pole", 0.015, H - 0.2, (0, 0, (H - 0.2) / 2 + 0.03), material=metal, segments=12))
    from .shapes import cone
    _attach(root, cone(f"{name}_Shade", 0.22, 0.14, 0.28, (0, 0, H - 0.1), material=shade, segments=32, caps=False))
    if light:
        _h.claim_name(f"{name}_Light")
        data = bpy.data.lights.new(f"{name}_Light", "POINT")
        data.energy = num(power, 60)
        data.color = (1.0, 0.85, 0.65)
        bulb = bpy.data.objects.new(f"{name}_Light", data)
        bulb.location = (0, 0, H - 0.12)
        _attach(root, bulb)
    return _place_root(root, location, 0)


def plant(name, location=(0, 0, 0), collection=None, leaf_material=None, height=0.8, seed=5, pot_material=None):
    """A potted plant: a tapered pot and a cluster of low poly leaf blobs."""
    H = num(height, 0.8)
    pot = _mat(pot_material, "Terracotta", (0.7, 0.35, 0.2), 0.8)
    leaf = _mat(leaf_material, "Leaf_Green", (0.18, 0.45, 0.2), 0.7)
    rng = random.Random(int(num(seed, 5)))
    root = _root(name, collection)
    from .shapes import cone
    ph = H * 0.35
    _attach(root, cone(f"{name}_Pot", ph * 0.45, ph * 0.6, ph, (0, 0, ph / 2), material=pot, segments=16, smooth=False))
    for i in range(6):
        a = i * math.pi / 3 + rng.uniform(-0.3, 0.3)
        r = H * rng.uniform(0.12, 0.2)
        dist = H * 0.12 if i else 0.0
        blob = sphere(f"{name}_Leaves{i + 1}", r, (math.cos(a) * dist, math.sin(a) * dist,
                                                   ph + H * rng.uniform(0.2, 0.45)), material=leaf, segments=8,
                      rings=6, smooth=False)
        _attach(root, blob)
    return _place_root(root, location, 0)


# Forgiving arguments (aliases, odd slots, unknown extras), like the shapes.
from .shapes import forgiving  # noqa: E402

for _name in __all__:
    globals()[_name] = forgiving(globals()[_name])
