# 实现上下文压缩、自动压缩与 Session Memory

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本计划必须按照仓库根目录的 `PLANS.md` 维护。`PLANS.md` 要求 ExecPlan 自包含、可执行、面向不了解本仓库的读者，并且每个里程碑都能通过测试或人工操作观察到工作行为。本文件是中文计划，但仍保留 `PLANS.md` 规定的英文段落标题，方便后续 agent 识别必须维护的章节。


## Purpose / Big Picture

OneCode 当前已经能持久化 JSONL transcript、运行工具、恢复会话和记录 trace，但模型可见上下文仍会随着长会话、工具输出和文件读取不断增长。完成本计划后，用户可以在同一个 CLI 会话里长时间工作：旧工具输出会被微压缩为预览或引用，接近模型上下文窗口时会自动压缩，API 返回上下文超限时会触发响应式压缩，`/compact` 可以手动压缩当前会话，Session Memory 会每轮自动更新并优先用于自动压缩。

可观察结果是：运行 CLI 后执行多轮包含大工具输出的任务，`.onecode/<session_id>/trace.jsonl` 中会出现 compact 相关事件，`.onecode/<session_id>/tool-results/` 中会保存可恢复的大工具结果，`.onecode/<session_id>/session-memory.md` 会随每轮交互更新，`/compact` 后当前活动消息链会变成 compact boundary、摘要消息和安全保留的最近消息。测试会证明模型调用前的 snapshot 变短，tool use 与 tool result 不被切断，session memory compact 会优先于 full compact。


## Progress

- [x] (2026-06-06 11:30+08:00) 阅读 `AGENTS.md`、`PLANS.md`、`architecture.md`、相关设计文档、技术债台账和 `docs/references/s08_context_compact/`，确认上下文压缩属于 `services/compaction/`、`services/context/`、`core/context_engine.py` 与恢复 transition 的交界。
- [x] (2026-06-06 11:45+08:00) 与用户确认关键设计方向：微压缩只改模型可见投影；full compact 和 session memory compact 才改写活动消息链；同时实现每轮增量 Session Memory；tool result 目录在敏感 `.onecode` 下获得读取豁免；阈值为模型上下文窗口减摘要输出预留后再减 15K；加入 `/compact`；full compact 使用相同模型的 fork agent。
- [x] (2026-06-06 12:00+08:00) 创建本中文 ExecPlan，记录设计、里程碑、验证方式和接口要求。
- [x] (2026-06-06 12:20+08:00) 修正计划：Session Memory 只保存 `.md` 文件；full compact 必须隐式调用现有 `agent` 工具生成的 fork subagent，不新增独立 fork agent 或 `fork_compactor.py`。
- [x] (2026-06-06 13:20+08:00) 实现 compaction 类型、配置、token 估算、result store 和模型可见 projector；新增 focused tests，并把 executor 可选 result-store 持久化接入现有 result policy。
- [x] (2026-06-06 14:30+08:00) 实现 hook 事件扩展：`UserPromptSubmit`、`PreCompact`、`PostCompact`、`CompactFailed`，并接入 trace。loop 在用户消息写入前触发 `UserPromptSubmit`；compaction service 在 session memory、manual、auto full 和 reactive 路径触发 compact hook 与 trace。
- [x] (2026-06-06 13:40+08:00) 实现模型调用前 cheap pipeline：tool result budget、snip、microcompact。新增 `ContextCompactionService.prepare_for_model()` / `prepare()`，可作为 `ContextEngine` 的 compaction-aware preparer 使用，且不改写 `MessageStore`。
- [x] (2026-06-06 13:40+08:00) 实现 `MessageStore.replace_messages_for_compaction()` 的受控活动消息链改写：先 flush transcript，再替换内存链，并把 compacted messages 作为新的 transcript records 追加写入。
- [x] (2026-06-06 14:30+08:00) 实现增量 Session Memory 抽取与 session memory compact。第一版使用规则提取器每轮写 `.onecode/<session_id>/session-memory.md`，不生成 JSON companion；自动压缩超过阈值时优先用 memory 加安全最近消息改写活动链。
- [x] (2026-06-06 14:30+08:00) 实现 full compact 的 fork subagent 摘要路径。`ContextCompactionService` 通过 `SubagentRunner.run(SubagentRequest(subagent_type=None, ...))` 复用现有隐式 fork agent，不新增第二套 fork compactor。
- [x] (2026-06-06 14:30+08:00) 实现自动压缩、响应式压缩和 `/compact` CLI 命令。`ContextEngine` 装配 compaction preparer；`AgentLoop` 对 `context_limit_exceeded` 只重试一次 reactive compact；CLI 新增 `/compact [focus]` 和 compact status。
- [x] (2026-06-06 14:30+08:00) 补充单元测试、CLI 测试、provider 错误恢复测试和编译检查。`uv run python -m pytest tests -q` 结果为 `180 passed`；`uv run python -m compileall core services infrastructure tools ui` 通过。


## Surprises & Discoveries

- Observation: 当前 `MessageStore.current_messages()` 返回深拷贝，因此微压缩可以先安全地只修改模型可见投影，不破坏内存里的完整活动消息链。
  Evidence: `services/context/message_store.py` 中 `current_messages()` 返回 `tuple(deepcopy(self._messages))`。

- Observation: 当前 transcript 已经会把超过 50KB 的 `tool_result.content` 外置，但这是 transcript record 层的持久化，不等于模型可见上下文治理。
  Evidence: `services/context/transcript.py` 中 `_message_for_record()` 会写入 `tool-results/<id>.txt`，但 `ContextEngine` 仍默认使用 `NoOpContextPreparer`。

