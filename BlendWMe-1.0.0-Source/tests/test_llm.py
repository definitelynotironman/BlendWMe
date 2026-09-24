"""Pure-Python tests for the model client helpers (no Blender needed)."""
import importlib.util, json, os
spec = importlib.util.spec_from_file_location("llm", os.path.join(os.path.dirname(__file__), "..", "blendwme", "llm.py"))
llm = importlib.util.module_from_spec(spec); spec.loader.exec_module(llm)

m = llm.normalise_message({"content": "<think>plan</think>Building now."})
assert m == {"role": "assistant", "content": "Building now."}, m
m = llm.normalise_message({"content": 'ok <tool_call>{"name":"look","arguments":{"question":"q"}}</tool_call>'})
assert m["tool_calls"][0]["function"]["name"] == "look" and json.loads(m["tool_calls"][0]["function"]["arguments"]) == {"question": "q"}
m = llm.normalise_message({"content": '```json\n{"name": "finish", "arguments": {"summary": "s"}}\n```'})
assert m["tool_calls"][0]["function"]["name"] == "finish" and m["content"] == ""
m = llm.normalise_message({"content": None, "tool_calls": [{"function": {"name": "get_scene_info"}}]})
assert m["tool_calls"][0]["id"] and m["tool_calls"][0]["function"]["arguments"] == "{}"
assert llm.parse_arguments('{"code": "a\nb"}') == {"code": "a\nb"}          # raw newline tolerated
assert llm.reasoning_kwargs("nvidia/nemotron-3-super-120b-a12b", "LOW") == {"enable_thinking": True, "low_effort": True}
assert llm.reasoning_kwargs("moonshotai/Kimi-K2.6", "LOW") is None
assert llm.strip_thinking("<think>never closed") == ""
print("llm helper tests passed")
