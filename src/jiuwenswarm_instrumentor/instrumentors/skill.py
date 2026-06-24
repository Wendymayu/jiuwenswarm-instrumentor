# src/jiuwenswarm_instrumentor/instrumentors/skill.py
"""Session-scoped skill duration state.

skill_tool (load) and skill_complete (release) execute in DIFFERENT asyncio tasks,
so ContextVars don't span them — this state is module-level (shared across tasks).
Keyed by (session_id, skill_name). Cleared on session end (instrumentors/session.py)
to avoid orphans if a skill is loaded but never released.
"""
from __future__ import annotations
import time

_skill_sessions: dict[tuple[str, str], float] = {}


def record_load(session_id: str, skill_name: str) -> None:
    try:
        if session_id and skill_name:
            _skill_sessions[(session_id, skill_name)] = time.monotonic()
    except Exception:
        pass


def pop_release(session_id: str, skill_name: str):
    try:
        if session_id and skill_name:
            return _skill_sessions.pop((session_id, skill_name), None)
    except Exception:
        pass
    return None


def clear_session(session_id: str) -> None:
    try:
        if not session_id:
            return
        for k in [k for k in _skill_sessions if k[0] == session_id]:
            _skill_sessions.pop(k, None)
    except Exception:
        pass
