"""Executed in a separate process. NEVER imports or touches bpy."""
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

def request(config, payload):
    provider = config["provider"]
    base = config["url"].rstrip("/")
    messages = [{"role":"system","content":config["system"]}, {"role":"user","content":json.dumps(payload)}]
    if provider == "OLLAMA":
        url = base + "/api/chat"
        data = {"model":config["model"],"messages":messages,"stream":False,"format":"json","options":{"temperature":.2}}
        headers = {"Content-Type":"application/json"}
    else:
        url = base + "/chat/completions"
        data = {"model":config["model"],"messages":messages,"temperature":.2,"max_tokens":1800}
        headers = {"Content-Type":"application/json","Authorization":"Bearer " + os.environ.get("BLENDWME_API_KEY", "")}
        if not os.environ.get("BLENDWME_API_KEY"):
            raise ValueError("Missing API key. Enter it in the session-only connection settings.")
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read(2_000_001)
        if len(raw)>2_000_000:
            raise ValueError("Provider response exceeded 2 MB")
        obj = json.loads(raw)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Provider returned HTTP {e.code}. Check key, model and endpoint.") from None
    except urllib.error.URLError:
        raise RuntimeError("Could not reach the provider. Check endpoint and network access.") from None
    if provider=="OLLAMA":
        return obj["message"]["content"]
    return obj["choices"][0]["message"]["content"]

def main():
    args = sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else sys.argv[1:]
    inp, out = map(Path, args[-2:])
    try:
        data = json.loads(inp.read_text(encoding="utf-8"))
        result = {"ok":True,"content":request(data["config"], data["payload"])}
    except Exception as e:
        # Do not echo raw server bodies, prompts or credentials into logs.
        result = {"ok":False,"error":str(e)[:250] if isinstance(e,(RuntimeError,ValueError)) else "Malformed provider response"}
    temp = out.with_suffix(".tmp")
    temp.write_text(json.dumps(result),encoding="utf-8")
    temp.replace(out)

if __name__ == "__main__":
    main()
