# 整合 Provider、受 Guard 保护的文件工具与工具 Hooks

本 ExecPlan 是一份活文档。随着工作推进，必须持续更新 `Progress`、`Surprises & Discoveries`、`Decision Log` 和 `Outcomes & Retrospective` 四个章节。

本文档遵循仓库根目录的 `PLANS.md`。任何实施本计划的贡献者都必须保持计划自包含，在每个停止点更新计划，并记录实现过程中产生的决策和发现。

## Purpose / Big Picture

完成本改动后，OneCode 将能够用现有的薄主循环调用真实的 OpenAI-compatible 模型 provider，只向模型暴露两个具体文件系统工具，并在任一工具接触文件系统前强制执行 sandbox path guard。这两个工具是 FileRead 和 FileEdit，在 OneCode 中实现为 `read_file` 和 `edit_file`，以匹配 Python 的 snake_case 命名和目标架构目录。

用户可以通过模拟 provider 返回 tool call 的测试看到工作效果：模型请求读取或编辑文件，主循环通过 registry-backed executor 分发请求，executor 检查 sandbox guard，只有路径被允许时工具才会运行，并且下一次模型调用时 provider 会收到合法的 Chat Completions tool-result message。后续里程碑会加入工具 hooks，使扩展逻辑可以观察、阻断或调整工具调用，而无需修改 `core/loop.py`。

## Progress

- [x] (2026-06-04 04:00Z) 阅读 `AGENTS.md`、`architecture.md`、`PLANS.md`、`docs/design-docs/core-beliefs.md`、`docs/tech-debt/tech-debt-tracker.md`，以及已完成的主循环、provider、guard 计划。
- [x] (2026-06-04 04:15Z) 检查当前实现：`core/loop.py`、`core/context_engine.py`、`services/model/`、`infrastructure/providers/`、`services/guard/` 和 `services/tools/`。
- [x] (2026-06-04 04:30Z) 阅读相关参考资料：`docs/references/s02_tool_use`、`docs/references/s04_hooks`、`docs/references/主循环和重建上下文` 和 `docs/references/Tools_full`。
- [x] (2026-06-04 04:45Z) 在 `docs/exec-plans/active/main-loop-provider-guard-file-tools-hooks.md` 撰写本 ExecPlan。
- [x] (2026-06-04 05:10Z) 重新按 `AGENTS.md` 要求复核 `architecture.md`、`docs/design-docs/core-beliefs.md`、活跃技术债、已完成的主循环/provider/guard 计划、当前 loop/provider/guard/tools 代码、provider 与 loop 测试，以及 FileRead/FileEdit/hooks/tool-use 参考资料；确认下一步应从 Milestone 1 的 provider-compatible tool-result projection 开始。
- [x] (2026-06-04 05:35Z) 在加入真实工具前，实现 provider-compatible 的内部 tool-result projection。新增 `ToolExecutionResult`，让 `MessageStore` 存储内部 `role: "tool_result"` messages，并让 Chat Completions adapter 在发送前投影为合法 `role: "tool"` messages；`uv run python -m pytest tests/test_loop.py tests/test_openai_compatible_provider.py -q` 通过，结果为 24 passed。
- [x] (2026-06-04 05:50Z) 实现 tool descriptors、registry、schema generation、runtime context 和 concrete executor。新增 `ToolDescriptor`、`ToolRuntime`、`ValidationResult`、`ToolRegistry`、OpenAI-compatible schema projection 和 `RegistryToolExecutor`；unknown tools、schema validation failures、custom validation failures 和 handler exceptions 均返回 structured error tool results。`uv run python -m pytest tests/test_tool_registry_and_executor.py -q` 通过，结果为 7 passed。
- [x] (2026-06-04 06:10Z) 实现受 guard 保护的 `tools/read_file` 和 `tools/edit_file`。新增顶层 `tools/` package、两个 concrete descriptors、prompt modules 和文件工具测试；`read_file` 返回带行号文本并记录 session read state，`edit_file` 执行 exact string replacement、要求 existing file 先读过，并在 guard deny/ask 时不触碰文件系统。`uv run python -m pytest tests/test_file_tools_guard.py -q` 通过，结果为 11 passed。
- [x] (2026-06-04 06:25Z) 将 registry 生成的 schemas 接入 `ContextEngine`，并证明真实 provider client 可以通过 fake transport 驱动工具循环。新增 `tests/test_runtime_integration.py`，使用真实 `ToolRegistry`、`RegistryToolExecutor`、`SandboxGuard` 和 `OpenAICompatibleChatCompletionsClient` 验证 fake provider 能驱动 `read_file`，以及 `read_file` 后继续驱动 `edit_file` 修改 sandbox 内文件；`uv run python -m pytest tests/test_runtime_integration.py tests/test_openai_compatible_provider.py -q` 通过，结果为 22 passed。
- [x] (2026-06-04 06:40Z) 实现最小工具 hook 事件，并从 executor 运行它们。新增 `services/hooks/events.py`、`services/hooks/registry.py` 和 `tests/test_hooks.py`；`RegistryToolExecutor` 现在运行 `PreToolUse`、`PostToolUse` 和 `ToolError`，并在 hook-updated input 后重新 validation 和 guard。`uv run python -m pytest tests/test_hooks.py tests/test_tool_registry_and_executor.py tests/test_file_tools_guard.py -q` 通过，结果为 24 passed。
- [x] (2026-06-04 06:45Z) 运行 compile 与 pytest 验证，并用最终测试数量和结果更新本计划。`uv run python -m compileall core services infrastructure tools` 通过；第一次完整 pytest 发现 `tests/test_context_engine.py` 仍使用旧 dict tool-result input，更新为 `ToolExecutionResult` 后，`uv run python -m pytest tests -q` 通过，结果为 65 passed。

