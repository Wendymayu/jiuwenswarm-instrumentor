# src/jiuwenswarm_instrumentor/instrumentors/__init__.py
from __future__ import annotations
import logging

from jiuwenswarm_instrumentor.instrumentors import llm, tool, agent, session, subagent, logs, gateway, agentserver, context_compaction
from jiuwenswarm_instrumentor.metrics import Metrics

logger = logging.getLogger("jiuwenswarm_instrumentor")


def apply_instrumentors(tracer, meter, cfg):
    """Apply all instrumentors. Fail-soft per instrumentor."""
    metrics = Metrics(meter)
    log_messages = getattr(cfg, "log_messages", False)
    for label, fn in (
        ("llm", lambda: llm.instrument_llm(tracer, metrics, log_messages=log_messages, message_max_length=getattr(cfg, "message_max_length", 4096))),
        ("tool", lambda: tool.instrument_tool(tracer, metrics, log_messages=log_messages, message_max_length=getattr(cfg, "message_max_length", 4096))),
        ("agent", lambda: agent.instrument_agent(tracer, metrics)),
        ("session", lambda: session.instrument_session(tracer, metrics)),
        ("subagent", lambda: subagent.instrument_subagent(tracer, metrics)),
    ):
        try:
            fn()
            logger.info("[instrumentor] applied %s", label)
        except Exception:
            logger.exception("[instrumentor] failed to apply %s — skipping", label)

    if getattr(cfg, "logs_exporter", "none") != "none":
        try:
            logs.instrument_logs(
                level=getattr(cfg, "log_level", "INFO"),
                excluded_loggers=getattr(cfg, "log_excluded_loggers", ()),
                message_max_length=getattr(cfg, "log_message_max_length", 8192),
            )
            logger.info("[instrumentor] applied logs")
        except Exception:
            logger.exception("[instrumentor] failed to apply logs — skipping")

    if getattr(cfg, "traces_exporter", "none") != "none":
        try:
            gateway.instrument_gateway(tracer)
            logger.info("[instrumentor] applied gateway")
        except Exception:
            logger.exception("[instrumentor] failed to apply gateway — skipping")
        try:
            agentserver.instrument_agentserver(tracer)
            logger.info("[instrumentor] applied agentserver")
        except Exception:
            logger.exception("[instrumentor] failed to apply agentserver — skipping")
        try:
            context_compaction.instrument_context_compaction(tracer, metrics)
            logger.info("[instrumentor] applied context_compaction")
        except Exception:
            logger.exception("[instrumentor] failed to apply context_compaction — skipping")
