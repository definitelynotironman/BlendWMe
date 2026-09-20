import importlib.util,sys,json
from pathlib import Path
import bpy
root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('bwm_native_shell',root/'extension/__init__.py',submodule_search_locations=[str(root/'extension')])
a=importlib.util.module_from_spec(spec);sys.modules[spec.name]=a;spec.loader.exec_module(a);a.register()
u=a.ui_bridge.ui;checks=[]
def check(c,name):
 assert c,name
 checks.append(name)
check(bpy.context.scene.bwm.ui_theme=='BLENDER','Blender Grey default')
check(len(u.FONTS)==3 and all(v>=0 for v in u.FONTS.values()),'three real bundled fonts loaded')
# Check layout and control hit boxes without requiring a GPU draw context.
u.fill=lambda *x,**kw:None;u.stroke=lambda *x,**kw:None;u.text=lambda *x,**kw:None
class Region:width=1400;height=900
for theme in u.THEMES:
 a.ui_bridge.action('theme:'+theme)
 check(bpy.context.scene.bwm.ui_theme==theme and u.UI['theme']==theme,theme+' runtime selection')
 for page in ('main','settings','details'):
  u.STATE.page=page;u.STATE.s=1;hits=[];u.draw_panel(Region(),0,hits,0)
  check(bool(hits),theme+' '+page+' controls')
  panel=hits[-1];check(panel[1]>=0,theme+' '+page+' fits height')
  for x,y,w,h,aid in hits[:-1]:
   check(x>=panel[0]-1 and x+w<=panel[0]+panel[2]+1 and y>=panel[1]-1 and y+h<=panel[1]+panel[3]+1,theme+' '+page+' '+aid+' within panel')
a.ui_bridge.action('settings');check(u.STATE.page=='settings','custom settings page')
a.ui_bridge.action('motion');check(bpy.context.scene.bwm.reduce_motion,'reduced motion toggle')
a.ui_bridge.action('compact');check(u.STATE.compact,'compact toggle')
a.ui_bridge.action('input');check(u.STATE.focus and u.STATE.buffer==bpy.context.scene.bwm.prompt,'inline prompt focus')
a.ui_bridge.action('ref:resume');check(u.STATE.flash=='resume','control reference highlights without executing')
check(a.RT.status=='IDLE','reference does not run scene operations')
a.ui_bridge.action('tab:auto');a.ui_bridge.send('Build a desk')
check(a.RT.status=='RUNNING' and u.STATE.primary_label=='Pause & edit','Auto proposes without making objects')
a.ui_bridge.action('resume');check(a.RT.status=='PAUSED','Auto primary hands control back')
a.ui_bridge.action('pause');check(a.RT.status=='STOPPED','paused secondary stops')
a.unregister();check(not u.FONTS,'fonts cleaned up')
a.register();a.unregister();checks.append('second register/unregister')
(root/'docs/native-shell-checks.json').write_text(json.dumps({'checks':len(checks),'passed':True,'blender_version':bpy.app.version_string,'workflow_tested':False,'gpu_visual_review':False},indent=2))
print('NATIVE_UI_CHECKS',len(checks),'passed')
