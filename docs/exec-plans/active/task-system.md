# OneCode Task 任务追踪系统

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本计划遵守仓库根目录的 `PLANS.md`。实现者只阅读本文件和当前工作树，也应能完成 Task 任务追踪系统第一版，不需要依赖此前对话。

## Purpose / Big Picture

完成本计划后，OneCode 会拥有一个面向 AI agent 的文件持久化任务追踪系统。用户可以让 agent 创建一组带依赖关系的任务，查看任务详情，更新任务状态和依赖，并在后续会话或未来 subagent 协作中继续读取同一组任务。任务不是后台进程，也不是当前 turn 的临时 todo；它是跨会话存在的工作图，每个任务保存为 `.onecode/tasks/{task_list_id}/{id}.json`。

可观察结果是：启动 CLI 后，`/tools` 能看到 `task_create`、`task_get`、`task_update` 和 `task_list` 四个工具；用户提示 agent 创建任务后，磁盘上出现 `.onecode/tasks/{task_list_id}/1.json` 等文件；调用 `/tasks` 能看到当前任务列表；完成上游任务后，下游任务的阻塞状态会随 `blockedBy` 关系变化；创建和完成任务时，项目现有 `services/hooks` 机制会发布 `TaskCreated` 和 `TaskCompleted` 事件，hook 可以阻断创建或完成。

第一版不实现 background task、后台 subagent 生命周期、任务进度输出文件、kill/cancel 任务、fs.watch 终端自动刷新、5 秒自动隐藏或 React/Ink 风格任务面板。第一版只实现结构化任务追踪、四个工具、hooks、CLI `/tasks` 命令和可测试的持久化行为。

## Progress

- [x] (2026-06-07 16:10+08:00) 阅读 `AGENTS.md`、`PLANS.md`、`architecture.md`、相关 design docs、active exec plans、tech debt tracker，以及 `docs/references/s12_task_system/` 中的 README、教学版 `code.py`、CC 参考工具和 `services/task/task/tasks.ts`。
- [x] (2026-06-07 16:20+08:00) 与用户确认第一版产品范围：偏向 CC 源码；实现 CC 四工具；新增 `TaskCreated` 和 `TaskCompleted` hook；任务文件位于 `.onecode/tasks/{task_list_id}/{id}.json`；为未来 subagent 共享任务列表预留 task list id 机制；维护 `blocks` 与 `blockedBy` 双向依赖；采用原子写加进程内锁；第一版只做 `/tasks` 命令；不把任务列表每轮自动注入 prompt。
- [x] (2026-06-07 16:35+08:00) 撰写本中文 ExecPlan，明确模块落点、接口、实现顺序、测试策略和验收方式。
- [ ] 新增 `services/tasks/` 持久化服务，包含任务数据模型、task list id 解析、原子写、进程内锁、高水位 ID、CRUD、依赖维护和 claim 行为。
- [ ] 扩展 `services/hooks/events.py`，新增 `TaskCreated` 和 `TaskCompleted`，并在 task 工具中运行现有 `HookRegistry`。
- [ ] 新增四个任务工具目录并注册到 CLI runtime：`tools/task_create`、`tools/task_get`、`tools/task_update` 和 `tools/task_list`。
- [ ] 为未来 subagent 共享预留 task list id 传播：主 runtime state、subagent child state、环境变量和 `/clear` / `/resume` 的行为需要一致。
- [ ] 新增 CLI `/tasks` 命令和渲染函数，显示当前 task list id、任务路径和任务摘要。
- [ ] 补充单元测试、工具集成测试、hook 测试、CLI 命令测试、compileall 和必要的全量 pytest 验证。

## Surprises & Discoveries

- Observation: 参考目录同时包含两类“task”。`docs/references/s12_task_system/code.py` 与 README 主体描述的是任务追踪系统；`docs/references/s12_task_system/Task.ts` 和 `services/task/task/tasks/*` 更偏后台任务、shell/agent 任务、输出文件和 kill 逻辑。
  Evidence: `Task.ts` 中状态为 `pending/running/completed/failed/killed`，包含 `outputFile`、`outputOffset`、`kill()` 等字段；用户明确说当前不需要 background task。因此本计划只吸收 `utils/tasks.ts` 风格的任务追踪系统，不实现后台任务执行框架。
- Observation: OneCode 的工具体系要求 provider-visible 工具名使用 snake_case，并通过 `ToolDescriptor` 注册，不能照搬 TypeScript 的 `TaskCreateTool` 类名进入模型 schema。
  Evidence: `docs/design-docs/tools-runtime-architecture.md` 说明 `ToolDescriptor.name` 是 snake_case 工具名；现有工具如 `read_file`、`edit_file`、`grep` 都按此规则注册。
