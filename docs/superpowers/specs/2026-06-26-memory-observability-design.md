# jiuwenswarm-instrumentor 记忆可观测 设计规格

- **日期**: 2026-06-26
- **状态**: Draft — 待用户评审
- **参考**: openjiuwen 记忆系统探索(SessionMemoryManager / FullCompactProcessor / 压缩记忆块 marker / LongTermMemory);`docs/superpowers/specs/2026-06-26-context-token-attribution-design.md`(静态 token 归因,本特性在其基础上加记忆块分桶);`.../2026-06-26-context-compaction-events-design.md`(压缩事件)。
- **相关**: 两个小特性,复用已有 `context_tokens.py` + `llm.py`。

---

## 1. 背景与目标

当前上下文 token 归因(`gen_ai.context.*`)按消息 role 分桶(skill/system/user/assistant/tool/tool_definitions),但**记忆块**(压缩产物 `[DIALOGUE_MEMORY_BLOCK]`/`[FULL_COMPACT_BOUNDARY]` 等 + 召回记忆 `<memory-context>`)被混在 role 桶里,看不出"上下文里多少是记忆"。记忆更新 LLM 调用(SessionMemoryManager 的 `_MEMORY_UPDATE_SYSTEM_PROMPT` + FullCompact 的 `BASE_COMPACT_PROMPT`)被 `gen_ai.chat` 抓到但**没标记**,看不出"哪些 LLM 调用是记忆更新"。

本规格补两个小特性:(1) 记忆块 token 分桶;(2) 记忆更新 LLM 标记。

### 关键约束
- **复用已有基建**:特性 1 改 `context_tokens.py`;特性 2 改 `llm.py`。零新文件、零新耦合。
- **零 jiuwenclaw/openjiuwen 源码改动**。
- **fail-soft**:marker 检查 / prompt 嗅探失败 → 跳过,不影响 LLM 调用或 token 归因。

---

## 2. 范围

### 做
- **特性 1**:`gen_ai.context.memory_blocks` span 属性 —— 记忆块 token 数(content 前缀识别,reclassify 不重复计)。
- **特性 2**:`gen_ai.operation.name = session_memory_update | full_compact_summary` —— 记忆更新 LLM 调用标记(system prompt 嗅探)。

### 不做(非目标)
- 不做记忆 CRUD 事件(SessionMemoryManager / LongTermMemory wrap;中耦合,边际价值低于特性 2)。
- 不做记忆召回观测(LTM 默认关;召回内容已由特性 1 的 `<memory-context>` 前缀覆盖)。
- 不做记忆 store 操作观测。

---

## 3. 设计原则

- **content 前缀识别**:记忆块 marker 在消息 content 里(代码自己用 `startswith` 识别),我们同样用前缀检查。
- **reclassify 不重复计**:记忆块消息只计 `memory_blocks`,不从 role 桶重复计。7 桶之和仍 ≈ `input_tokens`。
- **prompt 嗅探**:记忆更新 LLM 的 system prompt 是 openjiuwen 稳定常量;嗅探前缀可靠但半脆弱(版本可能变),fail-soft。

---

## 4. 特性 1:记忆块 token 分桶

### 4.1 marker 列表(8 个,都是 content 前缀)

| marker | 来源 |
|---|---|
| `[DIALOGUE_MEMORY_BLOCK]` | DialogueCompressor |
| `[CURRENT_ROUND_MEMORY_BLOCK]` | CurrentRoundCompressor |
| `[ROUND_LEVEL_MEMORY_BLOCK]` | RoundLevelCompressor |
| `[FULL_COMPACT_BOUNDARY]` | FullCompactProcessor |
| `[SESSION_MEMORY_BOUNDARY]` | FullCompactProcessor |
| `[FULL_COMPACT_STATE]` | FullCompactProcessor |
| `[QA_BLOCK_CATALOG]` | qa_block/catalog |
| `<memory-context>` | ExternalMemoryRail(召回记忆) |

### 4.2 改动

在 `context_tokens.py` 的 `record_context_composition` 分类循环里,role 分类**之前**,加 memory-block 检查:

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
    if not text:
        return False
    return text.startswith(_MEMORY_BLOCK_MARKERS)
```

在 `record_context_composition` 的消息循环里:

```python
        tokens = {"skill": 0, "system": 0, "user": 0, "assistant": 0, "tool": 0, "memory_blocks": 0}
        ...
        for msg in messages:
            ...
            est = counter.count(_extract_text_content(msg))
            ...
            if _is_memory_block(_extract_text_content(msg)):
                tokens["memory_blocks"] += est
                continue  # 不进 role 桶(reclassify)
            # ... 原有 role 分类 ...
