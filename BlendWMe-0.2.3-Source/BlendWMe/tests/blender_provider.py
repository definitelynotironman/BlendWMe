"""Actual subprocess/HTTP integration, using a labelled fixture rather than paid inference."""
import json
import sys
import time
import subprocess
import tempfile
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import extension as b
report=[]
def check(cond,label):
    assert cond,label
    report.append(label);print('PASS:',label)
def until_not_thinking():
    start=time.monotonic()
    while b.RT.status=='THINKING' and time.monotonic()-start<10:
        b.tick();time.sleep(.02)
    check(b.RT.status!='THINKING','request exits thinking state')

folder=tempfile.TemporaryDirectory();port=Path(folder.name)/'port'
server=subprocess.Popen([str(Path(sys.prefix)/'bin'/'python3.11'),str(Path(__file__).with_name('fake_provider.py')),str(port)])
try:
    deadline=time.monotonic()+5
    while not port.exists() and time.monotonic()<deadline:time.sleep(.02)
    b.register();s=bpy.context.scene.bwm;c=bpy.context.window_manager.bwm_connection
    s.provider='OLLAMA';s.mode='COLLABORATE';c.ollama_url='http://127.0.0.1:'+port.read_text();c.ollama_model='fixture'
    s.prompt='fixture';t=time.monotonic();bpy.ops.bwm.send()
    check(time.monotonic()-t<.2 and b.RT.status=='THINKING','model request returns without blocking Blender')
    until_not_thinking();check(b.RT.status=='READY','valid HTTP result becomes a preview')
    bpy.ops.bwm.stop()
    s.prompt='SLOW';bpy.ops.bwm.send();process=b.RT.process;pending_folder=b.RT.folder
    bpy.ops.bwm.stop();until_not_thinking()
    time.sleep(.1);b.tick()
    check(b.RT.status=='STOPPED' and not b.RT.pending,'stopped request cannot schedule an action')
    check(process.poll() is not None,'stop terminates isolated worker')
    check(not Path(pending_folder).exists(),'cancelled request removes temporary scene JSON')
    s.prompt='SLOW';bpy.ops.bwm.send()
    bpy.context.active_object.location.x=4;bpy.context.view_layer.update()
    until_not_thinking();check(b.RT.status=='PAUSED' and not b.RT.pending,'stale model response discarded after scene edit')
    bpy.ops.bwm.stop();s.prompt='BAD';bpy.ops.bwm.send();until_not_thinking()
    check(b.RT.status=='PAUSED' and not b.RT.pending,'unsafe provider action rejected')
    bpy.ops.bwm.stop();s.prompt='SLOW';bpy.ops.bwm.send();s.mode='TEACH';b.tick()
    check(b.RT.status=='PAUSED' and b.RT.process is None,'mode change cancels old request')
    b.unregister()
finally:
    server.terminate();server.wait(timeout=5);folder.cleanup()
Path(__file__).resolve().parents[1].joinpath('docs/provider-test-results.json').write_text(json.dumps({'blender':bpy.app.version_string,'checks':report,'note':'HTTP fixture, not live Nebius/Ollama inference','passed':len(report)},indent=2))
print('ALL PROVIDER CHECKS PASSED')
