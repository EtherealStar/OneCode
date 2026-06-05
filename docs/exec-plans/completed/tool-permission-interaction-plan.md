# 实现工具权限机制和 CLI 权限交互面板

本 ExecPlan 是一个活文档。实现过程中必须持续维护 `Progress`、`Surprises & Discoveries`、`Decision Log` 和 `Outcomes & Retrospective`。

本计划遵守仓库根目录的 `PLANS.md`。本文把必要背景、用户决策、实现边界和验证步骤写入同一文件，使后续执行者只阅读本文和当前工作区也能完成实现。

## Purpose / Big Picture

完成本改动后，OneCode 的工具执行不再把 `ask` 当作普通工具错误直接返回给模型。运行时会在工具真正执行前走权限机制：先执行硬拒绝，再判断是否需要询问用户，最后才允许工具 handler 触碰文件系统或其他目标。用户在 CLI 中会看到按工具类型定制的权限交互面板，例如读文件、编辑文件、搜索目录和未来 bash 命令各自展示不同的风险信息、目标路径、输入摘要和可选授权范围。

用户可以通过 CLI 观察这一行为：当模型请求读取项目外文件、编辑 `.git/` 或 `.vscode/` 等危险目录、访问可疑 Windows 路径，或访问项目目录外路径时，CLI 会暂停并询问。选择本次允许后工具继续执行；选择会话允许目录后，本会话后续同类访问不再询问；选择拒绝后工具不会执行，并会把结构化拒绝结果回填给模型。

本计划不实现持久配置写入，不写入用户级或项目级权限文件。第一版只实现运行时权限服务、session 级临时授权、deny-first 工具裁剪、执行入口重复校验，以及标准库 CLI 的不同工具交互面板。

## Progress

- [x] (2026-06-04) 阅读 `AGENTS.md`、`PLANS.md`、`architecture.md`、`docs/design-docs/core-beliefs.md`、`docs/design-docs/tool-design-guidelines.md`、`docs/exec-plans/active/dynamic-system-prompt-architecture-plan.md`、`docs/tech-debt/tech-debt-tracker.md`、`docs/references/s03_permission/` 和当前工具/guard/CLI 代码。
- [x] (2026-06-04) 与用户确认关键产品决策：项目目录内的 `.git/`、`.vscode/` 等危险目录需要 ask；可疑 Windows 路径需要 ask；项目目录外访问需要 ask；UI 先做 CLI 交互面板；暂不写持久配置；实现工具级裁剪接入；`edit_file` 权限 UI 可以展示简化 diff。
- [x] (2026-06-04) 创建本 ExecPlan，记录权限服务、executor 接入、session 授权、工具裁剪、CLI 面板和验证策略。
- [x] (2026-06-04) 实现 `services/permissions/` 的类型、规则评估、session 授权和 prompter 协议。
- [x] (2026-06-04) 改造 `RegistryToolExecutor`，让 `ask` 调用权限 prompter，允许后继续执行工具，拒绝时返回结构化 tool result；未注入 prompter 时返回 `permission_ask_required`。
- [x] (2026-06-04) 改造 `ToolRegistry.visible_descriptors()`，让工具级 deny/disabled 和权限服务共同驱动 schema/prompt 裁剪。
- [x] (2026-06-04) 在 CLI 中实现不同工具的权限交互面板，并在 runtime 装配中注入 CLI prompter。
- [x] (2026-06-04) 补充权限、executor、registry、CLI 渲染和工具集成测试；focused tests `uv run python -m pytest tests\test_permission_policy.py tests\test_tool_registry_and_executor.py tests\test_file_tools_guard.py tests\test_search_tools.py tests\test_hooks.py tests\test_cli_permissions.py tests\test_cli_commands.py tests\test_cli_resume.py -q` 已通过，结果为 63 passed。
- [x] (2026-06-04) 更新 `architecture.md` 和 `docs/tech-debt/tech-debt-tracker.md`，记录已落地能力和剩余限制。
- [x] (2026-06-04) 运行最终 compile check 和全量测试，并在 Outcomes 中记录结果：`uv run python -m compileall core services infrastructure tools ui prompts` 通过；`uv run python -m pytest tests -q` 通过，112 passed。

## Surprises & Discoveries

