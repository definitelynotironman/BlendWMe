"""Bind the Graphite Paper UI to the original v0.2 runtime."""
import hashlib
import json
import time
import bpy
from . import blendwme_ui as ui

_runtime = None
_verified_profile = None
_request_profile = None
MODES = {'build': 'COLLABORATE', 'teach': 'TEACH', 'auto': 'TAKEOVER'}
ACTIVE = {'THINKING', 'READY', 'RUNNING', 'TEACHING', 'PAUSED'}


def profile(context):
    s = context.scene.bwm
    c = context.window_manager.bwm_connection
    if s.provider == 'DEMO':
        return ('DEMO',)
    if s.provider == 'NEBIUS':
        key_hash = hashlib.sha256(c.api_key.encode()).hexdigest()
        return (s.provider, c.nebius_url, c.nebius_model, key_hash)
    return (s.provider, c.ollama_url, c.ollama_model)


def request_started():
    global _request_profile
    _request_profile = profile(bpy.context)


def request_finished(ok):
    global _verified_profile
    _verified_profile = _request_profile if ok else None


def sync(context):
    rt = _runtime
    if rt is None or not hasattr(context.scene, 'bwm'):
        return
    s = context.scene.bwm
    st = ui.STATE
    here = rt.scene_ptr == context.scene.as_pointer()
    status = rt.status if here else 'IDLE'
    pending = rt.pending if here else None
    ui.set_theme(s.ui_theme)
    st.compact=s.compact_panel
    st.mode = next(k for k, v in MODES.items() if v == s.mode)
    st.paused = status == 'PAUSED'
    st.prompt = s.prompt
    st.activity_label = {
        'IDLE': 'Ready', 'READY': 'Your turn', 'THINKING': 'Thinking',
        'RUNNING': 'Building', 'TEACHING': 'Your turn', 'PAUSED': 'Paused',
        'STOPPED': 'Stopped', 'COMPLETE': 'Finished',
    }.get(status, status.title())
    st.attention = status in ('READY', 'TEACHING', 'THINKING', 'RUNNING')
    st.log = [('YOU' if m.role == 'You' else ('TOOL' if m.role == 'Tool' else 'BLENDWME'),
               m.body.replace('\n', ' ')) for m in list(s.messages)[-30:]]
    if not st.log:
        st.log = [('BLENDWME', 'Choose a mode. Tell me what to make.')]
    n = len(rt.history) + (1 if pending else 0) if here else 0
    total = 0
    if here and rt.request_provider == 'DEMO':
        prompt = rt.instruction.lower()
        if 'lamp' in prompt or 'desk' in prompt or 'table' in prompt:
            total = 7
        elif 'snowman' in prompt:
            total = 4
        elif 'bevel' in prompt or 'polish' in prompt or 'color' in prompt or 'colour' in prompt:
            total = 1
        elif 'cube' in prompt or 'box' in prompt:
            total = 3
    st.step = (n, total)
    role = 'YOUR TURN' if status == 'TEACHING' else ('AI WORKING' if status in ('THINKING', 'RUNNING') else st.activity_label.upper())
    st.step_label = role + (f' · STEP {n} OF {total}' if n and total else (f' · STEP {n}' if n else ''))
    if pending:
        guide = pending['guide']
        st.step_text = guide.get('title') or pending['message']
        st.detail_text=guide.get('instruction','')+(' Why: '+guide['why'] if guide.get('why') else '')+' Values: '+json.dumps(pending['action']['args'])
    elif status == 'THINKING':
        st.step_text = 'Reading your scene. Planning the next step.'
    elif status == 'PAUSED':
        st.step_text = 'Edit freely. Resume reads your scene again.'
    elif status == 'RUNNING':
        st.step_text = 'Next part coming. Pause to take the controls.'
    elif status == 'COMPLETE':
        st.step_text = 'Made it. Every part stays editable.'
    elif status == 'STOPPED':
        st.step_text = 'Stopped. Send a prompt for a new task.'
    else:
        st.step_text = 'Build together, learn a step, or let Auto work.'
    st.primary_label = {'READY': 'Apply step', 'TEACHING': 'Check step', 'PAUSED': 'Resume',
                        'RUNNING': 'Pause & edit', 'THINKING': 'Pause & edit'}.get(status, 'Send')
    st.secondary_label = 'Pause' if status in ('READY', 'TEACHING') else 'Stop'
    st.connection_verified=current_verified = profile(context)==_verified_profile
    if not pending:st.detail_text=st.step_text
    current_profile = profile(context)
    if s.provider == 'DEMO':
        st.model_line = 'Local Demo · No AI'
    elif s.provider == 'NEBIUS':
        st.model_line = 'Nebius · ' + ('Connected' if current_profile == _verified_profile else 'Not verified')
    else:
        st.model_line = 'Ollama · ' + ('Connected' if current_profile == _verified_profile else 'Not verified')
    ui.UI['scale'] = s.graphite_scale
    ui.UI['reduce_motion'] = s.reduce_motion


