# src/jiuwenswarm_instrumentor/metrics.py
from __future__ import annotations
import logging

logger = logging.getLogger("jiuwenswarm_instrumentor")


class Metrics:
    """Owns OTel metric instruments + recording helpers.

    All record_* methods are fail-soft: a telemetry-side failure is logged and
    swallowed so it never propagates into the host application (spec §3/§7).
    """

    def __init__(self, meter):
        self._token_usage = meter.create_counter(
            "gen_ai.client.token.usage", unit="{token}",
            description="Tokens consumed by GenAI model calls, by type",
        )
        self._llm_duration = meter.create_histogram(
            "gen_ai.client.operation.duration", unit="s",
            description="LLM call duration in seconds",
        )
        self._tool_duration = meter.create_histogram(
            "gen_ai.tool.duration", unit="s",
            description="Tool execution duration in seconds",
        )
        self._tool_calls = meter.create_counter(
            "gen_ai.tool.count", unit="1",
            description="Number of tool executions",
        )
        self._agent_duration = meter.create_histogram(
            "gen_ai.agent.duration", unit="s",
            description="Agent invocation duration in seconds",
        )
        self._skill_call_count = meter.create_counter(
            "gen_ai.skill.call.count", unit="{call}",
            description="Skill activation count (skill_tool)",
        )
        self._skill_duration = meter.create_histogram(
            "gen_ai.skill.duration", unit="s",
            description="Skill execution duration (load→release)",
        )
        self._skill_error_count = meter.create_counter(
            "gen_ai.skill.error.count", unit="{call}",
            description="Skill execution error count",
        )
        self._skill_token_usage = meter.create_counter(
            "gen_ai.skill.token.usage", unit="{token}",
            description="Skill content tokens in context (body+pin), by skill",
        )
        self._tool_token_usage = meter.create_counter(
            "gen_ai.tool.token.usage", unit="{token}",
            description="Tool-definition tokens in context, by tool",
        )
        self._context_compaction_count = meter.create_counter(
            "gen_ai.context.compaction.count", unit="{event}",
            description="Context compaction episodes, by path+processor_type",
        )
        self._context_compaction_tokens_saved = meter.create_histogram(
            "gen_ai.context.compaction.tokens_saved", unit="{token}",
            description="Tokens saved per context compaction (GET=gross, ADD=net approx), by path+processor_type",
        )

    def record_token_usage(self, input_tokens, output_tokens, attrs):
        try:
            base = dict(attrs)
            self._token_usage.add(int(input_tokens or 0), {**base, "gen_ai.token.type": "input"})
            self._token_usage.add(int(output_tokens or 0), {**base, "gen_ai.token.type": "output"})
        except Exception:
            logger.debug("[instrumentor] token usage metric failed", exc_info=True)

    def record_llm_duration(self, seconds, attrs):
        try:
            self._llm_duration.record(seconds, attrs)
        except Exception:
            logger.debug("[instrumentor] llm duration metric failed", exc_info=True)

    def record_tool(self, seconds, attrs):
        try:
            self._tool_calls.add(1, attrs)
            self._tool_duration.record(seconds, attrs)
        except Exception:
            logger.debug("[instrumentor] tool metric failed", exc_info=True)

    def record_agent_duration(self, seconds, attrs):
        try:
            self._agent_duration.record(seconds, attrs)
        except Exception:
            logger.debug("[instrumentor] agent metric failed", exc_info=True)

    def record_skill_call(self, attrs):
        try:
            self._skill_call_count.add(1, attrs)
        except Exception:
            logger.debug("[instrumentor] skill call metric failed", exc_info=True)

    def record_skill_duration(self, seconds, attrs):
        try:
            self._skill_duration.record(seconds, attrs)
        except Exception:
            logger.debug("[instrumentor] skill duration metric failed", exc_info=True)

    def record_skill_error(self, attrs):
        try:
            self._skill_error_count.add(1, attrs)
        except Exception:
            logger.debug("[instrumentor] skill error metric failed", exc_info=True)

    def record_skill_token_usage(self, tokens, attrs):
        try:
            self._skill_token_usage.add(int(tokens or 0), attrs)
        except Exception:
            logger.debug("[instrumentor] skill token usage metric failed", exc_info=True)

    def record_tool_token_usage(self, tokens, attrs):
        try:
            self._tool_token_usage.add(int(tokens or 0), attrs)
        except Exception:
            logger.debug("[instrumentor] tool token usage metric failed", exc_info=True)

    def record_context_compaction(self, count, tokens_saved, attrs):
        try:
            self._context_compaction_count.add(int(count or 0), attrs)
            self._context_compaction_tokens_saved.record(int(tokens_saved or 0), attrs)
        except Exception:
            logger.debug("[instrumentor] context compaction metric failed", exc_info=True)
