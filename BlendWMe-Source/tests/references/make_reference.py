# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""Render tests/references/cottage.png, the reference image for the Stage 0 test 4.

    blender -b --factory-startup --python tests/references/make_reference.py

It is the tested cottage recipe made different ON PURPOSE, so "the result
follows the reference" can be judged: mustard-yellow walls, a long 6 x 3.4 m
body with a steep roof, the chimney on the LEFT end, a green door off-centre
to the left, three front windows and one on the right side, and a path that
curves to the right. The roof stays red so it agrees with the text brief.
"""

import os
import sys

__author__ = "definitelynotironman"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

import bpy  # noqa: E402

from blendwme import executor  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)

STEPS = [
    """
col = bwm.collection("Cottage")
bwm.cylinder("Ground", 6.5, 0.2, (0, -0.8, -0.1), segments=14, collection=col, material=(0.36, 0.56, 0.24), smooth=False)
bwm.box("Base", (6.5, 3.9, 0.35), (0, 0, 0.175), col, (0.35, 0.33, 0.31), bevel=0.04)
""",
    """
col = bwm.collection("Cottage")
walls = bwm.box("Walls", (6.0, 3.4, 2.5), (0, 0, 0.35 + 1.25), col, (0.95, 0.74, 0.32), bevel=0.02)
bwm.gable_roof("Roof", 6.0, 3.4, 2.2, (0, 0, bwm.top(walls)), overhang=0.4, thickness=0.18,
               collection=col, material=(0.66, 0.15, 0.1), gable_material=(0.95, 0.74, 0.32), tiles=7)
""",
    """
col = bwm.collection("Cottage")
ch = bwm.box("Chimney", (0.55, 0.55, 1.6), (-2.3, 0.7, 8), col, (0.5, 0.25, 0.2), bevel=0.02)
bwm.place_on(ch, "Roof", sink=True)
cap = bwm.box("Chimney_Cap", (0.68, 0.68, 0.1), (-2.3, 0.7, 9), col, (0.35, 0.33, 0.31))
bwm.place_on(cap, ch)
""",
    """
col = bwm.collection("Cottage")
bwm.door("Door", 0.95, 2.0, (-1.6, -1.7, 0.35), wall="Walls", collection=col, material=(0.16, 0.42, 0.25))
for i, x in enumerate((-0.2, 1.2, 2.4)):
    bwm.window(f"Window_Front_{i + 1}", 0.85, 1.0, (x, -1.7, 1.75), wall="Walls", collection=col,
               frame_material=(0.98, 0.97, 0.94))
bwm.window("Window_Right", 0.85, 1.0, (3.0, 0, 1.75), wall="Walls", collection=col, frame_material=(0.98, 0.97, 0.94))
bwm.path("Path", [(-1.6, -2.15), (-1.0, -3.4), (0.6, -4.4), (2.4, -5.0)], width=0.75, collection=col)
""",
    """
sun = D.objects.new("Sun", D.lights.new("Sun", "SUN"))
sun.data.energy = 3.5
sun.data.color = (1.0, 0.93, 0.8)
C.scene.collection.objects.link(sun)
bwm.rotate(sun, (50, 10, 35))
world = D.worlds.new("World")
C.scene.world = world
world.use_nodes = True
bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
bg.inputs["Color"].default_value = (0.62, 0.74, 0.9, 1.0)
bg.inputs["Strength"].default_value = 0.9
cam = D.objects.new("Camera", D.cameras.new("Camera"))
C.scene.collection.objects.link(cam)
cam.location = (10.5, -17.5, 7.0)
bwm.rotate(cam, (73, 0, 31))
C.scene.camera = cam
""",
]

for i, code in enumerate(STEPS, 1):
    result = executor.run(code, f"reference step {i}")
    if not result["ok"]:
        raise SystemExit(f"step {i} failed: {result.get('error')}")

scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE_NEXT"
scene.view_settings.view_transform = "Standard"   # true colours, not washed out
scene.render.resolution_x, scene.render.resolution_y = 1024, 768
scene.render.filepath = os.path.join(HERE, "cottage.png")
bpy.ops.render.render(write_still=True)
print("saved", scene.render.filepath)
