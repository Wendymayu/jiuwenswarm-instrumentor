# Skill Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add skill observability to `jiuwenswarm-instrumentor` — Layer 1 (skill span attrs + `skill.loaded`/`skill.released` events on the `gen_ai.tool` span for `skill_tool`/`skill_complete`) + Layer 2 (3 metrics: `gen_ai.skill.call.count`, `gen_ai.skill.duration`, `gen_ai.skill.error.count`), per OTel GenAI #86 (no separate skill span).

**Architecture:** New `instrumentors/skill.py` holds session-scoped duration state (module-level dict, cross-asyncio-task). `instrumentors/tool.py` detects `skill_tool`/`skill_complete` inside the existing `execute_single` wrap → sets skill attrs + events + records metrics + drives the duration state. `metrics.py` adds 3 skill metrics; `attributes.py` adds `GEN_AI_SKILL_NAME`/`GEN_AI_SKILL_ID`; `session.py` clears orphan duration state on session end. All fail-soft, self-contained, no tiktoken.

**Tech Stack:** Python 3.11–3.13, `opentelemetry-sdk`, `pytest`+`pytest-asyncio`. Targets: `openjiuwen` 0.1.10 / `jiuwenclaw` (`skill_tool`/`skill_complete` via `AbilityManager.execute_single`).

**Spec:** `docs/superpowers/specs/2026-06-25-skill-observability-design.md`

> **Python:** the machine's default `python`/`pip` is 3.14, rejected by `requires-python`. Use **`py -3.13`** for ALL commands.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/jiuwenswarm_instrumentor/attributes.py` | Modify | Add `GEN_AI_SKILL_NAME`, `GEN_AI_SKILL_ID` |
| `src/jiuwenswarm_instrumentor/instrumentors/skill.py` | Create | Session-scoped duration state (module dict) + `record_load`/`pop_release`/`clear_session` |
| `src/jiuwenswarm_instrumentor/metrics.py` | Modify | Add 3 skill metrics + `record_skill_call`/`record_skill_duration`/`record_skill_error` |
| `src/jiuwenswarm_instrumentor/instrumentors/tool.py` | Modify | Detect `skill_tool`/`skill_complete` → skill attrs + events + metrics + drive state |
| `src/jiuwenswarm_instrumentor/instrumentors/session.py` | Modify | `clear_session` on session cleanup |
| `tests/instrumentors/test_skill.py` | Create | skill.py state tests + tool enrichment tests |
| `tests/test_attributes.py` | Modify | Assert new constants |
| `tests/test_metrics.py` | Modify | Assert skill metric helpers |

**Data sources (pinned from old `TelemetryRail`):** skill_tool → `tool_msg.metadata.skill_name` (when `is_skill_body`/`original_is_skill_body`), `metadata.relative_file_path`; fallback `tool_call.arguments.skill_name`. skill_complete → `tool_call.arguments.skill_name` (JSON). `skill.id` = `skill_` + sha1(skill_name)[:8] (stable across processes). `session_id` from `session.get_session_id()`, fallback `current_request_attrs()`.

---

## Task 1: Attributes — GEN_AI_SKILL_NAME / GEN_AI_SKILL_ID

**Files:**
- Modify: `src/jiuwenswarm_instrumentor/attributes.py`
- Test: `tests/test_attributes.py`

- [ ] **Step 1: Write the failing test (append to `tests/test_attributes.py`)**

```python
def test_skill_constants():
    from jiuwenswarm_instrumentor import attributes as A
    assert A.GEN_AI_SKILL_NAME == "gen_ai.skill.name"
    assert A.GEN_AI_SKILL_ID == "gen_ai.skill.id"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.13 -m pytest tests/test_attributes.py::test_skill_constants -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'GEN_AI_SKILL_NAME'`

- [ ] **Step 3: Add the constants to `attributes.py`**

Append (after the `GEN_AI_TOOL_*` block, before `GEN_AI_AGENT_NAME`):

```python
GEN_AI_SKILL_NAME = "gen_ai.skill.name"
GEN_AI_SKILL_ID = "gen_ai.skill.id"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3.13 -m pytest tests/test_attributes.py -v`
Expected: PASS (all attributes tests)

- [ ] **Step 5: Commit**

