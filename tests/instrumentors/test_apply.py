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


def test_main_strips_module_name_from_argv(monkeypatch):
    """`jiuwen-instrument <module> [args...]` must not leak the module name into the
    target module's argv (regression: app_agentserver's argparse rejected it)."""
    import sys
    monkeypatch.setattr(activate, "activate", lambda: None)  # no-op instrumentation
    seen = {}

    def fake_run_module(target, *, run_name=None, alter_sys=False):
        seen["target"] = target
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(activate.runpy, "run_module", fake_run_module)
    monkeypatch.setattr(sys, "argv", ["jiuwen-instrument", "jiuwenclaw.app_agentserver", "--port", "9999"])
    activate.main()
    assert seen["target"] == "jiuwenclaw.app_agentserver"
    assert seen["argv"][1:] == ["--port", "9999"]  # module name stripped, real args preserved