- Observation: hook 架构已经存在并记录 trace，但当前只覆盖工具执行阶段。
  Evidence: `services/hooks/events.py` 目前只有 `PreToolUse`、`PostToolUse` 和 `ToolError`；`docs/design-docs/core-beliefs.md` 已把 `UserPromptSubmit`、`PreCompact`、`PostCompact` 和 `Stop`列为目标事件。

- Observation: `microcompact_keep_recent=0` 不能直接使用 Python 的 `[-0:]` 切片语义，因为它会返回完整列表而不是空列表。
  Evidence: `services/compaction/service.py` 中 `_microcompact()` 显式判断 `microcompact_keep_recent <= 0`，对应测试 `tests/test_compaction_service.py::test_prepare_method_can_be_used_as_context_preparer` 证明 0 表示不保留任何旧 tool result。

- Observation: executor 接入 durable result store 可以保持向后兼容。
  Evidence: `RegistryToolExecutor` 只有在构造时注入 `ToolResultStore` 且 `ToolResultPolicy.persist_when_exceeded=True` 时才写入 `tool-results/`；未注入时现有 JSON 预览行为保持不变，`uv run python -m pytest tests -q` 为 `174 passed`。

- Observation: CLI slash command handler 是同步函数，但运行在 async CLI 主循环内部，手动 `/compact` 需要桥接 async compaction service。
  Evidence: `ui/cli/commands.py::handle_command()` 由 `ui/cli/app.py::main_loop_async()` 直接调用；第一版 `/compact` 使用 `_run_async_blocking()` 在没有 running loop 时直接 `asyncio.run()`，在已有 running loop 时用短生命周期线程执行 coroutine，对应测试 `tests/test_cli_commands.py::test_compact_command_triggers_manual_compact` 通过。

- Observation: provider HTTP 413 和包含 “too many tokens” 的 400 错误此前都会变成 `invalid_response`，loop 无法进入 reactive compact。
  Evidence: `infrastructure/providers/http.py::provider_error_from_http_status()` 已改为把 413、`prompt_too_long`、`context_length_exceeded` 和 “too many tokens” 等错误归一化为 `ProviderError(error_type="context_limit_exceeded", retryable=False)`；测试 `tests/test_openai_compatible_provider.py::test_context_limit_http_errors_are_provider_neutral` 覆盖该行为。


## Decision Log

- Decision: 微压缩只修改模型可见投影，不改写 `MessageStore` 内的完整活动消息链。
  Rationale: 微压缩是廉价、可逆的上下文投影优化；保留完整内存链能让 full compact、session memory 抽取和 transcript 恢复仍看到原始细节。
  Date/Author: 2026-06-06 / 用户与 Codex

- Decision: full compact 和 session memory compact 可以改写活动消息链，但必须先 flush transcript，并写入 compact boundary。
  Rationale: 只有有损摘要需要真正释放后续上下文；compact boundary 让后续 projector、session memory 和测试知道旧历史已经被摘要覆盖。
  Date/Author: 2026-06-06 / 用户与 Codex

- Decision: 同时实现增量 Session Memory 抽取，每轮自动更新 memory，且 Session Memory 只保存 `.onecode/<session_id>/session-memory.md`，不保存 JSON companion file。
  Rationale: 自动压缩时优先使用已有 Session Memory 可以减少额外 LLM 摘要调用；每轮更新比临近超限时一次性提取更稳定。单一 Markdown 文件更符合用户要求，也便于人工检查和模型直接读取；必要 metadata 写入 Markdown front matter 或固定章节。
  Date/Author: 2026-06-06 / 用户与 Codex

- Decision: `.onecode/<session_id>/tool-results/` 虽位于敏感 `.onecode` 目录下，但应给读取豁免。
  Rationale: 大工具结果被外置后，模型需要能重新读取完整结果。这个目录是 runtime 生成的只读结果存储，不应与普通 `.onecode` 配置和内部状态同等阻断。
  Date/Author: 2026-06-06 / 用户

- Decision: 自动压缩阈值为 `模型上下文窗口 - 摘要输出预留 - 15_000`。
  Rationale: 先为摘要输出保留空间，再留 15K buffer，避免压缩请求或下一轮模型调用临界失败。
  Date/Author: 2026-06-06 / 用户

- Decision: 加入手动 `/compact` CLI 命令。
  Rationale: 用户需要在感知到任务阶段切换、长会话变慢或上下文混乱时主动压缩，而不只依赖自动触发。
  Date/Author: 2026-06-06 / 用户

- Decision: full compact 使用现有 `agent` 工具的隐式 fork subagent 路径，并使用相同模型；compaction 功能不得自己再实现一个 fork agent。
  Rationale: 省略 `subagent_type` 的 `agent` 工具已经代表 fork 请求，现有 `services/subagents/runner.py` 会继承父消息链和父轮次已渲染的 `ContextSnapshot.system_prompt`，这正是 compact 需要的 prompt-cache 友好路径。复用现有 subagent 机制能避免两套 fork 语义分叉。
  Date/Author: 2026-06-06 / 用户与 Codex

- Decision: 压缩机制通过 hook 暴露生命周期事件，但压缩事实来源仍是 `services/compaction/`。
  Rationale: hook 适合 memory、plan、skill、trace 和自定义附件这类横切扩展；消息安全不变量、tool result 配对和活动消息链改写必须由确定性服务控制。
  Date/Author: 2026-06-06 / Codex

