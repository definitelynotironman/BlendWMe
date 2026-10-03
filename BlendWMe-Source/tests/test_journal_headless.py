"""Persistence, schema and reference fault injection; no subscription clients."""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["BLENDWME_LOG_FOLDER"] = tempfile.mkdtemp(prefix="bwm_journal_logs_")
import bpy
import blendwme
from blendwme import journal as module, workspace
from blendwme.journal import Journal

passed = 0


def check(name, condition):
    global passed
    assert condition, name
    passed += 1
    print("ok -", name, flush=True)


workspace.set_folder(tempfile.mkdtemp(prefix="bwm_journal_workspace_"))
blendwme.register()
j = module.journal
j.reset()
code = "# retained original source\n" * 200
message = "original user sentence " * 200
j.record_task("original brief")
j.record_step("claude", "long code", "", code, True)
j.record_user_message(message)
for i in range(650):
    j.record_said("claude", f"older note {i}")
j.record_user_message("LATEST PALETTE REVERSAL: blue instead of red")
check("exact code survives beyond the old code cap", next(e for e in j.data["events"] if e["kind"] == "step")["code"] == code)
check("exact messages survive beyond the old event cap", any(e.get("text") == message for e in j.data["events"]))
check("latest decision is present in compressed handoff context", "LATEST PALETTE REVERSAL" in j.handoff_brief("codex"))
seq = j.data["seq"]
j.record_struggle("claude", "key", "first failure")
j.record_struggle("claude", "key", "updated failure")
check("struggle-only updates advance revision", j.data["seq"] == seq + 2)

folder = tempfile.mkdtemp(prefix="bwm_journal_file_")
path = os.path.join(folder, "saved-a.blend")
j.record_task("SAVED TASK A")
j.record_plan("claude", ["part"])
j.record_plan_tick(1)
j.set_session("claude", "saved-session")
seq_a = j.data["seq"]
bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
check("save pre serializes tick and session before writing the blend",
      json.loads(bpy.data.texts[module.TEXT_NAME].as_string())["seq"] == seq_a)
j.record_task("UNSAVED TASK B")
module.save()
with open(module.sidecar_path(path), encoding="utf-8") as file:
    check("unsaved work has a newer sidecar", json.load(file)["brief"] == "UNSAVED TASK B")
bpy.ops.wm.open_mainfile(filepath=path)
check("discard and reopen restores A rather than unsaved B", j.data["brief"] == "SAVED TASK A")
check("tick-only and client session state survive reopen", j.data["plan"][0]["done"] and
      j.data["sessions"]["claude"] == "saved-session")

real_write = module.write_sidecar
def updating_write(blend_path, text=None):
    result = real_write(blend_path, text)
    j.record_user_message("UPDATE DURING IO")
    return result
j.record_user_message("before IO")
module.write_sidecar = updating_write
try:
    module.save()
finally:
    module.write_sidecar = real_write
check("update during IO remains dirty", j.dirty)
module.save()
with open(module.sidecar_path(path), encoding="utf-8") as file:
    check("next flush retains the concurrent update", "UPDATE DURING IO" in json.load(file)["decisions"])

j.reset()
j.record_task("OLD IDENTITY")
def reset_during_write(blend_path, text=None):
    result = real_write(blend_path, text)
    j.reset()
    j.record_task("NEW IDENTITY")     # same seq as before, different in-memory revision
    return result
module.write_sidecar = reset_during_write
try:
    module.save()
finally:
    module.write_sidecar = real_write
check("reset to equal seq during IO cannot clear dirty state", j.dirty and j.data["brief"] == "NEW IDENTITY")
module.save()

for field, value in (("events", [None]), ("plan", ["wrong"]), ("sessions", []),
                     ("references", [{"path": 3, "name": "wrong"}]), ("seq", True)):
    before = j.to_text()
    invalid = json.loads(before)
    invalid[field] = value
    check(f"malformed {field} is rejected without replacing current state",
          not j.load_text(json.dumps(invalid)) and j.to_text() == before)
bpy.data.texts[module.TEXT_NAME].from_string("{broken saved journal")
check("corrupt embedded journal clears stale previous-project context",
      not module.load_for_file() and not j.data["brief"] and not j.has_history())

old = tempfile.mkdtemp(prefix="bwm_reference_old_")
new = tempfile.mkdtemp(prefix="bwm_reference_moved_")
reference = os.path.join(old, "reference.png")
with open(reference, "wb") as file:
    file.write(b"reference content fixture")
refs = Journal()
refs.record_references([reference], os.path.join(old, "project.blend"))
shutil.copyfile(reference, os.path.join(new, "reference.png"))
os.rename(reference, reference + ".moved")
resolved = refs.resolve_references(os.path.join(new, "project.blend"))[0]
check("moved project resolves a relative reference with the original hash",
      resolved["path"] == os.path.join(new, "reference.png") and not resolved["missing"] and not resolved["changed"])
with open(resolved["path"], "wb") as file:
    file.write(b"changed reference")
check("changed reference bytes are reported", refs.resolve_references(os.path.join(new, "project.blend"))[0]["changed"])
os.rename(resolved["path"], resolved["path"] + ".missing")
check("missing reference is explicit", refs.resolve_references(os.path.join(new, "project.blend"))[0]["missing"])
check("reference resolution leaves the exact original record intact", refs.data["references"][0]["path"] == reference)
refs.record_handoff("claude", "OLD TASK HANDOFF SENTINEL" * 100)
refs.set_session("claude", "old-client")
refs.record_user_message("OLD TASK DECISION")
refs.record_task("FRESH TASK")
check("new task clears active client, reference and handoff context",
      not refs.data["sessions"] and not refs.data["references"] and not refs.data["handoffs"] and not refs.data["decisions"])
check("archived handoff and reference records remain exact",
      any(e.get("text") == "OLD TASK HANDOFF SENTINEL" * 100 for e in refs.data["events"]) and
      any(e.get("references") for e in refs.data["events"]))
check("new task handoff excludes obsolete task instructions", "OLD TASK" not in refs.handoff_brief("codex"))

blendwme.unregister()
print(f"ALL {passed} JOURNAL CHECKS PASSED", flush=True)
