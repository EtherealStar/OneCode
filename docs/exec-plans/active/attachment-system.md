# 实现 OneCode 附件系统

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本计划必须按照仓库根目录的 `PLANS.md` 维护。本文是自包含计划：后续贡献者应能只阅读本文件，并结合文中点名的源码文件，完成附件系统的端到端实现。


## Purpose / Big Picture

完成本计划后，OneCode 会把附件理解为一等运行时上下文。用户可以在工作区根目录输入 `summarize @architecture.md#L1-40`，OneCode 会在当前工作目录内解析这个文件引用，通过与文件工具相同的安全边界读取指定行，把结构化附件消息存入会话，并在下一次模型调用前临时投影为“模型好像已经调用过 `read_file` 并拿到结果”的上下文。

这很重要，因为附件系统让 runtime 能以结构化方式注入用户选择的文件、目录列表、记忆文件、排队命令、hook 结果以及未来 plan mode 提醒，而不是把这些内容硬编码进 agent 主循环，或者全部压平成无结构 prompt 文本。第一版完整可观察行为是文件和目录提及支持：CLI 用户输入带 `@filename` 的 prompt 后，下一次模型请求包含针对该文件的合成 `read_file` assistant 工具调用和匹配的工具结果；transcript 中只保存原始用户 prompt 和结构化附件消息。附件的 UI 渲染明确不在本计划范围内，必须作为技术债记录。


## Progress

- [x] (2026-06-07 00:35+08:00) 已阅读 `AGENTS.md`、`PLANS.md`、`architecture.md`、相关 context/runtime 设计文档、当前活跃的 session memory ExecPlan，以及附件参考文件 `docs/references/attachement/attachments.ts`。
- [x] (2026-06-07 00:45+08:00) 已与用户确认设计决策：`@filename` 限定在当前工作目录；支持文件、行范围和目录，不支持图片或 PDF；虚拟 file-read 工具调用消息只在模型上下文中临时生成；shared attachments 不包含 todo reminders；必须实现 edited text file 检测和已读文件缓存；plan mode 当前只需要预留相关接口。
- [x] (2026-06-07 00:55+08:00) 已创建本活跃 ExecPlan，未修改 runtime 实现代码。
- [ ] 实现附件数据类型、文件状态缓存、mention 解析和 cwd 范围内路径解析。
- [ ] 实现用户输入附件、共享运行时事件附件、仅主线程附件和 edited text file 检测。
- [ ] 在 `MessageStore` 和 JSONL transcript 中保存 attachment messages，同时确保 raw `role="attachment"` 不暴露给 provider。
- [ ] 将 attachment messages 投影成 provider 可见模型消息，包括临时合成的 file-read 工具调用对。
- [ ] 在 CLI 输入进入 agent loop 前接入附件收集。
- [ ] 增加测试、文档更新，并为暂缓的 UI 渲染新增技术债记录。


## Surprises & Discoveries

- Observation: 用户给出的参考路径是 `docs\references\attachment`，但仓库中实际路径是 `docs\references\attachement\attachments.ts`。
  Evidence: `rg --files docs\references` 列出了 `docs\references\attachement\attachments.ts`。

- Observation: OneCode 已经有模型回复完成后的生命周期 hook 和 session memory extraction 代码，因此附件系统不应该再为 memory 或 hook 引入另一套 main-loop-specific 事件机制。
  Evidence: `services/hooks/events.py` 包含 `ASSISTANT_MESSAGE_COMPLETED`，`core/loop.py` 已经在追加 assistant message 后调用 `_after_assistant_message_completed()`。

- Observation: 当前 provider adapter 只理解内部 user、assistant 和 `tool_result` 消息。raw attachment role 不能直接发送给 OpenAI-compatible provider。
  Evidence: `infrastructure/providers/chat_completions.py::_project_messages()` 只把 `role="tool_result"` 映射成 wire `role="tool"`，其他 message dictionary 会原样透传。因此附件消息必须在 provider payload 构建前完成投影。

