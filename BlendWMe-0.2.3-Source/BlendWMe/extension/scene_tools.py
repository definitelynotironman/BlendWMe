"""All Blender scene access runs on the main thread."""
import hashlib
import math
import struct
import uuid
import bpy
import bmesh
from .protocol import validate_action

_ids = {}

def identity(obj):
    ptr = obj.as_pointer()
    if ptr not in _ids:
        tag = obj.get("_blendwme_id")
        # Shift+D copies custom properties; a duplicate must still have its own identity.
        _ids[ptr] = tag if tag and tag not in _ids.values() else uuid.uuid4().hex
    return _ids[ptr]

def material_state(obj):
    result=[]
    for slot in obj.material_slots:
        m=slot.material
        if not m:
            continue
        item={"name":m.name,"color":list(m.diffuse_color)}
        bs=m.node_tree.nodes.get("Principled BSDF") if m.use_nodes and m.node_tree else None
        if bs:
            item.update(color=list(bs.inputs['Base Color'].default_value),roughness=bs.inputs['Roughness'].default_value,metallic=bs.inputs['Metallic'].default_value)
        result.append(item)
    return result

def observe(scene):
    objects=[]
    for obj in sorted(scene.objects, key=lambda x:x.name):
        item={"id":identity(obj),"name":obj.name,"type":obj.type,
            "location":[round(v,5) for v in obj.location], "rotation_deg":[round(math.degrees(v),5) for v in obj.rotation_euler],
            "scale":[round(v,5) for v in obj.scale], "dimensions":[round(v,5) for v in obj.dimensions],
            "selected":obj.select_get(),"materials":material_state(obj),
            "modifiers":[{"type":m.type,"name":m.name, **({"width":m.width,"segments":m.segments} if m.type=="BEVEL" else {})} for m in obj.modifiers]}
        if obj.type=="MESH":
            mesh=obj.data
            item['mesh']=[len(mesh.vertices),len(mesh.edges),len(mesh.polygons)]
            # Detect edited vertex positions, including changes that keep bounds/counts unchanged.
            if len(mesh.vertices)<=20000:
                h=hashlib.sha256()
                for v in mesh.vertices:
                    h.update(struct.pack('fff',*v.co))
                item['mesh_hash']=h.hexdigest()[:16]
            else:
                item['mesh_hash']='large-mesh-counts-only'
        objects.append(item)
    active=bpy.context.view_layer.objects.active
    return {"objects":objects,"object_count":len(objects),"active_object":active.name if active else None,
            "observation":"structured scene data, no viewport image"}

def get(scene, name):
    obj=scene.objects.get(name)
    if not obj:
        raise ValueError(f"Object '{name}' no longer exists in this scene")
    if obj.library or obj.override_library:
        raise ValueError("Linked/overridden objects are outside this prototype's tool scope")
    return obj

