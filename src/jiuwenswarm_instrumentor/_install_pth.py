# src/jiuwenswarm_instrumentor/_install_pth.py
"""Install/uninstall the autoload ``.pth`` into site-packages so **editable**
installs also auto-activate instrumentation at every interpreter startup.

Why this exists
---------------
Non-editable installs (``pip install .``) ship ``jiuwenswarm_instrumentor.pth``
via ``setup.py``'s ``build_py`` cmdclass → pip drops it into site-packages, where
CPython's ``site`` module runs its ``import`` line at every python startup → fires
``_autoload._autoload()`` in every process in the venv (including jiuwenclaw's
``app_agentserver`` / ``app_gateway`` subprocesses).

Editable installs (``pip install -e .``) do **not** run ``build_py``, so the ``.pth``
never lands in site-packages and autoload never fires — instrumentation is silently
absent. This command writes the same ``.pth`` into site-packages manually, restoring
autoload for editable/dev installs.

Usage
-----
    pip install -e .
    jiuwen-instrument-install-pth              # install (idempotent)
    jiuwen-instrument-install-pth --uninstall  # remove

Note: pip does not know about this manually-written ``.pth`` (it is not a wheel
artifact), so ``pip uninstall jiuwenswarm-instrumentor`` will NOT remove it — run
``--uninstall`` first, or just delete the file from site-packages.
"""
from __future__ import annotations

import os
import site
import sys

_PTH_NAME = "jiuwenswarm_instrumentor.pth"
_PTH_CONTENT = (
    "import jiuwenswarm_instrumentor._autoload  "
    "# auto-instrumentation; no-op unless OTEL_INSTRUMENTOR_ENABLED=true\n"
)


def _is_site_packages_dir(d: str) -> bool:
    """A real site-packages dir as CPython's ``site`` knows it (basename match).

    ``site.getsitepackages()`` on Windows venvs returns the venv ROOT before
    ``Lib/site-packages``; the venv root is on sys.path but processing a ``.pth``
    there runs BEFORE the editable finder (which lives in ``Lib/site-packages``) is
    set up → ``import jiuwenswarm_instrumentor`` fails at startup. We must land the
    ``.pth`` in the same ``Lib/site-packages`` dir as the editable finder so the
    finder is ready first (alphabetical ``__editable__`` < ``j``).
    """
    base = os.path.basename(os.path.normpath(d)).lower()
    return base in ("site-packages", "dist-packages")


def _site_packages_dirs() -> list[str]:
    """All candidate site-packages dirs (site + user site), real ones first."""
    dirs: list[str] = []
    try:
        dirs.extend(site.getsitepackages())
    except Exception:
        pass
    try:
        us = site.getusersitepackages()
        if us:
            dirs.append(us)
    except Exception:
        pass
    # de-dup, preserve order
    seen, out = set(), []
    for d in dirs:
        d = os.path.normpath(d)
        if d not in seen:
            seen.add(d)
            out.append(d)
    # prefer real site-packages dirs so the .pth lands where CPython's site
    # processes it AFTER the editable finder is registered.
    out.sort(key=lambda d: 0 if _is_site_packages_dir(d) else 1)
    return out


def _writable_target() -> str | None:
    """First site-packages dir we can actually write into (creates it if needed)."""
    for d in _site_packages_dirs():
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".jwinst_probe")
            with open(probe, "w"):
                pass
            os.remove(probe)
            return d
        except OSError:
            continue
    return None


def install_pth() -> int:
    d = _writable_target()
    if d is None:
        print("error: no writable site-packages dir found", file=sys.stderr)
        return 1
    path = os.path.join(d, _PTH_NAME)
    with open(path, "w", encoding="utf-8") as f:
        f.write(_PTH_CONTENT)
    print(f"[instrumentor] wrote {_PTH_NAME} -> {path}")
    print("[instrumentor] autoload now fires at every python startup in this venv")
    print("[instrumentor] set OTEL_INSTRUMENTOR_ENABLED=true to activate; "
          "OTEL_LOG_MESSAGES / OTEL_INSTRUMENT_GATEWAY to tune")
    return 0


def uninstall_pth() -> int:
    removed = []
    for d in _site_packages_dirs():
        path = os.path.join(d, _PTH_NAME)
        if os.path.exists(path):
            try:
                os.remove(path)
                removed.append(path)
            except OSError as e:  # pragma: no cover - fs edge
                print(f"warn: could not remove {path}: {e}", file=sys.stderr)
    if removed:
        print("[instrumentor] removed " + ", ".join(removed))
    else:
        print("[instrumentor] no .pth found to remove")
    return 0


def main() -> int:
    if "--uninstall" in sys.argv or "-u" in sys.argv:
        return uninstall_pth()
    return install_pth()


if __name__ == "__main__":
    sys.exit(main())