- Observation: 现有 hook registry 是通用的，可以直接新增事件，而不需要创建 task-specific hook registry。
  Evidence: `services/hooks/events.py` 定义 `HookEvent`，`services/hooks/registry.py::HookRegistry` 对所有事件保存 callback 并返回 `HookResult(blocking_error, updated_input, metadata)`。工具 executor 已在 `PreToolUse`、`PostToolUse` 和 `ToolError` 上使用同一 registry。

## Decision Log

- Decision: 第一版偏向 CC 源码的四工具形态，而不是教学版的五工具形态。
  Rationale: 用户明确选择“CC四工具”。OneCode 中 provider-visible 名称采用 snake_case：`task_create`、`task_get`、`task_update`、`task_list`；用户可见说明和 prompt 中说明它们对应 CC 的 TaskCreate、TaskGet、TaskUpdate、TaskList。
  Date/Author: 2026-06-07 / Codex

- Decision: `claim` 不作为单独 provider-visible 工具出现，而由 `task_update` 的状态和 owner 更新能力覆盖。
  Rationale: CC 源码四工具中没有单独 `claim_task` 工具；claim 主要是 owner 竞争和任务状态/owner 更新的一部分。第一版仍会在 service 层提供原子 `claim_task()`，供后续 subagent 或内部调度使用，但模型主要通过 `task_update` 设置 `owner` 和 `status`。
  Date/Author: 2026-06-07 / Codex

- Decision: 任务文件放在当前 workspace 的 `.onecode/tasks/{task_list_id}/{id}.json`，高水位文件放在同一 task list 目录下。
  Rationale: 用户明确选择 `.onecode/tasks/{task_list_id}/{id}.json`。这保持任务与项目 transcript、trace、settings 同址，也避免写入 `~/.claude`。
  Date/Author: 2026-06-07 / Codex

- Decision: 第一版 task list id 解析为 `ONECODE_TASK_LIST_ID` 环境变量优先，其次 `RuntimeState.metadata["task_list_id"]`，最后 `RuntimeState.session_id`，并在首次解析后写回 state metadata。
  Rationale: 用户要求为之后 subagent 共享做准备。环境变量提供跨进程共享入口，state metadata 提供父子 runtime 共享入口，session id 保持普通单会话默认行为。
  Date/Author: 2026-06-07 / Codex

- Decision: 维护 `blocks` 和 `blockedBy` 双向依赖，删除任务时清理其他任务里的引用。
  Rationale: 用户同意双向维护。双向字段让列表、详情和下游 unblock 判断都不需要每次全量反推，但必须通过 service 层统一修改以避免不一致。
  Date/Author: 2026-06-07 / Codex

- Decision: 第一版使用进程内锁和原子写，不做跨进程文件锁。
  Rationale: 用户明确选择“原子写+进程内锁”。这能防止同一 Python 进程内并发工具调用互相踩写；跨进程竞争留给后续增强。
  Date/Author: 2026-06-07 / Codex

- Decision: 新增 `TaskCreated` 和 `TaskCompleted` hook event，并允许 blocking error 阻断创建或完成。
  Rationale: 用户明确要求直接修改现有 hooks。OneCode 已有 `HookRegistry` 和 `HookResult.blocking_error`，任务事件属于稳定生命周期扩展点，适合复用现有机制。
  Date/Author: 2026-06-07 / Codex

- Decision: 第一版 CLI 只新增 `/tasks`，不做自动 fs.watch、任务面板、5 秒隐藏或每轮自动渲染。
  Rationale: 用户明确选择之后再做自动 UI。当前 CLI 是标准库 stdout UI，先提供可验证命令能降低范围风险。
  Date/Author: 2026-06-07 / Codex

- Decision: 任务列表不每轮自动注入 system prompt 或 context。
  Rationale: 用户明确只实现工具。任务列表可能增长，自动注入会污染 prompt；模型需要时可调用 `task_list` 或 `task_get`。
  Date/Author: 2026-06-07 / Codex

## Outcomes & Retrospective

尚未开始实现。本计划完成后，应记录实际新增模块、工具注册点、测试数量、手动 CLI 验证输出、哪些行为与 CC 参考实现一致、哪些行为因 OneCode 架构而不同，以及后续是否要进入 fs.watch UI、background task 或跨进程文件锁阶段。

## Context and Orientation

OneCode 是 Python code agent runtime。主循环在 `core/loop.py`，它只负责编排用户输入、模型调用、工具调用、工具结果回填和 transition。任务系统不能作为工具名分支进入主循环。任务能力必须通过服务层、工具 descriptor、registry、executor、hook 和 CLI 命令接入。

工具运行时位于 `services/tools/`。`services/tools/types.py::ToolDescriptor` 是每个工具的事实来源，包含工具名、描述、输入 schema、输出 schema、prompt、输入校验、输入感知分类和 handler。`services/tools/registry.py::ToolRegistry` 管理当前启用的工具，并把同一组可见工具投影为 provider schema 和 prompt 工具说明。新增任务工具必须放在顶层 `tools/<tool_name>/`，不能让 `services/tools/` 静态 import 具体工具。

