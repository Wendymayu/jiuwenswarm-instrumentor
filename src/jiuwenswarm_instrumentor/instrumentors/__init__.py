# src/jiuwenswarm_instrumentor/instrumentors/__init__.py
from __future__ import annotations
import logging

from jiuwenswarm_instrumentor.instrumentors import llm, tool, agent, session
from jiuwenswarm_instrumentor.metrics import Metrics

logger = logging.getLogger("jiuwenswarm_instrumentor")


def apply_instrumentors(tracer, meter, cfg):
    """Apply all instrumentors. Fail-soft per instrumentor."""
    metrics = Metrics(meter)
    log_messages = getattr(cfg, "log_messages", False)
    for label, fn in (
        ("llm", lambda: llm.instrument_llm(tracer, metrics, log_messages=log_messages)),
        ("tool", lambda: tool.instrument_tool(tracer, metrics)),
        ("agent", lambda: agent.instrument_agent(tracer, metrics)),
        ("session", lambda: session.instrument_session(tracer, metrics)),
    ):
        try:
            fn()
            logger.info("[instrumentor] applied %s", label)
        except Exception:
            logger.exception("[instrumentor] failed to apply %s — skipping", label)
