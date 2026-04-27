# `userchange.md` 替换稿：基于 `profile_id` 的会话级隔离二次开发计划

## 概要
- 不做登录/鉴权，不引入 JWT。
- 用户切换本质是 `profile_id` 切换；`chat_id` 继续表示该用户下的会话。
- 目标是演示级隔离：A 看不到 B 的会话，B 也看不到 A。
- 切换起点明确为：`/webui/bootstrap?profile_id=...`，不是从 WebSocket 帧开始。

## Phase 1：隔离模型与键规范（后端基础）
- 定义演示 profile：`demo_alice`、`demo_bob`（先硬编码，后续可落盘）。
- 会话键升级为：`websocket:{profile_id}:{chat_id}`。
- 兼容旧键：`websocket:{chat_id}`（读取时迁移到 `demo_alice`）。
- 输出约束文档：统一术语 `profile_id`（演示用户）与 `chat_id`（会话）。

**验收**
- 新会话都按三段键写入。
- 旧会话可读且可迁移，不丢历史。

## Phase 2：Bootstrap 与 Token 绑定 profile（切换入口）
- 扩展 `GET /webui/bootstrap` 支持 `?profile_id=`。
- `bootstrap` 响应新增：
  - `profile_id`
  - `profiles: [{id,name}]`
- token 池从 `token -> expiry` 扩为 `token -> {expiry, profile_id}`。
- WebSocket 握手校验 token 后，把 `profile_id` 绑定到连接上下文。

**验收**
- 同一时刻不同 token 可绑定不同 profile。
- 缺省 profile 时回落到 `demo_alice`。

## Phase 3：会话读写隔离（API + WS 路由）
- WebSocket 收到 message 时，通过 `session_key_override` 写入 `websocket:{profile_id}:{chat_id}`。
- `/api/sessions` 仅返回当前 token 绑定 profile 的会话。
- `/api/sessions/{key}/messages` 与 `/delete` 仅允许访问当前 profile 前缀键；跨 profile 返回 404。
- 继续保留消息 fan-out 的 `chat_id` 路由机制，但存储与查询必须按 `profile_id` 作用域。

**验收**
- Alice/Bob 各自产生会话后，列表互不可见。
- A token 访问 B key 的 messages/delete 失败（404）。

## Phase 4：WebUI 用户切换（侧边栏 A/B）
- 侧边栏增加 profile 切换控件（A/B）。
- 切换时流程固定：
  1. 关闭旧 WebSocket 客户端
  2. 用新 `profile_id` 重新 bootstrap
  3. 重建并连接新客户端
  4. 刷新 sessions
  5. 激活该 profile 最近会话（无会话则空态）
- `useSessions` 创建会话 optimistic key 改为三段键。
- `api.ts` 的 key 解析逻辑支持 `websocket:profile:chat`。

**验收**
- 切换无需整页刷新。
- 切换后 sidebar 列表立即变化且仅显示当前 profile 数据。

## Phase 5：测试与回归（可执行）
- 后端测试：
  - `test_websocket_http_routes.py`：bootstrap profile、list 隔离、messages/delete 隔离、旧键迁移。
  - `test_websocket_channel.py`：写入 key 包含 profile。
- 前端测试：
  - `useSessions.test.tsx`：三段 key 解析与创建。
  - `app-layout/thread-shell`：切换 profile 后列表与 active chat 隔离刷新。
- 回归命令：
  - `pytest tests/channels/test_websocket_http_routes.py tests/channels/test_websocket_channel.py`
  - `cd webui && npm test -- useSessions app-layout thread-shell`

## 关键接口/类型变更
- `GET /webui/bootstrap?profile_id=<id>`
- `BootstrapResponse` 增加 `profile_id` 与 `profiles`
- Session key 规范从两段改三段：`websocket:{profile_id}:{chat_id}`

## 默认假设
- 仅本地演示，不提供真实安全边界。
- profile 仅用于会话级隔离，不涉及账号体系。
- 旧数据默认归入 `demo_alice` 以保证升级平滑。

---

