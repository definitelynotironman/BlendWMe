"""BlendWMe v0.2.1 — original v0.2 runtime with the Graphite Paper UI."""
bl_info = {'name':'BlendWMe','author':'definitelynotironman','version':(0,2,3),
           'blender':(4,2,0),'location':'3D View > N Sidebar > BlendWMe','category':'3D View'}

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from pathlib import Path
import bpy
from bpy.app.handlers import persistent
from . import protocol, demo, scene_tools, overlays, ui_bridge

ACTIVE={'THINKING','READY','RUNNING','TEACHING','PAUSED'}

class Runtime:
    def __init__(self):
        self.status='IDLE';self.mode='COLLABORATE';self.scene_ptr=0;self.scene=None
        self.pending=None;self.baseline=None;self.history=[];self.aliases={}
        self.process=None;self.folder=None;self.started=0;self.next_at=0
        self.instruction='';self.prefix='';self.last_signature=None;self.mutating=False
        self.request_provider='DEMO';self.manual_edits=False

    def cancel_request(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            # No blocking wait in Blender's UI. Reap asynchronously with a short deadline.
            old=self.process
            retired.append((old,self.folder,time.monotonic()+2))
            self.folder=None
        elif self.folder:
            shutil.rmtree(self.folder,ignore_errors=True)
        self.process=None;self.folder=None

    def pause(self,message='Paused. Edit Blender, then Resume when ready.'):
        self.cancel_request();self.pending=None;self.status='PAUSED'
        log('BlendWMe',message)

    def stop(self):
        self.cancel_request();self.pending=None;self.status='STOPPED'
        log('BlendWMe','Stopped. Applied changes remain editable; Blender Undo can reverse steps. Send starts a new task.')

RT=Runtime()
retired=[]

def log(role,message):
    if not RT.scene or not hasattr(RT.scene,'bwm'):return
    s=RT.scene.bwm
    entry=s.messages.add();entry.role=role;entry.body=message
    while len(s.messages)>100:s.messages.remove(0)
    s.message_index=len(s.messages)-1
    s.status=message[:400]
    txt=bpy.data.texts.get('BlendWMe Conversation') or bpy.data.texts.new('BlendWMe Conversation')
    txt.write(f'[{time.strftime("%H:%M:%S")}] {role}: {message}\n')

def redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in ('VIEW_3D','PROPERTIES'):area.tag_redraw()

def payload():
    scene=bpy.context.scene
    RT.baseline=scene_tools.observe(scene)
    RT.last_signature=protocol.signature(RT.baseline)
    return {'instruction':RT.instruction,'mode':RT.mode,'scene':RT.baseline,
        'history':RT.history[-24:],'prefix':RT.prefix,'manual_edits':RT.manual_edits}

def accept(raw):
    response=protocol.parse_response(raw)
    if protocol.signature(scene_tools.observe(RT.scene))!=RT.last_signature:
        RT.manual_edits=True
        RT.pause('Scene changed while planning. Resume to request a fresh step using your changes.');return
    log('BlendWMe',response['message'])
    if response['done'] or not response['action']:
        RT.status='COMPLETE' if response['done'] else 'PAUSED';RT.pending=None;return
    RT.pending=response
    if not response['guide']['instruction']:
        response['guide']['instruction']=response['message']+' '+json.dumps(response['action']['args'])
    if RT.mode=='TEACH':RT.status='TEACHING'
    elif RT.mode=='COLLABORATE':RT.status='READY'
    else:
        RT.status='RUNNING';RT.next_at=time.monotonic()+max(.8,RT.scene.bwm.delay)
    redraw()

def python_command():
    # Blender's sys.executable is often the Blender executable, not Python.
    prefix=Path(sys.prefix)
    candidates=[prefix/'bin'/f'python{sys.version_info.major}.{sys.version_info.minor}',prefix/'bin'/'python3',prefix/'bin'/'python.exe',prefix/'python.exe']
    for candidate in candidates:
        if candidate.is_file():return [str(candidate),str(Path(__file__).with_name('worker.py'))]
    # Portable fallback launches Blender factory-startup background, with no user file/scripts.
    return [bpy.app.binary_path,'--background','--factory-startup','--python',str(Path(__file__).with_name('worker.py')),'--']

def request_next():
    RT.cancel_request();RT.pending=None
    if len(RT.history)>=24:
        RT.status='COMPLETE';log('BlendWMe','Reached the 24-action session limit. Inspect the result and Send a new task.');return
    if bpy.context.mode!='OBJECT':
        RT.pause('Return to Object Mode, then Resume.');return
    p=payload();settings=RT.scene.bwm
    RT.request_provider=settings.provider
    if settings.provider=='DEMO':
        accept(demo.next_demo(p));return
    if hasattr(bpy.app,'online_access') and not bpy.app.online_access:
        RT.pause('Blender Online Access is disabled. Enable it in Preferences or select Local Demo.');return
    cfg=bpy.context.window_manager.bwm_connection
    from urllib.parse import urlparse
    endpoint=cfg.nebius_url if settings.provider=='NEBIUS' else cfg.ollama_url
    parsed=urlparse(endpoint)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
        raise ValueError('Use an endpoint URL without credentials, query or fragment')
    if settings.provider=='NEBIUS' and parsed.scheme!='https':
        raise ValueError('Nebius requires an HTTPS endpoint')
    if settings.provider=='OLLAMA' and parsed.scheme not in ('http','https'):
        raise ValueError('Ollama requires an HTTP(S) endpoint')
    # Prevent accidental credential forwarding to a typo/custom host.
    if settings.provider=='NEBIUS' and parsed.hostname not in ('api.tokenfactory.nebius.com','api.studio.nebius.com'):
        raise ValueError('Nebius endpoint must use api.tokenfactory.nebius.com or api.studio.nebius.com')
    model=cfg.nebius_model if settings.provider=='NEBIUS' else cfg.ollama_model
    if not model.strip():raise ValueError('Enter an exact model ID from your provider console')
    config={'provider':settings.provider,'url':endpoint,'model':model.strip(),'system':protocol.SYSTEM}
    RT.folder=tempfile.mkdtemp(prefix='blendwme-')
    inp=Path(RT.folder)/'request.json';out=Path(RT.folder)/'response.json'
    inp.write_text(json.dumps({'config':config,'payload':p}),encoding='utf-8')
    env=os.environ.copy()
    env['BLENDWME_API_KEY']=cfg.api_key if settings.provider=='NEBIUS' else ''
    kwargs={'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL,'env':env}
    if os.name=='nt':kwargs['creationflags']=subprocess.CREATE_NO_WINDOW
    RT.process=subprocess.Popen(python_command()+[str(inp),str(out)],**kwargs)
    RT.started=time.monotonic();RT.status='THINKING'
    ui_bridge.request_started()
    log('BlendWMe','Reading the live scene and requesting one step. Pause and Stop remain available.')

def record(obj,message):
    action=RT.pending['action']
    RT.history.append({'action':action,'object_id':scene_tools.identity(obj),'result':message})
    RT.aliases[action['args']['name']]=scene_tools.identity(obj)
    RT.pending=None
    log('You' if RT.mode=='TEACH' else 'Tool',message)
    RT.last_signature=protocol.signature(scene_tools.observe(RT.scene))
    if RT.mode=='TAKEOVER':
        # Yield a full visual interval before observing/planning the next action.
        RT.status='RUNNING';RT.next_at=time.monotonic()+max(.8,RT.scene.bwm.delay)
    else:
        RT.status='PAUSED';log('BlendWMe','Your turn. Inspect or edit this part, then Resume for the next step.')

def apply_pending():
    if RT.status not in ('READY','RUNNING') or RT.mode=='TEACH' or not RT.pending:return False
    if protocol.signature(scene_tools.observe(RT.scene))!=RT.last_signature:
        RT.manual_edits=True;RT.pause('Your scene changed. Resume to plan from the new state.');return False
    RT.mutating=True
    try:
        obj=scene_tools.execute(RT.scene,RT.pending['action'])
        record(obj,f"{RT.pending['action']['tool']} → {obj.name}")
    finally:RT.mutating=False
    return True

def tick():
    now=time.monotonic()
    for proc,folder,deadline in list(retired):
        if proc.poll() is not None:
            if folder:shutil.rmtree(folder,ignore_errors=True)
            retired.remove((proc,folder,deadline))
        elif now>deadline:proc.kill()
    try:
        if RT.status not in ACTIVE:return .15
        if not bpy.context.scene or bpy.context.scene.as_pointer()!=RT.scene_ptr:
            RT.cancel_request();RT.pending=None;RT.status='STOPPED';return .15
        if RT.scene.bwm.mode!=RT.mode:
            RT.mode=RT.scene.bwm.mode;RT.pause('Mode changed. Resume to continue in the new mode.');return .15
        if RT.status=='THINKING':
            out=Path(RT.folder)/'response.json'
            if out.exists():
                result=json.loads(out.read_text(encoding='utf-8'))
                RT.cancel_request()
                ui_bridge.request_finished(bool(result.get('ok')))
                if not result.get('ok'):raise ValueError(result.get('error','Provider failed'))
                accept(result['content'])
            elif RT.process.poll() is not None:
                raise ValueError('Request process exited without a response. Check your Python/Blender installation.')
            elif now-RT.started>60:
                RT.pause('Provider timed out. Check connection settings, then Resume.')
        elif RT.status=='RUNNING' and now>=RT.next_at:
            if bpy.context.mode!='OBJECT':RT.pause('Manual editing detected. Finish editing, return to Object Mode, then Resume.')
            elif RT.pending:
                area=next((a for a in bpy.context.screen.areas if a.type=='VIEW_3D'),None) if bpy.context.screen else None
                if area:
                    region=next((r for r in area.regions if r.type=='WINDOW'),None)
                    # Blender's background mode has no interactive Undo stack.
                    with bpy.context.temp_override(area=area,region=region):bpy.ops.bwm.apply_step('EXEC_DEFAULT',not bpy.app.background)
                else:RT.pause('Open a 3D View to continue.')
            elif protocol.signature(scene_tools.observe(RT.scene))!=RT.last_signature:
                RT.manual_edits=True;RT.pause('Manual scene change detected. Resume to use your changes.')
            else:request_next()
        redraw()
    except Exception as exc:
        RT.cancel_request();RT.pending=None;RT.status='PAUSED'
        log('Error',str(exc)[:500]);redraw()
    return .15

class BWM_Message(bpy.types.PropertyGroup):
    role:bpy.props.StringProperty()
    body:bpy.props.StringProperty()

class BWM_Settings(bpy.types.PropertyGroup):
    mode:bpy.props.EnumProperty(name='Mode',items=[('TEACH','Teach Me','You perform each step; guides explain where and why'),('COLLABORATE','Build With Me','Preview and apply one step, then edit'),('TAKEOVER','Take Over','Perform small steps with a visible interval')],default='COLLABORATE')
    provider:bpy.props.EnumProperty(name='Provider',items=[('DEMO','Local Demo · no AI','Finite lamp, desk, snowman, cube and selected-object recipes'),('NEBIUS','Nebius · model API','Requires your own key and model ID'),('OLLAMA','Ollama · local model','Requires Ollama running on your computer')],default='DEMO')
    prompt:bpy.props.StringProperty(name='Message',default='Build a desk lamp')
    delay:bpy.props.FloatProperty(name='Seconds per step',default=2,min=.8,max=15)
    overlay:bpy.props.BoolProperty(name='Visual guides',default=True)
    guide_details:bpy.props.BoolProperty(name='Full step details',default=False)
    ui_theme:bpy.props.EnumProperty(name='Theme',items=[('BLENDER','Blender Grey','Dark shell with orange guidance'),('GRAPHITE','Graphite Paper','Paper shell with red guidance'),('RED_WHITE','Red / White','White shell with red controls')],default='BLENDER')
    compact_panel:bpy.props.BoolProperty(name='Compact panel',default=False)
    graphite_scale:bpy.props.FloatProperty(name='Panel scale',default=1.1,min=.6,max=2)
    reduce_motion:bpy.props.BoolProperty(name='Reduce pointer motion',default=False)
    status:bpy.props.StringProperty(default='Ready. Send a request to start.')
    messages:bpy.props.CollectionProperty(type=BWM_Message)
    message_index:bpy.props.IntProperty(default=0)

class BWM_Connection(bpy.types.PropertyGroup):
    api_key:bpy.props.StringProperty(name='API Key',subtype='PASSWORD',options={'SKIP_SAVE'})
    nebius_url:bpy.props.StringProperty(name='Nebius endpoint',default='https://api.tokenfactory.nebius.com/v1',options={'SKIP_SAVE'})
    nebius_model:bpy.props.StringProperty(name='Nebius model ID',default='',options={'SKIP_SAVE'})
    ollama_url:bpy.props.StringProperty(name='Ollama URL',default='http://127.0.0.1:11434',options={'SKIP_SAVE'})
    ollama_model:bpy.props.StringProperty(name='Ollama model',default='qwen2.5-coder:7b',options={'SKIP_SAVE'})

class BWM_OT_send(bpy.types.Operator):
    bl_idname='bwm.send';bl_label='Send';bl_description='Start a new task using the current scene'
    def execute(self,context):
        if RT.status in ACTIVE:
            self.report({'INFO'},'Finish or Stop the active task before sending another');return {'CANCELLED'}
        prompt=context.scene.bwm.prompt.strip()
        if not prompt:self.report({'ERROR'},'Enter a request');return {'CANCELLED'}
        RT.cancel_request();RT.scene=context.scene;RT.scene_ptr=context.scene.as_pointer()
        RT.mode=context.scene.bwm.mode;RT.instruction=prompt;RT.history=[];RT.aliases={};RT.manual_edits=False
        RT.prefix='BWM_'+uuid.uuid4().hex[:5]+'_';RT.pending=None
        log('You',prompt)
        try:request_next()
        except Exception as e:RT.pause(str(e));self.report({'ERROR'},str(e));return {'CANCELLED'}
        return {'FINISHED'}

class BWM_OT_apply(bpy.types.Operator):
    bl_idname='bwm.apply_step';bl_label='Apply This Step';bl_options={'REGISTER','UNDO'}
    bl_description='Apply the displayed validated action, then give control back to you'
    def execute(self,context):
        try:return {'FINISHED'} if apply_pending() else {'CANCELLED'}
        except Exception as exc:
            RT.pause(str(exc));self.report({'ERROR'},str(exc));return {'CANCELLED'}

class BWM_OT_pause(bpy.types.Operator):
    bl_idname='bwm.pause';bl_label='Pause & Edit'
    def execute(self,context):RT.pause();redraw();return {'FINISHED'}

class BWM_OT_resume(bpy.types.Operator):
    bl_idname='bwm.resume';bl_label='Resume from Live Scene'
    def execute(self,context):
        if RT.status!='PAUSED':return {'CANCELLED'}
        RT.mode=context.scene.bwm.mode
        RT.manual_edits=protocol.signature(scene_tools.observe(context.scene))!=RT.last_signature
        try:request_next()
        except Exception as e:RT.pause(str(e));self.report({'ERROR'},str(e));return {'CANCELLED'}
        return {'FINISHED'}

class BWM_OT_stop(bpy.types.Operator):
    bl_idname='bwm.stop';bl_label='Stop';bl_description='Cancel future work immediately; keep applied changes'
    def execute(self,context):RT.stop();redraw();return {'FINISHED'}

class BWM_OT_check(bpy.types.Operator):
    bl_idname='bwm.check';bl_label='Check My Step'
    use_selected:bpy.props.BoolProperty(default=False)
    def execute(self,context):
        if RT.status!='TEACHING' or not RT.pending:return {'CANCELLED'}
        try:
            obj,msg=scene_tools.verify(context.scene,RT.pending['action'],RT.baseline,RT.aliases,context.active_object if self.use_selected else None)
            record(obj,msg+' → '+obj.name);redraw()
        except Exception as e:self.report({'INFO'},str(e));log('Guide',str(e));return {'CANCELLED'}
        return {'FINISHED'}

class BWM_OT_skip(bpy.types.Operator):
    bl_idname='bwm.skip';bl_label='Keep My Edit / Skip This Step'
    bl_description='Keep this part as it is and count the proposed polish/transform step as skipped'
    def execute(self,context):
        if not RT.pending or RT.mode=='TAKEOVER' or RT.pending['action']['tool']=='create_primitive':return {'CANCELLED'}
        try:
            obj=scene_tools.get(context.scene,RT.pending['action']['args']['name'])
            record(obj,'Skipped at your request; kept '+obj.name+' unchanged')
        except ValueError as e:self.report({'INFO'},str(e));return {'CANCELLED'}
        return {'FINISHED'}

class BWM_OT_snapshot(bpy.types.Operator):
    bl_idname='bwm.snapshot';bl_label='Inspect Scene'
    def execute(self,context):
        txt=bpy.data.texts.get('BlendWMe Scene Snapshot') or bpy.data.texts.new('BlendWMe Scene Snapshot')
        txt.clear();txt.write(json.dumps(scene_tools.observe(context.scene),indent=2))
        self.report({'INFO'},'Snapshot: Text Editor > BlendWMe Scene Snapshot');return {'FINISHED'}

class BWM_OT_example(bpy.types.Operator):
    bl_idname='bwm.example';bl_label='Example'
    prompt:bpy.props.StringProperty()
    def execute(self,context):context.scene.bwm.prompt=self.prompt;return {'FINISHED'}

class BWM_OT_forget_key(bpy.types.Operator):
    bl_idname='bwm.forget_key';bl_label='Forget Key'
    def execute(self,context):context.window_manager.bwm_connection.api_key='';return {'FINISHED'}

class BWM_UL_messages(bpy.types.UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):
        row=layout.row(align=True);row.label(text=item.role+':');row.label(text=item.body.replace('\n',' ')[:80])

def wrapped(layout,text,width):
    for paragraph in text.splitlines() or ['']:
        for line in textwrap.wrap(paragraph,width) or ['']:layout.label(text=line)

class BWM_PT_panel(bpy.types.Panel):
    bl_label='BlendWMe';bl_idname='BWM_PT_panel';bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='BlendWMe'
    def draw(self,context):
        l=self.layout;s=context.scene.bwm
        l.operator('bwm.overlay',text='Show / hide BlendWMe',icon='GREASEPENCIL')
        l.prop(s,'ui_theme')
        l.prop(s,'compact_panel')
        l.prop(s,'graphite_scale')
        l.prop(s,'reduce_motion')
        l.prop(s,'overlay')
        l.prop(s,'provider',text='Provider')
        if s.mode=='TAKEOVER':l.prop(s,'delay')
        if RT.status=='PAUSED':l.operator('bwm.resume',icon='PLAY')
        elif RT.status in ACTIVE:l.operator('bwm.pause',icon='PAUSE')
        if RT.status in ACTIVE:l.operator('bwm.stop',icon='CANCEL')
        l.operator('bwm.ui_details',text='Step details / Pointers',icon='INFO')
        l.operator('bwm.snapshot',icon='VIEWZOOM')

class BWM_PT_connection(bpy.types.Panel):
    bl_label='Connection & Pointers';bl_idname='BWM_PT_connection';bl_parent_id='BWM_PT_panel'
    bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='BlendWMe';bl_options={'DEFAULT_CLOSED'}
    def draw(self,context):
        l=self.layout;c=context.window_manager.bwm_connection;s=context.scene.bwm
        if s.provider=='NEBIUS':
            l.prop(c,'nebius_url');l.prop(c,'nebius_model');l.prop(c,'api_key');l.operator('bwm.forget_key')
            wrapped(l,'Key stays in this Blender session. Copy an exact model ID from your Nebius console. Requests send prompts and scene summaries.',30)
        elif s.provider=='OLLAMA':l.prop(c,'ollama_url');l.prop(c,'ollama_model')
        l.operator('bwm.unpin');wrapped(l,'Pointers use editor regions by default. Pin Pointer circles the exact UI control you click. Re-pin after changing layout.',30)

@persistent
def load_pre(_):
    ui_bridge.reset()
    RT.cancel_request();RT.pending=None;RT.status='IDLE';RT.scene=None;RT.scene_ptr=0
    bpy.context.window_manager.bwm_connection.api_key=''
    scene_tools._ids.clear()

@persistent
def undo_post(_):
    if RT.status in ACTIVE and not RT.mutating:
        # Undo can replace RNA wrappers; don't log through the old Scene pointer.
        RT.scene=bpy.context.scene;RT.scene_ptr=RT.scene.as_pointer()
        scene_tools._ids.clear()
        RT.stop();log('BlendWMe','Undo/Redo changed the scene. Task stopped to discard its old history; Send a new request.')

CLASSES=(BWM_Message,BWM_Settings,BWM_Connection,BWM_OT_send,BWM_OT_apply,BWM_OT_pause,BWM_OT_resume,BWM_OT_stop,BWM_OT_check,BWM_OT_skip,BWM_OT_snapshot,BWM_OT_example,BWM_OT_forget_key,BWM_UL_messages,overlays.BWM_OT_pin,overlays.BWM_OT_unpin,BWM_PT_panel,BWM_PT_connection)+ui_bridge.CLASSES

def register():
    for c in CLASSES:bpy.utils.register_class(c)
    bpy.types.Scene.bwm=bpy.props.PointerProperty(type=BWM_Settings)
    bpy.types.WindowManager.bwm_connection=bpy.props.PointerProperty(type=BWM_Connection,options={'SKIP_SAVE'})
    overlays.install(RT)
    ui_bridge.install(RT)
    bpy.app.handlers.load_pre.append(load_pre)
    bpy.app.handlers.undo_post.append(undo_post);bpy.app.handlers.redo_post.append(undo_post)
    bpy.app.timers.register(tick,first_interval=.15)

def unregister():
    RT.cancel_request()
    for proc,folder,_ in retired:
        if proc.poll() is None:proc.kill()
        if folder:shutil.rmtree(folder,ignore_errors=True)
    retired.clear();ui_bridge.remove();overlays.remove()
    if bpy.app.timers.is_registered(tick):bpy.app.timers.unregister(tick)
    for handlers,fn in ((bpy.app.handlers.load_pre,load_pre),(bpy.app.handlers.undo_post,undo_post),(bpy.app.handlers.redo_post,undo_post)):
        if fn in handlers:handlers.remove(fn)
    del bpy.types.Scene.bwm;del bpy.types.WindowManager.bwm_connection
    for c in reversed(CLASSES):bpy.utils.unregister_class(c)

if __name__=='__main__':register()
