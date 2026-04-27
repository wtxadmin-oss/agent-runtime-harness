# 动态模型切换 + 思考证据化配置实施计划 v1.0

## 目标摘要
- 保留原 `plan/llmchange.md` 的 Phase 0-6 主线能力。
- 新增“思考参数证据化提取与全局复用”机制。
- 明确：`basellm` 仍为全局默认模型（含 chat/思考能力/全局环境配置），不改其底层 provider 逻辑；是否启用思考由用户决定，默认关闭。
- 仅 `api_key` 放 `.env`；其余可复用配置（思考参数配方、证据）使用全局 JSON 持久化。
- URL 与样例冲突时采用自动评分决策，不做单一固定优先级。

---

## Phase 0：目标冻结与约束确认

### 冻结决策
- 用户可在右侧栏配置与切换模型：`modelName`、`apiKey`、`baseUrl`、`supportsThinking`。
- 未选择自定义模型时使用全局 `basellm`（当前 DeepSeek 链路）。
- 已选择自定义模型时按会话生效，可切换。
- 流式保持：`reasoning_delta -> delta -> stream_end`。
- API Key 不写 `config.json`，采用 `.env`（按现有策略）。
- `basellm` 逻辑不改：保持全局配置与能力。
- 思考模式默认关闭；用户显式打开后才进入“是否可启用”判断。

### 验收标准
- 团队对上述行为无歧义，后续不再改策略口径。

---

## Phase 1：后端数据层（用户隔离模型仓库）

### 实现内容
- 保持 `ModelStore`（profile 隔离）：
  - `workspace/users/<profile_id>/models.json`（非敏感）
  - `workspace/users/<profile_id>/.env.models`（API Key）
- 能力：`list/add/delete/get/update`，list 不返回明文 key。

### 验收标准
- profile 之间模型互相隔离；
- `models.json` 不出现明文 key；
- `.env.models` 可正确读写。

---

## Phase 2：WebSocket 协议与服务端路由

### 实现内容
- 模型协议：`model_list/model_add/model_delete/model_select` 与对应事件。
- `message` 支持 `model_id`，写入 metadata。
- `attach` 返回会话当前模型选择。
- 所有模型管理动作按 profile 绑定隔离。

### 验收标准
- 增删查选模型闭环可用；
- `attach` 可恢复会话模型；
- 不可跨 profile 读写。

---

## Phase 3：AgentLoop 动态路由（模型覆盖 + 回退）

### 实现内容
- `_model_id` -> 解析模型配置 -> 临时 provider/runner 覆盖本次消息。
- 无效/缺失/读取失败自动回退 `self.runner + self.model`。
- 会话元数据持久化：`selected_model_id/name/supports_thinking`。

### 验收标准
- 不同会话可并行使用不同模型；
- 删除或失效模型后平滑回退默认链路。

---

## Phase 4：思考模式拆分实施（4A-4E）

### 目标
- 将原 Phase4 与 4.5 拆分为有依赖顺序的子阶段，先稳定前后端基线，再接入 skill/subagent，避免返工。
- 保持 `basellm` 既有 provider 逻辑不变，仅改“思考是否启用”的流程控制。

### 4A：基线联动（无 skill）
#### 实现内容
- 思考按钮默认关闭。
- 当用户打开思考且当前模型无配方时，触发“需要配置思考参数”提醒弹窗。
- 聊天流程先暂停，待用户做出决定后继续：
  - 完成配置流程后继续；
  - 或关闭提醒弹窗后按非思考路径继续。

#### 验收标准
- 无配方时不会直接带 thinking 参数调用；
- 弹窗关闭或配置完成后可恢复当前对话流程。

### 4B：后端配方与证据存储接口
#### 实现内容
- 新增全局存储（非 profile 隔离）：
  - `workspace/system/thinking_recipes.json`
  - `workspace/system/thinking_evidence/*.json`
- 新增基础 tools（先不接 subagent）：
  - 配方查询（lookup）
  - 证据转标准参数（compile）
  - 配方保存（save）