工具执行入口是 `services/tools/executor.py::RegistryToolExecutor`。它会做 schema validation、工具 validation、classification、guard、permission、hook、handler、结果预算和 trace。任务工具本身不触达普通 workspace 文件，而是写 runtime 管理目录 `.onecode/tasks/`；因此它们的 `ToolCallClassification.targets` 应使用 `ToolTarget(kind="session_state", operation=...)` 或等价非文件 target，不应让 sandbox guard 把 `.onecode/tasks/` 当成普通用户文件写入请求来拦截。

Hook 机制位于 `services/hooks/`。`services/hooks/events.py::HookEvent` 当前包含 `PreToolUse`、`PostToolUse`、`ToolError`、`UserPromptSubmit`、`AssistantMessageCompleted` 和 compact 相关事件。`services/hooks/registry.py::HookRegistry.run()` 接收事件和 payload，按注册顺序运行 callback，并返回 `HookResult`。`HookResult.blocking_error` 已经能表示阻断行为。任务系统应新增 `TaskCreated` 和 `TaskCompleted`，并在工具 handler 里运行它们。

CLI 装配在 `ui/cli/app.py::build_runtime()`。这里创建 `RuntimeState`、`MessageStore`、permission policy、trace recorder、base descriptors、registry、prompt assembler、context engine、guard、hook registry、subagent runner、tool executor 和 `AgentLoop`。任务工具 descriptor 应在这里加入 base descriptors 或注册到 registry。CLI 命令在 `ui/cli/commands.py`，渲染在 `ui/cli/renderer.py`，共享 runtime 类型在 `ui/cli/types.py`。

Subagent 运行在 `services/subagents/runner.py`。当前 subagent 是同步工具调用，不是后台任务。为了未来共享任务列表，父 runtime 创建或解析的 task list id 应写入 `RuntimeState.metadata["task_list_id"]`，child runtime 创建时应继承该 metadata，或者 subagent runner 应显式传递 task list id。第一版不需要让 subagent 自动认领任务，也不需要后台执行任务。

参考实现位于 `docs/references/s12_task_system/`。`README.en.md` 的 Deep Dive 描述 CC 真实任务系统字段、四工具、task list id、高水位 ID、依赖字段和 hooks；`code.py` 是教学版，使用五个工具并把 claim 直接变成 `in_progress`；`Task.ts` 和 `services/task/task/tasks/*` 是后台任务系统参考，不进入本计划。

## Plan of Work

第一步是新增 `services/tasks/` 服务模块。创建 `services/tasks/__init__.py`、`services/tasks/types.py`、`services/tasks/store.py` 和 `services/tasks/ids.py`。`types.py` 定义 `TaskStatus`，值为 `pending`、`in_progress`、`completed`；定义 `TaskRecord` dataclass，字段为 `id: str`、`subject: str`、`description: str`、`active_form: str | None`、`owner: str | None`、`status: TaskStatus`、`blocks: tuple[str, ...]`、`blocked_by: tuple[str, ...]` 和 `metadata: dict[str, Any]`。磁盘 JSON 字段应使用用户和参考实现中的 camelCase：`activeForm`、`blockedBy`；Python 对象内部可以用 snake_case。序列化和反序列化必须集中在 `types.py`，避免工具里手写字段映射。

`services/tasks/ids.py` 实现 task list id 解析。提供函数 `resolve_task_list_id(state: RuntimeState, *, env: Mapping[str, str] | None = None) -> str`。解析顺序是：`ONECODE_TASK_LIST_ID` 环境变量；`state.metadata["task_list_id"]`；未来 subagent/team metadata，例如 `state.metadata["team_name"]` 或 `state.metadata["parent_task_list_id"]`；最后 `state.session_id`。第一版可以只实际读取前两项和 session id，但函数和注释必须为未来 subagent/team 扩展留出明确位置。解析出的值要经过文件名安全化，只允许字母、数字、点、下划线和短横线；其他字符替换为 `_`。解析后写回 `state.metadata["task_list_id"]`，让同一个 runtime 和 child runtime 能复用稳定 id。

`services/tasks/store.py` 实现 `TaskStore`。构造函数接收 `workspace: Path` 和可选 `clock`。根目录固定为 `workspace / ".onecode" / "tasks"`。`tasks_dir(task_list_id)` 返回 `.onecode/tasks/{task_list_id}`，`task_path(task_list_id, task_id)` 返回 `{id}.json`，高水位文件名为 `.highwatermark`。所有写操作先 `mkdir(parents=True, exist_ok=True)`，再写临时文件并用 `Path.replace()` 原子替换目标。进程内锁用模块级或实例级 `threading.RLock`，所有 create/update/delete/block/claim 操作必须持锁。不要引入跨进程锁库。

