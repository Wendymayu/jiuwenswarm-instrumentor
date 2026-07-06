# tests/test_env.py
"""Tests for _env.load_env_for_instrumentor — the .env bridge that lets OTEL_*
vars placed in jiuwenswarm's .env reach the instrumentor when activated at
interpreter startup (before jiuwenswarm's own load_dotenv runs).
"""
from __future__ import annotations

import os

import pytest

dotenv = pytest.importorskip("dotenv")  # skip all if python-dotenv absent

from jiuwenswarm_instrumentor._env import load_env_for_instrumentor  # noqa: E402


def _write_env(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _clean_data_dir_env(monkeypatch):
    for k in ("JIUWENSWARM_DATA_DIR", "JIUWENCLAW_DATA_DIR",
              "JIUWENSWARM_INSTRUMENT_ENV_FILE"):
        monkeypatch.delenv(k, raising=False)


def test_loads_jiuwenswarm_env_file_via_data_dir(tmp_path, monkeypatch):
    """OTEL_* in <JIUWENSWARM_DATA_DIR>/config/.env must reach os.environ."""
    _clean_data_dir_env(monkeypatch)
    env = tmp_path / "config" / ".env"
    _write_env(env, "OTEL_ENABLED=true\nOTEL_SERVICE_NAME=from-dotenv\n")
    monkeypatch.setenv("JIUWENSWARM_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OTEL_ENABLED", raising=False)
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)

    load_env_for_instrumentor()

    assert os.environ.get("OTEL_ENABLED") == "true"
    assert os.environ.get("OTEL_SERVICE_NAME") == "from-dotenv"


def test_loads_jiuwenclaw_env_file_fallback(tmp_path, monkeypatch):
    """jiuwenclaw data dir is still honored as a fallback (enterprise/old name)."""
    _clean_data_dir_env(monkeypatch)
    env = tmp_path / "config" / ".env"
    _write_env(env, "OTEL_ENABLED=true\n")
    monkeypatch.setenv("JIUWENCLAW_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OTEL_ENABLED", raising=False)

    load_env_for_instrumentor()

    assert os.environ.get("OTEL_ENABLED") == "true"


def test_explicit_override_path_wins(tmp_path, monkeypatch):
    """JIUWENSWARM_INSTRUMENT_ENV_FILE points at an explicit .env to load."""
    _clean_data_dir_env(monkeypatch)
    env = tmp_path / "custom.env"
    _write_env(env, "OTEL_TRACES_EXPORTER=console\n")
    monkeypatch.setenv("JIUWENSWARM_INSTRUMENT_ENV_FILE", str(env))
    # Point JIUWENSWARM_DATA_DIR at a dir with NO .env so the data-dir path is absent.
    monkeypatch.setenv("JIUWENSWARM_DATA_DIR", str(tmp_path / "nope"))
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)

    load_env_for_instrumentor()

    assert os.environ.get("OTEL_TRACES_EXPORTER") == "console"


def test_does_not_clobber_existing_shell_var(tmp_path, monkeypatch):
    """override=False: a var already in os.environ must NOT be overwritten by .env."""
    _clean_data_dir_env(monkeypatch)
    env = tmp_path / "config" / ".env"
    _write_env(env, "OTEL_SERVICE_NAME=from-dotenv\n")
    monkeypatch.setenv("JIUWENSWARM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OTEL_SERVICE_NAME", "from-shell")

    load_env_for_instrumentor()

    assert os.environ.get("OTEL_SERVICE_NAME") == "from-shell"


def test_missing_file_is_silent(tmp_path, monkeypatch):
    """No .env present → no error, no spurious vars."""
    _clean_data_dir_env(monkeypatch)
    # Point BOTH data dirs at absent dirs so neither the jiuwenswarm primary nor
    # the ~/.jiuwenclaw fallback loads the user's real .env (which would set
    # OTEL_ENABLED and break this assertion on a dev machine).
    absent = str(tmp_path / "absent")
    monkeypatch.setenv("JIUWENSWARM_DATA_DIR", absent)
    monkeypatch.setenv("JIUWENCLAW_DATA_DIR", absent)
    monkeypatch.delenv("OTEL_ENABLED", raising=False)
    load_env_for_instrumentor()  # must not raise
    assert os.environ.get("OTEL_ENABLED") is None


def test_candidate_paths_resolve_data_dir(monkeypatch):
    from jiuwenswarm_instrumentor._env import _candidate_env_files
    _clean_data_dir_env(monkeypatch)
    monkeypatch.setenv("JIUWENSWARM_DATA_DIR", "/opt/jws")
    paths = _candidate_env_files()
    assert any(p.replace("\\", "/").endswith("/opt/jws/config/.env") for p in paths)


def test_candidate_paths_default_home(monkeypatch):
    from jiuwenswarm_instrumentor._env import _candidate_env_files
    _clean_data_dir_env(monkeypatch)
    paths = _candidate_env_files()
    # jiuwenswarm primary
    assert any(p.replace("\\", "/").endswith("/.jiuwenswarm/config/.env") for p in paths)
    # jiuwenclaw fallback
    assert any(p.replace("\\", "/").endswith("/.jiuwenclaw/config/.env") for p in paths)