## Surprises & Discoveries

- Observation: `services/context/message_store.py` 当前把 tool results 作为包含内部 `tool_result` blocks 的 user message 追加。
  Evidence: `docs/tech-debt/tech-debt-tracker.md` 记录了 TD-001，并且 `MessageStore.append_tool_results()` 存储 `{"role": "user", "content": [...]}`。在 assistant `tool_calls` message 之后，这不是 OpenAI-compatible Chat Completions 的合法形态。

- Observation: sandbox guard 已经有经过测试的路径分类 API，但没有真实工具执行路径使用它。
  Evidence: `services/guard/policy.py` 暴露 `SandboxGuard.check_path()` 和 `check_write_target()`，而 `services/tools/executor.py` 目前只是一个 `Protocol`。

- Observation: 参考工具系统把工具视为富 metadata 对象，而不是普通函数。
  Evidence: `docs/references/Tools_full/Tool.ts` 定义的工具包含 schema、read-only metadata、concurrency safety、validation、permission checks、path extraction、prompt text 和 result mapping。OneCode 现在只应实现 FileRead/FileEdit 所需的子集，但 descriptor 应保留这些未来字段的可见边界。

- Observation: 参考 FileRead/FileEdit 的工具名是 `Read` 和 `Edit`，而 OneCode 架构使用 `tools/read_file` 和 `tools/edit_file` 这类目录。
  Evidence: `docs/references/Tools_full/FileReadTool/prompt.ts` 导出 `FILE_READ_TOOL_NAME = 'Read'`；`architecture.md` 展示了 `tools/read_file/` 和 `tools/edit_file/`。本计划使用 Python 工具名 `read_file` 和 `edit_file`，并说明它们是 FileRead 和 FileEdit 在 OneCode 中的对应实现。

- Observation: 当前测试仍固定旧的 tool result 存储形态，Milestone 1 需要同步更新 fake executor 输出和断言。
  Evidence: `tests/test_loop.py` 和 `tests/test_openai_compatible_provider.py` 中的 fake executor 返回 `{"type": "tool_result", "tool_use_id": ...}` blocks，并且 `tests/test_loop.py` 断言 `MessageStore` 将这些 blocks 追加为 `role: "user"` message。实现 `ToolExecutionResult` 后，这些测试应改为断言内部 `role: "tool_result"` message 以及 provider payload 中的 `role: "tool"` projection。

- Observation: 完整测试还暴露了 `tests/test_context_engine.py` 中两处旧的 direct `append_tool_results()` 调用。
  Evidence: 第一次运行 `uv run python -m pytest tests -q` 时结果为 2 failed, 63 passed，失败原因是测试传入旧 dict block，`MessageStore.append_tool_results()` 现在要求 `ToolExecutionResult`。更新测试后完整测试为 65 passed。

## Decision Log

- Decision: 保持 `core/loop.py` 薄，不从其中 import 具体 provider、具体工具、guard 实现或 hook 实现。
  Rationale: `architecture.md` 和 `docs/design-docs/core-beliefs.md` 都要求主循环只做编排：构建上下文、调用模型、通过 executor 执行被请求的工具、追加结果并停止。Provider、guard、tools 和 hooks 必须通过 service 边界注入。
  

- Decision: 在实现真实工具前，先修复 provider-compatible 的 tool-result projection。
  Rationale: 否则真实工具会产出第二次 Chat Completions 请求无法合法发送的 result message。这直接处理 TD-001，并让后续所有工具测试都有意义。
  

- Decision: 在本计划中，将 sandbox 的 `deny` 和 `ask` 都视为不执行工具的结构化 tool error。
  Rationale: 当前仓库已有 guard service，但没有完整的交互式 permission request service。返回结构化 tool result 可以让模型自我修正，同时不允许外部目录访问，也不让 loop 崩溃。
  

