# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`jiuwenswarm-instrumentor` — a **standalone, self-contained OpenTelemetry auto-instrumentation** Python package that collects **traces + metrics** from `jiuwenclaw` / `openjiuwen` (the multi-channel AI agent) and exports them via **OTLP** to a standard observability backend (Arize Phoenix / Langfuse / self-hosted `labubu` — all speak the same OTLP, only the endpoint differs).

## Hard constraint

**Never depend on `jiuwenclaw/telemetry/` or any of its extension points** (`TelemetryProviderExtension`, `ExtensionRegistry` telemetry hook, `TelemetryRail`). That in-tree module is slated for deletion. This package re-implements instrumentation from scratch and must keep working after the old module is removed.

## Commands

> The machine's default `python`/`pip` is **3.14**, which this package's `requires-python = ">=3.11,<3.14"` **rejects**. Always use **Python 3.13** via the `py` launcher.

```bash
py -3.13 -m pip install -e ".[test]"   # editable install + test deps
py -3.13 -m pytest                     # full suite (22 tests)
py -3.13 -m pytest tests/instrumentors/test_llm.py -v   # one file
py -3.13 -m pytest tests/instrumentors/test_llm.py::test_invoke_creates_genai_span -v  # one test
```

The `jiuwen-instrument` console script (and `python -m jiuwenswarm_instrumentor.activate`) is the CLI wrapper entry point.

## Architecture

In-process auto-instrumentation (no `jiuwenclaw` source edits). At activation (`activate.activate()` / `setup()` / `jiuwen-instrument <module>`) a self-contained OTel stack is installed and four core surfaces are monkey-patched via `wrap.patch_method` (idempotent + fail-soft):

| Surface | Target (openjiuwen 0.1.10 / jiuwenclaw enterprise_dev) | Span |
|---|---|---|
| LLM | `OpenAIModelClient.invoke` + `.stream` | `gen_ai.chat` (+ token usage, TTFT) |
| Tool | `AbilityManager.execute_single` | `gen_ai.tool` |
| Agent | `ReActAgent.invoke` | `jiuwenclaw.agent.invoke` |
| Session | `JiuWenClaw.create_instance` / `.cleanup` | `jiuwenclaw.session.create` / `.end` |

Data flow: patched method → `opentelemetry` SDK (`gen_ai.*` + `jiuwenclaw.*` attributes) → OTLP exporter (gRPC/HTTP) → backend. Config is env-driven (`OTEL_*`, see `config.py`). On **enterprise_dev** the probe reads its own master switch `OTEL_INSTRUMENTOR_ENABLED` (default false = zero-cost no-op), kept separate from `OTEL_ENABLED` which gates jiuwenclaw's built-in telemetry module — so installing both never double-exports. On **develop** (no built-in) the probe still reads `OTEL_ENABLED`. Revert to a single switch once the built-in module is removed.

### Metaclass caveat
`openjiuwen`'s `BaseAgent` metaclass rebinds `invoke` as a per-instance attribute at construction. Instrumentation **must be applied before any agent instance is built** — activation runs at process start, before `jiuwenclaw` constructs agents.

### Testing approach
Instrumentors take `tracer`/`metrics`/target-class as injected params (e.g. `instrument_llm(tracer, metrics, *, model_client_cls=Fake)`), so unit tests use **fake classes** + a dependency-free `CollectingSpanExporter` (in `tests/conftest.py`) — no real `openjiuwen`/`jiuwenclaw` or LLM calls needed. The real classes are imported lazily only when the param is `None` (production path).

## Layout

`src/jiuwenswarm_instrumentor/`: `config`, `attributes`, `metrics`, `context` (ContextVar propagation), `provider` (OTel providers + OTLP), `wrap` (`patch_method`), `activate` (CLI + `setup()`), `instrumentors/{llm,tool,agent,session}.py` + `instrumentors/__init__.py` (`apply_instrumentors`). `tests/` mirrors this.

Design spec: `docs/superpowers/specs/2026-06-24-jiuwenswarm-instrumentor-design.md`. Implementation plan: `docs/superpowers/plans/2026-06-24-jiuwenswarm-instrumentor.md`.
