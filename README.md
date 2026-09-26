# BlendWMe 1.0.5

An AI Blender artist that lives inside Blender. It can build at full speed, slow down and show you how, or coach you while you do it yourself.

Built for the Nebius x NVIDIA Global AI Hackathon. It runs on Nebius Token Factory with NVIDIA Nemotron as the default agent.

## What changed from 0.6 (and why)

Version 1.0 is a rebuild. The old version wrapped a smaller model in a strict custom protocol: one JSON action per call, a 6,000-character rulebook, and recipes run on a copy of the scene. Each rule was another way for the model to fail.

MCP for Blender showed a better pattern, so 1.0 uses it:

| | 0.6 | 1.0 |
|---|---|---|
| How the model acts | Custom JSON, one action per call | Native tool calling, many steps in one conversation |
| How code runs | Filtered recipe language in a second Blender process | Normal Blender Python on the live scene, with an undo step before each run |
| Memory | Rebuilt context every call | One continuous conversation |
| Seeing | Viewport review | Multi-angle contact sheets judged by a vision model "art director" |
| Knowledge | Long prompt rules | Skill cards loaded on demand + live Blender API lookup |
| Code style | 3,700 dense one-liners | About 3,000 lines of plain, commented code |

What MCP does not have, and BlendWMe does: it lives in Blender, so you need no outside chat app. It has a Watch mode that builds the way a person would and explains each step. It has a Teach mode where you do the work and it checks you. You can take the wheel at any time and it continues from your version.

## Install

Download `blendwme-1.0.5.zip` from this repo's **Releases** page (or build it yourself: `python tools/package.py` inside `BlendWMe-Source` writes it to `BlendWMe-Source/dist/`).

1. Edit > Preferences > Add-ons. Disable any older BlendWMe. Restart Blender.
2. Top-right menu > **Install from Disk** > choose `blendwme-1.0.5.zip`.
3. Make sure **Allow Online Access** is on (Preferences > System > Network).
4. In the 3D Viewport press **N** and open the **BlendWMe** tab.
5. Open **Connection**. Copy your Nebius key, then press **Paste Key**. Press **Test Connection**. It finds the exact model IDs on your account and checks that tool calling works.
6. Optional: press **Test Vision**. It lists the models on your account that accept images, tests them and keeps the first that works. If you skip this, BlendWMe picks one by itself the first time it looks.

**Why vision is a separate model:** no Nemotron model on Nebius accepts images. Nemotron is the agent (the brain and hands). A vision model such as Kimi K2.6 or Qwen3.8-27B is its eyes. If vision fails, BlendWMe keeps building from scene data instead of stopping.

## Your API key

The key is **never** saved in Blender's preferences or in any `.blend` file. You choose where it lives:

| Option | Where | When to use |
|---|---|---|
| Password store (default) | Windows Credential Manager, macOS Keychain or Linux Secret Service, encrypted for your user account | Your own PC |
| This session only | Memory only, gone when Blender closes | Shared or lab PCs, live demos |
| Environment variable | `NEBIUS_API_KEY` | If you already manage keys that way |

How it stays safe:
- **Paste Key** reads the clipboard, stores the key, then clears the clipboard.
- The panel only shows the last 4 characters.
- The AI's own code is blocked from reading the clipboard, environment variables, the password store or BlendWMe's internals. The model can never print your key into the conversation.
- Copy Log never includes the key.
- **Forget Key** (bin icon) deletes it from memory and the password store. On Windows you can also see it under Credential Manager > Windows Credentials > `BlendWMe-Nebius`.

Good habits: make a separate Nebius key just for BlendWMe, so you can revoke it alone. Never paste it into code, GitHub, Devpost or a screen recording. If it leaks, revoke it in the Nebius console and paste a new one.

## First test (the house)

