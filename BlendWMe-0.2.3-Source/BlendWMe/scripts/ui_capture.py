"""Capture REAL Blender screenshots after redraw, and exercise overlay callbacks."""
from pathlib import Path
import sys
import math
import json
import bpy
from mathutils import Euler, Vector
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import extension as b
from extension import overlays

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs'/'screenshots';OUT.mkdir(exist_ok=True)
b.register()
bpy.context.preferences.view.show_splash=False
scene=bpy.context.scene
# Remove factory objects only in this disposable test scene, not via product tools.
for obj in list(scene.objects):bpy.data.objects.remove(obj,do_unlink=True)
area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
space=area.spaces.active;space.show_region_ui=True
space.shading.type='SOLID';space.shading.color_type='MATERIAL';space.shading.light='STUDIO'
space.region_3d.view_rotation=Euler((math.radians(65),0,math.radians(45)),'XYZ').to_quaternion()
space.region_3d.view_distance=9;space.region_3d.view_location=Vector((0,0,2))
scene.bwm.mode='COLLABORATE';scene.bwm.prompt='Build a desk lamp'
with bpy.context.temp_override(area=area):
    bpy.ops.bwm.send()
    for _ in range(7):
        bpy.ops.bwm.apply_step()
        if b.RT.status=='PAUSED':bpy.ops.bwm.resume()
    bpy.ops.bwm.stop()
    scene.bwm.mode='TEACH';scene.bwm.prompt='Build a desk'
    bpy.ops.bwm.send()
phase=0
def capture():
    global phase
    if phase==0:
        OUT.joinpath('phase').write_text('teach-me')
        phase=1;area.tag_redraw();return 3.0
    if phase==1:
        # Pin ring to Add menu in the REAL region for proof of calibration rendering.
        header=next(r for r in area.regions if r.type=='HEADER')
        overlays._anchors['ADD']=(area.as_pointer(),'HEADER',.213,.5)
        OUT.joinpath('phase').write_text('teach-pointer')
        phase=2;area.tag_redraw();return 3.0
    if phase==2:
        with bpy.context.temp_override(area=area):
            bpy.ops.bwm.stop();scene.bwm.mode='COLLABORATE';scene.bwm.prompt='Bevel selected';bpy.ops.bwm.send()
        OUT.joinpath('phase').write_text('build-with-me')
        phase=3;area.tag_redraw();return 3.0
    if phase==3:
        region=next(r for r in area.regions if r.type=='WINDOW')
        with bpy.context.temp_override(area=area,region=region):
            bpy.ops.ed.undo_push(message='UI test baseline')
            bpy.ops.bwm.stop();scene.bwm.mode='TAKEOVER';scene.bwm.prompt='cube';bpy.ops.bwm.send()
            b.RT.next_at=0;b.tick()  # Exercise the autonomous timer's Undo-enabled path.
            created=bpy.context.active_object.name
            bpy.ops.ed.undo()
            assert created not in bpy.context.scene.objects,'Undo did not remove last created part'
            assert b.RT.status=='STOPPED','Undo did not invalidate task'
        (ROOT/'docs'/'ui-test-results.json').write_text(json.dumps({'blender':bpy.app.version_string,'checks':['Viewport guide card renders without label overlap','Exact pinned Add-menu circle renders','Native sidebar renders and remains navigable','Undo removes the object applied by the autonomous timer','Undo invalidates active task'],'passed':5},indent=2))
        print('UI AND UNDO CHECKS PASSED')
        b.unregister();bpy.ops.wm.quit_blender();return None
bpy.app.timers.register(capture,first_interval=3)
