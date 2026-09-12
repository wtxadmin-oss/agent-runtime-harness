# Agent Runtime Harness

基于 [HKUDS/nanobot](https://github.com/HKUDS/nanobot) 扩展的 Agent Runtime 工程分支，聚焦模型配置、Thinking Recipe、Session / Memory、工具事件与 WebUI 协同。

本仓库用于验证生产级 Agent 系统中的运行时治理问题。上游项目的历史提交与通用能力归原作者所有；本仓库重点展示个人新增的工程扩展、验证方式与架构分析。

## 工程方向

- **Profile 级模型配置**：按 Profile 隔离模型配置和运行参数，降低不同 Agent 配置间的串扰。
- **Thinking Recipe**：从官方文档或代码证据中提取模型思考参数，经过编译、预览和人工确认后持久化。
- **Agent Runtime**：围绕 Agent Loop、Context、Memory、Subagent 和 Tool Calling 扩展运行时能力。
- **Session 与 WebSocket**：统一会话状态和流式事件传递，为前端展示模型输出、工具调用与运行状态提供基础。
- **Skills 扩展**：通过结构化 Skill Contract 约束输入、证据、工具边界与质量门禁。
- **工程环境**：提供 Dev Container、Docker Compose、测试目录与架构文档。

## 个人增量

### Profile-scoped Model Storage

- 新增 Profile Store 与 Model Store。
- 将模型选择、Provider 配置和 Thinking Recipe 绑定到 Profile。
- 在 Agent Runner、Context 和 Session 链路中传递有效 Profile。

### Evidence-driven Thinking Recipe

- 新增 thinking_recipe_orchestrator 与 thinking_recipe_store。
- 支持 URL、代码片段或双来源证据提取。
- 发生证据冲突时进入比较与合并流程。
- 保存动作由前端人工确认触发，Skill 不直接写入持久层。

### Agent Event 与 WebUI 协同

- 扩展 WebSocket Channel 与结构化运行事件。
- 为模型输出、工具执行和状态变化提供统一前端消费入口。
- 将运行时异常与结果信息保留在可追踪事件链路中。

## 架构概览

~~~text
WebUI / Channel
       │
       ▼
WebSocket / Message Bus
       │
       ▼
Agent Loop ── Context ── Session
    │             │          │
    ├── Tools     ├── Skills └── Checkpoint
    ├── Memory    └── Profile / Model Store
    └── Provider / Thinking Recipe
~~~

## 关键目录

~~~text
nanobot/
├── agent/                 Agent Loop、Context、Memory、Tools
├── channels/              WebSocket 与消息接入
├── profile/               Profile、模型与 Thinking Recipe 存储
├── providers/             LLM Provider 适配
├── session/               会话状态管理
└── skills/                Skills 与参数提取规则

webui/                     React / Vite WebUI
tests/                     自动化测试
docs/                      架构、启动与参数治理文档
~~~

## 本地运行

~~~bash
pip install -e .
nanobot onboard
nanobot gateway
~~~

WebUI：

~~~bash
cd webui
bun install
bun run dev
~~~

## 工程原则

- 不为模型发明未记录的参数，每个候选字段必须保留证据。
- Profile、Session 与长期事实采用不同生命周期和隔离边界。
- 工具写入动作必须经过服务端权限控制或人工确认。
- 上游功能与个人增量分别说明，避免混淆代码归属。

## 技术栈

Python · Agent Runtime · WebSocket · React · Vite · MCP · Skills · Memory · Docker

