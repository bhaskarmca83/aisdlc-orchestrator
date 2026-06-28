"""sdlc_orchestrator/monitoring/tracker.py
Real-time event emission for the developer monitoring dashboard.
Events published to Redis Stream -> WebSocket -> Dashboard.
"""
import time
import json
from datetime import datetime, timezone
from contextlib import contextmanager
from dataclasses import dataclass, asdict

class EventType:
    INFO = "info"; TOOL = "tool"; LLM = "llm"
    GATE = "gate"; ERROR = "error"; DONE = "done"
    ROUTE = "route"; MEMORY = "memory"

@dataclass
class SDLCEvent:
    type: str; message: str; stage: str
    story_id: str; execution_id: str; timestamp: str
    metadata: dict = None
    def to_json(self): return json.dumps(asdict(self))

_ctx = {"execution_id": None, "stage": "idle", "story_id": None, "redis": None}

def init_tracker(execution_id: str, story_id: str):
    _ctx["execution_id"] = execution_id
    _ctx["story_id"] = story_id
    _ctx["stage"] = "init"

def set_stage(stage: str): _ctx["stage"] = stage

COLORS = {"info": "\033[36m", "tool": "\033[32m", "llm": "\033[35m",
           "gate": "\033[33m", "error": "\033[31m", "done": "\033[32m"}
RESET = "\033[0m"

def emit(event_type: str, message: str, metadata: dict = None):
    event = SDLCEvent(type=event_type, message=message,
        stage=_ctx.get("stage","unknown"), story_id=_ctx.get("story_id",""),
        execution_id=_ctx.get("execution_id",""),
        timestamp=datetime.now(timezone.utc).isoformat(), metadata=metadata or {})
    color = COLORS.get(event_type, "")
    ts = event.timestamp[11:19]
    print(f"{color}{ts} [{event_type.upper():6}] [{event.stage:15}] {message}{RESET}")

@contextmanager
def track_stage(stage_name: str, state: dict):
    set_stage(stage_name)
    start = time.perf_counter()
    emit(EventType.INFO, f"Stage [{stage_name}] started")
    try:
        yield
        elapsed = time.perf_counter() - start
        emit(EventType.DONE, f"Stage [{stage_name}] completed in {elapsed:.1f}s")
    except Exception as e:
        elapsed = time.perf_counter() - start
        emit(EventType.ERROR, f"Stage [{stage_name}] FAILED: {str(e)}")
        raise