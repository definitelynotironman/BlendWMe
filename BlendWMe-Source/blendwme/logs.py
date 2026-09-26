"""Full conversation logs, saved as files you can read or share.

After every task (finished, stopped or failed) BlendWMe writes two files to
Documents/BlendWMe Logs (or the folder you choose):

    2026-10-15_1552_cosy-low-poly-cottage.md    readable: every message, all code, results, timings
    2026-10-15_1552_cosy-low-poly-cottage.json  the exact messages sent to the model, for debugging

BlendWMe holds no keys or logins. As an extra safety net, a secret passed to
save() is replaced with [SECRET] before writing.
"""

import json
import os
import re
import time


def default_folder():
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    base = docs if os.path.isdir(docs) else os.path.expanduser("~")
    return os.path.join(base, "BlendWMe Logs")


def _slug(text):
    text = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "task").lower()).strip("-")
    return text[:40] or "task"


def _scrub(text, secret):
    if secret and len(secret) >= 8:
        text = text.replace(secret, "[SECRET]")
    return text


def _pretty(content):
    try:
        return json.dumps(json.loads(content), indent=2, ensure_ascii=False)
    except (ValueError, TypeError):
        return str(content)


def to_markdown(messages, trace, info):
    out = [f"# BlendWMe log: {info.get('title', 'task')}", ""]
    for key in ("saved", "version", "blender", "model", "mode", "level",
                "status", "model_calls", "tokens", "steps"):
        if key in info:
            out.append(f"- **{key.replace('_', ' ')}**: {info[key]}")
    out.append("")
    calls = [t for t in trace if t["type"] == "model"]
    if calls:
        total = sum(t.get("seconds", 0) for t in calls)
        out.append(f"Model time: {total:.0f}s over {len(calls)} calls "
                   f"(slowest {max(t.get('seconds', 0) for t in calls):.0f}s).")
        out.append("")
    step = 0
    for m in messages:
        role = m.get("role")
        if role == "system":
            out += ["<details><summary>System prompt</summary>", "", "```", m["content"], "```",
                    "</details>", ""]
        elif role == "user":
            content = m["content"]
            if isinstance(content, list):
                content = "\n".join(p.get("text", "") if p.get("type") == "text" else "[image]" for p in content)
            out += ["## YOU", "", content, ""]
        elif role == "assistant":
            if m.get("content"):
                out += ["## BLENDWME", "", m["content"], ""]
            for call in m.get("tool_calls") or []:
                fn = call["function"]
                try:
                    args = json.loads(fn["arguments"], strict=False)
                except ValueError:
                    args = {"raw": fn["arguments"]}
                if fn["name"] == "run_python":
                    step += 1
                    out += [f"### Step {step}: {args.get('title', '')}", "",
                            f"_{args.get('explain', '')}_", "", "```python", args.get("code", ""), "```", ""]
                else:
                    out += [f"### Tool: {fn['name']}", "", "```json",
                            json.dumps(args, indent=2, ensure_ascii=False), "```", ""]
        elif role == "tool":
            out += ["**Result:**", "", "```json", _pretty(m.get("content", "")), "```", ""]
    errors = [t for t in trace if t["type"] in ("tool_error", "error")]
    if errors:
        out += ["## Errors", ""] + [f"- {t.get('error', '')}" for t in errors] + [""]
    out += ["## Timeline", ""]
    start = trace[0]["t"] if trace else 0
    for t in trace:
        rest = ", ".join(f"{k}={v}" for k, v in t.items() if k not in ("t", "type"))
        out.append(f"- +{t['t'] - start:6.1f}s  {t['type']}  {rest}")
    return "\n".join(out) + "\n"


def _without_images(m):
    if isinstance(m.get("content"), list):
        m = dict(m, content=[p if p.get("type") == "text" else {"type": "text", "text": "[image]"}
                             for p in m["content"]])
    return m


def save(messages, trace, info, folder="", secret=""):
    """Write the .md and .json files. Returns the .md path."""
    folder = folder or default_folder()
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    base = os.path.join(folder, f"{stamp}_{_slug(info.get('title'))}")
    info = dict(info, saved=time.strftime("%Y-%m-%d %H:%M:%S"))
    messages = [_without_images(m) for m in messages]
    md = _scrub(to_markdown(messages, trace, info), secret)
    raw = _scrub(json.dumps({"info": info, "trace": trace, "messages": messages},
                            indent=1, ensure_ascii=False, default=str), secret)
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(md)
    with open(base + ".json", "w", encoding="utf-8") as f:
        f.write(raw)
    return base + ".md"
