# 实现 Fork Agent 驱动的 Session Memory 提取与压缩消费

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本计划必须按照仓库根目录的 `PLANS.md` 维护。`PLANS.md` 要求 ExecPlan 自包含、可执行、面向不了解本仓库的读者，并且每个里程碑都能通过测试或人工操作观察到工作行为。本文件是中文计划，但保留 `PLANS.md` 规定的英文段落标题，方便后续 agent 识别必须持续维护的章节。


## Purpose / Big Picture

完成本计划后，OneCode 的 Session Memory 不再只是每轮用本地规则重写 `session-memory.md`。它会在模型回复完成后根据 token 增长和工具调用增长判断是否需要提取记忆；满足阈值时，runtime 会复用现有 subagent fork 机制启动一个受限 fork child。这个 child 只能编辑当前会话的 `.onecode/<session_id>/session-memory.md`，不能读写其他路径、运行 bash、搜索项目或再调用 `agent`。自动压缩触发时，compaction service 会优先等待正在进行的 memory 提取完成，然后读取 `session-memory.md` 作为摘要来替换旧消息历史；如果 memory 文件为空或压缩后仍过大，再回退到 full compact。

用户可见行为是：长会话进行中，`.onecode/<session_id>/session-memory.md` 会在满足阈值后由一个受限 fork agent 更新；`/status` 能显示 memory 文件和最近提取状态；自动压缩时 trace 会显示先等待或消费 session memory，而不是立即调用 full compact；恢复旧 transcript 后，runtime 会重新估算当前消息链并在达到初始化阈值时固定触发一次后台 memory 提取。验证方式是运行新增测试，观察受限 fork child 的 tool schema 只包含 memory edit 能力，并确认 memory compact 能消费该文件替换被压缩历史。


## Progress

- [x] (2026-06-06 16:10+08:00) 阅读 `PLANS.md`、`architecture.md`、`docs/design-docs/context-and-prompt-architecture.md`、`docs/design-docs/subagents-architecture.md`、`docs/tech-debt/tech-debt-tracker.md`，确认当前实现已有 compaction service、session memory markdown store、subagent fork、context preparer 和 manual/reactive compact 入口。
- [x] (2026-06-06 16:20+08:00) 与用户确认设计取舍：复用 subagent 机制中的 fork agent；通过传参或 mode 让 child runtime 禁用其他工具；不依赖 transcript UUID；resume 后重新计算阈值并触发一次后台提取；`PostCompact` 不作为提取触发点。
- [x] (2026-06-06 16:35+08:00) 创建本 ExecPlan，记录目标数据流、实现边界、里程碑、验证命令和当前代码债务。
- [x] (2026-06-06 18:35+08:00) 实现 message token 与工具调用增长计数，新增 `SessionMemoryExtractionPolicy`、`SessionMemoryExtractionDecision`、`count_tool_calls()` 和 `should_extract_memory()`。
- [x] (2026-06-06 18:45+08:00) 新增 `AssistantMessageCompleted` hook，并从 `AgentLoop` 在 assistant message 写入后、工具执行前触发 `SessionMemoryExtractionService`。
- [x] (2026-06-06 19:00+08:00) 实现复用 subagent fork 的受限 memory extraction mode：child registry 只暴露 `edit_file`，permission policy 只允许写指定 `session-memory.md`。
- [x] (2026-06-06 19:10+08:00) 新增 `SessionMemoryExtractionService`，使用 `asyncio.Lock` 防止同一 session 并发提取，并把最近状态写入 `state.metadata["session_memory_extraction"]`。
- [x] (2026-06-06 19:20+08:00) 增强 session memory compaction：读取 memory 文件前等待正在进行的 extraction，空 memory 或压缩后仍过大时保持回退 full compact。
- [x] (2026-06-06 19:30+08:00) 接入 CLI 装配、`/resume` 和 `/clear` 的 session scoped rebinding，并在 `/status` 展示 memory extraction 状态。
- [x] (2026-06-06 19:45+08:00) 补充 focused tests、CLI tests、compile check 和全量 pytest；最终 `uv run python -m pytest tests -q` 结果为 `197 passed in 2.34s`。


## Surprises & Discoveries