```bash
git add src/jiuwenswarm_instrumentor/attributes.py tests/test_attributes.py
git commit -m "feat: GEN_AI_SKILL_NAME / GEN_AI_SKILL_ID constants"
```

---

## Task 2: skill.py — session-scoped duration state

**Files:**
- Create: `src/jiuwenswarm_instrumentor/instrumentors/skill.py`
- Test: `tests/instrumentors/test_skill.py`

- [ ] **Step 1: Write the failing test (create `tests/instrumentors/test_skill.py`)**

```python
# tests/instrumentors/test_skill.py
from jiuwenswarm_instrumentor.instrumentors import skill


def test_record_load_then_pop_release():
    skill.clear_session("s1")
    skill.record_load("s1", "data_analysis")
    start = skill.pop_release("s1", "data_analysis")
    assert start is not None
    # second pop returns None (already popped)
    assert skill.pop_release("s1", "data_analysis") is None


def test_clear_session_clears_orphans():
    skill.clear_session("s2")
    skill.record_load("s2", "skill_a")
    skill.record_load("s2", "skill_b")
    skill.clear_session("s2")
    assert skill.pop_release("s2", "skill_a") is None
    assert skill.pop_release("s2", "skill_b") is None


def test_record_load_noop_on_empty():
    skill.clear_session("s3")
    skill.record_load("", "x")  # empty session_id -> noop
    skill.record_load("s3", "")  # empty skill_name -> noop
    assert skill.pop_release("s3", "x") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.13 -m pytest tests/instrumentors/test_skill.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jiuwenswarm_instrumentor.instrumentors.skill'`

- [ ] **Step 3: Create `src/jiuwenswarm_instrumentor/instrumentors/skill.py`**

```python
# src/jiuwenswarm_instrumentor/instrumentors/skill.py
"""Session-scoped skill duration state.

skill_tool (load) and skill_complete (release) execute in DIFFERENT asyncio tasks,
so ContextVars don't span them — this state is module-level (shared across tasks).
Keyed by (session_id, skill_name). Cleared on session end (instrumentors/session.py)
to avoid orphans if a skill is loaded but never released.
"""
from __future__ import annotations
import time

_skill_sessions: dict[tuple[str, str], float] = {}


def record_load(session_id: str, skill_name: str) -> None:
    try:
        if session_id and skill_name:
            _skill_sessions[(session_id, skill_name)] = time.monotonic()
    except Exception:
        pass


def pop_release(session_id: str, skill_name: str):
    try:
        if session_id and skill_name:
            return _skill_sessions.pop((session_id, skill_name), None)
    except Exception:
        pass
    return None


def clear_session(session_id: str) -> None:
    try:
        if not session_id:
            return
        for k in [k for k in _skill_sessions if k[0] == session_id]:
            _skill_sessions.pop(k, None)
    except Exception:
        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3.13 -m pytest tests/instrumentors/test_skill.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/jiuwenswarm_instrumentor/instrumentors/skill.py tests/instrumentors/test_skill.py
git commit -m "feat: skill.py session-scoped duration state"
```

---

## Task 3: Metrics — 3 skill metrics

**Files:**
- Modify: `src/jiuwenswarm_instrumentor/metrics.py`
- Test: `tests/test_metrics.py`

- [ ] **Step 1: Write the failing test (append to `tests/test_metrics.py`)**

```python
def test_record_skill_call():
    from jiuwenswarm_instrumentor.metrics import Metrics
    meter = Mock()
    m = Metrics(meter)
    m.record_skill_call({"gen_ai.skill.name": "data_analysis"})
    meter.create_counter.return_value.add.assert_any_call(
        1, {"gen_ai.skill.name": "data_analysis"})


def test_record_skill_duration():
    from jiuwenswarm_instrumentor.metrics import Metrics
    meter = Mock()
    m = Metrics(meter)
    m.record_skill_duration(1.2, {"gen_ai.skill.name": "data_analysis"})
    meter.create_histogram.return_value.record.assert_any_call(
        1.2, {"gen_ai.skill.name": "data_analysis"})


def test_record_skill_error():
    from jiuwenswarm_instrumentor.metrics import Metrics
    meter = Mock()
    m = Metrics(meter)
    m.record_skill_error({"gen_ai.skill.name": "data_analysis"})
    meter.create_counter.return_value.add.assert_any_call(
        1, {"gen_ai.skill.name": "data_analysis"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.13 -m pytest tests/test_metrics.py -v`