def send(text):
    rt = _runtime
    context = bpy.context
    if rt is None or not text.strip():
        return
    if rt.status in ('THINKING', 'READY', 'RUNNING', 'TEACHING'):
        bpy.ops.bwm.ui_details('INVOKE_DEFAULT')
        return
    context.scene.bwm.prompt = text
    if rt.status == 'PAUSED' and text.strip() == rt.instruction.strip():
        bpy.ops.bwm.resume()
    else:
        if rt.status == 'PAUSED':
            bpy.ops.bwm.stop()
        bpy.ops.bwm.send()
    sync(context)


def action(aid):
    rt = _runtime
    context = bpy.context
    if rt is None:
        return
    sync(context)
    if aid=='settings':
        ui.STATE.page='main' if ui.STATE.page=='settings' else 'settings';ui.STATE.log_scroll=0
    elif aid=='back':ui.STATE.page='main';ui.STATE.log_scroll=0
    elif aid.startswith('theme:'):context.scene.bwm.ui_theme=aid.split(':',1)[1]
    elif aid.startswith('scale:'):context.scene.bwm.graphite_scale=max(.6,min(2,context.scene.bwm.graphite_scale+(.1 if aid.endswith('+') else -.1)))
    elif aid=='motion':context.scene.bwm.reduce_motion=not context.scene.bwm.reduce_motion
    elif aid=='guides':context.scene.bwm.overlay=not context.scene.bwm.overlay
    elif aid=='compact':context.scene.bwm.compact_panel=not context.scene.bwm.compact_panel
    elif aid=='connection':bpy.ops.bwm.ui_connection('INVOKE_DEFAULT')
    elif aid=='send':
        value=ui.STATE.buffer if ui.STATE.focus else context.scene.bwm.prompt
        ui.STATE.focus=False;send(value)
    elif aid=='stop':
        if rt.status in ACTIVE:bpy.ops.bwm.stop()
    elif aid in ('pin','unpin','skip'):
        if aid=='pin' and not rt.pending:ui.STATE.notice='Start a guide step before pinning a Blender control.'
        else:getattr(bpy.ops.bwm,aid)('INVOKE_DEFAULT' if aid=='pin' else 'EXEC_DEFAULT')
    elif aid.startswith('ref:'):
        target=aid[4:]
        if target=='pause' and ui.STATE.primary_label=='Pause & edit':target='resume'
        if target=='MODIFIER':
            from . import overlays
            if target in overlays._anchors:
                ui.STATE.flash_external=target;ui.STATE.flash_external_until=time.time()+2
            else:ui.STATE.notice='Use Pin pointer to map the actual Blender control first.'
        else:
            ui.STATE.page='details' if target=='pin' else 'main'
            ui.STATE.flash=target;ui.STATE.flash_until=time.time()+2
    elif aid.startswith('tab:'):
        mode = MODES[aid[4:]]
        context.scene.bwm.mode = mode
        context.scene.bwm.compact_panel=mode=='TAKEOVER'
        if rt.status in ACTIVE and rt.mode != mode:
            rt.mode = mode
            rt.pause('Mode changed. Resume when ready.')
    elif aid == 'resume':
        if rt.status == 'READY':
            bpy.ops.bwm.apply_step('EXEC_DEFAULT', True)
        elif rt.status == 'TEACHING':
            bpy.ops.bwm.check()
        elif rt.status == 'PAUSED':
            bpy.ops.bwm.resume()
        elif rt.status in ('THINKING', 'RUNNING'):
            bpy.ops.bwm.pause()
        else:
            send(context.scene.bwm.prompt)
    elif aid == 'pause':
        if rt.status in ('READY', 'TEACHING'):
            bpy.ops.bwm.pause()
        elif rt.status in ('THINKING','RUNNING'):
            bpy.ops.bwm.stop()
        elif rt.status == 'PAUSED':
            bpy.ops.bwm.stop()
    elif aid == 'input':
        ui.STATE.focus=True;ui.STATE.buffer=context.scene.bwm.prompt;ui.STATE.cursor=len(ui.STATE.buffer);ui.STATE.select_all=False
    elif aid.startswith('chip:'):
        name = aid[5:]
        prompt = {'Lamp': 'Build a desk lamp', 'Desk': 'Build a desk', 'Bevel': 'Bevel selected'}[name]
        if rt.status not in ACTIVE:
            context.scene.bwm.prompt = prompt
            send(prompt)
        else:
            bpy.ops.bwm.ui_details('INVOKE_DEFAULT')
    elif aid == 'details':
        ui.STATE.page='details';ui.STATE.log_scroll=0
    sync(context)