- `lookup/compile` 注册到 Agent function calling（主 Agent + subagent 可调用）。
- `save` 保持走“前端确认 -> 后端保存”链路，不开放给 function calling（避免绕过确认）。
- API Key 继续仅存 `.env`，不进入 JSON。

#### 验收标准
- 可完成“查询-预览-保存”闭环；
- 全局配方可被所有 profile 复用。

### 4C：前端配置流程（无 subagent）
#### 实现内容
- 弹窗输入仅允许两类来源：
  1. 官方 URL
  2. 官方样例代码/控制参数格式
- 提交后由后端返回参数预览，用户确认后落库。
- 用户关闭提醒弹窗时，当前消息继续按非思考发送。

#### 验收标准
- 无配方开启思考时，前端流程可完整走通且不会卡死会话。

### 4D：ThinkingParameterExtraction + subagent 设计冻结（不落地全编排）
#### 实现内容
- 冻结 orchestrator 全链路设计与边界（实现顺延至 Phase6）：
  - 前端开启思考 -> lookup 存在性检查 -> 缺失时进入证据流程 -> compile 预览 -> 人类确认 -> save
- 冻结 1 个数据提取 skill（`ThinkingParameterExtraction`，使用 `nanobot/skills/skill-creator`），在 skill 内定义 3 个 subagent 角色：
  - `code_extractor_subagent`：仅代码/参数输入时执行（清洗/提取/证据标注）
  - `url_extractor_subagent`：仅 URL 输入时执行（抓取/清洗/正文/示例提取/证据标注）
  - `compare_subagent`：URL+代码同时提交且检测冲突时执行（评分比对并产出最终参数）
- 冻结路由规则：
  - 仅 snippet：只走 `code_extractor_subagent`
  - 仅 URL：只走 `url_extractor_subagent`
  - URL + snippet：先跑前两者并做一致性检测；仅冲突时才走 `compare_subagent`
- 冻结工具边界：
  - `thinking_recipe_lookup/compile` 由 orchestrator 调用；
  - `save` 不经 LLM 自动调用，继续由前端确认后走后端保存链路。
- 冻结冲突评分维度：可信度、完整度、可执行性、时效性、模型匹配度。
- 术语边界：`ThinkingParameterExtraction` 负责提取/比对规则；`ThinkingRecipeOrchestrator` 负责流程编排。

#### 验收标准
- 全链路编排设计、输入输出契约、状态流与触发规则无歧义；
- 后续 Phase6 可直接实现，无需再改 4D 决策口径。

### 4E：端到端启用门槛收口
#### 启用门槛（三条件）
- `user_toggle_enabled == true`
- `model_supports_thinking == true`
- `global_recipe_exists && validated == true`

#### 实现内容
- 三条件满足才注入 thinking 参数；
- 不满足时走 4A 的提醒/暂停/继续分支，普通流式不受影响。

#### 验收标准
- 全链路稳定：暂停 -> 配置或关闭 -> 恢复对话 -> 流式正常。

### WebSocket 增量协议（用于 4B-4E）
- 入站：
  - `thinking_toggle`
  - `thinking_recipe_submit`（url/snippet）
  - `thinking_recipe_confirm`
- 出站：
  - `thinking_recipe_required`
  - `thinking_recipe_preview`
  - `thinking_recipe_saved`
  - `thinking_recipe_error`

---

## Phase 5：前端 UI 与状态管理

### 实现内容
- Provider 增加 thinking recipe 状态：
  - `thinkingProfileExists/Enabled/DocUrl`（或等价字段）。
- Header 增加“思考参数配置”入口（按当前模型）。
- 思考按钮默认关闭；无配方或不支持时禁用并解释原因。
- `useNanobotStream` 发消息时仅在三条件满足才带 `thinking` 参数。
- 聊天框持续显示当前实际模型。

### 验收标准
- 用户可完成：添加模型 -> 配置思考证据 -> 预览确认 -> 保存 -> 启用思考 -> 对话。
- 切会话/重连可恢复状态。

---

## Phase 6：全链路 Orchestrator 内核实现（不改协议）