- Observation: 当前 Session Memory 已经有文件 store 和压缩消费路径，但提取方式是规则生成，不调用 LLM。
  Evidence: `services/compaction/session_memory.py` 中 `SessionMemoryUpdater.update_after_turn()` 调用 `build_rule_based_memory()` 后直接写 `.onecode/<session_id>/session-memory.md`。

- Observation: 当前 full compact 已经复用 subagent fork，但它只在 prompt 中写 “Do not call tools”，没有在 child registry 或 permission policy 中强制无工具。
  Evidence: `services/compaction/service.py::_full_compact()` 创建 `SubagentRequest(subagent_type=None, metadata={"query_source": "compact", ...})`；`services/subagents/definitions.py` 的 `fork` definition 只 `disallowed_tools=("agent",)`。

- Observation: `PostCompact` 事件语义是压缩完成后，而用户想要的 memory 提取发生在模型采样完成后、压缩触发前。
  Evidence: `services/compaction/service.py` 只在 `_post_compact()` 中运行 `HookEvent.POST_COMPACT`，这已经晚于 `_replace_active_messages()`。

- Observation: 不依赖 transcript UUID 是可行的，但需要把 resume 语义设计清楚。
  Evidence: `MessageStore.from_transcript()` 会从 JSONL 重建当前 messages；只要基于重建后的 message token 和 tool call count 重新初始化 extraction counters，就能在恢复后重新触发一次提取，而不需要找旧的 `last_summarized_message_uuid`。

- Observation: 复用 `edit_file` 可满足第一版 memory write，但必须在 child state 预置目标文件为已读。
  Evidence: `tools/edit_file/tool.py` 对已存在文件有 read-before-edit 检查；`services/subagents/runner.py` 的 memory extraction mode 写入 `child_state.metadata["files_read"] = {allowed_memory_path}`，新增 `tests/test_session_memory_extraction.py::test_memory_extraction_child_denies_editing_non_memory_path` 验证非目标路径被 permission policy 拒绝。

- Observation: `.onecode/<session_id>/session-memory.md` 可能位于 workspace boundary 外侧的 transcript root；memory extraction 不能依赖普通 protected-dir ask 流程。
  Evidence: `PermissionPolicy._memory_extraction_decision()` 在 guard deny 之后、普通 ask 之前识别 `memory_extraction_agent`，只允许指定 memory path，测试中的 memory path 位于 `tmp_path/.onecode/...` 而 child workspace 是 `tmp_path/workspace`。


## Decision Log

- Decision: 复用现有 subagent fork 机制实现 memory extraction，不新增第二套 fork agent。
  Rationale: OneCode 已经有 `SubagentRunner`、fork message 构造、父 prompt 继承和 child loop drain 机制。Session Memory 提取需要的是“从当前父上下文分叉并执行一个内部任务”，这与 fork agent 的语义一致。新增第二套 fork runner 会让上下文继承、trace、权限 bubble 和 prompt-cache 行为分裂。
  Date/Author: 2026-06-06 / 用户与 Codex

- Decision: “只能编辑 session-memory.md” 必须由 child runtime 的工具裁剪和权限策略强制，而不是只写进 prompt。
  Rationale: Prompt 是模型行为提示，不是安全边界。受限 fork child 应该只看到或只允许 memory edit 能力；即使模型请求 `bash`、`read_file`、`grep`、`edit_file` 其他路径或 `agent`，registry/executor/permission policy 也必须拒绝。
  Date/Author: 2026-06-06 / Codex

- Decision: 不使用 `PostCompact` 作为 Session Memory 提取触发点。
  Rationale: `PostCompact` 在旧消息已经被压缩后才触发，无法实现“提取和 compaction 解耦”。提取应发生在模型回复完成后，根据增长阈值后台维护 memory；auto compact 只是消费已经维护好的 memory。`PostCompact` 可以继续用于记录压缩结果、刷新状态或通知 UI。
  Date/Author: 2026-06-06 / Codex

- Decision: 新增模型回复后的 hook 或 runtime event，推荐命名为 `POST_SAMPLING` 或 `ASSISTANT_MESSAGE_COMPLETED`。
  Rationale: 用户描述的数据流是“模型回复 → Post-Sampling Hook → shouldExtractMemory()”。当前 hook enum 没有这个事件，所以需要新增 provider-neutral hook。该 hook 不应拿 provider 私有 response，而应拿归一化 assistant message、final text、tool calls、usage、state 和 current messages。
  Date/Author: 2026-06-06 / Codex