- Decision: 在 hooks 运行前检查模型提供路径的 guard，并在任何 hook-updated input 之后再次检查 guard。
  Rationale: 这能防止 hook 把原本 denied 的路径改写成 allowed 路径，也能防止 hook 把 allowed 路径改写成 unsafe 路径。Hooks 可以阻断或更新输入，但不能覆盖 guard decision。
  

- Decision: 本计划只实现文本 FileRead 和 exact-string FileEdit。
  Rationale: 参考 FileRead 支持 images、PDFs、notebooks、token accounting 和 file-state caches，但这些都超出用户请求的第一批工具范围。第一版可工作行为应保持小、确定、可测试。

## Outcomes & Retrospective

2026-06-04 / Codex: 本计划已完成实施。OneCode 现在有 provider-compatible 内部 tool result boundary、registry-backed tool descriptors 和 schema projection、`RegistryToolExecutor`、受 `SandboxGuard` 保护的 `read_file` / `edit_file` concrete tools，以及 executor-local `PreToolUse` / `PostToolUse` / `ToolError` hooks。`core/loop.py` 保持薄，没有 import 具体工具、provider、guard 或 hook modules。最终验证结果是 `uv run python -m compileall core services infrastructure tools` 通过，`uv run python -m pytest tests -q` 通过，结果为 65 passed。TD-001、TD-002 和 TD-003 均被实质性减少；是否归档仍应由后续 tech-debt tracker 更新按条目 remediation 文本逐项判断。

## Context and Orientation

OneCode 是一个 Python code-agent runtime。当前 agent loop 位于 `core/loop.py`。它接收用户 prompt，将其追加到 `services/context/message_store.py`，请求 `core/context_engine.py` 构建 `ContextSnapshot`，再通过注入的 `services/model/client.py` `ModelClient` 发送 snapshot，并把返回的任意 `ToolCall` 交给注入的 `services/tools/executor.py` `ToolExecutor`。这个 loop 已经具备正确形态，不应重写。

当前模型 provider 边界位于 `services/model/` 和 `infrastructure/providers/`。`services/model/types.py` 定义 provider-neutral 的 `LLMResponse`、`ModelUsage` 和 `ProviderError`。`infrastructure/providers/chat_completions.py` 实现 `OpenAICompatibleChatCompletionsClient`，负责把 `ContextSnapshot` 转成 Chat Completions 请求，并把 provider response 解析成 `LLMResponse`。Adapter 已经能解析 `tool_calls`，但 outbound message projection 当前过于直接地透传 snapshot messages。在真实工具循环合法之前，必须修复这一点。

当前 sandbox guard 边界位于 `services/guard/`。`services/guard/boundary.py` 定义 `SandboxBoundary`，并将路径分类为 `inside_workspace`、`inside_worktree`、`inside_extra_allowed`、`external_directory` 或 `denied`。`services/guard/policy.py` 将分类封装进 `SandboxGuard.check_path()` 和 `SandboxGuard.check_write_target()`，返回 `allow`、`ask` 或 `deny`。在本计划中，`allow` 允许具体工具继续执行，而 `ask` 和 `deny` 会变成结构化 tool error，因为目前还不存在交互式 permission service。

当前工具系统只是骨架。`services/tools/types.py` 只定义了 `ToolCall`。`services/tools/executor.py` 是 protocol，不执行 registry lookup、input validation、guard checks、真实工具调用或 hook dispatch。目标架构要求 service layer 持有 registry、schema generation 和 execution，而具体工具放在顶层 `tools/`。

参考资料会塑造本计划，但不覆盖 OneCode 架构。`docs/references/s02_tool_use` 展示核心思想：增加工具应增加一个 descriptor 和一个 handler，而不改变 loop。`docs/references/s04_hooks` 展示 hook callbacks 应挂在 lifecycle points 上，而不是写进 loop。`docs/references/Tools_full` 展示生产级 FileRead/FileEdit 和 tool execution pipelines；本计划只采用最小有用子集：descriptor metadata、执行前 validation、文件系统访问前 guard、明确 result mapping，以及后续的 PreToolUse/PostToolUse hooks。

本计划使用的定义如下：

- Tool descriptor 是单个工具名称、描述、JSON schema、metadata、path extractor、validator、prompt text 和 handler 的唯一事实来源。
- Tool registry 是存储 enabled descriptors 并能返回模型可见 schemas 的 lookup 对象。
- Tool executor 是接收模型 `ToolCall`、查找匹配 descriptor、验证输入、检查 guard policy、运行 hooks、调用 tool handler、捕获错误并返回模型可消费 tool results 的 service。
- Tool result 是单个 tool call 输出的内部记录。它包含原始 provider tool call id、工具名、文本 content payload、是否为 error，以及可选 metadata。
- Hook 是为 lifecycle event 注册的 callback。在本计划中，hooks 只限于工具生命周期事件，并且可以阻断工具调用、更新输入或观察结果。

