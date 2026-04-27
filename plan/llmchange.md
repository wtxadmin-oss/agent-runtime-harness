# 动态模型切换实施计划（Phase 拆分版）

## Phase 0：目标冻结与约束确认

### 目标
- 明确本次改造的最终行为，避免后续实现偏移。

### 冻结决策
- 用户可在右侧栏配置并选择模型，配置项为：`modelName`、`apiKey`、`baseUrl`、`supportsThinking`。
- 未选择自定义模型时，使用当前 basellm（现有默认 DeepSeek 链路）。
- 已选择自定义模型时，按会话生效，可切换。
- 思考模式与普通输出都保持流式。
- 思考能力判定采用**用户显式开关**（`supportsThinking`）。
- API Key 不写 `config.json`，采用 profile 专属 `.env` 引用策略。
- 当前阶段传输层保持现状（不强制 WSS 改造）。

### 验收标准
- 团队对上述行为无歧义，后续实现不再引入策略级变更。

---

## Phase 1：后端数据层（用户隔离模型仓库）

### 目标
- 建立 profile 级模型存储，支持 API Key 与元数据分离。

### 实现内容
- 新增 `ModelStore`（建议：`nanobot/profile/model_store.py`）。
- 每个 profile 单独存储：
  - `workspace/users/<profile_id>/models.json`：非敏感字段（id/name/model_name/base_url/supports_thinking/api_key_env）。
  - `workspace/users/<profile_id>/.env.models`：真实 API Key（`ENV_VAR=value`）。
- 提供接口：`list_models/add_model/delete_model/get_model_secret/update_selection_related_fields`。
- `list_models` 返回时不包含明文 API Key。

### 验收标准
- 不同 profile 读取到的模型列表互不相同。
- `models.json` 不出现明文 key。
- `.env.models` 能正确读写并反解为运行时 key。

---

## Phase 2：WebSocket 协议与服务端路由

### 目标
- 打通前端与后端的模型管理与选择协议。

### 实现内容
- 扩展 `nanobot/channels/websocket.py` 的 envelope：
  - 入站：`model_list`、`model_add`、`model_delete`、`model_select`。
  - 出站：`model_list`、`model_saved`、`model_deleted`、`model_selected`。
- `message` 入站增加 `model_id` 解析并写入 metadata（如 `_model_id`）。
- `attach` 返回当前会话选中模型（`selected_model_id`）。
- 所有模型管理动作均按连接绑定 profile 执行（不跨 profile）。

### 验收标准
- 单连接可完成增删查选模型闭环。
- `attach` 后前端可恢复当前会话模型。
- 任一 profile 无法读取/操作其他 profile 模型。

---

## Phase 3：AgentLoop 动态路由（模型覆盖 + 回退）

### 目标
- 让单次消息可按会话选择路由到不同模型，同时保留默认链路兜底。

### 实现内容
- 在 `nanobot/agent/loop.py` 中增加运行时覆盖参数能力：
  - 支持 `_model_id` -> 解析模型配置 -> 临时构建 provider/runner。
  - `AgentRunSpec.model` 改为按消息可覆盖，而非始终 `self.model`。
- 当 `model_id` 无效、配置缺失或读取失败时：
  - 自动回退 `self.runner + self.model`（即 basellm）。
- 将会话选择写入 `Session.metadata`：
  - `selected_model_id`、`selected_model_name`、`selected_model_supports_thinking`。

### 验收标准
- 会话 A 与 B 可并行使用不同模型且不互相污染。
- 删除/失效模型后发送消息可平滑回退到默认模型。

---

## Phase 4：思考模式联动（supportsThinking）

### 目标
- 基于用户配置决定是否允许发送 thinking 参数，并保持流式一致。

### 实现内容
- 模型配置新增 `supports_thinking: boolean`。
- 前端：当前模型不支持思考时，禁用“思考”开关或提示。
- 后端兜底：若 `supports_thinking=false`，忽略 `_thinking_enabled/_reasoning_effort`，按普通流式调用。
- 保持现有 `reasoning_delta -> delta -> stream_end` 行为不变。

### 验收标准
- 支持思考模型：可收到 reasoning 流与正文流。
- 不支持思考模型：不会因传入 thinking 参数导致链路中断。

---

## Phase 5：前端 UI 与状态管理

### 目标
- 在右侧控制区完成模型配置、选择、显示与会话恢复。

### 实现内容
- `webui/src/providers/ClientProvider.tsx` 增加模型状态：
  - `models`、`selectedModelId`、`selectedModel`、`add/delete/select/refresh`。
- `webui/src/lib/types.ts`、`webui/src/lib/nanobot-client.ts` 扩展模型相关事件与 `sendMessage(..., modelId?)`。
- `webui/src/hooks/useNanobotStream.ts` 发消息时携带 `model_id`。
- `webui/src/components/thread/ThreadHeader.tsx` 增加模型选择入口（右侧）。
- 新增 `ModelSelector` 组件：
  - 列表展示、添加弹窗（4项配置 + supportsThinking）、删除、切换。
- 聊天框 model badge：显示会话当前模型；无自定义时显示 basellm 名称。

### 验收标准
- 用户可在 UI 内完成“添加 -> 选择 -> 对话 -> 切会话恢复 -> 删除”。
- 聊天框始终显示当前会话实际模型。

---

## Phase 6：测试、回归与文档

### 目标
- 确保改造稳定，避免破坏现有 websocket/chat 流程。

### 实现内容
- 后端测试：
  - `ModelStore` 单测（增删查、env 引用、profile 隔离）。
  - websocket 单测/集成：`model_*` 协议、`attach` 恢复、`message.model_id` 路由。
  - AgentLoop 单测：模型覆盖、无效回退、thinking 支持判定。
- 前端测试：
  - `ThreadHeader/ModelSelector` 交互。
  - `useNanobotStream` 确认 `model_id` 出站。
  - 会话切换恢复模型显示。
- 文档更新：
  - 增加“模型管理与隔离机制”说明。
  - 明确 API Key 存储策略与风险边界（`.env.models` 为本地明文文件）。

### 验收标准
- 全量测试通过（至少新增用例通过，现有关键用例无回归）。
- 文档可指导新同事独立完成部署与排错。

---

## 里程碑建议

- M1（可演示）：完成 Phase 1-3（后端闭环 + 默认回退）。
- M2（可试用）：完成 Phase 4-5（完整 UI 交互 + 思考联动）。
- M3（可合并）：完成 Phase 6（测试与文档齐备）。

