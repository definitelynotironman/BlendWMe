# BlendWMe: Nebius / Nemotron project notes

Read the whole "Vision" section before changing anything. It is the point of the project.
The reliability work further down is the floor we stand on, not the goal.

---

## 1. The vision (do not shrink it)

### In the owner's words
>
> We want an in-app extension that makes 3D modelling AI assisted. We want this done in all the
> right ways: visually accurate and artistic auto-completion of requests, deep understanding of the
> Blender app with the ability to provide in-app tutorials and guidance from a beginner (teaching
> them the simple, fundamental and semi-pro workflow techniques, giving them good intuition) to a
> more advanced user (showing them a super advanced technique that will help their flow), and
> finally the intertwining of the two: a comfortable Blender user can let go of the steering wheel
> for assistance and pop back in for their own adjustment.
>
> An AI prompt within the app is nothing new. It can drop rectangles, circles and squares into
> Blender, we know that. It's 2026. AI can do so much more than that. We need to aim higher.
>
> We want a person inside Blender, someone who is practically an advanced Blender user. That is
> what we want BlendWMe to be. Someone who can model a ridiculously good 3D character, scene,
> animation, sculpt, everything! And we want that to be controlled by the user to open doors to an
> easier workflow. that is BlendWMe

### What that means
BlendWMe behaves like an **experienced Blender artist working beside you, inside your live
project**. It understands your intention, chooses suitable techniques, produces convincing work,
inspects the result, explains its decisions, and responds intelligently to your adjustments.

It is **not** "MCP for Blender on Nemotron". Matching what Claude + MCP-for-Blender can build from
one prompt is the **minimum** bar for Auto mode. BlendWMe adds what MCP has none of: teaching,
working together, art direction, continuity with the user's edits, and a home inside Blender.

### The three modes: one collaborator, three ways of working
| Mode | Intended experience |
|---|---|
| **Teach** | It understands your level, demonstrates techniques, points to the actual controls and recognises your progress. Beginners get intuition. Experienced users get useful techniques without excessive instruction. |
| **Work together** (Watch / Piece by Piece in the current UI) | You and the AI develop the same artwork. You delegate selected tasks, interrupt, reshape the result and continue together. Your edits become part of its understanding. |
| **Auto** | It carries a creative request through planning, construction, inspection and refinement. You can stop it and take control at any time. |

The test of the idea, in one example:
- "Make this character's expression more mischievous" should produce an appropriate **artistic** change.
- "Teach me how you achieved that expression" should turn the **same process** into guidance.
- Pausing to reshape the mouth yourself should let the AI **continue from your version**.

### The five capabilities we must build (all connected)
1. **Perception**: viewport images, references, and exact Blender data, including geometry,
   modifiers, materials and animation state.
2. **Broad execution**: mesh editing, procedural geometry (geometry nodes), sculpting workflows,
   node materials, rigging, animation and Grease Pencil. A small set of operations cannot express
   that breadth.
3. **Artistic judgment**: review proportions, silhouette, composition, lighting, movement and
   fidelity to the user's intention, alongside technical checks.
4. **Teaching**: translate the actual workflow into concise guidance, demonstrations and the right
   UI pointers, at the user's level.
5. **Continuity**: remember the task, approved decisions and the user's changes, then resume
   without undoing their creative contribution.

### Solving the hard part: knowledge is not skill
Access to Blender's documentation does not automatically give an AI an expert artist's judgment or
execution ability. **That is true, so we solve it. We do not use it as an excuse to aim lower.**
Art tutorials, Blender documentation, animation principles and art-perception knowledge exist.
Our job is to turn them into **working, tested Blender workflows** the model can execute and teach:

- **Technique cards** (skills/): each is a workflow distilled from tutorials and docs: when to use
  it, steps as working code, good settings, what "good" looks like, common mistakes, how to teach it,
  which UI controls to point at. **A card is only kept after its code runs cleanly in Blender 4.2.**
- **Tavily research** feeds new cards and answers niche questions. Retrieving a tutorial is only
  part of the work. The technique must become a usable, tested Blender workflow.
- **A forgiving, high-level modelling kit** (`bwm`) so the model expresses artistic intent
  ("rounded box", "tapered limb", "gable roof", "lofted body from profiles") instead of fighting
  low-level API details.
