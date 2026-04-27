# 动态模型切换 + 思考证据化配置实施计划 v1.1

> 本版延续 `llmchange1.0` 结构，但按真实实施路径更新：保留 phase 框架，写入已发生的修正决策与最终口径，避免计划与实现脱节。

## 目标摘要
- 保留 `llmchange1.0` 的 Phase 0-8 主线能力与边界。
- 明确全局基座模型为 `DeepSeek-V4-Flash`，来源于全局配置/env，不挂在某个 profile 下。
- 用户可配置自定义模型：`name/model_name/base_url/api_key/supports_thinking`。
- `api_key` 仅存 profile 私有 `.env.models`；思考配方与证据使用全局 JSON 复用。
- 思考默认关闭；仅在门槛满足时发送 thinking 参数。
- 冲突场景采用自动评分决策（URL/代码证据比对），并对用户可观测。

---

## Phase 0：目标冻结与约束确认

### 冻结决策
- 右侧栏支持添加/切换用户自定义模型。
- 未选择自定义模型时统一回退 `basellm`（全局配置）。
- `basellm` provider 逻辑不改，只改“是否注入 thinking 参数”的门槛。
- 流式保持：`reasoning_delta -> delta -> stream_end`。
- `save` 只能由用户确认触发，禁止自动落库。

### 验收标准
- 模型来源、隔离边界、思考注入边界无歧义。

---

## Phase 1：后端数据层（用户隔离模型仓库）

### 实现内容
- `ModelStore` profile 隔离：
  - `workspace/users/<profile_id>/models.json`（非敏感元数据）
  - `workspace/users/<profile_id>/.env.models`（API Key）
- 提供 `list/add/delete/get/update_selection_related_fields`。
- list/get 结果不泄露明文 key。

### 真实修正
- 修复“默认用户配置污染全局”的设计偏差：
  - 引入全局只读基座模型注入；
  - profile 仅承载个人模型与密钥。

### 验收标准
- profile 之间模型与密钥互不泄露。
- `models.json` 不出现明文 API Key。

---

## Phase 2：WebSocket 协议与服务端路由

### 实现内容
- 模型协议：`model_list/model_add/model_delete/model_select`。
- 出站事件：`model_list/model_saved/model_deleted/model_selected`。
- `attach` 返回会话已选模型及 thinking 可用状态。
- `message` 支持 `model_id` 入站。

### 真实修正
- `model_add` 在 `supports_thinking=true` 时，要求 `thinking_doc_url` 或 `thinking_snippet` 至少一项。

### 验收标准
- 增删查选闭环可用。
- 会话重连/attach 可恢复模型选择。

---

## Phase 3：AgentLoop 动态路由（模型覆盖 + 回退）

### 实现内容
- `_model_id` 解析 profile 模型，构建临时 provider/runner 覆盖本次消息。
- 模型缺失、配置不完整、读取失败时自动回退基座链路。
- 会话元数据持久化：`selected_model_*`。

### 真实修正
- 当模型 `supports_thinking=false`，强制不注入 `_reasoning_effort`。

### 验收标准
- 多会话可并行使用不同模型。
- 失效模型不导致中断，能平滑回退。

---

## Phase 4：思考模式拆分实施（4A-4E）

### 4A：基线联动（无 skill）
#### 实现内容
- 思考按钮默认关闭。
- 用户开启思考但缺配方时触发提醒并暂停本次发送。
- 用户可选择“配置后继续”或“关闭提醒并继续普通发送”。

#### 真实修正
- 修复“提醒弹窗无法关闭”问题：无 pending 发送时允许直接关闭。

#### 验收标准
- 缺配方时不会误注入 thinking。
- 会话不会因弹窗卡死。

### 4B：后端配方与证据存储接口
#### 实现内容
- 全局存储：
  - `workspace/system/thinking_recipes.json`
  - `workspace/system/thinking_evidence/*.json`
- 能力：`lookup/compile/save`。
- `lookup/compile` 供编排调用；`save` 保持前端确认后执行。

#### 验收标准
- 可完成查询-预览-确认保存闭环。
- 配方可跨 profile 复用。

### 4C：前端配置流程（无 subagent）
#### 实现内容
- 仅接受两类输入：官方 URL 或官方代码/参数格式。
- `submit -> preview -> confirm` 后落库。

#### 验收标准
- 配置流程可用且不中断对话。