Expected: FAIL with `AttributeError: 'Metrics' object has no attribute 'record_skill_call'`

- [ ] **Step 3: Add the 3 metrics + record methods to `metrics.py`**

In `Metrics.__init__`, after `self._agent_duration = ...`:

```python
        self._skill_call_count = meter.create_counter(
            "gen_ai.skill.call.count", unit="{call}",
            description="Skill activation count (skill_tool)",
        )
        self._skill_duration = meter.create_histogram(
            "gen_ai.skill.duration", unit="s",
            description="Skill execution duration (load→release)",
        )
        self._skill_error_count = meter.create_counter(
            "gen_ai.skill.error.count", unit="{call}",
            description="Skill execution error count",
        )
```

After `record_agent_duration`:

```python
    def record_skill_call(self, attrs):
        try:
            self._skill_call_count.add(1, attrs)
        except Exception:
            logger.debug("[instrumentor] skill call metric failed", exc_info=True)

    def record_skill_duration(self, seconds, attrs):
        try:
            self._skill_duration.record(seconds, attrs)
        except Exception:
            logger.debug("[instrumentor] skill duration metric failed", exc_info=True)

    def record_skill_error(self, attrs):
        try:
            self._skill_error_count.add(1, attrs)
        except Exception:
            logger.debug("[instrumentor] skill error metric failed", exc_info=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3.13 -m pytest tests/test_metrics.py -v`
Expected: PASS (all metrics tests)

- [ ] **Step 5: Commit**

```bash
git add src/jiuwenswarm_instrumentor/metrics.py tests/test_metrics.py
git commit -m "feat: gen_ai.skill.call.count / duration / error.count metrics"
```

---

## Task 4: tool.py — skill enrichment for skill_tool / skill_complete

**Files:**
- Modify: `src/jiuwenswarm_instrumentor/instrumentors/tool.py`
- Test: `tests/instrumentors/test_skill.py`

- [ ] **Step 1: Write the failing test (append to `tests/instrumentors/test_skill.py`)**

```python
from unittest.mock import Mock
from opentelemetry import trace
from jiuwenswarm_instrumentor.instrumentors.tool import instrument_tool


class _SkillToolCall:
    def __init__(self, name, arguments="{}"):
        self.name = name
        self.id = "tc1"
        self.arguments = arguments


class _ToolMsg:
    def __init__(self, metadata=None):
        self.content = "ok"
        self.tool_call_id = "tc1"
        self.metadata = metadata or {}


class _Ctx:
    pass


class _Session:
    def get_session_id(self):
        return "sess-1"


def _fake_ability():
    class FakeAbility:
        async def execute_single(self, parent_ctx, tool_call, session, tag=None):
            if tool_call.name == "skill_tool":
                msg = _ToolMsg(metadata={
                    "is_skill_body": True,
                    "skill_name": "data_analysis",
                    "relative_file_path": "skills/data_analysis/SKILL.md",
                })
                return ("loaded", msg, _Ctx())
            return ("done", _ToolMsg(), _Ctx())
    return FakeAbility


async def test_skill_tool_load_enriches_span(exporter):
    skill.clear_session("sess-1")
    tracer = trace.get_tracer("t")
    metrics = Mock()
    Fake = _fake_ability()
    instrument_tool(tracer, metrics, ability_cls=Fake)
    await Fake().execute_single(_Ctx(), _SkillToolCall("skill_tool"), session=_Session())
    span = exporter.spans[0]
    assert span.name == "gen_ai.tool"
    assert span.attributes["gen_ai.operation.name"] == "load_skill"
    assert span.attributes["gen_ai.skill.name"] == "data_analysis"
    assert str(span.attributes["gen_ai.skill.id"]).startswith("skill_")
    assert any(e.name == "skill.loaded" for e in span.events)
    metrics.record_skill_call.assert_called_once()


async def test_skill_complete_release_enriches_span_and_duration(exporter):
    skill.clear_session("sess-1")
    tracer = trace.get_tracer("t")
    metrics = Mock()
    Fake = _fake_ability()
    inst = Fake()
    instrument_tool(tracer, metrics, ability_cls=Fake)
    await inst.execute_single(_Ctx(), _SkillToolCall("skill_tool"), session=_Session())
    await inst.execute_single(_Ctx(), _SkillToolCall(
        "skill_complete", arguments='{"skill_name": "data_analysis"}'), session=_Session())
    release_span = exporter.spans[1]
    assert release_span.attributes["gen_ai.operation.name"] == "release_skill"
    assert release_span.attributes["gen_ai.skill.name"] == "data_analysis"
    assert any(e.name == "skill.released" for e in release_span.events)
    metrics.record_skill_duration.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.13 -m pytest tests/instrumentors/test_skill.py -v`
