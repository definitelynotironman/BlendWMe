# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""Scene protection, recovery and bounded helper work in disposable Blender."""
import os
import sys
import time
import tempfile
import hashlib
from itertools import repeat

__author__ = "definitelynotironman"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bpy
from blendwme import executor, helpers, shapes

passed = 0


def check(name, condition):
    """Assert one named check, count it and print an ok line (a failure stops the suite)."""
    global passed
    assert condition, name
    passed += 1
    print("ok -", name, flush=True)


def refused(name, operation):
    """True when the operation raises ValueError and leaves objects and modifiers unchanged."""
    before = (len(bpy.data.objects), sum(len(o.modifiers) for o in bpy.data.objects))
    try:
        operation()
        rejected = False
    except ValueError:
        rejected = True
    after = (len(bpy.data.objects), sum(len(o.modifiers) for o in bpy.data.objects))
    check(name, rejected and before == after)


cube = bpy.context.active_object
cube.name = "Original"
folder = tempfile.mkdtemp(prefix="bwm_recovery_assets_")
reference = bpy.data.images.new("RecoveryReference", 2, 2)
reference.use_fake_user = True
reference.filepath_raw = os.path.join(folder, "reference.png")
reference.file_format = "PNG"
reference.save()
reference.filepath = "//reference.png"
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, "original.blend"), check_existing=False)
with open(bpy.data.filepath, "rb") as file:
    original_digest = hashlib.sha256(file.read()).hexdigest()
for count in (1_000_000, float("nan"), float("inf"), 3.5, True):
    refused(f"invalid sphere density {count!r} is refused before allocation",
            lambda count=count: shapes.sphere("TooDense", segments=count))
refused("excessive render subdivision is refused before adding a modifier",
        lambda: shapes.subdivide(cube, 1, 9))
refused("excessive viewport subdivision is refused before adding a modifier",
        lambda: shapes.subdivide(cube, 9))
refused("excessive array count is refused before adding a modifier", lambda: shapes.array(cube, 100_000))
refused("loft density is refused before replacing artwork",
        lambda: shapes.loft("Original", [(i, 1) for i in range(500)], segments=256))
refused("loft subdivision is refused before constructing the object",
        lambda: shapes.loft("Original", [(i, 1) for i in range(100)], segments=256, subdivide=4))
refused("raw helper mesh density is refused before replacing artwork",
        lambda: helpers.mesh("Original", repeat((0, 0, 0), 100_001), []))

mat = bpy.data.materials.new("UserWood")
mat.use_nodes = True
mat.diffuse_color = (0.1, 0.2, 0.3, 1)
bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
bsdf.inputs["Roughness"].default_value = 0.67
cube.data.materials.clear()
cube.data.materials.append(mat)
linked = cube.copy()
linked.name = "LinkedCopy"
bpy.context.collection.objects.link(linked)
mesh_before = cube.data
helpers.assign(cube, (0.9, 0.1, 0.2))
check("assign isolates a shared mesh and preserves the linked object's slots",
      cube.data != linked.data and linked.data == mesh_before and list(linked.data.materials) == [mat])
owned = helpers.material("SharedOwned", (0.2, 0.3, 0.4))
cube.data.materials.clear()
cube.data.materials.append(owned)
linked.data.materials.append(owned)
color_before = tuple(owned.diffuse_color)
clone = helpers.material("SharedOwned", (0.9, 0.9, 0.9))
check("helper material writes clone shared materials", clone != owned and tuple(owned.diffuse_color) == color_before)
single = helpers.material("SingleOwned", (0.2, 0.3, 0.4))
cube.data.materials.clear()
cube.data.materials.append(single)
cube["blendwme_author"] = "user"
single_before = tuple(single.diffuse_color)
clone = helpers.material("SingleOwned", (0.9, 0.9, 0.9))
check("material on a user-edited object is protected even with one user",
      clone != single and tuple(single.diffuse_color) == single_before)

