# 实现 JSONL 会话持久化与恢复

本 ExecPlan 是一个活文档。实现过程中必须持续维护 `Progress`、`Surprises & Discoveries`、`Decision Log` 和 `Outcomes & Retrospective`。本计划遵守仓库根目录的 `PLANS.md`，并把必要背景写入本文，使后续执行者只阅读本文和当前工作区也能完成实现。

## Purpose / Big Picture

完成本改动后，OneCode 在进入聊天运行时必须自动拥有一个会话 UUID，并把用户消息、assistant 消息、工具调用结果和必要 trace 信息持续写入项目内 `.onecode/<session_id>/messages.jsonl`。如果会话中某个工具结果内容超过 50KB，完整结果必须写入 `.onecode/<session_id>/tool-results/<toolUseId>.txt`，JSONL 中只保留预览和引用；小于或等于 50KB 的工具结果可以直接保存在 JSONL 记录里。

用户之后可以从某个 `.onecode/<session_id>/messages.jsonl` 读取并找回历史消息。恢复时应使用文件中的 session UUID 替换当前运行时自动生成的 UUID，使恢复后的会话继续写入同一个会话目录。用户执行 `/clear` 时应清除当前内存会话，重新生成 UUID，并进入新的空对话；旧会话目录和 JSONL 文件保留在磁盘上。

## Progress

- [x] (2026-06-04 08:40Z) 根据用户确认记录关键产品决策：目录使用 `.onecode/`，会话文件名是 `messages.jsonl`，必须支持从 JSONL 恢复，进程启动自动生成 UUID，超过 50KB 的工具结果外置到 `tool-results/<toolUseId>.txt`，`/clear` 开启新会话。
- [x] (2026-06-04 10:15Z) 实现运行时会话 UUID 默认生成，并提供 `RuntimeState.start_new_session()` 作为清空当前会话、生成新 UUID 的明确入口。
- [x] (2026-06-04 10:15Z) 实现 JSONL transcript 存储服务，负责路径计算、目录创建、定时缓冲写入、工具结果外置和读取恢复。
- [x] (2026-06-04 10:15Z) 改造 `MessageStore`，使所有消息追加同时进入内存和 JSONL transcript 缓冲，并保持当前模型上下文读取接口清晰。
- [x] (2026-06-04 10:15Z) 实现从 `messages.jsonl` 恢复 `MessageStore`，并在恢复时把 `RuntimeState.session_id` 替换为文件中的 session UUID。
- [x] (2026-06-04 10:15Z) 增加 focused tests，覆盖写入、恢复、大工具结果外置和 `/clear` 新会话语义。
- [x] (2026-06-04 10:25Z) 运行编译检查和全量测试，更新 `architecture.md` 与 `docs/tech-debt/tech-debt-tracker.md` 中与 transcript 相关的说明。

## Surprises & Discoveries

- Observation: 当前 `MessageStore` 只是内存 append-only list。
  Evidence: `services/context/message_store.py` 中只有 `_messages` 列表和 `append_user`、`append_assistant`、`append_tool_results`、`current_messages` 方法；技术债 `TD-005` 也明确记录缺少 durable transcript。

- Observation: 参考资料中的 JSONL 实现以“逐行 JSON + parentUuid 链 + sessionId + cwd + timestamp”为核心，但生产实现还包含大量 sidechain、compact boundary、metadata tail window 和远端同步逻辑。
  Evidence: `docs/references/JSONL会话记录/sessionStorage.ts` 的 `insertMessageChain` 会给 transcript message 补 `parentUuid`、`cwd`、`sessionId`、`timestamp` 等字段；`buildConversationChain` 按 parent 链从 leaf 回溯。OneCode 第一版只实现主会话链和工具结果外置，不一次性复制所有生产复杂度。

- Observation: 仓库 `.gitignore` 已忽略 `.onecode/`。
  Evidence: `.gitignore` 中存在 `.onecode/` 条目，因此会话记录默认不会进入 git。

