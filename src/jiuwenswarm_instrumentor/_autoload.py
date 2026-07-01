# src/jiuwenswarm_instrumentor/_autoload.py
"""Site-level autoload hook, fired by ``jiuwenswarm_instrumentor.pth`` at interpreter startup.

Imported once per Python process by the ``.pth`` file that ships with this package
(it lands in ``site-packages`` on a non-editable install). This is what makes
**split-process** apps auto-instrumented with zero source edits: ``jiuwenclaw.app``
forks ``app_agentserver`` / ``app_gateway`` via ``subprocess.Popen([python, "-m", ...])``;
each child inherits the ``OTEL_*`` env and runs this hook at startup — before any
jiuwenclaw/openjiuwen module is imported, which satisfies the openjiuwen agent metaclass
caveat (patch the class before any instance is built).

Gating (two switches, both opt-in/out so a default install is a true zero-cost no-op):

* ``OTEL_ENABLED=true`` — the package's existing enable switch. ``activate()`` itself
  returns ``False`` without it, so an unset env means no providers, no patches, no network.
* ``JIUWENSWARM_INSTRUMENT_AUTOLOAD=false`` (``0``/``no``/``off``) — explicit opt-out for
  users who set ``OTEL_ENABLED=true`` but drive instrumentation via the
  ``jiuwen-instrument`` CLI wrapper instead, and don't want every Python process in the
  venv to auto-activate.

Fail-soft: any exception is swallowed — a telemetry bootstrap failure must never crash
the host application.
"""
from __future__ import annotations

import os

_OPT_OUT = ("0", "false", "no", "off")


def _autoload() -> bool:
    """Run ``activate()`` unless explicitly opted out. Returns activate()'s result."""
    if os.getenv("JIUWENSWARM_INSTRUMENT_AUTOLOAD", "").strip().lower() in _OPT_OUT:
        return False
    # Late import: keeps the .pth import cheap when disabled, and avoids a hard failure
    # if the package is only partially importable in some stripped-down env.
    from jiuwenswarm_instrumentor.activate import activate

    return activate()


# Fired by the .pth line: `import jiuwenswarm_instrumentor._autoload`
try:
    _autoload()
except Exception:  # noqa: BLE001  — site-level hook, must never raise into host
    pass