1. Open a new General scene. Choose **Auto**.
2. Type: *A cosy low poly cottage with a red tiled roof, a chimney, wooden door, four windows, a stone base and a small path. Stylised, warm colours.*
3. Press **New Task**. Watch the card at the bottom of the viewport.
4. When it finishes, type a follow-up: *Make the chimney taller and add a fence.* It remembers the whole project.

Then try the other modes on the same scene:

- **Watch:** *Show me how you would build a wooden fence.* Press **Next** after each step. Each step uses the operators a person would use (loop cut, extrude, modifiers), and the card tells you the shortcut.
- **Pause and Take Control:** while it works, press Pause. Move or reshape something. Press **Resume**. It is told exactly what you changed and builds around your version.
- **Teach:** *Teach me to model a simple table.* You do each step. It points at what matters, and the step completes by itself when your scene shows it is done. Press **Show me** if you are stuck.

## Build styles (Auto mode)

- **Piece by Piece** (default): BlendWMe first writes a **plan**, a checklist of parts with sizes, shown in the panel. Then each part lands one at a time with a red pointer, and the checklist ticks off. This is the BlendWMe way: you can follow along, pause at any part and take over.
- **Full Build**: MCP-style. Few, big steps, as fast as possible. Best when you just want the result.

Watch mode is piece by piece with a Next button and an explanation for every step. Teach mode is you doing each piece.

## Controls

| Button | What it does |
|---|---|
| Send / New Task | Send continues the conversation. New Task starts fresh. |
| Tell BlendWMe | While it works, your message steers it at the next step. |
| Pause and Take Control | It stops after the current action. The scene is yours. |
| Resume / Continue | Hand back. It reads your changes first. |
| Next Step | Watch mode: go to the next step. Or set Auto-advance seconds. |
| I did it / Show me / Skip | Teach mode answers. |
| Undo AI Step | Same as Ctrl+Z. Every AI step is its own undo step. |
| Construction Notebook | Every step it ran, with its code, in a Text block. Good for learning and judges. |
| What the AI Saw | Opens the last picture the vision model judged. |

## Models

- **Agent:** **Nemotron 3 Ultra** by default. In a one-shot cottage test it was the fastest (12 s for 154 lines of code). Its mistakes were small API slips, which the pre-check now catches. Nemotron 3 Super also works but is slower and weaker. Nemotron 3.5 Lightning ran out of tokens without finishing, so it is not recommended. **Kimi K2.6** is available too: it can see pictures itself, which is the setup closest to Claude + MCP.
- **Vision:** picked automatically from models that accept images (Kimi K2.6, Qwen3.8-27B, GLM-5.3-Flash and others). Nebius reports this in its model list, so BlendWMe never guesses. Thinking is switched off for vision calls (about 2 s each). Before this, Kimi sometimes thought so long that it returned an empty answer, which left the agent blind.
- **Agent sees pictures itself:** if your agent model accepts images (e.g. Kimi K2.6), the picture goes straight to it, like Claude in MCP, instead of being described by a second model.
- **Reasoning:** Low effort is the default. Use Full for hard briefs and Off for speed.

Do a quick bake-off: give the same house prompt to Super and Ultra and compare the result and the time. Pick the winner for your demo.

## How it works

```
you ─► panel ─► agent thread ──► Nebius (Nemotron, tool calling)
                    │  ▲
         tool calls │  │ results
                    ▼  │
        bridge (Blender main thread)
          ├─ get_scene_info / get_object_info   facts
          ├─ run_python                          hands (undo step first)
          ├─ look ─► vision model                eyes + art director
          ├─ load_skill / inspect_api            knowledge
          ├─ point_at / teach_step               teaching
          └─ ask_user / finish
```

