"""Provider-agnostic LLM access for the agent council.

The council must never be the reason a demo fails: with no key, no
network or no ``openai`` package installed, :func:`ask_json` returns
``None`` and every agent falls back to its deterministic rule path and
says so in its status line.

Configuration is entirely environmental, so the same code drives a hosted
model, a free provider or a local one::

    OPENAI_API_KEY     enables the model path against the OpenAI API
    OPENAI_BASE_URL    any OpenAI-compatible endpoint, e.g. a local
                       model at http://localhost:11434/v1 (Ollama)
    WATCHTOWER_MODEL   model name (overrides the provider default)

A free provider is enough on its own - set one key and the endpoint and a
sensible default model are filled in (see :data:`PROVIDERS`)::

    GROQ_API_KEY       https://api.groq.com/openai/v1
    CEREBRAS_API_KEY   https://api.cerebras.ai/v1

Keys are looked up in the environment first, then ``.env``, then an
``Open_AI_API_Key.txt`` file in the working directory.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"

#: Attempts per output mode before giving up and letting the caller use rules,
#: and the waits used between them for errors that are not rate limits.
MAX_ATTEMPTS = 4
_BACKOFF_S = (1.5, 6.0, 15.0)

#: A rate limit is a "wait your turn", not a failure: free tiers are tight
#: enough that a burst would otherwise look like the model is broken. The
#: provider's own hint is honoured when it gives one.
_MAX_RATE_LIMIT_WAIT_S = 30.0
_FALLBACK_RATE_LIMIT_WAIT_S = 8.0

#: Free / free-tier OpenAI-compatible providers, in preference order. The
#: first one whose key is set supplies the endpoint *and* a default model, so
#: a demo can be stood up with a single key and nothing else configured. An
#: explicit ``OPENAI_BASE_URL`` (a local model, say) takes precedence over all
#: of them, and ``WATCHTOWER_MODEL`` always wins for the model name.
PROVIDERS: tuple[tuple[str, str, str], ...] = (
    ("GROQ_API_KEY", "https://api.groq.com/openai/v1", "openai/gpt-oss-20b"),
    ("CEREBRAS_API_KEY", "https://api.cerebras.ai/v1", "gpt-oss-120b"),
)

#: Call/token accounting, surfaced in the dashboard so the demo can show
#: that the model is actually being used.
usage: dict[str, int] = {
    "calls": 0,
    "failures": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
}

_client = None
_lock = threading.Lock()


def reset_usage() -> None:
    """Zero the counters (used between demo runs)."""
    with _lock:
        for key in usage:
            usage[key] = 0


def _parse_env(text: str) -> dict[str, str]:
    """Parse ``.env`` contents: ``#`` comments and blanks skipped, quotes stripped."""
    found: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        found[name.strip()] = value.strip().strip("\"'")
    return found


def _env_file() -> dict[str, str]:
    """``.env`` parsed into a mapping of name to value (empty if absent)."""
    path = Path(".env")
    if not path.exists():
        return {}
    return _parse_env(path.read_text(encoding="utf-8"))


def _credential(name: str) -> str | None:
    """One credential: the environment first, then ``.env``."""
    return os.getenv(name) or _env_file().get(name) or None


def _key_file() -> str | None:
    """An ``Open_AI_API_Key.txt`` in the working directory, if present."""
    path = Path("Open_AI_API_Key.txt")
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def _openai_key() -> str | None:
    """A plain OpenAI key: environment, ``.env``, then the key file."""
    return _credential("OPENAI_API_KEY") or _key_file()


def _first_provider() -> tuple[str, str, str] | None:
    """The first provider whose key is configured, if any."""
    for entry in PROVIDERS:
        if _credential(entry[0]):
            return entry
    return None


def _resolve() -> tuple[str | None, str | None, str]:
    """Resolve ``(key, base_url, model)`` from one consistent source.

    The key and the endpoint must always come from the *same* source: pairing
    an OpenAI key with a provider endpoint would just 401.
    """
    override = os.getenv("OPENAI_BASE_URL") or os.getenv("WATCHTOWER_BASE_URL")
    model_override = os.getenv("WATCHTOWER_MODEL")
    provider = _first_provider()
    if override:
        key = _openai_key() or (provider and _credential(provider[0]))
        return key, override, model_override or DEFAULT_MODEL
    if provider:
        return _credential(provider[0]), provider[1], model_override or provider[2]
    return _openai_key(), None, model_override or DEFAULT_MODEL


def api_key() -> str | None:
    """The credential the next call would use."""
    return _resolve()[0]


def base_url() -> str | None:
    """OpenAI-compatible endpoint, or ``None`` for the OpenAI API."""
    return _resolve()[1]


def model() -> str:
    """Configured model name."""
    return _resolve()[2]


def provider() -> str | None:
    """Name of the free provider in use (``groq``), else ``None``."""
    entry = _first_provider()
    if entry is None or os.getenv("OPENAI_BASE_URL") or os.getenv("WATCHTOWER_BASE_URL"):
        return None
    return entry[0].removesuffix("_API_KEY").lower()


def available() -> bool:
    """Is a model reachable in principle (package installed + key set)?"""
    try:
        import openai  # noqa: F401
    except ImportError:
        return False
    return bool(api_key())


