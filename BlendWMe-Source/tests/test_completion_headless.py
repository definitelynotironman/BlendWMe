"""Completion evidence, image integrity and argument-meaning regressions.

Author: definitelynotironman

Runs in background Blender with no subscription client:
    blender -b --factory-startup --python-exit-code 1 --python tests/test_completion_headless.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bpy
from blendwme import executor, tool_host
from blendwme import helpers as bwm_h
from blendwme.journal import Journal
from blendwme.state import session
from blendwme.tool_host import ToolHost

passed = 0


def check(name, condition, detail=None):
    """Assert one named condition and count it."""
    global passed
    assert condition, f"{name}: {detail}"
    passed += 1
    print("ok -", name, flush=True)


folder = tempfile.mkdtemp(prefix="bwm-completion-")


def write_png(name, width=4, height=4):
    """Save a small real PNG through Blender and return its path."""
    image = bpy.data.images.new(name, width, height)
    image.pixels[:] = [0.5] * (width * height * 4)
    path = os.path.join(folder, name + ".png")
    image.filepath_raw = path
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)
    return path


# ----------------------------------------------------------------------
# Image payloads: decoded by content, never trusted by extension
# ----------------------------------------------------------------------
good = write_png("good")
payload = tool_host._image(good)
check("a real PNG is encoded with its detected type", payload and payload["mime"] == "image/png")
check("decoding leaves no temporary image datablock", not any(i.filepath == good for i in bpy.data.images))

truncated = os.path.join(folder, "truncated.png")
with open(good, "rb") as src, open(truncated, "wb") as dst:
    dst.write(src.read()[:40])
check("a truncated PNG is refused", tool_host._image(truncated) is None)

fake = os.path.join(folder, "notes.png")
with open(fake, "w", encoding="utf-8") as f:
    f.write("this is text, not a picture")
check("a text file named .png is refused", tool_host._image(fake) is None)
check("a missing file is refused", tool_host._image(os.path.join(folder, "missing.png")) is None)

renamed = os.path.join(folder, "really-png.jpg")
with open(good, "rb") as src, open(renamed, "wb") as dst:
    dst.write(src.read())
check("type comes from content, not the extension", (tool_host._image(renamed) or {}).get("mime") == "image/png")

original_decode = tool_host._decodable_image


def changing_decode(path):
    """Simulate the file being replaced while Blender decodes it."""
    ok = original_decode(path)
    with open(path, "ab") as f:
        f.write(b"changed")
    return ok


changing = write_png("changing")
tool_host._decodable_image = changing_decode
check("a file that changes during decoding is refused", tool_host._image(changing) is None)
tool_host._decodable_image = original_decode

saved_limit = tool_host.MAX_IMAGE_BYTES
tool_host.MAX_IMAGE_BYTES = 10
check("an oversized image is refused", tool_host._image(good) is None)
tool_host.MAX_IMAGE_BYTES = saved_limit

# ----------------------------------------------------------------------
# A no-op step cannot tick a plan part, in the panel or in the journal
# ----------------------------------------------------------------------
host = ToolHost()
host.journal = Journal()
session.stop_flag.clear()
session.pause_flag.clear()
session.mode = "AUTO"
session.build_style = "FULL"
session.set_plan(["Chimney", "Door"])
host.journal.record_plan("claude", ["Chimney", "Door"])

result, _ = host._tool_run_python({"title": "Chimney", "code": "x = 1", "part": 1}, "claude")
check("a no-op step says its requirement is unverified", "requirement_unverified" in result, result)
check("a no-op step leaves the visible plan part open", not session.plan[0]["done"])
check("a no-op step leaves the journal plan part open", not host.journal.data["plan"][0]["done"])

result, _ = host._tool_run_python({"title": "Chimney", "part": 1,
                                   "code": "bwm.box('Chimney', (0.4, 0.4, 1.2), (1, 0, 3))"}, "claude")
check("a step that builds the part ticks it in the panel", result["ok"] and session.plan[0]["done"], result)
check("the same step ticks it in the journal", host.journal.data["plan"][0]["done"])

# ----------------------------------------------------------------------
# Completion gates
# ----------------------------------------------------------------------
session.set_plan([])
host.journal.data["plan"] = []
host.final_reviewed = True
host.journal.record_review("claude", "before finishing")
host.journal.set_review_verdict("claude", "NEEDS_WORK: the chimney floats above the roof")
result, _ = host._tool_finish({"summary": "Cottage done."}, "claude")
check("a NEEDS WORK verdict blocks finish", "NEEDS WORK" in result.get("not_finished_yet", ""), result)

host.journal.set_review_verdict("claude", "GOOD: everything in the brief is there")
host.inspection_snapshot = None
saved_review = host._review
host._review = lambda brain, when: None
host.final_reviewed = False
host.ok_steps = max(host.ok_steps, 1)
result, _ = host._tool_finish({"summary": "Cottage done."}, "claude")
check("a failed final inspection refuses completion", "unverified" in result.get("not_finished_yet", ""), result)
host._review = saved_review

saved_capture = tool_host.look.capture
tool_host.look.capture = lambda *a, **k: {"path": truncated}
result, images = host._tool_look({"mode": "angles"}, "claude")
check("a look whose picture cannot be decoded is an error", "error" in result and not images, result)
check("a failed look cannot authorise plan checks", host.looked_at_step == -1)
tool_host.look.capture = saved_capture

# ----------------------------------------------------------------------
# Repairs keep the meaning of diameters and refuse unknown arguments
# ----------------------------------------------------------------------
r = executor.run("bwm.cylinder('Pipe', diameter=2, depth=1, location=(10, 0, 0))")
size = bwm_h.bounds("Pipe").size
check("bwm diameter becomes half as radius", r["ok"] and abs(size.x - 2) < 0.05, (r, tuple(size)))

r = executor.run("bwm.cone('Frustum', diameter1=2, diameter2=1, depth=1, location=(14, 0, 0))")
size = bwm_h.bounds("Frustum").size
check("cone diameter1 / diameter2 keep their bottom and top sizes", r["ok"] and abs(size.x - 2) < 0.05, (r, tuple(size)))

r = executor.run("bpy.ops.mesh.primitive_cone_add(diameter1=4, depth=1, location=(18, 0, 0))")
obj = bpy.context.active_object
check("operator diameter1 is repaired to radius1", r["ok"] and obj and abs(obj.dimensions.x - 4) < 0.05,
      (r, obj and tuple(obj.dimensions)))

before = len(bpy.data.objects)
r = executor.run("bwm.box('Wrong', (1, 1, 1), (22, 0, 0), sparkle=3)")
check("an unsupported bwm argument fails instead of being dropped",
      not r["ok"] and "sparkle" in r.get("error", "") and len(bpy.data.objects) == before, r)

r = executor.run("bpy.ops.mesh.primitive_cube_add(size=1, sparkle=3)")
check("an unsupported operator argument fails with Blender's message", not r["ok"], r)

print(f"ALL {passed} COMPLETION CHECKS PASSED", flush=True)
