"""Run one real BlendWMe task in background Blender against real Nebius.

This is how we measure progress honestly: the real agent, the real model,
the real Blender, with nobody pressing buttons. It costs API credits.

Run (key from the NEBIUS_API_KEY environment variable):

    blender -b --factory-startup --python tools/live_eval.py -- --task cottage
    blender -b --factory-startup --python tools/live_eval.py -- --prompt "Model a teapot" --label teapot

Options after "--":
    --task NAME        one of the Stage 0 prompts: cottage, room, cone, cottage_ref
    --prompt TEXT      any prompt instead of --task
    --label NAME       short name used in file names (default: the task name)
    --ref PATH         reference image (cottage_ref uses tests/references/cottage.png)
    --model ID         agent model (default Nemotron 3 Ultra)
    --reasoning MODE   LOW, ON, OFF or DEFAULT (default ON, as in the 1.0.4 live logs)
    --style STYLE      PIECE or FULL (default PIECE, the add-on default)
    --minutes N        stop after N minutes (default 25)
    --out FOLDER       where to save results (default tools/live_runs)

Nobody is at the keyboard, so when BlendWMe pauses we count the pause and
press Resume for it, and when it asks a question we answer "use your best
judgment". A perfect run has zero pauses, zero questions and ends with finish.

Saved per run: the full log (.md + .json), a picture of the result from four
angles (.png), the .blend file, and one line in results.jsonl.
"""

import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(line_buffering=True)

import bpy  # noqa: E402

from blendwme import bridge, look, prefs, state, ui  # noqa: E402
from blendwme.agent import agent  # noqa: E402
from blendwme.state import session  # noqa: E402

TASKS = {
    "cottage": "A cosy low poly cottage with a red tiled roof, a chimney, wooden door, four windows, "
               "a stone base and a small path. Stylised, warm colours.",
    "room": "Model me a modern room, I want two walls only, the other side should be open for the viewer "
            "to see. In it i want a bed, a pc setup and a closet.",
    "cone": "Model me a simple ice cream cone.",
}
TASKS["cottage_ref"] = TASKS["cottage"]
DEFAULT_REF = os.path.join(ROOT, "tests", "references", "cottage.png")
ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"
ANSWER = "I'm not at the keyboard. Use your best judgment and carry on."


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="live_eval")
    p.add_argument("--task", choices=sorted(TASKS))
    p.add_argument("--prompt")
    p.add_argument("--label")
    p.add_argument("--ref")
    p.add_argument("--model", default=ULTRA)
    p.add_argument("--reasoning", default="ON", choices=["LOW", "ON", "OFF", "DEFAULT"])
    p.add_argument("--style", default="PIECE", choices=["PIECE", "FULL"])
    p.add_argument("--minutes", type=float, default=25)
    p.add_argument("--out", default=os.path.join(HERE, "live_runs"))
    args = p.parse_args(argv)
    if not args.prompt and not args.task:
        p.error("give --task or --prompt")
    args.prompt = args.prompt or TASKS[args.task]
    args.label = args.label or args.task or "custom"
    if args.task == "cottage_ref" and not args.ref:
        args.ref = DEFAULT_REF
    return args


def tool_results():
    """Every tool result the model received, parsed when it is JSON."""
    out = []
    for m in agent.messages:
        if m.get("role") != "tool":
            continue
        try:
            out.append(json.loads(m["content"]))
        except (ValueError, TypeError):
            out.append(m["content"])
    return out


def count_repairs(results):
    total = 0
    for r in results:
        if isinstance(r, dict) and r.get("auto_repaired"):
            total += len(r["auto_repaired"])
    return total