- Decision: 不依赖 transcript UUID 或 `last_summarized_message_uuid` 作为压缩边界。
  Rationale: 用户明确选择 resume 后重新计算增长阈值。这样恢复旧 transcript 后可以固定触发一次后台提取，避免当前 TD-015 中 fake `message-N` anchor 的不稳定性阻塞本功能。后续若补真实 UUID，可作为优化加入，但不是本计划的先决条件。
  Date/Author: 2026-06-06 / 用户

- Decision: Session Memory extraction 使用 sequential lock，同一 session 同一时间最多一个提取任务。
  Rationale: memory 文件是单个 Markdown 文件，多个 fork child 并发编辑会互相覆盖或制造 patch 冲突。顺序执行让后台提取、auto compact 等待和测试观察都更稳定。
  Date/Author: 2026-06-06 / Codex

- Decision: Resume 后如果 message token 达到 `minimumMessageTokensToInit`，应固定触发一次后台 memory extraction。
  Rationale: 不使用 transcript anchor 后，resume 的可靠做法是重新观察当前活动消息链，把它视为一个新的提取周期。重复提取一次的成本可接受，并且让 memory 文件尽快与恢复后的上下文对齐。
  Date/Author: 2026-06-06 / 用户与 Codex

- Decision: 本计划不实现 `files_changed` 记录，也不要求 Session Memory 包含 Files Changed 章节。
  Rationale: 用户明确认为 TD-015 所要求的 transcript anchor 和文件变更记录都不需要实现。Session Memory 的目标是维持当前会话连续性，可以由 fork agent 基于当前聊天记录和已有 memory 自行维护必要上下文；无需让 executor 额外记录文件变更事实。
  Date/Author: 2026-06-06 / 用户与 Codex


## Outcomes & Retrospective

本轮已完成第一版 fork agent 驱动的 Session Memory 提取闭环。`SessionMemoryExtractionService` 根据 token 与工具调用增长阈值触发，使用受限 fork child 更新 `.onecode/<session_id>/session-memory.md`；`AgentLoop` 在 assistant message 完成后触发 provider-neutral hook 和 extractor；`ContextCompactionService` 在消费 memory 前等待正在进行的 extraction；CLI 装配和 session 切换会重绑 extractor，`/status` 会显示最近提取状态。

验证结果：`uv run python -m pytest tests\test_session_memory_extraction.py tests\test_session_memory_compaction.py -q` 输出 `12 passed in 1.21s`；`uv run python -m pytest tests\test_subagent_runner.py tests\test_compaction_service.py tests\test_cli_commands.py -q` 输出 `19 passed in 0.34s`；`uv run python -m compileall core services infrastructure tools ui` 无语法错误；最终 `uv run python -m pytest tests -q` 输出 `197 passed in 2.34s`。

剩余风险：full compact 仍使用 fork child 并依赖 prompt 要求 “Do not call tools”，因此 TD-014 仍未解决；本轮只给 Session Memory extraction child 做了硬裁剪。`SessionMemoryUpdater` 规则版仍保留为兼容 fallback，但 CLI 已改用 `SessionMemoryExtractionService`。


## Context and Orientation

OneCode 是 Python code agent runtime。`core/loop.py` 是主循环，负责接收用户输入、调用模型、执行工具、写回消息和设置 transition。主循环必须保持薄，也就是不能在里面硬编码具体工具名或 provider 私有字段。`core/context_engine.py` 是每次模型调用前重建上下文的边界，它从 `services/context/message_store.py` 的 `MessageStore` 读取当前内部消息，然后调用可注入的 context preparer、prompt assembler 和 tool schema provider，返回 `services/context/snapshot.py` 中的 `ContextSnapshot`。

Session Memory 在本文中指当前会话的 Markdown 笔记，路径是 `.onecode/<session_id>/session-memory.md`。它不是长期记忆，也不是跨项目用户偏好；它只帮助当前长会话在压缩后保持连续性。当前实现位于 `services/compaction/session_memory.py`，包含 `SessionMemoryStore`、`SessionMemoryUpdater` 和 `build_rule_based_memory()`。第一版 updater 是本地规则摘要，不调用模型。本文要把 updater 升级为阈值控制的 fork agent 提取，同时保留 `SessionMemoryStore` 的单文件 Markdown 约束。

