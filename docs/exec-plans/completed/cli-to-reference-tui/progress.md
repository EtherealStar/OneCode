# Progress

## Current State

Status：M1、M2、M3 与 M4 已实现并有测试证据；未提交（按用户要求）。M5.1 已实现；M5.2 已切换默认 TTY 入口并加固依赖边界，但旧 `ui/cli/terminal/` 物理删除、随旧机制退役的测试迁移及真实终端人工烟测尚未完成。

Current milestone：M5.1 完成；M5.2 部分完成（入口切换与边界检查），剩余旧 TTY 路径退役与人工验收。

Last updated：2026-09-18，Asia/Shanghai。

本轮在 Windows 重建了失效的 Linux `.venv`（原 `.venv` 的 `pyvenv.cfg` 指向 `/home/rowla/...`，Windows `uv` 无法复用），`uv sync --dev` 成功；`pyproject.toml`、`uv.lock` 无差异。工作区既有大量修改及未跟踪 `reference/`、目标设计等均保留；本次只改动 M1 涉及的运行时与测试文件。

M1 交付：
- `services/context/transcript.py`：记录新增可选身份字段（`assistant_call_id`/`model_turn_index`/`source_uuid`/`record_kind`）；`load_messages(restore_external_results=)`、`read_records()`、`rewrite_records()`、`write_guard()`；flush 持锁写盘。
- `services/context/message_store.py`：`active_records()`、`last_record_uuid`、`replace_messages_for_compaction(source_uuids=)`、`finalize_interrupted_run()`。
- 新增 `services/context/message_shapes.py`、`services/context/run_facts.py`、`application/history.py`。
- `services/compaction/service.py`：compaction 传递复制来源 UUID。
- `core/loop.py`：保留运行事实（未定稿文字、声明、已完成结果、存储确认），取消后可由 Controller 冻结。
- `services/tools/executor.py`：并发批量取消/异常时先等待前台 handler 观察到取消。
- `services/context/recovery.py`：删除合成中断结果策略，改为剪除未配对声明/孤立结果并真实修复磁盘。

## Progress

- [x] (2026-09-17 +08:00) 阅读根架构、核心信念、三个目标设计、现状 CLI/消息渲染/上下文设计、活跃计划、技术债、PLANS 与计划包格式/模板。
- [x] (2026-09-17 +08:00) 审计 `reference/ui/` 的 projection、session facade、viewport、layout index、cache、scheduler、App/Composer/renderer/theme 与当前 CLI、loop、store/recovery。
- [x] (2026-09-17 +08:00) 执行环境同步及完整测试基线，记录真实失败。
- [x] (2026-09-17 +08:00) 创建 plan/execution/decisions/progress 四文件，明确五个宏里程碑及验收。
- [x] (2026-09-17 +08:00) 校验四文件的 39 个本地 Markdown 链接及标题锚点，全部可解析；校验现有路径与明确标为目标的新产物，并检查空白格式。
- [x] M1：取消与历史记录基础。
  - [x] M1.1：稳定记录身份、压缩来源、历史保留引用读取。
  - [x] M1.2：运行事实、受控整理、恢复与故障注入。
- [x] M2：共用 Controller。
  - [x] M2.1：应用装配、单 worker、快照与观察。
  - [x] M2.2：命令、交互、队列、切换与关闭。
  - [x] M2.3：batch Adapter。
- [x] M3：ConversationProjection。
  - [x] M3.1：移植归并、固定身份与声明顺序。
  - [x] M3.2：权威修正、同步、详情与管线集成。
- [x] M4：ConversationView。
  - [x] M4.1：Textual 版本验证、主题与虚拟视口。
  - [x] M4.2：刷新、Markdown、详情与资源回收。
- [ ] M5：完整交互与入口切换。
  - [x] M5.1：App、Composer、命令视图与 Modal。
  - [~] M5.2：入口已切换、边界检查已加固；旧 TTY 删除、旧机制测试退役与真实终端人工验收待完成。

## Surprises & Discoveries