Expected: FAIL (skill_tool span has no `gen_ai.operation.name` / `gen_ai.skill.name` attrs; `record_skill_call` not called)

- [ ] **Step 3: Rewrite `src/jiuwenswarm_instrumentor/instrumentors/tool.py` with skill enrichment**

```python
# src/jiuwenswarm_instrumentor/instrumentors/tool.py
from __future__ import annotations
import hashlib
import json
import time

from jiuwenswarm_instrumentor import attributes as A
from jiuwenswarm_instrumentor.context import current_request_attrs
from jiuwenswarm_instrumentor.wrap import patch_method
from opentelemetry.trace import StatusCode, SpanKind


def _cap(text, max_len):
    text = "" if text is None else str(text)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def _parse_args(arguments):
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _skill_id(skill_name):
    return "skill_" + hashlib.sha1(skill_name.encode("utf-8")).hexdigest()[:8]


def _session_id(session):
    try:
        return session.get_session_id() if session is not None else None
    except Exception:
        return None


def _skill_metric_attrs(skill_name):
    base = {A.GEN_AI_SKILL_NAME: skill_name, A.GEN_AI_SYSTEM: "jiuwenclaw"}
    base.update(current_request_attrs())
    return base


def _enrich_skill_load(span, tool_call, tool_msg, session, metrics, is_error):
    """OTel GenAI #86: set skill attrs + skill.loaded event on the gen_ai.tool span."""
    try:
        skill_name = ""
        skill_path = ""
        if tool_msg is not None:
            meta = getattr(tool_msg, "metadata", None) or {}
            if isinstance(meta, dict) and (meta.get("is_skill_body") or meta.get("original_is_skill_body")):
                skill_name = str(meta.get("skill_name", "") or "")
                skill_path = str(meta.get("relative_file_path", "") or "")
        if not skill_name:
            args = _parse_args(getattr(tool_call, "arguments", ""))
            skill_name = str(args.get("skill_name", "") or "") if isinstance(args, dict) else ""
        if not skill_name:
            return
        span.set_attribute(A.GEN_AI_OPERATION_NAME, "load_skill")
        span.set_attribute(A.GEN_AI_SKILL_NAME, skill_name)
        span.set_attribute(A.GEN_AI_SKILL_ID, _skill_id(skill_name))
        span.add_event("skill.loaded", {"skill.name": skill_name, "skill.path": skill_path})
        from jiuwenswarm_instrumentor.instrumentors import skill as skill_state
        sid = _session_id(session) or ""
        skill_state.record_load(sid, skill_name)
        attrs = _skill_metric_attrs(skill_name)
        metrics.record_skill_call(attrs)
        if is_error:
            metrics.record_skill_error(attrs)
    except Exception:
        pass


def _enrich_skill_release(span, tool_call, session, metrics, is_error):
    try:
        args = _parse_args(getattr(tool_call, "arguments", ""))
        skill_name = str(args.get("skill_name", "") or "") if isinstance(args, dict) else ""
        if not skill_name:
            return
        span.set_attribute(A.GEN_AI_OPERATION_NAME, "release_skill")
        span.set_attribute(A.GEN_AI_SKILL_NAME, skill_name)
        span.add_event("skill.released", {"skill.name": skill_name})
        from jiuwenswarm_instrumentor.instrumentors import skill as skill_state
        sid = _session_id(session) or ""
        start = skill_state.pop_release(sid, skill_name)
        attrs = _skill_metric_attrs(skill_name)
        if start is not None:
            metrics.record_skill_duration(time.monotonic() - start, attrs)
        if is_error:
            metrics.record_skill_error(attrs)
    except Exception:
        pass


def instrument_tool(tracer, metrics, *, log_messages=False, message_max_length=4096, ability_cls=None):
    """Wrap AbilityManager.execute_single (openjiuwen 0.1.10, ability_manager.py:635)."""
    if ability_cls is None:
        from openjiuwen.core.single_agent.ability_manager import AbilityManager
        ability_cls = AbilityManager

    def factory(original):
        async def traced(self, parent_ctx, tool_call, session, tag=None):
            name = getattr(tool_call, "name", "unknown")
            call_id = getattr(tool_call, "id", "") or ""
            attrs = {
                A.GEN_AI_TOOL_NAME: name,
                A.GEN_AI_TOOL_CALL_ID: call_id,
            }
            attrs.update(current_request_attrs())
            if log_messages:
                attrs[A.GEN_AI_TOOL_ARGUMENTS] = _cap(getattr(tool_call, "arguments", ""), message_max_length)
            start = time.monotonic()
            with tracer.start_as_current_span("gen_ai.tool", kind=SpanKind.CLIENT, attributes=attrs) as span:
                try:
                    result = await original(self, parent_ctx, tool_call, session, tag=tag)
                    tool_msg = result[1] if isinstance(result, tuple) and len(result) >= 2 else None
                    is_error = bool(
                        tool_msg is not None
                        and getattr(tool_msg, "metadata", None)
                        and tool_msg.metadata.get("is_error")
                    )
                    if log_messages and tool_msg is not None:
                        span.set_attribute(A.GEN_AI_TOOL_RESULT, _cap(getattr(tool_msg, "content", ""), message_max_length))
                    # --- skill enrichment (OTel GenAI #86) ---
                    if name == "skill_tool":
                        _enrich_skill_load(span, tool_call, tool_msg, session, metrics, is_error)
                    elif name == "skill_complete":
                        _enrich_skill_release(span, tool_call, session, metrics, is_error)
                    span.set_status(StatusCode.ERROR if is_error else StatusCode.OK)
                    return result
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc)[:256])
                    span.record_exception(exc)
                    raise
                finally:
                    base = {A.GEN_AI_TOOL_NAME: name}
                    base.update(current_request_attrs())
                    metrics.record_tool(time.monotonic() - start, base)
        return traced

    patch_method(ability_cls, "execute_single", factory)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3.13 -m pytest tests/instrumentors/test_skill.py -v`