- Decision: `RegistryToolExecutor` 的 result store 接入是可选依赖，不改变未注入 store 时的截断 JSON payload。
  Rationale: 这样可以先让 CLI 装配在具备 session 目录时启用 durable result store，同时保持现有单元测试、最小 runtime 和没有 transcript root 的调用方行为稳定。
  Date/Author: 2026-06-06 / Codex

- Decision: 当前 session 的 `.onecode/<session_id>/tool-results/` 只读访问跳过 protected `.onecode` ask，但 `.onecode` 其他路径和写操作不豁免。
  Rationale: 大工具结果被外置后，模型需要通过只读文件工具恢复完整输出；但 transcript、trace、配置和其他 runtime 内部文件仍属于受保护项目目录。
  Date/Author: 2026-06-06 / Codex

- Decision: Session Memory 第一版使用规则提取器，而不是额外模型调用。
  Rationale: 计划允许第一版规则合并；这样每轮自动更新 memory 不会增加 provider 调用、不会引入工具调用禁用的新 provider 分支，并且能用 deterministic tests 验证 `.onecode/<session_id>/session-memory.md` 的 front matter 和固定章节。后续可在同一 `SessionMemoryUpdater` 边界内替换为无工具模型摘要。
  Date/Author: 2026-06-06 / Codex

- Decision: `/compact` 通过 CLI command 的 async bridge 调用 service，不把 `handle_command()` 整体改成 async。
  Rationale: 现有命令测试和 CLI 调用方都使用同步 `handle_command()`；局部桥接能最小化 UI 改动，并保持 CLI 只负责命令分发和渲染，真正的消息改写仍由 `ContextCompactionService.manual_compact()` 完成。
  Date/Author: 2026-06-06 / Codex


## Outcomes & Retrospective

已完成上下文压缩与 Session Memory 的第一版闭环：`services/compaction/` 包含类型、配置、估算器、result store、cheap pipeline、Session Memory store/updater、session memory compact、full compact fork subagent 路径和 manual/reactive compact 入口；`services/context/projector.py` 能在裁剪模型可见消息时保留 tool call/tool result 配对；executor 可以把超预算工具结果持久化到 session `tool-results/`；`MessageStore` 支持 compact 后受控替换活动消息链且不删除旧 transcript；CLI 装配 compaction preparer、result store 和 memory updater，并新增 `/compact [focus]` 与 compact status。

当前第一版仍保留后续增强空间：full compact 请求本身超限时的三次裁剪重试尚未实现；自动阈值仍使用配置默认窗口而非 provider model catalog；Session Memory updater 采用规则摘要而非模型摘要；subagent child runtime 尚未独立装配 compaction service。核心用户可见链路已经可测试观察。

验证结果：`uv run python -m compileall core services infrastructure tools ui` 通过；`uv run python -m pytest tests -q` 通过，结果为 `180 passed`。


## Context and Orientation

OneCode 是一个 Python code agent runtime。`core/loop.py` 是薄主循环，负责接收用户输入、调用 `ContextEngine` 构建模型上下文、调用模型、执行工具并写回工具结果。`core/context_engine.py` 是每次模型调用前重建上下文的边界。它现在从 `MessageStore` 读取完整消息，调用可注入 `ContextPreparer`，组装 system prompt 和工具 schema，返回 `ContextSnapshot`。

上下文压缩在本计划里指三类行为。第一类是微压缩，英文常称 microcompact，意思是不调用模型、不改写完整历史，只在模型可见消息中把旧工具输出替换成占位符、预览或引用。第二类是自动压缩，意思是在模型调用前估算上下文接近上限时，自动生成摘要并替换活动消息链。第三类是响应式压缩，意思是模型 provider 已经返回上下文超限错误后，runtime 设置 `reactive_compact_retry` transition，压缩后重试一次模型调用。

Session Memory 在本计划里不是跨会话长期记忆。它是当前 session 内跨压缩保留任务连续性的 Markdown 文件，固定路径为 `.onecode/<session_id>/session-memory.md`，存放用户约束、当前目标、关键发现、已读文件、已修改文件、错误修复、待办事项和下一步。增量 Session Memory 抽取是每轮交互完成后自动更新这份 memory；session memory compact 是上下文接近上限时优先用已有 memory 加最近消息构造新活动上下文，而不是马上调用 full compact fork agent。

当前相关文件如下。`services/context/message_store.py` 存储内存消息并写入 JSONL transcript。`services/context/transcript.py` 写 `.onecode/<session_id>/messages.jsonl`，并会把很大的 tool result 外置到 `.onecode/<session_id>/tool-results/`。`services/context/snapshot.py` 定义 `ContextSnapshot`，已有 `usage_hints` 和 `transcript_refs` 字段但尚未由 compaction 填充。`services/hooks/` 当前只有工具 hook，需要扩展稳定生命周期事件。`services/subagents/runner.py` 已能创建 fork child，并隐藏递归 `agent` 工具。`ui/cli/commands.py` 和 `ui/cli/app.py` 管理 slash commands 和 CLI runtime 装配。

本计划不会把压缩细节写进 `core/loop.py`。主循环只应该知道 provider 发生 context-limit 错误时要设置 transition 并调用 compaction recovery 入口。常规模型调用前压缩应由 `ContextEngine` 调用 `services/compaction/` 完成。


## Plan of Work