Observation：真正选定的参考是根目录 Python `reference/ui/`。Evidence：三个目标设计的相对链接及参考 `app.py` 的 Textual imports；`docs/references/ui/` 主要为 TS/TSX。

Observation：参考代码不能整目录搬运。Evidence：`reference/ui/session_facade.py` import UI SessionSnapshot 且 `_publish` await callbacks；`projection.py` 按单 tool ID 归并、InputQueued 进入消息列表、孤立结果 fallback 独立显示；`renderers/tool.py` import 参考具体工具输入模型；`composer.py` import `miniagent.workspace_references`。

Observation：现有记录接口缺少 UI 所需的稳定关联。Evidence：`MessageStore._append` 生成 UUID 只传给 transcript，不在返回值中交付；compaction 重新 `_append` replacement，原来源未记录。计划先处理存储，不将身份问题藏到 View。

Observation：工具完成事件不等于持久化确认。Evidence：`core/loop.py::_stream_tool_execution` 先收集并 yield result，外层后调用 `append_tool_results`；`recovery.py` 当前合成缺失结果。M1 要覆盖取消在两者之间发生。

Observation：参考虚拟化仍有线性工作，Markdown 分块也不证明完整语义。Evidence：`layout_index.py::update_height` 调用 `_rebuild_prefixes`；`render_cache.py::_scan` 遍历 source，空行分块。M4 以实际挂载数/IO/最终文本验收。

Observation：活跃计划模式计划已有实现记录，不是等待本计划从零创建。Evidence：`plan-mode-full-implementation.md` Progress 与 `ui/cli/commands.py` 的 `/plan show|open|approve|reject`、`services/questions/`、`tools/exit_plan_mode/tool.py`。M2/M5 保留其业务。

## Validation Evidence

Command：`uv sync --dev`。

Result：成功，Resolved 44 packages，Installed 41 packages；跨文件系统 hardlink 不可用时 uv 自动改为 copy。使用 Linux CPython 3.12.14。

Command：`uv run python -m pytest tests -q`。

Result：退出码 1；**614 passed, 7 failed in 45.05s**。运行时与测试源代码尚未修改，属于本次调查基线；未重复运行或修复，不对原因作未经验证的判断。

失败明细：

- `tests/test_bash_tool.py::test_bash_descriptor_schema_and_prompt`：prompt 不包含断言要求的 `Tree-sitter`。
- `tests/test_cli_terminal.py::test_user_submitted_uses_reverse_style_dark`：捕获输出为 `> hello\n`，没有期望 ANSI SGR。
- `tests/test_cli_terminal.py::test_restored_user_messages_use_explicit_reverse_style`：恢复用户行同样缺少期望 ANSI SGR。
- `tests/test_mcp_manager.py::test_mcp_connection_manager_discovers_and_calls_stdio_tools`：状态为 `pending`，测试期望 `connected`。
- `tests/test_openai_compatible_provider.py::test_catalog_contains_builtin_providers`：缺少测试期望的 `claude-openai-compatible` catalog 项。
- `tests/test_path_sandbox_guard.py::test_root_worktree_does_not_allow_arbitrary_paths`：实际 `allow`，测试期望 `ask`。执行安全相关改动前必须调查，不按 UI 兼容问题忽略。
- `tests/test_search_tools.py::test_registry_generates_search_tool_schemas_and_prompts`：prompt 首段为 `Purpose`，测试期望工具名 `glob/grep`。

文档校验：使用 Python 扫描四文件 Markdown 链接并校验文件/目录及标题锚点，39 项全部通过；核对命名的现有路径与明确标注的目标产物，四文件均无行尾空白。`git status --short -- docs/exec-plans/active/cli-to-reference-tui pyproject.toml uv.lock` 仅列出新计划目录。新模块的测试与真实终端烟测尚未执行，因为实现尚未开始。

### M1 验证（2026-09-18，Windows）

Command：`uv sync --dev`。

Result：成功（重建 Windows venv；CPython 3.14.5，Windows 11）；`pyproject.toml`、`uv.lock` 未改动。

