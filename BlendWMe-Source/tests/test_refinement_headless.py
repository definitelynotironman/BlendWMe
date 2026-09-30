"""Formatted text regressions; no subscription clients."""
import os
import sys
import threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from blendwme import bridge, richtext
passed = 0

def check(name, condition):
    global passed
    assert condition, name
    passed += 1
    print("ok -", name, flush=True)

effects, errors = [], []


def expired():
    try:
        bridge.call(lambda: effects.append("expired"), timeout=0.01)
    except TimeoutError as error:
        errors.append(str(error))


worker = threading.Thread(target=expired)
worker.start()
worker.join(1)
bridge._pump()
check("expired pending work never executes", not effects and errors)

source = "plain **bold text** and `bpy.data`\n\n- first\n- **second**\nEND"
measure = lambda role, size, value: len(value) * (9 if role == "BLACK" else 7)
lines = richtext.layout(source, 16, 110, measure)
check("mixed fonts fit the measured width", all(sum(run[2] for run in line) <= 110 for line in lines))
check("bold markers are hidden", "**" not in richtext.plain(source))
check("inline code markers are hidden", "`" not in richtext.plain(source))
check("paragraphs and lists survive", "\n\n- first\n- second\nEND" in richtext.plain(source))
check("source is unchanged", "**bold text**" in source)
check("unfinished streaming bold is literal", richtext.plain("a **unfinished") == "a **unfinished")
check("unfinished streaming code is literal", richtext.plain("a `unfinished") == "a `unfinished")
long = (source + "\n") * 100 + "SENTINEL"
long_lines = richtext.layout(long, 16, 80, measure)
check("long messages reach final sentinel", "".join(r[1] for line in long_lines for r in line).endswith("SENTINEL"))
check("empty formatted text is safe", richtext.layout("", 16, 80, measure) == [[]])

print(f"ALL {passed} REFINEMENT CHECKS PASSED", flush=True)
