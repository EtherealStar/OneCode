# Context And Prompt Architecture

本文描述 `services/context/`、目标 compaction/projector 边界和 `prompts/` 的架构。

## 职责边界

context 负责内部消息结构、session transcript 和模型调用前的上下文快照。

prompt 负责根据当前运行时状态组装 system prompt。

compaction 和 projector 是目标上下文治理能力，负责压缩、替换、引用和模型可见 message 投影。当前代码还没有完整 `services/compaction/` 或 `services/context/projector.py`。

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

OneCode 内部保留 provider-neutral `tool_result`，provider adapter 负责投影为目标 wire format。

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

当前 `usage_hints` 和 `transcript_refs` 预留给后续 compaction/projector 使用。

## ContextPreparer 目标

`ContextPreparer` 是 `ContextEngine` 中的可注入边界。当前默认是 no-op，目标上应承载：

- 大工具结果替换和引用。
- 旧工具结果清理。
- sliding window。
- compact summary 注入。
- context limit reactive compact。
- 模型可见 message projector。

preparer 可以同步或异步返回消息 iterable。它不决定主循环是否继续，只返回模型可见上下文或更新状态。

## DynamicPromptAssembler

`DynamicPromptAssembler` 根据 `PromptRuntimeContext` 组装 system prompt。当前输入包括：

- `RuntimeState`
- 当前 cwd
- 当前可见工具 descriptor
- 已读文件列表
- last transition

它刻意不包含 API key、provider 配置、session id、transcript 路径或 CLI mode。

CLI 装配会把 `ToolRegistry` 传给 assembler，因此 prompt 中的可用工具说明与 provider-visible tool schema 来自同一个可见工具视图。

## Prompt Sections

`prompts/sections.py` 定义可组合 section。当前 section 覆盖：

- identity
- behavior rules
- workspace state
- available tools
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

当前代码已经完成第 1、4、5、6 步的可注入边界；第 2、3 步仍是后续目标。