cube.data.materials.clear()
cube.data.materials.append(mat)
location_before = tuple(cube.location)
start = time.monotonic()
original_filepath = bpy.data.filepath
result = executor.run("D.objects['Original'].location.x = 12\n"
                      "m = D.materials['UserWood']\n"
                      "m.diffuse_color = (1, 0, 0, 1)\n"
                      "next(n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED').inputs['Roughness'].default_value = 0.1\n"
                      "D.objects['Original'].data.materials.clear()\n"
                      "raise RuntimeError('injected material failure')")
print(f"RECOVERY COST small scene: {time.monotonic() - start:.3f} seconds", flush=True)
check("checkpoint copy leaves the user's active filename unchanged", bpy.data.filepath == original_filepath)
with open(original_filepath, "rb") as file:
    check("checkpoint does not overwrite the user's saved file", hashlib.sha256(file.read()).hexdigest() == original_digest)
check("checkpoint remapping preserves the current relative reference",
      os.path.normpath(bpy.path.abspath(reference.filepath)) == os.path.join(folder, "reference.png"))
check("failed transform and material slots are restored", not result["ok"] and tuple(cube.location) == location_before and
      list(cube.data.materials) == [mat])
check("failed material color and existing socket edits are restored",
      abs(mat.diffuse_color[0] - 0.1) < 0.001 and abs(bsdf.inputs["Roughness"].default_value - 0.67) < 0.001)
old_trace = sys.gettrace()
result = executor.run("D.objects['Original'].location.x = 100\nwhile True:\n    pass")
check("runaway Python is bounded and its supported edits recover",
      not result["ok"] and "work limit" in result.get("error", "") and tuple(cube.location) == location_before)
check("Python work guard restores the previous tracing hook", sys.gettrace() is old_trace)

cube["blendwme_author"] = "ai"
cube["blendwme_protected"] = True
result = executor.run("bwm.box('Original', location=(8, 0, 0))")
check("explicit protection prevents replacement of an AI-tagged object",
      result["ok"] and bpy.data.objects.get("Original") == cube and tuple(cube.location) == location_before)

# An AI rebuild must detach and preserve untagged descendants, including those
# nested under an AI-owned child that is removed by replacement.
parent = helpers.box("AIParent", location=(3, 0, 0))
parent["blendwme_author"] = "ai"
child = helpers.box("AIChild", location=(3, 2, 0))
child["blendwme_author"] = "ai"
child.parent = parent
user = helpers.box("UserGrandchild", location=(1, 1, 1))
user.parent = child
bpy.context.view_layer.update()
world_before = user.matrix_world.copy()
replacement = executor.run("bwm.box('AIParent', location=(0, 0, 0))")
user = bpy.data.objects.get("UserGrandchild")
check("AI rebuild preserves a nested user descendant and its world transform",
      replacement["ok"] and user is not None and all(abs(a - b) < 1e-5 for row_a, row_b in zip(user.matrix_world, world_before)
                                                       for a, b in zip(row_a, row_b)))

# Deleted nodes and geometry cannot be reconstructed by the supported inverses.
# Opening the verified scene checkpoint must actually recover them.
vertices_before = len(cube.data.vertices)
node_count = len(mat.node_tree.nodes)
result = executor.run("D.materials['UserWood'].node_tree.nodes.clear()\n"
                      "D.objects.remove(D.objects['Original'], do_unlink=True)\n"
                      "raise RuntimeError('injected deletion')")
path = result.get("recovery_checkpoint", "")
check("unsupported deletion returns a retained recovery checkpoint", not result["ok"] and os.path.isfile(path))
bpy.ops.wm.open_mainfile(filepath=path)
restored = bpy.data.objects.get("Original")
check("opening checkpoint restores the deleted mesh and node graph",
      restored is not None and len(restored.data.vertices) == vertices_before and
      restored.name in bpy.context.scene.objects and len(bpy.data.materials["UserWood"].node_tree.nodes) == node_count)
reference = bpy.data.images.get("RecoveryReference")
check("relative reference resolves after opening checkpoint", reference is not None and
      os.path.normpath(bpy.path.abspath(reference.filepath)) == os.path.join(folder, "reference.png"))

print(f"ALL {passed} RECOVERY CHECKS PASSED", flush=True)