`TaskStore.create_task()` 生成数字 ID。读取 `.highwatermark`，取整数加一，写回高水位，再写任务 JSON。即使任务被删除，ID 不能复用。若 `.highwatermark` 缺失但目录里已有数字任务文件，应扫描最大数字并从最大值加一开始。若高水位文件损坏，应抛出清晰异常，不要重置为 0。创建时默认 `status="pending"`、`owner=None`、`blocks=[]`、`blockedBy=[]`。创建完成后返回完整 `TaskRecord`。

`TaskStore.get_task()` 返回 task 或 `None`；`TaskStore.list_tasks()` 读取目录下所有 `*.json`，跳过 `.highwatermark` 和临时文件，按数字 ID 排序，无法解析的 JSON 应返回错误还是跳过需要明确。第一版推荐抛出 `TaskStoreError`，因为损坏任务文件会影响任务图正确性；`/tasks` 和工具 handler 捕获后返回可读错误。`TaskStore.update_task()` 接收部分字段，合并 metadata，写回 JSON。metadata 更新规则采用 CC：传入值为 `None` 时删除该 key，其他值覆盖。

依赖维护必须由 `TaskStore.block_task(task_list_id, blocker_id, blocked_id)` 统一完成。它表示 “blocker blocks blocked”。函数先读取两个任务，拒绝不存在 ID，拒绝 `blocker_id == blocked_id`，拒绝直接重复。第一版不实现完整 DAG cycle detection，但必须在注释和测试里说明；如果实现成本低，可以增加轻量 cycle 检测，拒绝新增边后从 `blocked_id` 能走回 `blocker_id` 的情况。无论是否做完整 cycle 检测，`block_task()` 必须同时更新 blocker 的 `blocks` 和 blocked 的 `blockedBy`，并保持排序稳定。

`TaskStore.delete_task()` 删除目标任务后，扫描所有其他任务并移除它们的 `blocks` 和 `blockedBy` 中的目标 ID，然后写回受影响任务。删除不降低 `.highwatermark`。若任务不存在，返回 False。删除前后都必须在同一进程内锁内完成，避免工具并发时出现半清理状态。

`TaskStore.claim_task()` 为未来 subagent 共享预留内部 API。它接收 `task_list_id`、`task_id` 和 `owner`。第一版语义偏 CC：claim 负责竞争 owner，不能认领已完成任务，不能认领被未完成上游阻塞的任务，不能抢占已有其他 owner 的任务。它可以把 owner 写入任务，但不强制修改 status；模型可通过 `task_update(status="in_progress")` 表示真正开始。若用户后来想把 claim 改成教学版语义，只需要调整 service 和 tests，不影响 provider-visible 四工具。

第二步是扩展 hooks。编辑 `services/hooks/events.py`，新增：

    TASK_CREATED = "TaskCreated"
    TASK_COMPLETED = "TaskCompleted"

保持 enum 名称风格与现有成员一致。`HookRegistry` 的构造函数使用 `for event in HookEvent` 初始化 callback 字典，因此新增 enum 后无需其他 registry 改动。任务工具 handler 调用 `hooks.run()` 时，payload 至少包含 `task_list_id`、`task`、`state`、`workspace`、`tool_call_id` 和 `event_source`。`TaskCreated` 的 blocking error 应导致刚创建的任务被删除并把错误作为 tool result 返回给模型。`TaskCompleted` 的 blocking error 应阻止 status 改为 completed，并返回非异常的 tool error content 给模型。

第三步是实现四个工具。新增目录 `tools/task_create/`、`tools/task_get/`、`tools/task_update/` 和 `tools/task_list/`，每个目录包含 `__init__.py`、`tool.py` 和 `prompt.py`。`__init__.py` 导出 `descriptor`。所有任务工具都通过 pydantic 或手写 validation 验证输入。优先沿用现有工具风格：`tool.py` 定义 `INPUT_SCHEMA`、`descriptor()`、`_validate()`、`_classify_input()` 和 `_handle()`。

`task_create` 输入包含 `subject`、`description`、可选 `activeForm` 和可选 `metadata`。第一版为了偏 CC，不在 create 输入中直接添加依赖；依赖通过 `task_update(addBlocks/addBlockedBy)` 增加。handler 解析 task list id，调用 `TaskStore.create_task()`，运行 `TaskCreated` hooks，hook 阻断时删除任务并返回 error result。成功 result 内容为 `Task #1 created successfully: <subject>`，metadata 包含 `task_id`、`task_list_id` 和 `task_path`。

