# tests/instrumentors/test_skill.py
from jiuwenswarm_instrumentor.instrumentors import skill


def test_record_load_then_pop_release():
    skill.clear_session("s1")
    skill.record_load("s1", "data_analysis")
    start = skill.pop_release("s1", "data_analysis")
    assert start is not None
    # second pop returns None (already popped)
    assert skill.pop_release("s1", "data_analysis") is None


def test_clear_session_clears_orphans():
    skill.clear_session("s2")
    skill.record_load("s2", "skill_a")
    skill.record_load("s2", "skill_b")
    skill.clear_session("s2")
    assert skill.pop_release("s2", "skill_a") is None
    assert skill.pop_release("s2", "skill_b") is None


def test_record_load_noop_on_empty():
    skill.clear_session("s3")
    skill.record_load("", "x")  # empty session_id -> noop
    skill.record_load("s3", "")  # empty skill_name -> noop
    assert skill.pop_release("s3", "x") is None


from unittest.mock import Mock
from opentelemetry import trace
from jiuwenswarm_instrumentor.instrumentors.tool import instrument_tool


class _SkillToolCall:
    def __init__(self, name, arguments="{}"):
        self.name = name
        self.id = "tc1"
        self.arguments = arguments


class _ToolMsg:
    def __init__(self, metadata=None):
        self.content = "ok"
        self.tool_call_id = "tc1"
        self.metadata = metadata or {}


class _Ctx:
    pass


class _Session:
    def get_session_id(self):
        return "sess-1"


def _fake_ability():
    class FakeAbility:
        async def execute_single(self, parent_ctx, tool_call, session, tag=None):
            if tool_call.name == "skill_tool":
                msg = _ToolMsg(metadata={
                    "is_skill_body": True,
                    "skill_name": "data_analysis",
                    "relative_file_path": "skills/data_analysis/SKILL.md",
                })
                return ("loaded", msg, _Ctx())
            return ("done", _ToolMsg(), _Ctx())
    return FakeAbility


async def test_skill_tool_load_enriches_span(exporter):
    skill.clear_session("sess-1")
    tracer = trace.get_tracer("t")
    metrics = Mock()
    Fake = _fake_ability()
    instrument_tool(tracer, metrics, ability_cls=Fake)
    await Fake().execute_single(_Ctx(), _SkillToolCall("skill_tool"), session=_Session())
    span = exporter.spans[0]
    assert span.name == "gen_ai.tool"
    assert span.attributes["gen_ai.operation.name"] == "load_skill"
    assert span.attributes["gen_ai.skill.name"] == "data_analysis"
    assert str(span.attributes["gen_ai.skill.id"]).startswith("skill_")
    assert any(e.name == "skill.loaded" for e in span.events)
    metrics.record_skill_call.assert_called_once()


async def test_skill_complete_release_enriches_span_and_duration(exporter):
    skill.clear_session("sess-1")
    tracer = trace.get_tracer("t")
    metrics = Mock()
    Fake = _fake_ability()
    inst = Fake()
    instrument_tool(tracer, metrics, ability_cls=Fake)
    await inst.execute_single(_Ctx(), _SkillToolCall("skill_tool"), session=_Session())
    await inst.execute_single(_Ctx(), _SkillToolCall(
        "skill_complete", arguments='{"skill_name": "data_analysis"}'), session=_Session())
    release_span = exporter.spans[1]
    assert release_span.attributes["gen_ai.operation.name"] == "release_skill"
    assert release_span.attributes["gen_ai.skill.name"] == "data_analysis"
    assert any(e.name == "skill.released" for e in release_span.events)
    metrics.record_skill_duration.assert_called_once()


async def test_session_cleanup_clears_skill_state(exporter):
    from jiuwenswarm_instrumentor.instrumentors.session import instrument_session
    from opentelemetry import trace
    tracer = trace.get_tracer("t")
    metrics = Mock()
    skill.clear_session("sess-9")
    skill.record_load("sess-9", "orphan_skill")  # loaded, never released

    class _FakeJW:
        _session_id = "sess-9"
        async def cleanup(self):
            pass
    instrument_session(tracer, metrics, jiuwenswarm_cls=_FakeJW)
    await _FakeJW().cleanup()
    # orphan must be cleared by session cleanup
    assert skill.pop_release("sess-9", "orphan_skill") is None