## Plan of Work

### Step 1
修复 provider-compatible tool result projection 的消息边界。在 `services/tools/types.py` 中增加 `ToolExecutionResult` 或 `ToolResult` dataclass，字段包括 `tool_call_id`、`tool_name`、`content`、`is_error` 和 `metadata`。修改 `services/context/message_store.py` 中的 `MessageStore.append_tool_results()`，让它为每个 result 存储一个内部 `role: "tool_result"` message，而不是存储一个包含 content blocks 的 user message。存储后的 tool result message 应是内部 message，而不是 Chat Completions wire message：

    {
        "role": "tool_result",
        "tool_call_id": "call_x",
        "tool_name": "read_file",
        "content": "1\tfrom __future__ import annotations",
        "is_error": false,
        "metadata": {}
    }

然后更新 `infrastructure/providers/chat_completions.py` 中的 `OpenAICompatibleChatCompletionsClient._build_payload()`，将内部 tool result messages 投影为 provider wire messages：

    {"role": "tool", "tool_call_id": "call_x", "content": "..."}

Adapter 应继续透传普通 user 和 assistant messages，并保留 provider response 产生的 assistant `tool_calls` messages。必须更新 `tests/test_openai_compatible_provider.py` 和 `tests/test_loop.py` 中的测试，断言工具调用后的第二次 provider request 包含合法的 assistant tool-call message，后面跟着一个或多个 `role: "tool"` messages。

### Step 2
实现 registry-backed tool runtime。在 `services/tools/types.py` 中扩展 `ToolDescriptor`、`ToolRuntime`、`ToolExecutionResult` 和一个小型 validation result type。命名保持具体且 Pythonic。`ToolDescriptor` 至少应包含 `name`、`description`、`input_schema`、`handler`、`read_only`、`modifies_filesystem`、`requires_guard`、`concurrency_safe`、`max_result_size_chars`、`prompt`、`validate_input` 和 `get_path`。`ToolRuntime` 应持有 `state`、`guard`，以及 FileRead/FileEdit 所需的小型 runtime services，包括 `RuntimeState.metadata` 中的 read-tracking dictionary。

新增 `services/tools/registry.py`。它应在构造时接收 descriptors，拒绝重复名称，按名称返回 descriptor，并以稳定名称顺序暴露 enabled descriptors。新增 `services/tools/schema.py`。它应将 descriptor 转成 OpenAI-compatible function-tool schema：

    {
        "type": "function",
        "function": {
            "name": descriptor.name,
            "description": descriptor.description,
            "parameters": descriptor.input_schema,
        },
    }

然后在保留 `ToolExecutor` protocol 用于依赖注入的同时，用 concrete executor 替换或扩展当前 protocol-only executor。一个合适形态是继续保留 `ToolExecutor` protocol，并在 `services/tools/executor.py` 中新增 `RegistryToolExecutor`。`RegistryToolExecutor.execute(tool_calls, state)` 在本里程碑中应按 provider 顺序串行执行 tool calls。它应返回 `ToolExecutionResult` 对象列表。Unknown tools、invalid input、guard blocks 和 handler exceptions 必须变成结构化 error results，而不是作为异常逃出 `core/loop.py`。

### Step 3
将 guard 接入 executor。Executor 应通过构造函数或 `ToolRuntime` 接收 `SandboxGuard`。对任何 `requires_guard=True` 且提供 `get_path` function 的 descriptor，executor 应在运行 hooks 前先分类模型提供的路径。如果 guard 返回 `deny`，立即使用 `GuardPolicy.to_tool_error()` 返回 tool error，不运行 handler。如果 guard 返回 `ask`，返回类似 `path_guard_ask_required` 的结构化 error，不运行 handler。如果 guard 返回 `allow`，继续执行。如果 PreToolUse hook 后续更新了 input，则在调用 handler 前，对更新后的 input 再次运行 schema validation 和 guard classification。

### Step 4
在顶层 `tools/` 下实现两个具体工具：

- `tools/read_file/tool.py`
- `tools/read_file/prompt.py`
- `tools/edit_file/tool.py`
- `tools/edit_file/prompt.py`

同时新增 `tools/__init__.py`、`tools/read_file/__init__.py` 和 `tools/edit_file/__init__.py`。具体工具模块应导出一个函数，例如 `descriptor()`，返回 `ToolDescriptor`。不要从 `core/loop.py` import 这些具体工具。测试或 composition helper 可以 import 它们来组装 registry。

