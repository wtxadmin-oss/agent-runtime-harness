# Thinking 参数配置：隔离、全链路与排错

本文用于 Phase 8 交付后的部署与排错，覆盖以下四个重点：

- 模型管理与隔离
- `ThinkingRecipeOrchestrator + ThinkingParameterExtraction + subagent` 全链路
- 思考证据提取与评分决策
- `.env` 与 JSON 的存储边界

## 1. 模型管理与隔离

- 全局基座模型（`DeepSeek-V4-Flash`）由系统配置解析，作为只读模型注入每个用户视图。
- 用户自定义模型按 profile 隔离：`/workspace/users/<profile>/models.json`。
- 自定义模型 API Key 不写入 `models.json`，只写 profile 私有 `.env.models`。
- 会话隔离按 profile 生效：`websocket:<profile_id>:<chat_id>`。

关键原则：

- profile 之间不共享 API Key、会话、个人模型。
- thinking recipe 为全局可复用，不做 profile 隔离（避免重复配置）。

## 2. 全链路（Orchestrator + Skill + Subagent）

WebSocket 交互链路：

1. `thinking_toggle`：检查是否支持 thinking、是否已存在可用 recipe。
2. `thinking_recipe_submit`：进入 Orchestrator 编排。
3. Orchestrator 运行时注册并路由 3 个 subagent：
   - `code_extractor_subagent`
   - `url_extractor_subagent`
   - `compare_subagent`（仅双源冲突时触发）
4. `thinking_recipe_preview`：返回可确认预览。
5. `thinking_recipe_confirm`：用户确认后保存到全局 recipe store。

协议可观测事件：

- `thinking_recipe_progress`：固定四阶段
  - `submitted`
  - `extracting`
  - `compiling`
  - `preview_ready`
- `thinking_recipe_conflict_detected`：冲突检测与自动决策信息（告知，不阻断保存）
- `thinking_recipe_preview`
- `thinking_recipe_saved`
- `thinking_recipe_error`

## 3. 证据提取与评分决策

输入源支持两类：

- 官方 URL
- 官方样例代码/控制参数格式（snippet）

分流规则：

- 仅 snippet：走 `code_extractor_subagent`
- 仅 URL：走 `url_extractor_subagent`
- URL + snippet：双提取；若签名冲突触发 `compare_subagent`

`compare_subagent` 输出：

- `decision`：`url | snippet | hybrid`
- `scoring`：多维评分（可信度、完整度、可执行性、时效性、模型匹配）
- `evidence`：保留证据条目

注意：

- 双源场景支持单侧失败降级（`dual_fallback_url` / `dual_fallback_snippet`）。
- 两侧都失败才返回 `dual_source_extraction_failed`。

## 4. `.env` 与 JSON 存储边界

### 4.1 存储位置

- Profile 模型元数据（非敏感）：
  - `users/<profile>/models.json`
- Profile 模型密钥（敏感）：
  - `users/<profile>/.env.models`
- 全局 thinking recipe（可复用配置）：
  - `system/thinking_recipes.json`
- 全局 evidence（证据快照）：
  - `system/thinking_evidence/*.json`

### 4.2 边界规则

- `models.json` 禁止存明文 API Key。
- `.env.models` 仅保存 API Key 对应 env 变量。
- `thinking_recipes.json` 仅保存参数映射与证据引用，不保存用户私钥。
- `thinking_evidence` 允许保存 URL/snippet 摘要，不得写入密钥内容。

## 5. 常见排错

- 开启 thinking 后提示 `missing_recipe`：
  - 说明模型支持 thinking 但无可用 recipe；走 submit->preview->confirm 完成配置。
- `model_does_not_support_thinking`：
  - 检查模型 `supports_thinking` 标记。
- 提交后仅收到 `thinking_recipe_error`：
  - 优先检查 URL/snippet 是否至少有一项，且 URL 为 `http/https`。
- 前端“卡在提交中”：
  - 检查是否收到 `thinking_recipe_progress` 与 `thinking_recipe_preview`；
  - 检查 gateway 与前端是否在同机并连接同一个 WebSocket 服务。
