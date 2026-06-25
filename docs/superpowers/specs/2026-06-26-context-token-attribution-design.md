# jiuwenswarm-instrumentor 上下文 token 归因 设计规格

- **日期**: 2026-06-26
- **状态**: Draft — 待用户评审
- **参考**: 旧 `jiuwenclaw/telemetry/instrumentors/telemetry_rail.py:1258-1436`(`_record_context_composition`,运行时**不依赖**,仅作参照——rail 已 `_degraded=True`);`openjiuwen/core/context_engine/token/tiktoken_counter.py`;`docs/zh/OpenTelemetry可观测性.md` §"上下文组成";`docs/superpowers/specs/2026-06-25-skill-observability-design.md`(本期补上其延后的 Layer 3)。
- **相关**: `docs/superpowers/specs/2026-06-25-log-collection-design.md`、`.../2026-06-25-trace-context-propagation-design.md`。

---

## 1. 背景与目标

skill 可观测(spec §2026-06-25)当初**延后了 Layer 3**(`gen_ai.context.*` token 分布 + `gen_ai.skill.token.usage`),理由是"需要识别上下文里哪段是 skill body/pin,属上下文计算范畴,本期跳过"。现确认:`msg.metadata` 在 LLM 调用时就携带 `is_skill_body`/`original_is_skill_body`/`active_skill_pin`/`skill_name` 等标签 —— 该开放问题已解决,可补上 Layer 3。

本规格在每次 LLM 调用时,采集**上下文各组成部分的 token 消耗**(估算),回答"上下文被什么占满(system / history / skill / tool 结果 / 工具定义)"+"哪个 skill / tool 最吃 context"。镜像旧 telemetry 的 `_record_context_composition`,但做 3 处改进(见 §3)。

### 关键约束
- **自包含,不依赖 `jiuwenclaw.telemetry`**(rail 已禁用,我们是替代)。允许只读 `msg.metadata`(openjiuwen 的 BaseMessage 字段,非 telemetry)。
- **零 jiuwenswarm 源码改动**:全部在 instrumentor 包内(已有的 `invoke`/`.stream` wrap + 只读 messages/tools + 自己的 span 属性/metric)。
- **tiktoken 可选**:`[estimate]` extra 已在 pyproject;生产环境 openjiuwen 也带 tiktoken。缺失则 `len//4` 回退。

---

## 2. 范围

### 做
- 6 个 `gen_ai.context.*` span 属性(skill / system_prompt / user_messages / assistant_messages / tool_results / tool_definitions),tiktoken 估算。
- `gen_ai.usage.estimated = true` 标记(估算非真值)。
- 2 个 Counter metric:`gen_ai.skill.token.usage`(label `gen_ai.skill.name`)+ `gen_ai.tool.token.usage`(label `gen_ai.tool.name`)。
- 按 `msg.metadata` 分类(skill body/pin 识别、stubbed skill、assistant 含 tool_calls/reasoning_content、多模态只算文本、工具定义按 framing 计数)。

