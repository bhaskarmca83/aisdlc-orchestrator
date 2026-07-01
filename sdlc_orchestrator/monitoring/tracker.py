"""sdlc_orchestrator/monitoring/tracker.py
Real-time event emission + structured JSON logging.
  - Colorized stdout  →  local dev terminal
  - JSON file         →  Promtail → Loki → Grafana
"""
import time
import json
import logging
from datetime import datetime, timezone
from contextlib import contextmanager
from dataclasses import dataclass, asdict

from sdlc_orchestrator.monitoring.logger import get_logger

_log = get_logger("sdlc.tracker")


class EventType:
    INFO   = "info"
    TOOL   = "tool"
    LLM    = "llm"
    GATE   = "gate"
    ERROR  = "error"
    DONE   = "done"
    ROUTE  = "route"
    MEMORY = "memory"


@dataclass
class SDLCEvent:
    type:         str
    message:      str
    stage:        str
    story_id:     str
    execution_id: str
    timestamp:    str
    metadata:     dict = None

    def to_json(self):
        return json.dumps(asdict(self))


_ctx: dict = {
    "execution_id": None,
    "stage":        "idle",
    "story_id":     None,
    "agent":        "",
}

_COLORS = {
    "info":   "\033[36m",
    "tool":   "\033[32m",
    "llm":    "\033[35m",
    "gate":   "\033[33m",
    "error":  "\033[31m",
    "done":   "\033[32m",
    "route":  "\033[34m",
    "memory": "\033[37m",
}
_RESET = "\033[0m"


def init_tracker(execution_id: str, story_id: str) -> None:
    _ctx["execution_id"] = execution_id
    _ctx["story_id"]     = story_id
    _ctx["stage"]        = "init"
    _ctx["agent"]        = ""


def set_stage(stage: str) -> None:
    _ctx["stage"] = stage


def set_agent(agent: str) -> None:
    _ctx["agent"] = agent


def emit(event_type: str, message: str, metadata: dict = None, duration_ms: float = None) -> None:
    now   = datetime.now(timezone.utc)
    ts    = now.isoformat()
    stage = _ctx.get("stage", "unknown")
    agent = _ctx.get("agent", "")

    # ── Colorized stdout (dev terminal) ───────────────────────────────────────
    color = _COLORS.get(event_type, "")
    print(f"{color}{ts[11:19]} [{event_type.upper():6}] [{stage:15}] {message}{_RESET}")

    # ── Structured JSON log (→ Promtail → Loki) ───────────────────────────────
    extra = {
        "event_type":   event_type,
        "stage":        stage,
        "agent":        agent,
        "execution_id": _ctx.get("execution_id", ""),
        "story_id":     _ctx.get("story_id", ""),
    }
    if duration_ms is not None:
        extra["duration_ms"] = round(duration_ms)
    if metadata:
        extra.update({f"meta_{k}": v for k, v in metadata.items()})

    level = logging.ERROR if event_type == EventType.ERROR else logging.INFO
    _log.log(level, message, extra=extra)


@contextmanager
def track_stage(stage_name: str, state: dict, agent_name: str = ""):
    set_stage(stage_name)
    if agent_name:
        set_agent(agent_name)
    start = time.perf_counter()
    emit(EventType.INFO, f"Stage [{stage_name}] started")
    try:
        yield
        elapsed_ms = (time.perf_counter() - start) * 1000
        emit(EventType.DONE, f"Stage [{stage_name}] completed", duration_ms=elapsed_ms)
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        emit(EventType.ERROR, f"Stage [{stage_name}] FAILED: {e}", duration_ms=elapsed_ms)
        raise
    finally:
        set_agent("")