Command：`uv run --no-sync python -m pytest tests/test_conversation_history.py tests/test_session_interrupt_cleanup.py tests/test_context_recovery.py tests/test_jsonl_session_persistence.py tests/test_async_loop.py tests/test_compaction_service.py -q`。

Result：退出码 0；**62 passed**（含新增 `test_conversation_history.py` 10 项、`test_session_interrupt_cleanup.py` 13 项；随后新增 staging 写失败与来源持久化断言，最终全套为 644 passed）。

Command：`uv run --no-sync python -m pytest tests -q`。

Result：退出码 1；**644 passed, 3 failed in 11.29s**。3 个失败均为计划基线已记录、与 M1 无关的既有失败（Windows 上 `test_bash_tool::test_bash_descriptor_schema_and_prompt`、`test_openai_compatible_provider::test_catalog_contains_builtin_providers`、`test_search_tools::test_registry_generates_search_tool_schemas_and_prompts`）。`test_mcp_manager` 本轮通过（上次基线为失败，属平台/时序波动）。无新增失败。

Command：`uv run --no-sync python -m pytest tests/test_import_boundaries.py -q` → 4 passed；`uv run --no-sync python -m compileall core services infrastructure application -q` → 退出码 0；先前记录的 `_synthetic_interrupted_tool_result` 合成结果预期已按 M1.2 更新为剪除并真实修复磁盘。

证据文件：`tests/test_conversation_history.py`、`tests/test_session_interrupt_cleanup.py`；受控整理入口 `services/context/message_store.py:finalize_interrupted_run`；运行事实 `services/context/run_facts.py`。

### M2 验证（2026-09-18，Windows）

M2 交付：
- 新增 `application/runtime.py`：从 `ui.cli.app::build_runtime/build_unconfigured_runtime` 与 `ui.cli.types::CliRuntime` 提取装配、`with_session`、`with_model_config`；应用层不 import UI。`ui/cli/types.py` 与 `ui/cli/app.py` 改为向 application 委托/重导出；`DeferredPermissionPrompter`/`DeferredUserQuestionPrompter` 允许 Controller 在装配后接管交互。
- 新增 `application/types.py`：`SessionSnapshot`、`SessionUpdate` 家族、`SubmissionReceipt`、`WithdrawalResult`、`CancelResult`、`ResponseResult`、`InteractionRequest/Answer`、`DetailRef/DetailResult`，全部为冻结值类型。
- 新增 `application/session.py::SessionController`：bootstrap task、单前台 worker、FIFO 队列与暂停、watch 无空隙订阅与慢观察者快照重同步、代次/序号、取消后调用 `finalize_interrupted_run` 收尾、`clear_session`/`resume_session` 重绑定并发布新代次、幂等 `close`。
- 新增 `application/interactions.py`：单 active 请求、按到达顺序排队、按 request ID + 类型校验回答、取消唤醒全部等待者；`PermissionPromptAdapter`/`UserQuestionPromptAdapter` 适配既有协议。
- 新增 `application/commands.py`：结构化命令 registry 与 `CommandOutcome`；查看即时、修改经 `await_safe_point` 串行、生命周期动作交 Controller。
- 新增 `application/sessions.py`：resume/list/restore 业务从 `ui.cli.resume` 迁入；`ui/cli/resume.py` 重导出。
- 重写 `ui/cli/batch.py`：改为 Controller Adapter，删除独立 loop/附件/shutdown；保留纯文本流式、工具摘要、成功/失败退出码、EOF 与权限/问答协议；不 import Textual。

Command：`uv run --no-sync python -m pytest tests/test_session_controller.py tests/test_session_commands.py tests/test_session_interactions.py tests/test_batch_session_controller.py -q`。

Result：退出码 0；**43 passed**（`test_session_controller.py` 15、`test_session_commands.py` 12、`test_session_interactions.py` 8、`test_batch_session_controller.py` 8）。

Command：`uv run --no-sync python -m pytest tests/test_session_controller.py tests/test_runtime_integration.py tests/test_import_boundaries.py -q` 与 M2.2/M2.3 指定的既有测试组合。