第一步是补齐压缩数据结构和配置。新增 `services/compaction/types.py`，定义压缩触发原因、结果、配置、token 估算和 compact boundary。触发原因至少包含 `micro`, `auto_session_memory`, `auto_full`, `manual`, `reactive`。配置要包含 `auto_compact_buffer_tokens=15000`、`summary_output_reserved_tokens`、`default_context_window_tokens`、`tool_result_budget_chars`、`microcompact_keep_recent`、`snip_max_messages`、`session_memory_min_tokens`、`session_memory_max_tokens`、`session_memory_min_text_messages`、`max_consecutive_auto_compact_failures=3` 和 `max_reactive_compact_retries=1`。配置第一版从 `.env` 或 runtime 装配注入读取；如果 provider catalog 暂时不能给出模型上下文窗口，必须使用明确默认值并在 trace 中标记 `context_window_source="default"`。

第二步是实现 token 估算。新增 `services/compaction/token_estimator.py`，提供 `estimate_message_tokens(message)`、`estimate_messages_tokens(messages)` 和 `estimate_snapshot_tokens(snapshot)`。估算策略先采用字符数除以 4，再乘以 4/3 保守系数；图片和文档块按固定 token 估算。若 `RuntimeState.metadata` 中有最近一次 provider usage 的 input tokens，或 `state.usage` 能提供有效信息，自动压缩判断应优先使用 provider usage，再回退到粗估。测试要覆盖字符串、tool result、assistant tool calls 和未知块。

第三步是建立模型可见 projector。新增 `services/context/projector.py`。Projector 接收内部消息 tuple 和 compaction projection 决策，返回 provider-neutral 的内部消息 tuple，仍由 provider adapter 最终转成 wire format。Projector 必须维护 tool use 与 tool result 配对：如果保留某个 `role="tool_result"`，必须保留对应 assistant message 中的 tool call；如果裁剪边界会切断配对，必须向前扩展边界或放弃该裁剪。Chat Completions 目前把 assistant `tool_calls` 放在 assistant message 字段，把 tool result 放在独立 `role="tool_result"` 消息；测试必须覆盖这两者不被切断。

第四步是实现 result store，并把 `.onecode/<session_id>/tool-results/` 变成可读豁免目录。新增 `services/compaction/result_store.py`，复用当前 transcript session 目录，提供 `persist_tool_result(tool_call_id, tool_name, content) -> StoredResultRef` 和 `format_model_reference(ref, preview) -> str`。`RegistryToolExecutor._apply_result_policy()` 当前只生成截断预览；要改为当 `ToolResultPolicy.persist_when_exceeded=True` 且内容超过预算时写入 result store，并把模型可见 content 替换为包含 result path、result id、预览和重新读取提示的文本。修改 guard 或 permission policy，使 `.onecode/<session_id>/tool-results/` 对 `read_file` 这类只读文件工具允许读取，但不豁免 `.onecode` 下其他路径，也不允许写入。

第五步是扩展 hook。更新 `services/hooks/events.py`，新增 `UserPromptSubmit`、`PreCompact`、`PostCompact` 和 `CompactFailed`。`UserPromptSubmit` 在 `AgentLoop.stream(prompt)` 收到用户输入、写入 `MessageStore` 前后触发；第一版可选择写入后触发，但 payload 必须包含 `prompt_length`、`session_id` 和 `turn_count`，不能包含 API key 或 provider config。`PreCompact` 在 compaction service 已决定执行 full/session/reactive/manual compact 之后、改写消息前触发；payload 包含 trigger、token_before、message_count、transcript path 和可安全暴露的 metadata。HookResult 可以通过 `metadata` 返回 `summary_instructions`、`attachments` 或 `preserve_message_ids`。`PostCompact` 在新活动消息链写回后触发；payload 包含 trigger、token_before、token_after、messages_before、messages_after 和 boundary id。`CompactFailed` 在压缩失败后触发，用于 trace 和断路器记录。Hook 不能直接获得可变的真实 message list；需要修改消息时只能返回结构化建议，由 compaction service 验证后采用。

第六步是扩展 `MessageStore` 的受控改写能力。新增 `replace_messages_for_compaction(messages, reason, metadata)`。它必须先 flush 当前 transcript，再把当前内存 `_messages` 替换为新的 compacted messages，并把这些新消息作为新的 transcript records 追加写入，不删除旧 transcript。这样 transcript 保留完整历史，活动消息链释放上下文。方法要拒绝空消息链，给每条新消息分配新的 uuid，并在 metadata 中记录 compact boundary id。测试要证明旧 transcript 文件仍包含压缩前消息，新 `current_messages()` 只返回压缩后消息。

第七步是实现 cheap pipeline。新增 `services/compaction/service.py`，定义 `ContextCompactionService.prepare_for_model(messages, state) -> PreparedContext`。它按固定顺序运行 tool result budget、snip、microcompact，再估算 token。顺序必须是 result budget 先于 snip 和 microcompact，因为只有 budget 先运行才能在旧工具结果变成占位符前把完整内容写入 result store。Snip 不应简单按裸消息下标裁剪，必须基于 API round 或安全边界保留 head、tail 和 compact marker。Microcompact 默认保留最近 N 个可压缩工具结果，旧结果 content 替换为类似 `[Old tool result content cleared. Re-read the referenced file or rerun the tool if exact output is needed.]` 的占位符。Cheap pipeline 只返回模型可见投影，不调用 `MessageStore.replace_messages_for_compaction()`。

