"""Thin OpenAI wrapper: structured JSON out, retries, token accounting. Returns None on failure so every
agent can fall back to its rule-based path."""
import json
import threading
import time

import config

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

_client = None
_lock = threading.Lock()
usage = {"calls": 0, "failures": 0, "prompt_tokens": 0, "completion_tokens": 0}


def enabled() -> bool:
    return OpenAI is not None and bool(config.openai_key())


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=config.openai_key(), timeout=180, max_retries=2)
    return _client


def ask_json(system: str, user: str, schema: dict, name: str, model: str | None = None,
             effort: str | None = None) -> dict | None:
    """Call the model with a strict JSON schema. `effort` is the reasoning effort (low/medium/high)."""
    if not enabled():
        return None
    model = model or config.DEEP_MODEL
    kwargs = dict(model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                  response_format={"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}})
    if effort:
        kwargs["reasoning_effort"] = effort
    for attempt in range(2):
        try:
            r = _get_client().chat.completions.create(**kwargs)
            with _lock:
                usage["calls"] += 1
                usage["prompt_tokens"] += r.usage.prompt_tokens
                usage["completion_tokens"] += r.usage.completion_tokens
            return json.loads(r.choices[0].message.content)
        except Exception as ex:
            if "reasoning_effort" in str(ex) and "reasoning_effort" in kwargs:  # model doesn't take it
                kwargs.pop("reasoning_effort")
                continue
            if attempt == 0:
                time.sleep(3)
                continue
            with _lock:
                usage["failures"] += 1
            print(f"    [llm] {name} failed on {model}: {type(ex).__name__}: {str(ex)[:160]}")
    return None


# --- schema helpers -----------------------------------------------------------
def obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def arr(items: dict) -> dict:
    return {"type": "array", "items": items}


STR = {"type": "string"}
NUM = {"type": "number"}
BOOL = {"type": "boolean"}
INT = {"type": "integer"}


def enum(*values) -> dict:
    return {"type": "string", "enum": list(values)}