- **A critic loop** (art director): multi-angle captures judged against the brief, the reference
  and an art rubric (silhouette, proportions, big/medium/small shapes, value contrast, colour,
  composition; for animation, the 12 principles). Plus hard measurements where possible
  (bounds, contact, gaps, OpenCV silhouette comparison against a reference).
- **References first**, like real artists: front/side references become a structured spec and
  blockout guides before any detail.
- **Quality standard**: an AI declaring "done" is never our quality standard. Done means the brief
  is met, the critic agrees, and the measurable checks pass.

### Product scope
Characters, scenes, sculpting, animation and artistic assistance. We build it in stages, but the
architecture and our standards always aim at the full collaborator.

### Identity and design
- Lives inside Blender (3D Viewport > N sidebar > BlendWMe). No outside chat app needed.
- **Graphite Paper** design (chosen template F): white and light-grey paper, graphite pencil lines,
  hard shadow, big readable writing. **Red is only for indicators**: blinking red blocks for
  instructions and radar-style radiating red rings around what the AI is talking about.
- YOU and BLENDWME are visually distinct in the conversation log.
- Built for the **Nebius x NVIDIA Global AI Hackathon** (Apps track). Rule: built and run on Nebius
  Token Factory with at least one NVIDIA open model. **Applications close 30 October 2026.**
- A version will also be adapted for the **OpenCV hackathon** (the silhouette/reference measurement
  work is the bridge).

---

## 2. Acceptance tests

### Stage 0: the reliability floor (must pass first)
Each must finish with **zero pauses** on Nemotron 3 Ultra, in Auto:
1. "A cosy low poly cottage with a red tiled roof, a chimney, wooden door, four windows, a stone base and a small path. Stylised, warm colours."
2. "Model me a modern room, I want two walls only, the other side should be open for the viewer to see. In it i want a bed, a pc setup and a closet."
3. "Model me a simple ice cream cone."
4. The cottage again **with a reference image**: the result must clearly follow the reference.

### Stage 1: the real acceptance test (the goal for the hackathon demo)
An **editable character built from references the owner chooses**: convincing forms, meaningful
detail, materials and a pose. Then:
- The owner alters it themselves (for example reshapes the mouth), and the AI **continues coherently**
  from his version.
- He asks it to **teach the techniques involved**, and it does, at his level, with pointers.
- **Repeat with a different brief** to prove the capability transfers, not just one memorised case.

### Stage 2 and beyond
Scenes with lighting and camera, simple animation (bouncing ball, walk cycle), sculpt preparation
and sculpt teaching, Grease Pencil, geometry nodes.

---

## 3. Where we are (1.0.5, 17 October 2026)

### Status in one paragraph
Phase A (the reliability floor) is built, tested headless and committed as **1.0.5**.
Phase B has **not** been started. See "Open problems" below.

### Folder layout (important)
- The real project is `BlendWMe-Source/` (renamed from `BlendWMe-1.0.4-Source` on 17 October 2026;
  the name has no version on purpose, so it never goes out of date):
  - `blendwme/`: the add-on code (zip its contents to install)
  - `tests/test_agent_headless.py` (192 checks), `tests/test_llm.py`
  - `tests/references/cottage.png`: the reference image for Stage 0 test 4, rendered by
    `tests/references/make_reference.py`. Yellow walls, 6 x 3.4 m body, steep red roof,
    chimney on the LEFT, green door off-centre left, 3 front windows plus 1 on the right side,
    a path curving right.
  - `tools/live_eval.py`: real runs against Nebius; `tools/package.py`: builds the zip
  - `tools/live_runs/` (gitignored): per-run folder (log, result.png, result.blend,
    summary.json) and `results.jsonl` (one line per run)
  - `dist/blendwme-1.0.5.zip` (gitignored; rebuild with `python tools/package.py`;
    it passes `blender --command extension validate`). Zips are shared as GitHub Releases,
    never committed.
- The old `blendwme-1.0.4/` copy was removed; 1.0.4 is in git history.
- `README.md` at the root and `BlendWMe-Source/README.md` are kept identical.
- `AGENTS.md` (a Codex copy of this guide) and `CLAUDE.md` are both gitignored and never committed.
- `local_test/`, `preTests/`, `design_process/`: the owner's own experiments. Leave them alone.
- Blender: `C:\Program Files\Blender Foundation\Blender 4.2\blender.exe` (4.2.3 LTS).