- Observation: 参考实现区分 user-input attachments、all-thread attachments 和 main-thread-only attachments。用户想保留这种高层划分，但明确 shared 集合不包含 todo reminders。
  Evidence: `docs/references/attachement/attachments.ts` 中 `getAttachments()` 先处理 user-input attachments，再处理 all-thread attachments，最后处理 only-main-thread attachments。用户确认 shared attachments 不需要 todo reminder。


## Decision Log

- Decision: 持久化结构化 attachment messages，但不持久化合成的 file-read assistant 工具调用或合成 tool results。
  Rationale: 用户明确说虚拟 file-read messages 只应临时生成。持久化 raw attachment messages 可以支持恢复和未来 UI 渲染，同时不会把“模型没有真实请求过的动作”污染进 transcript。
  Date/Author: 2026-06-07 / User and Codex

- Decision: `@filename` 只在当前工作目录内解析，任何指向目录外的引用都视为 unresolved 或 denied。
  Rationale: 用户说明用户输入只发生在 `cwd` 内，并且文件名解析应该搜索当前工作目录。这个选择让第一版简单，并与 OneCode 的 sandbox 模型一致。
  Date/Author: 2026-06-07 / User

- Decision: 第一版 at-mentions 支持文本文件、行范围和目录，不支持图片或 PDF。
  Rationale: 用户明确要求支持文件、行和目录，并说明不要支持图片/PDF。这避免了早期引入多模态 provider payload 和 PDF 提取复杂度。
  Date/Author: 2026-06-07 / User

- Decision: 文件附件必须通过工具使用的同一套 guard 和 permission 概念生成，即使用户输入预期位于 `cwd` 内。
  Rationale: 附件收集会在模型调用前读取本地文件。这个读取不能成为绕过 `SandboxGuard` 或 `PermissionPolicy` 的侧通道。实现可以调用共享读取 helper，或实现一个小型 attachment reader，按 `read_file` 相同方式分类 file target。
  Date/Author: 2026-06-07 / Codex

- Decision: Plan mode 只表示为预留 attachment 类型和 projection 接口；本计划不实现完整 plan-mode 行为。
  Rationale: 用户当前不想实现 plan mode 本身。预留接口可以减少未来 churn，同时让附件系统聚焦。
  Date/Author: 2026-06-07 / User

- Decision: 附件 UI 渲染明确暂缓，并在 backend 行为落地后作为技术债跟踪。
  Rationale: 用户原始阶段 3 说明 UI 渲染暂不实现，或者可以记录为技术债。backend 仍应保留足够附件 metadata，方便未来 UI 展示。
  Date/Author: 2026-06-07 / User and Codex


## Outcomes & Retrospective

本计划已创建，但实现尚未开始。尚未为附件系统新增或运行测试。当前预期结果保持不变：OneCode 能从用户输入和运行时状态收集结构化附件，将其保存为会话消息，并在不直接改变 provider wire protocol 的前提下投影进模型可见上下文。


## Context and Orientation

OneCode 是 Python code agent runtime。主循环位于 `core/loop.py`。它通过 `AgentLoop.stream(prompt)` 接收用户输入，把 user message 追加到 `services/context/message_store.py::MessageStore`，通过 `core/context_engine.py::ContextEngine` 构建模型上下文，从 provider-neutral model client 流式读取回复，写入 assistant messages，通过 `services/tools/executor.py` 执行工具调用，并把工具结果写回 message store。

附件系统属于用户输入、消息存储和模型上下文投影之间的层。附件是与某个 turn 关联的结构化上下文，例如用户提及的文件、目录列表、之前已读文件被修改的通知、排队命令、memory 内容、hook 结果，或未来 plan-mode 指令。Attachment message 是把一个 attachment 存入 transcript 的 durable internal message。Projection 是 internal message 的临时模型可见表示。例如，`@architecture.md` 对应的 durable attachment message 可以投影成一个包含 `read_file` 工具调用的合成 assistant message，再跟一个包含文件内容的合成 `tool_result` message。Synthetic 表示这是 OneCode 在 context build 阶段生成的内容，不应被写成“模型真实调用了工具”。