Expected: PASS (5 passed — 3 state + 2 enrichment)

- [ ] **Step 5: Commit**

```bash
git add src/jiuwenswarm_instrumentor/instrumentors/tool.py tests/instrumentors/test_skill.py
git commit -m "feat: skill enrichment on gen_ai.tool span (skill_tool/skill_complete)"
```

---

## Task 5: session.py — clear orphan skill state on cleanup

**Files:**
- Modify: `src/jiuwenswarm_instrumentor/instrumentors/session.py`
- Test: `tests/instrumentors/test_skill.py`

- [ ] **Step 1: Write the failing test (append to `tests/instrumentors/test_skill.py`)**

```python
async def test_session_cleanup_clears_skill_state(exporter):
    from jiuwenswarm_instrumentor.instrumentors.session import instrument_session
    from opentelemetry import trace
    tracer = trace.get_tracer("t")
    metrics = Mock()
    skill.clear_session("sess-9")
    skill.record_load("sess-9", "orphan_skill")  # loaded, never released

    class _FakeJW:
        _session_id = "sess-9"
        async def cleanup(self):
            pass
    instrument_session(tracer, metrics, jiuwenclaw_cls=_FakeJW)
    await _FakeJW().cleanup()
    # orphan must be cleared by session cleanup
    assert skill.pop_release("sess-9", "orphan_skill") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.13 -m pytest tests/instrumentors/test_skill.py::test_session_cleanup_clears_skill_state -v`
