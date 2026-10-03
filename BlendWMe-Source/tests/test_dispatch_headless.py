"""Controlled request races in real Blender; no subscription programs."""
import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bpy
from blendwme import bridge, socket_bridge, workspace

passed = 0


def check(name, condition):
    global passed
    assert condition, name
    passed += 1
    print("ok -", name, flush=True)


def pump_until(predicate):
    end = time.monotonic() + 5
    while not predicate() and time.monotonic() < end:
        bridge._pump()
        time.sleep(0.005)
    assert predicate(), "controlled worker did not reach its expected boundary"


class Host:
    def __init__(self):
        self.task_id = "task-one"
        self.entered = threading.Event()
        self.release = threading.Event()
        self.effects = []

    def call(self, name, args, brain):
        self.entered.set()
        assert self.release.wait(5), "fixture failed to release dispatch"
        bridge.call(lambda: self.effects.append(args["value"]))
        return {"result": {"applied": args["value"]}, "images": []}


workspace.set_folder(tempfile.mkdtemp(prefix="bwm_dispatch_"))
bpy.context.scene["blendwme_scene_id"] = "scene-one"
host = Host()
server = socket_bridge.SocketBridge(host)
server.start()


def request(identity, value=1):
    return {"op": "call", "id": identity, "client": "fixture", "token": server.token,
            "task": host.task_id, "scene": bpy.context.scene["blendwme_scene_id"],
            "tool": "run_python", "args": {"value": value}, "brain": "claude", "expires": time.time() + 10}


def launch(payload):
    result = {}
    def run():
        result["reply"] = server._handle(json.dumps(payload).encode())
    worker = threading.Thread(target=run)
    worker.start()
    return worker, result


try:
    worker, result = launch(request("stale-task"))
    pump_until(host.entered.is_set)
    host.task_id = "task-two"   # no epoch change: specifically exercise the task validator
    host.release.set()
    pump_until(lambda: not worker.is_alive())
    check("task changed after socket validation cannot mutate", not host.effects and not result["reply"]["ok"])

    host.entered.clear()
    worker, result = launch(request("stale-scene"))
    pump_until(lambda: bridge.pending() > 0)
    bpy.context.scene["blendwme_scene_id"] = "scene-two"
    pump_until(lambda: not worker.is_alive())
    check("scene changed before dispatch cannot mutate", not host.effects and not result["reply"]["ok"])

    host.entered.clear()
    host.release.clear()
    payload = request("retry", 7)
    first, first_result = launch(payload)
    pump_until(host.entered.is_set)
    retry, retry_result = launch(payload)
    conflict, conflict_result = launch(dict(payload, args={"value": 8}))
    pump_until(lambda: not conflict.is_alive())
    check("retry identity cannot be reused for different arguments",
          not conflict_result["reply"]["ok"] and "different arguments" in conflict_result["reply"]["error"])
    host.release.set()
    pump_until(lambda: not first.is_alive() and not retry.is_alive())
    check("concurrent retries apply once and share their outcome",
          host.effects == [7] and first_result["reply"] == retry_result["reply"])

    invalid = request("invalid-deadline")
    invalid["expires"] = float("nan")
    check("nonfinite deadlines are refused", not server._handle(json.dumps(invalid).encode())["ok"])

    host.entered.clear()
    host.release.clear()
    worker, result = launch(request("shutdown"))
    pump_until(host.entered.is_set)
    server.stop()
    host.release.set()
    pump_until(lambda: not worker.is_alive())
    check("shutdown rejects work already accepted by the socket", host.effects == [7] and not result["reply"]["ok"])

    # The dispatch boundary checks its own deadline, even before the waiting
    # worker has had a chance to mark the pending job cancelled.
    effects, errors = [], []
    def expired_job():
        try:
            with bridge.scope(deadline=time.time() - 1):
                bridge.call(lambda: effects.append("late"))
        except TimeoutError:
            errors.append("expired")
    worker = threading.Thread(target=expired_job)
    worker.start()
    worker.join(2)
    bridge._pump()
    check("expired request never enters the scene queue", not effects and errors == ["expired"])
finally:
    host.release.set()
    server.stop()

print(f"ALL {passed} DISPATCH CHECKS PASSED", flush=True)