`task_get` 输入包含 `taskId`。handler 返回完整详情，包括 description、activeForm、owner、status、blocks、blockedBy 和 metadata。不存在时返回 `Task not found`，作为普通 tool result 还是 error result要明确；推荐普通 result，方便模型恢复。

`task_list` 输入为空。handler 读取所有任务，过滤 `metadata._internal == True` 的内部任务。输出中对每个 task 显示 `#id [status] subject`，有 owner 时显示 owner，有未完成上游时显示 `[blocked by #x]`。和 CC 一样，已 completed 的 blockedBy 可以从列表摘要中过滤掉，但 `task_get` 仍显示完整依赖。工具是 read-only、concurrency_safe，result policy 可以设置为 `max_result_size_chars=100_000`，超出时使用 executor 现有结果预算。

`task_update` 输入包含 `taskId`，可选 `subject`、`description`、`activeForm`、`status`、`owner`、`addBlocks`、`addBlockedBy`、`metadata`。为接近 CC，`status` schema 接受 `pending`、`in_progress`、`completed` 和特殊值 `deleted`。当 status 是 `deleted`，调用 `delete_task()` 并返回。普通 status 更新要校验：不能完成不存在任务；从任何状态改到 completed 前运行 `TaskCompleted` hooks；如果 hook 阻断，不写 status。第一版是否允许 `completed -> pending` 需要明确；推荐允许，因为 `task_update` 是维护工具，但 result 中必须显示 statusChange。若 status 改为 `in_progress` 且 owner 未提供且当前 task 没 owner，可自动使用 `RuntimeState.metadata["agent_id"]` 或 `"main"`；如果没有 agent id，保留 owner 为空。`addBlocks` 对每个目标调用 `block_task(current_task, target)`；`addBlockedBy` 对每个 blocker 调用 `block_task(blocker, current_task)`。

所有任务工具的 `_classify_input()` 应返回 `read_only=True` 仅用于 `task_get` 和 `task_list`。`task_create` 和 `task_update` 是非只读，但不修改普通 filesystem；设置 `modifies_filesystem=False` 是可以接受的，因为它们修改 runtime-managed session state，不是用户 workspace 文件。为审计清晰，targets 使用 `ToolTarget(kind="session_state", operation="task_write" 或 "task_read", value=task_list_id)`。`concurrency_safe` 第一版可以设为 True，因为 store 有进程内锁；如果实现者担心多个写工具并发带来模型语义混乱，也可以把写工具设为 False，但必须在 Decision Log 记录。推荐 `task_create`、`task_get`、`task_update`、`task_list` 都 concurrency_safe，以接近 CC 源码。

第四步是在 CLI runtime 中装配 `TaskStore`。编辑 `ui/cli/types.py::CliRuntime`，新增字段 `task_store: TaskStore | None = None`。编辑 `ui/cli/app.py::build_runtime()`，创建 `task_store = TaskStore(workspace)`，把 `task_create_descriptor(task_store, hooks)`、`task_get_descriptor(task_store)`、`task_update_descriptor(task_store, hooks)`、`task_list_descriptor(task_store)` 加入 base descriptors。因为工具 handler 需要 hooks，可以让 descriptor factory 接收 `TaskStore` 和 `HookRegistry`，不要让工具创建自己的全局 store 或 hook registry。创建 hooks 的位置当前在 base descriptors 之后；实现时需要把 `hooks = HookRegistry(trace_recorder=trace_recorder)` 提前到创建 task descriptors 之前，或者先创建 hooks 再构造 base descriptors。此调整不能改变其他组件共享同一个 hooks 实例的事实。

`CliRuntime.with_session()` 要保证 task list id 行为稳定。普通 `/clear` 会生成新 session，默认也应生成新的 task list id，因为普通用户开始了新会话；但如果 `ONECODE_TASK_LIST_ID` 环境变量存在，`resolve_task_list_id()` 会继续使用它，从而跨 clear 共享。`/resume` 恢复旧 transcript 时，默认 task list id 是恢复后的 session id；如果 transcript state metadata 没持久保存 task list id，第一版可以接受由 session id 推导。实现者应在 plan 实施过程中验证是否需要把 `task_list_id` 写进 transcript message metadata；第一版不强制。

第五步是为 subagent 共享预留。编辑 `services/subagents/runner.py` 中创建 child `RuntimeState` 的位置，把父 state metadata 中的 `task_list_id` 或 `parent_task_list_id` 复制到 child state metadata。若 runner 当前没有直接持有父 `RuntimeState`，则从 parent message store 或 request metadata 传入不合适；更稳妥的是在 `SubagentRequest.metadata` 或 runner `run()` 入参处传递。实现者必须先阅读当前 `SubagentRunner.run()` 的 signature，再选择最小改动。验收标准是：父 runtime 一旦解析了 `state.metadata["task_list_id"]`，child runtime 调用任务工具时解析到同一个 id。不要为此实现 background task 或 agent 自动认领。

