"""A disposable real Blender endpoint for test_two_instances.py."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
folder = Path(sys.argv[sys.argv.index("--") + 1])
from blendwme import bridge, socket_bridge, workspace
from blendwme.state import session

workspace.set_folder(str(folder))
session.mode = "AUTO"
server = socket_bridge.get()
server.start()
deadline = time.monotonic() + 60
try:
    while not (folder / "stop").exists() and time.monotonic() < deadline:
        bridge._pump()
        time.sleep(0.005)
finally:
    server.stop()