- Observation: 用户明确修正第一版实现方向：`MessageStore` 不能是“默认纯内存、可选持久化”，而是会话内存态与强制持久化并存，持久化实现采用定时写入。
  Evidence: 2026-06-04 用户反馈：“不是默认纯内存，可选持久化，而是在会话进行时存在内存里，但是必须执行持久化，只不过持久化的实现是定时写入而已！”

## Decision Log

- Decision: 会话持久化目录使用项目根目录下 `.onecode/<session_id>/`，消息文件固定为 `messages.jsonl`。
  Rationale: 用户明确要求使用 `.onecode/` 和 `messages.jsonl`。按 session id 建目录可以自然容纳 `tool-results/`、未来 trace 文件和 metadata 文件。


- Decision: `RuntimeState.session_id` 不再默认为 `None`，而是在运行时状态创建时自动生成 UUID 字符串。
  Rationale: 用户要求程序进程一启动就自动生成 UUID。让 UUID 属于 `RuntimeState` 能让 loop、message store、transcript store 和未来 UI 共享同一个会话身份。


- Decision: 从 JSONL 恢复时，用文件中的 session UUID 替换当前运行时 UUID。
  Rationale: 用户要求“读取时用文件 UUID 替换当前 UUID”。这保证恢复后的继续对话追加到原会话目录，而不是自动生成的新空会话目录。


- Decision: 大于 50KB 的工具结果不直接写入 JSONL，而是写入 `.onecode/<session_id>/tool-results/<toolUseId>.txt`。
  Rationale: 用户明确给出 50KB 阈值和路径规则。JSONL 保留预览、引用路径和大小 metadata，使恢复和 trace 轻量，同时完整结果仍可找回。


- Decision: 本计划先实现主会话持久化与恢复，不实现多 leaf 分支、sidechain、compact boundary、远端同步和 metadata tail-window 优化。
  Rationale: 用户允许从容易实现的步骤开始。OneCode 当前没有 CLI、compaction、subagent 或 remote session 代码；先交付单主链可写可读，后续再扩展复杂拓扑。


- Decision: `MessageStore` 不提供纯内存运行模式；它始终拥有或接收一个 `JsonlTranscriptStore`，所有追加消息都会进入 transcript 缓冲区，并由定时 flush、显式 flush 或正常退出 flush 落盘。
  Rationale: 用户明确要求持久化是必选语义，而不是可选增强。内存仍是模型上下文读取来源，定时写入用于避免每条消息同步写文件。


- Decision: `AgentLoop.__init__` 会把 `MessageStore` 绑定到 `RuntimeState.session_id`。
  Rationale: `RuntimeState` 是运行时会话身份来源；如果调用方分别构造 state 和 message store，loop 装配时必须保证 transcript 目录使用同一个 session UUID。


## Outcomes & Retrospective

已完成基础 JSONL 会话持久化与恢复。`RuntimeState` 现在默认生成 session UUID，`MessageStore` 始终绑定 `JsonlTranscriptStore`，会话消息保存在内存中供模型上下文读取，同时进入 transcript 缓冲并通过定时 flush、显式 flush 或正常退出 flush 写入 `.onecode/<session_id>/messages.jsonl`。超过 50KB 的工具结果会外置到 `tool-results/<safe_tool_call_id>.txt`，恢复时会读回完整内容；`RuntimeState.start_new_session()` 与 `MessageStore.clear_for_new_session()` 提供未来 `/clear` 所需的新会话语义。

验证结果：`uv run python -m compileall core services infrastructure` 通过；`uv run python -m pytest tests -q` 通过，结果为 74 passed。`architecture.md` 已记录基础 transcript 已落地，`docs/tech-debt/tech-debt-tracker.md` 的 `TD-005` 已改为“部分缓解”，因为 compaction、projector、reactive compact 和通用 result store 仍未完成。

## Context and Orientation

OneCode 是一个 Python code-agent runtime。当前主循环在 `core/loop.py`。它通过 `AgentLoop.run(prompt)` 接收用户输入，把用户消息追加到 `services/context/message_store.py` 的 `MessageStore`，再调用 `core/context_engine.py` 构建 `ContextSnapshot`，最后通过注入的模型客户端和工具执行器完成一轮或多轮模型调用。