Compaction 在本文中指把模型可见上下文缩短的过程。`services/compaction/service.py` 中的 `ContextCompactionService.prepare_for_model()` 做 cheap pipeline，包括大工具结果引用、snip 滑窗和 microcompact 旧工具结果；`maybe_auto_compact()` 在超过阈值时先调用 `_try_session_memory_compact()`，再回退 `_full_compact()`；`manual_compact()` 支持 CLI `/compact`；`reactive_compact()` 支持 provider 返回 context limit error 后压缩并重试。本文要增强 `_try_session_memory_compact()`，让它等待正在进行的 memory extraction，并用 memory 内容替换旧历史。

Subagent 在本文中指由父 agent 通过 `agent` 工具启动的 child agent loop。`services/subagents/runner.py` 中的 `SubagentRunner` 每次创建 child `RuntimeState`、`MessageStore`、`ToolRegistry`、`ContextEngine`、`RegistryToolExecutor` 和 `AgentLoop`。省略 `subagent_type` 时，runner 使用隐藏 synthetic `fork` definition：child 复制父消息链，并继承父轮次已渲染的 system prompt 字符串。本文要复用这个 fork 机制，但增加一个 memory extraction mode，使 child registry 和 permission policy 只允许编辑当前 session memory 文件。

Hook 在本文中指 lifecycle extension event，由 `services/hooks/events.py` 和 `services/hooks/registry.py` 管理。当前已有工具相关 hook 和 compact hook。`PostCompact` 是压缩完成后的事件，不适合作为 memory 提取触发点。本文需要新增模型回复后的 hook 或 service event，让 `AgentLoop` 在 assistant message 完成后触发 `should_extract_memory()`。

本文使用的 “sequential lock” 指每个 session memory extraction service 内部持有一个异步锁。它保证同一 session 里即使连续多轮都满足阈值，也不会同时运行两个 memory fork child。后续请求可以发现已有任务正在运行并跳过，或者等待已有任务完成；auto compact 必须等待已有任务完成后再读取 memory 文件。


## Plan of Work

第一阶段先补提取策略和状态计数。新增或重写 `services/compaction/session_memory.py` 中的 updater 边界，建议新增 `SessionMemoryExtractionPolicy`、`SessionMemoryExtractionState` 和 `should_extract_memory(messages, state, policy)`。策略默认值必须与用户确认一致：`minimumMessageTokensToInit=10000`，`minimumTokensBetweenUpdate=5000`，`toolCallsBetweenUpdates=3`。函数使用 `services/compaction/token_estimator.py::estimate_messages_tokens()` 估算当前消息 token，并统计当前消息链中的工具调用次数。工具调用次数应从 assistant message 的 `tool_calls` 和 content block 中 `type="tool_use"` 的块统计，避免只看 `tool_result`。触发条件是：当前 token 达到初始化阈值，并且新增 token 至少 5000 且新增工具调用至少 3；或者当前 token 达到初始化阈值，并且新增 token 至少 5000 且最后一轮没有工具调用。`state.metadata["session_memory_extraction"]` 保存 `last_extracted_token_count`、`last_extracted_tool_call_count`、`last_status`、`last_started_at`、`last_completed_at` 和 `resume_generation` 等轻量状态。不要把 transcript UUID 作为必要字段。

第二阶段新增模型回复完成后的触发点。编辑 `services/hooks/events.py`，新增 `POST_SAMPLING = "PostSampling"`，或选择更直白的 `ASSISTANT_MESSAGE_COMPLETED = "AssistantMessageCompleted"`。编辑 `core/loop.py`，在 `self.message_store.append_assistant(completed_message.assistant_message)` 后、执行工具前，运行该 hook 或调用注入的 memory extraction service。推荐给 `AgentLoop.__init__()` 增加可选 `session_memory_extractor` 参数，而不是让 hook callback 自己持有复杂 runtime。接口可以是 `async def maybe_extract_after_model_response(messages, state, assistant_message, tool_calls, usage) -> None`。如果 assistant 本轮包含 tool calls，可以先更新计数但不等待提取；是否真正提取由 policy 判断。现有 `SessionMemoryUpdaterProtocol` 可以保留一段时间作为 fallback，但最终 CLI 应改用新的 extractor。