## 附加计划：支持手动创建用户 Profile（无注册流程）

### 目标
- 保持当前“本地演示模式、无登录”。
- 除 `Alice/Bob` 外，用户可在 WebUI 里手动创建新的 profile。
- 新 profile 的隔离逻辑与现有完全一致（会话、USER/SOUL、memory 都隔离）。
- 创建时只输入 `username`（作为展示名），`profile_id` 可由 name 生成。

### Phase 1：Profile 存储从硬编码改为可持久化（后端）
**改动**
1. 把 `nanobot/profile/store.py` 从固定 `DEMO_PROFILES` 改为文件存储（如 `workspace/users/profiles.json`）。
2. 增加接口函数：
   - `list_profiles()`
   - `create_profile(name: str) -> Profile`
   - `normalize_profile_id(value: str | None) -> str`
3. `create_profile` 规则：
   - `name` 非空、去首尾空格。
   - `profile_id` 由 `name` 生成 slug（小写、空格转 `-`、仅 `[a-z0-9_-]`）。
   - 冲突自动加后缀（`-2`, `-3`）。
4. 初始化逻辑：
   - 若无存储文件，自动创建并写入 `demo_alice/demo_bob`。

**验收**
- 重启后新建 profile 仍存在。
- 重名可创建且 `profile_id` 唯一。



### Phase 2：新增 Profile 管理 API（后端）
**改动**
在 `WebSocketChannel` HTTP 路由里新增：
1. `GET /api/profiles`
   - 返回 `{ profiles, current_profile_id }`（当前可由 token 绑定推导）。
2. `POST /api/profiles/create`
   - 请求体：`{ name: string }`
   - 返回：`{ profile: { id, name } }`
3. 沿用现有 bootstrap token 认证（Bearer），不引入新鉴权机制。

**验收**
- 前端可通过 API 拉取/创建 profile。
- 创建后立即可用于 bootstrap 切换。


### Phase 3：WebUI 增加“创建用户”交互
**改动**
1. 在 `Sidebar` 的 profile 区域增加“新建用户”按钮（`+`）。
2. 弹出轻量输入框（Dialog）输入 `username`。
3. 提交调用 `POST /api/profiles/create`，成功后刷新 profile 列表并自动切换到新 profile。
4. 切换流程复用现有：
   - 关闭旧 socket
   - `fetchBootstrap(profile_id)`
   - 重建 client
   - 刷新 sessions

**验收**
- 可连续创建多个用户。
- 新用户切换后看到空会话或其自身会话，不看到他人会话。


### Phase 4：Profile 目录与模板初始化
**改动**
`create_profile` 时自动创建：
- `workspace/users/{profile_id}/memory/`
- `workspace/users/{profile_id}/USER.md`
- `workspace/users/{profile_id}/SOUL.md`
- `workspace/users/{profile_id}/memory/MEMORY.md`
- `workspace/users/{profile_id}/memory/history.jsonl`

内容可从当前模板复制（与 `demo_alice/bob` 一致）。

**验收**
- 新 profile 首次使用不报文件缺失。
- 问“我是谁”时可读取该 profile 自己的 USER/SOUL。

### Phase 5：测试与回归
**后端测试**
- `profile/store`：
  - 首次初始化写入 demo 用户
  - `create_profile` 去重、slug、持久化
- `websocket` HTTP 路由：
  - `/api/profiles` 返回正确
  - `/api/profiles/create` 创建成功并可 bootstrap 切换

**前端测试**
- Sidebar：
  - 创建弹窗交互
  - 创建后自动切换
- App：
  - 新建 profile 后 `Alice/Bob/newUser` 往返切换正常

**验收脚本**
- 后端 `pytest`（profile + websocket 路由相关）
- 前端 `vitest`（app-layout/sidebar/useSessions 相关）

### 关键实现约束（建议默认）
1. `profile_id` 不直接等于原始 name，使用 slug + 去重。
2. name 可重复显示，但 id 必须唯一稳定。
3. 不做删除 profile（先避免误删数据），本期只做“创建 + 切换 + 列表”。
