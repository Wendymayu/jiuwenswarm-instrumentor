"""Tests for the site-level autoload hook (fired by jiuwenswarm_instrumentor.pth).

The .pth-fires-at-startup behavior itself can only be verified in a fresh
interpreter (covered by the manual/empirical verification in docs), but the
gating logic of `_autoload._autoload()` is unit-testable here.
"""
from __future__ import annotations

import importlib

import jiuwenswarm_instrumentor._autoload as al
from jiuwenswarm_instrumentor import activate as activate_mod


def test_runs_when_enabled(monkeypatch):
    """OTEL_INSTRUMENTOR_ENABLED=true and not opted out → activate() is called, return its result."""
    monkeypatch.delenv("JIUWENSWARM_INSTRUMENT_AUTOLOAD", raising=False)
    monkeypatch.setenv("OTEL_INSTRUMENTOR_ENABLED", "true")
    monkeypatch.setattr(activate_mod, "activate", lambda: True)
    assert al._autoload() is True


def test_noop_when_disabled(monkeypatch):
    """OTEL_INSTRUMENTOR_ENABLED unset → activate() reports disabled (returns False)."""
    monkeypatch.delenv("JIUWENSWARM_INSTRUMENT_AUTOLOAD", raising=False)
    monkeypatch.delenv("OTEL_INSTRUMENTOR_ENABLED", raising=False)
    monkeypatch.setattr(activate_mod, "activate", lambda: False)
    assert al._autoload() is False


def test_opt_out_suppresses_even_when_enabled(monkeypatch):
    """JIUWENSWARM_INSTRUMENT_AUTOLOAD=false must skip activate() entirely."""
    monkeypatch.setenv("JIUWENSWARM_INSTRUMENT_AUTOLOAD", "false")
    monkeypatch.setenv("OTEL_INSTRUMENTOR_ENABLED", "true")

    def _fail():  # pragma: no cover - must not be called
        raise AssertionError("activate() should not be called when opted out")

    monkeypatch.setattr(activate_mod, "activate", _fail)
    assert al._autoload() is False


def test_opt_out_accepts_aliases(monkeypatch):
    for v in ("0", "false", "no", "off", "FALSE", " No "):
        monkeypatch.setenv("JIUWENSWARM_INSTRUMENT_AUTOLOAD", v)
        monkeypatch.setattr(activate_mod, "activate", lambda: True)  # pragma: no cover
        assert al._autoload() is False, f"opt-out value {v!r} did not suppress"


def test_module_import_is_side_effect_safe(monkeypatch):
    """Re-importing the module must not raise even if activate would fail."""
    monkeypatch.delenv("OTEL_INSTRUMENTOR_ENABLED", raising=False)
    monkeypatch.delenv("JIUWENSWARM_INSTRUMENT_AUTOLOAD", raising=False)
    # Isolate from the real ~/.jiuwenclaw/config/.env: reload re-runs the
    # module-level _autoload() -> activate() -> load_env_for_instrumentor(),
    # which would load OTEL_INSTRUMENTOR_ENABLED=true from the real .env and set
    # _APPLIED=True, leaking into later tests.
    monkeypatch.setattr("jiuwenswarm_instrumentor._env.load_env_for_instrumentor", lambda: None)
    # importlib.reload re-runs the module-level _autoload() call; must not raise.
    importlib.reload(al)