当前 message store 位于 `services/context/message_store.py`，支持 `append_user()`、`append_assistant()` 和 `append_tool_results()`。它可以保存任意 message dictionary，并通过 `services/context/transcript.py::JsonlTranscriptStore` 持久化，但当前文档化 role 只有 `user`、`assistant` 和 `tool_result`。`infrastructure/providers/chat_completions.py` 中的 provider adapter 只对内部 `tool_result` 做特殊投影，其他 role 会透传。由于 OpenAI-compatible chat completions 不接受 `role="attachment"`，attachment messages 必须在 provider payload 构建前被展开或移除。

模型上下文由 `core/context_engine.py::ContextEngine.build_for_model()` 构建。它读取 current messages，调用 context preparer，组装 system prompt，并返回 `services/context/snapshot.py::ContextSnapshot`。现有 compaction 逻辑在 `services/compaction/service.py` 中已经使用这个 preparer 边界替换或裁剪 messages。Attachment projector 应该放在同一个 context-preparation 层：可以作为 composite preparer 的一环，也可以接入 compaction-aware preparation。这样可以保持 `core/loop.py` 的主循环仍然很薄。

当前 CLI 输入在 `ui/cli/app.py::main_loop_async()` 中。它从 `input("onecode> ")` 读取一行，处理 slash commands，否则调用 `runtime.loop.stream(line)`。Attachment collector 需要 workspace path、guard、permission policy 和 message history 等 runtime services，因此 `ui/cli/types.py::CliRuntime` 是保存 attachment service 的自然位置；该 service 应在 `ui/cli/app.py::build_runtime()` 中创建。

文件工具已经存在于 `tools/read_file/tool.py`、`tools/edit_file/tool.py`、`tools/glob/tool.py` 和 `tools/grep/tool.py`。Attachment file reader 应复用这些工具的安全概念，而不是重复写 ad hoc path reads。`read_file` 工具会对带可选 `offset` 和 `limit` 的路径返回带行号文本。本计划中，附件文件内容也应使用相同的带行号文本格式，使合成工具结果看起来与真实 `read_file` 结果一致。

`docs/references/attachement/attachments.ts` 是背景参考材料，不是要逐字移植的源码。可参考的行为包括解析 `@file#L10-20`、处理带空格路径的 quoted mentions、目录列表、区分 user-input attachments / shared attachments / main-thread-only attachments、创建 `AttachmentMessage`，以及维护 file-state cache 以便外部修改过的文本文件生成带 diff snippet 的 `edited_text_file` attachment。


## Plan of Work

首先，在 `services/attachments/` 下创建小型附件领域模块。新增 `services/attachments/types.py`，定义 `Attachment`、`AttachmentMessage` 和具体 payload 形状。第一版只覆盖这些 attachment type：`file`、`directory`、`edited_text_file`、`plan_mode`、`queued_command`、`relevant_memories`、`nested_memory`、`todo_reminder` 和 `hook_result`。`todo_reminder` 作为未来或 main-thread 使用的类型存在，但本计划中 shared collector 不得发出它。包含 `id`、`created_at`、`scope` 和 `source` 等 metadata，其中 `scope` 为 `shared` 或 `main_thread`。本计划不添加 image、PDF 或 MCP resource attachment 行为。

然后，新增 `services/attachments/parser.py` 处理用户输入解析。它应暴露 `extract_at_mentions(text: str) -> tuple[AtMention, ...]` 和 `parse_line_fragment(raw: str) -> AtMention`。必须支持 `@file.py`、`@"path with spaces/file.py"`、`@src/app.py#L10` 和 `@src/app.py#L10-20`。必须剥离 `#heading` 这类非行号 fragment，而不是把它当成行范围。应在保留首次出现顺序的同时去重。Parser 不应读取文件系统。

接着，新增 `services/attachments/file_state.py`。定义 `FileStateCache`，记录 resolved file path、content、mtime、offset、limit 以及缓存内容是否为 partial view。这个 cache 与当前 `RuntimeState.metadata["files_read"]` 分离；后者目前只记录路径供 read-before-edit 规则使用。该 cache 用于检测 `edited_text_file`：每个新 turn 前，比较缓存文件 mtime 与当前 mtime；当文本文件变化时读取新内容并生成紧凑 unified diff snippet。第一版可使用 Python 标准库 `difflib.unified_diff`，并把 snippet 限制在固定大小，例如 4,000 字符。如果文件被删除，则驱逐缓存项，不发出 attachment。如果文件不可读或被权限拒绝，跳过 edited-file attachment 并记录 trace event。

