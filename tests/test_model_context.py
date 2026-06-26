# tests/test_model_context.py
from jiuwenswarm_instrumentor.model_context import get_max_context, sync_model_context, _load_bundled


def test_bundled_has_common_models():
    bundled = _load_bundled()
    assert "gpt-4o" in bundled
    assert bundled["gpt-4o"] == 128000
    assert "claude-3-5-sonnet-20241022" in bundled
    assert bundled["claude-3-5-sonnet-20241022"] == 200000
    assert "glm-4" in bundled


def test_get_max_context_exact():
    sync_model_context()  # init (uses bundled since no network in test env)
    assert get_max_context("gpt-4o") == 128000
    assert get_max_context("glm-4") == 128000
    assert get_max_context("deepseek-chat") == 65536


def test_get_max_context_prefix_match():
    """Dated version 'gpt-4o-2024-08-06' → longest prefix 'gpt-4o' → 128000."""
    sync_model_context()
    assert get_max_context("gpt-4o-2024-08-06") == 128000
    assert get_max_context("claude-3-5-sonnet-20241022-latest") == 200000


def test_get_max_context_unknown():
    sync_model_context()
    assert get_max_context("some-unknown-model") is None
    assert get_max_context("") is None
    assert get_max_context(None) is None


def test_sync_does_not_raise():
    """sync_model_context must not raise even if network/cache unavailable."""
    sync_model_context()
    sync_model_context()  # idempotent