### 4D：ThinkingParameterExtraction + subagent 设计冻结
#### 实现内容
- 冻结单 skill：`ThinkingParameterExtraction`。
- 冻结 3 个 subagent 角色：
  - `code_extractor_subagent`
  - `url_extractor_subagent`
  - `compare_subagent`（仅冲突时）
- 冻结路由：snippet-only / url-only / dual-source+冲突比对。
- 冻结冲突评分维度：可信度、完整度、可执行性、时效性、模型匹配。

#### 验收标准
- 提取职责与编排职责清晰分离。

### 4E：端到端启用门槛收口
#### 启用门槛（三条件）
- `user_toggle_enabled == true`
- `model_supports_thinking == true`
- `global_recipe_exists && validated == true`

#### 验收标准
- 门槛不满足时回退普通流式。

---

## Phase 5：前端 UI 与状态管理

### 实现内容
- Header：模型显示/切换/添加表单。
- Stream hook：仅三条件满足时发送 thinking 参数。
- 聊天框可见当前模型。
- 会话切换恢复模型与 thinking 可用状态。

### 真实修正
- 修复 `globalHandlers` 未初始化导致前端崩溃：
  - `Cannot read properties of undefined (reading 'globalHandlers')`。

### 验收标准
- 添加模型 -> 配置思考参数 -> 保存 -> 启用 -> 发消息全流程可用。

---

## Phase 6：全链路 Orchestrator 内核实现（不改协议）

### 实现内容
- 新增 `ThinkingRecipeOrchestrator` 状态流：
  - `submitted -> lookup_checked -> source_routed -> extracted -> conflict_checked -> compiled_preview -> awaiting_confirm -> saved/error`
- runtime 注册 3 个 subagent。
- 分流：
  - 仅 snippet -> code
  - 仅 URL -> url
  - URL+snippet -> 双提取 + 冲突时 compare

### 真实修正
- dual-source 策略优化为：
  - 两侧都失败才报错；
  - 单侧成功时允许降级继续（`dual_fallback_url` / `dual_fallback_snippet`）。

### 验收标准
- 分流、冲突、失败路径均可测。
- `save` 仍不被自动绕过。

---

## Phase 7：协议改造与端到端接线

### 实现内容
- WebSocket 增强事件：
  - `thinking_recipe_progress`（固定四阶段）
  - `thinking_recipe_conflict_detected`
  - `thinking_recipe_preview`
  - `thinking_recipe_saved`
  - `thinking_recipe_error`
- 将 orchestrator 接入 `thinking_recipe_submit`。
- 前端适配事件并保持原交互主流程。

### 真实修正
- 冲突事件为“告知不阻断”，仍允许用户确认保存。
- 进度/冲突状态在 preview/saved/error 时清理，防止 UI 残留。

### 验收标准
- 配置流程无卡死、无重复发送、无越权保存。

---

## Phase 8：检测（测试、回归、文档）

### 后端测试
- `ModelStore`：隔离、密钥边界、损坏恢复、去重清洗。
- `ThinkingRecipeStore`：编译/保存、替换更新时间、损坏恢复。
- `Orchestrator`：分流、冲突、降级、进度状态。
- `WebSocket`：`thinking_recipe_*` 事件顺序与门槛。
- `AgentLoop`：有/无 recipe 的注入与回退。

### 前端测试
- Header/配置流程。
- `useNanobotStream` thinking 出站门槛。
- 会话切换恢复与错误提示。
- `thinking_recipe_progress/conflict` 渲染与清理。

### 文档
- 新增运维文档：`docs/thinking-parameter-ops.md`。
- 覆盖：模型隔离、全链路、评分决策、`.env` 与 JSON 边界。

### 验收标准
- 新增用例通过，关键旧用例无回归。
- 文档可支持独立部署与排错。

---

## 里程碑建议（v1.1）
- M1：完成 Phase 1-3。
- M2：完成 Phase 4A-4C + Phase 5。
- M2.5：完成 Phase 4D-4E（设计冻结 + 门槛收口）。
- M3：完成 Phase 6（Orchestrator 内核 + runtime subagent 注册）。
- M3.5：完成 Phase 7（协议增强与端到端接线）。
- M4：完成 Phase 8（测试、回归、文档）。

---

## 默认假设（本版）
- `basellm` provider 实现不改，重点只在注入门槛与参数来源流程。
- 冲突由自动评分决策，不做硬编码单一优先级。
- 全局 recipe 可复用，profile 仅隔离会话与密钥。