新增 `services/attachments/resolver.py` 做 cwd-scoped resolution。它应暴露 `resolve_mention(mention: AtMention, workspace: Path) -> ResolvedMention | ResolutionError`。解析行为为：先把 mention 作为相对于 `workspace` 的精确路径尝试；如果存在，就使用它。如果不存在，则在 `workspace` 下搜索文件名匹配 mention 文本，或 normalized separators 后相对路径等于 mention 文本的路径。若刚好一个匹配，使用它。若多个匹配，返回 ambiguous resolution error，不要不可预测地选择一个。若没有匹配，返回 not found。绝不解析到 `workspace` 外。避免跟随 symlink 到 `workspace` 外；使用 `Path.resolve()`，并确认 resolved path 相对 resolved workspace。

新增 `services/attachments/collector.py`。定义 `AttachmentCollector`，提供类似 `async def collect_for_user_turn(self, prompt: str, state: RuntimeState, messages: tuple[dict[str, Any], ...], *, is_main_thread: bool = True) -> AttachmentCollection` 的方法。收集顺序应为：user-input attachments 第一，shared attachments 第二，main-thread-only attachments 最后。User-input attachments 解析 `@filename`，解析路径，读取文件或目录，更新 file-state cache，并返回 `file` 或 `directory` attachments。第一版 shared attachments 应包括 queued commands 和 hook results，但只有 runtime 已有对应队列时才接入；如果这些队列尚不存在，定义 provider-neutral interfaces，并让具体 adapter 为空。Main-thread-only attachments 应包括来自 file-state cache 的 edited text files。Plan-mode attachments 应有接口但没有 active emitter。Shared collector 不得发出 todo reminders。

collector 内部读取文件时，使用现有安全边界。清晰实现是创建小型 `AttachmentFileReader`，接收 `SandboxGuard`、`PermissionPolicy` 和可选 `PermissionPrompter`。它应把文件读取分类为 `ToolTarget(kind="file", operation="read", value=path)`，执行 guard 和 permission policy，然后用 UTF-8 且 `errors="replace"` 读取文本。目录 mention 也应对目录路径走 read permission，返回最多 1,000 个按名称排序的 entry names；如果更多则加 truncation note。该 reader 不应调用 provider，不应执行真实模型 tool call，也不应 append tool results。它只生成 attachment payloads。

修改 `services/context/message_store.py` 支持 durable attachment messages。新增 `append_attachment(attachment_message: dict[str, Any])` 或 `append_attachments(messages: Iterable[dict[str, Any]])`。存储形状应是普通 message dictionary，包含 `role="attachment"`、`attachment` payload，以及可选 `source_prompt_uuid` 等 metadata。保持 transcript append-only。如果 `services/context/transcript.py` 没有校验 role，就无需修改；否则补上对 internal attachment role 的支持。更新设计文档，把 `attachment` 记录为 internal role，并强调 provider adapter 不应看到 raw attachment messages。

谨慎修改 `core/loop.py`。主循环必须保持薄。推荐把 `AgentLoop.stream()` 改为接收可选的预计算 attachment messages，例如 `async def stream(self, prompt: str, attachments: Iterable[dict[str, Any]] | None = None)`。它应先 append user prompt，再 append attachment messages，然后进入 `_run_loop_async()`。不要把附件解析、文件读取或 type-specific projection 放入 loop。如果改签名影响过大，可引入 `stream_turn(UserTurn)`，其中 `UserTurn` 是包含 prompt 和 attachment messages 的小 dataclass，再让 `stream(prompt)` 以无 attachments 的形式调用它，以兼容测试和旧调用方。

