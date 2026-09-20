"""Run with Blender 4.2 --background --factory-startup --python tests/blender_smoke.py."""
from pathlib import Path
import sys
import json
import math
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import extension as bwm
from extension import scene_tools as st

checks=[]
def check(condition,label):
    assert condition,label
    checks.append(label);print('PASS:',label)

bwm.register();s=bpy.context.scene.bwm
check(hasattr(bpy.types.Scene,'bwm'),'register scene settings')
s.mode='COLLABORATE';s.prompt='Build a desk lamp';s.provider='DEMO'
initial=len(bpy.context.scene.objects)
bpy.ops.bwm.send()
check(bwm.RT.status=='READY' and len(bpy.context.scene.objects)==initial,'collaboration previews without mutation')
bpy.ops.bwm.apply_step()
base=bpy.context.active_object
check(bwm.RT.status=='PAUSED' and base.type=='MESH','one operation then pause')
base.location.x=3;base.location.z=.5;base.scale.z=.5;base.name='User Base'
bpy.context.view_layer.update()
bpy.ops.bwm.resume();bpy.ops.bwm.skip()
check(bwm.RT.history[-1]['result'].startswith('Skipped'),'keep manual edit with skip')
bpy.ops.bwm.resume();bpy.ops.bwm.apply_step() # bevel
bpy.ops.bwm.resume()
action=bwm.RT.pending['action']
check(action['args']['location']==[3,0,2.25],'stem adapts to moved scaled renamed base')
bpy.ops.bwm.apply_step()
bpy.ops.bwm.stop();before=st.observe(bpy.context.scene)
for _ in range(3):bwm.tick()
check(st.observe(bpy.context.scene)==before,'stop prevents later actions')

s.mode='TEACH';s.prompt='Create a cube';bpy.ops.bwm.send()
check(bwm.RT.status=='TEACHING' and st.observe(bpy.context.scene)==before,'teach mode does not mutate scene')
# Simulate real human action, independently of BlendWMe tools.
bpy.ops.mesh.primitive_cube_add(location=(0,0,1))
human=bpy.context.active_object;human.name='My own cube'
bpy.ops.bwm.check()
check(bwm.RT.status=='PAUSED' and len(bwm.RT.history)==1,'teaching verifies user-created geometry')
check(human.name=='My own cube' and not human.get('_blendwme_id'),'teach verifier does not rename/tag user objects')
bpy.ops.bwm.resume()
check(bwm.RT.pending['action']['args']['name']=='My own cube','later teaching refers to adopted object')
bpy.ops.bwm.stop()

s.mode='TAKEOVER';s.prompt='cube';bpy.ops.bwm.send()
check(bwm.RT.status=='RUNNING','takeover scheduled rather than blocking')
n=len(bpy.context.scene.objects)
bpy.ops.bwm.pause();bwm.RT.next_at=0;bwm.tick()
check(len(bpy.context.scene.objects)==n and bwm.RT.status=='PAUSED','pause cancels queued action')
bpy.ops.bwm.resume()
bwm.RT.next_at=0;bwm.tick()
check(len(bpy.context.scene.objects)==n+1,'timer executes only one action')
# User changes scene between planned actions: automatic pause.
bpy.context.active_object.location.x=6;bpy.context.view_layer.update();bwm.RT.next_at=0;bwm.tick()
check(bwm.RT.status=='PAUSED','manual edit pauses autonomous sequence')
bpy.ops.bwm.stop()

# Strict scoped tools, shared materials/meshes and duplicate identities.
obj=bpy.context.active_object
dup=obj.copy();bpy.context.scene.collection.objects.link(dup)
check(st.identity(obj)!=st.identity(dup),'duplicated custom IDs do not collide')
shared=obj.data;old_materials=list(shared.materials)
st.execute(bpy.context.scene,{'tool':'set_material','args':{'name':obj.name,'base_color':[.1,.2,.3,1]}})
check(obj.data!=dup.data and list(dup.data.materials)==old_materials,'colour does not modify shared mesh/material slots')
for primitive in ('cube','cylinder','cone','plane','uv_sphere','torus'):
    o=st.execute(bpy.context.scene,{'tool':'create_primitive','args':{'primitive':primitive,'name':'Smoke_'+primitive}})
    check(len(o.data.vertices)>0,'primitive '+primitive)
check(all('mesh_hash' in o for o in st.observe(bpy.context.scene)['objects'] if o['type']=='MESH'),'mesh position fingerprints')

# Credentials must not appear in saved .blend.
bpy.context.window_manager.bwm_connection.api_key='BWM_SENTINEL_SECRET_NOT_TO_SAVE'
out=Path(__file__).resolve().parents[1]/'tests'/'secret-save.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(out),compress=False)
check(b'BWM_SENTINEL_SECRET_NOT_TO_SAVE' not in out.read_bytes(),'API key absent from saved blend bytes')
out.unlink()
bwm.unregister();check(not hasattr(bpy.types.Scene,'bwm'),'clean unregister')
report=Path(__file__).resolve().parents[1]/'docs'/'blender-test-results.json'
report.write_text(json.dumps({'blender':bpy.app.version_string,'checks':checks,'passed':len(checks)},indent=2))
print('ALL BLENDER CHECKS PASSED:',len(checks))
