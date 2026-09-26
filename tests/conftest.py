"""Shared test configuration.

The suite must be hermetic: :func:`watchtower.agents.llm.available` reads the
environment (and ``.env``), so a developer or CI with ``GROQ_API_KEY`` /
``OPENAI_API_KEY`` exported would otherwise make every council/orchestrator
test hit the network - spawning a real thread pool and turning deterministic
assertions into live API calls.

This autouse fixture pins the model path *off* by emptying its three sources
(provider environment variables, ``.env`` and the key file) rather than by
stubbing the resolver, so the credential-resolution logic stays testable.
Tests that exercise the model path opt back in by monkeypatching
``llm.available`` / ``llm.ask_json`` / ``llm._credential`` themselves, and those
monkeypatches - applied in the test body - win over this fixture.
"""

from __future__ import annotations

import pytest

from watchtower.agents import llm

#: Every credential this project knows how to read.
CREDENTIAL_VARS = (
    "OPENAI_API_KEY",
    *(entry[0] for entry in llm.PROVIDERS),
)


@pytest.fixture(autouse=True)
def _offline_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the model path off for every test unless a test opts in."""
    for name in CREDENTIAL_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm, "_env_file", lambda: {})
    monkeypatch.setattr(llm, "_key_file", lambda: None)
