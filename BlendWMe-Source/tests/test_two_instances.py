# BlendWMe by definitelynotironman
# SPDX-License-Identifier: MIT
"""Two simultaneous Blender processes and pinned discovery. No live brains."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

__author__ = "definitelynotironman"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from blendwme_mcp.link import BlenderLink, LinkError

parser = argparse.ArgumentParser()
parser.add_argument("--blender", required=True)
args = parser.parse_args()
folder = Path(tempfile.mkdtemp(prefix="bwm_two_instances_"))
children, outputs = [], []
passed = 0


def check(name, condition):
    """Assert one named check, count it and print an ok line (a failure stops the suite)."""
    global passed
    assert condition, name
    passed += 1
    print("ok -", name, flush=True)


try:
    for name in ("first", "second"):
        instance = folder / name
        instance.mkdir()
        output = open(instance / "process.log", "w", encoding="utf-8")
        outputs.append(output)
        env = dict(os.environ, BLENDER_USER_RESOURCES=str(instance / "profile"),
                   BLENDWME_LOG_FOLDER=str(instance / "logs"))
        children.append(subprocess.Popen([args.blender, "-b", "--factory-startup", "--python-exit-code", "1",
                                         "--python", str(ROOT / "tests" / "instance_fixture.py"), "--", str(instance)],
                                        cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT))
    end = time.monotonic() + 25
    connections = [folder / name / "connection.json" for name in ("first", "second")]
    while not all(path.exists() for path in connections) and time.monotonic() < end:
        if any(child.poll() is not None for child in children):
            raise AssertionError("Blender fixture exited before discovery; inspect " + str(folder))
        time.sleep(0.05)
    check("two real Blender instances start simultaneously", all(path.exists() for path in connections))
    data = [json.loads(path.read_text(encoding="utf-8")) for path in connections]
    check("instances have independent identities and listening ports", data[0]["instance"] != data[1]["instance"] and
          data[0]["port"] != data[1]["port"])
    first = BlenderLink(str(connections[0].parent), data[0]["instance"])
    second = BlenderLink(str(connections[1].parent), data[1]["instance"])
    first.ping()
    second.ping()
    result, _ = first.call("run_python", {"code": "bwm.box('OnlyFirst')", "title": "First instance object"}, "claude")
    check("first process applies its own mutation", result.get("ok"))
    info, _ = second.call("get_scene_info", {}, "codex")
    check("first process cannot mutate the second scene", not any(obj["name"] == "OnlyFirst" for obj in info["objects"]))
    result, _ = second.call("run_python", {"code": "bwm.box('OnlySecond')", "title": "Second instance object"}, "codex")
    check("second process applies its own mutation", result.get("ok"))
    connections[0].write_text(json.dumps(data[1]), encoding="utf-8")
    try:
        first.call("run_python", {"code": "bwm.box('Redirected')", "title": "Must be refused"}, "claude")
        rejected = False
    except LinkError:
        rejected = True
    check("old client refuses discovery redirected to another Blender", rejected)
    connections[0].write_text(json.dumps(data[0]), encoding="utf-8")
    info_first, _ = first.call("get_scene_info", {}, "claude")
    info_second, _ = second.call("get_scene_info", {}, "codex")
    names_first = {obj["name"] for obj in info_first["objects"]}
    names_second = {obj["name"] for obj in info_second["objects"]}
    check("both scenes remain isolated after redirection attempt", "OnlyFirst" in names_first and "OnlySecond" not in names_first and
          "OnlySecond" in names_second and "OnlyFirst" not in names_second and "Redirected" not in names_first | names_second)
finally:
    for name in ("first", "second"):
        (folder / name / "stop").touch()
    for child in children:
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
    for output in outputs:
        output.close()

check("both disposable Blender processes exit cleanly", all(child.returncode == 0 for child in children))
print(f"ALL {passed} TWO-INSTANCE CHECKS PASSED", flush=True)
