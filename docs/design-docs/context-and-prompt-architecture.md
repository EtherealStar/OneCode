# Context And Prompt Architecture

本文描述 `services/context/`、目标 compaction/projector 边界和 `prompts/` 的架构。

## 职责边界

context 负责内部消息结构、session transcript 和模型调用前的上下文快照。

prompt 负责根据当前运行时状态组装 system prompt。

compaction 和 projector 是上下文治理能力，负责压缩、替换、引用和模型可见 message 投影。当前代码已提供 `services/compaction/`、`services/context/projector.py`、session memory 和 durable tool result store 的第一版。

这些能力都由 `core/context_engine.py` 编排，不应进入 `core/loop.py`。

## MessageStore

`MessageStore` 是内存优先的 append-only session message store。当前支持：

- `append_user(content)`
- `append_assistant(message)`
- `append_tool_results(results)`
- `current_messages()`
- `seed_messages(messages)`
- `from_transcript(transcript_store, state)`
- `clear_for_new_session(new_session_id)`
- `flush_transcript()`

读取消息时返回 deepcopy，避免外部调用方直接修改内部状态。

内部消息角色当前包括：

- `user`
- `assistant`
- `tool_result`
- `attachment`

OneCode 内部保留 provider-neutral `tool_result`，provider adapter 负责投影为目标 wire format。

`attachment` 是 durable internal role，用于保存用户输入或运行时收集到的结构化上下文。它不能直接进入 provider payload；`AttachmentContextPreparer` 会在模型调用前把它投影成合法的 user、assistant 和 `tool_result` 消息。例如文件附件会临时变成 synthetic `read_file` assistant tool call 和匹配的 synthetic tool result，但这些 synthetic 消息不会写回 `MessageStore` 或 transcript。

`skill` attachment 是 runtime 按需加载技能全文的 payload。`skill` 工具的普通工具结果只保留 `Launching skill: <name>` 这类短文本，完整 `SKILL.md` 内容作为 durable `role="attachment"` 写入 transcript；下一轮 provider 调用前，`AttachmentProjector` 会把它投影成 synthetic user message，包含 `[skill loaded: <name>]` 边界、参数、来源和技能正文。

## JsonlTranscriptStore

`JsonlTranscriptStore` 将消息写入 `.onecode/<session_id>/messages.jsonl`。每条 record 包含：

- `type`
- `uuid`
- `parent_uuid`
- `session_id`
- `timestamp`
- `cwd`
- `message`

写入采用缓冲和定时 flush，测试、退出、session 切换和恢复时可显式 flush。

超过 50KB 的 `tool_result.content` 会外置到 `.onecode/<session_id>/tool-results/<tool_call_id>.txt`。JSONL 中保留预览和 metadata；恢复时会尝试读取外置文件并补回完整 content。

该 transcript 是会话恢复和未来上下文治理的事实来源，但它不是完整 durable result store 的替代品。

## ContextSnapshot

`ContextSnapshot` 是模型调用前的快照：

- `system_prompt`
- `messages`
- `tool_schemas`
- `usage_hints`
- `transcript_refs`
- `transition`

`usage_hints` 和 `transcript_refs` 由 compaction-aware preparer 填充，用于把模型可见投影的 token 估算、compact 信息和外置结果引用带入快照。

## ContextPreparer 目标

`ContextPreparer` 是 `ContextEngine` 中的可注入边界。当前默认是 no-op，compaction-aware 实现承载：

- 大工具结果替换和引用。
- 旧工具结果清理。
- sliding window。
- compact summary 注入。
- context limit reactive compact。
- 模型可见 message projector。

preparer 可以同步或异步返回消息 iterable，也可以返回 `PreparedContext` 以携带 `usage_hints` 和 `transcript_refs`。它不决定主循环是否继续，只返回模型可见上下文或更新状态。

## DynamicPromptAssembler

`DynamicPromptAssembler` 根据 `PromptRuntimeContext` 组装 system prompt。当前输入包括：

- `RuntimeState`
- 当前 cwd
- 当前可见工具 descriptor
- 当前可见 skill catalog
- 已读文件列表
- last transition

它刻意不包含 API key、provider 配置、session id、transcript 路径或 CLI mode。

CLI 装配会把 `ToolRegistry` 和 skill catalog provider 传给 assembler，因此 prompt 中的可用工具说明与 provider-visible tool schema 来自同一个可见工具视图；skill section 只展示名称、描述和 when-to-use 摘要，不展示技能全文。

## Prompt Sections

`prompts/sections.py` 定义可组合 section。当前 section 覆盖：

- identity
- behavior rules
- workspace state
- available tools
- available skills
- per-tool prompt

section 输出顺序稳定，空 body 会被跳过。工具专属 prompt 不放在 `prompts/`，而是由 `tools/<tool_name>/prompt.py` 提供，再通过 descriptor 暴露给 assembler。

## Prompt Cache

`PromptSectionCache` 提供进程内 section 级缓存。缓存 key 由 section key 和 fingerprint 组成。

fingerprint 应覆盖影响 section 输出的输入，例如 cwd、已读文件、可见工具集合、工具 prompt 文本和 prompt 版本。

## 目标上下文治理流程

完整目标流程：

1. `ContextEngine` 从 `MessageStore` 读取内部消息。
2. compaction service 处理大结果、旧结果和 compact boundary。
3. context projector 生成模型可见 messages。
4. prompt assembler 生成 system prompt。
5. tool schema provider 生成当前可见工具 schema。
6. 返回 `ContextSnapshot`。

当前代码已经完成上述流程的第一版；后续改进集中在更精细的 compact safety、session memory anchor 和 provider recovery 上。

附件投影是该流程的一环：CLI 或其他入口只负责提交预构建 attachment messages，`ContextEngine` 通过 context preparer 在 provider 调用前隐藏 raw attachment role。Provider adapter 不应包含 attachment-specific 分支；如果 provider payload 中出现 `role="attachment"`，应视为 context preparation bug。

## Session Memory

Session Memory 是当前会话目录下的 Markdown 文件，路径为 `.onecode/<session_id>/session-memory.md`。它只服务当前会话压缩后的连续性，不是跨项目长期记忆。

当前 runtime 在 assistant message 完成并写入 `MessageStore` 后发布 provider-neutral 的 `AssistantMessageCompleted` hook，并可调用 `SessionMemoryExtractionService`。该 service 使用 token 增长和工具调用增长策略判断是否需要更新 memory；满足阈值时复用 fork subagent，但 child runtime 被标记为 `memory_extraction_agent`，只允许通过 `edit_file` 写指定的 `session-memory.md`。

Compaction service 不负责生成 Session Memory。它在尝试 session-memory compact 前只等待正在进行的 extraction，然后读取现有 Markdown 文件作为摘要来源；如果 memory 不存在、为空或压缩后仍超过阈值，则继续回退 full compact。
