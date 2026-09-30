"""The tool list the model sees. Few tools, each powerful (the MCP lesson)."""


def _fn(name, description, properties, required=()):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": list(required)}}}


GET_SCENE_INFO = _fn(
    "get_scene_info",
    "List every object with type, location, rotation, scale, size, world bounds, materials, "
    "modifiers and who made it. Use it to check facts (sizes, positions, contact).", {})

GET_OBJECT_INFO = _fn(
    "get_object_info",
    "Detailed facts about one object: mesh counts, modifiers with settings, materials with "
    "colours, shape keys, bones, children.",
    {"name": {"type": "string"}}, ["name"])

RUN_PYTHON = _fn(
    "run_python",
    "Run Blender Python on the live scene to complete ONE meaningful stage. An undo step is "
    "saved first. Returns printed output and a list of scene changes. If it fails you get the line, "
    "the error and a hint, and everything the step created is removed, so fix it and run the whole step again.",
    {"title": {"type": "string", "description": "Short stage title, 2-6 words, naming the plan part it builds"},
     "explain": {"type": "string", "description": "What this step does and why, 1-2 short sentences. In Watch mode include the shortcut or menu a person would use."},
     "code": {"type": "string", "description": "Python code to run"},
     "part": {"type": "integer", "description": "Number of the plan part this step builds (1 = first)"}},
    ["title", "explain", "code"])

PLAN = _fn(
    "plan",
    "Write the build plan BEFORE building: the parts of THE USER'S SUBJECT in build order, each with its "
    "size and where it attaches. The user sees it as a checklist that ticks off as you build. Call again to revise it. "
    "Settle every part before finish: run_python with part=<n> ticks it; done=[n] ticks parts you checked with a "
    "look after building them; skip=[{part, reason}] leaves a part out and tells the user why.",
    {"parts": {"type": "array", "items": {"type": "string"},
               "description": "One string per part: what it is, size in metres, what it rests on. "
                              "Format only: '<part> <w x d x h> m on <other part or the ground>'. "
                              "Leave out to only mark parts."},
     "done": {"type": "array", "items": {"type": "integer"},
              "description": "Part numbers that are built and that you checked with look after building them"},
     "skip": {"type": "array", "items": {"type": "object", "properties": {
         "part": {"type": "integer"}, "reason": {"type": "string"}}},
              "description": "Parts left out on purpose, each with a short reason the user will see"}},
    [])

LOOK = _fn(
    "look",
    "See the scene. mode 'angles' frames the target from several sides (best for judging form). "
    "'viewport' is what the user sees. 'camera' is through the scene camera.",
    {"question": {"type": "string", "description": "What you want judged, e.g. 'Do all parts touch where they should?'"},
     "mode": {"type": "string", "enum": ["angles", "viewport", "camera"]},
     "targets": {"type": "array", "items": {"type": "string"}, "description": "Object or collection names to frame (angles mode). Default: everything visible except a big ground plane."},
     "views": {"type": "array", "items": {"type": "string", "enum": ["front", "back", "left", "right", "top", "three_quarter"]}},
     "shading": {"type": "string", "enum": ["solid", "material", "wireframe"]},
     "compare_reference": {"type": "boolean", "description": "Also show the user's reference images"}},
    ["question"])

INSPECT_API = _fn(
    "inspect_api",
    "Look up the real Blender API in this Blender version: properties of a type "
    "(e.g. 'BevelModifier', 'Material'), arguments of an operator (e.g. 'bpy.ops.mesh.loopcut_slide'), "
    "the bwm kit ('bwm' or 'bwm.gable_roof'), bmesh ('bmesh', 'bmesh.ops', 'bmesh.ops.create_cone', "
    "'bmesh.types.BMVert') and mathutils ('mathutils.Vector', 'mathutils.geometry').",
    {"path": {"type": "string"}}, ["path"])

LOAD_SKILL = _fn(
    "load_skill",
    "Load a skill card: a tested workflow with steps, good settings and common mistakes.",
    {"name": {"type": "string"}}, ["name"])

POINT_AT = _fn(
    "point_at",
    "Draw BlendWMe's red pointer in the user's Blender to show where to look. Point at an "
    "object, a 3D location, or a part of the interface.",
    {"object": {"type": "string"},
     "location": {"type": "array", "items": {"type": "number"}, "description": "[x, y, z] world position"},
     "editor": {"type": "string", "enum": ["VIEW_3D", "PROPERTIES", "OUTLINER", "NODE_EDITOR", "DOPESHEET_EDITOR", "GRAPH_EDITOR", "TIMELINE", "IMAGE_EDITOR", "SEQUENCE_EDITOR", "TEXT_EDITOR"]},
     "region": {"type": "string", "enum": ["WINDOW", "HEADER", "TOOLS", "UI", "NAVIGATION_BAR"], "description": "Part of the editor: TOOLS is the left toolbar, UI is the N sidebar, HEADER is the top bar, NAVIGATION_BAR is the Properties editor tab column (modifiers, materials...)"},
     "tab": {"type": "string", "enum": ["TOOL", "RENDER", "OUTPUT", "VIEW_LAYER", "SCENE", "WORLD", "COLLECTION", "OBJECT", "MODIFIER", "PARTICLES", "PHYSICS", "CONSTRAINT", "DATA", "MATERIAL", "TEXTURE"], "description": "With editor PROPERTIES: the exact tab to ring (e.g. MODIFIER for the wrench)"},
     "label": {"type": "string", "description": "A few words shown next to the pointer"}})

