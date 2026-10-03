# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""Representative user edits that preserve names, counts and outer bounds."""
import os
import sys

__author__ = "definitelynotironman"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bpy
from blendwme import helpers, scene_info

passed = 0


def check(name, condition):
    """Assert one named check, count it and print an ok line (a failure stops the suite)."""
    global passed
    assert condition, name
    passed += 1
    print("ok -", name, flush=True)


def changed(before, obj):
    """True when the object is reported as modified since the `before` snapshot."""
    after = scene_info.snapshot()
    return obj.name in scene_info.diff(before, after)[1]["modified"]


obj = helpers.mesh("EditableGrid", [(x, y, 0) for y in (-1, 0, 1) for x in (-1, 0, 1)],
                   [(0, 1, 4, 3), (1, 2, 5, 4), (3, 4, 7, 6), (4, 5, 8, 7)])
bpy.context.view_layer.update()
before = scene_info.snapshot()
bounds = scene_info.world_bounds(obj)
obj.data.vertices[4].co.x = 0.1
check("interior vertex edit is detected with unchanged counts and bounds",
      changed(before, obj) and scene_info.world_bounds(obj) == bounds)
identity = obj["blendwme_id"]
before = scene_info.snapshot()
obj.name = "RenamedGrid"
lines, diff = scene_info.diff(before, scene_info.snapshot())
check("rename retains identity and is not misreported as delete/add", obj["blendwme_id"] == identity and
      obj.name in diff["modified"] and not diff["added"] and not diff["removed"] and any("renamed" in line for line in lines))
duplicate = obj.copy()
bpy.context.collection.objects.link(duplicate)
scene_info.snapshot()
check("duplicate gets an independent identity", duplicate["blendwme_id"] != obj["blendwme_id"])

modifier = obj.modifiers.new("EditableBevel", "BEVEL")
before = scene_info.snapshot()
modifier.width = 0.07
check("modifier scalar changes are detected", changed(before, obj))
material = helpers.material("EditableMaterial", (0.2, 0.4, 0.6))
helpers.assign(obj, material)
node = next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
before = scene_info.snapshot()
node.inputs["Roughness"].default_value = 0.84
check("material socket changes are detected", changed(before, obj))

obj.shape_key_add(name="Basis")
key = obj.shape_key_add(name="Expression")
before = scene_info.snapshot()
key.data[4].co.y = 0.2
check("shape-key coordinate changes are detected", changed(before, obj))
before = scene_info.snapshot()
key.value = 0.5
check("shape-key weight changes are detected", changed(before, obj))

obj.location = (0, 0, 0)
obj.keyframe_insert("location", frame=1)
obj.location = (1, 0, 0)
obj.keyframe_insert("location", frame=12)
bpy.context.scene.frame_set(1)
before = scene_info.snapshot()
obj.location.x = 2
obj.keyframe_insert("location", frame=12)
obj.location.x = 0             # same current transform; a future key is different
check("future animation key edit is detected at the unchanged current transform", changed(before, obj))

scene_info.tag_author([obj.name], "ai")
scene_info.tag_brain([obj.name], "claude")
scene_info.tag_author([obj.name], "user")
check("user edit retains creator while changing last editor and protection",
      obj["blendwme_creator"] == "claude" and obj["blendwme_last_editor"] == "user" and obj["blendwme_protected"])
scene_info.tag_brain([obj.name], "codex")
check("later brain edits retain user protection and the original creator",
      obj["blendwme_creator"] == "claude" and obj["blendwme_last_editor"] == "codex" and
      obj["blendwme_author"] == "user" and obj["blendwme_protected"])
check("scene facts disclose incomplete change-detection domains", "not fully tracked" in scene_info.get_scene_info()["change_detection_limits"])

print(f"ALL {passed} IDENTITY CHECKS PASSED", flush=True)