第三阶段实现受限 fork mode。编辑 `services/subagents/types.py`，给 `SubagentRequest` 增加 mode 或 metadata 约定，例如 `mode="fork"` 且 `metadata={"purpose": "session_memory_extraction", "allowed_memory_path": "<absolute path>"}`。编辑 `services/subagents/runner.py`，在 `run()` 中识别这个 purpose。仍然使用 `subagent_type=None` 的 fork 路径来继承父消息链和父 system prompt，但 child registry 必须进入 memory-only 模式。最保守实现是只注册一个受限版 `edit_file` descriptor，或者只注册原 `edit_file` descriptor 但配合 permission policy deny 非 memory path。为了让模型能安全编辑 memory 文件，child prompt 必须告诉它目标文件路径、当前 memory 内容和固定 front matter/章节要求；但 prompt 只是辅助，安全限制必须在代码层生效。

第四阶段实现 memory 文件工具权限。推荐新增 `services/permissions/memory_policy.py` 或在现有 `PermissionPolicy.evaluate()` 中识别 `state.metadata["memory_extraction_agent"]`。当这个 metadata 存在时，除目标为 `ToolTarget(kind="file", operation="write", value=<session-memory.md>)` 的 `edit_file` 调用外，所有工具调用都返回 deny。由于 `edit_file` 可能要求 read-before-edit，memory extraction mode 还应在 child state 中预置 `files_read` 包含 session memory 文件路径，或者提供一个专用 `memory_edit` descriptor 绕过普通项目文件 read-before-edit 限制。推荐优先做专用 descriptor，因为它的 schema 只需要接收完整 Markdown 内容或 patch，handler 只写一个固定路径，权限面最小。若选择复用 `edit_file`，测试必须证明不能编辑其他文件，不能读取其他文件，不能运行 bash，不能调用 agent。

第五阶段实现 `SessionMemoryExtractionService`。它构造时接收 `SessionMemoryStore`、`SubagentRunner`、policy、trace recorder 和当前 session 的 message store。`maybe_extract_after_model_response()` 先检查 `should_extract_memory()`，不满足则只更新 trace。满足时进入 sequential lock；锁内重新读取当前 messages 和 counters，避免等待期间状态过期。然后读取 `session-memory.md`，构造 prompt：说明这是内部 Session Memory 更新任务；要求保留 YAML front matter；要求更新 Current Goal、User Constraints、Key Findings、Relevant Files、Errors And Fixes、Pending Work、Next Step；要求只编辑指定 memory 文件；要求不要输出长解释。不要要求 executor 提供或维护 `files_changed`，也不要把 Files Changed 作为固定章节。随后调用 `SubagentRunner.run(SubagentRequest(subagent_type=None, metadata={"purpose": "session_memory_extraction", ...}))`。成功后更新 `state.metadata["session_memory_extraction"]` counters 和 status；失败时记录 trace 和 status，但不能让父主循环失败。

第六阶段增强 auto compact 的 memory 消费。编辑 `ContextCompactionService`，让它可选接收 `session_memory_extractor` 或一个小协议 `wait_for_session_memory_extraction(state) -> None`。在 `_try_session_memory_compact()` 读取 memory 文件前，如果存在正在运行的 extraction，则等待它完成。等待必须有合理 timeout 或只等待当前 service 持有的任务，避免无限阻塞。读取 memory 后，如果文件不存在、为空或只有模板，则返回 `None` 回退 full compact。由于本计划不使用 transcript UUID，`_recent_tail_for_session_memory()` 应继续基于 token 和消息数保留最近 tail，但需要更接近用户描述：计算要保留的消息起点时必须保证至少保留 `session_memory_min_tokens` 和 `session_memory_min_text_messages`，并通过 `ContextProjector.adjust_start_index_to_preserve_tool_pairs()` 保持 assistant tool call 与 tool result 配对。压缩后估算 token，如果仍超过 `auto_compact_threshold_tokens`，返回 `None` 回退 full compact。

第七阶段处理 resume、clear 和 CLI 状态。编辑 `ui/cli/types.py::CliRuntime.with_session()`，在 `/resume` 和 `/clear` 后重新创建 session scoped `SessionMemoryStore`、`SessionMemoryExtractionService` 和 result store。Resume 后应设置 metadata，例如 `state.metadata["session_memory_resume_needs_extraction"] = True`，或者让 extractor 看到 counters 缺失且当前 token 超过初始化阈值时固定触发一次提取。编辑 `ui/cli/renderer.py` 的 status 渲染，展示 `session-memory.md` 路径、最近提取 status、token count、tool call count 和是否有 extraction running。`/compact` 命令不直接触发 extraction，但 manual compact 可以选择先等待正在进行的 extraction；如果没有 memory 或压缩不达标，仍走 full compact。