### Architecture (already in place)
MCP pattern inside Blender: native tool calling on Nebius, one continuous conversation,
`run_python` on the live scene with an undo step and rollback of failed steps, `look` with
multi-angle captures and a vision model, scene snapshots that report the user's edits,
skill cards, Auto / Watch / Teach, Piece by Piece / Full Build with a plan checklist,
pause-and-continue, Graphite Paper overlay, safe API-key storage, full logs.

### Code map (blendwme/)
| File | Job |
|---|---|
| agent.py | The loop: notice user edits, ask the model, run tools, Watch/Teach waits, rescue of Python sent as tool calls, logs |
| llm.py | OpenAI-compatible client for Nebius, vision model discovery |
| tools.py | Tool schemas the model sees |
| prompts.py | System prompt, mode rules, build styles |
| executor.py | run_python on the live scene: blocklist, pre-check, undo step, rollback of failed steps, hints |
| repair.py | (1.0.5) Repairs mechanical API slips before a step runs; keeps line numbers; forgiving `_bwm_set` for property sets |
| precheck.py | Rejects only what cannot be repaired (checks `from bwm import` names too) |
| helpers.py | The `bwm` helper API available inside run_python (`import bwm` works during a step) |
| shapes.py, furniture.py | (1.0.5) The forgiving bwm kit: shapes, modifiers, placement, architecture, furniture |
| coerce.py | Turns `bwm.mesh(...)` style tool calls into one Python step |
| look.py | Viewport / camera / multi-angle captures (Workbench render in background Blender; true material colours) |
| scene_info.py | Scene summaries, snapshots, user-edit diffs, AI-object inventory for reviews, default-cube flag |
| knowledge.py + skills/ | Skill cards and inspect_api |
| overlay.py, ui.py, prefs.py, state.py, bridge.py, credentials.py, logs.py | UI, settings, threading, key storage, logs |

### Models (tested on the owner's Nebius account)
- Agent: **Nemotron 3 Ultra** (`nvidia/Nemotron-3-Ultra-550b-a55b`), NVIDIA's flagship. Fastest and
  most complete in a one-shot cottage bake-off, but makes small Blender API slips.
- Nemotron 3 Super: works, slower and weaker. Nemotron 3.5 Lightning: ran out of tokens.
- No Nemotron model on Nebius accepts images. Vision uses Kimi K2.6 / Qwen3.8-27B / GLM-5.3-Flash,
  found automatically from the `/models?verbose=true` modality field. Thinking must be off for
  vision calls (otherwise Kimi can return empty answers).

### What 1.0.5 added (Phase A, one commit per fix)
- **Test suite fixes:** stdout is line-buffered (results show in 4.2.3); test logs go to a temp
  folder, not the user's log folder; look renders with Workbench when there is no viewport;
  no borrowed viewport in background mode (it crashed transform macros).
- **A1 `import bwm`:** works only during a step (registered in sys.modules, then removed).
- **A2 repair.py:** 3 -> 4 colour values, swapped `modifiers.new` args, renamed Principled
  inputs, `scene.objects.link`, `bpy.active_object`, `create_cone(radius=)`, `obj.select = True`,
  2.7x properties dropped with a note, `D.*` shorthands. The model is told what was fixed and
  the step counts as a success.
- **A3 bwm kit:** one positional order for everything: `name, sizes, location, collection,
  material`, the rest by keyword. Forgiving aliases (`color=`, `position=`, `height=`), colours
  as 3/4 values, 0-255, '#hex' or names; unknown extras ignored with a note. `claim_name`
  replaces the AI's own earlier object of that name (never the user's) and restores it if the
  step fails. Furniture takes `against='Wall'`, `on='Desk'`, `at='Desk'`. Exact bounds.
  `inspect_api('bwm.<name>')` shows help.
- **A4 example cards:** `example-cottage`, `example-room`, `example-ice-cream-cone`. The suite
  runs every step of every card. Examples are kept neutral (not copies of the test briefs).
- **A5 art director:** references become a written spec before building; the harness reviews
  after build step 1 (with a reference) or 3 (without), then every 4 steps, at most 3 times
  (REVIEW_MAX), plus one final check the first time the model calls finish. Reviews restate the
  brief and list every AI-made object with its size (catches leftovers and wrong scale).
  Wrap-up note when every plan part is built or after 20 build steps (WRAP_UP_STEPS); after the
  wrap-up, `look` points at finish instead of giving a new critique.
