"""Fault injection and formatting regressions. No subscription clients."""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from blendwme import bridge, executor, helpers, lesson, richtext, scene_info
from blendwme.journal import Journal
import bpy

passed = 0


def check(name, condition):
    global passed
    assert condition, name
    passed += 1
    print("ok -", name, flush=True)


effects, errors = [], []


def expired():
    try:
        bridge.call(lambda: effects.append("expired"), timeout=0.01)
    except TimeoutError as error:
        errors.append(str(error))


worker = threading.Thread(target=expired)
worker.start()
worker.join(1)
bridge._pump()
check("expired pending work never executes", not effects and errors)

source = "plain **bold text** and `bpy.data`\n\n- first\n- **second**\nEND"
measure = lambda role, size, value: len(value) * (9 if role == "BLACK" else 7)
lines = richtext.layout(source, 16, 110, measure)
check("mixed fonts fit the measured width", all(sum(run[2] for run in line) <= 110 for line in lines))
check("bold markers are hidden", "**" not in richtext.plain(source))
check("inline code markers are hidden", "`" not in richtext.plain(source))
check("paragraphs and lists survive", "\n\n- first\n- second\nEND" in richtext.plain(source))
check("source is unchanged", "**bold text**" in source)
check("unfinished streaming bold is literal", richtext.plain("a **unfinished") == "a **unfinished")
check("unfinished streaming code is literal", richtext.plain("a `unfinished") == "a `unfinished")
long = (source + "\n") * 100 + "SENTINEL"
long_lines = richtext.layout(long, 16, 80, measure)
check("long messages reach final sentinel", "".join(r[1] for line in long_lines for r in line).endswith("SENTINEL"))
check("empty formatted text is safe", richtext.layout("", 16, 80, measure) == [[]])

obj = bpy.context.active_object
spec = lesson.prepare({"kind": "selected", "object": obj.name})
obj.select_set(False)
check("lesson requires the intended selection", lesson.evaluate(spec)[0] == "incorrect")
obj.select_set(True)
check("lesson recognizes the intended selection", lesson.evaluate(spec)[0] == "correct")
obj.name = "Renamed lesson object"
check("lesson survives rename", lesson.evaluate(spec)[0] == "correct")
before = tuple(obj.location)
for _ in range(5):
    lesson.evaluate(spec)
check("lesson polling leaves the scene unchanged", tuple(obj.location) == before)
try:
    lesson.prepare("D.objects.clear()")
    refused = False
except ValueError:
    refused = True
check("generated teaching expressions are refused", refused)

before = scene_info.snapshot()
obj.data.vertices[0].co.x += 0.05
after = scene_info.snapshot()
check("same-count vertex movement is detected", any("edited" in line for line in scene_info.diff(before, after)[0]))
identity = obj["blendwme_id"]
obj.name = "Renamed again"
renamed = scene_info.snapshot()
check("object identity survives rename", obj["blendwme_id"] == identity and
      any("renamed" in line for line in scene_info.diff(after, renamed)[0]))
copy = obj.copy()
bpy.context.collection.objects.link(copy)
scene_info.snapshot()
check("duplicate receives a separate identity", copy["blendwme_id"] != identity)

wood = bpy.data.materials.new("Wood")
wood.diffuse_color = (0.1, 0.2, 0.3, 1)
made = helpers.material("Wood", (0.9, 0.8, 0.7))
check("generic user material is preserved", made != wood and abs(wood.diffuse_color[0] - 0.1) < 0.001)
location = tuple(obj.location)
result = executor.run("D.objects['Renamed again'].location.x += 5\nraise RuntimeError('injected')")
check("supported transform edit is restored on failure", not result["ok"] and tuple(obj.location) == location)
check("unsupported recovery has a verified checkpoint", os.path.isfile(result.get("recovery_checkpoint", "")))

j = Journal()
j.record_task("original brief")
j.record_plan("claude", ["part"])
revision = j.data["seq"]
j.record_plan_tick(1)
check("tick-only changes advance journal revision", j.data["seq"] > revision)
revision = j.data["seq"]
j.set_session("claude", "thread")
check("session-only changes advance journal revision", j.data["seq"] > revision)
for i in range(80):
    j.record_user_message(f"palette decision {i}")
j.record_user_message("LATEST PALETTE SENTINEL")
check("late decisions are retained", j.data["decisions"][-1] == "LATEST PALETTE SENTINEL")
check("journal rejects malformed schema", not j.load_text('{"events": "wrong", "seq": 1, "version": 1}'))
print(f"ALL {passed} REFINEMENT CHECKS PASSED", flush=True)
