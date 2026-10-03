"""Typed lesson facts and distinct learner outcomes, without a live brain."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bpy
from blendwme import bridge, lesson
from blendwme.journal import Journal
from blendwme.state import session
from blendwme.tool_host import ToolHost

passed = 0


def check(name, condition):
    global passed
    assert condition, name
    passed += 1
    print("ok -", name, flush=True)


cube = bpy.context.active_object
cube.name = "LessonCube"
other = cube.copy()
other.data = cube.data.copy()
other.name = "OtherCube"
bpy.context.collection.objects.link(other)
cube.select_set(False)
other.select_set(True)
selected = lesson.prepare({"kind": "selected", "object": cube.name})
check("a different selected cube does not satisfy the lesson", lesson.evaluate(selected)[0] == "incorrect")
cube.select_set(True)
check("the intended cube satisfies the lesson", lesson.evaluate(selected)[0] == "correct")
active = lesson.prepare({"kind": "active", "object": cube.name})
bpy.context.view_layer.objects.active = other
check("selected and active are distinct facts", lesson.evaluate(active)[0] == "incorrect")
bpy.context.view_layer.objects.active = cube
mode = lesson.prepare({"kind": "mode", "object": cube.name, "value": "EDIT_MESH"})
other.select_set(False)
bpy.ops.object.mode_set(mode="EDIT")
check("Edit Mode check accepts the correct active object without changing mode",
      lesson.evaluate(mode)[0] == "correct" and bpy.context.mode == "EDIT_MESH")
bpy.ops.object.mode_set(mode="OBJECT")
cube.select_set(False)
other.select_set(True)
bpy.context.view_layer.objects.active = other
bpy.ops.object.mode_set(mode="EDIT")
check("Edit Mode on another object does not satisfy the lesson", lesson.evaluate(mode)[0] == "incorrect")
bpy.ops.object.mode_set(mode="OBJECT")
bpy.context.view_layer.objects.active = cube
other.select_set(False)

for spec in ({"kind": "property", "object": cube.name, "property": "location", "value": [float("nan"), 0, 0]},
             {"kind": "property", "object": cube.name, "property": "name", "value": [1, 2, 3]},
             {"kind": "mode", "value": "not-a-mode"}):
    try:
        lesson.prepare(spec)
        rejected = False
    except ValueError:
        rejected = True
    check("invalid lesson fact is refused before waiting", rejected)

host = ToolHost()
host.journal = Journal()
session.stop_flag.clear()
session.pause_flag.clear()
session.mode = "TEACH"
cube.select_set(True)
args = {"instruction": "Select LessonCube", "point_object": cube.name,
        "check": {"kind": "selected", "object": cube.name}}
result, _ = host._tool_teach_step(args, "claude")
check("pre-existing correct state is reported as already satisfied", result["result"] == "already_satisfied")
cube.select_set(False)


def answer(choice):
    def wait(timeout=None):
        session.teach_choice = choice
        return True
    return wait


host._wait_signal = answer("hint")
result, _ = host._tool_teach_step(args, "claude")
check("Hint keeps the action uncompleted", result["result"] == "hint_requested" and not result["check_passed"] and not host.show_me_allowed)
choices = iter(("done", "skip"))
seen = []
def wrong_then_skip(timeout=None):
    session.teach_choice = next(choices)
    seen.append(session.teach_choice)
    return True
host._wait_signal = wrong_then_skip
result, _ = host._tool_teach_step(args, "claude")
check("Check my work uses the same facts and refuses the wrong state", seen == ["done", "skip"] and result["result"] == "skipped")
host._wait_signal = answer("show_me")
result, _ = host._tool_teach_step(args, "claude")
check("demonstration permission is separate from completion", result["result"] == "show_me" and host.show_me_allowed and not result["check_passed"])

session.signal.clear()
session.wake("hint")
fresh_host = ToolHost()
check("learner action arriving before the wait is not lost", fresh_host._wait_signal(timeout=0.01))
bridge.cancel_all("new lesson fixture")
check("checks from an obsolete task diagnose unavailable context", lesson.evaluate(selected)[0] == "unavailable")

print(f"ALL {passed} TEACHING CHECKS PASSED", flush=True)
