# src/jiuwenswarm_instrumentor/_env.py
"""Load jiuwenclaw's ``.env`` before the instrumentor reads ``os.environ``.

Why this exists: the ``.pth`` autoload (and the ``jiuwen-instrument`` CLI wrapper)
call ``activate()`` at interpreter startup / before the target module runs. But
jiuwenclaw loads its own ``.env`` via ``load_dotenv(get_env_file())`` *inside*
``app_agentserver`` / ``app_gateway`` / ``app`` ``main()`` — i.e. **after** the
instrumentor already read ``os.environ``. So any ``OTEL_*`` vars placed in
jiuwenclaw's ``.env`` (the natural, jiuwenclaw-conventional place) were invisible
to instrumentation, and nothing got patched/exported.

Fix: ``activate()`` calls :func:`load_env_for_instrumentor` before
:func:`load_config`. This replicates jiuwenclaw's env-file path lookup **without
importing jiuwenclaw** (importing ``jiuwenclaw.utils`` costs ~1 s and would run on
every Python startup via the ``.pth``), then ``load_dotenv(path, override=False)``
so shell-exported vars still win and ``.env`` only fills gaps.

Fail-soft: missing dotenv, missing file, or any error → silent skip. Never raises
into the host application.

Path resolution mirrors ``jiuwenclaw.utils.get_env_file``:
  - ``$JIUWENCLAW_DATA_DIR/config/.env`` if ``JIUWENCLAW_DATA_DIR`` is set
  - else ``~/.jiuwenclaw/config/.env``
An explicit ``JIUWENSWARM_INSTRUMENT_ENV_FILE`` overrides everything.
"""
from __future__ import annotations

import os


def _candidate_env_files() -> list[str]:
    paths: list[str] = []
    explicit = os.environ.get("JIUWENSWARM_INSTRUMENT_ENV_FILE", "").strip()
    if explicit:
        paths.append(os.path.expanduser(explicit))
    data_dir = os.environ.get("JIUWENCLAW_DATA_DIR", "").strip()
    root = os.path.expanduser(data_dir) if data_dir else os.path.expanduser("~/.jiuwenclaw")
    paths.append(os.path.join(root, "config", ".env"))
    return paths


def load_env_for_instrumentor() -> None:
    """Load jiuwenclaw's .env (and a cwd .env) into os.environ, override=False.

    Idempotent-ish: dotenv won't overwrite vars already in os.environ. Safe to
    call from activate() on every activation path.
    """
    try:
        from dotenv import load_dotenv
    except Exception:
        # python-dotenv not installed in this env (e.g. instrumentor's own dev venv).
        # jiuwenclaw's runtime venv has it as a dep. Skip silently.
        return
    try:
        # Generic cwd .env (dotenv walks upward). Cheap, no-override.
        load_dotenv(override=False)
    except Exception:
        pass
    for path in _candidate_env_files():
        try:
            if os.path.isfile(path):
                load_dotenv(path, override=False)
        except Exception:
            pass