第八步是实现增量 Session Memory。新增 `services/compaction/session_memory.py`，提供 `SessionMemoryStore` 和 `SessionMemoryExtractor`。Store 只写 `.onecode/<session_id>/session-memory.md`，不得生成 `.json` companion file。需要机器读取的 metadata 写在 Markdown front matter 或固定标题章节中，至少包含 `last_summarized_message_uuid`、`updated_at`、`covered_turn_count` 和 `source`。Extractor 每轮模型交互完成后运行，可以先用同一个 model client 的无工具调用生成更新，也可以用规则合并第一版摘要；本计划要求“每轮自动更新 memory”，因此实现必须在 `AgentLoop` 完成一次交互或每次 assistant completed 后触发。为避免主循环变厚，loop 只调用一个注入的 `SessionMemoryUpdater` protocol，实际逻辑在 service 内。更新 prompt 必须要求模型输出 Markdown，不允许工具调用。失败时记录 trace，不阻断用户主任务。

第九步是实现 session memory compact。`ContextCompactionService` 判断超过自动压缩阈值后，先读取 Session Memory。若 memory 存在且不是空模板，根据 `last_summarized_message_uuid` 找出 memory 已覆盖到哪条消息，再计算最近消息保留起点。算法必须保证保留至少 `session_memory_min_tokens` 和 `session_memory_min_text_messages`，最多不超过 `session_memory_max_tokens`，并且不跨越旧 compact boundary 向前无限扩展。保留范围确定后调用 projector 的配对修复函数，确保 tool_result 不孤立。若 memory + 最近消息估算后低于自动压缩阈值，写入 compact boundary 和 summary message，调用 `replace_messages_for_compaction()`，触发 `PostCompact(trigger="auto_session_memory")`。若仍然超过阈值，回退 full compact。

第十步是实现 full compact 的 fork subagent 摘要路径。不要把摘要逻辑硬写成普通 provider 调用散落在 loop 里，也不要新增 `services/compaction/fork_compactor.py` 或自己实现第二套 fork agent。Compaction service 必须隐式调用现有 `agent` 工具生成的 subagent：构造与 `tools/agent/tool.py` 相同语义的 `SubagentRequest(prompt=<compact prompt>, subagent_type=None, parent_tool_call_id=<synthetic compact id>)`，交给现有 `services/subagents/runner.py::SubagentRunner.run()`。`subagent_type=None` 是现有机制中的 fork 信号，runner 会使用父消息链和父轮次已渲染的 system prompt，从而保留 prompt-cache 友好的上下文前缀。压缩提示词必须要求纯文本输出，不得调用工具，并要求包含固定章节：用户请求与意图、关键技术概念、文件和代码片段、错误与修复、问题解决过程、所有用户消息摘要、待办事项、当前工作、下一步。fork subagent 返回后删除 `<analysis>` 部分，只把 `<summary>` 或格式化摘要写入活动上下文。若 fork subagent 的压缩请求本身上下文超限，按 API round 从头裁剪重试，最多 3 次；失败后触发 `CompactFailed` 并增加断路器计数。

第十一步是实现自动压缩阈值和断路器。新增 `services/compaction/thresholds.py` 或放在 service 内。有效上下文窗口等于模型上下文窗口减摘要输出预留。自动压缩阈值等于有效上下文窗口减 15,000。模型上下文窗口优先来自 provider model catalog 或配置；不可用时使用默认值，例如 128,000，并在 trace 中记录来源。摘要输出预留优先取模型最大输出和配置上限中较小者；第一版可配置默认 20,000。连续 auto compact 失败达到 3 次后，本 session 跳过主动 auto compact，但 reactive compact 和手动 `/compact` 仍可尝试。

第十二步是实现 reactive compact。修改 provider adapter 和 HTTP transport 的错误归一化，使 HTTP 413、provider 明确的 `prompt_too_long`、`context_length_exceeded` 或包含 “too many tokens” 的错误变成 `ProviderError(error_type="context_limit_exceeded", retryable=False)`。`core/loop.py` 捕获该错误时，若 `RuntimeState.has_attempted_reactive_compact` 为 false，则设置 `TransitionReason.REACTIVE_COMPACT_RETRY`，记录 trace，调用 compaction service 的 `reactive_compact()`，然后继续当前 loop 重新构建 snapshot 并调用模型。若已经尝试过，则把错误暴露给调用方。Reactive compact 使用 full compact 或更激进的 session memory compact，然后保留最后几个安全 API round；如果仍然太大，按组裁剪旧消息。Reactive compact 不能无限循环。

第十三步是实现手动 `/compact`。更新 `ui/cli/commands.py`，加入 `/compact [focus]`。命令调用 compaction service 的 manual compact 入口，focus 会作为额外摘要指令传入 `PreCompact` 和 fork compact prompt。CLI 渲染应显示压缩前后估算 token、保留消息数、transcript 路径和 session memory 路径。`/help` 和 `/status` 也要更新：`/status` 显示 auto compact 阈值、最近 compact trigger、session memory 更新时间和连续失败次数。

第十四步是把 compaction service 装配进 CLI 和 subagent。更新 `ui/cli/app.py` 的 runtime 构建，创建 `ContextCompactionService`、`SessionMemoryStore`、`ResultStore` 和扩展后的 `HookRegistry`，注入 `ContextEngine`，并把当前已有的 `SubagentRunner` 注入 compaction service，供 full compact 隐式调用现有 `agent` fork 路径。Subagent 第一版应默认拥有自己的 session 目录和 compaction service，但 compact 触发的 fork subagent 本身不能递归触发 auto compact 或 session memory 更新；要通过 `RuntimeState.metadata["query_source"]="compact"` 或等价字段跳过递归自动压缩。普通 fork subagent 可以使用 cheap pipeline，但是否启用 auto compact 由配置控制，第一版建议启用 reactive compact、禁用主动 auto compact，以降低递归复杂度。

