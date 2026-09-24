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

    def describe_image(self, model, prompt, image_paths, max_tokens=1200):
        """Ask a vision model about one or more PNG/JPEG files."""
        content = [{"type": "text", "text": prompt}]
        for path in image_paths:
            content.append({"type": "image_url",
                            "image_url": {"url": image_data_url(path)}})
        body = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        data = self._request("/chat/completions", body)
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        return strip_thinking(msg.get("content") or "").strip()


# ----------------------------------------------------------------------
# Helpers (pure functions, unit tested)
# ----------------------------------------------------------------------

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


def normalise_message(message):
    """Make every reply look like a standard assistant message.

    Some open models sometimes write a tool call as text instead of using
    the tools field. We recover those so one formatting slip does not
    stop the whole task.
    """
    content = strip_thinking(message.get("content") or "")
    tool_calls = message.get("tool_calls") or []
    if not tool_calls and content:
        found = _TOOL_TAG.findall(content) or _FENCED.findall(content)
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