Result：退出码 0；**110 passed**（含 `test_plan_mode`、`test_permission_policy`、`test_cli_resume`、`test_cli_connect`、`test_cli_mcp_trust_prompt`、`test_async_cli_streaming`）。

Command：`uv run --no-sync python -m pytest tests -q`。

Result：退出码 1；**687 passed, 3 failed in 13.76s**。3 个失败与 M1 记录一致，均为与 M2 无关的既有平台失败（`test_bash_tool`、`test_openai_compatible_provider`、`test_search_tools`）。无新增失败。

代码复查后修复（2026-09-18）：`/permissions`（查看）与 `/plan show|open` 按具体 invocation 归为查看以免等待运行；取消调用 `InteractionCoordinator.cancel_pending` 唤醒全部等待者；`resume_queue` 在收尾失败后拒绝继续；`/plan <描述>` 与审批附件经同一 submit/附件入口；`respond` 接受 `InteractionAnswer` 或 kind+payload；`reload_model_config` 在安全点执行；快照附带 pending interactions。

Command：`uv run --no-sync python -m compileall -q core services infrastructure application ui` → 退出码 0。

Command：`uv run --no-sync python -m pytest tests/test_import_boundaries.py -q` → 4 passed。

证据文件：`tests/test_session_controller.py`、`tests/test_session_commands.py`、`tests/test_session_interactions.py`、`tests/test_batch_session_controller.py`；Controller `application/session.py`；交互 `application/interactions.py`；命令 `application/commands.py`。

### M3 验证（2026-09-18，Windows）

M3 交付：
- 新增 `ui/tui/projection.py::ConversationProjection`、`ui/tui/projection_types.py`：参考 `UiProjection` 的 `replace/apply/_ingest/_upsert` 组织方式，同步确定性、无 I/O；`ViewChange` 返回 updated/deleted IDs、结构、队列/状态/交互/usage/run 变化、reset 与 `resync_required`。工具键为 session + assistant 调用 + tool ID 复合；assistant 草稿以 `(model_turn_index, assistant_call_id)` 归组、定稿时原位重绑到持久化 UUID；工具按声明顺序出现在同一 assistant 容器，结果先到也保留；只有工具也创建容器；未配对结果不落为永久聊天消息；附件摘要消费 `HistoryRecord.attachments`，错误状态取自结果字段。
- 契约补齐（M3 适配所需的最小扩展）：`HistoryRecord` 增加 `assistant_call_id`/`model_turn_index`，`HistoryToolCall` 增加结构化 `input`（`message_shapes.assistant_tool_declarations` 解析两种声明表示）；`application.types` 增加 `UserMessageCommitted`，为 `ToolUpdate`/`ToolRunState` 增加调用归属与 `input`；`core.loop` 的 `interaction_started` 携带刚追加用户消息的持久化 UUID；`SessionController` 在 `interaction_started` 发布 `UserMessageCommitted`、在工具事件补齐调用归属、在取消收尾后与 `/compact` 成功后发布权威快照供投影 `replace` 重同步。
- 新增 `tests/test_conversation_projection.py`（22 项）与 `tests/test_conversation_pipeline.py`（5 项）：纯文本、工具 A/B 乱序声明完成、多次模型调用复用 tool ID、重复/晚到定稿、只有工具、撤回/清空队列、旧代次/重复序号拒绝、序号缺口要求完整快照、详情加载/缺失、状态/usage/交互无消息变化仍通知、pending interaction 增删、同会话快照替换保留 ID 且不置 reset、收尾删除未配对工具、无归属工具结果不产生永久孤立消息；管线用真实 `SessionController` + 记录型 fake loop，验证正常完成、工具完成、取消整理、compact 与慢订阅者重同步后投影与 Controller 权威历史一致，且旧会话详情不污染新投影。
- 代码复查后修复：`replace` 现在按前后 `UiMessage` 差异给出精确 `updated_ids`/`deleted_ids`，仅当会话代次或 session 变化才置 `reset`；`UsageChanged` 更新连通 live usage；`InteractionRequested/Resolved` 维护 pending 列表且过期回答不再误报变化；无显式调用归属的工具结果不再创建永久孤立 assistant；历史未配对结果只在 tool_call_id 唯一已声明时回配；`assistant_tool_declarations` 与 `assistant_tool_call_ids` 共用同一解析实现。

