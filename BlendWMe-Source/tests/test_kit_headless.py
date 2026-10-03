"""Headless tests of BlendWMe's hands: the executor, repairs, the pre-check, look, the bwm
modelling kit, the worked example cards and inspect_api.

Run (no brain and no subscription is used):

    blender -b --factory-startup --python-exit-code 1 --python tests/test_kit_headless.py

These checks came from the 1.0.5 suite. The checks of the old pay-per-token agent loop were
removed with that loop; the brains' tools are tested in tests/test_v2_headless.py.
"""

import json
import os
import sys
import tempfile

# Blender buffers stdout when it is piped, and os._exit() at the end skips the
# flush, so print each line straight away.
sys.stdout.reconfigure(line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
# Never write test logs into the real logs folder.
os.environ["BLENDWME_LOG_FOLDER"] = os.path.join(tempfile.gettempdir(), "bwm_logs_test")

import bpy  # noqa: E402

from blendwme import prefs  # noqa: E402

passed = []
LOGDIR = os.path.join(tempfile.gettempdir(), "bwm_logs_test")


def check(name, cond, detail=""):
    assert cond, f"FAILED {name} {detail}"
    passed.append(name)
    print("ok -", name)


def fresh_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


prefs.register()
fresh_scene()

# ----------------------------------------------------------------------
# 1. Safety blocklist, and bwm as an importable module during a step
# ----------------------------------------------------------------------
from blendwme import executor  # noqa: E402
check("blocks os", executor.run("import os\nos.remove('x')")["ok"] is False)
check("blocks quitting", executor.run("bpy.ops.wm.quit_blender()")["ok"] is False)

check("blocks clipboard reads", executor.run("print(C.window_manager.clipboard)")["ok"] is False)
check("blocks importlib", executor.run("import importlib")["ok"] is False)
check("blocks reaching the add-on", executor.run("import bl_ext.user_default.blendwme.relay")["ok"] is False)
check("allows environment textures", executor.run("n = 'ShaderNodeTexEnvironment'")["ok"] is True)

# Fix A1: bwm can be imported like a module, only while a step runs
r = executor.run("import bwm\nfrom bwm import box, material\nbox('ImportedBox', (1, 1, 1), material=material('ImpMat', (1, 0, 0)))")
check("import bwm works", r["ok"] and "ImportedBox" in bpy.data.objects, r)
check("bwm module only exists during a step", "bwm" not in sys.modules)
r = executor.run("from bwm import made_up_helper")
check("unknown bwm import caught before running", r["ok"] is False and "made_up_helper" in r["error"], r)

# 2. Error hints and notes about existing objects
executor.run("bwm.box('Door', (1, 1, 2), (0, 0, 1))")
executor.run("bwm.collection('Props')")
r = executor.run("bwm.move_to('Door', 'prop')")
check("bad collection name explained", "No collection named" in r["error"] and "Similar" in r["error"], r)
r = executor.run("x = 'abc'\nx.objects")
check("str-instead-of-object hint", "needs the object itself" in r.get("hint", ""), r)
r = executor.run("D.objects['Door'].location.x += 0.5\nraise RuntimeError('boom')")
check("failed existing transform is restored with a checkpoint", not r["ok"]
      and r.get("recovery_checkpoint") and bpy.data.objects["Door"].location.x == 0, r)

# 3. look renders the views in background Blender (there is no viewport to draw)
from blendwme import look as look_mod, logs  # noqa: E402
engine_before, cam_before, n_before = bpy.context.scene.render.engine, bpy.context.scene.camera, len(bpy.data.objects)
shot = look_mod.capture("angles")
check("look works without a viewport", os.path.isfile(shot.get("path", "")) and shot.get("rendered"), shot)
nm = bpy.data.materials.new("NodeOnlyRed")
nm.use_nodes = True
next(n for n in nm.node_tree.nodes if n.type == "BSDF_PRINCIPLED").inputs["Base Color"].default_value = (0.9, 0.05, 0.05, 1)
seen = []
real_capture = look_mod._capture
look_mod._capture = lambda *a: seen.append(tuple(nm.diffuse_color)) or {"path": ""}
look_mod.capture("angles")
look_mod._capture = real_capture
check("captures show node materials in their real colour, then restore",
      seen and seen[0][0] > 0.85 and tuple(nm.diffuse_color)[0] < 0.85, (seen, tuple(nm.diffuse_color)))
check("render look leaves the scene as it was", bpy.context.scene.render.engine == engine_before
      and bpy.context.scene.camera == cam_before and len(bpy.data.objects) == n_before)

# 4. The pre-check stops invented API names before anything runs
r = executor.run("bpy.ops.mesh.primitive_cube_add()\nslab = bpy.active_object\nslab.name = 'Slab'")
check("bpy.active_object repaired, not rejected", r["ok"] and "Slab" in bpy.data.objects
      and any("bpy.context" in n for n in r.get("auto_repaired", [])), r)
r = executor.run("bpy.ops.mesh.primitive_cube_add()\nx = bpy.not_a_real_thing")
check("unrepairable names still caught before running", "does not exist" in r["error"]
      and not any(o.name.startswith("Cube") for o in bpy.data.objects), r)
r = executor.run("import bmesh\nbm = bmesh.new()\nbm.new((0,0,0))")
check("pre-check catches bm.new", "bm.verts.new" in r["error"], r)

# 5. Logs never keep a secret
p = logs.save([{"role": "user", "content": "key is sk-SECRET-123456789"}], [], {"title": "x"}, LOGDIR, "sk-SECRET-123456789")
check("key scrubbed from logs", "sk-SECRET" not in open(p, encoding="utf-8").read())

# 6. Legacy API repairs, hidden collections, swapped modifier arguments
r = executor.run("o = D.objects.new('L', D.meshes.new('L'))\nbpy.context.scene.objects.link(o)")
check("legacy 2.7 link repaired", r["ok"] and "L" in bpy.context.scene.objects, r)
r = executor.run("import bmesh\nbm = bmesh.new()\nbmesh.ops.create_cone(bm, radius=1, depth=1, segments=4)\n"
                 "me = D.meshes.new('ConeM')\nbm.to_mesh(me)\nprint(round(max(v.co.x for v in me.vertices), 2))")
check("create_cone radius repaired to radius1/radius2", r["ok"] and "1.0" in r.get("printed", ""), r)
hidden = bpy.data.collections.new("HiddenCottage")   # exists in the file, not in the scene
r = executor.run("c = bwm.collection('HiddenCottage')\nbwm.box('H1', (1, 1, 1), collection=c)")
check("hidden collection relinked so work is visible", "H1" in bpy.context.scene.objects and not r.get("warning"), r)
r = executor.run("o = bwm.box('H2', (1, 1, 1))\nC.scene.collection.objects.unlink(o)")
check("invisible new objects are reported", "NOT in the scene" in r.get("warning", ""), r)
r = executor.run("o = bwm.box('H3', (1,1,1))\no.modifiers.new('SOLIDIFY', 'Solidify')")
check("swapped modifier arguments repaired", r["ok"] and bpy.data.objects["H3"].modifiers[0].type == "SOLIDIFY"
      and any("swapped" in n for n in r["auto_repaired"]), r)

# Fix A2: more mechanical slips are repaired, run, and reported
# EEVEE's engine id: BLENDER_EEVEE_NEXT in 4.2 to 4.4, BLENDER_EEVEE again from 5.0.
EEVEE_ID = next(i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
                if i.identifier.startswith("BLENDER_EEVEE"))
REPAIRS = [
    ("diffuse_color with 3 values", "m = bwm.material('RepA', (1, 0, 0))\nm.diffuse_color = (0.2, 0.4, 0.6)",
     lambda: tuple(round(v, 1) for v in bpy.data.materials['RepA'].diffuse_color) == (0.2, 0.4, 0.6, 1.0)),
    ("socket colour with 3 values", "m = bwm.material('RepB')\nb = next(n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')\n"
     "b.inputs['Base Color'].default_value = (0.1, 0.2, 0.3)",
     lambda: round(bpy.data.materials['RepB'].node_tree.nodes['Principled BSDF'].inputs[0].default_value[2], 1) == 0.3),
    ("renamed Principled input", "m = bwm.material('RepC')\nb = next(n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')\n"
     "b.inputs['Specular'].default_value = 0.7\nb.inputs['Emission'].default_value = (1, 1, 0, 1)",
     lambda: round(bpy.data.materials['RepC'].node_tree.nodes['Principled BSDF'].inputs['Specular IOR Level'].default_value, 1) == 0.7),
    ("removed 2.7x property skipped", "w = C.scene.world or D.worlds.new('W')\nw.light_settings.use_ambient_occlusion = True\n"
     "bwm.box('AfterSkip', (1, 1, 1))", lambda: "AfterSkip" in bpy.data.objects),
    ("obj.select = True", "o = bwm.box('Sel', (1, 1, 1))\no.select = True", lambda: bpy.data.objects['Sel'].select_get()),
    ("engine id from another version",
     "C.scene.render.engine = " + repr("BLENDER_EEVEE" if EEVEE_ID == "BLENDER_EEVEE_NEXT" else "BLENDER_EEVEE_NEXT"),
     lambda: C_scene().render.engine == EEVEE_ID),
    ("enum in lower case", "o = bwm.box('Wire', (1, 1, 1))\no.display_type = 'wire'", lambda: bpy.data.objects['Wire'].display_type == 'WIRE'),
    ("one number for a vector", "o = bwm.box('Sc', (1, 1, 1))\no.scale = 2", lambda: tuple(bpy.data.objects['Sc'].scale) == (2, 2, 2)),
    ("operator keywords and name", "bpy.ops.mesh.primitive_cylinder_add(radius=0.2, height=3, segments=12, name='Pole')",
     lambda: "Pole" in bpy.data.objects and round(bpy.data.objects['Pole'].dimensions.z, 1) == 3.0
     and len(bpy.data.objects['Pole'].data.vertices) == 24),
    ("modifier with type only, lower case", "o = bwm.box('ModT', (1, 1, 1))\no.modifiers.new(type='bevel')\no.modifiers.new('Subdivision')",
     lambda: [m.type for m in bpy.data.objects['ModT'].modifiers] == ['BEVEL', 'SUBSURF']),
    ("node by its label", "m = bwm.material('RepN')\nn = m.node_tree.nodes.new('Principled BSDF')\nn2 = m.node_tree.nodes.new('ShaderNodePrincipledBsdf')",
     lambda: sum(1 for n in bpy.data.materials['RepN'].node_tree.nodes if n.type == 'BSDF_PRINCIPLED') == 3),
    ("D used as bpy / bpy.context (live round 3)",
     "D.ops.mesh.primitive_cone_add(vertices=8)\nD.objects.active.name = 'DCone'\nm = D.data.materials.new('DMat')\n"
     "D.objects['DCone'].select_set(True)\nprint(D.scene.name)",
     lambda: "DCone" in bpy.data.objects and "DMat" in bpy.data.materials),
    ("material_slots.append", "o = bwm.box('Slot', (1, 1, 1))\no.material_slots.append(bwm.material('RepS'))",
     lambda: bpy.data.objects['Slot'].material_slots[0].material.name == 'RepS'),
]


def C_scene():
    return bpy.context.scene


for label, code, ok in REPAIRS:
    r = executor.run(code)
    check("repaired: " + label, r["ok"] and r.get("auto_repaired") and ok(), r)

# Background Blender: transform macros used to crash Blender (borrowed viewport)
r = executor.run("bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=1)\nC.active_object.name = 'Macro'\n"
                 "bpy.ops.object.mode_set(mode='EDIT')\nbpy.ops.mesh.select_all(action='SELECT')\n"
                 "bpy.ops.mesh.extrude_region_move(TRANSFORM_OT_translate={'value': (0, 0, 1)})\n"
                 "bpy.ops.object.mode_set(mode='OBJECT')\nbpy.ops.object.duplicate_move()")
check("extrude and duplicate macros run in background Blender", r["ok"] and "Macro" in bpy.data.objects, r)

r = executor.run("m = bwm.material('RepL', (1, 0, 0))\nm.diffuse_color = (1, 1, 1)\nundefined_after_repair()")
check("errors after a repair keep the model's line number", r["ok"] is False and "line 3" in r["error"]
      and "undefined_after_repair" in r["error"], r)
r = executor.run("o = C.scene.objects.get('Nope')\no.location = (1, 2, 3)")
check("real mistakes are not hidden by repairs", r["ok"] is False and "NoneType" in r["error"], r)
r = executor.run("o = bwm.box('RO', (1, 1, 1))\no.dimensions = (2, 2, 2)\nprint('dims', tuple(o.dimensions))")
check("normal property sets are untouched", r["ok"] and "auto_repaired" not in r, r)

# 7. Values sent as text are converted using the tool schema; plan parts in any shape
from blendwme.tool_host import _coerce_types, _plan_parts  # noqa: E402
check("plan accepts steps= as a JSON string",
      _plan_parts({"title": "x", "steps": '["Ground plane", "Walls"]'}) == ["Ground plane", "Walls"])
check("plan accepts one part per line and dict parts",
      _plan_parts({"parts": "1. Base\n2. Roof"}) == ["Base", "Roof"]
      and _plan_parts({"parts": [{"name": "Body", "size": "2 m"}]}) == ["Body"])
fixed = _coerce_types("plan", {"parts": '["Base", "Walls"]'})
check("array sent as text is converted", fixed["parts"] == ["Base", "Walls"])
fixed = _coerce_types("run_python", {"title": "x", "code": "print(1)", "part": "3"})
check("integer sent as text is converted, code untouched", fixed["part"] == 3 and fixed["code"] == "print(1)")
fixed = _coerce_types("look", {"question": "q", "compare_reference": "True"})
check("boolean sent as text is converted", fixed["compare_reference"] is True)

# ----------------------------------------------------------------------
# 7i. Fix A3: the bwm modelling kit (shapes, placement, architecture, furniture)
# ----------------------------------------------------------------------
import math  # noqa: E402
from blendwme import helpers as bwm_h, knowledge  # noqa: E402
fresh_scene()
HOUSE = """
col = bwm.collection('Cottage')
bwm.box('Base', (4.4, 3.4, 0.25), (0, 0, 0.125), col, 'stone', bevel=0.03)
walls = bwm.box('Walls', (4, 3, 2.4), (0, 0, 1.45), col, '#e8d6b0')
roof = bwm.gable_roof('Roof', 4, 3, 1.3, (0, 0, 2.65), collection=col, gable_material='#e8d6b0')
ch = bwm.box('Chimney', (0.45, 0.45, 1.2), (1.2, 0.6, 4), col, (150, 60, 50))
bwm.place_on(ch, roof, sink=True)
bwm.door('Door', 0.9, 2.0, (0, -1.4, 0.25), wall=walls)
for i, x in enumerate((-1.3, 1.3)):
    bwm.window(f'Win_F{i}', 0.8, 1.0, (x, -1.2, 1.6), wall=walls)
    bwm.window(f'Win_B{i}', 0.8, 1.0, (x, 1.7, 1.6), wall='Walls')
bwm.path('Path', [(0, -1.9), (0.3, -3.5), (-0.2, -5)])
"""
r = executor.run(HOUSE, "house")
check("kit: cottage builds", r["ok"], r)
D_ = bpy.data
# Roof top surface under the chimney's low (front) edge: 2.65 + 1.3 - (1.3 / 1.5) * 0.825 + slab = 3.43
check("kit: chimney sunk into the roof slope", abs(bwm_h.bounds("Chimney").min.z - 3.43) < 0.03,
      bwm_h.bounds("Chimney"))
check("kit: windows snap onto the nearest face of a solid house body",
      abs(D_.objects["Win_F0"].location.y + 1.5) < 0.01 and abs(D_.objects["Win_B0"].location.y - 1.5) < 0.01)
check("kit: windows face outwards", abs(D_.objects["Win_F0"].rotation_euler.z) < 0.01
      and abs(abs(D_.objects["Win_B0"].rotation_euler.z) - math.pi) < 0.01)
check("kit: door and windows cut live holes", sum(1 for m in D_.objects["Walls"].modifiers if m.type == "BOOLEAN") == 5)
check("kit: gable roof underside meets the wall tops", abs(bwm_h.bounds("Roof_Gables").max.z - 3.95) < 0.01
      and abs(bwm_h.bounds("Roof_Gables").min.z - 2.65) < 0.01)

r = executor.run(HOUSE, "house again")
check("kit: re-running a step replaces the AI's earlier objects",
      r["ok"] and not [o.name for o in D_.objects if ".00" in o.name or ".replaced" in o.name]
      and any("Replaced" in n for n in r.get("helper_notes", [])), r)
r = executor.run(HOUSE, "house a third time")
check("kit: a replaced object is tagged as the AI's, so later re-runs replace it again",
      r["ok"] and D_.objects["Walls"].get("blendwme_author") == "ai"
      and not [o.name for o in D_.objects if ".00" in o.name or ".replaced" in o.name],
      [o.name for o in D_.objects if ".00" in o.name])
# Live round 4: replacing an object outside the active view layer failed the whole step
hidden_col = D_.collections.new("NotInLayer")              # not linked to the scene: not in the view layer
D_.objects["Chimney"].users_collection[0].objects.unlink(D_.objects["Chimney"])
hidden_col.objects.link(D_.objects["Chimney"])
r = executor.run("bwm.box('Chimney', (0.5, 0.5, 1.2), (1.2, 0.6, 4))")
check("kit: replacing an object outside the view layer works", r["ok"] and "Chimney" in D_.objects
      and D_.objects["Chimney"].get("blendwme_author") == "ai", r)
D_.objects["Base"]["blendwme_author"] = "user"           # the user takes ownership of the base
r = executor.run("bwm.box('Base', (1, 1, 1))\nbwm.box('Walls', (1, 1, 1))\nraise RuntimeError('late failure')")
check("kit: failed step restores the replaced objects", r["ok"] is False and tuple(round(v, 2) for v in D_.objects["Walls"].dimensions) == (4, 3, 2.4))
r = executor.run("bwm.box('Base', (1, 1, 1))")
check("kit: user objects are never replaced", "Base" in D_.objects and round(D_.objects["Base"].dimensions.x, 1) == 4.4
      and "Base.001" in D_.objects, [o.name for o in D_.objects if o.name.startswith("Base")])

ROOM = """
bwm.floor('Floor', (5, 4), (8, 0, 0))
bwm.wall('Wall_Back', (5.5, 2), (10.5, 2), 2.6)
bwm.wall('Wall_Left', (5.5, -2), (5.5, 2), 2.6)
bwm.bed('Bed', (1.6, 2.0), (6.6, 1, 0), against='Wall_Back')
bwm.desk('Desk', (1.4, 0.7, 0.75), (9.3, 1, 0), against='Wall_Back')
bwm.monitor('Monitor', on='Desk')
bwm.keyboard('Keyboard', on='Desk')
bwm.pc_tower('PC', (10.2, 1.0, 0), against='Wall_Back')
bwm.chair('Chair', at='Desk')
bwm.wardrobe('Closet', (1.2, 0.6, 2.0), (6, -1.0, 0), against='Wall_Left')
bwm.shelf('Shelf', (0.8, 0.3, 1.8), (8.1, 1, 0), against='Wall_Back')
bwm.rug('Rug', (2, 1.4), (8, 0, 0)); bwm.lamp('Lamp', (10.2, -1.5, 0)); bwm.plant('Plant', (7.5, -1.6, 0))
bwm.window('RoomWin', 1.2, 1.0, (8, 2, 1.5), wall='Wall_Back')
"""
r = executor.run(ROOM, "room")
check("kit: room with furniture builds", r["ok"], r)
check("kit: bed headboard against the back wall", abs(bwm_h.bounds("Bed").max.y - 1.915) < 0.03, bwm_h.bounds("Bed"))
check("kit: closet turned to face the room", abs(math.degrees(D_.objects["Closet"].rotation_euler.z) - 90) < 0.5
      and abs(bwm_h.bounds("Closet").min.x - 5.585) < 0.04, bwm_h.bounds("Closet"))
check("kit: monitor stands on the desk", abs(bwm_h.bounds("Monitor").min.z - bwm_h.bounds("Desk").max.z) < 0.02 or
      abs(D_.objects["Monitor"].location.z - 0.75) < 0.01)
check("kit: chair tucked in front of the desk, facing it",
      abs(abs(D_.objects["Chair"].rotation_euler.z - D_.objects["Desk"].rotation_euler.z) - math.pi) < 0.01
      and D_.objects["Chair"].location.y < D_.objects["Desk"].location.y)
check("kit: room window goes through the thin wall", abs(D_.objects["RoomWin"].location.y - 2) < 0.01)
# Live round 2: a window placed right after its wall (same step) stood in the middle of the room
r = executor.run("bwm.wall('FreshWall', (30, 2), (35, 2), 2.7)\n"
                 "bwm.window('FreshWin', 1.2, 1.1, (32, 2.0, 1.65), wall='FreshWall')\n"
                 "bwm.wall('FreshSide', (30, -2), (30, 2), 2.7)\nbwm.shelf('FreshShelf', (0.8, 0.3, 1.6), (30.5, 0, 0), against='FreshSide')")
check("kit: window right after its wall sits in the wall", r["ok"] and abs(D_.objects["FreshWin"].location.y - 2) < 0.01
      and abs(D_.objects["FreshWin"].location.x - 32) < 0.01, tuple(D_.objects["FreshWin"].location))
check("kit: against= a wall made in the same step", abs(bwm_h.bounds("FreshShelf").min.x - 30.085) < 0.03, bwm_h.bounds("FreshShelf"))

SHAPES = """
c = bwm.cone('IceCone', 0.35, 0.0, 1.2, (-6, 0, 0.6), axis='-Z', material=(0.8, 0.55, 0.25))
s = bwm.sphere('Scoop', 0.4, ('-6', 0, 1.3), material='pink')
bwm.place_on(bwm.sphere('Scoop2', (0.36, 0.36, 0.32), (-6, 0, 3)), s, sink=True)
bwm.lathe('Vase', [(0, 0), (0.3, 0.05), (0.35, 0.4), (0.15, 0.8), (0.2, 1.0)], (-6, 3, 0))
bwm.torus('Ring', 0.5, 0.1, (-6, -3, 0.1))
bwm.tube('Pipe', [(-7, -5, 0.2), (-6, -5, 1), (-5, -5, 0.4)], [0.08, 0.05, 0.03])
bwm.limb('Arm', (-7, 5, 1), (-5, 5, 1), 0.12, 0.06, bend=(0, -0.3, 0))
b = bwm.loft('Body', [(0, 0.0), (0.2, 0.4, 0.3), (0.8, 0.5, 0.35), (1.4, 0.3), (1.6, 0)], (-9, 0, 0), subdivide=1)
bwm.mirror(b); bwm.bevel('Ring', 0.01); bwm.array('Pipe', 2, (0, 1, 0)); bwm.solidify('Ring')
bwm.extrude_profile('Ell', [(0, 0), (1, 0), (1, 0.3), (0.3, 0.3), (0.3, 1), (0, 1)], 0.5, (-9, 4, 0))
bwm.prism('Prism', (2, 1, 1), (-9, -4, 0.5)); bwm.wedge('Ramp', (1, 2, 0.5), (-9, -6, 0.25))
g = bwm.group('Treats', 'IceCone', 'Scoop', 'Scoop2'); bwm.rotate(g, 10)
bwm.duplicate('IceCone', 'Cone2', offset=(0, -1.5, 0))
bwm.box('Tolerant', '1, 2, 3', ['0', '0', '1.5'], material='#ff0000')
bwm.cylinder('Cyl', '0.5', '2', (3, 3, 1), axis='x')
"""
r = executor.run(SHAPES, "shapes")
check("kit: every shape builds", r["ok"], r)
cb = bwm_h.bounds("IceCone")
check("kit: cone point-down with real size", abs(cb.size.z - 1.2) < 0.01 and abs(cb.size.x - 0.7) < 0.02)
check("kit: lathe profile becomes a closed body", D_.objects["Vase"].data.polygons and abs(bwm_h.bounds("Vase").size.z - 1.0) < 0.01)
check("kit: tolerant arguments", tuple(D_.objects["Tolerant"].dimensions) == (1, 2, 3)
      and D_.objects["Tolerant"].material_slots[0].material.diffuse_color[0] > 0.99
      and abs(D_.objects["Cyl"].dimensions.x - 2) < 0.01)
check("kit: all meshes valid (no loose or broken geometry)",
      all(not o.data.validate(verbose=False) for o in D_.objects if o.type == "MESH"))
# Calls a model really made in a live run, which must work (with a note, not an error)
r = executor.run("col = bwm.collection('Live')\nstem = bwm.material('Stem', (0.2, 0.4, 0.1))\n"
                 "bwm.cylinder('Stem', 0.002, 0.015, (0, 0, 0.342), col, stem, segments=6)")
check("live: name, sizes, location, collection, material works for every shape", r["ok"] and "Stem" in D_.objects, r)
r = executor.run("bwm.box('LiveWall', (4, 3, 2.4), (20, 0, 1.2))\n"
                 "bwm.window('LiveWin', 0.8, 1.0, (20, -1.5, 1.5), wall='LiveWall', material=(1, 1, 1))\n"
                 "bwm.duplicate('Stem', 'Stem2')\nbwm.box('Red', (1, 1, 1), (25, 0, 0.5), color='red')\n"
                 "bwm.gable_roof('LiveRoof', 4, 3, height=1.2, location=(20, 0, 2.4))")
check("live: supported alias arguments are read", r["ok"]
      and any("frame_material" in n for n in r.get("helper_notes", []))
      and D_.objects["Red"].material_slots[0].material.diffuse_color[0] > 0.5
      and abs(bwm_h.bounds("LiveRoof_Gables").size.z - 1.2) < 0.01, r)
from blendwme import prompts as prompts_mod, tools as tools_mod  # noqa: E402
schema_text = json.dumps(tools_mod.ALL) + prompts_mod.CORE
check("tool schemas and core prompt carry no subject the model could copy",
      not any(w in schema_text for w in ("Stone base", "Cottage", "Gable roof", "four windows", "Walls 4 x 3")))
info = knowledge.inspect_api("bwm")
check("kit: inspect_api lists the new helpers", all(f"bwm.{n}" in " ".join(info["bwm_helpers"]) for n in ("cylinder", "gable_roof", "bed", "place_on")))
check("kit: inspect_api gives full help for one helper", "WALL TOP" in str(knowledge.inspect_api("bwm.gable_roof")).upper()
      or "top of the walls" in str(knowledge.inspect_api("bwm.gable_roof")))

# ----------------------------------------------------------------------
# 7j. Fix A4: worked example cards. A card is only kept if every step runs cleanly.
# ----------------------------------------------------------------------
import re  # noqa: E402
EXPECT = {"example-cottage": ["Ground", "Base", "Walls", "Roof", "Roof_Tiles", "Chimney", "Door", "Window_Left", "Path"],
          "example-room": ["Floor", "Wall_Back", "Wall_Left", "Bed", "Desk", "Monitor", "Chair", "Closet"],
          "example-ice-cream-cone": ["Cone", "Cone_Waffle", "Scoop_1", "Scoop_2", "Cherry", "Plate"]}
def floating_objects(tolerance=0.02):
    """Top-level visible meshes whose bottom touches neither the ground nor anything below them."""
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH" and not o.hide_render and o.visible_get()]
    boxes = {o.name: bwm_h.bounds(o, children=False) for o in meshes}
    out = []
    for o in meshes:
        b = boxes[o.name]
        if b.min.z <= tolerance or o.parent is not None:
            continue
        supported = any(n != o.name and c.min.z - tolerance <= b.min.z <= c.max.z + tolerance
                        and c.min.x < b.max.x and c.max.x > b.min.x and c.min.y < b.max.y and c.max.y > b.min.y
                        for n, c in boxes.items())
        if not supported:
            out.append(o.name)
    return out


examples = [n for n, _ in knowledge.skill_index() if n.startswith("example-")]
check("example cards exist for the Stage 0 briefs", set(EXPECT) <= set(examples), examples)
for card in examples:
    fresh_scene()
    text = knowledge.load_skill(card)
    blocks = re.findall(r"```python\n(.*?)```", text, re.S)
    results = [executor.run(code, f"{card} step {i + 1}") for i, code in enumerate(blocks)]
    bad = [(i + 1, r.get("error")) for i, r in enumerate(results) if not r["ok"]]
    check(f"{card}: all {len(blocks)} steps run cleanly", blocks and not bad, bad)
    repaired = [(i + 1, r["auto_repaired"]) for i, r in enumerate(results) if r.get("auto_repaired")]
    check(f"{card}: needs no auto-repair (it is the model's example)", not repaired, repaired)
    missing = [n for n in EXPECT.get(card, []) if n not in bpy.data.objects]
    check(f"{card}: builds every named part", not missing, missing)
    check(f"{card}: nothing floats", not floating_objects(), floating_objects())

# Blender's untouched default cube is flagged so the model can clear it; a moved one is the user's.
from blendwme import scene_info as si  # noqa: E402
bpy.ops.wm.read_factory_settings(use_empty=False)
check("default startup cube flagged", "default startup cube" in si.get_scene_info().get("default_startup_cube", ""))
bpy.data.objects["Cube"].location.x = 1.0
check("an edited cube is the user's, not flagged", "default_startup_cube" not in si.get_scene_info())

# 7k. Fix A7: inspect_api covers bmesh and mathutils
r = knowledge.inspect_api("bmesh")
check("inspect bmesh module", r.get("module") == "bmesh" and "ops" in r["members"] and "no bm.ops" in r["usage"], r)
r = knowledge.inspect_api("bmesh.ops")
check("inspect bmesh.ops lists the ops", "create_cone" in r.get("members", {}) and "extrude_face_region" in r["members"])
r = knowledge.inspect_api("bmesh.ops.create_cone")
check("inspect one bmesh op gives its arguments", {"radius1", "radius2", "depth", "segments"} <= set(r.get("arguments", {})), r)
r = knowledge.inspect_api("bmesh.types.BMVert")
check("inspect bmesh.types class", r.get("class") == "bmesh.types.BMVert" and "co" in r["members"], r)
check("inspect BMesh by its bare name", "verts" in knowledge.inspect_api("BMesh").get("members", {}))
r = knowledge.inspect_api("mathutils.Vector")
check("inspect mathutils.Vector", "normalized" in r.get("members", {}) and "cross" in r["members"], r)
check("inspect Vector by its bare name", "lerp" in knowledge.inspect_api("Vector").get("members", {}))
check("inspect mathutils.geometry", "intersect_line_line" in knowledge.inspect_api("mathutils.geometry").get("members", {}))
r = knowledge.inspect_api("bmesh.ops.create_cylinder")
check("unknown bmesh op suggests real ones", "create_circle" in r.get("did_you_mean", []) or "create_cone" in r.get("did_you_mean", []), r)
check("bpy types still inspected", knowledge.inspect_api("BevelModifier").get("type") == "BevelModifier")

print(f"\nALL {len(passed)} KIT CHECKS PASSED")
os._exit(0)