- Observation: 当前 `SandboxGuard` 已经能返回 `allow`、`ask` 和 `deny`，但 `RegistryToolExecutor` 会把 `ask` 直接转换成 `path_guard_ask_required` 工具错误，不会暂停等待用户确认。
  Evidence: `services/guard/policy.py` 中 `external_directory` 返回 `GuardPolicy(action="ask")`；`services/tools/executor.py` 的 `_prepare_input()` 对任何非 allow policy 都返回 `_guard_error_result()`。

- Observation: 具体文件工具 handler 内部也重复执行 guard，并会把 ask 转成工具错误。
  Evidence: `tools/read_file/tool.py`、`tools/edit_file/tool.py`、`tools/glob/tool.py` 和 `tools/grep/tool.py` 都在 handler 中调用 `runtime.guard.check_path()` 或 `check_write_target()`，并在 `policy.action == "ask"` 时返回 `path_guard_ask_required`。

- Observation: 实现前，动态 prompt 架构已经建立了统一的 visible tool view，但只支持 disabled/denied 工具名，尚未接入完整 permission policy。
  Evidence: `services/tools/registry.py` 的 `visible_descriptors(state)` 会读取 registry 构造期的 `disabled_tools`、`denied_tools` 和 `RuntimeState.metadata` 中的 `disabled_tools`、`denied_tools`、`hidden_tools`；技术债 TD-008 明确记录真实 permission policy 尚未接入。

- Observation: 当前 CLI 是标准库同步输入界面，不是 Ink、React、web 或桌面 UI。
  Evidence: `ui/cli/app.py` 使用 `input("onecode> ")` 进入主循环；`ui/cli/renderer.py` 只返回纯文本渲染字符串。

- Observation: handler 内重复 guard 不能直接删除，否则会失去工具层兜底；但如果不传递已批准 ask，上层允许后仍会被 handler 拦截。
  Evidence: `tools/read_file/tool.py`、`tools/edit_file/tool.py`、`tools/glob/tool.py` 和 `tools/grep/tool.py` 都在 handler 内再次调用 guard。实现后在 `ToolRuntime.approved_guard_policies` 中传入 executor 已批准的 ask，并通过 `is_guard_policy_allowed()` 统一判断。

- Observation: 非交互 executor 的 ask 错误从 `path_guard_ask_required` 变为 `permission_ask_required`。
  Evidence: 更新后的 `tests/test_file_tools_guard.py`、`tests/test_search_tools.py` 和 `tests/test_hooks.py` 断言 ask fallback 来自权限层，同时保留 guard deny 的 `path_guard_denied`。

## Decision Log

- Decision: 第一版权限 UI 做在 CLI 交互面板中，不引入 web UI 或桌面弹窗。
  Rationale: 仓库当前唯一落地 UI 是 `ui/cli/`。按架构约束，UI 只负责展示和收集用户选择，权限策略和执行判断仍放在 services/tool executor 边界。
  Date/Author: 2026-06-04 / User + Codex

- Decision: 项目目录外访问必须 ask；项目目录内的危险目录如 `.git/`、`.vscode/` 等也必须 ask。
  Rationale: OneCode 不应把项目目录内所有路径都视为低风险。`.git/` 等目录可能影响版本历史、配置、安全凭据或编辑器状态，应显式让用户确认。
  Date/Author: 2026-06-04 / User

- Decision: 可疑 Windows 路径必须 ask。
  Rationale: 项目已强调 Windows、WSL、Cygwin、盘符和路径归一化是安全边界的一部分。可疑路径不应因为字符串形式混乱而默默 allow；无法明确归入安全项目边界时应进入人工确认。
  Date/Author: 2026-06-04 / User

- Decision: 第一版只做 session 级临时授权，不写持久配置。
  Rationale: 这能先验证权限交互和执行入口行为，避免过早设计用户设置、项目设置、本地设置、CLI flag 和组织策略的合并优先级。持久规则可在后续计划中加入。
  Date/Author: 2026-06-04 / User + Codex

- Decision: 实现工具级裁剪接入 `ToolRegistry.visible_descriptors()`。
  Rationale: 被工具级 deny 或 disabled 的能力不应继续出现在 provider schema 和 system prompt 中。动态裁剪不能替代执行入口检查，但能减少模型看到不可用能力的机会。
  Date/Author: 2026-06-04 / User + Codex