### 不做(非目标)
- 不用 API `usage.prompt_tokens` 做分解(API 只给总数,无 by-component;总数仍由现有 `gen_ai.usage.input_tokens` 记录)。
- 不依赖 openjiuwen ContextEngine 的内部预算数据(只从 messages+tools 归因)。
- 不做 bg-task 并发计数(改同步,见 §3 改进 #3)。
- 不拆 skill body vs pin(skill 合并;per-skill 指标已够回答"哪个 skill 大")。

---

## 3. 设计原则(镜像参考 + 3 改进)

- **镜像参考**:`_record_context_composition` 的分类逻辑、skill body/pin 识别、assistant extras、多模态、工具 framing 都照搬(成熟设计)。
- **改进 #1 — 分词器按模型解析**:不写死 `gpt-4`(cl100k_base);用 `tiktoken.encoding_for_model(model_name)` 按实际模型解析(GPT-4o→o200k_base 等),回退 cl100k_base,再回退 `len//4`。per-call 解析 + encoding 缓存。
- **改进 #2 — 打 `estimated` 标**:span 上设 `gen_ai.usage.estimated=true`,让消费者明确这是 tiktoken 估算(非 API 真值),不会 naively 把 6 个 context.* 加起来对账 `input_tokens`。
- **改进 #3 — 同步计数替 bg-task**:在 LLM 调用返回后、`span.end()` 之前的 finally 里同步计数(不阻塞调用启动,无 span 已 end 的竞态)。tiktoken 对典型 context <5ms;大 context 真慢再加 bg-task(YAGNI)。
- **fail-soft**:计数失败 → 不写属性,不抛,不影响 LLM 调用。
- **可注入**:counter 可注入(测试用 fake,避免 tiktoken 依赖 + 确定性计数)。

---

## 4. 组件 + 属性/指标

### 4.1 6 个 span 属性(每条 `gen_ai.chat` span)

| 属性 | 来自 |
|---|---|
| `gen_ai.context.skill` | tool msg `is_skill_body`/`original_is_skill_body` + system msg `active_skill_pin`,按 `skill_name` 累计 |
| `gen_ai.context.system_prompt` | system msg(非 skill pin) |
| `gen_ai.context.user_messages` | user msg |
| `gen_ai.context.assistant_messages` | assistant msg 文本 + `tool_calls` JSON + `reasoning_content` |
| `gen_ai.context.tool_results` | tool msg(非 skill body) |
| `gen_ai.context.tool_definitions` | 工具定义(按模型 framing 计数) |

+ `gen_ai.usage.estimated = true`。

方程:`skill + system_prompt + user_messages + assistant_messages + tool_results + tool_definitions ≈ gen_ai.usage.input_tokens`(估算;中文/新模型有偏差,见 §8)。

### 4.2 2 个 metric(Counter)

- `gen_ai.skill.token.usage`(unit `{token}`,label `gen_ai.skill.name` + `gen_ai.system="jiuwenclaw"` + 请求属性)—— 按 skill 拆的 token 累计。
- `gen_ai.tool.token.usage`(unit `{token}`,label `gen_ai.tool.name` + 同上)—— 按 tool 定义拆的 token 累计。

---

## 5. 架构 + 数据流

```
OpenAIModelClient.invoke / .stream  (我们已 wrap → gen_ai.chat span)
   │  messages (BaseMessage[], 带 .metadata) + tools + model_name  ← 只读
   │  ↓ LLM 调用(original)返回后、span.end() 之前(同步,改进 #3)
   ▼
_record_context_composition(span, messages, tools, model_name, counter=None)
   ├── counter = counter or _get_token_counter(model_name)   # 改进 #1
   ├── 逐条 message 分类(tiktoken 计数 + metadata 标签)
   │     tool + is_skill_body/original_is_skill_body → skill (+ per_skill)
   │     tool 无 skill 标                       → tool_results
   │     system + active_skill_pin               → skill (+ per_skill)
   │     system 无 pin                           → system_prompt
   │     assistant                               → assistant_messages (+ extras)
   │     user                                    → user_messages
   ├── _count_tool_definitions(tools, counter)   # framing 计数,per-tool + total
   ├── span.set_attribute(gen_ai.context.* = 6 个) + gen_ai.usage.estimated=true
   └── metrics.record_skill_token_usage / record_tool_token_usage (per-skill / per-tool)
```

---

## 6. 组件设计

### 6.1 `instrumentors/context_tokens.py`(新建)— counter + 分类 helpers

把 counter + 分类逻辑从 `llm.py` 拆出(`llm.py` 已不小),聚焦可测:

```python
def _get_token_counter(model_name):
    """改进 #1:按模型解析 tiktoken encoding → cl100k_base → len//4。encoding 缓存。"""
    try:
        import tiktoken
    except Exception:
        return _LenCounter()
    enc = None
    if model_name:
        try:
            enc = tiktoken.encoding_for_model(model_name)
        except Exception:
            enc = None
    if enc is None:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            return _LenCounter()
    return _TiktokenCounter(enc)

class _TiktokenCounter:
    def __init__(self, enc): self._enc = enc
    def count(self, text):
        try: return len(self._enc.encode(text or "", disallowed_special=()))
        except Exception: return len(text or "") // 4

class _LenCounter:
    def count(self, text): return len(text or "") // 4


def _extract_text_content(msg):
    """content 是 str → 直接;是 list(多模态)→ 只取 type=='text' 部分,图片/URL 不算。"""

def _count_assistant_extras(msg, counter):
    """assistant 的 tool_calls(JSON)+ reasoning_content 的 token 数。"""

def _count_tool_definitions(tools, counter):
    """按 framing <|start|>functions.{name}:{idx}\n{json}<|end|> 计数;返回 {total, per_tool[name]}。
    按 tool-name tuple 缓存(同一组 tools 不重复算)。"""

def record_context_composition(span, metrics, messages, tools, model_name, *, counter=None):
    """主入口:分类 + 计数 + 写 span 属性 + 2 metric。全 try/except fail-soft。"""
    try:
        counter = counter or _get_token_counter(model_name)
        tokens = {"skill":0, "system":0, "user":0, "assistant":0, "tool":0}
        per_skill = {}
        for msg in messages:
            role = getattr(msg, "role", "")
            content = _extract_text_content(msg)
            est = counter.count(content)
            md = getattr(msg, "metadata", {}) or {}
            if role == "tool":
                if md.get("is_skill_body") or md.get("original_is_skill_body"):
                    tokens["skill"] += est
                    _acc_skill(per_skill, md)
                else:
                    tokens["tool"] += est
            elif role == "system":
                if md.get("active_skill_pin"):
                    tokens["skill"] += est
                    _acc_skill(per_skill, md)
                else:
                    tokens["system"] += est
            elif role == "assistant":
                tokens["assistant"] += est + _count_assistant_extras(msg, counter)
            elif role == "user":
                tokens["user"] += est
        td = _count_tool_definitions(tools, counter)
        # span 属性(6)+ estimated
        span.set_attribute(A.GEN_AI_CONTEXT_SKILL, tokens["skill"])
        span.set_attribute(A.GEN_AI_CONTEXT_SYSTEM_PROMPT, tokens["system"])
        span.set_attribute(A.GEN_AI_CONTEXT_USER_MESSAGES, tokens["user"])
        span.set_attribute(A.GEN_AI_CONTEXT_ASSISTANT_MESSAGES, tokens["assistant"])
        span.set_attribute(A.GEN_AI_CONTEXT_TOOL_RESULTS, tokens["tool"])
        span.set_attribute(A.GEN_AI_CONTEXT_TOOL_DEFINITIONS, td["total"])
        span.set_attribute(A.GEN_AI_USAGE_ESTIMATED, True)
        # metrics(2)
        base = {A.GEN_AI_SYSTEM: "jiuwenclaw"}; base.update(current_request_attrs())
        for sname, t in per_skill.items():
            metrics.record_skill_token_usage(t, {**base, A.GEN_AI_SKILL_NAME: sname})
        for tname, t in td["per_tool"].items():
            metrics.record_tool_token_usage(t, {**base, A.GEN_AI_TOOL_NAME: tname})
    except Exception:
        logger.debug("[instrumentor] context composition failed", exc_info=True)
```

`_acc_skill(per_skill, md)`:取 `md.get("skill_name","")`,跳过空/`true`/`false`,累计。

### 6.2 `instrumentors/llm.py` — 调用点

在 `invoke_factory` + `stream_factory` 里,现有 `_record_input_messages` / `_record_tool_definitions` / `_record_usage` 旁,加一行(在 LLM 返回后、span.end 前):

```python
record_context_composition(span, metrics, messages, tools, model_name, counter=None)
```

`model_name` 从 invoke/stream 的 `model` 参数取(已有 `_resolve_model`)。流式:在 finally(stream 结束后)调,同 `start_span` 非 current 的现有模式。

### 6.3 `attributes.py` — 新增常量

```python
GEN_AI_CONTEXT_SKILL = "gen_ai.context.skill"
GEN_AI_CONTEXT_SYSTEM_PROMPT = "gen_ai.context.system_prompt"
GEN_AI_CONTEXT_USER_MESSAGES = "gen_ai.context.user_messages"
GEN_AI_CONTEXT_ASSISTANT_MESSAGES = "gen_ai.context.assistant_messages"
GEN_AI_CONTEXT_TOOL_RESULTS = "gen_ai.context.tool_results"
GEN_AI_CONTEXT_TOOL_DEFINITIONS = "gen_ai.context.tool_definitions"
GEN_AI_USAGE_ESTIMATED = "gen_ai.usage.estimated"
```

### 6.4 `metrics.py` — 2 个 Counter

```python
self._skill_token_usage = meter.create_counter("gen_ai.skill.token.usage", unit="{token}", description="Skill content tokens in context (body+pin), by skill")
self._tool_token_usage = meter.create_counter("gen_ai.tool.token.usage", unit="{token}", description="Tool-definition tokens in context, by tool")

def record_skill_token_usage(self, tokens, attrs):
    try: self._skill_token_usage.add(int(tokens or 0), attrs)
    except Exception: logger.debug(...)

def record_tool_token_usage(self, tokens, attrs):
    try: self._tool_token_usage.add(int(tokens or 0), attrs)
    except Exception: logger.debug(...)
```

### 6.5 不改 `agent.py` / `activate.py` / `__init__.py`

只在 `llm.py` 调 `record_context_composition`(`llm.py` 已在 `apply_instrumentors` 里被装)。无新 instrumentor 注册。

---

## 7. 错误处理

- `record_context_composition` 全 try/except → 失败(debug log)→ 不写属性、不抛、不影响 LLM 调用或 span。
- tiktoken 缺失 → `_LenCounter`(`len//4`);encode 异常 → 单条回退 `len//4`。
- `msg.metadata` 缺失 → 当空 dict(分类走默认分支)。
- 消息 content 异常(list 无 text / None)→ `_extract_text_content` 返 `""`,count=0。
- 工具 framing 异常 → `_count_tool_definitions` 回退 raw JSON 计数。

---

## 8. 准确度 + 已知偏差(写进文档/属性)

- tiktoken 估算 ≠ API `usage.prompt_tokens`:中文场景 cl100k_base 偏差 5-15%;GPT-4o/o-系列用 `encoding_for_model` 解析到 o200k_base 后偏差缩小(改进 #1 的价值)。
- `gen_ai.usage.estimated=true` 明确标记(改进 #2),消费者不对账绝对值,看相对占比。
- 流式请求 API 常不返 usage → 现有 `gen_ai.usage.input_tokens` 可能缺失,但 context.* 估算**独立于 API**,仍可用(参考的优势)。
- Counter 是累计值(非分布);要看 per-call 分布可后端按 trace 聚合。

---

## 9. 测试(`tests/instrumentors/test_context_tokens.py` 新建)

fake counter(注入,`count = len(text)` 便于推算)+ fake BaseMessage(role/content/metadata)+ `CollectingSpanExporter` + fake metrics:

1. `test_tool_skill_body_goes_to_skill` — tool msg `is_skill_body=True`+`skill_name` → `gen_ai.context.skill` += count + `gen_ai.skill.token.usage` metric。
2. `test_system_skill_pin_goes_to_skill` — system msg `active_skill_pin`+`skill_name` → skill。
3. `test_stubbed_skill_body` — tool msg `is_skill_body=False, original_is_skill_body=True` → skill(不被漏)。
4. `test_regular_tool_result` — tool msg 无 skill 标 → `gen_ai.context.tool_results`。
5. `test_system_prompt_user_assistant` — 各归位;assistant 含 `tool_calls`(extras 计入)。
6. `test_tool_definitions_framed` — tools 列表 → `gen_ai.context.tool_definitions` total + `gen_ai.tool.token.usage` per-tool metric。
7. `test_estimated_flag_set` — span `gen_ai.usage.estimated == True`。
8. `test_multimodal_text_only` — content list(图片+文本)→ 只算文本部分。
9. `test_failsoft_counter_raises` — 注入会抛的 counter → 不抛、不写 context 属性(span 仍正常)。
10. `test_fallback_no_tiktoken` — `_get_token_counter`(mock tiktoken import 失败)→ `_LenCounter`(`len//4`)。

---

## 10. 验收

- `OTEL_LOG_MESSAGES=true OTEL_TRACES_EXPORTER=otlp` 跑一条带 skill 的对话,labubu 的 `gen_ai.chat` span 上出现 6 个 `gen_ai.context.*` + `gen_ai.usage.estimated=true`。
- `gen_ai.skill.token.usage` / `gen_ai.tool.token.usage` metric 在 labubu 出现(按 skill/tool name 标签)。
- 6 个 context.* 之和 ≈ `gen_ai.usage.input_tokens`(偏差在中文/模型范围内)。
- 关 `OTEL_TRACES_EXPORTER` → 无 context 属性,无 metric(随 trace 信号关停);LLM 调用不受影响。
- 单测全绿(新增 10 例)。
