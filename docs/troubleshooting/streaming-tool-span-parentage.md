# 流式工具执行下 `gen_ai.tool` 错挂到 `gen_ai.chat` 的问题

> 日期: 2026-06-24 · 分支: `feat/instrumentor-impl` · 涉及文件: `src/jiuwenswarm_instrumentor/instrumentors/llm.py`
> 参考实测 trace: `2ba2540e21ffa472dca43b094987aa45`(jiuwenswarm `resume_enterprise_dev` 分支,enterprise / deep_agent 架构)

## 问题现象

在真实 jiuwenclaw 上跑 instrumentor,trace 里 `gen_ai.tool` span 出现在 `gen_ai.chat` span **下面**(子 span),而不是与 `gen_ai.chat` **平级**(挂在 `jiuwenclaw.agent.invoke` 下)。

## 原因(两个因素叠加)

1. **openjiuwen 的流式工具执行(`StreamingToolExecutor`)**:工具不是等 LLM 流结束后才跑,而是在流式输出过程中、收到 tool_call 增量时**并发执行**(`StreamingToolExecutor` 的 `executor_fn` 直接调 `AbilityManager.execute_single`)。所以工具执行在时间上落在 streaming `gen_ai.chat` span 的存活期内。

2. **我们的 streaming `gen_ai.chat` span 持有 context 太久**:原实现用
   ```python
   with tracer.start_as_current_span("gen_ai.chat", ...) as span:
       ...
       async for chunk in original(...):
           ...
           yield chunk
   ```
   `start_as_current_span` 让 chat span 在整个 `async for` 迭代期间(包括把 chunk `yield` 给调用方、调用方在迭代中执行工具的时段)都是 **current span** → 工具的 `gen_ai.tool` span 的 parent 就成了 chat span。

**为什么不对(按 OTel GenAI 语义约定):** `gen_ai.chat`(模型推理)和 `gen_ai.tool`(工具执行)应是**兄弟**,都挂在 agent span 下——工具是 agent 拿到 tool_call 之后的动作,不属于"模型推理"本身。span 层级是"因果/语义"关系,不是纯时间重叠:即使工具在时间上落在 chat 存活期内,它的因果父仍应是 agent(是 agent 决定执行工具)。

## 验证(实测时序)

trace `2ba2540e...` 的 span 时序(从 labubu 拉取):

```
jiuwenclaw.agent.invoke   0.0s ───────────────── 18.9s   (root)
gen_ai.chat #1            0.05s ─── 14.7s
gen_ai.chat #2                       14.9s ─── 18.8s
gen_ai.tool #1                                     18.0s (48ms)   ← 落在 chat #2 区间内
gen_ai.tool #2                                     18.1s (34ms)   ← 落在 chat #2 区间内
```

两个 tool 在 18.0s 执行,在 chat #2(14.9–18.8s)存活期内 → 印证"流式执行 + span 持 context"导致嵌套。

## 解决

**只改 streaming 路径。** invoke(非流式)路径原本就正确:它的 span 在 `await original()` 返回后、工具执行前就随 `with` 退出结束了,工具自然挂 agent,本来就是兄弟。

`instrumentors/llm.py` 的 `stream_factory`:

- `with tracer.start_as_current_span(...)` → `span = tracer.start_span(...)`(**非当前**)
- 手动 `span.end()`(放 `finally`,保证正常结束 / 异常 / 调用方提前 `break` 都能 end)

```python
# NOT start_as_current_span: 不让这个 span 在 async 迭代期间持 current,
# 这样调用方(agent)在流式迭代中执行的工具不会挂到 gen_ai.chat 下。
# 按 OTel GenAI 约定,gen_ai.chat(推理)与 gen_ai.tool(执行)是 agent 下的兄弟。
span = tracer.start_span("gen_ai.chat", kind=SpanKind.CLIENT, attributes=attrs)
...
try:
    async for chunk in original(...):
        ...  # TTFT / usage / finish / output 都用 span.set_attribute 直接写,不依赖 current
        yield chunk
    ...  # 记录最终 usage / finish / output
    span.set_status(StatusCode.OK)
except Exception as exc:
    span.set_status(StatusCode.ERROR, str(exc)[:256])
    span.record_exception(exc)
    raise
finally:
    metrics.record_llm_duration(...)
    span.end()
```

**效果:** chat span 不再是 current → agent 在迭代期间执行的工具,其 `gen_ai.tool` span 的 parent = 当时 current 的 agent span → 与 `gen_ai.chat` 平级(都挂 `jiuwenclaw.agent.invoke`)。

## 权衡

- chat span 不在流式迭代期间持 context → 不会有子 span 嵌到 chat 下(符合 OTel 约定)。
- chat span 的 duration 现在只覆盖模型推理部分(不再含 agent 消费流 + 执行工具的等待),更准。
- 代价:chat span 不是 current,所以流式期间不会有任何 span 自动挂到它下面;也不向模型 API 做 traceparent 上下文传播。**当前可接受**——我们没有对 openai SDK 的 HTTP 做插桩,不需要 chat span 持 context 来传播 traceparent。
- 所有记录(TTFT、token usage、input/output messages、tool definitions)都通过 `span.set_attribute` 直接写到 span 对象,不依赖 span 是否 current,所以不受影响。

## 相关

- 单测: `tests/instrumentors/test_llm.py`(stream 路径仍验证 span + 属性 + TTFT + 输出;父级关系属集成行为,靠真实 app trace 验证)。
- 同一 trace 还暴露过另外两个问题(已分别修复):
  - 流式 tool_call 响应没有 output(只收 `content` 增量,工具调用响应 content 为空)→ 按 `index` 累积 tool_call 增量作为输出。
  - 输入没有 tool 定义 → 记录 `gen_ai.tool.definitions`(传给 LLM 的 `tools` 参数)。