- Decision: `edit_file` 权限面板展示简化 diff。
  Rationale: 文件编辑是高影响写操作。显示 `old_string`、`new_string`、`replace_all` 和目标文件摘要能让用户在确认前理解即将发生的变化。第一版不做 IDE diff 或可编辑 diff。
  Date/Author: 2026-06-04 / User

- Decision: 保留具体工具 handler 内的 guard 兜底，并由 executor 注入已批准 guard policy。
  Rationale: 这能避免未走 executor 的工具调用绕过安全检查，同时保证用户在 permission prompter 中允许 ask 后不会被 handler 重复 guard 无理由拦回错误。
  Date/Author: 2026-06-04 / Codex

- Decision: 未注入 permission prompter 时，ask 返回 `permission_ask_required` 而不是旧的 `path_guard_ask_required`。
  Rationale: ask 现在属于权限机制的暂停点，不只是路径 guard 错误。非交互环境仍 fail closed，并通过结构化 payload 保留 guard policy 细节。
  Date/Author: 2026-06-04 / Codex

## Outcomes & Retrospective

已落地第一版运行时权限机制：`services/permissions/` 提供类型、session store、policy 和 prompter protocol；`RegistryToolExecutor` 在 handler 前执行 guard 和 permission policy，ask 时可调用 CLI prompter，拒绝或无 prompter 时返回结构化工具结果；`ToolRegistry.visible_descriptors()` 已消费工具级 permission deny/disabled；CLI 已提供 `read_file`、`edit_file`、`glob`、`grep` 的同步权限面板，并在 `/clear` 和 `/resume` 后清理 session 临时授权。

当前保留限制包括：权限规则不持久化；没有 bash 命令解析；没有多来源用户/项目/组织规则合并；权限事件尚未进入 observability trace；CLI 面板是纯文本同步输入；路径级具体参数仍必须在执行入口判断，不能在 prompt/schema 组装阶段预裁剪。最终验证已通过：`uv run python -m compileall core services infrastructure tools ui prompts` 成功；`uv run python -m pytest tests -q` 成功，112 passed。

## Context and Orientation

OneCode 是 Python code agent runtime。主循环在 `core/loop.py`，它把用户 prompt 写入 `MessageStore`，通过 `ContextEngine` 构建模型上下文，调用模型客户端，执行模型返回的工具调用，并把工具结果回填给模型。主循环必须保持薄，不应直接实现权限弹窗、路径规则或工具名分支。

工具运行时在 `services/tools/`。`services/tools/types.py` 定义 `ToolCall`、`ToolDescriptor`、`ToolCallClassification`、`ToolTarget` 和 `ToolExecutionResult`。每个工具通过 `classify_input()` 把一次调用分类成只读、是否修改文件系统、是否可并发、触达的 targets、结果预算和 `permission_subject`。`services/tools/executor.py` 的 `RegistryToolExecutor` 负责查找 descriptor、校验输入、分类、检查 guard、运行 hook、调用 handler 和返回统一工具结果。

路径 guard 在 `services/guard/`。`services/guard/boundary.py` 根据 sandbox boundary 把路径分成 `inside_workspace`、`inside_worktree`、`inside_extra_allowed`、`external_directory` 和 `denied`。`services/guard/policy.py` 把这些分类映射为 `allow`、`ask` 和 `deny`。当前 `denied` pattern 返回 deny，外部目录返回 ask，workspace/worktree/extra allowed 返回 allow。

当前工具包括 `read_file`、`edit_file`、`glob` 和 `grep`。它们都通过 descriptor 注册到 `ToolRegistry`，CLI 装配在 `ui/cli/app.py` 的 `build_runtime()` 中完成。`ToolRegistry.visible_descriptors(state)` 是模型可见 schema 和 system prompt 工具 section 的统一来源。被工具级 deny 的工具应在这里消失，但路径级权限仍必须在执行入口根据实际输入重复判断。

`docs/references/s03_permission/README.en.md` 是权限参考。它描述了教学版三道门：硬拒绝、规则匹配、用户确认；也记录了生产系统中存在 `allow`、`deny`、`ask`、`passthrough` 四类行为、多来源规则、hook 协调和工具自有 permission 判断。OneCode 第一版应采用足够小但结构正确的版本：deny-first、ask 可暂停、allow 才执行、拒绝结果回填模型。