Expected: FAIL (`pop_release` returns the start — orphan NOT cleared, because session.cleanup doesn't call `clear_session` yet)

- [ ] **Step 3: Add `clear_session` to `session.py` cleanup**

In `instrumentors/session.py`, inside `cleanup_factory`'s `traced`, add a `finally` that clears skill state. The current `traced` body is:

```python
        async def traced(self, *args, **kw):
            sid = getattr(self, "_session_id", None)
            attrs = {A.JIUWENCLAW_SESSION_ID: sid or ""}
            attrs.update(current_request_attrs())
            with tracer.start_as_current_span("jiuwenclaw.session.end", kind=SpanKind.INTERNAL, attributes=attrs) as span:
                try:
                    result = await original(self, *args, **kw)
                    span.set_status(StatusCode.OK)
                    return result
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc)[:256])
                    span.record_exception(exc)
                    raise
```

Change it to add a `finally` clearing skill state:

```python
        async def traced(self, *args, **kw):
            sid = getattr(self, "_session_id", None)
            attrs = {A.JIUWENCLAW_SESSION_ID: sid or ""}
            attrs.update(current_request_attrs())
            with tracer.start_as_current_span("jiuwenclaw.session.end", kind=SpanKind.INTERNAL, attributes=attrs) as span:
                try:
                    result = await original(self, *args, **kw)
                    span.set_status(StatusCode.OK)
                    return result
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc)[:256])
                    span.record_exception(exc)
                    raise
                finally:
                    try:
                        from jiuwenswarm_instrumentor.instrumentors import skill as skill_state
                        skill_state.clear_session(sid or "")
                    except Exception:
                        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3.13 -m pytest tests/instrumentors/test_skill.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Run the full suite**

Run: `py -3.13 -m pytest -q`
Expected: all PASS (prior suite + new skill tests)

- [ ] **Step 6: Commit**

```bash
git add src/jiuwenswarm_instrumentor/instrumentors/session.py tests/instrumentors/test_skill.py
git commit -m "feat: clear skill duration state on session cleanup"
```

---

## Self-Review (plan author)

**1. Spec coverage:**
- Layer 1 (skill attrs: operation.name/skill.name/skill.id) → Task 4 (`_enrich_skill_load`/`_enrich_skill_release`). ✓
- skill.loaded / skill.released events → Task 4. ✓
- Layer 2 (skill.call.count / duration / error.count) → Task 3 (metrics) + Task 4 (recording). ✓
- skill.py session state (duration cross-task) → Task 2. ✓
- session.clear_session cleanup → Task 5. ✓
- GEN_AI_SKILL_NAME/ID attributes → Task 1. ✓
- Non-goals (no context.*, no skill.token.usage, no identity, no tiktoken, no separate skill span) — none implemented. ✓

**2. Placeholder scan:** No TBD/TODO; every code step has complete code; all referenced symbols (`GEN_AI_SKILL_NAME`, `GEN_AI_SKILL_ID`, `record_skill_call/duration/error`, `record_load/pop_release/clear_session`, `_enrich_skill_load/_release`, `_skill_id`, `_parse_args`, `_session_id`, `_skill_metric_attrs`) are defined in this plan. ✓

**3. Type/name consistency:**
- `record_load(session_id, skill_name)` / `pop_release(session_id, skill_name)` / `clear_session(session_id)` — defined Task 2, called in Task 4 (`_enrich_skill_load`/`_release`) + Task 5 (cleanup). ✓
- `record_skill_call(attrs)` / `record_skill_duration(seconds, attrs)` / `record_skill_error(attrs)` — defined Task 3, called Task 4. ✓
- `GEN_AI_SKILL_NAME` / `GEN_AI_SKILL_ID` — defined Task 1, used Task 4. (`GEN_AI_OPERATION_NAME`, `GEN_AI_SYSTEM` already exist in attributes.py.) ✓
- `instrument_tool(tracer, metrics, *, log_messages, message_max_length, ability_cls)` — signature unchanged from existing; Task 4 keeps it. ✓

**Note (deliberate deviation from spec, an improvement):** spec §7 wrote `skill.id = skill_<hash(skill_name) & 0xFFFFFFFF:08x>` (copied from old `TelemetryRail`, which uses Python's process-randomized `hash()`). The plan uses `hashlib.sha1(skill_name)[:8]` — a **stable** id across processes, so the same skill gets the same id across restarts (better for backend aggregation). Semantically still "skill_<hash of name>". This is a strict improvement; `skill.name` remains the primary stable key regardless.

**Known impl-time verification (risks §11):** `tool_msg.metadata` field names (`skill_name`, `is_skill_body`, `original_is_skill_body`, `relative_file_path`) + `session` non-None on the skill call path — to be confirmed against openjiuwen's skill-tool return at implementation time; if names differ, adjust the field lookups (no design change).
