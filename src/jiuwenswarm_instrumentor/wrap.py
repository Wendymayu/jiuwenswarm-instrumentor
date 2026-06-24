from __future__ import annotations
import logging

logger = logging.getLogger("jiuwenswarm_instrumentor")

_WRAPPED_ATTR = "_jiuwenswarm_wrapped"


def patch_method(cls, name, factory):
    """Monkey-patch cls.<name> with factory(original).

    factory(original_callable) -> replacement_callable. Sets the replacement on the class.
    Idempotent and fail-soft: returns True if applied, False if skipped (missing attr or
    already wrapped). Never raises into the host application.
    """
    original = getattr(cls, name, None)
    if original is None:
        logger.warning("[instrumentor] %s.%s not found — skipping", _qualname(cls), name)
        return False
    if getattr(original, _WRAPPED_ATTR, False):
        return False
    try:
        wrapper = factory(original)
    except Exception:
        logger.exception("[instrumentor] failed to build wrapper for %s.%s — skipping", _qualname(cls), name)
        return False
    setattr(wrapper, _WRAPPED_ATTR, True)
    setattr(wrapper, "__wrapped__", original)
    setattr(cls, name, wrapper)
    return True


def _qualname(cls):
    return f"{getattr(cls, '__module__', '?')}.{getattr(cls, '__qualname__', repr(cls))}"
