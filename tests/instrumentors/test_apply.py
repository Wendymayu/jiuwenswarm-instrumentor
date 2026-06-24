# tests/instrumentors/test_apply.py
from unittest.mock import Mock, patch
from jiuwenswarm_instrumentor import activate


def test_activate_disabled_is_noop(monkeypatch):
    monkeypatch.delenv("OTEL_ENABLED", raising=False)
    with patch("jiuwenswarm_instrumentor.activate.init_providers", return_value=None) as ip:
        result = activate.activate()
    assert result is False
    ip.assert_not_called()


def test_apply_instrumentors_invokes_each(monkeypatch):
    calls = []
    for name in ("llm", "tool", "agent", "session"):
        monkeypatch.setattr(
            f"jiuwenswarm_instrumentor.instrumentors.{name}.instrument_{name}",
            lambda *a, _n=name, **k: calls.append(_n),
        )
    from jiuwenswarm_instrumentor.instrumentors import apply_instrumentors
    apply_instrumentors(tracer=object(), meter=Mock(), cfg=None)
    assert set(calls) == {"llm", "tool", "agent", "session"}