当前 `services/context/message_store.py` 是消息状态事实来源。实现后，它在会话进行时仍以内存保存当前模型上下文，但每次追加消息都会进入 `JsonlTranscriptStore` 的缓冲区，并由定时 flush、显式 `flush_transcript()` 或正常退出 flush 写入 `.onecode/<session_id>/messages.jsonl`。它暴露四个核心追加/读取方法：`append_user(content)`、`append_assistant(message)`、`append_tool_results(results)` 和 `current_messages()`。`current_messages()` 返回模型可见的内部消息列表，当前 provider adapter `infrastructure/providers/chat_completions.py` 会把内部 `role="tool_result"` message 投影为 OpenAI-compatible 的 `role="tool"` wire message。

当前 `core/runtime_state.py` 定义 `RuntimeState`，其中 `session_id` 目前是 `str | None = None`。本计划要把它改成自动生成的 UUID 字符串，并为 `/clear` 和恢复场景提供明确的 session 切换语义。

本计划中的 JSONL 是“每行一个 JSON 对象”的文本文件格式。追加一条消息时，写入一整行 JSON，再换行。读取时逐行解析，跳过空行和格式错误的行；第一版遇到单行损坏不应导致整个会话无法恢复。

本计划中的 transcript record 是写入 JSONL 的外层记录，不等同于模型调用时的 message。它应包含运行时追踪字段，例如 `uuid`、`parent_uuid`、`session_id`、`timestamp`、`cwd` 和 `message`。`message` 字段保存 `MessageStore` 当前内部消息对象；恢复时主要从这个字段重建内存消息。

本计划中的 `toolUseId` 对应 `ToolExecutionResult.tool_call_id`。为了形成安全文件名，写入 `tool-results/<toolUseId>.txt` 前必须把 tool call id 限制为简单文件名字符；如果包含路径分隔符或其他不安全字符，应转换为下划线或使用 UUID fallback，同时把原始 tool call id 保存在 JSONL metadata 里。

## Plan of Work

第一阶段实现会话身份和 transcript 存储的最小边界。编辑 `core/runtime_state.py`，把 `session_id` 改成 `field(default_factory=lambda: str(uuid.uuid4()))`。同一文件中增加 `start_new_session()` 方法或同等清晰入口，用于 `/clear` 语义：生成新的 session id，重置 `turn_count`、`last_transition`、usage 和与当前消息链相关的 metadata。不要在 `core/loop.py` 中硬编码 `/clear` 字符串；如果当前还没有 CLI，测试可以直接调用这个方法或未来应用层调用它。

第二阶段新增 `services/context/transcript.py`。定义一个 `JsonlTranscriptStore` 类，构造时至少接收 `root_dir: Path`、`session_id: str` 和可选 `cwd: Path`。`root_dir` 在项目运行中应是工作区根目录下 `.onecode`。它提供 `messages_path`、`tool_results_dir`、`append_message(message, parent_uuid)`、`load_messages()`、`flush()` 和 `switch_session(session_id)` 这类能力。追加消息时把 record 放入内存缓冲区，并安排定时 flush；`flush()`、读取恢复、session 切换和正常进程退出时会把缓冲内容以 UTF-8 追加到 `.onecode/<session_id>/messages.jsonl`。JSON 序列化使用标准库 `json.dumps(..., ensure_ascii=False, separators=(",", ":"))`，保证每条记录占一行。

第三阶段改造 `services/context/message_store.py`。`MessageStore.__init__` 接收可注入的 `transcript_store: JsonlTranscriptStore | None = None`、`session_id: str | None = None`、`transcript_root` 和 `flush_interval_seconds`。注入 store 只是为了装配和测试控制路径，不代表持久化可选；如果调用方不传 store，`MessageStore` 必须创建默认 `.onecode` transcript store。每次 `_append()` 成功写内存后，也写入 JSONL transcript 缓冲。为了建立链，`MessageStore` 内部维护 `_last_uuid: str | None`。每条新消息写入 record 时分配一个新的 `uuid`，`parent_uuid` 使用 `_last_uuid`，进入 transcript 缓冲后更新 `_last_uuid`。内存中的模型 message 不需要暴露这些 transcript 字段，避免污染 provider projection。