第六步是新增 `/tasks`。编辑 `ui/cli/commands.py::handle_command()`，识别 `/tasks`，调用新的 helper。helper 通过 `runtime.task_store` 和 `resolve_task_list_id(runtime.state)` 读取任务列表，捕获 `TaskStoreError` 并渲染为 `renderer.render_error()`。编辑 `ui/cli/renderer.py`，新增 `render_tasks(runtime, tasks, task_list_id, tasks_dir)`。输出应简洁，例如：

    Tasks:
      task list: <task_list_id>
      path: .onecode/tasks/<task_list_id>
      #1 [completed] Set up schema
      #2 [pending] Build API [blocked by #1]

没有任务时输出 `No tasks found for task list <id>.`。同时把 `/help` 和 banner command list 加上 `/tasks`。

第七步是 trace 和 metadata。任务工具成功或失败时，metadata 应记录 task-specific 摘要，例如 `task_id`、`task_list_id`、`updated_fields`、`status_change`、`blocked_by_count`。不要把完整 description 或 metadata 大对象写进 trace。Hook payload 可以包含完整 task 对象给 in-process hook 使用，但 trace sanitizer 不应记录完整内容。若发现 `TraceRecorder` 会记录 hook payload，需要补 sanitizer 或只把摘要传给 trace。

第八步是测试。新增 `tests/test_task_store.py`，覆盖高水位 ID、create/get/list、update metadata merge/delete key、delete 清理依赖、block 双向维护、直接自依赖拒绝、completed dependency 从 list 摘要过滤、原子写后 JSON 可读。新增 `tests/test_task_tools.py`，用 fake runtime state 和 `HookRegistry` 调用 descriptors，覆盖四工具成功路径、missing task、status deleted、TaskCreated hook 阻断后文件被删除、TaskCompleted hook 阻断后 status 不改变、`addBlocks` 和 `addBlockedBy`。新增或扩展 `tests/test_hooks.py`，验证 `HookEvent.TASK_CREATED` 和 `HookEvent.TASK_COMPLETED` 能注册和运行。新增或扩展 `tests/test_cli_commands.py`，覆盖 `/tasks` 空列表、有任务、store error 和 `/help` 包含命令。新增 subagent 相关测试，验证 child 继承 task list id metadata。

第九步是文档同步。因为本计划是 active ExecPlan，不需要立刻更新 `architecture.md`。若实现中发现任务系统成为稳定模块，应在完成后更新 `docs/design-docs/tools-runtime-architecture.md` 或新增 `docs/design-docs/task-system-architecture.md`，并在 `architecture.md` 模块地图中加入 `services/tasks/`。第一版实现期间如果只写代码和测试，可以先在本 plan 的 `Outcomes & Retrospective` 记录后续文档任务。

## Concrete Steps

从仓库根目录运行所有命令：

    cd D:\study\OneCode

先确认工作树状态，避免覆盖他人变更：

    git status --short

阅读当前相关文件：

    Get-Content -Path services\tools\types.py
    Get-Content -Path services\hooks\events.py
    Get-Content -Path services\hooks\registry.py
    Get-Content -Path ui\cli\app.py
    Get-Content -Path ui\cli\commands.py
    Get-Content -Path ui\cli\renderer.py

实现服务和工具后，运行定向测试：

    uv run python -m pytest tests/test_task_store.py -q
    uv run python -m pytest tests/test_task_tools.py -q
    uv run python -m pytest tests/test_hooks.py -q
    uv run python -m pytest tests/test_cli_commands.py -q
    uv run python -m pytest tests/test_subagent_runner.py -q

运行编译检查：

    uv run python -m compileall core services infrastructure prompts tools ui

最后运行全量测试：

    uv run python -m pytest tests -q

手动 CLI 验证需要 `.env` 中已有可用 provider 设置。启动 CLI：

    uv run python -m ui.cli.app

在 CLI 中输入：

    /tools

预期看到 `task_create`、`task_get`、`task_update` 和 `task_list`。然后输入：

    /tasks

如果当前 task list 没有任务，预期看到 `No tasks found for task list ...`。再让模型创建任务，例如：

    Create three tasks: set up schema, build API blocked by schema, write tests blocked by API. Use the task tools.

模型应调用 `task_create` 三次，再用 `task_update` 添加依赖。磁盘上应出现：

    .onecode\tasks\<task_list_id>\.highwatermark
    .onecode\tasks\<task_list_id>\1.json
    .onecode\tasks\<task_list_id>\2.json
    .onecode\tasks\<task_list_id>\3.json

再次输入 `/tasks`，应看到三个任务及阻塞关系。让模型完成上游任务：

    Mark task #1 completed and list tasks.

