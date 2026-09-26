"""A fake brain program for the headless tests: stands in for `claude -p` or `codex exec --json`.

It runs in the BlendWMe workspace folder (like the real programs) and plays
one turn from fake_script.json there (a list of turns; the first is removed).
A turn is a list of actions:

    {"say": "text"}                      streamed reply text (Claude) or an agent message (Codex)
    {"call": "run_python", "args": {}}   a real tool call to BlendWMe over the socket
    {"line": {...}}                      print this JSON event as it is
    {"text_line": "..."}                 print a plain text line
    {"stderr": "..."}                    print to stderr
    {"sleep": 0.5}
    {"exit": 1}                          end with this exit code (no final event)

Every run appends its arguments, stdin and some environment facts to
fake_runs.json, so the tests can check exactly how the program was started.
"""

import json
import os
import socket
import sys
import time

FOLDER = os.getcwd()
ARGS = sys.argv[1:]
CODEX = bool(ARGS) and ARGS[0] == "exec"


def record():
    path = os.path.join(FOLDER, "fake_runs.json")
    runs = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []
    stdin = sys.stdin.read() if not sys.stdin.isatty() else ""
    runs.append({"argv": ARGS, "stdin": stdin, "cwd": FOLDER,
                 "env": {k: (k in os.environ) for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY",
                                                        "PYTHONPATH", "MCP_TOOL_TIMEOUT")}})
    json.dump(runs, open(path, "w", encoding="utf-8"))
    return stdin


def next_turn():
    path = os.path.join(FOLDER, "fake_script.json")
    turns = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []
    turn = turns.pop(0) if turns else [{"say": "Nothing scripted."}]
    json.dump(turns, open(path, "w", encoding="utf-8"))
    return turn


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def tool_call(name, args, brain):
    conn = json.load(open(os.path.join(FOLDER, "connection.json"), encoding="utf-8"))
    with socket.create_connection(("127.0.0.1", conn["port"]), timeout=10) as s:
        s.settimeout(300)
        s.sendall(json.dumps({"op": "call", "id": 1, "token": conn["token"], "tool": name, "args": args,
                              "brain": brain}).encode() + b"\n")
        return json.loads(s.makefile("rb").readline())


def main():
    record()
    session = "fake-thread-1"
    if not CODEX:
        if "--resume" in ARGS:
            session = ARGS[ARGS.index("--resume") + 1]
        elif "--session-id" in ARGS:
            session = ARGS[ARGS.index("--session-id") + 1]
        emit({"type": "system", "subtype": "init", "session_id": session,
              "mcp_servers": [{"name": "blendwme", "status": "connected"}]})
    else:
        emit({"type": "thread.started", "thread_id": session})
        emit({"type": "turn.started"})
    final = ""
    n = 0
    for action in next_turn():
        n += 1
        if "say" in action:
            final = action["say"]
            if CODEX:
                emit({"type": "item.completed", "item": {"id": f"item_{n}", "type": "agent_message",
                                                         "text": action["say"]}})
            else:
                mid = f"msg_{n}"
                emit({"type": "stream_event", "event": {"type": "message_start", "message": {"id": mid}}})
                words = action["say"].split(" ")
                for i, word in enumerate(words):
                    emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {
                        "type": "text_delta", "text": word + (" " if i < len(words) - 1 else "")}}})
                emit({"type": "assistant", "message": {"id": mid, "content": [{"type": "text",
                                                                               "text": action["say"]}]}})
        elif "call" in action:
            brain = "codex" if CODEX else "claude"
            if CODEX:
                emit({"type": "item.started", "item": {"id": f"item_{n}", "type": "mcp_tool_call", "server": "blendwme",
                                                       "tool": action["call"], "arguments": action.get("args", {}),
                                                       "status": "in_progress"}})
            else:
                emit({"type": "assistant", "message": {"id": f"msg_{n}", "content": [{
                    "type": "tool_use", "id": f"tu_{n}", "name": "mcp__blendwme__" + action["call"],
                    "input": action.get("args", {})}]}})
            reply = tool_call(action["call"], action.get("args", {}), brain)
            text = json.dumps(reply.get("result"))
            if CODEX:
                emit({"type": "item.completed", "item": {"id": f"item_{n}", "type": "mcp_tool_call",
                                                         "server": "blendwme", "tool": action["call"],
                                                         "status": "completed", "result": {"content": [
                                                             {"type": "text", "text": text}]}}})
            else:
                emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"tu_{n}",
                                                               "content": [{"type": "text", "text": text}]}]}})
        elif "line" in action:
            emit(action["line"])
        elif "text_line" in action:
            sys.stdout.write(action["text_line"] + "\n")
            sys.stdout.flush()
        elif "stderr" in action:
            sys.stderr.write(action["stderr"] + "\n")
            sys.stderr.flush()
        elif "sleep" in action:
            time.sleep(action["sleep"])
        elif "exit" in action:
            sys.exit(action["exit"])
    if CODEX:
        emit({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})
    else:
        emit({"type": "result", "subtype": "success", "is_error": False, "result": final, "session_id": session})


if __name__ == "__main__":
    main()
