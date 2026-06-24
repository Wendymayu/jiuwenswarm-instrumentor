from __future__ import annotations
from contextvars import ContextVar
from typing import Any

from jiuwenswarm_instrumentor import attributes as A

_request_context: ContextVar[dict | None] = ContextVar("jiuwenswarm_request_context", default=None)


class _RequestContextToken:
    """Handle returned by :func:`set_request_context`.

    Call :meth:`reset` to restore the context to its prior state. This wraps a
    ``contextvars.Token`` so callers can reset via the returned handle rather
    than reaching back into this module's private ``ContextVar``.
    """

    __slots__ = ("_var", "_token")

    def __init__(self, var: ContextVar, token: Any) -> None:
        self._var = var
        self._token = token

    def reset(self) -> None:
        """Restore the request context to its state before ``set_request_context``."""
        self._var.reset(self._token)


def set_request_context(*, session_id=None, channel_id=None, request_id=None, agent_name=None):
    """Set request/session/channel/agent ids in the current context.

    Values are merged on top of any existing context (so callers can set ids
    incrementally). Returns a handle whose ``reset()`` restores the prior
    context — typically used in a ``finally`` block.
    """
    current = dict(_request_context.get() or {})
    if session_id is not None:
        current[A.JIUWENCLAW_SESSION_ID] = session_id
    if channel_id is not None:
        current[A.JIUWENCLAW_CHANNEL_ID] = channel_id
    if request_id is not None:
        current[A.JIUWENCLAW_REQUEST_ID] = request_id
    if agent_name is not None:
        current[A.JIUWENCLAW_AGENT_NAME] = agent_name
    return _RequestContextToken(_request_context, _request_context.set(current))


def current_request_attrs() -> dict:
    """Return a copy of the current request context attrs (empty dict if unset)."""
    return dict(_request_context.get() or {})
