"""Honest local recipe provider, with live anchors rather than fixed world positions."""
from .protocol import parse_response

def result(tool, args, message, instruction, why, target):
    return parse_response({"message": message, "done": False, "action": {"tool": tool, "args": args},
        "guide": {"title": message, "instruction": instruction, "why": why, "target": target}})

def create(primitive, name, loc, scale, label):
    return result("create_primitive", {"primitive": primitive, "name": name, "location": loc, "scale": scale}, label,
        f"In Object Mode: Shift+A > Mesh > {primitive.replace('_', ' ').title()}. Open N > Item. Set Location {loc} and Scale {scale}. Then press Check My Step (or select the part and Use Selected).",
        "Primitives start with simple geometry. Object scale changes proportions without destroying the mesh.", "ADD")

def next_demo(payload):
    text = payload["instruction"].lower()
    hist = payload["history"]
    i = len(hist)
    scene = payload["scene"]
    objects = scene["objects"]
    prefix = payload["prefix"]
    def resolve(suffix):
        label = prefix + suffix
        for h in hist:
            if h["action"]["tool"] == "create_primitive" and h["action"]["args"]["name"] == label:
                found = next((o for o in objects if o["id"] == h.get("object_id")), None)
                if found:
                    return found
        return next((o for o in objects if o["name"] == label), None)
    def color(suffix, rgba):
        o = resolve(suffix)
        if not o:
            return {"message": "A required part is missing. Stop and start a new recipe, or restore it with Undo.", "done": True, "action": None}
        return result("set_material", {"name": o["name"], "base_color": rgba, "roughness": .42}, f"Colour {suffix.lower()}",
            f"Select {o['name']}. Properties > Material (red sphere) > New > Base Color. Enter RGBA {rgba}, Roughness 0.42. Check My Step.",
            "A dedicated material prevents changing other objects that share an existing material.", "MATERIAL")
    def bevel(suffix):
        o = resolve(suffix)
        if not o:
            return {"message": "The part to bevel is missing. Restore it or start again.", "done": True, "action": None}
        return result("add_bevel", {"name": o["name"], "width": .04, "segments": 3}, f"Soften {suffix.lower()} edges",
            f"Select {o['name']}. Properties > Modifiers (wrench) > Add Modifier > Bevel. Amount 0.04, Segments 3. Check My Step.",
            "A Bevel modifier is nondestructive: its settings remain editable. Nonuniform object scale affects the visible bevel width.", "MODIFIER")
    if "lamp" in text:
        base = resolve("Base")
        loc = base["location"] if base else [0, 0, .18]
        dims = base.get("dimensions", [2.4, 2.4, .36]) if base else [2.4, 2.4, .36]
        top = loc[2] + dims[2] / 2
        stem = resolve("Stem")
        shade_z = stem["location"][2] + stem["dimensions"][2]/2 if stem else top + 2.5
        steps = [lambda: create("cylinder", prefix+"Base", [0, 0, .18], [1.2, 1.2, .18], "Add a stable lamp base"),
                 lambda: color("Base", [.12, .25, .35, 1]),
                 lambda: bevel("Base"),
                 lambda: create("cylinder", prefix+"Stem", [loc[0], loc[1], top+1.25], [.09, .09, 1.25], "Add the stem above your live base"),
                 lambda: color("Stem", [.15, .16, .18, 1]),
                 lambda: create("cone", prefix+"Shade", [stem['location'][0] if stem else loc[0], stem['location'][1] if stem else loc[1], shade_z+.42], [.85, .85, .55], "Add the shade above your live stem"),
                 lambda: color("Shade", [.95, .55, .18, 1])]
    elif "desk" in text or "table" in text:
        top = resolve("Top")
        loc = top["location"] if top else [0, 0, 2]
        dims = top.get("dimensions", [3.6, 2, .2]) if top else [3.6, 2, .2]
        leg_height = max(.1, loc[2] - dims[2]/2)
        steps = [lambda: create("cube", prefix+"Top", [0, 0, 2], [1.8, 1, .1], "Create the tabletop"), lambda: color("Top", [.38, .19, .085, 1]), lambda: bevel("Top")]
        for n, (sx, sy) in enumerate(((-1,-1),(-1,1),(1,-1),(1,1)), 1):
            steps.append(lambda n=n, sx=sx, sy=sy: create("cube", prefix+f"Leg{n}", [loc[0]+sx*dims[0]*.4, loc[1]+sy*dims[1]*.38, leg_height/2], [.1,.1,leg_height/2], f"Add leg {n} under your live tabletop"))
    elif "snowman" in text:
        steps = [lambda: create("uv_sphere",prefix+"Body",[0,0,1],[1,1,1],"Create the snowman body"),
                 lambda: create("uv_sphere",prefix+"Head",[0,0,2.5],[.6,.6,.6],"Create the head"),
                 lambda: color("Body",[.82,.9,1,1]), lambda: color("Head",[.82,.9,1,1])]
    elif "bevel" in text or "polish" in text or "color" in text or "colour" in text:
        target = next((o for o in objects if o["name"] == scene.get("active_object") and o["type"]=="MESH"), None)
        if not target:
            return {"message":"Select a mesh first, then Send: bevel selected or colour selected.","done":True,"action":None}
        steps = [lambda: result("add_bevel", {"name":target["name"],"width":.04,"segments":3}, "Bevel the selected object", "Properties > Modifiers > Add Modifier > Bevel. Amount 0.04, Segments 3.", "Keep the modifier editable.", "MODIFIER")] if "bevel" in text or "polish" in text else [lambda: result("set_material", {"name":target["name"],"base_color":[.12,.48,.72,1]}, "Colour the selected object", "Properties > Material > New > Base Color: [0.12, 0.48, 0.72, 1].", "Material colour is visible in Material Preview or Solid > Color: Material.", "MATERIAL")]
    elif "cube" in text or "box" in text:
        steps = [lambda: create("cube",prefix+"Cube",[0,0,1],[1,1,1],"Create a cube"),lambda: bevel("Cube"),lambda: color("Cube",[.12,.48,.72,1])]
    else:
        return {"message":"Local demo recipes: lamp, desk/table, snowman, cube, bevel selected, colour selected. This is a recipe engine, not a language model. Select Nebius or Ollama for free-form requests.","done":True,"action":None}
    if i >= len(steps):
        return {"message":"Recipe complete. All parts remain ordinary editable Blender objects. Try moving a part or requesting bevel selected.","done":True,"action":None}
    return steps[i]()

