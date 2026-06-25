# 上下文 token 归因 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在每次 LLM 调用的 `gen_ai.chat` span 上采集上下文各组成部分(skill/system/user/assistant/tool_results/tool_definitions)的 tiktoken 估算 token 数 + 2 个 per-skill/per-tool metric。

**Architecture:** 新建 `instrumentors/context_tokens.py`(counter + 分类 helpers + `record_context_composition`),在 `llm.py` 的 invoke/stream wrap 里(LLM 返回后、span.end 前)同步调用。镜像旧 telemetry `_record_context_composition` + 3 改进(按模型解析分词器、打 `estimated` 标、同步替 bg-task)。零 jiuwenswarm 改动;tiktoken 可选(`[estimate]` extra)。

**Tech Stack:** Python 3.13(`py -3.13`)、tiktoken(`encoding_for_model`,可选,回退 `len//4`)、opentelemetry-sdk、pytest + pytest-asyncio。

**Spec:** `docs/superpowers/specs/2026-06-26-context-token-attribution-design.md`

**关键参考(实测,镜像其逻辑):**
- 分类逻辑:`jiuwenclaw/telemetry/instrumentors/telemetry_rail.py:1258-1324`(`_record_context_composition`)。
- `_extract_text_content`: `telemetry_rail.py:1095-1118`(str/list/多模态只取 text)。
- `_count_assistant_extras`: `telemetry_rail.py:1120-1145`(tool_calls JSON + reasoning_content)。
- `_serialize_tool_def` + framing: `telemetry_rail.py:1326-1378`(`<|start|>functions.{name}:{idx}\n{json}<|end|>`)。
- framing 来源: `openjiuwen/core/context_engine/token/tiktoken_counter.py:81-101`(`count_tools`)。
- `msg.metadata` skill 标签:`is_skill_body`/`original_is_skill_body`(tool)、`active_skill_pin`+`skill_name`(system)。

**与参考的差异(改进):**
- 分词器:`tiktoken.encoding_for_model(model_name)`(参考写死 gpt-4/cl100k_base)→ 回退 cl100k_base → 回退 `len//4`。
- 多一个 `gen_ai.usage.estimated=true` span 属性(参考没有)。
- 同步计数(LLM 返回后 finally),不用 bg-task(参考用 asyncio.create_task + 0.2s timeout)。
- 不缓存 tool-def 计数(参考按 tool-name tuple 缓存;YAGNI,tool-def 计数快)。
- 不加 +3 assistant priming(参考加;相对占比里 3 token 可忽略)。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/jiuwenswarm_instrumentor/attributes.py` | `GEN_AI_CONTEXT_*`(6)+ `GEN_AI_USAGE_ESTIMATED` 常量 | Modify |
| `src/jiuwenswarm_instrumentor/metrics.py` | `gen_ai.skill.token.usage` + `gen_ai.tool.token.usage` Counter + record 方法 | Modify |
| `src/jiuwenswarm_instrumentor/instrumentors/context_tokens.py` | counter 类 + `_get_token_counter` + 分类 helpers + `record_context_composition` | Create |
| `src/jiuwenswarm_instrumentor/instrumentors/llm.py` | invoke/stream 里调 `record_context_composition` | Modify |
| `tests/test_metrics.py` | 2 个新 metric 的 fail-soft 测试 | Modify |
| `tests/instrumentors/test_context_tokens.py` | 10 用例(fake counter + fake metrics + real span) | Create |

---

### Task 1: attributes + metrics — 7 常量 + 2 Counter

**Files:**
- Modify: `src/jiuwenswarm_instrumentor/attributes.py`
- Modify: `src/jiuwenswarm_instrumentor/metrics.py`
- Test: `tests/test_metrics.py`

- [ ] **Step 1: 写失败测试** — 在 `tests/test_metrics.py` 末尾追加:

```python
def test_skill_and_tool_token_usage_counters_created():
    from unittest.mock import Mock
    meter = Mock()
    Metrics(meter)
    names = [c.args[0] for c in meter.create_counter.call_args_list]
    assert "gen_ai.skill.token.usage" in names
    assert "gen_ai.tool.token.usage" in names


