# nanobot 架构概览

## 项目结构

```
nanobot/
├── nanobot/   ← 后端核心（Python）
├── webui/     ← 前端（React + Vite）
└── bridge/    ← WhatsApp 桥接（Node.js）
```

## 后端模块分层

| 层 | 模块 |
|----|------|
| 入口层 | `cli/`（三种启动模式）、`api/`（OpenAI 兼容 HTTP） |
| 接入层 | `channels/`（12 种平台）、`bus/`（消息总线） |
| 核心层 | `agent/`（AgentLoop）、`session/`、`command/`、`skills/` |
| 能力层 | `agent/tools/`、`providers/`、`cron/`、`heartbeat/` |
| 基础层 | `config/`、`security/`、`utils/`、`templates/`、`web/` |

## 三种启动模式

| 命令 | 消息入口 | 是否走 Bus |
|------|---------|-----------|
| `nanobot gateway` | Channel（websocket/telegram 等） | ✅ |
| `nanobot agent` | 终端 stdin | ❌ |
| `nanobot serve` | HTTP `/v1/chat/completions` | ❌ |

## 核心消息链路（gateway 模式）

```
Channel → bus/queue.py → agent/loop.py → providers/（LLM）→ agent/tools/（工具）→ Channel
```

Cron/Heartbeat 绕过 Bus，直接调用 `agent.process_direct()`。

## Session vs Memory

- **Session**：对话历史，按 `channel:chat_id` 隔离，存 `workspace/sessions/*.jsonl`，`/new` 可清空
- **Memory**：长期记忆，全局共享，存 `USER.md` / `MEMORY.md`，由 Dream 自动维护

## 内置技能（9 个）

始终加载：`memory`、`my`

按需加载：`cron`、`github`、`summarize`、`tmux`、`weather`、`skill-creator`、`clawhub`