CLI 当前是纯文本同步界面。`ui/cli/app.py` 通过 `input()` 读取用户命令；`ui/cli/renderer.py` 负责 banner、状态、历史、工具列表和错误文本渲染。权限交互面板应继续使用标准库输入输出实现，不引入额外 UI 框架。

## Plan of Work

第一阶段新增权限服务包。创建 `services/permissions/__init__.py`、`services/permissions/types.py`、`services/permissions/session.py`、`services/permissions/policy.py` 和 `services/permissions/prompter.py`。`types.py` 定义 `PermissionAction = Literal["allow", "ask", "deny", "passthrough"]`、`PermissionDecision`、`PermissionRequest`、`PermissionResponse`、`PermissionOption` 和 `PermissionScope`。`prompter.py` 定义协议 `PermissionPrompter.request_permission(request) -> PermissionResponse`。第一版 prompter 可以同步阻塞，因为当前 CLI 和 executor 都是同步执行。

第二阶段实现 session 级临时授权。`services/permissions/session.py` 定义 `SessionPermissionStore`，保存在当前 `RuntimeState` 或 executor 持有对象中均可，但不能写磁盘。它至少支持按工具名整体 deny/disabled、按路径目录 pattern 会话 allow、按 operation 区分 read/write/list/delete 的 allow。建议使用规范化后的目录 pattern，而不是原始字符串。session allow 只能覆盖 ask，不能覆盖 deny。

第三阶段实现 permission policy。`services/permissions/policy.py` 定义 `PermissionPolicy.evaluate(tool_call, descriptor, classification, guard_policies, state) -> PermissionDecision`。该 policy 消费 `ToolCallClassification.targets`、guard policy 结果、session store 和固定风险规则。执行顺序必须是：先检查工具级 deny/disabled，再检查每个 target 的 guard deny，再检查危险目录或可疑 Windows 路径导致的 ask，再检查 guard ask，再检查 session allow，最后 allow。若多个 target 冲突，deny 胜出；任一 target 需要 ask 且没有 session allow，则整体 ask。

第四阶段补充危险目录和可疑 Windows 路径判断。危险目录第一版至少包括 `.git`, `.vscode`, `.idea`, `.onecode`，并允许后续从配置扩展。对于文件系统 target，若规范化路径位于这些目录内，返回 ask，除非更高优先级 deny 命中。可疑 Windows 路径指输入路径或规范化路径存在无法稳定归类的 Windows/WSL/Cygwin 形式，例如带盘符但不在当前 workspace 盘符下、`/mnt/<drive>/...`、`/cygdrive/<drive>/...`、`\\server\share`、包含 Windows reserved device name 或解析异常。第一版实现要保守：发现疑似形式但无法明确 inside workspace 时 ask；解析异常应成为结构化 deny 或 ask，不应崩溃。

第五阶段改造 executor。编辑 `services/tools/executor.py`，在 `_prepare_input()` 中不要直接把 guard ask 变成错误。更合适的结构是：校验输入、分类、收集所有 target 的 guard policy，然后交给 `PermissionPolicy`。若 decision 是 deny，返回结构化工具错误且不运行 hook 或 handler。若 decision 是 ask，构造 `PermissionRequest` 并调用注入的 `PermissionPrompter`；用户拒绝时返回结构化工具错误；用户允许本次时继续；用户选择会话允许时先更新 `SessionPermissionStore`，再继续。若 hook 更新输入，必须重新执行 schema validation、tool validation、classification、guard 和 permission policy。

第六阶段处理 handler 内重复 guard。当前具体工具 handler 会再次调用 guard，并在 ask 时返回错误。实现 executor 级 ask 后，handler 内部重复 guard 仍可能把用户已允许的请求拦回错误。第一版应新增一个清晰机制，让 handler 里的 guard 检查知道本次调用已经被 permission service 授权。推荐在 `ToolRuntime` 中加入 `permission_context` 或 `approved_guard_policies`，由工具使用统一 helper 检查。也可以让 handler 继续 guard，但在 permission service 对 session allow 生效后，guard ask 被 policy 视为 allow；不要让工具绕过 executor 的 deny-first 顺序。最终目标是工具 handler 不各自手写 ask 错误格式，而是由 executor/permission 层统一处理。