| File | Job |
|---|---|
| `agent.py` | The loop. Notices your edits, asks the model, runs tools, waits in Watch/Teach |
| `llm.py` | OpenAI-compatible client for Nebius. Recovers tool calls written as text |
| `tools.py` | The tool list the model sees |
| `prompts.py` | The system prompt: workflow, quality bar, Python rules, mode rules |
| `executor.py` | Runs code on the live scene, reports errors with line numbers and what changed |
| `look.py` | Viewport, camera and multi-angle captures (ignores huge ground planes) |
| `scene_info.py` | Scene summaries, snapshots, and the diff that tells the AI what you changed |
| `knowledge.py` + `skills/` | Skill cards and live API lookup |
| `overlay.py` | The Graphite Paper card and red radar pointers |
| `ui.py`, `prefs.py` | Panel, buttons, settings |
| `credentials.py` | Keeps your API key in the OS password store, never in Blender files |
| `repair.py` | Repairs mechanical API slips (3 vs 4 colour values, swapped modifier arguments, renamed inputs, 2.7x calls) before a step runs |
| `precheck.py` | Rejects only what cannot be repaired, before it runs |
| `shapes.py`, `furniture.py` | The bwm modelling kit |
| `logs.py` | Writes the full conversation logs |
| `bridge.py` | Runs Blender work safely on the main thread |

### The bwm modelling kit (new in 1.0.5)

Open models write plausible but wrong low-level Blender Python. So instead of making the model fight bmesh, BlendWMe gives it a tested, forgiving kit, available inside run_python as `bwm.<name>`:

| Group | Functions |
|---|---|
| Shapes | `box`, `cylinder`, `cone` (point-down too), `sphere` (or ellipsoid), `torus`, `prism`, `wedge`, `extrude_profile` (2D outline to solid), `loft` (smooth body through sections), `lathe`, `tube`, `limb`, `gable_roof` (with tile rows) |
| Modifiers, kept live | `bevel`, `mirror`, `array`, `subdivide`, `solidify`, `boolean` |
| Placement | `bounds`, `top`, `place_on` (ray-cast onto sloped roofs, `sink=True` for chimneys), `align`, `rotate`, `duplicate`, `parent`, `group` |
| Architecture | `wall`, `floor`, `opening`, `window` and `door` (they snap onto a thin wall or a solid house body, cut a live hole and face outwards), `path` |
| Furniture | `bed`, `desk`, `table`, `chair(at='Desk')`, `wardrobe` / `closet`, `shelf`, `monitor(on='Desk')`, `keyboard(on='Desk')`, `pc_tower`, `rug`, `lamp`, `plant`; `against='WallName'` turns a piece to face the room with its back on that wall |

Every function takes `name, sizes, location, collection, material` in that order and everything else by keyword. Arguments are forgiving: numbers as text, colours as 3 or 4 values, 0-255, `'#hex'` or `'red'`, common alias names (`color=`, `position=`, `height=`), and unknown extras are ignored with a note. Re-running a step with the same names replaces the AI's own earlier objects instead of making `Roof.001` copies; the user's objects are never replaced. `inspect_api('bwm.gable_roof')` shows the full help.

Three worked example cards (`example-cottage`, `example-room`, `example-ice-cream-cone`) show complete builds with the kit. The test suite runs every step of every example card in Blender 4.2.

### Making it a better artist

Add or improve cards in `skills/`. Each card is a short Markdown file: the workflow, good settings, code patterns and common mistakes. The first line is the description the AI sees. Test each card's code in Blender before you keep it. This is the main way to raise quality in new areas (characters, animation, sculpting) without touching the engine.

## When things go wrong

BlendWMe expects mistakes and is built to recover on its own:

| Problem | What happens |
|---|---|
| The code has a small mechanical API slip (`diffuse_color` with 3 values, `modifiers.new('SOLIDIFY', 'Solidify')`, `inputs['Specular']`, `scene.objects.link`, `bpy.active_object`, `create_cone(radius=)`, `obj.select = True`, a removed 2.7x property) | It is repaired and the step runs. The model is told what was fixed, and the step counts as a success. Line numbers in errors still match the model's code. |
| The model writes tool calls as text (tags, bare JSON, XML, YAML, code in python fences) or calls MCP-style tools like `execute_blender_code` | They are recovered and run. In Auto mode BlendWMe asks the provider for a tool call every turn (`tool_choice=required`), which stopped Nemotron from answering in broken text. |
| The model only tests the environment ("is bwm available?") | The step is not run and not counted as a failure; the model gets the answer (everything is ready, plus the bwm list). |
| A step's code crashes | Everything that step created is removed, so the retry never leaves duplicates like `Roof.001`. The AI gets the failing line, the error and a plain-language hint (for example "you passed a name where Blender needs the object"), then runs the whole step again. You see STEP FAILED, then STEP (retry). |
| The same step fails twice | It is told to change approach (and, if the error repeats exactly, that it is the same error). Failures are counted per step, not across different steps. |
| The same step fails 4 times | It pauses and hands you the wheel with the error on the card. Pre-check rejections (nothing ran) count separately, with a limit of 6. |
| A step half-changed objects that already existed | It is told exactly which ones, so it can fix them instead of guessing. |
| Network trouble or the provider is busy | Automatic retry after 3, 8 and 20 seconds. |
| The conversation gets too long | Older steps are shortened and the request is retried. |
| The code uses a Blender name that does not exist and cannot be repaired (like `bm.new` or `bmesh.ops.cube`) | A pre-check reads the code before it runs, compares it with the real API of your Blender, and sends back the right name. Nothing runs, so nothing needs undoing. |
| A step fails and gets fixed | Shown as a quiet grey SELF-CORRECTING line ("fixed a code error, 2 tries"), not a red problem. Red is kept for real trouble. |
| The vision model is missing or fails | Another model that accepts images is found automatically. If none works, BlendWMe keeps building and checks its work with scene data. |
| The model calls Python as if it were a tool (`bwm.mesh(...)`, `bpy.ops...` as tool calls) | BlendWMe runs those calls together as one real Python step (materials and collections first) and tells the model to use run_python. Unknown tool names get a reply listing the real tools and an example. At most 8 tool calls run per turn. |
| New objects end up outside the scene (e.g. in a collection that was removed from the scene) | bwm.collection links the collection back into the scene, and any new object that is still not in the scene is reported to the AI. |
| The model writes a broken tool call | It is recovered when possible, or the model is asked to resend it. |
| The model only talks and stops working | It is nudged once to carry on. |
| Something still goes wrong | Press Pause or Stop at any time. Undo AI Step (Ctrl+Z) reverses any step. Copy Log gives a full report to share. |

## The art director (new in 1.0.5)

In Auto mode the harness drives vision, not the model:

- **References first.** Reference images become a written build spec (style, parts with counts and relative sizes, proportions, colours) before anything is built.
- **Reviews without being asked.** After the first build step (with a reference) or the third (without), and then every 4 steps, BlendWMe captures the scene from four angles and a vision model judges it with a rubric: match to the brief and reference, silhouette and proportions, contact, shape detail, colour and value, then the most valuable fixes. The review restates the brief and lists every AI-made object with its exact size, so leftovers and wrong scale are easy to spot.
- **One check before finishing.** The first time the model calls finish, the result is reviewed once more if anything changed.
- **Critique converges.** At most 3 periodic reviews per task. When every plan part is built (or after 20 build steps without a plan), the model is told to fix only clear problems and finish.

## Live evaluation

`tools/live_eval.py` runs one real task in background Blender against real Nebius (key from `NEBIUS_API_KEY`). When nobody is at the keyboard it presses Resume and answers questions, and counts each time. It saves the full log, a four-angle picture, the `.blend` and a summary line.

```
blender -b --factory-startup --python tools/live_eval.py -- --task cottage      # or room, cone, cottage_ref
```

## Full logs

After every task, BlendWMe saves the whole conversation to **Documents/BlendWMe Logs**:

- a `.md` file you can read: every message, the full code of every step, every result, errors, and a timeline with how long each model call took
- a `.json` file with the exact messages sent to the model, for debugging

