# Resume 会话恢复优化计划

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本计划遵守仓库根目录的 `PLANS.md`。执行者只需要当前工作树和本文档，就应能完成恢复功能优化。本文中的“transcript”指保存在 `.onecode/<session_id>/messages.jsonl` 的会话消息日志；“trace”指 `.onecode/<session_id>/trace.jsonl` 中的运行时观测事件；“active chain”指恢复后下一轮模型应使用的当前有效消息链，它可以短于屏幕上展示的完整历史。

## Purpose / Big Picture

用户执行 `/resume` 后，应看到旧会话像普通对话一样出现在 CLI 历史中，而不是看到一个额外的 `Session History` 表格页。继续输入时，模型应使用恢复后的有效上下文：如果会话被压缩过，模型只看到压缩后的有效链和后续消息，而不是压缩前完整历史与压缩摘要的重复混合。恢复还应重建对继续工作必要的会话状态，例如已读文件、已修改文件、结果存储目录和 session memory 目录。

实现完成后，用户可以在 `D:\study\OneCode` 运行 CLI，创建一个会话，执行 `/resume <session-id>`，然后直接看到正常消息历史并继续输入。开发者可以运行聚焦测试，证明恢复不会把 append-only transcript 的旧分支、压缩前历史或非法 tool call 序列送入后续模型上下文。

本项目只恢复当前工作目录下 `.onecode` 内的会话，不实现跨工作目录恢复。`/resume` 参数可以按 session id、`messages.jsonl` 路径或标题搜索当前工作目录内的会话。

## Progress

- [x] (2026-06-12) 阅读 `PLANS.md`，确认 ExecPlan 必须自包含并包含 Progress、Surprises & Discoveries、Decision Log、Outcomes & Retrospective。
- [x] (2026-06-12) 对照当前 OneCode 恢复实现和参考 `docs/references/ui/commands/resume`、`docs/references/ui/screens/REPL.tsx`、`docs/references/ui/utils/sessionStorage.ts`、`docs/references/ui/utils/conversationRecovery.ts`、`docs/references/ui/utils/sessionRestore.ts`，明确恢复优化范围。
- [x] (2026-06-12) 实现 transcript active chain 恢复，不再把 append-only `messages.jsonl` 全量塞回 `MessageStore`。
- [x] (2026-06-12) 实现恢复清理层，保证恢复后的消息链对 provider 和后续上下文构建合法。
- [x] (2026-06-12) 删除额外 `Session History` 表格展示路径，改为恢复后按普通消息历史展示。
- [x] (2026-06-12) 恢复必要会话态，尤其是 `files_read`、`files_changed`、`FileStateCache`、session scoped result/session-memory storage。
- [x] (2026-06-12) 实现 `/resume` 标题搜索和 `/continue` alias。
- [x] (2026-06-12) 更新测试，删除不再适用的旧表格断言，补充恢复链、清理、展示、状态和标题搜索用例。
- [x] (2026-06-12) 运行聚焦测试和必要结构测试，记录结果。

## Surprises & Discoveries

- Observation: 当前 OneCode 的 `MessageStore.replace_messages_for_compaction()` 会把内存链替换为压缩后的消息，但 `MessageStore.from_transcript()` 恢复时直接读取 `JsonlTranscriptStore.load_messages()` 的全部记录。这样 append-only transcript 和 active chain 的语义混在一起。
  Evidence: `services/context/message_store.py` 中 `replace_messages_for_compaction()` 清空 `_messages` 后追加 replacement；同文件 `from_transcript()` 将 `loaded` 全部赋给 `_messages`。

- Observation: 参考实现恢复时不会按 JSONL 文件顺序恢复全部记录，而是读取 transcript 后找 leaf，再沿 `parentUuid` 构建 conversation chain。
  Evidence: `docs/references/ui/utils/sessionStorage.ts` 中 `loadTranscriptFile()` 返回 `leafUuids`，`loadFullLog()` 和 `getLastSessionLog()` 使用 `buildConversationChain()`，再调用 `removeExtraFields()`。

- Observation: 参考实现把“恢复后显示历史”和“下一轮模型上下文”分开处理。REPL 调用 `setMessages(() => messages)` 让历史进入正常消息流；API 调用前仍会经过上下文治理、压缩和投影。
  Evidence: `docs/references/ui/screens/REPL.tsx` 中 `deserializeMessages(log.messages)` 后 `setMessages(() => messages)`；普通渲染路径使用 `<Messages messages={displayedMessages} ... />`。