Command：`uv run python -m pytest tests/test_conversation_projection.py tests/test_conversation_history.py -q`。

Result：退出码 0；**32 passed**。

Command：`uv run python -m pytest tests/test_conversation_projection.py tests/test_conversation_pipeline.py tests/test_session_interrupt_cleanup.py -q`。

Result：退出码 0；**41 passed**。

Command：`uv run python -m compileall -q core services infrastructure application ui`。

Result：退出码 0。

Command：`uv run python -m pytest tests -q`。

Result：退出码 1；**714 passed, 3 failed in 13.04s**。3 个失败与 M1/M2 记录一致，均为与 M3 无关的既有平台失败（`test_bash_tool`、`test_openai_compatible_provider`、`test_search_tools`）。无新增失败。

证据文件：`tests/test_conversation_projection.py`、`tests/test_conversation_pipeline.py`；实现 `ui/tui/projection.py`、`ui/tui/projection_types.py`。

### M4 验证（2026-09-18，Windows）

M4 交付：
- 依赖：`uv add "textual>=0.89"` 锁定 `textual==8.2.8`（另引入 `linkify-it-py`、`mdit-py-plugins`、`platformdirs`），`uv sync --dev` 成功。
- 新增 `ui/tui/theme.py`：OneCode 调色板、`ONECODE_THEME`、`RICH_STYLES`（`ui.*`/`markdown.*` 命名样式）、`apply_theme(app)`；新增 `ui/tui/onecode.tcss`（深背景、对话区占剩余高度、新内容入口、状态栏/Composer 规则）。
- 新增 `ui/tui/renderers/`：`tool.py`（基于 OneCode 公共事实的 presenter registry + 通用 fallback + 脱敏）、`message.py`（user/assistant/system/tool 渲染，按消息内 part 顺序）、`status.py`（状态栏与运行态）。
- 新增 `ui/tui/conversation/`：`layout_index.py::VirtualLayoutIndex`、`render_cache.py::MarkdownBlockCache`（流式空行分块 + 定稿整文重解析，混合围栏处理）、`refresh_scheduler.py::UiRefreshScheduler`（40ms 合并、immediate、close）、`viewport.py`（可见区挂载 + overscan=8、spacer、message ID + 行偏移锚点、resize/滚底代次、详情 `DetailRequested`）、`view.py::ConversationView.update(projection, change)`（只调度刷新，辅助状态空 dirty 也刷新）。包 `__init__` 使用惰性属性避免 renderers↔view 循环 import。
- 新增 `tests/test_conversation_view.py`（9）、`tests/test_tui_rendering.py`（15）、`tests/test_tui_refresh_scheduler.py`（7）、`tests/test_tui_details.py`（8）与共享构造 `tests/tui_test_support.py`。

Command：`uv run python -m pytest tests/test_conversation_view.py tests/test_tui_rendering.py tests/test_conversation_projection.py -q`。

Result：退出码 0；**46 passed**。

Command：`uv run python -m pytest tests/test_conversation_view.py tests/test_tui_rendering.py tests/test_tui_refresh_scheduler.py tests/test_tui_details.py tests/test_conversation_projection.py -q`。

Result：退出码 0；**61 passed**。

Command：`uv run python -m pytest tests -q`。

Result：退出码 1；**754 passed, 3 failed in 27.81s**。3 个失败与 M1/M2/M3 记录一致，均为与 M4 无关的既有平台失败（`test_bash_tool`、`test_openai_compatible_provider`、`test_search_tools`）。无新增失败。

Command：`uv run python -m compileall -q core services infrastructure application ui` → 退出码 0；`uv run python -m pytest tests/test_import_boundaries.py -q` → 4 passed。