Use **Learn and Debug > Save Full Log** to save at any moment, and **Open Logs Folder** to find them. Turn auto-saving off or pick another folder in Connection. The key is never in the conversation, and any copy of it is replaced with `[API KEY]` before saving.

## What it can do in Blender

Anything Blender can do from Python is in reach: modelling, materials and shader nodes, lighting, cameras, render setup, rigging, keyframe animation, shape keys, geometry nodes, Grease Pencil, physics settings, compositing and the video sequencer. Skill cards currently cover modelling, low-poly scenes, characters, materials, lighting, rigging, animation, sculpting (preparing and teaching), video editing and hard surface. Modelling is the most tested. The other areas work but have not been tuned yet.

Limits: it cannot paint freehand sculpt or texture strokes like a person with a tablet (it prepares the mesh and teaches instead). It judges animation from still frames, not by watching playback. It can point at editors and panels, but not at one exact button.

## Safety and limits

- Code runs on your live scene, like MCP for Blender. Every step is undoable. A small blocklist stops file access, system commands and closing Blender. This is a guardrail, not a sandbox, so save your work first.
- Freehand sculpt strokes are not automated. BlendWMe prepares meshes and teaches sculpting instead.
- Video editing is supported through the sequencer API, but needs your own footage paths.
- Quality depends on the model. Nemotron 3 Super is a mid-size model. If results are weak, try Ultra and Full reasoning before changing code.

## Tests

- `tests/test_agent_headless.py` runs the real agent loop in headless Blender 4.2.3 against a fake model server: building, error recovery, user-edit detection, pause mid-thought, Watch gating, Teach checks, Stop, auto-repair, the bwm kit (with geometry checks), the example cards, harness-driven reviews, the stuck guard and text tool-call recovery. 192 checks:
  `"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe" -b --factory-startup --python-exit-code 1 --python tests/test_agent_headless.py`
- `tests/test_llm.py` tests the client helpers, including every text tool-call format seen in live runs.
- `tools/live_eval.py` runs real tasks against Nebius (see Live evaluation).
- `tools/package.py` builds `dist/blendwme-<version>.zip`.

## Credits

The offscreen capture and multi-angle framing in `look.py` adapt MCP for Blender by Siddharth Ahuja (MIT). See `LICENSE-THIRD-PARTY`.

## Changelog

- **1.0.5**: the reliability floor (Phase A). `import bwm` works. Mechanical API slips are repaired instead of rejected. The forgiving bwm modelling kit (shapes, placement, architecture, furniture). Worked example cards for the cottage, room and ice cream cone. Harness-driven vision: reference spec, art director reviews with an object inventory, a final check, converging critique. Smarter stuck guard (per step; pre-check and test steps not counted). inspect_api for bmesh and mathutils. Auto mode requires a tool call every turn, and tool calls written as text are recovered in five formats. look works in background Blender, and captures show node materials in their real colour. Blender's default cube is flagged. Live evaluation script.

- **1.0.4**: tool arguments sent as text ("2.0", "[1, 2]", "True") are converted using each tool's schema, like MCP servers do; long-conversation shortening no longer adds a fake code field to non-code tool calls.

- **1.0.3**: rescue Python-as-tool calls; clear "tools vs Python" section in the prompt (Nemotron 3 Ultra then used run_python correctly in 3 of 3 live trials); hidden collections relinked; warning for objects outside the scene; checks for swapped `modifiers.new` arguments and wrong node types; inspect_api lists operator groups and bwm helpers; red "Vision is OFF" warning in the panel; max 8 tool calls per turn.
- **1.0.2**: plan checklist, Piece by Piece / Full Build, quiet self-correction, vision without thinking, agent sees images itself, legacy API and argument checks, Nemotron 3 Ultra default.
- **1.0.1**: vision model auto-discovery, never stops on vision errors, pre-check of API names, bwm.box / bwm.mesh, full logs.
- **1.0.0**: rebuild on the MCP pattern: native tool calling, run_python on the live scene, look, skills, Auto / Watch / Teach, safe key storage, rollback of failed steps.