`read_file` descriptor 应是 OneCode 的 FileRead 工具。它的 JSON schema 要求 `file_path`，并可选接受 `offset` 和 `limit`。`offset` 是 one-based，默认值为 1。`limit` 存在时是正整数。Handler 应对文件执行 guard-read，拒绝 directories，使用 UTF-8 且用 replacement 处理 decoding errors 来读取文本，split 成 lines，应用 `offset` 和 `limit`，默认读取上限为 2000 lines，并返回 cat-n 风格输出，行号从 1 开始。它应在 `state.metadata["files_read"]` 中记录 normalized path，使 FileEdit 可以要求 prior read。Binary、image、PDF 和 notebook handling 不属于本计划范围。

`edit_file` descriptor 应是 OneCode 的 FileEdit 工具。它的 JSON schema 要求 `file_path`、`old_string` 和 `new_string`，并可选接受默认 false 的 `replace_all`。Handler 应对目标路径执行 guard-write，要求 `old_string != new_string`，并要求该文件在本 session 中已经被读取过；例外是文件不存在且 `old_string` 为空。对 existing file，`old_string` 必须存在。如果它出现多次且 `replace_all` 为 false，返回错误，要求模型提供更多上下文或设置 `replace_all=true`。如果 `replace_all` 为 true，替换所有 exact occurrences。如果目标文件不存在且 `old_string` 为空，在 sandbox 内创建 parent directories 并写入 `new_string`。返回包含 normalized path 和 replacement count 的简洁 success result。不要在本计划中实现 fuzzy matching、quote normalization、diff UI 或 user-modified patch confirmation。

### Step 5
将 registry-generated schemas 接入 context reconstruction。`ContextEngine` 已接受 `ToolSchemaProvider`；使用新的 `ToolRegistry` 或它之上的小型 adapter 作为 schema provider。Loop 仍应通过构造函数接收 dependencies。在测试中，构造 `MessageStore`、`ContextEngine(message_store, tool_schema_provider=registry)`、使用 fake transport 的 provider client、`SandboxGuard(SandboxBoundary(cwd=tmp_path))` 和 `RegistryToolExecutor(registry, guard=guard)`。

### Step 6
增加 tool hooks。创建 `services/hooks/events.py`、`services/hooks/registry.py`，并可选创建 `services/hooks/builtin.py`。第一版实现只需支持 `PreToolUse`、`PostToolUse` 和 `ToolError`，即使架构中还命名了 `UserPromptSubmit`、`PreCompact`、`PostCompact` 和 `Stop`。PreToolUse hook 接收一个 payload，其中包含 tool call、descriptor、当前 input 和 state。它可以返回无结果、blocking error 或 updated input。PostToolUse hook 接收 final result 并可观察它；在本里程碑中，它不应修改模型可见输出。ToolError hook 接收 validation failures、guard blocks 和 handler exceptions 的结构化错误。

将 hooks 集成进 `RegistryToolExecutor`，不要集成进 `AgentLoop`。Executor 顺序应为：

    1. 按 tool name 查找 descriptor。
    2. 根据 descriptor schema 验证模型 input。
    3. 如果 descriptor requires guard，检查原始路径。Deny 和 ask 在这里停止。
    4. 运行 PreToolUse hooks。如果 hook 阻断，返回 tool error。
    5. 如果 hook 更新 input，验证 updated input 并再次运行 guard。
    6. 调用 descriptor handler。
    7. 成功时运行 PostToolUse hooks。
    8. 对任何 validation、guard 或 handler failure，运行 ToolError hooks，并返回结构化 tool error。

这个顺序保持 guard denial 强于 hook updates，同时仍允许 hooks 增加无害的 input normalization 或 logging。任何 hook 都不能返回一个绕过 guard 的 allow result。

## Concrete Steps

从仓库根目录工作：

    cd D:\study\OneCode

修改代码前，确认当前 working tree 并保留无关用户改动：

    git status --short

计划撰写时的预期状态包括：`tech_debt_tracker_guide.md` 已有修改，并且 `docs/Tools_full` 或 `docs/references/Tools_full` 下有未跟踪参考材料。不要 revert 它们。只编辑本计划要求的文件。

Milestone 1 是 provider-compatible tool result projection。编辑 `services/tools/types.py`、`services/context/message_store.py`、`infrastructure/providers/chat_completions.py`、`tests/test_loop.py` 和 `tests/test_openai_compatible_provider.py`。新增或更新测试，使 fake provider 先返回 tool call，然后收到第二次请求，其 `messages` list 包含：

    {"role": "user", "content": "inspect"}
    {"role": "assistant", "content": null or "", "tool_calls": [...]}
    {"role": "tool", "tool_call_id": "call_x", "content": "..."}

