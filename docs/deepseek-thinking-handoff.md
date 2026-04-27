# DeepSeek 深度思考模式 — 交接文档

> 面向接手此功能的开发者（Codex 或其他）。记录本次改动的背景、所有修改文件、关键设计决策、已知坑点，以及如何验证功能正常。

---

## 一、功能概述

在 nanobot WebUI 中新增「深度思考模式」：

- 顶部工具栏出现 **🧠 思考** 切换按钮
- 开启后，每次发消息会携带 `thinking: {enabled: true, effort: "high"|"max"}` 参数
- 后端调用 DeepSeek API 时注入 `extra_body={"thinking": {"type": "enabled"}}` 和 `reasoning_effort`
- DeepSeek 返回的 `reasoning_content` 通过新增的 `reasoning_delta` WebSocket 事件流式推送到前端
- 前端在正文回答之前渲染可折叠的 **🧠 深度思考** 紫色块，实时显示思考过程

---

## 二、修改文件清单

### 后端（Python）

| 文件 | 改动摘要 |
|------|---------|
| `nanobot/providers/openai_compat_provider.py` | DeepSeek thinking 参数注入；`chat_stream` 新增 `on_reasoning_delta` 回调，流式回调 `delta.reasoning_content` |
| `nanobot/providers/base.py` | `chat_stream` / `chat_stream_with_retry` 签名增加 `on_reasoning_delta: Callable[[str], Awaitable[None]] \| None = None` |
| `nanobot/agent/runner.py` | `AgentRunSpec` 增加 `reasoning_delta_callback` 字段；`_request_model` 将其透传给 `chat_stream_with_retry` |
| `nanobot/agent/loop.py` | `_run_agent_loop` / `_process_message` 增加 `on_reasoning_delta` 和 `reasoning_effort` 参数；在 `_wants_stream` 块内创建 `on_reasoning_delta` 回调，发布带 `_reasoning_delta=True` 元数据的 `OutboundMessage` |
| `nanobot/channels/base.py` | 新增 `send_reasoning_delta(chat_id, reasoning)` 空基类方法 |
| `nanobot/channels/manager.py` | `_send_once` 新增分支：`msg.metadata.get("_reasoning_delta")` → `channel.send_reasoning_delta(...)` |
| `nanobot/channels/websocket.py` | 解析入站 `thinking` 字段，写入 `metadata["_reasoning_effort"]` 和 `metadata["_thinking_enabled"]`；新增 `send_reasoning_delta` 方法，发送 `{"event": "reasoning_delta", ...}` |

### 前端（TypeScript / React）

| 文件 | 改动摘要 |
|------|---------|
| `webui/src/lib/types.ts` | `InboundEvent` 新增 `reasoning_delta` 类型；`UIMessage` 新增 `reasoning?: string` / `isThinking?: boolean`；`Outbound` message 新增 `thinking?` 字段 |
| `webui/src/lib/nanobot-client.ts` | `sendMessage` 增加可选 `thinking` 参数，序列化进 WebSocket 帧 |
| `webui/src/providers/ClientProvider.tsx` | Context 新增 `thinkingEnabled` / `setThinkingEnabled` / `reasoningEffort` / `setReasoningEffort` |
| `webui/src/hooks/useNanobotStream.ts` | 新增 `reasoning_delta` 事件处理：累积到 `message.reasoning`，设 `isThinking: true`；`stream_end` 清除 `isThinking`；`send` 携带 `thinkingParam` |
| `webui/src/components/ThinkingBlock.tsx` | **新建**：可折叠思考块，紫色风格，thinking 中显示跳动光标 |
| `webui/src/components/MessageBubble.tsx` | assistant 消息渲染 `ThinkingBlock`（在正文前）；空内容时仅在非 thinking 状态显示 `TypingDots` |
| `webui/src/components/thread/ThreadHeader.tsx` | 右侧新增 🧠 切换按钮 + `high`/`max` 推理强度选择器 |

---

## 三、关键设计决策

### 3.1 `supports_streaming` 门控机制

这是本次开发中最容易踩的坑，**必须理解**。

`BaseChannel.supports_streaming` 的实现：

```python
# nanobot/channels/base.py
@property
def supports_streaming(self) -> bool:
    cfg = self.config
    streaming = cfg.get("streaming", False) if isinstance(cfg, dict) else getattr(cfg, "streaming", False)
    return bool(streaming) and type(self).send_delta is not BaseChannel.send_delta
```

只有当：
1. channel 配置中 `streaming: true`，**且**
2. 子类**真正覆盖**了 `send_delta` 方法（不是继承基类的空实现）

`supports_streaming` 才返回 `True`。

`loop.py` 中的 `_wants_stream` 标志由此决定，而 `on_reasoning_delta` 回调**只在 `_wants_stream=True` 时才被创建**。如果 `send_delta` 没有正确覆盖，`on_reasoning_delta` 永远是 `None`，思考内容永远不会发送。

### 3.2 `send_reasoning_delta` 与 `send_delta` 的位置关系

`websocket.py` 中 `send_reasoning_delta` 必须定义在 `send_delta` **之前**（或之后，顺序无所谓），但两个方法都必须有完整的 `async def` 声明头。本次开发曾因插入 `send_reasoning_delta` 时误删 `send_delta` 的函数声明头，导致 `send_delta` 方法体变成孤立代码，`WebSocketChannel.send_delta is not BaseChannel.send_delta` 返回 `False`，整条链路失效。