def execute(scene, action):
    action=validate_action(action)
    t,a=action['tool'],action['args']
    if bpy.context.mode!='OBJECT':
        raise ValueError("Return to Object Mode before automation")
    if t=='create_primitive':
        if bpy.data.objects.get(a['name']):
            raise ValueError("That object name already exists. Replan with a unique name.")
        bm=bmesh.new()
        try:
            p=a['primitive']
            if p=='cube': bmesh.ops.create_cube(bm,size=2)
            elif p=='plane': bmesh.ops.create_grid(bm,x_segments=1,y_segments=1,size=1)
            elif p=='uv_sphere': bmesh.ops.create_uvsphere(bm,u_segments=32,v_segments=16,radius=1)
            elif p in ('cylinder','cone'):
                bmesh.ops.create_cone(bm,cap_ends=True,cap_tris=False,segments=32,radius1=1,radius2=1 if p=='cylinder' else 0,depth=2)
            else:
                # Repo-native torus: 32x12 quads, no context-sensitive mesh operator.
                verts=[]
                for j in range(32):
                    ring=[]
                    for k in range(12):
                        u,v=2*math.pi*j/32,2*math.pi*k/12
                        ring.append(bm.verts.new(((1+.25*math.cos(v))*math.cos(u),(1+.25*math.cos(v))*math.sin(u),.25*math.sin(v))))
                    verts.append(ring)
                for j in range(32):
                    for k in range(12):
                        bm.faces.new((verts[j][k],verts[(j+1)%32][k],verts[(j+1)%32][(k+1)%12],verts[j][(k+1)%12]))
            mesh=bpy.data.meshes.new(a['name']+'Mesh')
            bm.to_mesh(mesh)
        finally:
            bm.free()
        obj=bpy.data.objects.new(a['name'],mesh)
        coll=bpy.data.collections.get('BlendWMe Builds')
        if coll is None:
            coll=bpy.data.collections.new('BlendWMe Builds')
        if coll.name not in scene.collection.children:
            scene.collection.children.link(coll)
        coll.objects.link(obj)
        obj['_blendwme_id']=uuid.uuid4().hex
        for key in ('location','scale'):
            if key in a: setattr(obj,key,a[key])
        obj.rotation_euler=[math.radians(v) for v in a.get('rotation_deg',[0,0,0])]
        for o in bpy.context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active=obj
    else:
        obj=get(scene,a['name'])
        if t=='set_transform':
            for key in ('location','scale'):
                if key in a: setattr(obj,key,a[key])
            if 'rotation_deg' in a: obj.rotation_euler=[math.radians(v) for v in a['rotation_deg']]
        elif t=='set_material':
            if obj.type!='MESH': raise ValueError('Materials require a mesh')
            # Always allocate an independent material; shared user materials remain untouched.
            mat=bpy.data.materials.new('BlendWMe '+obj.name)
            mat.use_nodes=True
            mat.diffuse_color=a['base_color']
            bs=mat.node_tree.nodes.get('Principled BSDF')
            bs.inputs['Base Color'].default_value=a['base_color']
            bs.inputs['Roughness'].default_value=a.get('roughness',.5)
            bs.inputs['Metallic'].default_value=a.get('metallic',0)
            if obj.data.users>1: obj.data=obj.data.copy()
            if obj.data.materials: obj.data.materials[0]=mat
            else: obj.data.materials.append(mat)
            obj.color=a['base_color']
        elif t=='add_bevel':
            if obj.type!='MESH': raise ValueError('Bevel requires a mesh')
            mod=obj.modifiers.get('BlendWMe Bevel')
            if mod and mod.type!='BEVEL': raise ValueError('Modifier name conflict')
            mod=mod or obj.modifiers.new('BlendWMe Bevel','BEVEL')
            mod.width=a['width'];mod.segments=a['segments']
    bpy.context.view_layer.update()
    return obj

def close(actual, wanted, tolerance=.025):
    return len(actual)==len(wanted) and all(abs(x-y)<=tolerance for x,y in zip(actual,wanted))

def primitive_matches(obj,p):
    if obj.type!='MESH': return False
    v,f=len(obj.data.vertices),len(obj.data.polygons)
    if p=='cube': return v==8 and f==6
    if p=='plane': return v==4 and f==1
    if p=='cone': return 12<=v<=128 and v%2==1
    if p=='cylinder': return 24<=v<=256 and v%2==0 and any(len(face.vertices)>4 for face in obj.data.polygons)
    if p=='uv_sphere': return v>100 and any(len(face.vertices)==3 for face in obj.data.polygons)
    if p=='torus': return v>100 and all(len(face.vertices)==4 for face in obj.data.polygons)
    return False

def verify(scene, action, baseline, aliases, selected=None):
    """Observe human work; never rename, tag or transform a user object in Teach Me."""
    a=action['args'];t=action['tool']
    if t=='create_primitive':
        old={o['id'] for o in baseline['objects']}
        candidates=[selected] if selected else [o for o in scene.objects if identity(o) not in old]
        for obj in candidates:
            if not obj or not primitive_matches(obj,a['primitive']): continue
            if selected or (close(obj.location,a.get('location',[0,0,0])) and close(obj.scale,a.get('scale',[1,1,1]))):
                return obj, 'Human-created part accepted' if selected else 'Primitive and transforms verified'
        raise ValueError('No matching new primitive yet. Follow the values, or select your matching primitive and Use Selected to keep your proportions.')
    ident=aliases.get(a['name'])
    obj=next((o for o in scene.objects if identity(o)==ident),None) if ident else scene.objects.get(a['name'])
    if not obj: raise ValueError('Select/create the expected part first')
    if t=='set_transform':
        for key in ('location','scale','rotation_deg'):
            if key in a and not close([math.degrees(v) for v in obj.rotation_euler] if key=='rotation_deg' else getattr(obj,key),a[key]):
                raise ValueError(f'{key} does not yet match the requested values')
    elif t=='add_bevel':
        if not any(m.type=='BEVEL' and abs(m.width-a['width'])<.005 and m.segments==a['segments'] for m in obj.modifiers):
            raise ValueError('Add a Bevel modifier with the requested Amount and Segments')
    elif t=='set_material':
        if not any(close(m['color'],a['base_color']) and all(abs(m.get(k,.5 if k=='roughness' else 0)-a[k])<.025 for k in ('roughness','metallic') if k in a) for m in material_state(obj)):
            raise ValueError('Material colour or roughness/metallic does not yet match')
    return obj,'Scene change verified'