运行：

    uv run python -m pytest tests/test_loop.py tests/test_openai_compatible_provider.py -q

预期结果是指定测试通过，并且没有任何 provider payload 对 Chat Completions 使用 `{"role": "user", "content": [{"type": "tool_result", ...}]}`。

Milestone 2 是 registry 和 executor。新增 `services/tools/registry.py` 和 `services/tools/schema.py`。扩展 `services/tools/types.py`。将 `services/tools/executor.py` 替换或扩展为包含 `RegistryToolExecutor`。新增 `tests/test_tool_registry_and_executor.py`，覆盖 duplicate descriptor rejection、stable schema generation、unknown tool errors、input validation errors、serial execution order，以及将 handler exceptions 转成 tool errors。

运行：

    uv run python -m pytest tests/test_tool_registry_and_executor.py -q

预期结果是所有 registry/executor tests 通过，除了 pytest temporary directories 以外不接触真实文件系统。

Milestone 3 是受 guard 保护的 FileRead 和 FileEdit。新增顶层 `tools/` package 和两个工具目录。新增 `tests/test_file_tools_guard.py`。覆盖 successful workspace reads、line offsets、line limits、directory read errors、denied-pattern read failure、external-directory read returning an ask-required tool error、edit requiring prior read、successful exact single replacement、multiple-match failure without `replace_all`、all-match replacement with `replace_all`、new-file creation with empty `old_string`，以及 prevention of writes outside the sandbox。

运行：

    uv run python -m pytest tests/test_file_tools_guard.py -q

预期结果是所有 file tool tests 通过。测试应通过检查工具调用前后的 file contents，证明 denied 和 external paths 未被读取或写入。

Milestone 4 是 loop/provider/tool integration。新增一个测试，可以放在 `tests/test_openai_compatible_provider.py` 中，也可以放在新的 `tests/test_runtime_integration.py` 中。该测试使用带 fake transport 的 `OpenAICompatibleChatCompletionsClient`、包含 `read_file` 和 `edit_file` 的真实 `ToolRegistry`、真实 `RegistryToolExecutor`，以及以 `tmp_path` 为根的 `SandboxGuard`。Fake provider 应先请求 `read_file`，然后返回 final text。第二个测试应在 read 之后请求 `edit_file`，并证明 temp file 已改变。

运行：

    uv run python -m pytest tests/test_runtime_integration.py tests/test_openai_compatible_provider.py -q

如果 integration tests 放在已有测试文件中，相应调整命令。预期行为是 `AgentLoop.run()` 返回最终 provider text，并且 fake transport 记录合法的 two-request 或 three-request tool loop。

Milestone 5 是 tool hooks。新增 `services/hooks/events.py`、`services/hooks/registry.py` 和 `tests/test_hooks.py`。将 `HookRegistry` 接入 `RegistryToolExecutor`。覆盖阻断 `edit_file` 的 PreToolUse hook、更新 `read_file` input 并导致 guard 重新检查 updated path 的 PreToolUse hook、观察 successful output 的 PostToolUse hook，以及观察 guard denial 或 validation failure 的 ToolError hook。新增一个 regression test，证明 hook 不能绕过 denied path。

运行：

    uv run python -m pytest tests/test_hooks.py tests/test_tool_registry_and_executor.py tests/test_file_tools_guard.py -q

预期结果是 hook behavior 只通过 executor tests 可见，并且 `core/loop.py` 除了严格必要的 type annotation 更新外保持不变。

最后，运行完整验证：

    uv run python -m compileall core services infrastructure tools
    uv run python -m pytest tests -q

将最终 passing test count 记录到 `Progress` 和 `Outcomes & Retrospective`。

## Validation and Acceptance

验收以行为为准。当以下条件全部满足时，改动完成：

1. Fake OpenAI-compatible provider 可以驱动 `AgentLoop` 完成至少一次 `read_file` tool call，然后返回最终 assistant response。
2. 工具调用后的第二次 provider request 使用合法 Chat Completions messages，包括 `role: "tool"` 和原始 `tool_call_id`。
3. `ContextEngine` 从 registry 或 registry adapter 接收 tool schemas，而不是从 hardcoded loop logic 接收。
4. `core/loop.py` 不 import `tools/read_file`、`tools/edit_file`、`SandboxGuard`、`OpenAICompatibleChatCompletionsClient` 或 hook modules。
5. `read_file` 可以读取 sandbox 内的 text file，返回带行号 content，并遵守 `offset` 和 `limit`。
6. `edit_file` 可以用 exact string replacement 修改一个 previously read sandbox file，并且只有在 `old_string` 为空时才能创建新的 sandbox file。
7. Guard `deny` 和 `external_directory` decisions 返回结构化 tool errors，并且不接触目标文件。
8. Unknown tool names、invalid input、guard blocks 和 handler exceptions 变成 tool results，而不是从 `AgentLoop.run()` 抛出 uncaught exceptions。
9. PreToolUse、PostToolUse 和 ToolError hooks 从 executor 运行。Hook 可以 block 或 update input，但任何 hook 都不能覆盖 guard denial。
10. `uv run python -m compileall core services infrastructure tools` 和 `uv run python -m pytest tests -q` 均通过。