def status() -> str:
    """One-line human summary of the model path, for the UI."""
    try:
        import openai  # noqa: F401
    except ImportError:
        return "AI off (install the ai extra)"
    if not api_key():
        return "AI off (no API key)"
    named = provider() or ("local" if base_url() else "openai")
    return f"AI on: {model()} @ {named}"


def _get_client():
    """Lazily build the client (import stays optional)."""
    global _client
    with _lock:
        if _client is None:
            from openai import OpenAI

            kwargs: dict = {"api_key": api_key(), "timeout": 120, "max_retries": 2}
            if base_url():
                kwargs["base_url"] = base_url()
            _client = OpenAI(**kwargs)
        return _client


def ask_json(
    system: str,
    user: str,
    schema: dict,
    *,
    name: str = "result",
    model_name: str | None = None,
    effort: str | None = None,
) -> dict | None:
    """Ask the model for JSON matching ``schema``; ``None`` on any failure.

    Callers treat ``None`` as "use your rules" - the fallback is a normal
    outcome, not an error, because it is what runs at a venue with no
    wifi.
    """
    if not available():
        return None
    payload_model = model_name or model()
    base_kwargs: dict = {
        "model": payload_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if effort:
        base_kwargs["reasoning_effort"] = effort

    # Free and self-hosted endpoints implement different slices of OpenAI's
    # structured-output surface, so the request degrades from strict JSON
    # Schema to best-effort schema to plain JSON mode instead of giving up and
    # silently falling back to rules. Only a schema-shaped rejection degrades;
    # anything else is treated as transient and retried.
    formats: list[dict] = [
        {
            "type": "json_schema",
            "json_schema": {"name": name, "schema": schema, "strict": True},
        },
        {
            "type": "json_schema",
            "json_schema": {"name": name, "schema": schema, "strict": False},
        },
        {"type": "json_object"},
    ]
    for index, response_format in enumerate(formats):
        kwargs = {**base_kwargs, "response_format": response_format}
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = _get_client().chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 - any failure means "use rules"
                message = str(exc)
                if _schema_rejected(message):
                    if index < len(formats) - 1:
                        log.info(
                            "llm %s: %s rejected %s, degrading",
                            name,
                            payload_model,
                            response_format["type"],
                        )
                        break  # deterministic rejection - don't retry it
                    # every output mode rejected: retrying changes nothing
                    with _lock:
                        usage["failures"] += 1
                    log.warning(
                        "llm %s: %s rejected every output mode: %s",
                        name,
                        payload_model,
                        message[:150],
                    )
                    return None
                if "reasoning_effort" in message and "reasoning_effort" in kwargs:
                    kwargs.pop("reasoning_effort")  # model doesn't take it
                    continue
                if attempt < MAX_ATTEMPTS - 1:
                    delay = (
                        _retry_after(message)
                        if _rate_limited(message)
                        else _BACKOFF_S[min(attempt, len(_BACKOFF_S) - 1)]
                    )
                    log.info(
                        "llm %s retrying %s in %.1fs: %s",
                        name,
                        payload_model,
                        delay,
                        message[:120],
                    )
                    time.sleep(delay)
                    continue
                with _lock:
                    usage["failures"] += 1
                log.warning(
                    "llm %s failed on %s: %s", name, payload_model, message[:200]
                )
                return None
            with _lock:
                usage["calls"] += 1
                tokens = getattr(response, "usage", None)
                if tokens is not None:
                    usage["prompt_tokens"] += getattr(tokens, "prompt_tokens", 0)
                    usage["completion_tokens"] += getattr(tokens, "completion_tokens", 0)
            content = response.choices[0].message.content
            try:
                parsed = json.loads(content)
            except (TypeError, json.JSONDecodeError):
                with _lock:
                    usage["failures"] += 1
                log.warning("llm %s returned unparseable JSON", name)
                return None
            return parsed if isinstance(parsed, dict) else None
    return None


def _rate_limited(message: str) -> bool:
    """Is this a throttle ("wait your turn") rather than a real failure?"""
    text = message.lower()
    return any(
        term in text
        for term in ("429", "rate limit", "quota", "too many requests")
    )


def _retry_after(message: str) -> float:
    """How long a provider asked us to wait, clipped to something sane."""
    match = re.search(r"try again in ([0-9.]+)\s*s", message, re.IGNORECASE)
    if match:
        return min(float(match.group(1)) + 0.5, _MAX_RATE_LIMIT_WAIT_S)
    return _FALLBACK_RATE_LIMIT_WAIT_S


def _schema_rejected(message: str) -> bool:
    """Did the endpoint refuse our structured-output request outright?"""
    text = message.lower()
    if not any(
        term in text
        for term in (
            "response_format",
            "json_schema",
            "json schema",
            "structured output",
            "schema",
        )
    ):
        return False
    return any(
        term in text for term in ("400", "invalid", "unsupported", "not supported")
    )


# --- schema helpers (JSON-schema fragments for the agents) ------------------


def obj(props: dict) -> dict:
    """A strict object schema over ``props``."""
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def arr(items: dict) -> dict:
    """An array schema of ``items``."""
    return {"type": "array", "items": items}


def enum(*values: str) -> dict:
    """A string schema restricted to ``values``."""
    return {"type": "string", "enum": list(values)}


STR: dict = {"type": "string"}
NUM: dict = {"type": "number"}
BOOL: dict = {"type": "boolean"}
INT: dict = {"type": "integer"}