第八阶段更新测试。新增 `tests/test_session_memory_extraction.py`，覆盖 `should_extract_memory()` 的四种场景：未达初始化 token 不触发；达 token 且新增 token/工具调用均达标触发；达 token 且最后一轮无工具调用且新增 token 达标触发；新增 token 不够不触发。新增 fake subagent runner 测试 extractor 会传 `subagent_type=None` 和 `metadata["purpose"]="session_memory_extraction"`。新增 runner 测试 memory extraction child 的 snapshot 或 registry 只暴露允许的 memory edit 能力。新增 permission/executor 测试证明 memory extraction child 不能编辑非 memory path、不能调用 bash、不能调用 agent。扩展 `tests/test_session_memory_compaction.py`，证明 auto compact 会等待 fake extraction 后读取新 memory；memory 空时回退 full compact；压缩后 token 仍过大时回退 full compact。扩展 CLI tests，证明 `/resume` 后重新绑定 extractor 并在阈值满足时标记需要提取。

第九阶段更新文档和技术债。编辑 `docs/design-docs/context-and-prompt-architecture.md`，说明 Session Memory 提取由模型回复后的受限 fork child 维护，compaction 只消费结果。编辑 `docs/design-docs/subagents-architecture.md`，说明 fork 支持内部 purpose mode，memory extraction mode 的工具能力硬限制。编辑 `docs/tech-debt/tech-debt-tracker.md`：如果实现后 full compact 仍只靠 prompt 禁工具，TD-014 只能部分缓解；如果同时给 compact summary fork 也做无工具/受限工具硬裁剪，则可更新 TD-014 状态。TD-015 已废弃，后续实现不得为了满足旧 TD-015 而补 transcript UUID anchor 或 `files_changed` 记录。


## Concrete Steps

从仓库根目录运行以下命令确认当前状态：

    cd D:\study\OneCode
    git status --short

预期会看到当前工作区可能已有其他未提交变更。执行本计划时不要回滚与本计划无关的变更。开始实现前阅读关键文件：

    Get-Content services\compaction\session_memory.py
    Get-Content services\compaction\service.py
    Get-Content services\subagents\runner.py
    Get-Content services\subagents\definitions.py
    Get-Content services\permissions\policy.py
    Get-Content core\loop.py
    Get-Content ui\cli\types.py

先实现纯策略测试，再写代码。建议第一批测试命令：

    uv run python -m pytest tests\test_session_memory_extraction.py -q

实现策略后，预期该文件中的 `should_extract_memory` 测试通过。随后实现受限 fork runner 和 permission 测试：

    uv run python -m pytest tests\test_session_memory_extraction.py tests\test_subagent_runner.py tests\test_tool_registry_and_executor.py -q

实现 compaction 等待和消费后，运行：

    uv run python -m pytest tests\test_session_memory_compaction.py tests\test_compaction_service.py -q

实现 CLI rebind 和 status 后，运行：

    uv run python -m pytest tests\test_cli_commands.py -q

最后运行编译和全量测试：

    uv run python -m compileall core services infrastructure tools ui
    uv run python -m pytest tests -q

成功时，compileall 不输出语法错误，pytest 最后一行应显示全部测试 passed。实际通过数量以当前工作树为准；执行者必须把最终输出摘要写入本计划的 `Outcomes & Retrospective`。


## Validation and Acceptance

功能验收必须证明四个行为。

第一，阈值控制正确。构造一组消息，估算 token 小于 10000 时不会提取；token 大于 10000 且比上次提取增长至少 5000，并且工具调用数增长至少 3 时会提取；token 大于 10000 且最后一轮无工具调用，并且 token 增长至少 5000 时也会提取。测试应直接断言 `should_extract_memory()` 的结果和 reason 字段。

第二，fork child 安全受限。使用 fake model client 让 memory extraction child 尝试调用 `bash`、`read_file`、`agent` 或编辑非 memory 文件时，结果必须是 deny 或 unknown tool；让它编辑 `.onecode/<session_id>/session-memory.md` 时应成功。测试要证明不是 prompt 文本阻止，而是 registry 或 permission policy 阻止。

