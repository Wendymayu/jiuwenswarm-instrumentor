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
