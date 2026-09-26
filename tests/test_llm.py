"""Tests for the optional LLM layer: provider resolution and degradation.

The key invariant is that the credential and the endpoint always come from the
same source - an OpenAI key sent to a Groq endpoint is a 401, not a fallback.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from watchtower.agents import llm


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test with no endpoint/model override in the environment."""
    for name in ("OPENAI_BASE_URL", "WATCHTOWER_BASE_URL", "WATCHTOWER_MODEL"):
        monkeypatch.delenv(name, raising=False)


def _creds(**keys: str):
    """A ``_credential`` stand-in that only knows ``keys``."""
    return lambda name: keys.get(name)


def test_groq_key_alone_configures_the_endpoint_and_model(monkeypatch) -> None:
    monkeypatch.setattr(llm, "_credential", _creds(GROQ_API_KEY="gsk_test"))
    assert llm.base_url() == "https://api.groq.com/openai/v1"
    assert llm.model() == "openai/gpt-oss-20b"
    assert llm.provider() == "groq"
    assert llm.api_key() == "gsk_test"
    assert llm.status() == "AI on: openai/gpt-oss-20b @ groq"


def test_openai_key_alone_keeps_the_default_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(llm, "_credential", _creds(OPENAI_API_KEY="sk-test"))
    assert llm.base_url() is None
    assert llm.model() == llm.DEFAULT_MODEL
    assert llm.provider() is None
    assert llm.status() == f"AI on: {llm.DEFAULT_MODEL} @ openai"


def test_provider_wins_and_its_key_is_never_paired_with_another_endpoint(
    monkeypatch,
) -> None:
    """Both keys set: the Groq key must go to the Groq endpoint, not OpenAI's."""
    monkeypatch.setattr(
        llm, "_credential", _creds(OPENAI_API_KEY="sk-dead", GROQ_API_KEY="gsk_test")
    )
    assert llm.api_key() == "gsk_test"
    assert llm.base_url() == "https://api.groq.com/openai/v1"
    assert llm.provider() == "groq"


def test_explicit_base_url_wins_over_a_provider(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(llm, "_credential", _creds(OPENAI_API_KEY="ollama"))
    assert llm.base_url() == "http://localhost:11434/v1"
    assert llm.provider() is None  # a local model, not a named provider
    assert llm.status() == f"AI on: {llm.DEFAULT_MODEL} @ local"


def test_explicit_base_url_can_borrow_a_provider_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(llm, "_credential", _creds(GROQ_API_KEY="gsk_test"))
    assert llm.api_key() == "gsk_test"
    assert llm.base_url() == "https://example.test/v1"


def test_model_override_beats_the_provider_default(monkeypatch) -> None:
    monkeypatch.setenv("WATCHTOWER_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setattr(llm, "_credential", _creds(GROQ_API_KEY="gsk_test"))
    assert llm.model() == "openai/gpt-oss-120b"


def test_no_credentials_anywhere_means_the_model_path_is_off(monkeypatch) -> None:
    monkeypatch.setattr(llm, "_credential", _creds())
    assert llm.api_key() is None
    assert llm.available() is False
    assert llm.status() == "AI off (no API key)"


def test_env_file_parses_and_strips_quotes() -> None:
    parsed = llm._parse_env('GROQ_API_KEY="gsk_from_dotenv"\n# comment\n\nBARE=1\n')
    assert parsed == {"GROQ_API_KEY": "gsk_from_dotenv", "BARE": "1"}


def test_credential_prefers_the_environment_then_dotenv(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_from_env")
    assert llm._credential("GROQ_API_KEY") == "gsk_from_env"
    monkeypatch.delenv("GROQ_API_KEY")
    monkeypatch.setattr(llm, "_env_file", lambda: {"GROQ_API_KEY": "gsk_from_dotenv"})
    assert llm._credential("GROQ_API_KEY") == "gsk_from_dotenv"
    assert llm.api_key() == "gsk_from_dotenv"


class _FakeCompletions:
    """Records the response_format of each call, then replays ``responses``."""

    def __init__(self, responses: list):
        self.responses = responses
        self.formats: list[dict] = []

    def create(self, **kwargs):
        self.formats.append(kwargs["response_format"])
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok(payload: str) -> SimpleNamespace:
    message = SimpleNamespace(content=payload)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def _client(monkeypatch, responses: list) -> _FakeCompletions:
    """A fake OpenAI client whose ``chat.completions`` replays ``responses``."""
    completions = _FakeCompletions(responses)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    return completions


def test_ask_json_degrades_when_strict_json_schema_is_rejected(monkeypatch) -> None:
    """A provider without strict mode should still answer, not fall back."""
    client = _client(
        monkeypatch,
        [
            Exception("Error code: 400 - response_format json_schema unsupported"),
            _ok('{"relevant": true}'),
        ],
    )
    result = llm.ask_json("system", "user", llm.obj({"relevant": llm.BOOL}))
    assert result == {"relevant": True}
    assert client.formats[0]["json_schema"]["strict"] is True
    assert client.formats[1]["json_schema"]["strict"] is False


def test_ask_json_reports_plain_json_mode_or_nothing(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        [
            Exception("400 invalid response_format"),
            Exception("400 unsupported json_schema"),
            _ok('{"ok": 1}'),
        ],
    )
    result = llm.ask_json("system", "user", llm.obj({"ok": llm.NUM}))
    assert result == {"ok": 1}
    assert [f["type"] for f in client.formats] == [
        "json_schema",
        "json_schema",
        "json_object",
    ]


def test_schema_rejection_detection() -> None:
    assert llm._schema_rejected("Error code: 400 - invalid response_format")
    assert llm._schema_rejected("json_schema is not supported")
    # a transient error is not a schema rejection
    assert not llm._schema_rejected("Error code: 429 - rate limit exceeded")


def test_rate_limit_detection() -> None:
    assert llm._rate_limited("Error code: 429 - rate limit reached")
    assert llm._rate_limited("insufficient quota")
    assert llm._rate_limited("429 Too Many Requests")
    assert not llm._rate_limited("Error code: 400 - bad request")


def test_rate_limit_wait_honours_the_hint_and_is_capped() -> None:
    assert llm._retry_after("please try again in 12.5s") == 13.0
    assert llm._retry_after("try again in 600s") == llm._MAX_RATE_LIMIT_WAIT_S
    assert llm._retry_after("no hint here") == llm._FALLBACK_RATE_LIMIT_WAIT_S


def test_ask_json_waits_out_a_rate_limit_then_succeeds(monkeypatch) -> None:
    """A throttle is a "wait your turn", not a reason to use the rules."""
    slept: list[float] = []
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: slept.append(seconds))
    _client(
        monkeypatch,
        [
            Exception("Error code: 429 - rate limit reached, try again in 12.5s"),
            _ok('{"ok": 1}'),
        ],
    )
    assert llm.ask_json("system", "user", llm.obj({"ok": llm.NUM})) == {"ok": 1}
    assert slept == [13.0]


def test_ask_json_fails_fast_when_every_output_mode_is_rejected(monkeypatch) -> None:
    """A deterministic 400 is not retried - there is nothing left to try."""
    slept: list[float] = []
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: slept.append(seconds))
    _client(monkeypatch, [Exception("400 invalid response_format")] * 3)
    assert llm.ask_json("system", "user", llm.obj({"ok": llm.NUM})) is None
    assert slept == []
