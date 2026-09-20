"""Isolated UI QA on Linux/Xvfb. No interaction with the user's screen.
Usage: BWM_BLENDER=/path/blender BWM_XVFB=/path/Xvfb python scripts/run_ui_preview.py
Requires Pillow and an XTest library. Screenshots are actual Blender frames.
"""
import ctypes
import ctypes.util
import os
from pathlib import Path
import socket
import subprocess
import time
from PIL import ImageGrab

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs'/'screenshots';OUT.mkdir(exist_ok=True)
marker=OUT/'phase'
if marker.exists():marker.unlink()
env=os.environ.copy();env['DISPLAY']='127.0.0.1:101';env['LIBGL_ALWAYS_SOFTWARE']='1';env['GHOST_X11_NO_MITSHM']='1'
env['XDG_RUNTIME_DIR']='/tmp/bwm-runtime';Path(env['XDG_RUNTIME_DIR']).mkdir(exist_ok=True)
blender=env.get('BWM_BLENDER','blender');xvfb=env.get('BWM_XVFB','Xvfb')
log=open('/tmp/bwm-preview-x.log','w')
x=subprocess.Popen([xvfb,':101','-screen','0','1600x1000x24','-ac','-nolisten','unix','-nolisten','local','-listen','tcp'],env=env,stdout=log,stderr=log)
bl=None
try:
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        try:q=socket.create_connection(('127.0.0.1',6101),.2);q.close();break
        except OSError:time.sleep(.1)
    # On a clean Blender config this avoids the first-run setup modal.
    subprocess.run([blender,'--background','--factory-startup','--python-expr','import bpy;bpy.context.preferences.view.show_splash=False;bpy.ops.wm.save_userpref()'],env=env,check=True,stdout=subprocess.DEVNULL)
    bl=subprocess.Popen([blender,'--factory-startup','-p','0','0','1600','1000','--python',str(ROOT/'scripts'/'ui_capture.py')],env=env)
    time.sleep(1.5)
    libx=ctypes.CDLL(ctypes.util.find_library('X11'));libx.XOpenDisplay.restype=ctypes.c_void_p;libx.XOpenDisplay.argtypes=[ctypes.c_char_p]
    display=libx.XOpenDisplay(env['DISPLAY'].encode())
    xt=ctypes.CDLL(env.get('BWM_XTEST',ctypes.util.find_library('Xtst') or 'libXtst.so.6'))
    xt.XTestFakeMotionEvent.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_ulong]
    xt.XTestFakeButtonEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_ulong]
    libx.XFlush.argtypes=[ctypes.c_void_p]
    # Coordinate taken from the observed isolated 1600x1000 Blender UI.
    xt.XTestFakeMotionEvent(display,-1,1309,258,0)
    xt.XTestFakeButtonEvent(display,1,1,0);xt.XTestFakeButtonEvent(display,1,0,0);libx.XFlush(display)
    seen=set();deadline=time.monotonic()+35
    while bl.poll() is None and time.monotonic()<deadline:
        if marker.exists():
            name=marker.read_text()
            if name and name not in seen:
                time.sleep(.6)
                im=ImageGrab.grab(xdisplay=env['DISPLAY']);im.save(OUT/(name+'.png'))
                assert any(hi>0 for lo,hi in im.getextrema()),'Black screenshot'
                print('Captured',name);seen.add(name)
        time.sleep(.05)
    bl.wait(timeout=5)
    assert seen=={'teach-me','teach-pointer','build-with-me'},seen
finally:
    if bl and bl.poll() is None:bl.terminate();bl.wait(timeout=5)
    x.terminate();x.wait(timeout=5);log.close()
    if marker.exists():marker.unlink()