第十五步是补充 observability。`TraceRecorder` 需要记录 `compact_prepare`、`compact_result_budget`、`compact_micro`、`compact_auto_decision`、`compact_start`、`compact_completed`、`compact_failed`、`session_memory_update` 和 `reactive_compact_retry`。Trace attributes 只记录 token 数、消息数、路径摘要、trigger、失败类型和计数，不记录 prompt 全文、源码全文、工具结果全文或 API key。

第十六步是补充测试。新增 `tests/test_compaction_token_estimator.py`、`tests/test_context_projector.py`、`tests/test_result_store.py`、`tests/test_compaction_service.py`、`tests/test_session_memory_compaction.py`、`tests/test_reactive_compact_loop.py` 和 `tests/test_cli_compact_command.py`，必要时更新现有 hook、provider、guard、CLI 和 subagent tests。测试应大量使用 fake model client 和 temporary transcript root，不依赖真实 API key。


## Concrete Steps

从仓库根目录 `D:\study\OneCode` 开始。先确认当前状态：

    git status --short
    Get-ChildItem docs\exec-plans\active -Force

实现第一个里程碑时，新增文件：

    services\compaction\__init__.py
    services\compaction\types.py
    services\compaction\token_estimator.py
    services\compaction\result_store.py
    services\context\projector.py
    tests\test_compaction_token_estimator.py
    tests\test_context_projector.py
    tests\test_result_store.py

运行 focused tests：

    uv run python -m pytest tests\test_compaction_token_estimator.py tests\test_context_projector.py tests\test_result_store.py -q

期望看到新增测试通过，例如：

    12 passed

实现第二个里程碑时，更新：

    services\hooks\events.py
    services\hooks\registry.py
    services\context\message_store.py
    services\compaction\service.py
    core\context_engine.py
    tests\test_hooks.py
    tests\test_compaction_service.py
    tests\test_jsonl_session_persistence.py

运行：

    uv run python -m pytest tests\test_hooks.py tests\test_compaction_service.py tests\test_jsonl_session_persistence.py -q

实现第三个里程碑时，新增或更新：

    services\compaction\session_memory.py
    services\subagents\runner.py
    core\loop.py
    core\runtime_state.py
    infrastructure\providers\chat_completions.py
    infrastructure\providers\http.py
    tests\test_session_memory_compaction.py
    tests\test_subagent_runner.py
    tests\test_reactive_compact_loop.py
    tests\test_openai_compatible_provider.py

运行：

    uv run python -m pytest tests\test_session_memory_compaction.py tests\test_subagent_runner.py tests\test_reactive_compact_loop.py tests\test_openai_compatible_provider.py -q

实现第四个里程碑时，更新：

    ui\cli\commands.py
    ui\cli\renderer.py
    ui\cli\types.py
    ui\cli\app.py
    tests\test_cli_commands.py
    tests\test_cli_compact_command.py
    tests\test_runtime_integration.py

运行：

    uv run python -m pytest tests\test_cli_commands.py tests\test_cli_compact_command.py tests\test_runtime_integration.py -q

最后运行全量验证：

    uv run python -m compileall core services infrastructure tools ui
    uv run python -m pytest tests -q

如果需要人工观察 CLI，复制 `.env.example` 到 `.env` 并配置模型 provider 后运行：

    uv run python -m ui.cli.app

在 CLI 中执行多轮读取或搜索大文件，再运行：

    /status
    /compact 当前目标和下一步
    /trace 20

应能看到 `/compact` 成功摘要、`/status` 展示 compact/session memory 状态，`.onecode/<session_id>/trace.jsonl` 中出现 compact 事件。


## Validation and Acceptance

验收标准一：微压缩只影响模型可见 snapshot。构造一个 `MessageStore`，写入多条包含大 `tool_result` 的消息，调用 `ContextEngine.build_for_model()` 后，snapshot 中旧工具结果被占位符替换或 result ref 替换；再次调用 `message_store.current_messages()`，原始 content 仍完整存在。

验收标准二：result store 持久化大工具结果。让一个带 `persist_when_exceeded=True` 的工具返回超过预算的文本，executor 或 compaction result budget 将完整内容写入 `.onecode/<session_id>/tool-results/<safe-id>.txt`，模型可见 content 只包含预览和引用。随后用 `read_file` 读取该 result path，权限层允许只读访问；读取 `.onecode/<session_id>/messages.jsonl` 或其他 `.onecode` 内部文件仍按敏感目录规则处理。

验收标准三：projector 不破坏工具配对。测试从消息中裁剪旧段时，如果保留某个 `tool_result`，对应 assistant `tool_calls` 仍存在；如果无法保留配对，projector 选择向前扩展保留边界或删除孤立 tool result。Provider adapter 发送的 Chat Completions messages 不包含孤立 `role="tool"`。

验收标准四：自动压缩阈值正确。给定模型上下文窗口 128,000、摘要输出预留 20,000 时，有效上下文窗口为 108,000，自动压缩阈值为 93,000。测试应断言低于阈值只运行 cheap pipeline，高于阈值先尝试 session memory compact。

