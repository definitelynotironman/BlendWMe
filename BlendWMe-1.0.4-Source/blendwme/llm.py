"""Talking to the AI models.

Nebius Token Factory speaks the standard OpenAI "chat completions" format,
including native tool calling. That means we do NOT invent our own JSON
protocol. The model gets a list of tools and calls them. This is exactly how
MCP clients (like Claude) drive Blender, and it is what made the one-prompt
house possible.

This file has no Blender imports, so it can run in a background thread and
be tested outside Blender.
"""

import base64
import json
import re
import time
import urllib.error
import urllib.request
import uuid

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1"


class LLMError(Exception):
    """A readable error from the model provider."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class Client:
    def __init__(self, api_key, base_url=DEFAULT_BASE_URL, timeout=240):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Low level HTTP
    # ------------------------------------------------------------------
    def _request(self, path, body=None, method="POST"):
        if not self.api_key:
            raise LLMError("No API key. Add your Nebius key in BlendWMe > Connection.")
        url = self.base_url + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.api_key)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:800]
            except Exception:
                pass
            hint = {
                401: "The API key was rejected. Paste the full key again.",
                404: "The model or endpoint was not found. Check the model ID.",
                429: "Rate limited. Wait a moment and try again.",
            }.get(e.code, "")
            raise LLMError(f"HTTP {e.code}. {hint} {detail}".strip(), status=e.code) from None
        except urllib.error.URLError as e:
            raise LLMError(f"Could not reach {self.base_url}: {e.reason}. "
                           "Check your internet and Blender's 'Allow Online Access'.") from None
        except TimeoutError:
            raise LLMError(f"The model took longer than {self.timeout}s to answer.") from None

    def list_models(self):
        data = self._request("/models", method="GET")
        return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))

    def list_models_detailed(self):
        """[{"id", "vision"}]. Nebius adds modality info with ?verbose=true."""
        try:
            data = self._request("/models?verbose=true", method="GET")
        except LLMError:
            return [{"id": m, "vision": None} for m in self.list_models()]
        out = []
        for m in data.get("data", []):
            if not m.get("id"):
                continue
            out.append({"id": m["id"], "vision": accepts_images(m)})
        return sorted(out, key=lambda x: x["id"])

    # ------------------------------------------------------------------
    # Chat with tools
    # ------------------------------------------------------------------
    def chat(self, model, messages, tools=None, temperature=0.4, max_tokens=8192,
             reasoning="LOW"):
        """One model turn. Returns (message_dict, usage_dict).

        reasoning: DEFAULT (send nothing extra), LOW, ON or OFF. These map
        to NVIDIA Nemotron chat template switches. If the provider rejects
        them (HTTP 400) we retry once without them, so other models still work.
        """
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        extra = reasoning_kwargs(model, reasoning)
        if extra:
            body["chat_template_kwargs"] = extra
        try:
            data = self._request("/chat/completions", body)
        except LLMError as e:
            if e.status == 400 and extra:
                body.pop("chat_template_kwargs", None)
                data = self._request("/chat/completions", body)
            else:
                raise
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("The model returned no answer: " + json.dumps(data)[:400])
        message = choices[0].get("message") or {}
        finish = choices[0].get("finish_reason")
        message = normalise_message(message)
        message["_finish_reason"] = finish
        return message, data.get("usage") or {}

    def describe_image(self, model, prompt, image_paths, max_tokens=2500):
        """Ask a vision model about one or more PNG/JPEG files.

        Vision models on Nebius are often "thinking" models. Left alone they can
        spend the whole answer budget thinking and return no text (this happened
        with Kimi K2.6). We switch thinking off for this quick job, and if the
        answer is still empty we use the thinking text instead.
        """
        content = [{"type": "text", "text": prompt}]
        for path in image_paths:
            content.append({"type": "image_url",
                            "image_url": {"url": image_data_url(path)}})
        body = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"thinking": False, "enable_thinking": False},
        }
        try:
            data = self._request("/chat/completions", body)
        except LLMError as e:
            if e.status != 400:
                raise
            body.pop("chat_template_kwargs")
            data = self._request("/chat/completions", body)
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        text = strip_thinking(msg.get("content") or "").strip()
        if not text:
            text = (msg.get("reasoning_content") or msg.get("reasoning") or "").strip()[-2500:]
        return text

# ----------------------------------------------------------------------
# Helpers (pure functions, unit tested)
# ----------------------------------------------------------------------

# Vision models on Nebius, best first (Nemotron models on Nebius are text only).
VISION_PREFERENCE = [
    "moonshotai/Kimi-K2.6",
    "Qwen/Qwen3.8-27B",
    "zai-org/GLM-5.3-Flash",
    "moonshotai/Kimi-K3",
    "openbmb/MiniCPM-V-4_5",
    "deepseek-ai/DeepSeek-V4.1-Flash",
    "Qwen/Qwen2.5-VL-72B-Instruct",
    "Qwen/Qwen2-VL-72B-Instruct",
]


def accepts_images(model_info):
    """True/False from a verbose /models entry, or None when unknown."""
    arch = model_info.get("architecture") or {}
    modality = arch.get("modality") or model_info.get("modality") or ""
    if isinstance(modality, str) and "->" in modality:
        return "image" in modality.split("->")[0]
    inputs = arch.get("input_modalities") or model_info.get("input_modalities")
    if isinstance(inputs, list):
        return "image" in inputs
    kind = str(model_info.get("type") or "")
    if kind:
        return kind.startswith("image") or "vision" in kind
    return None


def pick_vision_model(models, current=""):
    """Choose a vision model from a detailed model list.

    Keeps the current choice if it exists and accepts images. Otherwise uses
    VISION_PREFERENCE, then any model that says it accepts images.
    """
    by_id = {m["id"].lower(): m for m in models}
    if current and current.lower() in by_id and by_id[current.lower()]["vision"] is not False:
        return by_id[current.lower()]["id"]
    for want in VISION_PREFERENCE:
        m = by_id.get(want.lower())
        if m and m["vision"] is not False:
            return m["id"]
    for m in models:
        if m["vision"]:
            return m["id"]
    return ""


def reasoning_kwargs(model, mode):
    if mode == "DEFAULT" or "nemotron" not in (model or "").lower():
        return None
    if mode == "OFF":
        return {"enable_thinking": False}
    if mode == "LOW":
        return {"enable_thinking": True, "low_effort": True}
    return {"enable_thinking": True}


def image_data_url(path):
    with open(path, "rb") as f:
        raw = f.read()
    mime = "image/jpeg" if path.lower().endswith((".jpg", ".jpeg")) else "image/png"
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


_THINK = re.compile(r"<think>.*?</think>", re.S)


def strip_thinking(text):
    text = _THINK.sub("", text or "")
    # A reply that opens a <think> block and never closes it is all thinking.
    if "<think>" in text:
        text = text.split("<think>")[0]
    return text


_TOOL_TAG = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
_FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


_XML_FUNCTION = re.compile(r"<function=([\w.\-]+)>(.*?)</function>", re.S | re.I)
_XML_PARAM = re.compile(r"<parameter=([\w\-]+)>\n?(.*?)\n?</parameter>", re.S | re.I)


def _xml_calls(text):
    """Calls in the XML style some chat templates use, returned as JSON strings:

        <tool_call><function=run_python><parameter=code>print(1)</parameter></function></tool_call>

    Nemotron 3 Ultra wrote '<FUNCTION=get_scene_info></FUNCTION>' in a live run when the
    provider did not turn it into a native tool call.
    """
    out = []
    for name, body in _XML_FUNCTION.findall(text):
        args = {}
        for key, value in _XML_PARAM.findall(body):
            try:
                args[key] = json.loads(value)
                if not isinstance(args[key], (list, dict, int, float, bool)):
                    args[key] = value
            except ValueError:
                args[key] = value
        out.append(json.dumps({"name": name, "arguments": args}))
    return out


def _bare_json_calls(text):
    """Tool calls written as plain JSON objects in the text, one after another.

    Nemotron 3 Ultra wrote '{"tool": "blender_exec", "arguments": {...}}' twice in a row with no
    tags or fences in a live run. Returns the raw JSON strings that look like tool calls.
    """
    decoder = json.JSONDecoder()
    out, i = [], 0
    while True:
        i = text.find("{", i)
        if i < 0:
            return out
        try:
            obj, end = decoder.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict) and (obj.get("name") or obj.get("tool")) and \
                any(k in obj for k in ("arguments", "args", "parameters")):
            out.append(text[i:end])
        i = end


def normalise_message(message):
    """Make every reply look like a standard assistant message.

    Some open models sometimes write a tool call as text instead of using
    the tools field. We recover those so one formatting slip does not
    stop the whole task.
    """
    content = strip_thinking(message.get("content") or "")
    tool_calls = message.get("tool_calls") or []
    if not tool_calls and content:
        found = (_TOOL_TAG.findall(content) or _FENCED.findall(content) or _bare_json_calls(content)
                 or _xml_calls(content))
        for raw in found:
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            name = obj.get("name") or obj.get("tool")
            args = obj.get("arguments", obj.get("args", obj.get("parameters", {})))
            if not name:
                continue
            tool_calls.append({
                "id": "call_" + uuid.uuid4().hex[:12],
                "type": "function",
                "function": {"name": name,
                             "arguments": args if isinstance(args, str) else json.dumps(args)},
            })
        if tool_calls:
            content = _TOOL_TAG.sub("", content)
            content = _FENCED.sub("", content).strip()
            for raw in found:
                content = content.replace(raw, "")
            content = _XML_FUNCTION.sub("", content)
            content = re.sub(r"</?tool_call>", "", content).strip()
    out = {"role": "assistant", "content": content}
    if tool_calls:
        clean = []
        for i, call in enumerate(tool_calls):
            fn = call.get("function") or {}
            clean.append({
                "id": call.get("id") or f"call_{int(time.time())}_{i}",
                "type": "function",
                "function": {"name": fn.get("name", ""),
                             "arguments": fn.get("arguments") or "{}"},
            })
        out["tool_calls"] = clean
    return out


def parse_arguments(raw):
    """Tool arguments arrive as a JSON string. Be forgiving about it."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"value": value}
    except ValueError:
        # Common slip: code with raw newlines inside the JSON string.
        try:
            return json.loads(raw, strict=False)
        except ValueError as e:
            raise ValueError(f"Tool arguments were not valid JSON ({e}).") from None