- Observation: 现有 `read_file` 工具结果 metadata 记录了 `path` 和 `offset`，但不记录原始 `limit` 或“是否完整读取”的结构化事实。
  Evidence: `tools/read_file/tool.py` 返回 metadata `{"path": str(path), "offset": offset, "line_count": len(selected)}`。因此恢复 `FileStateCache` 时把 `read_file` cache entry 标记为 partial，避免恢复后把分页读取误当作完整文件快照。

## Decision Log

- Decision: 不把恢复状态写入 `services/observability` 的 trace 或 error log。
  Rationale: trace 是运行时观测事实，error log 是错误诊断；会话恢复需要的是可恢复消息链和会话态，属于 `services/context` 和 CLI 装配职责。把 active chain 信息放入 trace 会让恢复依赖诊断日志，破坏分层。
  Date/Author: 2026-06-12 / Codex

- Decision: 恢复后应展示历史，但展示历史不等于模型上下文必须包含全部历史。
  Rationale: 用户需要视觉上回到旧对话；模型需要的是安全、合法、预算内的 active chain。屏幕展示可包含完整恢复历史，模型调用仍应由 `ContextEngine`、compaction 和 projector 决定。
  Date/Author: 2026-06-12 / Codex

- Decision: 新实现中不再保留 `render_session_history()` 表格恢复路径，也不保留测试对该表格的断言。
  Rationale: 用户明确要求能重构就重构，不为了迁移式安全保留无用旧路径。恢复成功后的历史展示应进入普通消息渲染，旧表格路径会制造两套 UI 事实来源。
  Date/Author: 2026-06-12 / Codex

- Decision: 不实现跨工作目录恢复。
  Rationale: 本项目只读取当前工作目录的 `.onecode` 会话。参考实现中的 cross-project resume 检查不适用；保留该能力会扩大权限和路径语义。
  Date/Author: 2026-06-12 / Codex

- Decision: `/resume <text>` 支持按标题搜索当前工作目录内会话。
  Rationale: 参考命令的 argument hint 是 conversation id or search term。OneCode 可以先用已有 `SessionSummary.title` 做大小写不敏感包含匹配，不引入跨项目搜索或 agentic search。
  Date/Author: 2026-06-12 / Codex

## Outcomes & Retrospective

已实施。

完成的行为：

- 新增 `services/context/recovery.py`，从 append-only transcript 选择最新 leaf 的 parent chain，并清理空 assistant、孤立 tool result 和中断 tool call。
- `MessageStore.from_transcript()` 现在恢复 active chain；`replace_messages_for_compaction()` 会让 replacement 第一条 record 以 `parent_uuid=None` 成为新 active chain 起点。
- `/resume` 成功后返回 inline 普通消息历史，不再使用 `Session History` 表格；无参数 selector 仍保留。
- `/resume <text>` 支持当前 workspace `.onecode` 内标题搜索；`/continue` 是 `/resume` alias。
- 恢复时从成功 file tool result metadata 重建 `files_read`、`files_changed` 和 `FileStateCache`；`read_file` 恢复为 partial cache，`edit_file`/`write_file` 恢复为当前磁盘快照。

验证结果：

- `uv run python -m pytest tests/test_context_recovery.py tests/test_jsonl_session_persistence.py -q` -> 11 passed。
- `uv run python -m pytest tests/test_cli_resume.py tests/test_cli_prompt_input_suggestions.py -q` -> 13 passed。
- `uv run python -m pytest tests/test_cli_resume.py tests/test_jsonl_session_persistence.py tests/test_context_recovery.py tests/test_context_engine.py tests/test_loop.py -q` -> 36 passed。
- `uv run python -m pytest tests/test_import_boundaries.py -q` -> 2 passed。
- `uv run python -m compileall services ui tests` -> passed。

残余风险：

- 旧 transcript 如果已经在 compaction replacement 上写入了错误的 parent_uuid，新恢复逻辑只做 best-effort 清理，不迁移历史文件。
- `read_file` metadata 不足以证明完整读取，所以恢复后的 write overwrite 安全 cache 保守处理为 partial。

## Context and Orientation

OneCode 的 CLI 从 `ui/cli/app.py` 启动。`build_runtime()` 创建 `RuntimeState`、`MessageStore`、`TraceRecorder`、`ErrorLogRecorder`、工具 registry、context engine 和 `AgentLoop`。普通用户输入在 `main_loop_async()` 中调用 `runtime.loop.stream(line, attachments=attachments)`，而 slash command 由 `ui/cli/commands.py` 的 `dispatch_command()` 分发。