本实现还应减少 TD-001、TD-002 和 TD-003。除非实现完全满足各技术债条目的 remediation text，否则不要将这些 debt entries 标记为 resolved。如果只修复了某条的一部分，应在 tracker 中用具体状态说明更新，而不是删除它。

## Idempotence and Recovery

所有步骤都是 additive 或局部编辑，可以重试。如果某个里程碑后测试失败，保留该里程碑的文件，并修复导致失败的最小边界。不要为了让测试通过而重写 main loop；失败通常应在 message projection、registry schema generation、executor behavior、concrete tool validation 或 hook dispatch 中处理。

避免 destructive shell commands。测试必须使用 pytest temporary directories 进行文件读取和编辑。测试 guard denial 时，创建一个 denied file，并在工具调用后断言其 content 未改变。测试 external directories 时，在 sandbox 外、pytest 的 `tmp_path` parent 下创建路径，并断言 executor 返回 ask-required tool error，且没有读取或写入文件。

如果改变 message storage 导致现有测试失败，应更新这些测试，使其断言新的 provider-neutral 内部 `tool_result` message shape 和 provider-specific Chat Completions projection。不要仅为保持测试通过而保留旧的 user-message tool-result shape，因为 TD-001 已将它识别为真实 provider loop 的 invalid shape。

如果 hook-updated input 与 guard checks 产生歧义，使用本计划中的保守规则：hooks 前先对原始模型路径进行 guard-check，handler 执行前再对 updated paths 进行 guard-check。任何 `deny` 或 `ask` result 都停止执行。

## Artifacts and Notes

重要现有文件：

- `core/loop.py` 已具备目标编排形态，应保持薄。
- `core/context_engine.py` 已支持 injectable prompt 和 schema providers。
- `services/context/message_store.py` 是当前 invalid tool-result storage 的来源，必须修改。
- `infrastructure/providers/chat_completions.py` 是 Chat Completions-specific message projection 应该所在的位置。
- `services/guard/policy.py` 已暴露 `SandboxGuard.check_path()` 和 `check_write_target()`。
- `services/tools/types.py` 和 `services/tools/executor.py` 当前过小，是主要 service-layer 扩展点。
- `docs/references/s02_tool_use` 展示 registry-style dispatch。
- `docs/references/s04_hooks` 展示 loop 外部的 lifecycle hooks。
- `docs/references/Tools_full/FileReadTool` 和 `docs/references/Tools_full/FileEditTool` 提供 FileRead/FileEdit 的参考行为，但本计划有意只实现 text 与 exact-replacement 子集。

`read_file` 调用后的一个小型预期 provider payload：

    [
      {"role": "user", "content": "Read the file"},
      {
        "role": "assistant",
        "content": "",
        "tool_calls": [
          {
            "id": "call_read",
            "type": "function",
            "function": {
              "name": "read_file",
              "arguments": "{\"file_path\":\"D:/study/OneCode/README.md\"}"
            }
          }
        ]
      },
      {
        "role": "tool",
        "tool_call_id": "call_read",
        "content": "1\t..."
      }
    ]

小型预期 `read_file` schema：

    {
      "type": "function",
      "function": {
        "name": "read_file",
        "description": "Read a text file from the local filesystem.",
        "parameters": {
          "type": "object",
          "properties": {
            "file_path": {"type": "string"},
            "offset": {"type": "integer", "minimum": 1},
            "limit": {"type": "integer", "minimum": 1}
          },
          "required": ["file_path"],
          "additionalProperties": false
        }
      }
    }

小型预期 `edit_file` schema：

    {
      "type": "function",
      "function": {
        "name": "edit_file",
        "description": "Perform exact string replacements in a local text file.",
        "parameters": {
          "type": "object",
          "properties": {
            "file_path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean"}
          },
          "required": ["file_path", "old_string", "new_string"],
          "additionalProperties": false
        }
      }
    }

## Interfaces and Dependencies