def is_open(context):
    st = ui.STATE
    return (st.running and st.visible and context.area is not None and context.window is not None
            and context.area.as_pointer() == st.owner_area
            and context.window.as_pointer() == st.owner_window)


def pointer(x, y, radius=22, icon=False):
    """Use the supplied pencil/radar drawing at v0.2's observed or pinned location."""
    rt = _runtime
    if rt is None:
        return
    now = time.time()
    marker = id(rt.pending)
    if getattr(ui.STATE, 'guide_marker', None) != marker:
        ui.STATE.guide_marker = marker
        ui.STATE.guide_started = now
    context = bpy.context
    ui.set_theme(context.scene.bwm.ui_theme)
    indicator = {'kind': 'radar' if icon else 'circle', 'text': '' if icon else 'Working here' if rt.mode=='TAKEOVER' else '', 'obj': None,
                 'xy': (x, y), 'rect': (x-radius, y-radius, radius*2, radius*2) if not icon else None,
                 't0': ui.STATE.guide_started, 'ttl': 0}
    ui.draw_indicator(indicator, context.region, context.region_data, now)


class BWM_OT_ui_details(bpy.types.Operator):
    bl_idname = 'bwm.ui_details'
    bl_label = 'BlendWMe · Step details'
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=480)
    def draw(self, context):
        import textwrap
        layout = self.layout
        rt = _runtime
        if rt.pending:
            guide = rt.pending['guide']
            for value in (guide.get('instruction', ''), 'Why: ' + guide['why'] if guide.get('why') else ''):
                for line in textwrap.wrap(value, 64):
                    layout.label(text=line)
            if rt.status == 'TEACHING':
                layout.operator('bwm.check', text='Check my step')
                if rt.pending['action']['tool'] == 'create_primitive':
                    layout.operator('bwm.check', text='Use selected · Keep my proportions').use_selected = True
            if rt.status == 'READY':
                layout.operator('bwm.apply_step')
            if rt.mode != 'TAKEOVER' and rt.pending['action']['tool'] != 'create_primitive':
                layout.operator('bwm.skip')
            layout.operator('bwm.pin')
        else:
            for line in textwrap.wrap(context.scene.bwm.status, 64):
                layout.label(text=line)
        if rt.status in ACTIVE:
            layout.operator('bwm.stop')
        layout.label(text='Provider and API settings: N → BlendWMe → Connection & Pointers')
    def execute(self, context):
        return {'FINISHED'}


class BWM_OT_ui_connection(bpy.types.Operator):
    bl_idname='bwm.ui_connection';bl_label='BlendWMe connection'
    def invoke(self,context,event):return context.window_manager.invoke_props_dialog(self,width=450)
    def draw(self,context):
        l=self.layout;s=context.scene.bwm;c=context.window_manager.bwm_connection
        l.prop(s,'provider')
        if s.provider=='NEBIUS':
            l.prop(c,'nebius_model');l.prop(c,'api_key');l.prop(c,'nebius_url');l.operator('bwm.forget_key')
        elif s.provider=='OLLAMA':l.prop(c,'ollama_model');l.prop(c,'ollama_url')
        l.label(text='Connection badge verifies a real provider response.')
        l.label(text='Keys stay in this Blender session.')
    def execute(self,context):return {'FINISHED'}

CLASSES = (ui.BWM_OT_overlay, ui.BWM_OT_prompt, BWM_OT_ui_details,BWM_OT_ui_connection)


def install(runtime):
    global _runtime
    _runtime = runtime
    ui.load_fonts()
    ui.STATE.on_send = send
    ui.STATE.on_action = action
    ui.STATE.on_refresh = sync
    ui.STATE.log.clear()
    ui.STATE.model_line = 'Local Demo · No AI'
    sync(bpy.context)


def reset():
    global _verified_profile, _request_profile
    ui.close_overlay()
    ui.STATE.indicators.clear()
    _verified_profile = _request_profile = None


def remove():
    global _runtime
    reset()
    ui.STATE.on_send = ui.STATE.on_action = ui.STATE.on_refresh = None
    ui.unload_fonts()
    _runtime = None