环境与测量：Windows 11、CPython 3.14.5、Textual 8.2.8，headless `run_test(size=...)`。5,000 条固定内容消息在 120×40 与 60×20 下，布局稳定后挂载的 `MessageWidget` 数量约 28（顶部/中部/底部抽样），断言 `mounted <= viewport.height + 16` 且远小于历史总量；两个 spacer 不计。10,000 个 `AssistantDelta` 在 40ms 调度器上 `requested_updates=10001`、`flush_count=2`（含挂载时一次 immediate 刷新，流式窗口合并为一次），`projection.messages[0].parts[0].content` 与 widget 消息正文均为完整 10,000 字符，渲染尾部与首部带标记；`MessageCommitted` 的 immediate 刷新在无等待下即可见。

代码复查后修复：`ui/tui/conversation/__init__.py` 改为惰性导出以解除 `renderers.message → conversation.render_cache → conversation.__init__ → view → viewport → renderers.message` 的循环 import；`tests/test_batch_session_controller.py` 的“batch 不 import TUI”断言改为子进程内用 meta_path finder 拦截 `textual`，因 pytest 收集期会导入 TUI 测试模块导致进程内 `sys.modules` 检查失效。

证据文件：`tests/test_conversation_view.py`、`tests/test_tui_rendering.py`、`tests/test_tui_refresh_scheduler.py`、`tests/test_tui_details.py`；实现 `ui/tui/theme.py`、`ui/tui/conversation/`、`ui/tui/renderers/`。

### M5 验证（2026-09-18，Windows）

M5.1 交付：
- 新增 `ui/tui/app.py::OneCodeTuiApp`：装配 Controller/Projection/ConversationView/StatusBar/Composer/CompletionOverlay，挂载后经 `asyncio.to_thread` 构建 runtime（未配置回退 `build_unconfigured_runtime`），启动期 MCP trust 通过 `run_coroutine_threadsafe` + 模态面板回答，不阻塞 UI。App 只提交意图、转发详情请求、显示交互面板，不 drain 队列、不收集附件、不重绑定 runtime。
- 新增 `ui/tui/composer.py::Composer`：`TextArea` 子类，覆写 `_on_key` 保证 Enter 提交、Ctrl+Enter/Ctrl+J 换行、Tab 补全、Ctrl+C 取消，避免 Textual 8.2.8 的 TextArea 原生 Enter 插入换行；`cursor_offset` 自行计算。
- 新增 `ui/tui/completion.py::CompletionOverlay`：参考布局的 `OptionList`，命令/文件两种模式，pending 帧拒绝过期接受。
- 新增 `ui/tui/status_bar.py::StatusBar` 与 `ui/tui/command_views.py`（`CommandOutcome` → Rich renderable，覆盖全部注册命令与 alias）。
- 新增 `ui/tui/modals/`：`PermissionModal`（只消费 `request.options`，Esc 拒绝）、`QuestionModal`（单选 OptionList、多选 SelectionList）、`PlanApprovalModal`、`McpTrustModal`、`SessionPickerModal`、`ProviderPickerModal`/`CredentialModal`/`ModelPickerModal`、`CommandOutputModal`。
- 计划审批：`exit_plan_mode` 工具结果带 `awaiting_approval` 时弹出面板，批准/拒绝仍走既有 `/plan approve|reject` 业务。
- `/connect` 经供应商选择 → 凭据 → 模型选择，保存仍用 `write_provider_env`，随后 `SessionController.reload_model_config()`。
- 文件 `@` 补全复用 `ui.cli.suggestions.suggestions_for`，经 `asyncio.to_thread` 异步计算并用 generation 拒绝旧结果。
- 交互面板经单一 FIFO worker 串行呈现，不叠加；`InteractionResolved` 会关闭当前面板。Ctrl+C 只取消运行，不退出、不静默删除草稿；提交/命令仅在文本未被改写时清理对应草稿。F8 撤回最后一条排队输入（草稿非空则不覆盖），F9 在暂停时继续队列。