- **A6 stuck guard:** failures counted per step (STUCK_FAILS = 4); pre-check rejections
  separately (STUCK_PRECHECK = 6); "test/probe" steps are not run and not counted, and the model
  is told everything is ready.
- **A7 inspect_api:** bmesh, bmesh.ops.X, bmesh.types.*, mathutils.
- **Live fixes found in real runs:** Auto sends `tool_choice="required"` (falls back if the
  provider refuses); tool calls written as text are recovered (tags, bare JSON, XML, YAML,
  FUNCTION chunks, python fences, MCP-style `execute_blender_code`) and history is cleaned up;
  plan accepts `steps=`; AI tag by object identity; stale wall matrices refreshed before
  placement; best-effort hiding; Blender's untouched default cube is flagged; cut-off
  repetition loops are trimmed; two nudges when the model answers with no tool call.
- Version is 1.0.5 in blender_manifest.toml, agent.py log info and the ui.py panel line.

### Open problems (what is still weak)
1. **Polishing instead of finishing.** The last fix (look after wrap-up points at finish) is
   tested headless but **not verified live**.
2. Quality varies: leftovers from abandoned attempts, a misplaced closet. Sometimes the model
   deletes every object in the scene to start over.
3. The `auto_repairs` counter in live_eval summaries is unreliable.
4. Design decisions waiting for the owner (not implemented, ask before doing):
   - should the 60-step check-in count only build steps?
   - should Auto finish by itself after a hard limit?
   - should the AI be blocked from deleting the user's objects?

### History: diagnosis from the 1.0.4 live logs (Nemotron 3 Ultra, reasoning ON)
Every prompt (cottage, modern room, ice cream cone) got stuck, each with a different error.
The pattern: Nemotron writes plausible but wrong low-level Blender Python, and BlendWMe
**rejects** it instead of **repairing** it. Evidence:
1. `import bwm` -> ModuleNotFoundError. bwm is only a variable, not a module.
2. Raw bmesh mistakes: `bm.ops`, `bm.transform(location=...)`, `bm.to_object`, `bm.cylinder`,
   `bmesh.ops.create_cone(radius=...)`.
3. Mechanical slips: `material.diffuse_color` with 3 values, `modifiers.new('SOLIDIFY', 'Solidify')`
   (swapped), wrong modifier type, `bpy_prop_collection.link`, `world.light_settings.use_ambient_occlusion`.
4. The model wastes turns on "Test bwm availability" style steps, then the 4-failure guard pauses it.
5. `inspect_api('bmesh')` and `inspect_api('bmesh.ops')` fail, so it cannot look bmesh up.
6. Vision only runs when the model calls look. With a reference image it got stuck before ever
   looking, so the result was nowhere near the reference.
7. Earlier (1.0.2): 71% of tool calls were invented tools like `bwm.mesh`. 1.0.3 rescues these.

**Core insight:** Claude succeeds with MCP because it writes correct bpy. Nemotron does not.
BlendWMe must not depend on the model writing correct low-level bpy. Make the correct path easy
and forgiving, repair mechanical mistakes automatically, and let the harness (not the model)
drive vision and art direction.

---

## 4. Plan

### Phase A: reliability floor (fixes, in order). BUILT in 1.0.5
Next step for Phase A: settle the open design decisions in section 3, then a few more live
runs (ask for a run budget first) until each Stage 0 prompt passes.
Do not start Phase B until the owner says so.
1. **Allow `import bwm` / `from bwm import box`.** Register the helpers module as `bwm` in
   sys.modules during each run, or strip those imports before exec.
2. **Auto-repair instead of reject.** Rewrite known mechanical mistakes, run the fixed code, and
   tell the model what changed (count it as success): diffuse_color 3->4 values, swapped
   modifiers.new args, create_cone radius -> radius1/radius2, renamed Principled inputs,
   scene.objects.link -> scene.collection.objects.link, bpy.active_object -> bpy.context.active_object,
   removed 2.7x properties (drop the line + note). Keep the pre-check only for what cannot be
   repaired safely.