会话消息由 `services/context/message_store.py` 管理。`MessageStore` 是内存优先的消息链，每次追加 user、assistant、tool_result 或 attachment 时，同时通过 `services/context/transcript.py` 的 `JsonlTranscriptStore` 写入 `.onecode/<session_id>/messages.jsonl`。该 JSONL 是 append-only 日志：压缩、恢复和后续追加不会删除旧记录。

运行诊断不在 transcript 中。`services/observability/sinks.py` 写 `.onecode/<session_id>/trace.jsonl`，记录 span 和 event。`services/observability/error_log.py` 写 `.onecode/<session_id>/errors.jsonl`，记录脱敏错误。恢复逻辑不得依赖这两个文件，也不得把 active chain 信息写到 trace 里。

当前恢复入口在 `ui/cli/resume.py`。`restore_runtime_from_target()` 解析 session id 或 JSONL 路径，调用 `MessageStore.from_transcript()` 载入消息，然后调用 `CliRuntime.with_session()` 重建 session scoped 组件。当前问题是 `MessageStore.from_transcript()` 会把 transcript 中所有 loadable message 直接恢复为内存链。

当前恢复 UI 在 `ui/cli/commands.py::_resume()` 和 `ui/cli/app.py::_resume_history_result()`。它们恢复 runtime 后调用 `renderer.render_resume()` 加 `renderer.render_session_history()`，并返回 `presentation="page"`。`renderer.render_session_history()` 最终来自 `ui/cli/views/resume.py`，它用表格显示 role/detail。这条路径应删除或收缩到不再被恢复命令使用。

参考实现位于 `docs/references/ui`。关键设计点如下：`utils/sessionStorage.ts` 的 `loadTranscriptFile()` 读取 JSONL 里的 transcript messages 和 session metadata；`buildConversationChain()` 从 leaf 沿 parentUuid 恢复有效链；`utils/conversationRecovery.ts` 的 `deserializeMessages()` 清理 unresolved tool uses 等非法序列；`screens/REPL.tsx` 的 resume callback 把恢复后的 messages 放入正常 REPL message state，而不是展示一个独立 history table；`utils/sessionRestore.ts` 恢复 file history、agent setting、worktree 等会话态。OneCode 不需要复制 React/Ink 组件，也不需要跨目录、worktree、agent setting 等超出当前项目目标的能力。

## Plan of Work

第一步，重构 transcript 恢复为“从日志恢复 active chain”。在 `services/context/transcript.py` 中保留 `load_messages()` 作为读取所有 loadable records 的底层能力，但新增一个面向恢复的结果结构，包含按 uuid 索引的消息、parent_uuid、session_id 和 timestamp。实现一个新函数或新模块，例如 `services/context/recovery.py`，负责从这些 records 中选择 active chain。active chain 选择规则应优先沿 parent_uuid 从最新 leaf 回溯。leaf 是没有其他 loadable message 把它作为 parent_uuid 的消息；如果存在多个 leaf，选择 timestamp 最新的非 attachment-only leaf；如果 timestamp 缺失，选择文件顺序最后的 leaf。回溯时遇到缺失 parent_uuid 就停止，返回从 root 到 leaf 的链。

第二步，修正 compaction replacement 的 parent chain。当前 `replace_messages_for_compaction()` flush 后清空内存，但 `_last_uuid` 仍然指向压缩前最后一条 record，导致 replacement 第一条消息继续挂在旧链尾。应让 compaction replacement 成为新的 active chain 起点：在清空 `_messages` 的同时把 `_last_uuid` 置为 `None`，然后追加 replacement。这样以后按 leaf 回溯时不会走回压缩前历史。旧 transcript 如果已经存在压缩 replacement 但 parent_uuid 仍连着旧链，本计划不保留复杂迁移兼容；测试只保证新写入的 transcript 正确。对旧文件，恢复清理层仍应尽量避免非法 tool pairing，但不承诺修复历史 parent_uuid 设计错误。

