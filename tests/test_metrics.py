from unittest.mock import Mock
from jiuwenswarm_instrumentor.metrics import Metrics


def test_record_token_usage_calls_counter():
    meter = Mock()
    m = Metrics(meter)
    m.record_token_usage(input_tokens=10, output_tokens=5, attrs={"gen_ai.request.model": "gpt-x"})
    counter = meter.create_counter.return_value
    counter.add.assert_any_call(10, {"gen_ai.request.model": "gpt-x", "gen_ai.token.type": "input"})
    counter.add.assert_any_call(5, {"gen_ai.request.model": "gpt-x", "gen_ai.token.type": "output"})


def test_record_llm_duration_calls_histogram():
    meter = Mock()
    m = Metrics(meter)
    m.record_llm_duration(1.5, {"gen_ai.system": "openai"})
    meter.create_histogram.return_value.record.assert_called_once_with(1.5, {"gen_ai.system": "openai"})


def test_record_skill_call():
    from jiuwenswarm_instrumentor.metrics import Metrics
    meter = Mock()
    m = Metrics(meter)
    m.record_skill_call({"gen_ai.skill.name": "data_analysis"})
    meter.create_counter.return_value.add.assert_any_call(
        1, {"gen_ai.skill.name": "data_analysis"})


def test_record_skill_duration():
    from jiuwenswarm_instrumentor.metrics import Metrics
    meter = Mock()
    m = Metrics(meter)
    m.record_skill_duration(1.2, {"gen_ai.skill.name": "data_analysis"})
    meter.create_histogram.return_value.record.assert_any_call(
        1.2, {"gen_ai.skill.name": "data_analysis"})


def test_record_skill_error():
    from jiuwenswarm_instrumentor.metrics import Metrics
    meter = Mock()
    m = Metrics(meter)
    m.record_skill_error({"gen_ai.skill.name": "data_analysis"})
    meter.create_counter.return_value.add.assert_any_call(
        1, {"gen_ai.skill.name": "data_analysis"})


def test_skill_and_tool_token_usage_counters_created():
    from unittest.mock import Mock
    meter = Mock()
    Metrics(meter)
    names = [c.args[0] for c in meter.create_counter.call_args_list]
    assert "gen_ai.skill.token.usage" in names
    assert "gen_ai.tool.token.usage" in names


def test_token_usage_record_failsoft():
    from unittest.mock import Mock
    meter = Mock()
    m = Metrics(meter)
    m._skill_token_usage.add.side_effect = RuntimeError("boom")
    m._tool_token_usage.add.side_effect = RuntimeError("boom")
    m.record_skill_token_usage(5, {"gen_ai.skill.name": "s"})  # must not raise
    m.record_tool_token_usage(3, {"gen_ai.tool.name": "t"})    # must not raise


def test_context_compaction_counters_created():
    from unittest.mock import Mock
    meter = Mock()
    Metrics(meter)
    names = [c.args[0] for c in meter.create_counter.call_args_list]
    hists = [h.args[0] for h in meter.create_histogram.call_args_list]
    assert "gen_ai.context.compaction.count" in names
    assert "gen_ai.context.compaction.tokens_saved" in hists


def test_context_compaction_record_failsoft():
    from unittest.mock import Mock
    meter = Mock()
    m = Metrics(meter)
    m._context_compaction_count.add.side_effect = RuntimeError("boom")
    m._context_compaction_tokens_saved.record.side_effect = RuntimeError("boom")
    m.record_context_compaction(1, 50, {"context.compaction.path": "ADD"})  # must not raise