3. **Richer, forgiving `bwm` kit** so the model rarely needs bmesh or bpy.ops:
   cylinder, cone (radius_bottom/radius_top), sphere, torus, wedge/prism, gable_roof,
   extrude_profile (2D outline -> solid), loft (profiles -> smooth body), tube along points,
   array, mirror, bevel, place_on(obj, target, side="top"), align; plus an architecture and
   furniture kit (wall, floor, door/window cutouts, shelf, table, bed, chair) built from those.
   Every argument tolerant: colours with 3 or 4 values, numbers or strings, lists or tuples.
   Prompt rule: "Build with bwm. Use bmesh or bpy.ops only for things bwm cannot do."
4. **Few-shot examples.** Open models copy examples better than they follow rules. Add complete,
   tested builds as skill cards: cottage, room interior with furniture, ice cream cone.
5. **Harness-driven vision.** At task start, references become a structured spec (parts,
   proportions, colours, style) added to the brief. After the first successful build stage and
   every few steps after, the harness captures angles and adds the critique itself.
6. **Smarter stuck guard.** Do not count auto-repaired or pre-check-rejected steps as failures.
   Forbid "test" steps. Count failures per step.
7. **inspect_api for bmesh, bmesh.ops.X, bmesh.types.\*, mathutils.**
8. **Live evaluation script** `tools/live_eval.py`: runs a prompt in background Blender against
   real Nebius (key from NEBIUS_API_KEY), saves the log, prints steps, failures, pauses, objects,
   tokens. Iterate until all Stage 0 prompts pass.

### Phase B: toward the collaborator (Stage 1 character test)
1. **Character pipeline**: reference intake (front/side) -> structured spec -> blockout with loft /
   tapered tubes / mirror -> refine (subdivision, shape keys for expressions) -> materials ->
   simple Rigify or custom armature -> pose. Each stage is a tested technique card.
2. **Critic loop with a rubric**: silhouette, proportions vs reference, shape language, value
   contrast, pose readability. OpenCV silhouette overlap against the front reference where possible.
3. **Continuity**: decisions log (what the user approved or changed) carried in the conversation;
   user-made objects and edits are protected and built around.
4. **Teaching from the same process**: the construction notebook (every step and its code) can be
   replayed as Watch steps or Teach steps with pointers at the real controls.
5. **Tavily -> tested cards**: a small pipeline that drafts a technique card from a tutorial
   search, runs its code in background Blender, and keeps it only if it works.

### Phase C: breadth
Animation (keyframes, easing, principles), sculpt preparation + sculpt teaching, Grease Pencil,
geometry nodes, lighting/camera/render polish for the demo video.

---

## 5. How to test
Run these from `BlendWMe-Source/`.
- Headless suite (fake model server, real Blender), expect "ALL 192 CHECKS PASSED":
  `"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe" -b --factory-startup --python-exit-code 1 --python tests/test_agent_headless.py`
- Client helpers: `python tests/test_llm.py`
- Live run (costs Nebius credits, ask about the budget first; key only from the
  `NEBIUS_API_KEY` env var, never print or log it):
  `blender -b --factory-startup --python tools/live_eval.py -- --task cottage`
  (tasks: cottage, room, cone, cottage_ref; or `--prompt "..."`; `--minutes N`, default 25).
  It presses Resume and answers questions by itself, and counts each time.
- Live logs from real runs in Blender: the BlendWMe Logs folder in your Documents (`*.md` and `*.json`)
- Package: `python tools/package.py`, which zips the CONTENTS of blendwme/ (manifest at the zip root) as `dist/blendwme-<version>.zip`.
  Keep `schema_version = "1.0.0"` in blender_manifest.toml. Only bump `version`.
- Install: Blender > Preferences > Add-ons > Install from Disk. Disable older versions first.

---

## 6. Working rules
- **Do not let the prototype's current limits define the product.** If something is hard, find a
  staged way to get there. Challenge assumptions honestly, but keep the ambition.
- Never claim something works live without a real run. Say what was tested and how.
- Readable, commented code. No dense one-liners.
- Run the headless suite after every change. Add a test for every bug fixed.
- Keep Auto / Work together / Teach, pause-and-continue, and the Graphite Paper identity.
- Explain things to the owner in simple words and short sentences.
- One commit per fix. If a fix needs a design decision, ask the owner first.
- **No Claude attribution anywhere:** no "Co-Authored-By: Claude" and no "Generated with Claude
  Code" in commits or PRs.
- Never commit `CLAUDE.md`, `tools/live_runs/` or API keys. Commit or push only when the owner asks.
- Keep live runs few: each costs credits.
