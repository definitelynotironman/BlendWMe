"""Nonmodal visual guides; rings can be pinned to real UI controls."""
import math
import time
import textwrap
import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader
from bpy_extras.view3d_utils import location_3d_to_region_2d
from . import ui_bridge

_handles=[]
_runtime=None
_anchors={}  # Session-only (area pointer, region type, normalized x/y) by guide target.

def shape(points, color, kind='LINE_STRIP'):
    shader=gpu.shader.from_builtin('UNIFORM_COLOR')
    batch=batch_for_shader(shader,kind,{'pos':points})
    shader.bind();shader.uniform_float('color',color);batch.draw(shader)

def ring(x,y,r=22,icon=False):
    ui_bridge.pointer(x,y,r,icon=icon)

def rect(x,y,w,h,color):
    shape([(x,y),(x+w,y),(x+w,y+h),(x,y+h)],color,'TRI_FAN')

def txt(x,y,s,size=14,color=None):
    font=ui_bridge.ui.FONT_ID
    blf.size(font,size);blf.color(font,*(color or ui_bridge.ui.INK));blf.position(font,x,y,0);blf.draw(font,s)

def card(region,runtime):
    left=68 if bpy.context.space_data.show_region_toolbar else 18
    w=min(490,max(210,region.width-left-20))
    chars=max(24,int((w-30)/7.4))
    guide=runtime.pending['guide'] if runtime.pending else {}
    instruction=guide.get('instruction') or (runtime.pending or {}).get('message','')
    lines=textwrap.wrap(instruction,chars)[:8]
    facts=textwrap.wrap(guide.get('why',''),chars)[:3]
    h=104+len(lines)*19+len(facts)*18
    # Leave room for Blender's own view/object labels, which render over POST_PIXEL.
    x=left;y=max(60,region.height-h-108)
    rect(x,y,w,h,ui_bridge.ui.PAPER)
    rect(x,y+h-4,w,4,ui_bridge.ui.HOT)
    txt(x+15,y+h-29,'BlendWMe / '+ ('TEACH ME' if runtime.mode=='TEACH' else runtime.status.replace('_',' ')),13,ui_bridge.ui.HOT)
    cursor=y+h-57
    for line in lines: txt(x+15,cursor,line);cursor-=19
    cursor-=9
    for line in facts: txt(x+15,cursor,line,12,ui_bridge.ui.MUTED);cursor-=18
    txt(x+15,y+16,'N > BlendWMe: Check My Step / Pause / Stop',11,ui_bridge.ui.MUTED)
    return x,y,w,h

def draw(region_type):
    rt=_runtime
    ctx=bpy.context
    if not rt or not rt.pending or rt.status not in ('TEACHING','READY','RUNNING','PAUSED') or not ctx.area or not ctx.region:
        return
    if not ctx.scene.bwm.overlay or ctx.scene.as_pointer()!=rt.scene_ptr:
        return
    ui_bridge.ui.set_theme(ctx.scene.bwm.ui_theme)
    target=rt.pending['guide'].get('target','OBJECT')
    if time.time()<getattr(ui_bridge.ui.STATE,'flash_external_until',0):target=ui_bridge.ui.STATE.flash_external
    anchor=_anchors.get(target)
    gpu.state.blend_set('ALPHA');gpu.state.line_width_set(2)
    try:
        reg=ctx.region
        if ctx.area.type=='VIEW_3D' and region_type=='WINDOW':
            box=None if ui_bridge.is_open(ctx) else card(reg,rt)
            if not anchor and target=='OBJECT':
                a=rt.pending.get('action')
                obj=ctx.scene.objects.get(a['args']['name']) if a else ctx.active_object
                if obj:
                    p=location_3d_to_region_2d(reg,ctx.space_data.region_3d,obj.matrix_world.translation)
                    if p:
                        ring(p.x,p.y,28)
                        if box:shape([(box[0]+box[2],box[1]+box[3]/2),(p.x,p.y)],ui_bridge.ui.HOT)
            if not anchor and not ui_bridge.is_open(ctx) and target in ('ADD','ITEM','MATERIAL','MODIFIER'):
                label={'ADD':'Add menu in viewport header / Shift+A','ITEM':'N sidebar > Item: Location, Rotation, Scale','MATERIAL':'Properties > Material (red sphere icon)','MODIFIER':'Properties > Modifiers (wrench icon)'}[target]
                txt(22,24,label,13,ui_bridge.ui.HOT)
        if anchor and anchor[0]==ctx.area.as_pointer() and anchor[1]==region_type:
            x,y=anchor[2]*reg.width,anchor[3]*reg.height
            radius=min(22,max(8,reg.width/2-2),max(8,reg.height/2-2))
            ring(x,y,radius,icon=True)
            if reg.height>55:
                txt(max(4,min(x+29,reg.width-190)),max(12,min(y+20,reg.height-18)),'Next: '+target.title(),12,ui_bridge.ui.HOT)
        elif not anchor and target=='ADD' and region_type=='HEADER' and ctx.area.type=='VIEW_3D':
            # Highlight the region honestly; exact menu/button geometry is not exposed by bpy.
            shape([(2,2),(reg.width-2,2),(reg.width-2,reg.height-2),(2,reg.height-2),(2,2)],ui_bridge.ui.HOT)
    finally:
        gpu.state.line_width_set(1);gpu.state.blend_set('NONE')