在 `services/tools/types.py` 中定义这些接口或非常接近的等价物：

    @dataclass(frozen=True)
    class ToolCall:
        id: str
        name: str
        input: dict[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True)
    class ToolExecutionResult:
        tool_call_id: str
        tool_name: str
        content: str
        is_error: bool = False
        metadata: dict[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True)
    class ValidationResult:
        ok: bool
        message: str | None = None

    @dataclass(frozen=True)
    class ToolRuntime:
        state: RuntimeState
        guard: SandboxGuard | None = None

    @dataclass(frozen=True)
    class ToolDescriptor:
        name: str
        description: str
        input_schema: dict[str, Any]
        handler: Callable[[dict[str, Any], ToolRuntime], ToolExecutionResult]
        read_only: bool
        modifies_filesystem: bool
        requires_guard: bool
        concurrency_safe: bool
        max_result_size_chars: int | None
        prompt: str
        validate_input: Callable[[dict[str, Any], ToolRuntime], ValidationResult] | None = None
        get_path: Callable[[dict[str, Any]], str | Path | None] | None = None

如果 `services/tools/types.py` 直接 import `RuntimeState` 或 `SandboxGuard` 会产生 circular imports，使用 `typing.TYPE_CHECKING` 或 protocols。不要为了避免 cycle 而把 guard logic 移入 tools。

在 `services/tools/registry.py` 中定义 `ToolRegistry`，包含：

    def __init__(self, descriptors: Iterable[ToolDescriptor] = ()) -> None
    def register(self, descriptor: ToolDescriptor) -> None
    def get(self, name: str) -> ToolDescriptor | None
    def descriptors(self) -> tuple[ToolDescriptor, ...]
    def tool_schemas(self, state: RuntimeState) -> tuple[dict[str, Any], ...]

它可以通过暴露 `tool_schemas(state)` 来实现 `core.context_engine.ToolSchemaProvider`。

在 `services/tools/executor.py` 中保留 protocol，并新增：

    class RegistryToolExecutor:
        def __init__(
            self,
            registry: ToolRegistry,
            *,
            guard: SandboxGuard | None = None,
            hooks: HookRegistry | None = None,
        ) -> None: ...

        def execute(
            self,
            tool_calls: tuple[ToolCall, ...],
            state: RuntimeState,
        ) -> list[ToolExecutionResult]: ...

在 `services/hooks/events.py` 中定义一个小型 enum：

    class HookEvent(StrEnum):
        PRE_TOOL_USE = "PreToolUse"
        POST_TOOL_USE = "PostToolUse"
        TOOL_ERROR = "ToolError"

在 `services/hooks/registry.py` 中定义：

    @dataclass(frozen=True)
    class HookResult:
        blocking_error: str | None = None
        updated_input: dict[str, Any] | None = None
        metadata: dict[str, Any] = field(default_factory=dict)

    class HookRegistry:
        def register(self, event: HookEvent, callback: HookCallback) -> None: ...
        def run(self, event: HookEvent, payload: HookPayload) -> HookResult: ...

Registry 应按注册顺序运行 callbacks。对 PreToolUse，第一个 blocking result 停止执行。多个 input updates 可以按顺序应用，只要每次 update 后在 handler 执行前重新 validation 和 re-guard。对 PostToolUse 和 ToolError，callbacks 应观察事件；在本里程碑中，hooks 内的 non-blocking errors 应被捕获到 metadata 或忽略，hook failures 不能让 loop 崩溃。

在 `tools/read_file/tool.py` 中导出：

    def descriptor() -> ToolDescriptor: ...

在 `tools/edit_file/tool.py` 中导出：

    def descriptor() -> ToolDescriptor: ...

不需要新增第三方依赖。使用 Python 标准库、现有 `pytest` 和现有 `uv` environment。除非实现证明手写 minimal validator 过于容易出错，否则不要增加 JSON Schema validation library；如果增加，必须同时更新本计划、`pyproject.toml`，并添加 decision entry。

## Revision Note

2026-06-04 / Codex: 初始 ExecPlan 在阅读仓库指引、当前 runtime code、活跃技术债、已完成的 main-loop/provider/guard plans，以及用户要求的 tool-use、hook、main-loop、FileRead/FileEdit references 后创建。本计划优先处理 provider-compatible tool-result projection，因为否则真实工具会在第二次 Chat Completions 请求中失败。

2026-06-04 / Codex: 开始实施前复核了 `AGENTS.md` 指定阅读顺序、活跃计划、已完成计划、tech debt、当前主循环/provider/guard/tools 代码和相关测试，并记录 Milestone 1 的测试更新注意事项。下一步应直接实现 provider-compatible tool-result projection。

2026-06-04 / Codex: 完成全部里程碑。实现过程中保持 `AgentLoop` 仅做编排，把 provider message projection 放在 `infrastructure/providers/chat_completions.py`，把 tool registry/execution/guard/hook 行为放在 `services/tools/` 与 `services/hooks/`，把 concrete file tools 放在顶层 `tools/`。最终 compile 和完整 pytest 均通过。