在 `ui/cli/app.py` 接入 CLI。`build_runtime()` 中创建 `AttachmentCollector` 并保存到 `CliRuntime`。`main_loop_async()` 中，在读取非 slash input 后、调用 agent loop 前，用 prompt、current messages、state 和 `is_main_thread=True` 调用 collector。然后调用带 prompt 与 collected attachment messages 的 loop。如果单个 mention 收集失败，collector 应返回一个结构化 attachment 告诉模型文件无法解析或读取；除非是编程错误，否则不应让整个 turn 崩溃。CLI 当前仍可以不渲染附件 UI。

新增投影层 `services/attachments/projector.py`。定义 `AttachmentProjector.project(messages: tuple[dict[str, Any], ...], state: RuntimeState) -> tuple[dict[str, Any], ...]`。它应扫描内部 message sequence，把每个 `role="attachment"` message 替换为零条或多条 provider-visible messages。对 `file`，生成一个带 `read_file` tool call 的合成 assistant message，以及匹配的内部 `tool_result` message。使用稳定的 synthetic IDs，例如 `attachment_read_<attachment_id>`，确保一个 attachment 在一次 projection 中有一组稳定 call/result pair。对 `directory`，生成一个简洁 system-reminder 风格 user message，包含目录路径和列表。对 `edited_text_file`，生成 user message，说明文件被外部修改并包含 diff snippet。对 `queued_command`，生成带 origin marker 的 user message，例如 `[queued command from coordinator]` 后接排队内容。对 `relevant_memories` 和 `nested_memory`，生成包含路径和 memory content 的 user messages。对 `plan_mode`，只在未来有具体 emitter 时生成预留指令文本；第一版可以包含 projector case 和测试，但 collector 默认不输出该类型。

把 attachment projector 接入现有 context preparation 边界。最稳妥路径是新增 `services/context/preparers.py`，实现 `CompositeContextPreparer`，先运行 compaction，再运行 attachment projection；或者修改 `ContextCompactionService.prepare()`，在 compaction 正常 message preparation 后调用可选 attachment projector。关键不变量是 provider adapter 只能收到合法的 user、assistant 和 `tool_result` messages。保留现有 `ContextProjector.adjust_start_index_to_preserve_tool_pairs()` 行为，确保合成 file-read tool pair 不会被 sliding window 或 compaction 切开。增加测试证明 attachment projection 发生在 `infrastructure/providers/chat_completions.py` 构建 payload 之前。

只在必要时更新 provider 测试。Provider adapter 不应知道 attachment，因为 raw attachment messages 应该在 `ContextSnapshot` 到达 provider 前消失。增加 defensive test：通过真实 runtime 构建带 attachment 的 snapshot，结果不包含 `role="attachment"` messages。如果 raw attachment 进入 `_project_messages()`，应视为 context 层 bug，而不是 provider 职责。

更新文档和技术债。在 `docs/design-docs/context-and-prompt-architecture.md` 中说明 attachment messages 是 internal context role，并说明 projector。在 `docs/design-docs/core-runtime-architecture.md` 中说明 `AgentLoop` 可以接收预构建 user turn，但不负责收集附件。在 `docs/tech-debt/tech-debt-tracker.md` 中新增具体技术债，跟踪缺失的 CLI/UI attachment rendering，关联 `ui/cli/renderer.py` 和 `services/attachments/types.py`。新增技术债时遵守 `docs/tech-debt/tech_debt_tracker_guide.md`。不要在本计划中把 UI 渲染标记完成。


## Concrete Steps

从仓库根目录开始：

    cd D:\study\OneCode
    git status --short

预期工作区可能已有无关 modified files。不要 revert 它们。实现本计划时，只修改附件所需文件；如果相关文件已有其他未提交改动，要仔细合并而不是覆盖。

编辑前阅读最相关文件：

    Get-Content core\loop.py
    Get-Content core\context_engine.py
    Get-Content services\context\message_store.py
    Get-Content services\context\projector.py
    Get-Content services\compaction\service.py
    Get-Content infrastructure\providers\chat_completions.py
    Get-Content ui\cli\app.py
    Get-Content ui\cli\types.py
    Get-Content tools\read_file\tool.py
    Get-Content services\permissions\policy.py
    Get-Content services\guard\policy.py