def test_token_usage_record_failsoft():
    from unittest.mock import Mock
    meter = Mock()
    m = Metrics(meter)
    m._skill_token_usage.add.side_effect = RuntimeError("boom")
    m._tool_token_usage.add.side_effect = RuntimeError("boom")
    m.record_skill_token_usage(5, {"gen_ai.skill.name": "s"})  # must not raise
    m.record_tool_token_usage(3, {"gen_ai.tool.name": "t"})    # must not raise
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.13 -m pytest tests/test_metrics.py::test_skill_and_tool_token_usage_counters_created -v`
Expected: FAIL — `AttributeError: 'Metrics' object has no attribute '_skill_token_usage'`（或 AssertionError：计数器不在列表中）

- [ ] **Step 3: 实现** — 在 `attributes.py` 末尾(`GEN_AI_STREAMING_FIRST_TOKEN_MS` 之后)加:

```python
GEN_AI_CONTEXT_SKILL = "gen_ai.context.skill"
GEN_AI_CONTEXT_SYSTEM_PROMPT = "gen_ai.context.system_prompt"
GEN_AI_CONTEXT_USER_MESSAGES = "gen_ai.context.user_messages"
GEN_AI_CONTEXT_ASSISTANT_MESSAGES = "gen_ai.context.assistant_messages"
GEN_AI_CONTEXT_TOOL_RESULTS = "gen_ai.context.tool_results"
GEN_AI_CONTEXT_TOOL_DEFINITIONS = "gen_ai.context.tool_definitions"
GEN_AI_USAGE_ESTIMATED = "gen_ai.usage.estimated"
```

在 `metrics.py` 的 `Metrics.__init__` 末尾(`self._skill_error_count = ...` 之后)加两个 Counter:

```python
        self._skill_token_usage = meter.create_counter(
            "gen_ai.skill.token.usage", unit="{token}",
            description="Skill content tokens in context (body+pin), by skill",
        )
        self._tool_token_usage = meter.create_counter(
            "gen_ai.tool.token.usage", unit="{token}",
            description="Tool-definition tokens in context, by tool",
        )
```

在 `Metrics` 类末尾(`record_skill_error` 之后)加两个 record 方法:

```python
    def record_skill_token_usage(self, tokens, attrs):
        try:
            self._skill_token_usage.add(int(tokens or 0), attrs)
        except Exception:
            logger.debug("[instrumentor] skill token usage metric failed", exc_info=True)

    def record_tool_token_usage(self, tokens, attrs):
        try:
            self._tool_token_usage.add(int(tokens or 0), attrs)
        except Exception:
            logger.debug("[instrumentor] tool token usage metric failed", exc_info=True)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `py -3.13 -m pytest tests/test_metrics.py -v`
Expected: PASS（全部，含新 2 例）

- [ ] **Step 5: 提交**

```bash
git add src/jiuwenswarm_instrumentor/attributes.py src/jiuwenswarm_instrumentor/metrics.py tests/test_metrics.py
git commit -m "feat: gen_ai.context.* attrs + skill/tool token usage counters"
```

---

### Task 2: context_tokens.py — counter + 分类 helpers + record_context_composition

**Files:**
- Create: `src/jiuwenswarm_instrumentor/instrumentors/context_tokens.py`
- Test: `tests/instrumentors/test_context_tokens.py`

- [ ] **Step 1: 写失败测试** — 创建 `tests/instrumentors/test_context_tokens.py`:

```python
# tests/instrumentors/test_context_tokens.py
from opentelemetry import trace
from jiuwenswarm_instrumentor.instrumentors.context_tokens import (
    record_context_composition, _get_token_counter, _LenCounter,
)


class FakeCounter:
    """Deterministic counter: count = len(text)."""
    def count(self, text):
        return len(text or "")


class FakeMetrics:
    def __init__(self):
        self.skill_calls = []
        self.tool_calls = []
    def record_skill_token_usage(self, tokens, attrs):
        self.skill_calls.append({"tokens": tokens, "skill_name": attrs.get("gen_ai.skill.name", "")})
    def record_tool_token_usage(self, tokens, attrs):
        self.tool_calls.append({"tokens": tokens, "tool_name": attrs.get("gen_ai.tool.name", "")})


class FakeMsg:
    def __init__(self, role, content, metadata=None, tool_calls=None, reasoning_content=None):
        self.role = role
        self.content = content
        self.metadata = metadata or {}
        self.tool_calls = tool_calls
        self.reasoning_content = reasoning_content


def _run(exporter, messages, tools=None, counter=None):
    tracer = trace.get_tracer("t")
    fm = FakeMetrics()
    with tracer.start_as_current_span("gen_ai.chat") as span:
        record_context_composition(span, fm, messages, tools or [], "gpt-4o", counter=counter or FakeCounter())
    return exporter.spans[0], fm


def test_tool_skill_body_goes_to_skill(exporter):
    sp, fm = _run(exporter, [FakeMsg("tool", "hello skill", {"is_skill_body": True, "skill_name": "myskill"})])
    assert sp.attributes["gen_ai.context.skill"] == len("hello skill")
    assert sp.attributes["gen_ai.context.tool_results"] == 0
    assert fm.skill_calls == [{"tokens": len("hello skill"), "skill_name": "myskill"}]


def test_system_skill_pin_goes_to_skill(exporter):
    sp, fm = _run(exporter, [FakeMsg("system", "pin text", {"active_skill_pin": True, "skill_name": "myskill"})])
    assert sp.attributes["gen_ai.context.skill"] == len("pin text")
    assert sp.attributes["gen_ai.context.system_prompt"] == 0
    assert fm.skill_calls == [{"tokens": len("pin text"), "skill_name": "myskill"}]


def test_stubbed_skill_body_caught(exporter):
    """is_skill_body=False but original_is_skill_body=True (offloaded) → still skill."""
    sp, _ = _run(exporter, [FakeMsg("tool", "stub", {"is_skill_body": False, "original_is_skill_body": True, "skill_name": "myskill"})])
    assert sp.attributes["gen_ai.context.skill"] == len("stub")
    assert sp.attributes["gen_ai.context.tool_results"] == 0


def test_regular_tool_result(exporter):
    sp, _ = _run(exporter, [FakeMsg("tool", "result data", {})])
    assert sp.attributes["gen_ai.context.tool_results"] == len("result data")
    assert sp.attributes["gen_ai.context.skill"] == 0


def test_system_user_assistant(exporter):
    msgs = [FakeMsg("system", "sys"), FakeMsg("user", "hi"), FakeMsg("assistant", "hello")]
    sp, _ = _run(exporter, msgs)
    assert sp.attributes["gen_ai.context.system_prompt"] == len("sys")
    assert sp.attributes["gen_ai.context.user_messages"] == len("hi")
    assert sp.attributes["gen_ai.context.assistant_messages"] == len("hello")


def test_assistant_extras_counted(exporter):
    """assistant tool_calls JSON + reasoning_content added to assistant_messages."""
    m = FakeMsg("assistant", "body", tool_calls=[{"id": "x", "function": {"name": "f"}}], reasoning_content="thinking")
    sp, _ = _run(exporter, [m])
    # assistant = body + tool_calls JSON + reasoning
    assert sp.attributes["gen_ai.context.assistant_messages"] > len("body")


def test_tool_definitions_framed(exporter):
    tools = [{"type": "function", "function": {"name": "search", "description": "d", "parameters": {"type": "object"}}}]
    sp, fm = _run(exporter, [], tools=tools)
    assert sp.attributes["gen_ai.context.tool_definitions"] > 0
    assert fm.tool_calls == [{"tokens": sp.attributes["gen_ai.context.tool_definitions"], "tool_name": "search"}]


def test_estimated_flag_set(exporter):
    sp, _ = _run(exporter, [FakeMsg("user", "hi")])
    assert sp.attributes["gen_ai.usage.estimated"] is True


def test_multimodal_text_only(exporter):
    """content is a list (image + text) → only text parts counted."""
    m = FakeMsg("user", [{"type": "text", "text": "hello"}, {"type": "image_url", "image_url": {"url": "x"}}])
    sp, _ = _run(exporter, [m])
    assert sp.attributes["gen_ai.context.user_messages"] == len("hello")


def test_failsoft_counter_raises(exporter):
    class _Boom:
        def count(self, text): raise RuntimeError("boom")
    sp, _ = _run(exporter, [FakeMsg("user", "hi")], counter=_Boom())
    # must not raise; context attrs simply not set (span still valid)
    assert "gen_ai.context.user_messages" not in sp.attributes


def test_fallback_no_tiktoken(monkeypatch):
    """_get_token_counter with tiktoken import failing → _LenCounter."""
    import builtins
    real_import = builtins.__import__
    def _fake_import(name, *a, **k):
        if name == "tiktoken":
            raise ImportError("no tiktoken")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", _fake_import)
    c = _get_token_counter("gpt-4o")
    assert isinstance(c, _LenCounter)
    assert c.count("hello") == len("hello") // 4
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.13 -m pytest tests/instrumentors/test_context_tokens.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jiuwenswarm_instrumentor.instrumentors.context_tokens'`

