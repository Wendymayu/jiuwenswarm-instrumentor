# gateway 插桩:`process_stream` 是 coroutine 不是 async generator(踩坑)

> 日期: 2026-06-25 · 分支: `feat/instrumentor-impl` · 涉及文件: `src/jiuwenswarm_instrumentor/instrumentors/gateway.py`
> 实测环境: jiuwenswarm `resume_enterprise_dev`,启动 agentserver + gateway 后浏览器发消息**无响应**。
> 修复 commit: `7cdcdaa`

## 问题现象

启用 gateway 端到端 trace 插桩(`channel.request` wrap `MessageHandler.process_stream`)后,浏览器发消息**没有任何回复**。gateway 日志反复报:

```
ERROR jiuwenclaw.gateway.message_handler:2398: AgentServer send_request failed for req_xxx: a coroutine was expected, got <async_generator object instrument_gateway.<locals>.factory_process_stream.<locals>.traced at 0x...>
Traceback (most recent call last):
  File ".../message_handler.py", line 2369, in _forward_loop
    task = asyncio.create_task(
        self.process_stream(env, msg.session_id, msg.metadata)
    )
  File ".../asyncio/base_events.py", line 469, in create_task
    task = tasks.Task(coro, loop=self, name=name, context=context)
TypeError: a coroutine was expected, got <async_generator object ...traced at 0x...>
```

## 根因:coroutine 和 async generator 不能互换

Python 里 `async def` 函数,**有没有 `yield` 决定它是 coroutine 还是 async generator**——二者**不通用**:

| 定义 | 调用 `f()` 返回 | 怎么消费 | `asyncio.create_task(f())` | `await f()` | `async for x in f()` |
|---|---|---|---|---|---|
| `async def f(): return 1` | **coroutine** | `await f()` | ✅ | ✅ → 1 | ❌ TypeError |
| `async def g(): yield 1` | **async generator** | `async for x in g()` | ❌ "a coroutine was expected, got <async_generator>" | ❌ TypeError | ✅ → 1 |

关键:**`asyncio.create_task(coro)` 只收 coroutine;`async for x in agen` 只收 async generator。** 把 async generator 喂给 `create_task` 就是上面那个 `TypeError`。

`MessageHandler.process_stream` 的**调用方**是:

```python
# message_handler.py:2369  _forward_loop
task = asyncio.create_task(
    self.process_stream(env, msg.session_id, msg.metadata)   # ← 期望 coroutine
)
```

→ `process_stream` 是 **coroutine**(`async def` + `return`,内部跑完整条流式处理)。

但我的 wrap 写成了 async generator(用了 `yield`):

```python
# 错误版本(gateway.py factory_process_stream)
def factory_process_stream(original):
    async def traced(self, *args, **kw):
        with tracer.start_as_current_span("channel.request", ...):
            async for chunk in original(self, *args, **kw):   # ← yield 在这
                yield chunk                                     # ← 这一行让 traced 变成 async generator
    return traced
```

`yield` 让 `traced` 变成 async generator → `self.process_stream(...)` 返回 async_generator → `asyncio.create_task` 报 `a coroutine was expected` → `_forward_loop` 异常 → 消息发不出去 → 浏览器无响应。

## 为什么单测没挡住(最值得学的点)

`tests/instrumentors/test_gateway.py` 的 `FakeMessageHandler.process_stream` 我也写成了 async generator:

```python
# 错误的 fake(和我的错误假设一致)
class FakeMessageHandler:
    async def process_stream(self, *args, **kw):
        async for c in self._ac.send_request_stream(FakeEnvelope({})):
            yield c                     # ← async generator
```

测试用 `async for c in mh.process_stream()` 消费它 → ✅ 通过。**但这个 fake 印证了我自己的错误假设**(以为 process_stream 是 async gen),没有反映**真实调用方**(`create_task`,要 coroutine)的契约。

