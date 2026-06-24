from __future__ import annotations


class Metrics:
    """Owns OTel metric instruments + recording helpers."""

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

    def record_token_usage(self, input_tokens, output_tokens, attrs):
        base = dict(attrs)
        self._token_usage.add(int(input_tokens or 0), {**base, "gen_ai.token.type": "input"})
        self._token_usage.add(int(output_tokens or 0), {**base, "gen_ai.token.type": "output"})

    def record_llm_duration(self, seconds, attrs):
        self._llm_duration.record(seconds, attrs)

    def record_tool(self, seconds, attrs):
        self._tool_calls.add(1, attrs)
        self._tool_duration.record(seconds, attrs)

    def record_agent_duration(self, seconds, attrs):
        self._agent_duration.record(seconds, attrs)