第三步，新增恢复清理层。创建 `services/context/recovery.py`，定义 `restore_active_messages(transcript_store) -> RestoredTranscript` 或等价接口。`RestoredTranscript` 至少包含 `session_id`、`messages`、`warnings`。清理规则包括：丢弃没有匹配 assistant tool_call 的孤立 `tool_result`；如果 assistant tool_call 没有对应 tool_result 且位于链尾或接近链尾，追加 synthetic error `tool_result`，内容说明工具调用在恢复前中断，避免 provider 因 tool call/result 不配对而报错；去掉空白 assistant 消息；保留 user、assistant、attachment 和合法 tool_result 的相对顺序。清理层应是 provider-neutral 的，不引用 OpenAI 或具体 provider adapter。

第四步，重构 `MessageStore.from_transcript()`。不要让它自己决定恢复策略。保留一个从明确 messages seed 的构造路径，例如新增 `MessageStore.from_restored_messages(transcript_store, state, messages, last_uuid=None)`，或让 `restore_runtime_from_target()` 创建空 `MessageStore` 后直接设置受控内部链。更好的方案是让 `MessageStore.from_transcript()` 改为调用新恢复服务并只恢复 active messages；如果没有其他调用方需要 full transcript 语义，就直接替换旧行为并更新测试。不要保留无人使用的 full-load 恢复分支。

第五步，重做恢复后的展示。删除 `ui/cli/views/resume.py::render_session_history()`，删除 `ui/cli/renderer.py` 中对应 re-export，删除 `ui/cli/commands.py::_resume()` 和 `ui/cli/app.py::_resume_history_result()` 对该函数的调用。新增一个普通消息历史渲染函数，位置可以是 `ui/cli/renderer.py` 或 `ui/cli/views/messages.py`。该函数应逐条复用当前运行时的常规表现：user 消息像用户输入，assistant 消息显示文本，assistant tool_call 显示工具调用摘要，tool_result 显示和 `render_tool_result_summary()` 类似的摘要，attachment 显示附件摘要。恢复成功后返回 inline renderable 或让主循环打印恢复后的消息历史，而不是 `presentation="page"`。

第六步，分离“展示历史”和“模型上下文”。`MessageStore.current_messages()` 应返回 active chain，因为这是 `ContextEngine` 的输入。恢复后屏幕展示可以用同一 active chain；如果以后要显示 pre-compact 完整历史，必须从 transcript viewer 读取 full transcript，而不能把 full transcript 放进 `MessageStore`。本计划不实现完整 transcript viewer，只确保恢复后的普通显示不是表格，并且模型上下文不会包含 append-only 全量记录。

第七步，恢复必要会话态。扩展 `CliRuntime.with_session()`，允许传入可选 `file_state_cache` 和恢复 metadata。新增一个恢复状态提取函数，扫描恢复后的 active messages，填充 `state.metadata["files_read"]` 和 `state.metadata["files_changed"]`，并重建 `FileStateCache`。具体提取策略从现有工具 metadata 开始，不解析任意 stdout 文本；如果 tool_result metadata 中已有路径事实则使用；否则只在 `read_file`、`edit_file`、`write_file` 的结构化 metadata 缺失时做保守空恢复。`ToolResultStorage`、`SessionMemoryStore` 已按 restored session dir 重新绑定，应保留。`SessionPermissionStore` 继续清空，不从旧会话继承临时授权。

第八步，实现当前工作目录内标题搜索。更新 `ui/cli/commands.py::_resume()`：当参数不是存在的 JSONL 路径，也不是 `.onecode/<session_id>/messages.jsonl` 对应的 session id 时，调用 `list_session_summaries(runtime.workspace)`，对 `SessionSummary.title` 做大小写不敏感包含匹配。零个匹配时返回明确错误；一个匹配时直接恢复；多个匹配时打开 selector 或返回匹配列表并提示用户输入更精确的标题。为了保持交互完整，优先实现多个匹配时复用现有 selector 交互；如果当前 command result 无法携带过滤后的 selector 数据，则新增 `CommandInteraction` 类型，例如 `resume_selector_filtered`，或让 `_resume` 返回 page 列表作为第一步。增加 `CommandSpec("resume", ..., aliases=("continue",))`，使 `/continue` 等价于 `/resume`。

第九步，删除旧测试预期并添加新测试。更新 `tests/test_cli_resume.py`，删除对 `Session History`、`Tool` 表格和折叠表格文本的断言，改为断言恢复结果不进入 page mode，并且普通历史渲染包含 user/assistant/tool 摘要。新增测试覆盖标题搜索、多个标题匹配、`/continue` alias。更新 `tests/test_jsonl_session_persistence.py`，新增 compaction 后恢复只返回新 active chain 的用例，并验证 replacement 第一条 record 的 `parent_uuid` 为 `None`。新增 `tests/test_context_recovery.py` 或同类文件，覆盖孤立 tool_result 被丢弃、缺失 tool_result 被 synthetic error 补齐、空白 assistant 被过滤。