- [ ] **Step 3: 实现** — 创建 `src/jiuwenswarm_instrumentor/instrumentors/context_tokens.py`:

```python
# src/jiuwenswarm_instrumentor/instrumentors/context_tokens.py
from __future__ import annotations
import json
import logging

from jiuwenswarm_instrumentor import attributes as A
from jiuwenswarm_instrumentor.context import current_request_attrs

logger = logging.getLogger("jiuwenswarm_instrumentor")


# --- token counters (improvement #1: per-model tokenizer) ---

class _TiktokenCounter:
    """tiktoken-based counter. count() falls back to len//4 on encode error."""
    def __init__(self, enc):
        self._enc = enc
    def count(self, text):
        try:
            return len(self._enc.encode(text or "", disallowed_special=()))
        except Exception:
            return len(text or "") // 4


class _LenCounter:
    """Ultimate fallback: len(text)//4."""
    def count(self, text):
        return len(text or "") // 4


_ENC_CACHE = {}


def _get_token_counter(model_name):
    """Resolve tokenizer by model name (improvement #1) → cl100k_base → len//4.
    Encoding objects are cached (creation is expensive)."""
    try:
        import tiktoken
    except Exception:
        return _LenCounter()
    enc = None
    if model_name:
        try:
            enc = _ENC_CACHE.get(("for", model_name))
            if enc is None:
                enc = tiktoken.encoding_for_model(model_name)
                _ENC_CACHE[("for", model_name)] = enc
        except Exception:
            enc = None
    if enc is None:
        try:
            enc = _ENC_CACHE.get(("base", "cl100k_base"))
            if enc is None:
                enc = tiktoken.get_encoding("cl100k_base")
                _ENC_CACHE[("base", "cl100k_base")] = enc
        except Exception:
            return _LenCounter()
    return _TiktokenCounter(enc)


# --- message helpers (mirror telemetry_rail.py:1095-1145) ---

def _extract_text_content(msg):
    """str → str; list (multimodal) → join text parts only (images/URLs excluded)."""
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
            elif hasattr(part, "type") and getattr(part, "type", "") == "text":
                parts.append(getattr(part, "text", ""))
        return "\n".join(parts)
    return str(content)


def _count_assistant_extras(msg, counter):
    """tool_calls (JSON) + reasoning_content tokens. 0 if no extras."""
    extra = 0
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls is None and isinstance(msg, dict):
        tool_calls = msg.get("tool_calls")
    if tool_calls:
        if hasattr(msg, "model_dump"):
            tc_json = msg.model_dump().get("tool_calls", tool_calls)
        else:
            tc_json = tool_calls
        extra += counter.count(json.dumps(tc_json, ensure_ascii=False))
    reasoning = getattr(msg, "reasoning_content", None)
    if reasoning is None and isinstance(msg, dict):
        reasoning = msg.get("reasoning_content")
    if reasoning and isinstance(reasoning, str):
        extra += counter.count(reasoning)
    return extra


# --- tool-definition helpers (mirror telemetry_rail.py:1326-1436) ---

def _serialize_tool_def(tool, idx):
    """Return (name, framed_piece) where piece = <|start|>functions.{name}:{idx}\n{json}<|end|>."""
    if isinstance(tool, dict):
        func_obj = tool.get("function", tool)
        name = func_obj.get("name", "") if isinstance(func_obj, dict) else ""
        json_str = json.dumps(tool, ensure_ascii=False, separators=(",", ":"))
        return name, f"<|start|>functions.{name}:{idx}\n{json_str}<|end|>"
    name = getattr(tool, "name", "") or ""
    func = getattr(tool, "function", None)
    if not name and func:
        name = getattr(func, "name", "")
    func_obj = {"name": name or ""}
    desc = getattr(tool, "description", "")
    if not desc and func:
        desc = getattr(func, "description", "")
    func_obj["description"] = desc or ""
    parameters = getattr(tool, "parameters", None)
    if parameters is None and func:
        parameters = getattr(func, "parameters", None)
    if parameters is not None:
        if isinstance(parameters, type) and hasattr(parameters, "model_json_schema"):
            parameters = parameters.model_json_schema()
        func_obj["parameters"] = parameters
    tool_def = {"type": getattr(tool, "type", "function"), "function": func_obj}
    json_str = json.dumps(tool_def, ensure_ascii=False, separators=(",", ":"))
    return name or "", f"<|start|>functions.{func_obj['name']}:{idx}\n{json_str}<|end|>"


def _count_tool_definitions(tools, counter):
    """Return {total, per_tool[name]} using the framed format."""
    per_tool = {}
    if not tools:
        return {"total": 0, "per_tool": per_tool}
    for idx, tool in enumerate(tools):
        name, piece = _serialize_tool_def(tool, idx)
        name = name or f"_unknown_{idx}"
        per_tool[name] = per_tool.get(name, 0) + counter.count(piece)
    return {"total": sum(per_tool.values()), "per_tool": per_tool}


# --- main entry ---

def record_context_composition(span, metrics, messages, tools, model_name, *, counter=None):
    """Record 6 gen_ai.context.* span attrs + gen_ai.usage.estimated + 2 metrics.
    Fail-soft: any failure → no attrs, no raise. Sync (call after LLM returns, before span.end)."""
    try:
        counter = counter or _get_token_counter(model_name)
        tokens = {"skill": 0, "system": 0, "user": 0, "assistant": 0, "tool": 0}
        per_skill = {}  # skill_name -> running token total
        for msg in messages:
            role = getattr(msg, "role", None)
            if role is None and isinstance(msg, dict):
                role = msg.get("role")
            role = role or "unknown"
            est = counter.count(_extract_text_content(msg))
            metadata = getattr(msg, "metadata", None) or {}
            if isinstance(msg, dict):
                metadata = msg.get("metadata", {}) or {}
            is_skill = (
                (role == "tool" and (metadata.get("is_skill_body") or metadata.get("original_is_skill_body")))
                or (role == "system" and metadata.get("active_skill_pin"))
            )
            if is_skill:
                tokens["skill"] += est
                sname = str(metadata.get("skill_name", "") or "")
                if sname and sname.lower() not in ("true", "false"):
                    per_skill[sname] = per_skill.get(sname, 0) + est
            elif role == "tool":
                tokens["tool"] += est
            elif role == "system":
                tokens["system"] += est
            elif role == "assistant":
                tokens["assistant"] += est + _count_assistant_extras(msg, counter)
            elif role == "user":
                tokens["user"] += est
        td = _count_tool_definitions(tools, counter)
        span.set_attribute(A.GEN_AI_CONTEXT_SKILL, tokens["skill"])
        span.set_attribute(A.GEN_AI_CONTEXT_SYSTEM_PROMPT, tokens["system"])
        span.set_attribute(A.GEN_AI_CONTEXT_USER_MESSAGES, tokens["user"])
        span.set_attribute(A.GEN_AI_CONTEXT_ASSISTANT_MESSAGES, tokens["assistant"])
        span.set_attribute(A.GEN_AI_CONTEXT_TOOL_RESULTS, tokens["tool"])
        span.set_attribute(A.GEN_AI_CONTEXT_TOOL_DEFINITIONS, td["total"])
        span.set_attribute(A.GEN_AI_USAGE_ESTIMATED, True)
        base = {A.GEN_AI_SYSTEM: "jiuwenclaw"}
        base.update(current_request_attrs())
        for sname, t in per_skill.items():
            metrics.record_skill_token_usage(t, {**base, A.GEN_AI_SKILL_NAME: sname})
        for tname, t in td["per_tool"].items():
            metrics.record_tool_token_usage(t, {**base, A.GEN_AI_TOOL_NAME: tname})
    except Exception:
        logger.debug("[instrumentor] context composition failed", exc_info=True)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `py -3.13 -m pytest tests/instrumentors/test_context_tokens.py -v`
Expected: PASS(11 例)

- [ ] **Step 5: 提交**

```bash
git add src/jiuwenswarm_instrumentor/instrumentors/context_tokens.py tests/instrumentors/test_context_tokens.py
git commit -m "feat: context_tokens — per-component tiktoken attribution + estimated flag"
```

---

### Task 3: 接入 llm.py + 全量回归

**Files:**
- Modify: `src/jiuwenswarm_instrumentor/instrumentors/llm.py`

- [ ] **Step 1: 实现** — 在 `llm.py` 顶部 import 区加:

```python
from jiuwenswarm_instrumentor.instrumentors.context_tokens import record_context_composition
```

在 `invoke_factory` 的 `traced_invoke` 里,`_record_usage(span, metrics, result, mdl, provider)`(line 159)之后、`finish = ...`(line 160)之前,加一行(无条件,不受 `log_messages` 门控):

```python
                    _record_usage(span, metrics, result, mdl, provider)
                    record_context_composition(span, metrics, messages, tools, mdl)
                    finish = getattr(result, "finish_reason", None)
