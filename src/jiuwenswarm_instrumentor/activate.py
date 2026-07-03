# src/jiuwenswarm_instrumentor/activate.py
from __future__ import annotations
import logging
import runpy
import sys

from opentelemetry import trace, metrics

from jiuwenswarm_instrumentor.config import load_config
from jiuwenswarm_instrumentor.provider import init_providers
from jiuwenswarm_instrumentor.instrumentors import apply_instrumentors

logger = logging.getLogger("jiuwenswarm_instrumentor")
_APPLIED = False
_APPLY_DONE = False
# Top-level packages whose first import triggers deferred apply_instrumentors.
_DEFER_TARGETS = ("jiuwenswarm", "openjiuwen")


def _install_deferred_apply(tracer, meter, cfg):
    """Defer apply_instrumentors to the app's first jiuwenswarm/openjiuwen import.

    Why: at site-init (the .pth autoload), running apply_instrumentors imports
    jiuwenswarm modules, which transitively import mcp (via fastmcp). With the
    traces span processor already attached, this corrupts mcp's import *at
    site-init* — `mcp` is evicted from sys.modules mid-init while its submodules
    (`mcp.types`, ...) are orphaned, so a later `mcp.types.ImageContent` raises
    AttributeError and crashes fastmcp/AgentServer. In a normal (non-site-init)
    import context the same chain imports mcp cleanly, so the fix is purely to
    move apply_instrumentors out of site-init.

    A post-import hook on the jiuwenswarm/openjiuwen top-level package fires
    apply_instrumentors after the package __init__ execs — in the app's normal
    import context, and before any agent instance is built (satisfying the
    openjiuwen metaclass caveat). Fires once; fail-soft."""
    class _DeferredFinder:
        def find_spec(self, name, path, target=None):
            if name not in _DEFER_TARGETS:
                return None
            # Find the real spec via the other finders, then wrap its loader to
            # fire apply_instrumentors AFTER the package __init__ execs.
            for f in sys.meta_path:
                if isinstance(f, _DeferredFinder):
                    continue
                try:
                    spec = f.find_spec(name, path, target) if hasattr(f, "find_spec") else None
                except Exception:
                    continue
                if spec is not None and getattr(spec, "loader", None) is not None:
                    spec.loader = _PostLoadLoader(spec.loader, self)
                    return spec
            return None

    class _PostLoadLoader:
        def __init__(self, real, finder):
            self._real = real
            self._finder = finder

        def exec_module(self, mod):
            self._real.exec_module(mod)
            try:
                sys.meta_path.remove(self._finder)
            except ValueError:
                pass
            global _APPLY_DONE
            if _APPLY_DONE:
                return
            _APPLY_DONE = True
            try:
                apply_instrumentors(tracer, meter, cfg)
            except Exception:
                logger.exception("[instrumentor] deferred apply_instrumentors failed")

        def __getattr__(self, name):
            return getattr(self._real, name)

    sys.meta_path.insert(0, _DeferredFinder())


def activate() -> bool:
    """Read config, install providers, apply instrumentors. Idempotent + fail-soft.
    Returns True if instrumentation is active, False if disabled."""
    global _APPLIED
    if _APPLIED:
        return True
    # Load jiuwenswarm's .env BEFORE reading os.environ: the .pth autoload (and
    # the jiuwen-instrument CLI) run before jiuwenswarm's own load_dotenv() in app
    # main(), so OTEL_* vars placed in jiuwenswarm's .env would otherwise be
    # invisible. Cheap path lookup, no jiuwenswarm import, fail-soft.
    try:
        from jiuwenswarm_instrumentor._env import load_env_for_instrumentor
        load_env_for_instrumentor()
    except Exception:
        pass
    cfg = load_config()
    if not cfg.enabled:
        logger.info("[instrumentor] OTEL_ENABLED not set — instrumentation disabled")
        return False
    try:
        init_providers(cfg)
        tracer = trace.get_tracer("jiuwenswarm_instrumentor")
        meter = metrics.get_meter("jiuwenswarm_instrumentor")
        # If jiuwenswarm/openjiuwen is already imported (late activate() from app
        # code), patch now. Otherwise defer to the first import via a post-import
        # hook — applying at site-init corrupts mcp's import (see
        # _install_deferred_apply).
        if any(m in sys.modules for m in _DEFER_TARGETS):
            apply_instrumentors(tracer, meter, cfg)
            global _APPLY_DONE
            _APPLY_DONE = True
        else:
            _install_deferred_apply(tracer, meter, cfg)
        _APPLIED = True
        logger.info("[instrumentor] active: traces=%s metrics=%s logs=%s endpoint=%s",
                    cfg.traces_exporter, cfg.metrics_exporter, cfg.logs_exporter, cfg.traces_endpoint)
        return True
    except Exception:
        logger.exception("[instrumentor] activation failed — running without instrumentation")
        return False


def main():
    """CLI entry point: `jiuwen-instrument <module> [args...]`.

    Activates instrumentation, then runs the target module as __main__. Must run
    BEFORE jiuwenclaw/openjiuwen construct agent instances (openjiuwen agent metaclass
    rebinds invoke at construction time).
    """
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        print("usage: jiuwen-instrument <module> [args...]", file=sys.stderr)
        sys.exit(2)
    target = sys.argv[1]
    activate()
    # Strip the module name from argv so the target module sees only its own CLI args
    # (mirrors `python -m <module> [args...]`, which run_module(alter_sys=True) expects).
    sys.argv = [sys.argv[0]] + sys.argv[2:]
    runpy.run_module(target, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