验收标准五：Session Memory 每轮自动更新。运行一次 fake loop，assistant 完成后生成或更新 `.onecode/<session_id>/session-memory.md`，metadata 记录最新覆盖的 message uuid。不得生成 `.onecode/<session_id>/session-memory.json` 或其他 JSON companion file。更新失败时主任务仍完成，并在 trace 记录 `session_memory_update` failure。

验收标准六：session memory compact 优先于 full compact。准备一个已有 session memory 且覆盖旧消息的会话，把 token 估算推到自动压缩阈值以上。调用 context build 后，compaction service 使用 session memory 加最近消息替换活动消息链，不调用 fork full compactor。若 session memory 为空或压缩后仍超阈值，则回退 full compact。

验收标准七：full compact 使用现有 `agent` 工具的隐式 fork subagent。使用 fake `SubagentRunner`，断言 full compact 调用 `SubagentRunner.run()` 且 request 的 `subagent_type is None`，没有实例化独立 fork compactor 或第二套 fork runner。该请求使用相同 model client 和父上下文，输出摘要后写入 compact boundary 和 summary message。压缩 prompt 禁止工具调用。

验收标准八：响应式压缩可恢复 context-limit 错误。Fake model client 第一次抛出 `ProviderError(error_type="context_limit_exceeded")`，loop 设置 `reactive_compact_retry`，调用 compaction service 改写消息，第二次模型调用成功。若第二次仍失败，loop 不无限重试。

验收标准九：`/compact` 可手动触发。CLI command 测试调用 `/compact focus text`，应触发 manual compact，renderer 显示压缩摘要和路径。`/help` 列出 `/compact`，`/status` 显示 compact/session memory 状态。

验收标准十：trace 不泄漏内容。Compact trace 事件包含 token 数、消息数、trigger、路径摘要和失败类型，但不包含完整 prompt、源码、工具输出或 API key。

验收标准十一：全量测试和编译通过。运行：

    uv run python -m compileall core services infrastructure tools ui
    uv run python -m pytest tests -q

预期 compileall 无错误，pytest 全部通过。


## Idempotence and Recovery

所有新文件和新目录应在当前 session 目录下创建，默认位于 `.onecode/<session_id>/`。重复运行 compaction 不应删除旧 transcript，也不应覆盖无关 session。Result store 文件名必须从 tool call id 规范化生成；若冲突，可以追加短 uuid。`replace_messages_for_compaction()` 只能替换内存活动链并追加新的 transcript records，不能截断或重写 `messages.jsonl`。

如果 full compact 失败，不能留下半替换的活动消息链。实现时先在局部变量中生成完整 compacted messages，只有摘要、boundary、tail 和 attachments 全部构造成功后才调用 `replace_messages_for_compaction()`。如果 session memory 更新失败，记录 trace 并保留旧 memory。手动 `/compact` 失败时向 CLI 返回清晰错误，不清空会话。

如果新增配置读取失败，使用保守默认值并在 trace 中记录。不要依赖网络下载 tokenizer；token 估算第一版必须用本地纯 Python 实现。


## Artifacts and Notes

参考材料来自 `docs/references/s08_context_compact/`，但本计划不照搬教学代码。OneCode 已有 provider-neutral message store、tool registry、guard、permission、hook、subagent 和 trace 边界，因此实现要落在这些边界内。

Cheap pipeline 顺序固定为：

    tool_result_budget -> snip -> microcompact -> token check -> session memory compact -> full compact

Reactive compact 在 provider 错误后运行：

    model call -> context_limit_exceeded -> reactive_compact_retry transition -> reactive compact -> retry once

Compact 后活动消息链建议形态：

    {"role": "user", "content": "[Compact boundary: trigger=auto_session_memory, ...]", "metadata": {"is_compact_boundary": true, ...}}
    {"role": "user", "content": "This session is being continued from a compacted context...\n\nSummary:\n..."}
    ...safe recent messages...

Session Memory 文件必须是单一 Markdown 文件 `.onecode/<session_id>/session-memory.md`。需要机器读取的 metadata 放在 front matter 或固定章节中，不新增 JSON 文件。内容建议包含：

    # Session Memory

    ## Current Goal
    ...

    ## User Constraints
    ...

    ## Key Findings
    ...

    ## Files Read
    ...

    ## Files Changed
    ...

    ## Errors And Fixes
    ...

    ## Pending Work
    ...

    ## Next Step
    ...


## Interfaces and Dependencies

在 `services/compaction/types.py` 中定义：

    class CompactionTrigger(StrEnum):
        MICRO = "micro"
        AUTO_SESSION_MEMORY = "auto_session_memory"
        AUTO_FULL = "auto_full"
        MANUAL = "manual"
        REACTIVE = "reactive"

    @dataclass(frozen=True)
    class CompactionConfig:
        default_context_window_tokens: int = 128_000
        summary_output_reserved_tokens: int = 20_000
        auto_compact_buffer_tokens: int = 15_000
        tool_result_budget_chars: int = 200_000
        tool_result_preview_chars: int = 4_000
        microcompact_keep_recent: int = 5
        snip_max_messages: int = 80
        session_memory_min_tokens: int = 10_000
        session_memory_max_tokens: int = 40_000
        session_memory_min_text_messages: int = 5
        max_consecutive_auto_compact_failures: int = 3
        max_reactive_compact_retries: int = 1

    @dataclass(frozen=True)
    class CompactionResult:
        trigger: CompactionTrigger
        messages: tuple[dict[str, Any], ...]
        token_before: int
        token_after: int
        transcript_refs: tuple[str, ...] = ()
        metadata: dict[str, Any] = field(default_factory=dict)