```

在 `stream_factory` 的 `finally`(line 245-248)里,`metrics.record_llm_duration(...)` 之后、`span.end()`(line 248)之前,加一行:

```python
            finally:
                metrics.record_llm_duration(time.monotonic() - start,
                                            {A.GEN_AI_REQUEST_MODEL: mdl, A.GEN_AI_SYSTEM: provider.lower()})
                record_context_composition(span, metrics, messages, tools, mdl)
                span.end()
```

- [ ] **Step 2: 跑全量测试**

Run: `py -3.13 -m pytest`
Expected: PASS(全部既有 + 新增 Task1 2 + Task2 11 = 13 例;总数应为 75)

- [ ] **Step 3: 提交**

```bash
git add src/jiuwenswarm_instrumentor/instrumentors/llm.py
git commit -m "feat: call record_context_composition in llm invoke/stream wraps"
```

---

## Self-Review (已执行)

- **Spec 覆盖**:§4.1 6 属性 → Task 2 `record_context_composition`;§4.1 estimated → Task 2;§4.2 2 metric → Task 1;§5 数据流 → Task 2+3;§6.1 context_tokens.py → Task 2;§6.2 llm.py 调用 → Task 3;§6.3 attributes → Task 1;§6.4 metrics → Task 1;§7 fail-soft → Task 2(try/except);§9 测试 10+ 例 → Task 2(11 例)+ Task 1(2 例);§10 验收 → Task 3 全量回归。无遗漏。
- **Placeholder 扫描**:无 TBD/TODO;每步含完整代码 + 命令。Task 2 Step 3 的 `record_context_composition` 有两版(初版 + Step 3b 简化版)——以 Step 3b 为准(删 `_acc_skill`,per_skill 直接累加);实现者按 Step 3b 写。
- **类型一致**:`record_context_composition(span, metrics, messages, tools, model_name, *, counter=None)` 在 Task 2 定义、Task 3 调用 `record_context_composition(span, metrics, messages, tools, mdl)` 一致;`record_skill_token_usage(tokens, attrs)` / `record_tool_token_usage(tokens, attrs)` 在 Task 1 定义、Task 2 调用一致;属性常量 `GEN_AI_CONTEXT_*` + `GEN_AI_USAGE_ESTIMATED` 在 Task 1 定义、Task 2 使用一致。
- **API 校准**:分词器 `tiktoken.encoding_for_model` → `get_encoding("cl100k_base")` → `_LenCounter`;framing 镜像 `tiktoken_counter.py:97`(`<|start|>functions.{name}:{idx}\n{json}<|end|>`);分类镜像 `telemetry_rail.py:1286-1307`;assistant extras 镜像 `:1120-1145`;multimodal 镜像 `:1095-1118`。