第四阶段处理工具结果外置。`MessageStore.append_tool_results()` 当前把 `ToolExecutionResult.content` 放入内部 `tool_result` message。写 JSONL 时，`JsonlTranscriptStore` 应检查 `role == "tool_result"` 且 content 字符长度按 UTF-8 编码后大于 50 * 1024 字节。如果不超过阈值，record 中保留完整 content。如果超过阈值，把完整 content 写入 `.onecode/<session_id>/tool-results/<safe_tool_call_id>.txt`，record 中的 `message.content` 替换为预览或引用文本，并在 record metadata 中写入：

    {
      "tool_result_externalized": true,
      "tool_result_path": "tool-results/<safe_tool_call_id>.txt",
      "original_size_bytes": 123456,
      "preview_chars": 4000
    }

模型当前上下文是否继续保留完整工具结果，需要按实现风险决定。第一版可以让内存 `MessageStore` 继续保留完整 `content`，只在 JSONL 中外置；这样不改变当前 loop 行为。后续 result store/projector 再决定模型可见内容是否也替换为引用。

第五阶段实现恢复。`JsonlTranscriptStore.load_messages()` 读取 `.onecode/<session_id>/messages.jsonl`，逐行解析 JSON。只处理 `type == "message"` 的 record，跳过损坏行、缺少 `message` 的行和角色不明的行。第一版按文件顺序恢复主链即可；如果要更接近参考实现，可按 `parent_uuid` 建图，选择最新 timestamp 的 leaf，再从 leaf 回溯到 root。为了降低实现风险，本计划要求第一版先按文件顺序恢复，并在测试中只覆盖单主链会话。恢复外置工具结果时，如果 record metadata 标记 `tool_result_externalized`，读取 `tool_result_path` 对应文件，把完整内容放回内存 message 的 `content`。如果外置文件缺失，保留 JSONL 中的预览/引用文本，并在 message metadata 中标记 `missing_external_tool_result: true`。

第六阶段增加 `MessageStore.load_from_transcript(...)` 或同等构造入口。该入口接收 `JsonlTranscriptStore` 和 `RuntimeState`，读取 JSONL 后把 `_messages` 重建为恢复出的 message tuple/list，把 `_last_uuid` 设置为最后一条 record 的 uuid，并把 `RuntimeState.session_id` 替换为文件中的 session id。恢复完成后继续追加消息时，必须写回同一个 `.onecode/<session_id>/messages.jsonl`。

第七阶段补充 `/clear` 所需的服务入口。当前仓库没有 `ui/cli/`，所以不要为了 `/clear` 临时创建完整 CLI。应在 context/runtime 边界提供可被未来 CLI 调用的能力，例如 `MessageStore.clear_for_new_session(new_session_id)` 和 `RuntimeState.start_new_session()`。调用后内存消息为空、`_last_uuid` 为空、transcript store 切到新的 session 目录。测试用这个入口模拟 `/clear`，验证旧文件保留、新消息写入新 session 目录。

第八阶段更新测试。新增 `tests/test_jsonl_session_persistence.py`，用 `tmp_path` 创建临时 workspace，不读写真实 `.onecode`。测试要覆盖默认持久化语义、正确路径、JSONL 行可解析、parent 链按顺序建立、恢复后 message 内容等于原消息、大于 50KB 的 tool result 被写到 `tool-results` 并可恢复完整内容、模拟 `/clear` 后新旧 session 分离。已有 `tests/test_loop.py`、`tests/test_context_engine.py`、`tests/test_runtime_integration.py` 和 provider 集成测试要在构造 `MessageStore` 时使用临时 transcript root，避免测试污染真实项目目录。