```

`_emit` 加:`span.set_attribute(A.GEN_AI_CONTEXT_MEMORY_BLOCKS, tokens["memory_blocks"])`。

`attributes.py` 加:`GEN_AI_CONTEXT_MEMORY_BLOCKS = "gen_ai.context.memory_blocks"`。

### 4.3 不重复计

一条 `[DIALOGUE_MEMORY_BLOCK]` 的 UserMessage → `memory_blocks` 计,`user_messages` 不计。7 桶之和 ≈ `input_tokens`。

---

## 5. 特性 2:记忆更新 LLM 标记

### 5.1 prompt 嗅探

在 `llm.py` 加 helper:

```python
_MEMORY_OP_PROMPTS = (
    ("You are a session memory updater", "session_memory_update"),
    ("Your task is to create a detailed summary", "full_compact_summary"),
)

def _detect_memory_operation(messages):
    """嗅探 system prompt 前缀 → 返回 operation name 或 None。fail-soft。"""
    try:
        for msg in messages or []:
            if _msg_role(msg) == "system":
                content = _msg_content(msg)
                for prefix, op_name in _MEMORY_OP_PROMPTS:
                    if content.startswith(prefix):
                        return op_name
                break  # 只看第一条 system
    except Exception:
        pass
    return None
```

### 5.2 改动

在 `invoke_factory` + `stream_factory` 的 `_common_attrs` 调用后,加:

```python
            attrs = _common_attrs(self, mdl, provider)
            _op = _detect_memory_operation(messages)
            if _op:
                attrs[A.GEN_AI_OPERATION_NAME] = _op  # 覆盖默认 "chat"
```

(`GEN_AI_OPERATION_NAME` 已在 `attributes.py`,默认 `"chat"` 由 `_common_attrs` 设。命中则覆盖。)

### 5.3 半脆弱

prompt 文本是 openjiuwen 稳定常量(`_MEMORY_UPDATE_SYSTEM_PROMPT` / `BASE_COMPACT_PROMPT`),但版本可能变。嗅探失败 → 返 None → 保持 `"chat"`(fail-soft)。

---

## 6. 错误处理

- 特性 1:`_is_memory_block` 失败 → 返 False → 消息进 role 桶(不进 memory_blocks)。不影响归因。
- 特性 2:`_detect_memory_operation` 失败 → 返 None → `operation.name` 保持 `"chat"`。不影响 LLM 调用。
- 两个都复用现有 fail-soft 模式。

---

## 7. 测试

**特性 1**(`tests/instrumentors/test_context_tokens.py` 加):
1. `test_memory_block_goes_to_memory_bucket` — 一条 `[DIALOGUE_MEMORY_BLOCK]...` 消息 → `gen_ai.context.memory_blocks` > 0,`gen_ai.context.user_messages` == 0(不重复计)。
2. `test_memory_block_not_in_role_bucket` — 混合(普通 user + memory block user)→ `memory_blocks` 只含 block,`user_messages` 只含普通。
3. `test_non_memory_block_not_in_memory_bucket` — 普通消息 → `memory_blocks` == 0。

**特性 2**(`tests/instrumentors/test_llm.py` 加):
4. `test_session_memory_update_labeled` — system prompt = "You are a session memory updater..." → span `gen_ai.operation.name == "session_memory_update"`。
5. `test_full_compact_summary_labeled` — system prompt = "Your task is to create a detailed summary..." → `gen_ai.operation.name == "full_compact_summary"`。
6. `test_normal_chat_not_labeled` — 普通 system prompt → `gen_ai.operation.name == "chat"`(默认)。

---

## 8. 验收

- `OTEL_TRACES_EXPORTER=otlp` 跑一条带压缩的对话,`gen_ai.chat` span 上出现 `gen_ai.context.memory_blocks`(记忆块 token 数)。
- 记忆更新 LLM 调用的 `gen_ai.chat` span 上 `gen_ai.operation.name = session_memory_update` 或 `full_compact_summary`(普通对话仍是 `chat`)。
- 7 桶之和 ≈ `gen_ai.usage.input_tokens`。
- 关 `OTEL_TRACES_EXPORTER` → 无属性(随 trace 信号关停)。
- 单测全绿(新增 6 例)。
