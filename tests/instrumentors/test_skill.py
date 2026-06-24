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
