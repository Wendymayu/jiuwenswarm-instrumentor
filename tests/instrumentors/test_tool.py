# tests/instrumentors/test_tool.py
from unittest.mock import Mock
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from jiuwenswarm_instrumentor.instrumentors.tool import instrument_tool
from jiuwenswarm_instrumentor.metrics import Metrics


class _ToolCall:
    name = "search"; id = "tc1"; arguments = '{"q": "x"}'


class _ToolMsg:
    def __init__(self):
        self.content = "ok"; self.tool_call_id = "tc1"; self.metadata = {}


def _fake_ability_cls():
    class FakeAbility:
        async def _execute_single_tool_call(self, tool_call, session, tag=None):
            return ("result", _ToolMsg())
    return FakeAbility


def _fake_ability_error_cls():
    class FakeAbilityErr:
        async def _execute_single_tool_call(self, tool_call, session, tag=None):
            msg = _ToolMsg()
            msg.metadata = {"is_error": True}
            return ("err", msg)
    return FakeAbilityErr


async def test_tool_span(exporter):
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _fake_ability_cls()
    instrument_tool(tracer, metrics, ability_cls=Fake)
    res = await Fake()._execute_single_tool_call(_ToolCall(), session=None)
    assert res[1].content == "ok"
    assert len(exporter.spans) == 1
    span = exporter.spans[0]
    assert span.name == "gen_ai.tool"
    assert span.attributes["gen_ai.tool.name"] == "search"
    assert span.attributes["gen_ai.tool.call.id"] == "tc1"
    assert span.status.status_code == StatusCode.OK


async def test_tool_error_sets_error_status(exporter):
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _fake_ability_error_cls()
    instrument_tool(tracer, metrics, ability_cls=Fake)
    await Fake()._execute_single_tool_call(_ToolCall(), session=None)
    span = exporter.spans[0]
    assert span.name == "gen_ai.tool"
    assert span.status.status_code == StatusCode.ERROR
