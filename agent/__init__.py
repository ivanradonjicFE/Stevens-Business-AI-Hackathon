"""Multi-agent research system. Agents share state through store.py (the blackboard)."""
import time
from dataclasses import asdict
from datetime import datetime

import store
from collect import Event


class Agent:
    name = "agent"
    every = 0  # seconds between runs; 0 = every cycle

    def __init__(self):
        self._last = 0.0

    def due(self) -> bool:
        return time.time() - self._last >= self.every

    def mark(self):
        self._last = time.time()

    def log(self, action: str, detail=None, event_id: str | None = None):
        store.log(self.name, action, detail, event_id)
        msg = f"  [{self.name}]" + (f" ({event_id[:32]})" if event_id else "") + f" {action}"
        if isinstance(detail, str):
            msg += f": {detail}"
        print(msg, flush=True)


def to_dict(e: Event) -> dict:
    d = asdict(e)
    d["time"] = e.time.isoformat()
    return d


def to_event(d: dict) -> Event:
    return Event(**{**d, "time": datetime.fromisoformat(d["time"])})