模型应调用 `task_update`，完成任务 #1，并调用或展示 `task_list`。若存在 `TaskCompleted` blocking hook，工具结果应显示 hook error，磁盘 JSON 中 status 仍保持原值。

## Validation and Acceptance

任务存储验收要求：创建任务后任务 JSON 使用 camelCase 字段，包含 `activeForm`、`blockedBy`、`blocks` 和 `metadata`；ID 从 `1` 开始递增；删除任务不复用 ID；`.highwatermark` 记录最高 ID；所有写入后 JSON 文件完整可解析；`delete_task()` 会清理其他任务里的依赖引用；`block_task()` 会同步维护 `blocks` 和 `blockedBy`。

task list id 验收要求：没有环境变量时，主 runtime 使用 `RuntimeState.session_id` 作为 task list id，并写入 `state.metadata["task_list_id"]`；设置 `ONECODE_TASK_LIST_ID=shared-demo` 后，主 runtime 和 child subagent 都使用 `shared-demo`；未来 subagent 共享所需的 metadata 复制有测试覆盖。

四工具验收要求：`/tools` 显示四个任务工具；`task_create` 能创建任务并触发 `TaskCreated`；`task_get` 能查看完整任务；`task_update` 能更新基本字段、status、owner、metadata、delete 和依赖；`task_list` 能列出非内部任务并隐藏已完成 blockedBy 摘要。工具错误以 `ToolExecutionResult` 返回给模型，不让主循环崩溃。

hook 验收要求：注册到 `HookEvent.TASK_CREATED` 的 callback 会在创建任务后运行；如果 callback 返回 `HookResult(blocking_error="...")`，新任务文件被删除，工具结果是 error。注册到 `HookEvent.TASK_COMPLETED` 的 callback 会在任务变为 completed 前运行；如果 callback 阻断，status 不改变，工具结果说明阻断原因。hook callback 异常遵循现有 registry 行为：记录到 metadata，不中断 hook 链，除非返回 blocking error。

CLI 验收要求：`/help` 和 banner 包含 `/tasks`；`/tasks` 能显示 task list id、相对路径和任务摘要；空列表、损坏 JSON、缺少 task store 都有清晰错误或空状态。CLI 不自动 watch 文件，不自动隐藏任务，不每轮把任务列表注入 prompt。

安全和架构验收要求：`core/loop.py` 不出现 `task_create`、`task_update` 等工具名分支；`services/tools/` 不静态 import 顶层任务工具；任务工具通过 `ToolDescriptor` 和 `ToolRegistry` 接入；任务写入仅限 `.onecode/tasks/{task_list_id}/`；项目级工具 deny、registry hidden/disabled 和 hook blocking 仍然生效。任务工具不绕过 executor 的 schema validation、classification、permission 和 hook 流程。

测试验收要求：新增测试在实现前应失败，完成后通过。最终运行 `uv run python -m pytest tests -q` 应通过；若存在与本计划无关的已知失败，必须在 `Surprises & Discoveries` 和 `Outcomes & Retrospective` 中记录命令、失败名称和判断依据。

## Idempotence and Recovery

`TaskStore` 的目录创建和读取必须幂等。重复运行 `/tasks` 不修改磁盘。重复调用 `TaskStore.tasks_dir()` 或 `ensure_task_list_dir()` 不清空已有任务。`rebuild` 类操作第一版不需要提供；如果实现者加入修复工具，也不能删除用户任务文件。

原子写策略必须允许中断后恢复。写入先创建同目录临时文件，再 `replace()` 到目标路径。下次 list 时应忽略临时文件。若进程在 replace 前崩溃，旧 JSON 仍存在；若进程在 replace 后崩溃，新 JSON 完整存在。

如果 `.highwatermark` 损坏，service 应返回清晰错误，而不是从 0 重新开始造成 ID 复用。用户可以手动修复 `.highwatermark`，然后重试命令。若某个任务 JSON 损坏，`/tasks` 应显示文件路径和解析错误，帮助用户修复，不应默默跳过导致依赖图不可信。

如果 hook 阻断 `TaskCreated`，handler 必须删除刚创建的任务并清理任何依赖引用。第一版 create 不接受依赖，因此通常只需删除新文件；若后续扩展 create-with-deps，必须同步清理。若 hook 阻断 `TaskCompleted`，handler 不应写入 completed status，也不应误报下游已解锁。

如果用户设置了 `ONECODE_TASK_LIST_ID`，`/clear` 不应删除或重置该 task list。若没有环境变量，`/clear` 开启新 session 后默认进入新 task list；旧任务仍在旧 session id 目录中，用户可通过设置 `ONECODE_TASK_LIST_ID` 或 `/resume` 回到对应列表。

## Artifacts and Notes