第三，Session Memory 提取与 compaction 解耦。运行一个 fake turn 后，extractor 满足阈值并启动 fork child 更新 memory 文件；随后调用 `maybe_auto_compact()`，它读取 memory 文件并替换旧消息链，不再调用 full compact runner。测试应断言 fake full compact runner 请求数为 0，message store 当前第一条是 compact boundary，第二条包含 session memory 内容。

第四，resume 后重新计算并触发一次提取。用 `MessageStore.from_transcript()` 恢复一条长 transcript，创建新的 runtime/extractor。即使没有 `last_summarized_message_uuid`，只要当前 messages token 达到初始化阈值，下一次模型回复完成后应标记或启动一次 memory extraction。测试应断言 extractor 的 counters 从当前 messages 重建，而不是读取 transcript UUID。

手工验收可以在 CLI 中进行。配置 `.env` 后启动 OneCode，进行多轮包含工具调用的长对话，然后运行 `/status`。应能看到 session memory 路径和最近 extraction 状态。运行 `/compact current task` 后，如果 memory 文件存在且足够短，应看到 compact result 的 source 为 session memory；如果 memory 为空，应回退 full compact，并在 trace 中看到对应事件。


## Idempotence and Recovery

本计划的代码改动应是幂等的。重复运行 memory extraction 时，只会更新同一个 `.onecode/<session_id>/session-memory.md`。如果上一次提取正在运行，新触发应跳过或等待，不应启动第二个并发 child。`ToolResultStore` 和 transcript 继续保持 append-only 语义，不删除旧 messages。

如果 memory extraction fork 失败，父 agent 当前 turn 不能失败。Extractor 应记录 `session_memory_extraction_failed` trace 和 `state.metadata["session_memory_extraction"]["last_status"] = "failed"`，然后让主循环继续。下一次满足阈值时可以重试。如果 auto compact 正在等待 extraction，但 extraction 失败，应读取现有 memory；若 memory 为空或不够用，则回退 full compact。

如果受限 memory edit 工具写入了坏格式 Markdown，`SessionMemoryStore.read()` 不应崩溃。它可以把缺失 front matter 视为空 metadata，并保留文件内容。后续 extractor prompt 应要求修复 front matter。不要自动删除用户可检查的 memory 文件。

如果实现过程中需要修改测试 fixture 或 fake runner，保持 fixture 名称清晰，并避免依赖测试执行顺序。所有路径都使用 pytest `tmp_path`，不要写入真实用户 home 目录。当前仓库使用 `.onecode/<session_id>/session-memory.md`，不要写 `~/.claude/session-memory.md`；用户图中的 `~/.claude` 是参考设计，不是 OneCode 的目标路径。


## Artifacts and Notes

当前代码中最相关的入口如下：

    services/compaction/session_memory.py
      SessionMemoryStore 读写 .onecode/<session_id>/session-memory.md。
      SessionMemoryUpdater 当前是规则版，后续可替换为 fork extraction service 或保留为 fallback。

    services/compaction/service.py
      ContextCompactionService.prepare() 每轮模型前运行 cheap pipeline。
      maybe_auto_compact() 超阈值时先尝试 session memory compact，再 full compact。
      _try_session_memory_compact() 当前读取 memory 文件并替换活动消息链。
      _full_compact() 当前通过 SubagentRunner.run(SubagentRequest(subagent_type=None)) 复用 fork。

    services/subagents/runner.py
      SubagentRunner.run() 创建 child runtime。
      subagent_type is None 表示 fork child。
      需要新增 purpose=memory_extraction 时的工具裁剪。

    core/loop.py
      AgentLoop._run_loop_async() 在 message_completed 后 append assistant message。
      需要在该点触发 PostSampling hook 或 session memory extractor。

    ui/cli/types.py
      CliRuntime.with_session() 在 /clear 和 /resume 后重建 session scoped runtime。
      需要重新绑定 memory extraction service。

计划创建时的工作区状态包含既有未提交变更。后续执行者应先运行 `git status --short`，确认哪些文件是自己改动，避免回滚用户或其他 agent 的工作。


## Interfaces and Dependencies

