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


def activate() -> bool:
    """Read config, install providers, apply instrumentors. Idempotent + fail-soft.
    Returns True if instrumentation is active, False if disabled."""
    global _APPLIED
    if _APPLIED:
        return True
    # Load jiuwenclaw's .env BEFORE reading os.environ: the .pth autoload (and the
    # jiuwen-instrument CLI) run before jiuwenclaw's own load_dotenv() in app main(),
    # so OTEL_* vars placed in jiuwenclaw's .env would otherwise be invisible. Cheap
    # path lookup, no jiuwenclaw import, fail-soft.
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
        apply_instrumentors(trace.get_tracer("jiuwenswarm_instrumentor"),
                            metrics.get_meter("jiuwenswarm_instrumentor"), cfg)
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