推荐磁盘 JSON 形态：

    {
      "id": "1",
      "subject": "Set up schema",
      "description": "Create initial database schema.",
      "activeForm": "Setting up schema",
      "owner": "main",
      "status": "in_progress",
      "blocks": ["2"],
      "blockedBy": [],
      "metadata": {
        "priority": "high"
      }
    }

推荐 `/tasks` 输出：

    Tasks:
      task list: 2d8c8a3f-1e7a-4f32-9d42-3b8b8e7a7d91
      path: .onecode/tasks/2d8c8a3f-1e7a-4f32-9d42-3b8b8e7a7d91
      #1 [completed] Set up schema
      #2 [pending] Build API [blocked by #1]
      #3 [pending] Write tests [blocked by #2]

推荐 task tool result 文本：

    Task #1 created successfully: Set up schema

    Task #2 updated: status, owner, blockedBy

    Task #3 not found

本计划吸收 CC 参考实现的部分行为：四工具、数字 ID、高水位、`blocks` 与 `blockedBy` 双向依赖、`TaskCreated` 和 `TaskCompleted` hook、内部任务过滤、完成依赖在列表摘要中过滤。第一版不吸收的行为：proper-lockfile 跨进程锁、agent busy list-level lock、mailbox 通知、verification nudge、feature flags、fs.watch UI、background task 输出和 kill/cancel。

## Interfaces and Dependencies

在 `services/tasks/types.py` 中定义：

    TaskStatus = Literal["pending", "in_progress", "completed"]

    @dataclass(frozen=True)
    class TaskRecord:
        id: str
        subject: str
        description: str
        active_form: str | None
        owner: str | None
        status: TaskStatus
        blocks: tuple[str, ...]
        blocked_by: tuple[str, ...]
        metadata: dict[str, Any]

    def task_from_json(data: Mapping[str, Any]) -> TaskRecord: ...
    def task_to_json(task: TaskRecord) -> dict[str, Any]: ...

在 `services/tasks/ids.py` 中定义：

    def resolve_task_list_id(
        state: RuntimeState,
        *,
        env: Mapping[str, str] | None = None,
    ) -> str: ...

在 `services/tasks/store.py` 中定义：

    class TaskStoreError(Exception): ...

    class TaskStore:
        def __init__(self, workspace: Path | str) -> None: ...
        def tasks_dir(self, task_list_id: str) -> Path: ...
        def task_path(self, task_list_id: str, task_id: str) -> Path: ...
        def create_task(self, task_list_id: str, *, subject: str, description: str, active_form: str | None = None, metadata: dict[str, Any] | None = None) -> TaskRecord: ...
        def get_task(self, task_list_id: str, task_id: str) -> TaskRecord | None: ...
        def list_tasks(self, task_list_id: str) -> tuple[TaskRecord, ...]: ...
        def update_task(self, task_list_id: str, task_id: str, updates: TaskUpdate) -> TaskRecord | None: ...
        def delete_task(self, task_list_id: str, task_id: str) -> bool: ...
        def block_task(self, task_list_id: str, blocker_id: str, blocked_id: str) -> tuple[TaskRecord, TaskRecord]: ...
        def claim_task(self, task_list_id: str, task_id: str, owner: str) -> TaskClaimResult: ...

`TaskUpdate` 和 `TaskClaimResult` 可以是 dataclass，也可以是内部 helper 类型，但必须让 tests 能断言 updated fields、status change、blocked reason 和 already claimed reason。

在 `services/hooks/events.py` 中扩展：

    class HookEvent(StrEnum):
        ...
        TASK_CREATED = "TaskCreated"
        TASK_COMPLETED = "TaskCompleted"

在每个任务工具目录中导出：

    def descriptor(task_store: TaskStore, hooks: HookRegistry | None = None) -> ToolDescriptor: ...

`task_get` 和 `task_list` 可以不需要 hooks 参数，但为了统一装配也可以接受并忽略。工具 handler 必须返回 `ToolExecutionResult`，不要抛出用户可恢复错误。不可恢复的程序 bug 可以抛出，让 executor 转换为 tool error。

在 `ui/cli/types.py::CliRuntime` 中新增：

    task_store: TaskStore | None = None

在 `ui/cli/renderer.py` 中新增：

    def render_tasks(runtime: CliRuntime, tasks: Iterable[TaskRecord], *, task_list_id: str, tasks_dir: Path) -> str: ...

在 `ui/cli/commands.py` 中新增 `/tasks` 分支。该命令不调用模型，不修改任务，只读取 `TaskStore` 并渲染。

## Revision Notes

- 2026-06-07 / Codex: 初始版本。根据用户确认的范围撰写完整中文 ExecPlan，选择 CC 四工具、workspace-local `.onecode/tasks/{task_list_id}/`、task list id 共享预留、双向依赖、进程内锁加原子写、`TaskCreated` / `TaskCompleted` hooks、CLI `/tasks`，并明确不实现 background task 和每轮自动任务注入。