## Concrete Steps

在仓库根目录 `D:\study\OneCode` 执行以下步骤。

首先阅读相关文件，确认当前代码位置：

    Get-Content services\context\message_store.py
    Get-Content services\context\transcript.py
    Get-Content ui\cli\resume.py
    Get-Content ui\cli\commands.py
    Get-Content ui\cli\app.py
    Get-Content ui\cli\views\resume.py

实现恢复链模块和测试。建议先创建 `services/context/recovery.py`，再创建 `tests/test_context_recovery.py`。测试应先失败，证明当前恢复不能处理 active chain 或非法 tool pairing。然后修改 `services/context/message_store.py` 和 `services/context/transcript.py` 使测试通过。

完成 context 层后运行：

    uv run python -m pytest tests/test_context_recovery.py tests/test_jsonl_session_persistence.py -q

期望看到新增测试和既有 transcript 测试全部通过。输出形态类似：

    18 passed in 1.5s

然后重构 CLI 恢复展示和会话态恢复。修改 `ui/cli/resume.py` 让 `restore_runtime_from_target()` 调用新恢复服务，并把恢复状态传给 `runtime.with_session()`。修改 `ui/cli/types.py` 使 `with_session()` 可以接收恢复出的 `FileStateCache` 和 metadata，或在 `restore_runtime_from_target()` 调用后立即更新 `state.metadata` 和 executor 的 file state cache。删除不再使用的 `render_session_history()` 和相关 import。不要留下未被调用的兼容函数。

完成 CLI 层后运行：

    uv run python -m pytest tests/test_cli_resume.py tests/test_cli_prompt_input_suggestions.py -q

最后运行更广的相关测试：

    uv run python -m pytest tests/test_cli_resume.py tests/test_jsonl_session_persistence.py tests/test_context_recovery.py tests/test_context_engine.py tests/test_loop.py -q

如果修改了 import 边界或新增 context module，再运行：

    uv run python -m pytest tests/test_import_boundaries.py -q

## Validation and Acceptance

行为验收一：恢复压缩会话时，模型上下文只包含 active chain。测试应构造一个会话，先追加 old user/assistant，再调用 `replace_messages_for_compaction()` 写入 `[Compact boundary]` 和 summary，再追加后续 user。恢复后 `message_store.current_messages()` 不应包含 old user/assistant。新测试在修改前应失败，因为当前 `from_transcript()` 会恢复全部记录；修改后应通过。

行为验收二：恢复非法或中断 transcript 时，后续 context 构建不会产生孤立 tool_result 或缺失 tool_result。测试应构造 assistant tool_call 缺少 result 的 transcript，恢复后应看到 synthetic error tool_result；构造孤立 tool_result，恢复后应被丢弃。运行 `uv run python -m pytest tests/test_context_recovery.py -q` 应通过。

行为验收三：恢复后展示是普通历史，不是 `Session History` 表格。`dispatch_command(runtime, "/resume <session>")` 的结果不应是 `presentation="page"`，渲染文本不应包含 `Session History` 标题；应包含恢复成功提示和普通历史内容。旧测试中对 `read_file call_read ok` 可以保留为普通工具摘要断言，但不能依赖表格 role/detail 列。

行为验收四：恢复后必要会话态被重建。测试应恢复包含 read/edit/write 工具结果 metadata 的会话，并断言 `state.metadata["files_read"]`、`state.metadata["files_changed"]` 或 executor 绑定的 `FileStateCache` 中存在对应路径。若现有工具结果 metadata 不足，应先补齐工具 metadata，再基于 metadata 恢复，不从任意工具 stdout 猜测。

行为验收五：标题搜索可用。创建两个 session summary，执行 `/resume restore this` 能匹配标题包含该文本的会话。若多个标题匹配，应展示可选择的候选或返回明确的 multiple matches 提示。`/continue <target>` 与 `/resume <target>` 行为一致。

## Idempotence and Recovery

所有修改应限制在仓库内。不要删除 `.onecode` 里的用户真实会话文件；测试必须使用 `tmp_path`。不要添加迁移脚本修改已有用户 transcript。新恢复逻辑应对旧 transcript 做 best-effort 读取，但不为旧 bug 保留新代码不会使用的并行路径。