先写 focused tests。新增 `tests/test_attachment_parser.py` 覆盖 mention parsing。运行：

    uv run python -m pytest tests\test_attachment_parser.py -q

实现前，预期会出现 import errors 或 assertion failures。实现 `services/attachments/parser.py` 后，预期 parser tests 通过。

新增 `tests/test_attachment_collector.py` 覆盖 cwd resolution、文件读取、目录列表、权限拒绝、ambiguous names 和 edited text file 检测。使用 pytest `tmp_path` 创建 workspace 和文件。运行：

    uv run python -m pytest tests\test_attachment_collector.py -q

新增 `tests/test_attachment_projector.py` 覆盖 durable attachment messages 到模型可见 messages 的投影。验证 `file` attachment 精确变为两条消息：一条带 `read_file` tool call 的 assistant message，以及一条匹配的 `tool_result`。验证原始 message store 仍包含 `role="attachment"` message，并且没有 append 合成 tool messages。

    uv run python -m pytest tests\test_attachment_projector.py -q

新增 integration test，可放在新的 `tests/test_attachment_runtime.py`，或扩展现有 runtime tests。测试通过带 attachment projector 的真实 `ContextEngine` 构建 context，并确认 `ContextSnapshot.messages` 不包含 raw attachment roles。也要用 fake transport 或检查 snapshot 的方式，证明 OpenAI-compatible provider payload builder 只会收到合法 roles。

    uv run python -m pytest tests\test_attachment_runtime.py tests\test_openai_compatible_provider.py -q

CLI 接入后，新增或扩展 CLI tests，证明含 `@note.txt` 的 prompt 会 append user message 和 attachment message，然后模型收到 projected file content。使用 fake model client，不要调用真实 provider。

    uv run python -m pytest tests\test_cli_commands.py tests\test_runtime_integration.py -q

最后运行编译和全量测试：

    uv run python -m compileall core services infrastructure tools ui
    uv run python -m pytest tests -q

实际 passed 数会受并行仓库改动影响。实现完成时，把最终命令输出摘要记录到本计划的 `Outcomes & Retrospective`。


## Validation and Acceptance

当以下行为能通过测试观察，并在可能时通过手动 CLI 场景观察，本功能即验收通过。

第一，解析正确。给定文本 `read @architecture.md#L1-5 and @"docs/design-docs/core beliefs.md"#L10`，`extract_at_mentions()` 返回两个 mention，包含路径和行范围。`@README.md#intro` 这类非行号 fragment 会解析为 `README.md` 且不带行范围。

第二，cwd-scoped resolution 正确。在临时 workspace 中创建 `src/example.py`，prompt `inspect @example.py` 会通过 workspace 搜索解析到该文件。如果不同目录下存在两个 `example.py`，用户只写 `@example.py` 时，collector 生成 ambiguity attachment，而不是任选一个。如果路径解析到 workspace 外，不读取文件。

第三，文件和目录附件正确。给定包含三行的 workspace 文件 `note.txt` 和 prompt `summarize @note.txt#L2-3`，collector 返回 `file` attachment，其内容只包含第 2 和第 3 行，并使用与 `read_file` 相同的带行号格式。给定目录 `src`，prompt `list @src` 返回 `directory` attachment，包含排序后的 entries，并在超过 1,000 项时包含 truncation note。

第四，attachment messages 是 durable 的，但 synthetic tool messages 是临时的。一次带 `@note.txt` 的 CLI turn 后，`MessageStore.current_messages()` 包含 user message 和 `role="attachment"` message。它不包含持久化的 assistant `read_file` tool call，也不包含持久化的 synthetic `tool_result`。当 `ContextEngine.build_for_model()` 运行时，`ContextSnapshot.messages` 包含临时 assistant tool call 和匹配的 `tool_result`，且不包含 raw `role="attachment"` messages。

第五，edited text file 检测正确。如果某文件之前被 attachment 读取并缓存，然后下一轮前磁盘文件被修改，collector 会发出带 diff snippet 的 `edited_text_file` attachment。Snippet 必须有大小上限，避免大改动淹没上下文。如果文件被删除，不发出 edited-file attachment，并移除缓存项。

