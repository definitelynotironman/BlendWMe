# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""Reference pictures: attaching and removing them, picking them up mid-task, and shrinking big ones."""
import base64
import os
import sys
import tempfile

__author__ = "definitelynotironman"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["BLENDWME_LOG_FOLDER"] = tempfile.mkdtemp(prefix="bwm_refs_logs_")
import bpy
import numpy
import blendwme
from blendwme import panel_ops, prefs, tool_host, workspace
from blendwme.relay import relay
from blendwme.state import MAX_REFERENCES
from blendwme.tool_host import host

passed = 0


def check(name, condition):
    """Assert one named check, count it and print an ok line (a failure stops the suite)."""
    global passed
    assert condition, name
    passed += 1
    print("ok -", name, flush=True)


def picture(folder, name, width, height, alpha=False, noise=False):
    """Write a PNG or JPEG of the given size (noise makes it heavy) and return its path."""
    image = bpy.data.images.new(name, width, height, alpha=alpha)
    if noise:
        pixels = numpy.random.rand(width * height * 4).astype(numpy.float32)
        if alpha:
            pixels[3::4] = numpy.where(pixels[3::4] > 0.5, 1.0, 0.0)
        image.pixels.foreach_set(pixels)
    path = os.path.join(folder, name)
    image.filepath_raw = path
    image.file_format = "JPEG" if name.endswith(".jpg") else "PNG"
    image.save()
    bpy.data.images.remove(image)
    return path


def delivered_size(payload, folder):
    """(width, height) of a delivered payload, decoded by Blender."""
    path = os.path.join(folder, "delivered" + (".png" if payload["mime"] == "image/png" else ".jpg"))
    with open(path, "wb") as f:
        f.write(base64.b64decode(payload["data"]))
    image = bpy.data.images.load(path, check_existing=False)
    size = tuple(image.size)
    bpy.data.images.remove(image)
    return size


workspace.set_folder(tempfile.mkdtemp(prefix="bwm_refs_workspace_"))
blendwme.register()
folder = tempfile.mkdtemp(prefix="bwm_refs_")
small = [picture(folder, f"ref{i}.png", 64, 48) for i in range(MAX_REFERENCES + 2)]
note = os.path.join(folder, "notes.txt")
with open(note, "w") as f:
    f.write("not a picture")
refs = bpy.context.window_manager.blendwme.references

# -- attaching and removing --------------------------------------------------
bpy.ops.blendwme.add_references(directory=folder, files=[{"name": "ref0.png"}, {"name": "ref1.png"},
                                                         {"name": "ref0.png"}, {"name": "notes.txt"}])
check("several pictures attach at once, without repeats or non-pictures",
      [os.path.basename(r.path) for r in refs] == ["ref0.png", "ref1.png"])
bpy.ops.blendwme.add_references(directory=folder, files=[{"name": os.path.basename(p)} for p in small])
check(f"at most {MAX_REFERENCES} pictures stay attached", len(refs) == MAX_REFERENCES)
check("the turn settings carry every attached picture",
      prefs.gather_config(bpy.context)["references"] == [os.path.abspath(r.path) for r in refs])
panel_ops.do_action(bpy.context, "refs:remove:0")
check("the panel's X removes that picture", len(refs) == MAX_REFERENCES - 1
      and os.path.basename(refs[0].path) == "ref1.png")
bpy.ops.blendwme.remove_reference(index=-1)
check("removing with -1 clears every picture", len(refs) == 0)
check("the old two Settings slots are gone", not hasattr(bpy.context.window_manager.blendwme, "reference_1"))

# -- the tool host's list ------------------------------------------------------
check("set_references reports a change", host.set_references([small[0], small[1]]))
check("the same list again is no change", not host.set_references([small[0], small[1]]))
host.set_references(small + [small[0], os.path.join(folder, "missing.png")])
check(f"missing files and repeats are dropped, and the list stops at {MAX_REFERENCES}",
      host.references == small[:MAX_REFERENCES])

# -- pictures attached after the task started (the reported bug) -------------
started = []
relay._start = lambda brain, text, handoff="", generation=None: started.append(text)
relay.reset()
relay.send("How can I add an image?", {"brain": "claude", "references": []})
check("a task can start without pictures", relay.has_conversation() and host.references == [])
relay.told.add("claude")
relay.send("Model the attached character", {"brain": "claude", "references": small[:3]})
check("pictures attached after the task started are picked up", host.references == small[:3])
check("the journal records the new pictures",
      [r["path"] for r in blendwme.journal.journal.data.get("references", [])] == small[:3])
message = relay.compose("claude", "Model the attached character")
check("the brain is told to look at the new pictures", "attached 3 reference pictures" in message
      and "compare_reference=true" in message)
check("a brain is told only once about the same pictures",
      "reference picture" not in relay.compose("claude", "Carry on"))
check("the other brain is told too", "attached 3 reference pictures" in relay.compose("codex", "Take over"))
relay.send("Never mind the pictures", {"brain": "claude", "references": []})
check("removing the pictures mid-task is passed on",
      host.references == [] and "removed the reference pictures" in relay.compose("claude", "Go on"))
relay.running = "claude"
relay.send("Use these instead", {"brain": "claude", "references": small[:2]})
check("pictures attached while a brain works reach it with the message",
      host.references == small[:2] and "attached 2 reference pictures" in tool_host.session.inbox.get_nowait())
relay.running = None
info, images = host._capture_images("angles", compare_reference=True)
check("look delivers every attached picture next to the capture",
      info.get("references_attached") == 2 and len(images) == 3 and all(images))

# -- big pictures are shrunk, not refused -------------------------------------
heavy = picture(folder, "photo.png", 3000, 2000, noise=True)
check("the test photo is over the delivery limit", os.path.getsize(heavy) > tool_host.MAX_IMAGE_BYTES)
payload = tool_host._image(heavy)
check("a heavy picture is shrunk to a JPEG instead of refused", payload and payload["mime"] == "image/jpeg")
check("the shrunk copy is at most MAX_SIDE on its longer side",
      delivered_size(payload, folder) == (tool_host.MAX_SIDE, round(2000 * tool_host.MAX_SIDE / 3000)))
check("the shrunk copy fits the delivery limit", len(base64.b64decode(payload["data"])) <= tool_host.MAX_IMAGE_BYTES)
wide = picture(folder, "wide.jpg", 4000, 3000)
payload = tool_host._image(wide)
check("a light but large JPEG is shrunk too", payload and max(delivered_size(payload, folder)) == tool_host.MAX_SIDE)
clear = picture(folder, "cutout.png", 2000, 1000, alpha=True, noise=True)
payload = tool_host._image(clear)
check("a picture with transparency stays PNG", payload and payload["mime"] == "image/png")
check("a copy that is still too heavy is made smaller again until it fits",
      max(delivered_size(payload, folder)) < tool_host.MAX_SIDE
      and len(base64.b64decode(payload["data"])) <= tool_host.MAX_IMAGE_BYTES)
check("a small picture is delivered as it is",
      base64.b64decode(tool_host._image(small[0])["data"]) == open(small[0], "rb").read())
left = [i.name for i in bpy.data.images if i.name.startswith(("photo", "wide", "cutout", "ref"))]
check("no temporary images are left behind", not left)

print(f"ALL {passed} REFERENCE CHECKS PASSED", flush=True)