### 实现内容
- 新增后端 `ThinkingRecipeOrchestrator` 内核（状态机 + 分流 + 失败处理）：
  - `submitted -> lookup_checked -> source_routed -> extracted -> conflict_checked -> (optional_compared) -> compiled_preview -> awaiting_confirm -> saved/error`
- 沿用 `ThinkingParameterExtraction` skill 定义的 subagent 执行规范。
- 按输入类型执行固定分流：
  - 仅 snippet -> `code_extractor_subagent`
  - 仅 URL -> `url_extractor_subagent`
  - URL + snippet -> 先双提取再一致性检查，冲突时触发 `compare_subagent`
- 内核复用 function-calling tools：
  - `thinking_recipe_lookup`
  - `thinking_recipe_compile`
- 单来源提取失败时直接报错并要求用户重提，不自动兜底猜测参数。

### 验收标准
- 在不改 WebSocket 协议的前提下，内核状态流与分流规则可独立运行并通过服务层测试；
- `save` 仍保留“用户确认后执行”的边界，不被内核绕过。

## Phase 7：协议改造与端到端接线

### 实现内容
- 继续沿用 `ThinkingParameterExtraction` skill 的提取/比对规范，保持与 Orchestrator 内核一致。
- 将 Orchestrator 内核接入 WebSocket 入口：
  - `thinking_toggle`（触发是否需要配置判断）
  - `thinking_recipe_submit`（触发编排与预览）
  - `thinking_recipe_confirm`（触发保存）
- 增加/完善出站事件用于链路可观测：
  - `thinking_recipe_progress`
  - `thinking_recipe_conflict_detected`
  - `thinking_recipe_preview`
  - `thinking_recipe_saved`
  - `thinking_recipe_error`
- 前端适配协议增量，保持 4C 交互主流程不变（暂停 -> 配置 -> 预览 -> 确认 -> 恢复发送）。

### 验收标准
- Orchestrator 与前端通过 WebSocket 完整闭环；
- 配置流程与消息恢复行为无卡死、无重复发送、无越权保存。

## Phase 8：检测（测试、回归、文档）

### 后端测试
- ModelStore/ThinkingRecipeStore：
  - 隔离与全局边界、读写、版本更新、损坏恢复。
- Orchestrator：
  - 路由分流、冲突判定、状态流、失败处理、人类确认保存闸门。
- WebSocket：
  - `model_*` 协议；
  - `thinking_recipe_*` 协议；
  - 三条件门槛判定；
  - 无配方时 fallback 到普通流式。
- AgentLoop：
  - 有配方时参数注入；
  - 缺配方或冲突未确认时不注入。

### 前端测试
- Header/配置流程交互（含禁用态）。
- Stream hook 出站参数校验（满足条件才发送 thinking）。
- 会话切换恢复与错误提示行为。
- 协议进度事件渲染与异常提示行为。

### 文档
- 补充“模型管理与隔离”；
- 补充“orchestrator + ThinkingParameterExtraction + subagent 全链路”；
- 补充“思考证据提取与评分决策”；
- 明确“.env 与 JSON 的存储边界”。

### 验收标准
- 新增用例通过，关键旧用例无回归；
- 文档可支持独立部署与排错。

---

## 里程碑建议
- M1：完成 Phase 1-3（后端模型闭环 + 回退）。
- M2：完成 Phase 4A-4C + Phase 5（思考基线联动 + 配置流程 + UI）。
- M2.5：完成 Phase 4D-4E（设计冻结 + 启用门槛收口）。
- M3：完成 Phase 6（Orchestrator 内核）。
- M3.5：完成 Phase 7（协议改造与端到端接线）。
- M4：完成 Phase 8（检测、回归与文档齐备）。

---

## 默认假设（本版）
- `basellm` provider 侧实现不改，只改“是否启用思考”的门槛与参数来源流程。
- 官方 URL 与样例冲突时走自动评分，不做硬编码优先级。
- 全局思考配方可被所有 profile 复用；profile 仅隔离个人会话与密钥信息。