第六，shared 和 main-thread scopes 被遵守。Shared attachment collection 不包含 todo reminders。Plan-mode attachment interfaces 存在，但默认不发出具体 plan-mode reminder。测试应断言这些默认行为，避免后续贡献者意外发布未完成的 plan-mode 行为。

手动验证可在 `.env` 配好本地 provider 后进行，但测试是主要验收路径。从 `D:\study\OneCode` 启动 CLI，创建一个小文本文件，然后输入：

    onecode> summarize @architecture.md#L1-5

启用 trace 或 fake provider 时，应能观察到模型可见 context 包含 `architecture.md` 第 1 到第 5 行对应的 synthetic `read_file` result。CLI 当前不需要渲染可见 attachment card；该缺口通过技术债跟踪。


## Idempotence and Recovery

所有实现步骤都是可重复、可加性的。对同一个 prompt 重复运行 collector 应为新的 turn 生成新的 attachment messages，但每个 attachment ID 的 projection 应是确定性的。重复运行 context projection 不应向 store 追加 messages，也不应修改 transcript 文件。

如果 attachment collection 部分失败，用户 turn 仍应继续。缺失、ambiguous、denied 或 unreadable 文件应变成模型可见的结构化 attachment notice，或者在可能暴露敏感路径细节时变成 trace event 加 skipped attachment。除非 collector 本身存在编程错误，否则不应从 CLI loop 抛出。

如果 projection 遇到未知 attachment type，优先生成保守 user-message notice，说明该 attachment type 不受支持，并记录 trace event。不要把 raw `role="attachment"` message 传给 provider。如果 provider 因 attachment role 拒绝 payload，应把它视为 context projection bug，并添加 regression test。

File-state cache 是内存中的 session state。如果需要，未来可以从 attachment messages 机会性重建；但第一版不要求 `/resume` 后跨进程检测 edited-file。Resume 时，如果既有 attachment messages 仍在 active message chain 中，它们应继续能投影进模型可见上下文；进程重启后的 edited-file 检测从重启后新 attach 的文件继续。

不要用 destructive git commands 从错误中恢复。如果需要回退某个改动，用 targeted patch 只删除本计划引入的文件或行。当前仓库可能存在无关未提交 work；不要 revert 无关文件。


## Artifacts and Notes

`docs/references/attachement/attachments.ts` 中最有用的参考行为是：

    getAttachments(input, toolUseContext, ideSelection, queuedCommands, messages, querySource)
      先处理 user-input attachments，再处理 all-thread attachments，最后处理 main-thread-only attachments。

    extractAtMentionedFiles(content)
      支持 @file 和 @"file with spaces" 形式。

    parseAtMentionedFileLines(mention)
      支持 #L10 和 #L10-20 行号 fragment。

    processAtMentionedFiles(input, toolUseContext)
      解析 mentions，检测目录，读取文件，并返回结构化 attachments。

    createAttachmentMessage(attachment)
      把 attachment 包装成带 type "attachment"、uuid 和 timestamp 的 durable message。

不要直接复制 TypeScript 文件。它包含 OneCode 当前还没有的产品特性，包括 image/PDF 处理、MCP resources、agent swarms、skill discovery 和 auto-mode reminders。本计划只实现一个更小的 Python 版本，并与当前 OneCode 架构匹配。

File attachment 的 synthetic projection 形状应类似以下内部 Python dictionary：

    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "attachment_read_<attachment_id>",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": "{\"file_path\": \"D:\\\\study\\\\OneCode\\\\architecture.md\", \"offset\": 1, \"limit\": 5}",
                },
            }
        ],
        "metadata": {"synthetic": True, "source": "attachment"},
    }

    {
        "role": "tool_result",
        "tool_call_id": "attachment_read_<attachment_id>",
        "tool_name": "read_file",
        "content": "1\t# OneCode 架构\n2\t...",
        "is_error": False,
        "metadata": {"synthetic": True, "source": "attachment"},
    }

精确内容可以不同，但 assistant tool call ID 与 tool result ID 必须匹配，provider adapter 必须把内部 `tool_result` 投影成合法 wire format。