本计划不引入新的第三方库。异步锁使用 Python 标准库 `asyncio.Lock`。Token 估算复用 `services.compaction.token_estimator.estimate_messages_tokens`。Subagent 复用 `services.subagents.runner.SubagentRunner`。Memory 文件读写复用 `services.compaction.session_memory.SessionMemoryStore`。

建议最终存在以下接口。具体类型名可在实现中微调，但语义必须保持。

在 `services/compaction/session_memory.py` 中定义：

    @dataclass(frozen=True)
    class SessionMemoryExtractionPolicy:
        minimum_message_tokens_to_init: int = 10_000
        minimum_tokens_between_update: int = 5_000
        tool_calls_between_updates: int = 3

    @dataclass(frozen=True)
    class SessionMemoryExtractionDecision:
        should_extract: bool
        reason: str
        message_tokens: int
        tool_call_count: int
        token_delta: int
        tool_call_delta: int

    def should_extract_memory(
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
        policy: SessionMemoryExtractionPolicy,
        *,
        last_response_had_tool_calls: bool,
    ) -> SessionMemoryExtractionDecision:
        ...

    class SessionMemoryExtractionService:
        async def maybe_extract_after_model_response(
            self,
            messages: tuple[dict[str, Any], ...],
            state: RuntimeState,
            *,
            assistant_message: dict[str, Any],
            tool_calls: tuple[Any, ...],
            usage: Any | None = None,
        ) -> None:
            ...

        async def wait_for_current_extraction(self, state: RuntimeState) -> None:
            ...

在 `services/subagents/types.py` 中让 `SubagentRequest.metadata` 承载：

    {
        "purpose": "session_memory_extraction",
        "allowed_memory_path": "D:\\study\\OneCode\\.onecode\\<session_id>\\session-memory.md"
    }

在 `services/subagents/runner.py` 中，`SubagentRunner.run()` 必须识别该 purpose，并对 child runtime 设置：

    child_state.metadata["memory_extraction_agent"] = True
    child_state.metadata["allowed_memory_path"] = allowed_path
    child_state.metadata["hidden_tools"] = {"agent", "bash", "read_file", "grep", "glob"}

如果实现专用 memory edit descriptor，则 child registry 只注册该 descriptor。如果复用 `edit_file`，permission policy 必须拒绝 `allowed_memory_path` 之外的任何 target。

在 `core/loop.py` 中，`AgentLoop.__init__()` 可新增：

    session_memory_extractor: SessionMemoryExtractionServiceProtocol | None = None

并在 assistant message append 后调用：

    await self.session_memory_extractor.maybe_extract_after_model_response(
        self.message_store.current_messages(),
        self.state,
        assistant_message=completed_message.assistant_message,
        tool_calls=tool_calls,
        usage=completed_message.usage,
    )

这个调用不得阻塞工具执行太久。第一版可以 await sequential service，因为它使用 fake tests；如果后续改后台 task，必须保证 auto compact 能等待当前 task 完成。

在 `ContextCompactionService` 中新增可选依赖：

    session_memory_extractor: object | None = None

并在 `_try_session_memory_compact()` 读取 memory 前调用 extractor 的 `wait_for_current_extraction()`。如果没有 extractor，保持现有行为。


## Revision Notes

2026-06-06 / Codex: 初始创建本 ExecPlan。原因是用户要求阅读 `PLANS.md` 并用中文撰写计划，目标是把当前规则版 Session Memory 改为阈值触发、受限 fork agent 提取、auto compact 消费 memory 的数据流。计划明确记录用户确认的取舍：复用 subagent fork、不依赖 transcript UUID、resume 后重新计算并触发一次提取、`PostCompact` 不作为提取触发点。

2026-06-06 / Codex: 根据用户反馈修订计划和技术债方向。Session Memory 不需要 `files_changed` 记录，也不需要 TD-015 中要求的真实 transcript anchor；计划中移除 Files Changed 章节要求，并明确后续不得为了旧 TD-015 增加 executor 文件变更记录。

2026-06-06 / Codex: 实现第一版 fork agent 驱动 Session Memory extraction。新增阈值策略、`AssistantMessageCompleted` hook、受限 memory extraction fork mode、permission policy 硬限制、compaction 等待 extractor、CLI rebind/status 展示和 focused tests；保留规则版 updater 作为 fallback。原因是用户要求开始执行计划并编写代码，且当前基础设施已足以落地最小安全闭环。