第九阶段更新文档和技术债。实现完成后，更新 `architecture.md` 中 `services/context/message_store.py` 和目标 transcript 描述，说明当前已经有基础 JSONL transcript，但 compaction/result store 仍未完整实现。更新 `docs/tech-debt/tech-debt-tracker.md` 的 `TD-005`：如果只完成基础 transcript，应把债务描述改为“已有 JSONL transcript，仍缺 compaction/projector/result store 治理”；不要错误地把全部上下文治理债务标记为完全解决。

## Concrete Steps

在仓库根目录执行所有命令：

    cd D:\study\OneCode

开始前查看工作区，确认不要覆盖用户已有改动：

    git status --short

实现阶段建议按以下顺序编辑：

1. 编辑 `core/runtime_state.py`，引入 `uuid` 和 `field(default_factory=...)`，新增新会话重置入口。
2. 新建 `services/context/transcript.py`，实现 JSONL 追加、读取、工具结果外置、session 切换。
3. 编辑 `services/context/message_store.py`，接入必选 transcript 持久化、uuid parent 链和恢复入口；构造函数可接收外部 transcript store，但不能提供纯内存模式。
4. 新增或更新 tests，优先覆盖 transcript store 的纯文件行为，再覆盖 MessageStore 集成行为。
5. 必要时更新 `core/context_engine.py` 或测试装配，使恢复后的 `current_messages()` 能被现有 context engine 正常读取。
6. 更新 `architecture.md` 和 `docs/tech-debt/tech-debt-tracker.md`。

每完成一个阶段，运行相关测试。例如只改 transcript store 后先运行：

    uv run python -m pytest tests/test_jsonl_transcript_store.py -q

MessageStore 集成后运行：

    uv run python -m pytest tests/test_context_engine.py tests/test_loop.py tests/test_jsonl_transcript_store.py -q

最终运行：

    uv run python -m compileall core services infrastructure
    uv run python -m pytest tests -q

期望最终测试全部通过。当前仓库在本计划创建时已有用户未提交改动，实现者不能回退这些无关改动。

## Validation and Acceptance

验收标准一：创建带 transcript store 的 `MessageStore` 后，调用 `append_user("hello")` 会创建 `.onecode/<session_id>/messages.jsonl`。文件至少包含一行 JSON，该行 `type` 是 `"message"`，`session_id` 等于当前 `RuntimeState.session_id`，`parent_uuid` 是 `null`，`message.role` 是 `"user"`，`message.content` 是 `"hello"`。

验收标准二：连续追加 user、assistant、tool_result 后，JSONL 中三条 message record 的 parent 链是线性的：第二条 `parent_uuid` 等于第一条 `uuid`，第三条 `parent_uuid` 等于第二条 `uuid`。`MessageStore.current_messages()` 仍返回当前 provider adapter 能消费的内部 message 列表。

验收标准三：当 `ToolExecutionResult.content` 的 UTF-8 大小为 50KB 或更小时，JSONL record 中可以直接包含完整 content。当大小大于 50KB 时，`.onecode/<session_id>/tool-results/<toolUseId>.txt` 必须存在且包含完整 content，`messages.jsonl` 中不直接保存完整大内容，而保存预览和引用 metadata。

验收标准四：从某个 `messages.jsonl` 恢复时，新建的 `MessageStore` 能重建原消息列表；如果存在外置工具结果文件，恢复后的内存 `tool_result` message 包含完整内容。恢复时 `RuntimeState.session_id` 被设置为文件 session id，后续追加消息写入同一个会话目录。

验收标准五：模拟 `/clear` 时，调用新会话入口后内存消息为空，`RuntimeState.session_id` 变成一个新的 UUID，后续第一条消息写入 `.onecode/<new_session_id>/messages.jsonl`。旧 `.onecode/<old_session_id>/messages.jsonl` 仍存在且内容未被清空。

验收标准六：运行以下命令通过：

    uv run python -m compileall core services infrastructure
    uv run python -m pytest tests -q

## Idempotence and Recovery

