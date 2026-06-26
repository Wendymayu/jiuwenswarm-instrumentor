# 记忆可观测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `gen_ai.chat` span 上加 `gen_ai.context.memory_blocks`(记忆块 token 分桶)+ `gen_ai.operation.name=session_memory_update|full_compact_summary`(记忆更新 LLM 标记)。

**Architecture:** 两个小改动:特性 1 在 `context_tokens.py` 的 `record_context_composition` 消息循环里加 memory-block 前缀检查(content prefix → reclassify 到 memory_blocks 桶,不重复计 role 桶);特性 2 在 `llm.py` 的 invoke/stream wrap 里加 system prompt 嗅探(匹配记忆更新 prompt → 覆盖 `gen_ai.operation.name`)。复用现有基建,零新文件,零新耦合。

**Tech Stack:** Python 3.13(`py -3.13`)、opentelemetry-sdk、pytest + pytest-asyncio。

**Spec:** `docs/superpowers/specs/2026-06-26-memory-observability-design.md`

---

## File Structure

| 文件 | 改动 | 动作 |
|---|---|---|
| `src/jiuwenswarm_instrumentor/attributes.py` | 加 `GEN_AI_CONTEXT_MEMORY_BLOCKS` | Modify |
| `src/jiuwenswarm_instrumentor/instrumentors/context_tokens.py` | 加 `_MEMORY_BLOCK_MARKERS`/`_is_memory_block` + memory_blocks 桶 + span 属性 | Modify |
| `src/jiuwenswarm_instrumentor/instrumentors/llm.py` | 加 `_MEMORY_OP_PROMPTS`/`_detect_memory_operation` + invoke/stream 调用 | Modify |
| `tests/instrumentors/test_context_tokens.py` | 3 个 memory-block 测试 | Modify |
| `tests/instrumentors/test_llm.py` | 3 个 memory-update LLM 标记测试 | Modify |

---

### Task 1: 记忆块 token 分桶

**Files:**
- Modify: `src/jiuwenswarm_instrumentor/attributes.py`
- Modify: `src/jiuwenswarm_instrumentor/instrumentors/context_tokens.py`
- Test: `tests/instrumentors/test_context_tokens.py`

- [ ] **Step 1: 写失败测试** — 在 `tests/instrumentors/test_context_tokens.py` 末尾追加:

```python
def test_memory_block_goes_to_memory_bucket(exporter):
    """A [DIALOGUE_MEMORY_BLOCK] message → memory_blocks bucket, NOT user_messages."""
    sp, _ = _run(exporter, [FakeMsg("user", "[DIALOGUE_MEMORY_BLOCK] summarized conversation here")])
    assert sp.attributes["gen_ai.context.memory_blocks"] == len("[DIALOGUE_MEMORY_BLOCK] summarized conversation here")
    assert sp.attributes["gen_ai.context.user_messages"] == 0  # not double-counted


def test_memory_block_not_in_role_bucket(exporter):
    """Mixed: normal user + memory block user → memory_blocks only has block, user_messages only has normal."""
    msgs = [FakeMsg("user", "normal message"), FakeMsg("user", "[FULL_COMPACT_BOUNDARY] compacted")]
    sp, _ = _run(exporter, msgs)
    assert sp.attributes["gen_ai.context.memory_blocks"] == len("[FULL_COMPACT_BOUNDARY] compacted")
    assert sp.attributes["gen_ai.context.user_messages"] == len("normal message")


def test_non_memory_block_not_in_memory_bucket(exporter):
    """Normal message → memory_blocks == 0."""
    sp, _ = _run(exporter, [FakeMsg("user", "hello")])
    assert sp.attributes["gen_ai.context.memory_blocks"] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.13 -m pytest tests/instrumentors/test_context_tokens.py::test_memory_block_goes_to_memory_bucket -v`
Expected: FAIL — `KeyError: 'gen_ai.context.memory_blocks'` (属性不存在)

- [ ] **Step 3: 实现** — 三处改动:

**(a)** 在 `attributes.py` 末尾(context compaction 区,`JIUWENCLAW_CONTEXT_ID` 之后)加:

```python
GEN_AI_CONTEXT_MEMORY_BLOCKS = "gen_ai.context.memory_blocks"
```

**(b)** 在 `context_tokens.py` 的 `# --- main entry ---` 之前(message helpers 区,`_count_tool_definitions` 之后)加:

```python
_MEMORY_BLOCK_MARKERS = (
    "[DIALOGUE_MEMORY_BLOCK]",
    "[CURRENT_ROUND_MEMORY_BLOCK]",
    "[ROUND_LEVEL_MEMORY_BLOCK]",
    "[FULL_COMPACT_BOUNDARY]",
    "[SESSION_MEMORY_BOUNDARY]",
    "[FULL_COMPACT_STATE]",
    "[QA_BLOCK_CATALOG]",
    "<memory-context>",
)


def _is_memory_block(text):
    """Check if message content starts with a memory-block marker."""
    if not text:
        return False
    return text.startswith(_MEMORY_BLOCK_MARKERS)
```

**(c)** 在 `context_tokens.py` 的 `record_context_composition` 函数里做 3 个修改:

1. `tokens` dict 加 `memory_blocks`(line ~154):
```python
        tokens = {"skill": 0, "system": 0, "user": 0, "assistant": 0, "tool": 0, "memory_blocks": 0}
```

2. 消息循环里,提取 text 一次 + memory-block 检查在 role 分类之前(line ~161-165)。把:
```python
            est = counter.count(_extract_text_content(msg))
            metadata = getattr(msg, "metadata", None) or {}
            if isinstance(msg, dict):
                metadata = msg.get("metadata", {}) or {}
            is_skill = (
```
改为:
```python
            text = _extract_text_content(msg)
            est = counter.count(text)
            metadata = getattr(msg, "metadata", None) or {}
            if isinstance(msg, dict):
                metadata = msg.get("metadata", {}) or {}
            if _is_memory_block(text):
                tokens["memory_blocks"] += est
                continue  # reclassify, not double-counted in role bucket
            is_skill = (
```

3. span 属性区,在 `GEN_AI_CONTEXT_TOOL_DEFINITIONS` 之后(line ~188)加:
```python
        span.set_attribute(A.GEN_AI_CONTEXT_MEMORY_BLOCKS, tokens["memory_blocks"])
```

- [ ] **Step 4: 跑测试确认通过**

Run: `py -3.13 -m pytest tests/instrumentors/test_context_tokens.py -v`
Expected: PASS(全部,含新 3 例)

- [ ] **Step 5: 提交**

```bash
git add src/jiuwenswarm_instrumentor/attributes.py src/jiuwenswarm_instrumentor/instrumentors/context_tokens.py tests/instrumentors/test_context_tokens.py
git commit -m "feat: gen_ai.context.memory_blocks token bucket (content-prefix reclassify)"
```

---

### Task 2: 记忆更新 LLM 标记

**Files:**
- Modify: `src/jiuwenswarm_instrumentor/instrumentors/llm.py`
- Test: `tests/instrumentors/test_llm.py`

- [ ] **Step 1: 写失败测试** — 在 `tests/instrumentors/test_llm.py` 末尾追加:

```python
async def test_session_memory_update_labeled(exporter):
    """System prompt starting with 'You are a session memory updater' → gen_ai.operation.name=session_memory_update."""
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _make_fake_client_cls()
    instrument_llm(tracer, metrics, log_messages=False, model_client_cls=Fake)
    messages = [
        {"role": "system", "content": "You are a session memory updater. Your only task is to update a markdown notes file."},
        {"role": "user", "content": "update"},
    ]
    await Fake().invoke(messages)
    span = exporter.spans[0]
    assert span.attributes["gen_ai.operation.name"] == "session_memory_update"


async def test_full_compact_summary_labeled(exporter):
    """System prompt starting with 'Your task is to create a detailed summary' → gen_ai.operation.name=full_compact_summary."""
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _make_fake_client_cls()
    instrument_llm(tracer, metrics, log_messages=False, model_client_cls=Fake)
    messages = [
        {"role": "system", "content": "Your task is to create a detailed summary of the conversation so far."},
        {"role": "user", "content": "summarize"},
    ]
    await Fake().invoke(messages)
    span = exporter.spans[0]
    assert span.attributes["gen_ai.operation.name"] == "full_compact_summary"


async def test_normal_chat_not_labeled(exporter):
    """Normal system prompt → gen_ai.operation.name stays 'chat' (default)."""
    tracer = trace.get_tracer("t")
    metrics = Metrics(Mock())
    Fake = _make_fake_client_cls()
    instrument_llm(tracer, metrics, log_messages=False, model_client_cls=Fake)
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "hi"},
    ]
    await Fake().invoke(messages)
    span = exporter.spans[0]
    assert span.attributes["gen_ai.operation.name"] == "chat"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3.13 -m pytest tests/instrumentors/test_llm.py::test_session_memory_update_labeled -v`
Expected: FAIL — `assert 'chat' == 'session_memory_update'`(operation.name 没被覆盖)

- [ ] **Step 3: 实现** — 在 `llm.py` 做 2 个改动:

**(a)** 在 `_record_usage` 函数之后(或 `_common_attrs` 之后,helpers 区)加:

```python
_MEMORY_OP_PROMPTS = (
    ("You are a session memory updater", "session_memory_update"),
    ("Your task is to create a detailed summary", "full_compact_summary"),
)


def _detect_memory_operation(messages):
    """Sniff system prompt prefix → return operation name or None. Fail-soft."""
    try:
        for msg in messages or []:
            if _msg_role(msg) == "system":
                content = _msg_content(msg)
                for prefix, op_name in _MEMORY_OP_PROMPTS:
                    if content.startswith(prefix):
                        return op_name
                break  # only first system message
    except Exception:
        pass
    return None
```

**(b)** 在 `invoke_factory` 的 `traced_invoke` 里,`attrs = _common_attrs(self, mdl, provider)` 之后加:

```python
            attrs = _common_attrs(self, mdl, provider)
            _op = _detect_memory_operation(messages)
            if _op:
                attrs[A.GEN_AI_OPERATION_NAME] = _op
```

在 `stream_factory` 的 `traced_stream` 里,`attrs = _common_attrs(self, mdl, provider)` + `attrs[A.GEN_AI_REQUEST_STREAMING] = True` 之后加同样的 3 行:

```python
            attrs = _common_attrs(self, mdl, provider)
            attrs[A.GEN_AI_REQUEST_STREAMING] = True
            _op = _detect_memory_operation(messages)
            if _op:
                attrs[A.GEN_AI_OPERATION_NAME] = _op
```

- [ ] **Step 4: 跑测试确认通过**

Run: `py -3.13 -m pytest tests/instrumentors/test_llm.py -v`
Expected: PASS(全部,含新 3 例)

- [ ] **Step 5: 提交**

```bash
git add src/jiuwenswarm_instrumentor/instrumentors/llm.py tests/instrumentors/test_llm.py
git commit -m "feat: label memory-update LLM calls (session_memory_update / full_compact_summary)"
```

---

## Self-Review (已执行)

- **Spec 覆盖**:§4 记忆块分桶 → Task 1;§5 LLM 标记 → Task 2;§7 测试 6 例 → Task 1(3)+ Task 2(3);§8 验收 → 两 task 都跑。无遗漏。
- **Placeholder 扫描**:无 TBD/TODO;每步含完整代码 + 命令。
- **类型一致**:`_is_memory_block(text)` 在 Task 1 定义、`record_context_composition` 使用一致;`_detect_memory_operation(messages)` 在 Task 2 定义、invoke/stream 调用一致;`GEN_AI_CONTEXT_MEMORY_BLOCKS` 在 Task 1 定义、`_emit` 使用一致;`_MEMORY_OP_PROMPTS` + `_MEMORY_BLOCK_MARKERS` 前后一致。`_msg_role`/`_msg_content`(Task 2 的 `_detect_memory_operation` 用)是 `llm.py` 已有 helper(§line 40-51)。