→ 单测验的是"wrap 的 span 行为对不对",**没验"wrap 后调用方还能不能正常调用"**。fake 的形状和真实方法的形状不一致,bug 就藏在缝隙里。

这正是最终评审 #4 预言的"production-verification risk":"the wrap-target nature can't be unit-tested with a fake that doesn't match reality"——一语成谶。

## 验证(实测定位)

1. gateway 日志的 traceback 直接点出 `asyncio.create_task(self.process_stream(...))` + `TypeError: a coroutine was expected, got <async_generator>` → process_stream 是 coroutine,wrap 把它变成了 async generator。
2. 翻 `message_handler.py:2369` 确认调用方是 `create_task`(要 coroutine),不是 `async for`(要 async gen)。
3. 单测的 fake 是 async gen(用 `yield` + `async for` 消费)——和真实调用方不一致。

## 解决

`factory_process_stream` 改回 **coroutine**(`return await`,不要 `yield`):

```python
# 正确版本(gateway.py)
def factory_process_stream(original):
    async def traced(self, *args, **kw):
        attrs = _process_attrs(args, kw)
        with tracer.start_as_current_span("channel.request", kind=SpanKind.INTERNAL, attributes=attrs):
            return await original(self, *args, **kw)   # ← await,不是 yield;traced 是 coroutine
    return traced
```

fake 也改成 coroutine(和真实方法形状一致):

```python
class FakeMessageHandler:
    """Real MessageHandler.process_stream is a COROUTINE (caller passes it to
    asyncio.create_task), not an async generator — so the fake matches that."""
    async def process_stream(self, *args, **kw):
        async for _c in self._ac.send_request_stream(FakeEnvelope({})):
            pass            # 消费流(真实方法内部也是消费/转发流)
        return "done"       # ← return,不是 yield;coroutine
```

测试改用 `await`(coroutine 的消费方式):

```python
result = await mh.process_stream()    # 不是 async for
assert result == "done"
```

**为什么 `return await` 能覆盖全程**:`process_stream` 是个长跑 coroutine(`create_task` 起的任务跑到流结束),`await original(...)` 等它跑完 → `channel.request` span 覆盖整条流式处理。✅

**效果**:重启后浏览器发消息正常回复;`channel.request` span 正常产出。

## 经验(供后续)

1. **wrap 一个方法前,先看它的调用方要什么**:`create_task(f())` → `f` 是 coroutine;`async for x in f()` → `f` 是 async gen。wrap 后的形状必须和调用方期望一致。**`yield` vs `return` 不是风格选择,是类型选择。**
2. **单测的 fake 要复刻真实方法的"调用契约"(coroutine / async gen / 同步),不能只复刻"被测行为"**。否则 fake 顺着你的错误假设长,bug 就测不出。
3. **更进一步:加一个"调用方契约"测试**——比如 `asyncio.iscoroutine(mh.process_stream())` 或直接 `asyncio.create_task(mh.process_stream())`(不报错)。这种测试不验 span,只验"形状对不对",能挡住本类 bug。(本次修复后,`await mh.process_stream()` 若 wrap 退回 async gen 会直接 TypeError,已隐式锁住。)
4. **评审的"production-verification risk"不是客套**:跨进程/跨真实调用链的假设(本例:process_stream 的真实形状)单测覆盖不到,真机冒烟才能兜底。新插桩上线前发一条真消息验证,是值得的。

## 相关

- 修复 commit: `7cdcdaa`(gateway.py `factory_process_stream` coroutine 化 + fake/测试同步)。
- 评审 #4(`docs/superpowers/specs/2026-06-25-trace-context-propagation-design.md` 对应最终评审)预言了这类风险。
- 同特性其他文档:`docs/superpowers/specs/2026-06-25-trace-context-propagation-design.md`(设计)、`docs/superpowers/plans/2026-06-25-trace-context-propagation.md`(计划)。
- 本文为学习/排障留存;bug 已在 `7cdcdaa` 修复,用户不会再遇到。