在 `services/compaction/result_store.py` 中定义：

    @dataclass(frozen=True)
    class StoredResultRef:
        result_id: str
        relative_path: str
        absolute_path: Path
        tool_call_id: str
        tool_name: str
        original_size_chars: int

    class ToolResultStore:
        def persist_tool_result(self, *, tool_call_id: str, tool_name: str, content: str) -> StoredResultRef: ...
        def format_model_reference(self, ref: StoredResultRef, *, preview: str) -> str: ...

在 `services/context/projector.py` 中定义：

    class ContextProjector:
        def project(self, messages: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]: ...
        def adjust_start_index_to_preserve_tool_pairs(self, messages: tuple[dict[str, Any], ...], start_index: int) -> int: ...

在 `services/compaction/service.py` 中定义：

    class ContextCompactionService:
        async def prepare_for_model(self, messages: tuple[dict[str, Any], ...], state: RuntimeState) -> CompactionResult: ...
        async def maybe_auto_compact(self, messages: tuple[dict[str, Any], ...], state: RuntimeState) -> CompactionResult | None: ...
        async def manual_compact(self, state: RuntimeState, *, focus: str | None = None) -> CompactionResult: ...
        async def reactive_compact(self, state: RuntimeState, *, error: ProviderError) -> CompactionResult: ...

在 `services/compaction/session_memory.py` 中定义：

    class SessionMemoryStore:
        def read(self) -> SessionMemory | None: ...
        def write(self, memory: SessionMemory) -> None: ...
        @property
        def path(self) -> Path: ...  # always .onecode/<session_id>/session-memory.md

    class SessionMemoryUpdater:
        async def update_after_turn(self, messages: tuple[dict[str, Any], ...], state: RuntimeState) -> None: ...

    class SessionMemoryCompactor:
        async def try_compact(self, messages: tuple[dict[str, Any], ...], state: RuntimeState, threshold_tokens: int) -> CompactionResult | None: ...

在 `services/hooks/events.py` 中扩展：

    class HookEvent(StrEnum):
        PRE_TOOL_USE = "PreToolUse"
        POST_TOOL_USE = "PostToolUse"
        TOOL_ERROR = "ToolError"
        USER_PROMPT_SUBMIT = "UserPromptSubmit"
        PRE_COMPACT = "PreCompact"
        POST_COMPACT = "PostCompact"
        COMPACT_FAILED = "CompactFailed"

在 `services/context/message_store.py` 中新增：

    def replace_messages_for_compaction(
        self,
        messages: Iterable[dict[str, Any]],
        *,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        ...

在 `core/context_engine.py` 中让 `ContextPreparer` 可以返回带 metadata 的 prepared context，或新增 compaction-aware preparer adapter。若保留当前 protocol，`ContextCompactionService.prepare_for_model()` 可以实现 `prepare(messages, state)`，并通过 `state.metadata["last_compaction"]` 暴露 usage hints；更完整的实现应扩展 `ContextSnapshot` 填充 `usage_hints` 和 `transcript_refs`。

在 `core/loop.py` 中只做两件事：触发 `UserPromptSubmit` hook；捕获 `context_limit_exceeded` 并调用 reactive compact retry。不要把 microcompact、session memory compact、full compact prompt 或 result store 逻辑写进 loop。

在 `ui/cli/commands.py` 中新增 `/compact`，并在 `ui/cli/types.py` 的 `CliRuntime` 保存 compaction service 引用。CLI 不直接改写 messages，而是调用 service。Compaction service 需要持有或能访问现有 `SubagentRunner`，full compact 时通过 `SubagentRunner.run(SubagentRequest(subagent_type=None, ...))` 复用现有隐式 fork agent。


## Change Log

2026-06-06 / Codex: 初始中文 ExecPlan。根据用户确认的六项方向，明确微压缩、自动压缩、Session Memory、tool result 读取豁免、`/compact` 和 fork subagent full compact 的设计，并把 hook 放在压缩生命周期扩展点而不是压缩事实来源。

2026-06-06 / Codex: 根据用户反馈修正两点。Session Memory 只保存 `.onecode/<session_id>/session-memory.md`，不保存 JSON；full compact 不新增独立 fork agent 或 `fork_compactor.py`，必须隐式调用现有 `agent` 工具的 fork subagent 路径，以继承上下文并利用 prompt cache。

2026-06-06 / Codex: 开始实现计划。新增 compaction 类型、token 估算器、durable tool result store、context projector 和 cheap pipeline service；扩展 hook event enum；新增 `MessageStore.replace_messages_for_compaction()`；让 executor 可选接入 result store；为当前 session `.onecode/<session_id>/tool-results/` 增加只读权限豁免。补充 focused tests 和全量验证，暂未实现 Session Memory、full compact、reactive compact 或 CLI `/compact`。

2026-06-06 / Codex: 继续执行计划并完成第一版端到端链路。新增 `services/compaction/session_memory.py`，每轮完成后写单一 Markdown Session Memory；`ContextCompactionService` 实现 session memory compact、full compact 复用现有隐式 fork subagent、manual compact 和 reactive compact；`AgentLoop` 触发 `UserPromptSubmit`、完成后更新 memory，并在 `context_limit_exceeded` 时只响应式压缩重试一次；HTTP provider 错误归一化 context-limit；CLI 装配 compaction/result store/memory 并新增 `/compact [focus]`、status 展示和测试。验证结果为 compileall 通过，`uv run python -m pytest tests -q` 为 `180 passed`。