## Interfaces and Dependencies

不要增加第三方依赖。使用 Python 标准库，例如 `dataclasses`、`pathlib`、`uuid`、`datetime` 和 `difflib`。

创建 `services/attachments/types.py`，提供稳定 public types。具体实现可以用 dataclasses 或 typed dictionaries，但模块必须暴露等价概念：

    class AttachmentScope(StrEnum):
        SHARED = "shared"
        MAIN_THREAD = "main_thread"

    @dataclass(frozen=True)
    class AttachmentMessage:
        id: str
        type: str
        attachment: dict[str, Any]
        created_at: str
        scope: AttachmentScope
        source: str

    def to_message(self) -> dict[str, Any]:
        ...

内部 message shape 应为：

    {
        "role": "attachment",
        "content": "",
        "attachment": {...},
        "metadata": {
            "attachment_id": "...",
            "attachment_type": "file",
            "scope": "main_thread",
            "source": "user_input",
        },
    }

创建 `services/attachments/parser.py`：

    @dataclass(frozen=True)
    class AtMention:
        raw: str
        path_text: str
        line_start: int | None = None
        line_end: int | None = None

    def extract_at_mentions(text: str) -> tuple[AtMention, ...]:
        ...

创建 `services/attachments/file_state.py`：

    @dataclass
    class FileState:
        path: Path
        content: str
        mtime_ns: int
        offset: int | None = None
        limit: int | None = None
        partial: bool = False

    class FileStateCache:
        def get(self, path: Path) -> FileState | None: ...
        def set(self, state: FileState) -> None: ...
        def remove(self, path: Path) -> None: ...
        def changed_text_attachments(self, reader: AttachmentFileReader) -> tuple[dict[str, Any], ...]: ...

创建 `services/attachments/resolver.py`：

    @dataclass(frozen=True)
    class ResolvedMention:
        mention: AtMention
        path: Path
        is_directory: bool

    def resolve_mention(mention: AtMention, workspace: Path) -> ResolvedMention | ResolutionError:
        ...

创建 `services/attachments/collector.py`：

    class AttachmentCollector:
        async def collect_for_user_turn(
            self,
            prompt: str,
            state: RuntimeState,
            messages: tuple[dict[str, Any], ...],
            *,
            is_main_thread: bool = True,
        ) -> tuple[dict[str, Any], ...]:
            ...

创建 `services/attachments/projector.py`：

    class AttachmentProjector:
        def project(
            self,
            messages: tuple[dict[str, Any], ...],
            state: RuntimeState,
        ) -> tuple[dict[str, Any], ...]:
            ...

更新 `services/context/message_store.py`：

    def append_attachments(
        self,
        attachments: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        ...

更新 `core/loop.py`，加入薄 API 扩展：

    async def stream(
        self,
        prompt: str,
        *,
        attachments: Iterable[dict[str, Any]] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        ...

实现应 append prompt；如果存在 attachments，则 append attachments；发出相同 interaction event；然后进入 `_run_loop_async()`。

更新 `ui/cli/types.py::CliRuntime`，增加：

    attachment_collector: AttachmentCollector | None = None

更新 `ui/cli/app.py::build_runtime()`，创建 collector 并传入 `CliRuntime`。更新 `main_loop_async()`，在调用 `runtime.loop.stream(line, attachments=attachments)` 前收集 attachments。

更新 context preparation，确保 attachment projection 总是在 provider 调用前发生。最终设计可以是 composite preparer，也可以是 `ContextCompactionService` 中的 optional projector，但必须能脱离 CLI 测试。


## Revision Notes

2026-06-07 / Codex: 初始计划根据用户需求和仓库调研创建。计划记录已确认范围：cwd-scoped `@filename` 解析；只支持文本文件、行范围和目录；临时 synthetic file-read projection；shared attachments 不包含 todo reminder；实现 edited text file cache 和 diff detection；仅预留 plan-mode interfaces；UI 渲染作为技术债延后。

2026-06-07 / Codex: 将计划正文翻译为中文，同时保留 `PLANS.md` 要求长期维护和识别的英文 section 标题。原因是用户要求把计划翻译为中文。