写入 JSONL 是追加式操作。重复运行测试必须使用 `tmp_path` 隔离目录，不应污染真实项目 `.onecode/`。如果追加过程中目录不存在，store 应自动创建目录。工具结果外置写入同一 `toolUseId` 文件时，如果内容相同可以覆盖；如果文件已存在但内容不同，第一版可以覆盖，因为同一 session 的同一 tool call id 不应被重复用于不同结果。未来需要并发写入时，再引入更严格的队列或原子写策略。

读取 JSONL 时应跳过空行和格式错误行，不能因为单行损坏导致整个会话无法恢复。外置工具结果文件缺失时，不应抛出导致恢复失败；应恢复 JSONL 中的预览文本并标记 metadata，使用户知道完整结果缺失。

`/clear` 不删除旧会话文件。它只是新建运行时会话身份并清空内存消息。这样误触 `/clear` 后仍可通过旧 session id 恢复。

## Artifacts and Notes

一个最小 JSONL message record 示例：

    {"type":"message","uuid":"9fd38f49-4db0-4a98-96f2-82f6f01c5756","parent_uuid":null,"session_id":"39093bfa-58de-4ad4-8ec6-893b65785d2e","timestamp":"2026-06-04T08:40:00Z","cwd":"D:\\study\\OneCode","message":{"role":"user","content":"hello"}}

一个外置工具结果 record 示例：

    {"type":"message","uuid":"e6cc8141-4075-4a1d-aa1e-2cd706fba4a3","parent_uuid":"9fd38f49-4db0-4a98-96f2-82f6f01c5756","session_id":"39093bfa-58de-4ad4-8ec6-893b65785d2e","timestamp":"2026-06-04T08:40:05Z","cwd":"D:\\study\\OneCode","message":{"role":"tool_result","tool_call_id":"call_read","tool_name":"read_file","content":"[tool result externalized: tool-results/call_read.txt]","is_error":false,"metadata":{"tool_result_externalized":true,"tool_result_path":"tool-results/call_read.txt","original_size_bytes":61440,"preview_chars":4000}}}

## Interfaces and Dependencies

在 `core/runtime_state.py` 中，`RuntimeState.session_id` 最终应是非空字符串：

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))

同一 dataclass 应提供新会话入口，名称可以是：

    def start_new_session(self) -> str:
        ...

该方法返回新的 session id，方便应用层同步切换 `MessageStore` 和 transcript store。

在 `services/context/transcript.py` 中定义：

    class JsonlTranscriptStore:
        def __init__(self, root_dir: Path, session_id: str, cwd: Path | None = None) -> None: ...
        @property
        def session_dir(self) -> Path: ...
        @property
        def messages_path(self) -> Path: ...
        @property
        def tool_results_dir(self) -> Path: ...
        def switch_session(self, session_id: str) -> None: ...
        def append_message(self, message: dict[str, Any], *, message_uuid: str, parent_uuid: str | None) -> None: ...
        def load_messages(self) -> tuple[LoadedTranscriptMessage, ...]: ...

`LoadedTranscriptMessage` 可以是 dataclass，至少包含：

    uuid: str
    parent_uuid: str | None
    session_id: str
    message: dict[str, Any]

在 `services/context/message_store.py` 中，`MessageStore` 应继续支持无参构造，但无参构造也必须创建默认 `.onecode` transcript store。持久化不是可选能力；外部注入只用于让测试或应用装配控制 root、session id 和 flush 间隔：

    message_store = MessageStore(transcript_store=store)

恢复入口可以是类方法：

    MessageStore.from_transcript(store, state)

也可以是实例方法：

    message_store.load_from_transcript(store, state)

选择其中一种即可，但测试和文档要保持一致。

实现不得把具体 provider 字段泄露进 `core/loop.py`。JSONL 写入属于 `services/context` 边界，工具结果外置属于 transcript/result-store 的第一版能力，主循环仍只调用 `MessageStore.append_*`。

2026-06-04 / Codex: 初始中文 ExecPlan 创建，纳入用户关于 `.onecode/`、`messages.jsonl`、恢复、自动 UUID、大工具结果外置和 `/clear` 新会话语义的决策。计划按可独立验证的阶段组织，避免一次性实现完整 compaction/sessionStorage 复杂度。