**验证方式**（任何时候修改 websocket.py 后都应运行）：

```python
python3 -c "
from nanobot.channels.websocket import WebSocketChannel
from nanobot.channels.base import BaseChannel
assert WebSocketChannel.send_delta is not BaseChannel.send_delta, 'send_delta NOT overridden!'
assert WebSocketChannel.send_reasoning_delta is not BaseChannel.send_reasoning_delta, 'send_reasoning_delta NOT overridden!'
print('OK: both methods properly override base class')
"
```

### 3.3 DeepSeek API 参数

```python
# openai_compat_provider.py 中的注入位置：_build_kwargs()
elif spec.name == "deepseek":
    extra = {"thinking": {"type": "enabled" if thinking_enabled else "disabled"}}
    if reasoning_effort:
        extra["reasoning_effort"] = reasoning_effort
    kwargs["extra_body"] = {**kwargs.get("extra_body", {}), **extra}
```

流式 chunk 中 `delta.reasoning_content` 字段即思考内容，已在 `_parse_chunks` 中处理（`LLMResponse.reasoning_content`）。`on_reasoning_delta` 回调在每个非空 chunk 时触发。

### 3.4 WebSocket 事件协议

新增事件（服务端 → 客户端）：

```json
{"event": "reasoning_delta", "chat_id": "xxx", "text": "...思考片段..."}
```

入站消息新增字段（客户端 → 服务端）：

```json
{
  "type": "message",
  "chat_id": "xxx",
  "content": "用户输入",
  "thinking": {"enabled": true, "effort": "high"}
}
```

---

## 四、数据流全链路

```
用户点击发送（thinking=enabled, effort=high）
  │
  ▼
nanobot-client.ts: sendMessage() → WebSocket frame 含 thinking 字段
  │
  ▼
websocket.py: _dispatch_envelope() 解析 thinking → metadata["_reasoning_effort"]="high"
  │
  ▼
bus → loop.py: _process_message() 读取 metadata["_reasoning_effort"]
  │
  ▼
loop.py: _run_agent_loop()
  ├─ _wants_stream=True（因 WebSocketChannel.supports_streaming=True）
  ├─ 创建 on_reasoning_delta 回调（发布 _reasoning_delta=True 的 OutboundMessage）
  └─ 创建 AgentRunSpec(reasoning_delta_callback=on_reasoning_delta, reasoning_effort="high")
  │
  ▼
runner.py: _request_model() → provider.chat_stream_with_retry(on_reasoning_delta=...)
  │
  ▼
openai_compat_provider.py: chat_stream()
  ├─ _build_kwargs() 注入 extra_body={"thinking": {"type": "enabled"}, "reasoning_effort": "high"}
  └─ 流式循环：delta.reasoning_content → on_reasoning_delta(text)
  │
  ▼
loop.py: on_reasoning_delta() → bus.publish_outbound(metadata={"_reasoning_delta": True})
  │
  ▼
manager.py: _send_once() → channel.send_reasoning_delta(chat_id, reasoning)
  │
  ▼
websocket.py: send_reasoning_delta() → {"event": "reasoning_delta", "text": "..."}
  │
  ▼
useNanobotStream.ts: reasoning_delta 事件 → message.reasoning 累积，isThinking=true
  │
  ▼
MessageBubble.tsx: <ThinkingBlock reasoning={...} isThinking={true} />
```

---

## 五、已知问题 / 待清理

1. **调试日志未删除**：`websocket.py` 和 `loop.py` 中各有一行 `logger.debug(...)` 是排查 bug 时加的，功能验证后应删除：
   - `websocket.py`：`logger.debug("websocket: inbound message thinking={}", thinking)`
   - `loop.py`：`logger.debug("loop: _reasoning_effort={} on_reasoning_delta={}", ...)`

2. **历史消息无 reasoning**：会话历史从 JSONL 回放时不含 `reasoning_content`（后端存储时未持久化），历史消息的思考块不会显示，这是预期行为。

3. **非 DeepSeek 模型**：thinking 参数只对 DeepSeek provider 生效（`_build_kwargs` 中有 `elif spec.name == "deepseek"` 分支）。其他 provider 会忽略 `on_reasoning_delta`（回调存在但 provider 不调用它）。

---

## 六、验证步骤

```bash
# 1. 启动后端
set -a && source /workspaces/nanobot/.env && set +a
nanobot gateway

# 2. 启动前端（另一个终端）
cd /workspaces/nanobot/webui
bun run dev

# 3. 打开浏览器
# http://localhost:5173  （Ctrl+Shift+R 强制刷新）
```

验证清单：
- [ ] 顶部出现「🧠 思考」按钮
- [ ] 点击开启 → 出现 `high` / `max` 选择器
- [ ] 发送消息 → 先出现紫色「🧠 深度思考」折叠块（内容流式更新）
- [ ] 思考结束后出现正文回答，折叠块停止更新
- [ ] 关闭思考开关 → 发送消息 → 无思考块，只有正文
- [ ] 切换 `high`/`max` → 思考内容详细程度有变化

---

## 七、配置说明

`~/.nanobot/config.json` 中需使用 DeepSeek 模型：

```json
{
  "agents": {
    "defaults": {
      "model": "deepseek-v4-flash",
      "provider": "deepseek"
    }
  }
}
```

DeepSeek API Key 在 `.env` 文件中配置（`DEEPSEEK_API_KEY` 或对应字段）。