def main():
    args = parse_args()
    key = os.environ.get("NEBIUS_API_KEY", "").strip()
    if not key:
        print("NEBIUS_API_KEY is not set.")
        os._exit(2)
    refs = []
    if args.ref:
        if not os.path.isfile(args.ref):
            print(f"Reference image not found: {args.ref}")
            os._exit(2)
        refs = [os.path.abspath(args.ref)]

    stamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(args.out, f"{stamp}_{args.label}")
    os.makedirs(run_dir, exist_ok=True)

    prefs.register()
    ui.register()
    session.mode = "AUTO"
    session.build_style = args.style
    session.level = "BEGINNER"

    config = {
        "api_key": key, "base_url": "https://api.tokenfactory.nebius.com/v1", "model": args.model,
        "vision_enabled": True, "agent_vision": True, "vision_model": "",
        "reasoning": args.reasoning, "temperature": 0.4, "max_tokens": 8192, "max_steps": 60,
        "references": refs, "auto_save_logs": True, "log_folder": run_dir,
        "blender_version": bpy.app.version_string,
    }

    print(f"== live_eval {args.label}: {args.prompt[:80]}")
    print(f"   model {args.model}, reasoning {args.reasoning}, style {args.style}, refs {len(refs)}")
    pauses, questions = [], 0
    t0 = time.time()
    deadline = t0 + args.minutes * 60
    timed_out = False
    agent.send(args.prompt, config, new_task=True)

    last_report = 0
    while agent.is_running():
        bridge._pump()
        if session.status == state.PAUSED and session.pause_flag.is_set():
            title = (session.card or {}).get("title", "paused")
            pauses.append({"t": round(time.time() - t0), "why": title,
                           "text": (session.card or {}).get("text", "")[:300]})
            print(f"   PAUSE at {pauses[-1]['t']}s: {title}. Pressing Resume.")
            session.pause_flag.clear()
            session.wake()
        if session.status == state.ASKING and session.inbox.empty():
            questions += 1
            print(f"   QUESTION: {(session.card or {}).get('text', '')[:200]}")
            session.inbox.put(ANSWER)
            session.wake()
        if time.time() > deadline and not timed_out:
            timed_out = True
            print("   TIME LIMIT reached, stopping.")
            agent.stop()
        if time.time() - last_report > 30:
            last_report = time.time()
            print(f"   ... {int(time.time() - t0)}s, {len(session.steps)} steps, {session.tokens} tokens, "
                  f"status {session.status}")
        time.sleep(0.02)
    # Let the final bridge jobs (log saving runs in the agent thread) settle.
    bridge._pump()
    seconds = round(time.time() - t0)

    results = tool_results()
    steps = session.steps
    failed = [s for s in steps if not s["ok"]]
    made = sorted(o.name for o in bpy.context.scene.objects if o.get("blendwme_author") == "ai")
    finished = agent.finished
    outcome = "DONE" if finished else ("TIMEOUT" if timed_out else
                                       ("ERROR" if session.status == state.ERROR else "STOPPED_EARLY"))

    picture = ""
    shot = look.capture("angles")
    if shot.get("path"):
        picture = os.path.join(run_dir, "result.png")
        shutil.copyfile(shot["path"], picture)
    try:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(run_dir, "result.blend"), copy=True)
    except Exception as e:
        print("   could not save .blend:", e)

    summary = {
        "label": args.label, "prompt": args.prompt, "model": args.model, "reasoning": args.reasoning,
        "style": args.style, "references": len(refs), "outcome": outcome, "finished": finished,
        "seconds": seconds, "model_calls": session.calls, "tokens": session.tokens,
        "steps": len(steps), "failed_steps": len(failed), "auto_repairs": count_repairs(results),
        "pauses": len(pauses), "pause_reasons": pauses, "questions": questions,
        "looks": sum(1 for r in results if isinstance(r, dict) and ("vision_report" in r or "image" in r)),
        "harness_reviews": sum(1 for m in agent.messages if m.get("role") == "user"
                               and "[Art director review" in str(m.get("content"))),
        "rescued_calls": agent.rescued, "bad_tool_calls": agent.bad_tool_calls,
        "objects_created": len(made), "objects": made[:60],
        "error": session.last_error if outcome == "ERROR" else "",
        "log": session.last_log, "picture": picture, "run_dir": run_dir,
    }
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    with open(os.path.join(args.out, "results.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({k: v for k, v in summary.items() if k not in ("objects", "pause_reasons")}) + "\n")

    print("== SUMMARY")
    for k in ("outcome", "seconds", "model_calls", "tokens", "steps", "failed_steps", "auto_repairs",
              "pauses", "questions", "looks", "harness_reviews", "rescued_calls", "bad_tool_calls",
              "objects_created"):
        print(f"   {k:16} {summary[k]}")
    for p in pauses:
        print(f"   pause: {p['why']} - {p['text'][:160]}")
    print(f"   log: {summary['log']}")
    print(f"   picture: {picture}")
    os._exit(0 if finished and not pauses else 1)


main()