M5.2 已完成部分：
- `ui/cli/app.py::main` 的 TTY 分支不再构建 runtime/启动内联 REPL，改为延迟 import 并调用 `ui.tui.app.run_tui`；非 TTY stdin 仍走 batch；stdin TTY + stdout 重定向保留明确错误与非零退出；TUI 启动异常写 stderr 并返回 1。
- `tests/test_import_boundaries.py` 增加基于 AST 的边界检查：`core/services/infrastructure` 不依赖 `application`/`ui`/`textual`；`application` 不依赖 `ui`/`textual`；Projection 不依赖 Textual 或旧 reducer；生产代码不依赖 `reference`/`miniagent`。
- `tests/test_async_cli_streaming.py` 的 TTY 用例迁移为断言调用 `run_tui`。

Command：`uv run python -m pytest tests/test_tui_app.py tests/test_tui_composer.py tests/test_tui_interactions.py tests/test_tui_entrypoint.py -q`。

Result：退出码 0；**36 passed**（App 14、Composer 6、Interactions 12、Entrypoint 4）。

Command：`uv run python -m pytest tests/test_tui_app.py tests/test_tui_composer.py tests/test_tui_interactions.py tests/test_session_commands.py tests/test_session_interactions.py tests/test_plan_mode.py -q`（execution M5.1 指定组合）。

Result：退出码 0；**78 passed**。

Command：`uv run python -m pytest tests -q`。

Result：退出码 1；**794 passed, 3 failed in 31.19s**。3 个失败与 M1–M4 记录一致，均为与 M5 无关的既有平台失败（`test_bash_tool`、`test_openai_compatible_provider`、`test_search_tools`）。无新增失败。

Command：`uv run python -m compileall -q core services infrastructure application ui` → 退出码 0；`uv run python -m pytest tests/test_import_boundaries.py -q` → 8 passed。

M5.2 未完成（记录为剩余工作）：
- 旧 `ui/cli/terminal/` 中 REPL、stream state/reducer/view/session、output coordinator、static replay、page/selector/permission/trust/connect transient 路径仍在磁盘上，但已不被生产 TTY 入口引用；`test_cli_terminal.py`、`test_cli_checkpoint_state.py`、`test_cli_output_coordinator.py`、`test_cli_stream_reducer.py`、`test_cli_stream_view.py`、`test_cli_streaming_session_commit.py`、`test_streaming_coalescer.py`、`test_markdown_rendering.py`、`test_text_cache.py` 仍验证旧静态打印机制，尚未迁走或退役。
- `prompt-toolkit` 仍作为依赖保留，因为 batch/旧 helper 与上述测试仍引用。
- Windows Terminal / POSIX 真实终端人工烟测（中文输入、Ctrl+Enter、resize、Modal 焦点、退出恢复）未执行；headless 通过不代表真实终端验收。
- README 的运行/按键说明尚未更新；撤回承接区目前复用 Composer（草稿非空时不覆盖），非独立可选择编辑区；计划审批由 `exit_plan_mode` 工具结果驱动而非 `plan_approval` interaction。

## Artifacts And Notes

入口为 [plan.md](plan.md)，批次与命令为 [execution.md](execution.md)，选择与兼容门槛为 [decisions.md](decisions.md)。当前不新增第五份计划/研究报告；参考适配清单放 plan 的 Context，发现与证据保留在本文件。

最主要风险为受控重写与 flush 竞争、旧压缩历史来源不足、启动 trust 与 bootstrap 互等、切换时旧代次事件污染，以及 Textual 在目标 Windows 终端的键盘/中文输入差异。各风险分别由 M1 故障测试、M2 控制器测试和 M4/M5 终端测试关闭。

## Outcomes & Retrospective

M1、M2、M3 已交付：应用层有可独立于 Textual 运行的 `SessionController`，`ui/tui/ConversationProjection` 已能从权威快照与有序更新得到同一消息树。三个目标设计仍视为“部分实现”：View 与 App 尚未存在，M3 只关闭了展示投影边界，不能宣称 TUI 已完成，也不能关闭 TD-007/TD-016。