def install(runtime):
    global _runtime
    _runtime=runtime
    if _handles:return
    for region in ('WINDOW','HEADER','UI','TOOLS','TOOL_HEADER'):
        handle=bpy.types.SpaceView3D.draw_handler_add(draw,(region,),region,'POST_PIXEL')
        _handles.append((bpy.types.SpaceView3D,handle,region))
    for region in ('WINDOW','HEADER','NAVIGATION_BAR'):
        handle=bpy.types.SpaceProperties.draw_handler_add(draw,(region,),region,'POST_PIXEL')
        _handles.append((bpy.types.SpaceProperties,handle,region))

def remove():
    global _runtime
    for cls,h,r in _handles:
        try:cls.draw_handler_remove(h,r)
        except (ValueError,ReferenceError):pass
    _handles.clear();_anchors.clear();_runtime=None

class BWM_OT_pin(bpy.types.Operator):
    bl_idname='bwm.pin'
    bl_label='Pin Pointer to UI'
    bl_description='Click an actual UI control to pin the current guide ring there; Esc cancels'

    def invoke(self,context,event):
        if not _runtime or not _runtime.pending:
            self.report({'INFO'},'Start a teaching step first');return {'CANCELLED'}
        self.target=_runtime.pending['guide'].get('target','OBJECT')
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set('BlendWMe: click the control to circle. Esc cancels. The click will not activate it.')
        return {'RUNNING_MODAL'}

    def modal(self,context,event):
        if event.type in ('ESC','RIGHTMOUSE'):
            context.workspace.status_text_set(None);return {'CANCELLED'}
        if event.type=='LEFTMOUSE' and event.value=='PRESS':
            for area in context.screen.areas:
                if area.type not in ('VIEW_3D','PROPERTIES'):continue
                for reg in area.regions:
                    if reg.type not in ('WINDOW','HEADER','UI','TOOLS','TOOL_HEADER','NAVIGATION_BAR'):continue
                    if reg.x<=event.mouse_x<reg.x+reg.width and reg.y<=event.mouse_y<reg.y+reg.height:
                        _anchors[self.target]=(area.as_pointer(),reg.type,(event.mouse_x-reg.x)/reg.width,(event.mouse_y-reg.y)/reg.height)
                        context.workspace.status_text_set(None);area.tag_redraw();return {'FINISHED'}
            self.report({'INFO'},'Choose a control in the 3D View or Properties editor')
            return {'RUNNING_MODAL'}
        return {'RUNNING_MODAL'}

class BWM_OT_unpin(bpy.types.Operator):
    bl_idname='bwm.unpin'
    bl_label='Reset Pointers'
    def execute(self,context):
        _anchors.clear()
        for area in context.screen.areas:area.tag_redraw()
        return {'FINISHED'}