第七阶段接入工具裁剪。编辑 `services/tools/registry.py`，让 `ToolRegistry` 可以接收可选 `PermissionPolicy` 或可见性 provider。`visible_descriptors(state)` 继续作为唯一入口，但内部应合并 registry 构造期 disabled/denied、`RuntimeState.metadata` 中的 disabled/denied/hidden，以及 permission policy 的工具级 deny/disabled 结果。路径级 ask/deny 不应在这里猜测，因为模型调用前没有具体工具输入。`tool_schemas(state)` 和 `tool_prompt_sections(state)` 必须继续基于同一个 `visible_descriptors(state)`。

第八阶段实现 CLI prompter 和渲染。新增或扩展 `ui/cli/permissions.py` 和 `ui/cli/renderer.py`。CLI prompter 根据 `PermissionRequest.kind` 或 tool name 选择不同交互面板：

`read_file` 面板显示标题 `Read file permission requested`，目标路径、规范化路径、原因、是否在危险目录、可选项 `y` 本次允许、`s` 本会话允许读取该目录、`n` 拒绝。

`edit_file` 面板显示标题 `Edit file permission requested`，目标路径、原因、是否创建或编辑、`old_string`/`new_string` 简化 diff、`replace_all`、可选项 `y` 本次允许、`s` 本会话允许编辑该目录、`n` 拒绝。不要读取或显示超大文件全量内容；diff 只来自工具输入和短预览。

`glob` 面板显示标题 `Search files permission requested`，搜索根目录、pattern、分页参数、原因、可选项 `y` 本次允许、`s` 本会话允许列出该目录、`n` 拒绝。

`grep` 面板显示标题 `Search contents permission requested`，搜索根目录、pattern、glob/type/output mode、原因、可选项 `y` 本次允许、`s` 本会话允许搜索该目录、`n` 拒绝。

未来 `bash` 面板保留接口和测试 fixture，但如果本计划不新增 bash 工具，不要求完整实现 bash parser。可以先提供 fallback 面板显示 tool name、input JSON、reason、`y`/`n`。

第九阶段更新 CLI runtime 装配。编辑 `ui/cli/app.py` 和 `ui/cli/types.py`，在 `build_runtime()` 中创建 `SessionPermissionStore`、`PermissionPolicy` 和 `CliPermissionPrompter`，并注入 `RegistryToolExecutor` 与 `ToolRegistry`。`/resume` 或 `CliRuntime.with_session()` 创建新 session 时，应创建新的 session permission store，避免旧会话临时授权泄露到新 session。`/clear` 也应清掉 session 授权。

第十阶段补充测试。新增 `tests/test_permission_policy.py` 覆盖 deny-first、危险目录 ask、外部目录 ask、可疑 Windows 路径 ask、session allow 只覆盖 ask 不覆盖 deny。更新 `tests/test_tool_registry_and_executor.py` 覆盖 executor ask allow 后执行、ask deny 不执行、hook 更新输入后重新 permission、unknown tool 和 invalid input 不触发 prompter。更新 `tests/test_file_tools_guard.py` 和 `tests/test_search_tools.py`，将当前期望 `path_guard_ask_required` 的场景拆成无 prompter fallback 和有 prompter allow/deny 两类测试。新增 `tests/test_cli_permissions.py` 或合并到现有 CLI tests，覆盖不同工具面板渲染文本和用户选择解析。

第十一阶段更新文档和技术债。编辑 `architecture.md`，把 `services/permissions/` 写入目标目录和职责，说明 guard 负责路径分类，permission policy 负责 deny/ask/allow 决策和用户确认协调，CLI 只负责展示。编辑 `docs/tech-debt/tech-debt-tracker.md`，把 TD-007 和 TD-008 中权限交互、完整 permission policy 的状态调整为部分缓解，并新增剩余债务，例如 bash 命令解析、持久规则、多来源规则合并、observability trace、图形 UI 和 sub-agent permission bubbling。

## Concrete Steps

所有命令都在仓库根目录执行：

    cd D:\study\OneCode

开始前检查工作区，不覆盖用户已有变更：

    git status --short

新增权限服务文件：

    services/permissions/__init__.py
    services/permissions/types.py
    services/permissions/session.py
    services/permissions/policy.py
    services/permissions/prompter.py

编辑工具运行时：

    services/tools/types.py
    services/tools/executor.py
    services/tools/registry.py

按需要编辑具体工具，去除分散的 ask 错误格式或改为消费统一 permission context：

    tools/read_file/tool.py
    tools/edit_file/tool.py
    tools/glob/tool.py
    tools/grep/tool.py