M2 已知限制（不阻塞本批次验收，记入后续）：
- MCP trust 交互能力已在 `InteractionCoordinator` 与 Controller 公开（`interaction_requested`/`respond`），batch watcher 会以纯文本回答；但 `build_runtime` 的启动期 trust 仍在装配时同步完成，Controller 尚未在 bootstrap 阶段主动发布 trust 请求。该异步启动编排属于 M5.1 薄 App 的职责，M2 只提供机制。
- `/resume` 目标解析在应用层支持 session ID 与 `.jsonl` 路径；标题模糊匹配仍保留在旧 CLI 路径，M5 迁移时统一。
- `ui/cli/terminal/` 旧 REPL 仍在，M2 未删除，默认入口未切换。

M3 已知限制（不阻塞本批次验收，记入后续）：

- 任务明细没有独立更新类型（M2 snapshot 也无任务字段），`/tasks` 仍由命令视图负责；`pending_interactions` 与 usage 已有增量更新，任务状态变化留给 M5 命令视图。
- 附件摘要只在 `replace(snapshot)` 时按 `HistoryRecord.attachments` 呈现；`UserMessageCommitted` 未携带附件，实时提交瞬间的摘要等下一次权威快照，这是 M3 的有意边界。
- `HistoryRecord` 对旧记录不含 `assistant_call_id`/`model_turn_index` 时，工具结果按“已声明且无结果且 tool_call_id 全局唯一”回配；这是 M1 兼容规则下的配对，不是按最近 assistant 或正文猜测，多义时不配对。
- 取消收尾现在额外发布一次快照；慢观察者仍会先收到 `RunCancelled`/`QueueChanged` 再被快照替换，投影按快照收敛。

执行者从 M4.1 开始：先以固定 Projection 的测试 App 验证 Textual `run_test`/TextArea/Theme/timer/resize 等实际 API，锁版本后再移植虚拟视口。全部 M1–M5 验收达到后将计划包整体移入 completed。

M4 已交付：`ui/tui/ConversationView` 以投影 `messages` 为唯一事实，只在可见区 + overscan 内挂载 MessageWidget，支持向上阅读时增长不跳底、主动返回最新、resize 与锚点删除回退、工具详情按需外置读取与迟到/缺失处理，Markdown 定稿整文重解析。Textual 版本锁定为 8.2.8。三个目标设计仍视为“部分实现”：App、Composer、命令视图与 Modal 属 M5，默认入口未切换，因此不能宣称 TUI 已完成，也不能关闭 TD-007/TD-016。

M4 已知限制（不阻塞本批次验收，记入后续）：
- View 目前只在测试 App 中挂载；生产 Textual App 装配、状态栏/Composer/补全与按键绑定在 M5.1。
- `DetailRequested` 事件已定义并由 View 发出，但 `App → Controller.load_detail` 的实际连接在 M5.1；M4 用测试 App 模拟该回链。
- `onecode.tcss` 已提供目标布局规则，M4 未在真实终端做视觉验收（M5.2 人工验收）。

M5.1 已交付：`OneCodeTuiApp` 装配 Controller/Projection/View/StatusBar/Composer/补全与全部交互面板，默认 TTY 入口已切到 `ui.tui.app.run_tui`（M5.2 部分）。三个目标设计仍视为“部分实现”：旧 `ui/cli/terminal/` 未删除、随旧机制退役的测试未迁移、真实终端人工烟测未执行，因此不能关闭 TD-007/TD-016，计划包不能归档。

M5 已知限制（记入剩余工作）：
- 旧 TTY 展示管线仍在磁盘但无生产引用；`prompt-toolkit` 依赖与旧机制测试待退役。
- `/connect` 的模型列表获取失败时回退手动输入，未在真实供应商上做端到端烟测。
- 计划审批面板由 `exit_plan_mode` 工具结果的 `awaiting_approval` 驱动，而非 `plan_approval` interaction；业务仍由 `/plan approve|reject` 承担。
- 真实终端的中文输入法预编辑/确认、Ctrl+Enter 实际键码、窄窗口 resize 与退出恢复未验证。
