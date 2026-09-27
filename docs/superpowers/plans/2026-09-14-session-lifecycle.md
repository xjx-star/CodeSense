# 学习会话连续性与可解释状态实施计划

> 目标：让学生在离开、刷新或重新进入学习场景后看到可信、可解释、可继续的会话状态，并让教师在已有可见范围内识别“正在学习 / 暂停过久 / 已完成”的会话。

## 约束与验收标准

- 仅基于 `ThinkingSession` 与 `ThinkingStageLog` 的既有字段投影状态；不新增表、字段或迁移，不回写旧数据。
- 状态固定为 `active`、`idle`、`completed`、`abandoned`。`in_progress` 只作为持久化兼容值，不直接暴露给新 UI。
- 空闲阈值默认为 30 分钟；状态计算接受显式 `now`，测试不依赖真实时间。
- 最后活动时间使用会话开始时间与日志最大 `created_at` 的较晚者；批量查询必须是一次聚合查询，避免会话列表 N+1。
- elapsed 是“服务器观察到的会话经过时间”或“旧数据保存的客户端计时”，明确标注来源；本轮不声称精确测量学生思考时间。
- 新状态接口必须遵守管理员、本人学生、作业创建教师或所管理班级教师的访问边界；越权请求返回 403 且不泄露会话存在性。
- 不自动跳转、不强制刷新页面；页面恢复可见时按需同步一次状态，失败时保留当前界面并给出可读提示。
- 测试覆盖纯函数、权限、API 合同、批量聚合、学生首页、竞技场可访问性和教师视图；全量回归必须继续通过。

## 研究依据

- W3C WCAG 2.2 的 2.2.1/2.2.6 要求对时间限制提供调整、延长或保留状态的路径，因此状态同步不能依赖强制倒计时或 timed redirect。
- W3C Page Visibility API 文档说明 `visibilitychange` 可用于页面隐藏/恢复的生命周期同步；只在恢复可见时请求状态，避免后台无意义轮询。
- OpenTelemetry GenAI 语义约定把流式响应的 time-to-first-chunk 作为独立指标；本轮只补会话级可解释状态，不重复已有 SSE/LLM tracing。

## 实施顺序（每项先红后绿）

### 1. 纯投影服务与序列化契约

新增 `services/session_lifecycle.py`，实现：

- `session_lifecycle_status`：终态优先，其次按最后活动时间判断 active/idle。
- `session_elapsed_seconds`：安全处理 naive/aware datetime、未来时间和缺失时间。
- `session_lifecycle_payload`：输出阶段、完成度、下一动作、是否可继续、状态、最后活动时间、elapsed 及来源。
- `latest_session_activity`：给定 ID 批量聚合日志最大时间；空集合直接返回空映射。
- `can_view_session`：管理员、本人、作业创建教师、被分配班级教师。

先在 `tests/test_session_lifecycle.py` 覆盖至少 10 个边界：四种状态、阈值边界、终态、缺失日期、未来日期、旧计时来源、三个阶段进度、空集合、批量 ID 去重、四类权限。

### 2. 状态 API 与恢复响应

在 `routes/thinking.py`：

- 新增 `GET /thinking/api/session/<id>/status`，统一返回 `{success, session}`；越权/不存在不泄露详情。
- 在 `start_session` 的新建与恢复响应中加入同一份 `session_lifecycle` 投影，保持既有键兼容。
- 在既有作业会话列表中补充投影字段，并支持安全的 `lifecycle_status` 过滤；保留原有角色门槛，不扩大现有接口权限。

先扩展 `tests/test_thinking_api.py` 或相邻现有 API 测试，验证：本人可读、管理教师可读、无关教师 403、状态字段稳定、恢复响应一致、列表过滤和单次聚合路径。

### 3. 学生首页“继续学习”入口

在 `routes/main.py` 查询当前学生最近 3 个会话（只选必要列，批量取最后活动），转换为投影后传给 `student_home.html`。模板新增可访问的最近会话卡片：

- 显示作业标题、阶段、状态、最近活动和下一步。
- active/idle 提供回到竞技场的链接；completed/abandoned 仍可查看但文案不暗示可继续。
- 空数据给出明确空态。

先加首页路由/模板回归测试，断言越权数据不会进入 context、空态可渲染、状态文案/链接存在。

### 4. 竞技场状态条与可见性同步

在 `templates/thinking/arena.html` 增加带 `role=status`/`aria-live=polite` 的状态条、阶段进度、下一动作、服务器时间说明和“同步状态”按钮；在 `thinking.css` 复用现有变量并保证窄屏不横向溢出。

在 `static/js/thinking.js`：

- 处理 `session_lifecycle` 初始响应。
- 新增按需刷新函数，更新状态条而不重载或跳转。
- `visibilitychange` 仅在回到 visible 时刷新；失败保持输入/当前阶段并提示同步失败。
- 不修改现有 SSE 节流、取消、错误收敛行为。

先加模板契约测试和静态脚本断言，再执行现有 Playwright/浏览器烟测（若环境提供）。

### 5. 教师会话概览

新增只读页面 `GET /thinking/assignment/<id>/sessions/view`，复用既有教师/管理员角色门槛与 `can_view_session` 过滤，显示会话状态、学生、阶段、完成度、最后活动和下一动作，并链接到已有作业会话 JSON/API。页面说明状态是活动投影，不是精确思考时长。

先加访问、空态、状态筛选和模板渲染测试；不修改现有宽权限 JSON 接口的默认授权逻辑。

### 6. 验证与候选交付

- 单测红绿后运行受影响测试。
- 运行 `compileall`、模板/静态资源检查、完整 `pytest -q --disable-warnings`、必要的浏览器窄屏烟测。
- 检查 `git diff --check`、变更文件和运行数据未被纳入版本控制。
- 更新 `docs/ops-runs/2026-09-14-session-lifecycle.md`：价值、触达端、文件、研究链接、测试证据、发布风险与回滚方式。
- 只保留隔离候选，不合并、不 push、不部署，等待用户明确同意。

## 预期用户价值

学生可以在刷新或暂时离开后快速判断“从哪里继续”，教师可以区分真正活跃的学习会话与长时间未更新的会话；状态来源和时间口径被明确展示，减少把客户端计时误读为实际思考时长的风险。

## 代码审阅重点

- 查询是否保持批量聚合、避免首页/教师列表 N+1。
- 新状态接口是否严格限定新权限边界并避免 404/403 泄露。
- 空/未来/时区混用时间是否稳定，旧 `total_time_seconds` 是否保持向后兼容。
- 页面恢复逻辑是否只同步状态，不丢输入、不打断 SSE、不自动刷新。