编辑 CLI 装配和渲染：

    ui/cli/app.py
    ui/cli/types.py
    ui/cli/renderer.py
    ui/cli/permissions.py

新增和更新测试：

    tests/test_permission_policy.py
    tests/test_tool_registry_and_executor.py
    tests/test_file_tools_guard.py
    tests/test_search_tools.py
    tests/test_cli_permissions.py

更新文档：

    architecture.md
    docs/tech-debt/tech-debt-tracker.md

实现过程中先运行 focused tests：

    uv run python -m pytest tests/test_permission_policy.py tests/test_tool_registry_and_executor.py -q

再运行文件工具和 CLI 相关测试：

    uv run python -m pytest tests/test_file_tools_guard.py tests/test_search_tools.py tests/test_cli_permissions.py -q

运行 compile check：

    uv run python -m compileall core services infrastructure tools ui prompts

最后运行全量测试：

    uv run python -m pytest tests -q

## Validation and Acceptance

验收标准一：外部目录访问进入 CLI ask。构造模型或测试直接请求 `read_file` 读取 workspace 外文件时，executor 不执行 handler，先生成 `PermissionRequest`。用户选择 `y` 后读取成功；用户选择 `n` 后返回结构化 permission denied tool result，并且 `state.metadata["files_read"]` 不新增目标路径。

验收标准二：危险目录进入 CLI ask。请求读取或编辑 `.git/config`、`.vscode/settings.json`、`.idea/` 或 `.onecode/` 内文件时，即使路径在 workspace 内，也必须 ask。用户未允许前不得读取或写入。

验收标准三：可疑 Windows 路径进入 ask 或安全失败。输入如 `C:\...`、`D:\...`、`/mnt/c/...`、`/cygdrive/c/...`、UNC 路径或解析异常路径时，不得因为字符串形式绕过 guard。若能解析且位于 workspace 外，应 ask；若解析失败，应返回结构化错误，不得执行 handler。

验收标准四：deny 优先。若路径命中 `SandboxBoundary.denied_patterns`，即使用户此前会话允许了同目录，也必须 deny，且不显示 allow 面板。工具级 deny 也必须使工具不出现在 schema 和 prompt 中，并且执行入口仍拒绝旧 tool call。

验收标准五：session allow 只覆盖 ask。用户在 CLI 面板选择会话允许某目录后，同一 session 中同工具同 operation 对该目录的后续 ask 自动 allow；新 session、`/clear` 或 `/resume` 后该授权不保留。session allow 不写入磁盘。

验收标准六：不同工具展示不同 CLI 面板。`read_file`、`edit_file`、`glob`、`grep` 至少各有独立标题和目标摘要；`edit_file` 面板包含简化 diff；搜索工具面板包含 pattern 和搜索根；fallback 面板可用于未知 future tool。

验收标准七：hook 修改输入后重新权限检查。若 `PreToolUse` hook 把一个 workspace 内路径改成外部路径或危险目录路径，executor 必须重新触发 ask 或 deny，不能沿用原始输入的 allow。

验收标准八：schema 和 prompt 裁剪一致。工具级 denied 或 disabled 的工具不出现在 `ToolRegistry.tool_schemas(state)`，也不出现在 `ToolRegistry.tool_prompt_sections(state)` 和动态 system prompt 中。

验收标准九：以下命令通过：

    uv run python -m compileall core services infrastructure tools ui prompts
    uv run python -m pytest tests -q

## Idempotence and Recovery

本计划的实现应是 additive-first。先新增 `services/permissions/` 和 CLI prompter，再把 executor 接入新服务，最后清理分散的 ask 错误路径。任何阶段都不应删除 transcript、`.onecode` 会话文件或用户项目文件。

如果 CLI 输入在权限确认时收到 EOF 或 KeyboardInterrupt，应视为 deny，并返回结构化 tool result 给模型或停止当前用户请求，不应半执行工具。

如果权限服务未注入 prompter，executor 应有明确行为。推荐在测试和非交互环境中把 ask 转换为结构化 `permission_ask_required` 错误，保持当前 fail closed 行为；在 CLI runtime 中必须注入 prompter。

如果发现 handler 内重复 guard 与 executor 级授权冲突，优先保持 deny-first 和不执行未授权 side effect。可以在第一版保留 handler 重复 guard 作为安全兜底，但必须通过 session permission context 让用户已允许的 ask 不被无理由拦回。

