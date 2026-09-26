"""LLM wrapper with failover across providers/models (Groq first: fast; OpenRouter free models as backup)."""
import json
import os
import re
import time

TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "10"))
OR_MODELS = ["google/gemma-4-31b-it:free", "nvidia/nemotron-3-super-120b-a12b:free", "qwen/qwen3.8-27b:free"]
_clients = {}


def model_name():
    return "openai/gpt-oss-120b (Groq) -> OpenRouter free models failover"


def _chain():
    chain = []
    if os.environ.get("GROQ_API_KEY"):
        chain += [("groq", "openai/gpt-oss-120b"), ("groq", "qwen/qwen3.8-27b")]
    if os.environ.get("OPENROUTER_API_KEY"):
        chain += [("or", m) for m in OR_MODELS]
    return chain


def _client(kind):
    if kind not in _clients:
        if kind == "groq":
            from groq import Groq
            _clients[kind] = Groq(api_key=os.environ["GROQ_API_KEY"], timeout=TIMEOUT, max_retries=0)
        else:
            from openai import OpenAI
            _clients[kind] = OpenAI(api_key=os.environ["OPENROUTER_API_KEY"], base_url="https://openrouter.ai/api/v1",
                                    timeout=TIMEOUT, max_retries=0)
    return _clients[kind]


def _call(kind, model, msgs, max_tokens):
    kw = {"max_tokens": max_tokens}
    if kind == "groq" and "gpt-oss" in model:
        kw["reasoning_effort"] = "low"
    return _client(kind).chat.completions.create(model=model, temperature=0, messages=msgs, **kw)


def chat_json(system, user, max_tokens=700):
    """Return parsed JSON dict, or None if every provider fails. Waits out short Groq TPM 429s once."""
    msgs = [{"role": "system", "content": system + "\nReturn ONLY a JSON object, no markdown."},
            {"role": "user", "content": user}]
    for kind, model in _chain():
        mt = 900 if "gpt-oss" in model else max_tokens
        for attempt in (0, 1):
            try:
                r = _call(kind, model, msgs, mt)
                m = re.search(r"\{.*\}", r.choices[0].message.content or "", re.S)
                if m:
                    return json.loads(m.group(0))
                break
            except Exception as e:
                w = re.search(r"try again in ([\d.]+)s", str(e))
                if attempt == 0 and w and float(w.group(1)) <= 9 and "429" in str(e):
                    time.sleep(float(w.group(1)) + 0.3)
                    continue
                break
    return None
