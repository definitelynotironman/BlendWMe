"""Malformed process output and controlled Stop races. No live clients."""
import json
import os
from pathlib import Path
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from blendwme.engines.base import ProcessEngine, TERMINAL
from blendwme.relay import Relay
from blendwme.state import IDLE, session

passed = 0
python = Path(__file__).resolve().parent.parent / ".venv" / "Scripts" / "python.exe"


def check(name, condition):
    global passed
    assert condition, name
    passed += 1
    print("ok -", name, flush=True)


class FixtureEngine(ProcessEngine):
    def __init__(self, payload, parsed=None, failure=None):
        super().__init__()
        self.payload, self.parsed, self.failure = payload, parsed, failure

    def build(self, message, config):
        return [str(python), "-u", "-c", "print(" + repr(json.dumps(self.payload)) + ")"], None, {}

    def parse(self, obj):
        if self.failure:
            raise self.failure("injected parser failure")
        return self.parsed


for payload, parsed, failure in (([], None, None), ({}, None, ValueError), ({}, None, TypeError),
                                 ({}, [None], None), ({}, [{"text": "no type"}], None)):
    engine = FixtureEngine(payload, parsed, failure)
    events = []
    engine.start("fixture", events.append, {})
    engine.thread.join(8)
    terminals = [event for event in events if event["type"] in TERMINAL]
    check("malformed output ends once with a useful error", not engine.is_running() and
          len(terminals) == 1 and terminals[0]["type"] == "error")
engine = FixtureEngine({}, [{"type": "done"}, {"type": "done"}])
events = []
engine.start("fixture", events.append, {})
engine.thread.join(8)
check("duplicate terminal events collapse to one", len([event for event in events if event["type"] in TERMINAL]) == 1)

entered, release = threading.Event(), threading.Event()
class StartingEngine(FixtureEngine):
    def build(self, message, config):
        entered.set()
        assert release.wait(5)
        return super().build(message, config)


engine = StartingEngine({}, [])
events = []
launcher = threading.Thread(target=engine.start, args=("fixture", events.append, {}))
launcher.start()
assert entered.wait(5)
engine.interrupt()
release.set()
launcher.join(5)
engine.thread.join(8)
terminals = [event for event in events if event["type"] in TERMINAL]
check("Stop requested during startup survives process creation", not engine.is_running() and len(terminals) == 1 and
      terminals[0].get("stopped") is True)

class StandIn:
    thread = None
    def interrupt(self):
        pass


for fail_after_stop in (False, True):
    relay = Relay()
    relay.engines = {"claude": StandIn(), "codex": StandIn()}
    relay.active = "claude"
    relay.told = {"claude"}
    relay.config = {"ask_handoff_note": True}
    relay.handing_over = True
    entered, release = threading.Event(), threading.Event()
    launched = []
    def quiet(*args):
        entered.set()
        assert release.wait(5)
        if fail_after_stop:
            raise RuntimeError("late obsolete handoff error")
        return "done"
    relay._quiet_turn = quiet
    relay._start = lambda *args, **kwargs: launched.append(args)
    worker = threading.Thread(target=relay._hand_over, args=("claude", "codex", "fixture", relay.generation))
    worker.start()
    assert entered.wait(5)
    relay.stop()
    release.set()
    worker.join(5)
    check("Stop during handoff prevents a late target launch or error", not worker.is_alive() and not launched and
          relay.running is None and not relay.handing_over and session.status == IDLE)

print(f"ALL {passed} ENGINE FAULT CHECKS PASSED", flush=True)