TEACH_STEP = _fn(
    "teach_step",
    "Show the user ONE action to do themselves, then wait until it is done. Returns how it went "
    "('completed', 'user_said_done', 'show_me' or 'skipped') and what changed in the scene.",
    {"title": {"type": "string", "description": "2-6 words"},
     "instruction": {"type": "string", "description": "ONE action with its shortcut or menu and values. Under 20 words."},
     "why": {"type": "string", "description": "Everything else: why artists do this, extra detail. Shown smaller."},
     "check": {"type": "string", "description": "Python expression that becomes True when the step is done. Names: bpy, C, D. Empty if it cannot be checked."},
     "point_object": {"type": "string"},
     "point_editor": {"type": "string"},
     "point_region": {"type": "string"},
     "point_tab": {"type": "string", "description": "With point_editor PROPERTIES: the tab to ring, e.g. MODIFIER"}},
    ["title", "instruction"])

ASK_USER = _fn(
    "ask_user",
    "Ask the user a short question and wait for the answer. Use only when a wrong guess would waste real work.",
    {"question": {"type": "string"}}, ["question"])

FINISH = _fn(
    "finish",
    "End the task with a short summary of what was made and one useful next idea. Refused while plan parts are "
    "open (build, mark done after a look, or skip them with plan), or when the summary claims a render that "
    "does not exist.",
    {"summary": {"type": "string"}}, ["summary"])

_COMMON = [PLAN, GET_SCENE_INFO, GET_OBJECT_INFO, LOOK, INSPECT_API, LOAD_SKILL, ASK_USER, FINISH]


def for_mode(mode):
    if mode == "TEACH":
        return _COMMON + [TEACH_STEP, POINT_AT, RUN_PYTHON]
    return _COMMON + [RUN_PYTHON]


ALL = _COMMON + [RUN_PYTHON, TEACH_STEP, POINT_AT]


# ----------------------------------------------------------------------
# Tools only the outside brains (Claude Code, Codex) get, through the MCP server.
# The project journal ("the baton") lets one brain hand the work to the other.
# ----------------------------------------------------------------------

HANDOFF_NOTE = _fn(
    "handoff_note",
    "Write a note for the next brain (or for yourself later): what you did, what is left, and what you "
    "struggled with. BlendWMe saves it in the project journal. Write one when you are asked to hand over, "
    "and whenever you stop with work left.",
    {"summary": {"type": "string", "description": "What you built or changed, 1-4 short sentences"},
     "next_steps": {"type": "array", "items": {"type": "string"}, "description": "What should happen next, in order"},
     "struggles": {"type": "array", "items": {"type": "string"},
                   "description": "Problems you could not solve (errors, parts that look wrong)"}},
    ["summary"])

READ_JOURNAL = _fn(
    "read_journal",
    "Read the project journal: the brief and the user's decisions, the plan, every step (signed by the brain "
    "that made it), who made each object, open struggles, the latest art director review and handoff notes.",
    {"section": {"type": "string", "enum": ["brief", "everything", "steps", "objects", "struggles", "handoffs"],
                 "description": "Default 'brief': the compact handoff brief"}})

BRAIN_ONLY = [HANDOFF_NOTE, READ_JOURNAL]

# What the outside brains are told about look: they see the picture themselves.
_MCP_DESCRIPTIONS = {
    "look": ("See the scene: returns the picture itself. mode 'angles' frames the target from several sides "
             "in one contact sheet (best for judging form). 'viewport' is what the user sees. 'camera' is "
             "through the scene camera. compare_reference adds the user's reference images."),
}

# Tools that only read. MCP clients may use this hint to skip approval prompts.
READ_ONLY = {"get_scene_info", "get_object_info", "inspect_api", "load_skill", "look", "read_journal"}


def mcp_tools():
    """Every tool in MCP form: [{"name", "description", "inputSchema", "readOnly"}]."""
    out = []
    for tool in ALL + BRAIN_ONLY:
        fn = tool["function"]
        out.append({"name": fn["name"],
                    "description": _MCP_DESCRIPTIONS.get(fn["name"], fn["description"]),
                    "inputSchema": fn["parameters"],
                    "readOnly": fn["name"] in READ_ONLY})
    return out


def names_for_mode(mode):
    """Tool names a brain may use in this mode (the MCP server lists them all)."""
    return {t["function"]["name"] for t in for_mode(mode)} | {t["function"]["name"] for t in BRAIN_ONLY}
