"""Settings for the agent system. Override any of these with environment variables."""
import os
from pathlib import Path

import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())  # python.org macOS builds ship without CA certs

ROOT = Path(__file__).parent
OUT = ROOT / "out"
DB_PATH = ROOT / "state.db"

# Models: a cheap fast one for high-volume triage, a strong one for research / analysis / critique / writing
FAST_MODEL = os.getenv("FAST_MODEL", "gpt-5.4-mini")
DEEP_MODEL = os.getenv("DEEP_MODEL", "gpt-5.5")

# Schedules (seconds)
CYCLE_SECONDS = int(os.getenv("CYCLE_SECONDS", 300))          # orchestrator heartbeat
HAZARD_SCOUT_EVERY = int(os.getenv("HAZARD_SCOUT_EVERY", 600))
NEWS_SCOUT_EVERY = int(os.getenv("NEWS_SCOUT_EVERY", 900))
HAZARD_LOOKBACK_DAYS = int(os.getenv("HAZARD_LOOKBACK_DAYS", 10))

# Cost / load guards
MAX_EVENTS_ANALYZED_PER_CYCLE = int(os.getenv("MAX_EVENTS_ANALYZED_PER_CYCLE", 3))
TRIAGE_BATCH = 40
MAX_CRITIC_ROUNDS = 1                                          # analyst gets one revision if the critic objects
NOTIFY_MIN_LEVEL = os.getenv("NOTIFY_MIN_LEVEL", "WATCH")      # ADVISORY / WATCH / WARNING
LEVELS = ["ADVISORY", "WATCH", "WARNING"]


def openai_key() -> str | None:
    """OPENAI_API_KEY env var, else a line in .env, else the key file in the project folder."""
    if os.getenv("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    f = ROOT / "Open_AI_API_Key.txt"
    return f.read_text().strip() if f.exists() else None