如果实现中发现某个旧函数只被旧恢复表格使用，应删除该函数、import 和测试断言。删除前用 `rg` 确认没有其他调用方。删除后运行相关测试，保证没有死 import。

如果新恢复服务引入的 active chain 选择规则与某些旧测试冲突，优先更新测试到新语义：`messages.jsonl` 是 append-only 事实来源，`MessageStore.current_messages()` 是 active chain。不要为了旧测试保留全量恢复行为。

## Artifacts and Notes

当前代码中需要重点删除或替换的旧路径：

    ui/cli/views/resume.py::render_session_history
    ui/cli/renderer.py import/export render_session_history
    ui/cli/commands.py::_resume 中 renderer.render_session_history(...)
    ui/cli/app.py::_resume_history_result 中 renderer.render_session_history(...)
    tests/test_cli_resume.py 中 "Session History" 表格相关断言

参考实现中最重要的设计证据：

    docs/references/ui/utils/sessionStorage.ts:
      loadTranscriptFile() 读取 transcript messages、metadata 和 leafUuids。
      buildConversationChain() 从 leaf 沿 parentUuid 恢复有效链。
      removeExtraFields() 去掉存储字段后交给上层。

    docs/references/ui/utils/conversationRecovery.ts:
      deserializeMessages() 清理 unresolved tool uses、orphaned thinking 和空白 assistant。

    docs/references/ui/screens/REPL.tsx:
      resume callback 中 setMessages(() => messages)，让恢复历史进入正常消息流。

## Interfaces and Dependencies

新增或最终应存在的接口如下。具体命名可微调，但职责必须保持。

在 `services/context/recovery.py` 定义：

    @dataclass(frozen=True)
    class RestoredTranscript:
        session_id: str
        messages: tuple[dict[str, Any], ...]
        last_uuid: str | None
        warnings: tuple[str, ...] = ()

    def restore_transcript_active_chain(
        transcript_store: JsonlTranscriptStore,
    ) -> RestoredTranscript:
        ...

该函数读取 transcript store，选择最新 active chain，清理消息合法性，并返回恢复结果。它不得引用 CLI、provider、tools 具体实现或 observability。

在 `services/context/message_store.py` 调整恢复接口：

    @classmethod
    def from_transcript(
        cls,
        transcript_store: JsonlTranscriptStore,
        state: RuntimeState,
    ) -> "MessageStore":
        ...

该接口应使用 `restore_transcript_active_chain()`，不再 full-load append-only transcript。若为了测试需要 full-load，使用 transcript store 的底层读取函数，而不是保留第二套 runtime 恢复路径。

在 `ui/cli/resume.py` 调整：

    def restore_runtime_from_target(runtime: CliRuntime, target: str) -> CliRuntime:
        ...

该函数解析当前 workspace 内目标，恢复 active messages 和会话态，flush 当前 transcript，构造恢复后的 runtime。它不得允许读取当前 workspace `.onecode` 之外的会话，除非目标路径显式位于 workspace 内。

在 `ui/cli/types.py` 调整 `CliRuntime.with_session()`，允许注入恢复出的 file state：

    def with_session(
        self,
        *,
        state: RuntimeState,
        message_store: MessageStore,
        file_state_cache: FileStateCache | None = None,
    ) -> "CliRuntime":
        ...

如果传入 `file_state_cache`，executor 和 attachment collector 都应绑定该实例。否则保持创建空 `FileStateCache()` 的现有行为。

在 `ui/cli/commands.py` 调整 command spec：

    CommandSpec(
        "resume",
        "Restore a previous session.",
        _resume,
        "[session-id-or-title-or-messages.jsonl]",
        aliases=("continue",),
        parameter_completer=_resume_candidates,
    )

`_resume_candidates()` 应继续列出 session id/path；如果成本低，也可以加入标题候选。标题搜索必须只搜索当前 workspace 的 `list_session_summaries(runtime.workspace)`。

## Revision Notes

2026-06-12 / Codex: 初版计划。根据用户要求，计划明确删除旧 `Session History` 表格路径，不实现跨工作目录恢复；恢复后需要展示普通历史，但模型上下文由 active chain 和 context pipeline 决定。计划吸收参考实现中 parent chain 恢复、deserialize 清理和 session state restore 的核心思想，并按 OneCode 当前模块边界裁剪。