如果危险目录列表误伤正常读取，可在后续计划中增加配置化或更细 operation 区分；第一版按保守策略执行。

## Artifacts and Notes

目标权限请求结构示例：

    PermissionRequest(
        request_id="perm-call-1",
        tool_name="edit_file",
        tool_call_id="call-1",
        action="ask",
        reason="Target is inside a protected project directory: .git",
        targets=(...),
        tool_input={...},
        options=(allow_once, allow_session_directory, deny),
    )

目标 CLI 面板示例：

    Permission required: Edit file
    reason: Target is inside a protected project directory: .git
    path: .git/config
    operation: write

    Proposed edit:
    - old_string: old
    + new_string: new
    replace_all: false

    [y] allow once  [s] allow edits in this directory for this session  [n] deny

结构化拒绝结果示例：

    {
      "error": "permission_denied",
      "tool_name": "edit_file",
      "tool_call_id": "call-1",
      "reason": "User denied the permission request.",
      "decision": "deny"
    }

结构化 ask required fallback 示例：

    {
      "error": "permission_ask_required",
      "tool_name": "read_file",
      "reason": "Path is outside the configured sandbox boundary.",
      "decision": "ask"
    }

## Interfaces and Dependencies

`services/permissions/types.py` 应定义类似接口：

    PermissionAction = Literal["allow", "ask", "deny", "passthrough"]
    PermissionScope = Literal["once", "session"]

    @dataclass(frozen=True)
    class PermissionDecision:
        action: PermissionAction
        reason: str
        source: str
        targets: tuple[ToolTarget, ...] = ()
        metadata: dict[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True)
    class PermissionRequest:
        request_id: str
        tool_call: ToolCall
        descriptor: ToolDescriptor
        classification: ToolCallClassification
        decision: PermissionDecision
        tool_input: dict[str, Any]

    @dataclass(frozen=True)
    class PermissionResponse:
        action: Literal["allow", "deny"]
        scope: PermissionScope = "once"
        feedback: str | None = None
        metadata: dict[str, Any] = field(default_factory=dict)

`services/permissions/prompter.py` 应定义：

    class PermissionPrompter(Protocol):
        def request_permission(
            self,
            request: PermissionRequest,
        ) -> PermissionResponse: ...

`services/permissions/session.py` 应定义：

    class SessionPermissionStore:
        def allow_directory(
            self,
            *,
            tool_name: str,
            operation: str,
            directory: Path,
        ) -> None: ...

        def is_allowed(
            self,
            *,
            tool_name: str,
            operation: str,
            target: Path,
        ) -> bool: ...

        def deny_tool(self, tool_name: str) -> None: ...
        def is_tool_denied(self, tool_name: str) -> bool: ...
        def clear(self) -> None: ...

`services/permissions/policy.py` 应定义：

    class PermissionPolicy:
        def evaluate(
            self,
            *,
            tool_call: ToolCall,
            descriptor: ToolDescriptor,
            classification: ToolCallClassification,
            guard_policies: tuple[GuardPolicy, ...],
            state: RuntimeState,
        ) -> PermissionDecision: ...

`services/tools/executor.py` 的 `RegistryToolExecutor.__init__()` 应增加可选参数：

    permission_policy: PermissionPolicy | None = None
    permission_prompter: PermissionPrompter | None = None

如果不传 policy，保持现有保守行为：deny 和 ask 都不执行 handler，其中 ask 返回结构化 `permission_ask_required`。CLI 装配必须传入 policy 和 prompter。

`ui/cli/permissions.py` 应定义：

    class CliPermissionPrompter:
        def request_permission(
            self,
            request: PermissionRequest,
        ) -> PermissionResponse: ...

CLI prompter 只负责显示和读取用户选择；它不直接修改文件、执行工具或绕过 permission policy。

2026-06-04 / Codex: 初始中文 ExecPlan 创建，纳入用户关于危险目录、项目外路径、可疑 Windows 路径、CLI 面板、session 授权、工具裁剪和 edit diff 展示的决策。

2026-06-04 / Codex: 实现第一版权限服务、executor 接入、CLI prompter、工具级裁剪、handler 已批准 ask 上下文和相关测试；同步更新架构与技术债文档，并完成 compile check 与全量测试验证。
