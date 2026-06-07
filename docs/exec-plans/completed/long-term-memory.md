# OneCode 长期记忆机制

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本计划遵守仓库根目录的 `PLANS.md`。实现者只阅读本文件和当前工作树，也应能完成长期记忆第一版，不需要依赖此前对话。

## Purpose / Big Picture

完成本计划后，OneCode 可以像一个有长期项目记忆的 code agent 一样工作。用户可以在项目中放置 `ONECODE.md`、`.onecode/rules/*.md` 和 `ONECODE.local.md` 来控制运行时指令；OneCode 也会在当前 workspace 的 `.onecode/memory/` 中维护跨会话长期记忆。用户明确说“remember”或“forget”时，主 agent 能直接写入记忆；用户没有明确要求但对未来有价值的偏好、反馈或项目事实，会在每轮结束后由受限 fork agent 提取并写入记忆。

可观察结果是：启动 CLI 后，系统提示词包含按优先级拼接的 OneCode 指令和 `.onecode/memory/MEMORY.md` 索引；相关长期记忆正文会按需作为 memory attachment 注入模型上下文；显式记忆写入会产生普通 `write_file` 或 `edit_file` 工具调用；自动提取会在 assistant 完成后阻塞运行一个受限 fork child，并只允许它读工作区或写 `.onecode/memory/`。第一版不实现团队记忆、不实现 Dream consolidation，也不把提取放入真正后台任务。

## Progress

- [x] (2026-06-07 20:45+08:00) 阅读 `AGENTS.md`、`PLANS.md`、`architecture.md`、相关 design docs、tech debt tracker，以及 `docs/references/s09_memory/` 中的 `claudemd.ts`、`memdir/*`、`services/extractMemories/*` 和 `services/autoDream/*`。
- [x] (2026-06-07 20:50+08:00) 与用户确认第一版产品范围：使用 OneCode 原生命名；长期记忆目录为 `<workspace>/.onecode/memory/`；显式 remember/forget 由主 agent 直接写，自动补漏由 fork agent 写；自动提取第一版阻塞；相关性选择使用 LLM side-query 且不做 keyword fallback；记忆类型固定四类；Dream 不进入第一版；支持 `@include`、HTML 注释过滤和 `MEMORY.md` 截断。
- [x] (2026-06-07 21:00+08:00) 撰写本中文 ExecPlan，明确模块落点、权限豁免、加载顺序、写入互斥和验证方式。
- [x] (2026-06-07 21:15+08:00) 根据用户要求修订计划：明确扩展项目现有 `services/hooks` 机制，新增一个 hook 事件作为长期记忆自动提取时机判断接口。
- [x] (2026-06-07) 新增 `services/memory/` 长期记忆服务模块，包含路径、frontmatter、instruction loader、auto-memory store、scan、prompt、selection、context preparer 和 extraction 代码。
- [x] (2026-06-07) 将 Layer 1 instruction section 和 Layer 2 auto-memory prompt/index section 接入 `DynamicPromptAssembler`，并用 provider fingerprint 保持 prompt section cache 正确失效。
- [x] (2026-06-07) 将相关记忆正文选择接入模型调用前的 context preparation，复用现有 attachment projector 的 `relevant_memories` 类型。
- [x] (2026-06-07) 扩展 permission policy，让 `.onecode/memory/` 与 session memory、tool results 一样有受控读写豁免，同时保持 deny-first 原则。
- [x] (2026-06-07) 增加长期记忆自动提取服务，复用 `SubagentRunner` 的 fork child，并加入 `purpose="long_term_memory_extraction"` 的更窄权限模式。
- [x] (2026-06-07) 增加 CLI 装配和状态展示，让 `/status` 显示长期记忆目录、索引状态、最近提取状态和相关记忆注入摘要。
- [x] (2026-06-07) 补充单元测试并运行定向 pytest、compileall 和全量测试；手动 CLI provider 验证未执行。

## Surprises & Discoveries

- Observation: 当前 active exec plan 目录为空，原 `mcp-discovery-connection-tools.md` 与 `write-file-tool-lightweight.md` 已出现在 `docs/exec-plans/completed/`，但 git status 显示它们是从 active 移动到 completed 的未提交变化。
  Evidence: `Get-ChildItem docs\exec-plans -Recurse -Force` 显示 active 目录无文件，completed 目录包含这两个文件；`git status --short` 显示 active 中两个文件为 deleted、completed 中两个文件为 untracked。此计划不修改这些用户或其他进程造成的变更。
- Observation: OneCode 已有 Session Memory，但它只服务当前会话压缩连续性，不是跨会话长期记忆。
  Evidence: `docs/design-docs/context-and-prompt-architecture.md` 说明 Session Memory 路径是 `.onecode/<session_id>/session-memory.md`，用途是 compact 后继续当前会话；`services/compaction/session_memory.py` 中 `SessionMemoryExtractionService` 只维护单个 session-local Markdown 文件。
- Observation: OneCode 已有可复用的安全边界来承载长期记忆提取。
  Evidence: `services/subagents/runner.py` 已支持通过 `SubagentRequest.metadata["purpose"]` 进入特殊 fork mode；`services/permissions/policy.py` 已有 `memory_extraction_agent` 对单个 session memory 文件的硬限制；`services/attachments/projector.py` 已能投影 `relevant_memories` attachment。

- Observation: 当前测试环境没有启用 `pytest-asyncio`。
  Evidence: 新增长期记忆 selector/extraction 测试最初使用 `pytest.mark.asyncio` 时失败，提示 async def functions are not natively supported；按仓库既有测试风格改为 `asyncio.run(...)` 后通过。

## Decision Log

- Decision: 第一版采用 OneCode 原生命名：`ONECODE.md`、`.onecode/ONECODE.md`、`.onecode/rules/*.md`、`ONECODE.local.md` 和用户目录 `~/.onecode`。
  Rationale: 用户明确要求不使用 Claude 命名。这样可以避免读取或污染真实 Claude Code 配置，同时让 OneCode 的项目约定和数据目录保持一致。
  Date/Author: 2026-06-07 / Codex

- Decision: 长期记忆目录固定为当前 workspace 内的 `.onecode/memory/`。
  Rationale: 用户明确选择 `<workspace>/.onecode/memory/`。这让记忆与项目同址，便于检查、备份和权限判断，也符合 OneCode 已经把 session transcript、tool results 和 session memory 放在 `.onecode/` 下的实现习惯。
  Date/Author: 2026-06-07 / Codex

- Decision: 显式 remember/forget 由主 agent 直接写 memory，自动补漏由 fork extraction agent 写 memory，并用本轮 memory 写入检测互斥。
  Rationale: 显式用户意图应该出现在主对话工具流中，用户能看到文件写入；自动提取只用于补漏，不能重复处理主 agent 已经保存的同一段新消息。互斥机制以 message cursor 和 memory path 写入记录为依据。
  Date/Author: 2026-06-07 / Codex

- Decision: 自动提取第一版阻塞当前 turn 结束流程，不引入真正 background task。
  Rationale: 当前技术债 TD-010 记录 subagent 缺少 background lifecycle。第一版阻塞可以保持 transcript、trace、权限和退出行为简单可测；后续 background 化应单独设计任务生命周期、取消、flush 和 UI 状态。
  Date/Author: 2026-06-07 / Codex

- Decision: 相关性搜索使用 LLM side-query，不实现 keyword fallback。
  Rationale: 用户明确要求使用 LLM selector，且不需要 keyword fallback。失败时应记录 trace 并跳过相关正文注入，不应用不可靠的关键词规则强行选择。
  Date/Author: 2026-06-07 / Codex

- Decision: 记忆类型固定为 `user`、`feedback`、`project` 和 `reference`，但去掉 team/private scope。
  Rationale: 用户不需要团队记忆。四类 taxonomy 仍能覆盖用户画像、协作反馈、项目背景和外部引用，且与参考材料的 individual-only 模式一致。
  Date/Author: 2026-06-07 / Codex

- Decision: Dream consolidation 不进入第一版。
  Rationale: Dream 需要时间门、session 扫描、lock、任务状态和较复杂的 fork agent 行为。第一版先交付可读、可写、可选择、可验证的长期记忆闭环。
  Date/Author: 2026-06-07 / Codex

- Decision: 扩展项目现有 `services/hooks` 机制，新增一个自然停止后的 hook 事件，用它判断自动提取的触发时机。
  Rationale: 长期记忆提取是 turn 生命周期上的扩展，不应通过 `AgentLoop` 持有 memory-specific extractor 字段硬接入。参考 `docs/references/主循环和重建上下文/query.ts` 的行为，相关记忆读取在 turn 开始预取并在后续上下文中消费，自动提取在自然停止后的 stop hook 阶段触发。OneCode 已有 `services/hooks/events.py::HookEvent` 和 `services/hooks/registry.py::HookRegistry`，第一版应在这个既有机制上增加一个事件，让 runtime 在明确的自然停止点发布 payload，长期记忆服务通过注册 callback 决定是否提取。
  Date/Author: 2026-06-07 / Codex

## Outcomes & Retrospective

已完成长期记忆第一版代码闭环：新增 `services/memory/`，接入 prompt section、相关记忆 attachment preparer、`TurnStopped` hook、受限 fork extraction child、permission policy 受控豁免和 CLI `/status` 状态展示。

新增测试覆盖 instruction loader、store、selector、extraction 和 permission policy；全量测试结果为 `273 passed`。已运行 `uv run python -m compileall core services infrastructure prompts tools ui` 和 `uv run python -m pytest tests -q`。未执行需要真实 provider `.env` 的手动 CLI 验证。

与参考实现一致的行为包括：OneCode 原生命名、workspace-local `.onecode/memory/`、`@include` 深度限制、HTML 注释过滤、`MEMORY.md` 截断、LLM side-query 选择最多 5 个相关 topic、相关正文作为 memory attachment 注入、自然停止后通过 hook 触发受限 fork 提取、主 agent memory 写入与自动提取互斥。

因 OneCode 架构而不同的行为包括：长期记忆接入现有 `DynamicPromptAssembler`、`AttachmentContextPreparer`、`HookRegistry`、`SubagentRunner` 和 `PermissionPolicy`，没有在 `core/loop.py` 中增加 memory-specific service 字段；loop 只发布通用 `TurnStopped` lifecycle hook。下一阶段仍建议单独设计 background extraction 和 Dream consolidation。

## Context and Orientation

OneCode 是 Python code agent runtime。主循环在 `core/loop.py`，它只负责编排：接收用户输入、构建模型上下文、调用模型、执行工具、写回消息和设置 transition。长期记忆不能作为工具名分支或 provider-specific 分支进入 `core/loop.py`。

每轮模型调用前，`core/context_engine.py::ContextEngine.build_for_model()` 从 `MessageStore` 读取当前消息，调用 context preparer 生成模型可见消息，调用 prompt assembler 生成 system prompt，再调用 tool schema provider 生成工具 schema。长期记忆读取链路应接入这里的 prompt 和 context 边界。

系统提示词组装在 `prompts/assembler.py` 和 `prompts/sections.py`。`DynamicPromptAssembler` 当前根据 `PromptRuntimeContext` 渲染 identity、behavior rules、workspace state、available tools、available skills、MCP instructions 和每个工具自己的 prompt。长期记忆的 Layer 1 指令和 Layer 2 `MEMORY.md` 索引应成为新的 prompt sections，使用 stable fingerprint 缓存。

附件投影在 `services/attachments/projector.py`。它已经支持 `relevant_memories` 和 `nested_memory` attachment 类型，会把内部 attachment message 投影为模型可见 user notice。相关记忆正文不应直接塞入 system prompt；应在 context preparation 阶段作为 memory attachment 注入，使 `MEMORY.md` 索引保持较稳定，正文按需进入当前 turn。

Session Memory 当前位于 `services/compaction/session_memory.py`。它维护 `.onecode/<session_id>/session-memory.md`，服务当前会话跨 compaction 的连续性。长期记忆不同：它位于 `.onecode/memory/`，跨 session 存活，记录未来对话仍有价值的偏好、反馈、项目事实和外部引用。

Subagent 运行在 `services/subagents/runner.py`。当前已有 `purpose="session_memory_extraction"` 的特殊模式，child 只暴露 `edit_file` 并只能编辑一个 `session-memory.md` 文件。长期记忆自动提取应复用同一思想，但允许 child 读候选 memory 和工作区上下文，并只允许写 `.onecode/memory/` 内的 Markdown 文件。

权限策略在 `services/permissions/policy.py`。它是 deny-first：read-only subagent、工具 deny/disabled、guard deny 和项目 deny 必须先于 allow。长期记忆目录豁免不能绕过 deny-first。豁免的含义是：`.onecode/memory/` 像 `.onecode/<session_id>/tool-results/` 和 session memory 一样，是 runtime 管理目录，主 agent 在显式 remember/forget 时可以用正常 file tools 读写它，内部提取 agent 可以在更窄权限下写它。

参考材料位于 `docs/references/s09_memory/` 和 `docs/references/主循环和重建上下文/query.ts`。本计划吸收的行为包括：层级指令加载、`@include` 最多 5 层、conditional rules 的 `paths:` frontmatter、HTML 注释过滤、`MEMORY.md` 200 行 / 25KB 截断、LLM side-query 从 memory catalog 选择最多 5 个相关文件、自然停止后的 hook 阶段触发 fork agent 自动提取、主 agent 写入与 fork 提取互斥。不吸收的行为包括团队记忆、Claude 路径命名、feature flags、remote mode、Dream consolidation 和真正后台任务。

## Plan of Work

第一步是新增长期记忆服务模块。创建 `services/memory/`，至少包含 `__init__.py`、`types.py`、`frontmatter.py`、`paths.py`、`instruction_loader.py`、`auto_store.py`、`scan.py`、`prompt.py`、`selector.py` 和 `extraction.py`。这些模块属于 services 层，不得 import `core.loop`，不得 import 具体 provider adapter。它们可以依赖 `RuntimeState`、`MessageStore` 的普通 message shape、`TraceRecorder`、`SubagentRequest`、现有 `HookRegistry` 和 provider-neutral `ModelClient` protocol。

`services/memory/types.py` 定义稳定数据结构。需要有 `MemoryKind` 或等价 literal，值为 `user`、`feedback`、`project`、`reference`。需要有 `InstructionMemoryFile`，保存 `path`、`source_layer`、`content`、`globs`、`parent`、`transformed` 和 `load_reason`。需要有 `LongTermMemoryFile`，保存 `path`、`name`、`description`、`type`、`mtime` 和正文预览。需要有 `MemoryPaths`，保存 `workspace`、`memory_dir` 和 `entrypoint`。

`services/memory/frontmatter.py` 实现最小 YAML frontmatter parser。第一版只需要解析文件开头的 `---` 到下一行 `---`，支持简单 `key: value`，其中 `paths:` 可以是一行字符串或逗号分隔字符串。不要引入 PyYAML 依赖，除非项目已有该依赖并确认适合；简单 parser 足以覆盖本计划需要的字段。解析后正文应去掉 frontmatter。

`services/memory/paths.py` 负责路径。函数建议为 `memory_paths(workspace: Path) -> MemoryPaths`，返回 `<workspace>/.onecode/memory/` 和 `<workspace>/.onecode/memory/MEMORY.md`。另加 `is_auto_memory_path(path: Path, workspace: Path) -> bool` 和 `normalize_memory_path(path, workspace)`，供 permission policy、executor side effects 和 extraction 互斥检测使用。

`services/memory/instruction_loader.py` 实现 Layer 1。加载顺序从低优先级到高优先级为：用户 source，project source，local source。用户 source 读取 `~/.onecode/ONECODE.md` 和 `~/.onecode/rules/*.md`。Project source 从 workspace root 到当前 CWD 遍历每个目录，读取 `ONECODE.md`、`.onecode/ONECODE.md` 和 `.onecode/rules/*.md`。Local source 从 workspace root 到当前 CWD 遍历 `ONECODE.local.md`。最终内容按这个顺序拼接，后加载的内容出现在后面，以表达更高优先级。

`instruction_loader.py` 必须支持 `@include`。在 Markdown 文本中，形如 `@./path/to/file` 或 `@../notes.md` 的 include 应相对当前文件解析。最多递归 5 层，重复文件只加载一次，循环 include 应停止并记录 trace 或 metadata。项目 source 的 include 默认限制在 workspace 内；用户 source 的 include 默认限制在用户 OneCode home 内。第一版只允许文本和 Markdown 类文件，遇到二进制或不可读文件应跳过并记录错误 metadata，不中断 prompt 构建。

`instruction_loader.py` 还必须实现 HTML 注释过滤。过滤规则采用参考实现方向：删除 Markdown 中独立 block 形式的 `<!-- ... -->`，使模型看到的内容可以不同于磁盘内容。实现不需要完全复制 TypeScript lexer，但要有测试覆盖单行 block、多行 block 和普通段落中的文本。Inline comment 是否删除应在代码注释中写清楚；建议第一版删除所有 `<!-- ... -->` span，保持行为简单。

Conditional rules 的 `paths:` frontmatter 按参考实现建模：`.onecode/rules/*.md` 中带 `paths:` 的规则只有在目标路径匹配时加载，不带 `paths:` 的规则作为 unconditional rules 加载。OneCode 没有编辑器“当前文件”概念，因此第一版定义“目标路径”为当前 turn 中模型明确获得或操作过的文件路径：文件附件路径、`RuntimeState.metadata["files_read"]`、`RuntimeState.metadata["files_changed"]` 和本轮工具 target 中的文件路径。`InstructionMemoryProvider` 应暴露 API 接收 `target_paths`，并在 context preparation 或 prompt assemble 前从 state metadata 读取这些路径。匹配语义应和参考实现一致：project rules 的 glob 相对包含 `.onecode` 的目录；user rules 的 glob 相对当前 workspace；使用 Python `fnmatch` 或 `pathlib.PurePath.match` 时必须补测试，确保 `src/*.py` 和 `**/*.py` 行为符合预期。

第二步是实现 Auto Memory store 和 prompt。`services/memory/auto_store.py` 管理 `.onecode/memory/`，提供 `ensure_exists()`、`read_entrypoint()`、`write_topic_file()`、`rebuild_entrypoint()`、`scan_topic_files()` 和 `record_memory_write()` 等函数。`MEMORY.md` 是索引，不带 frontmatter；每行格式为 `- [Title](file.md) - one-line hook` 或等价 ASCII 分隔符。Topic 文件使用 Markdown + frontmatter：

    ---
    name: user-preference-tabs
    description: User prefers tabs for indentation
    type: user
    ---

    User prefers using tabs, not spaces, for indentation.
    **Why:** Consistency with existing codebase conventions.
    **How to apply:** Use tabs when writing or editing files when the codebase allows it.

`services/memory/prompt.py` 构造 Layer 2 system prompt。它应说明长期记忆目录、四类 taxonomy、什么应该保存、什么不应该保存、显式 remember/forget 的行为、保存步骤和读取规则。必须明确：不要保存可从代码、git history 或当前 project files 直接推导出的事实；不要把当前任务计划或 todo 存进长期记忆；不要把大量正文写进 `MEMORY.md`；更新或删除过时记忆优先于重复新增。`MEMORY.md` 内容注入 system prompt 前必须截断到 200 行和 25KB，两种限制任一触发时附加 warning，要求把细节移动到 topic files。

第三步是把 prompt sections 接入 `prompts/`。编辑 `prompts/runtime_context.py`，增加可选 fields，例如 `instruction_memory: str = ""`、`auto_memory_prompt: str = ""` 或更结构化的 provider 结果。编辑 `prompts/assembler.py::DynamicPromptAssembler`，让构造函数接受 `instruction_memory_provider` 和 `long_term_memory_provider`，在 `_build_context()` 中按当前 state/cwd 获取内容。编辑 `prompts/sections.py`，新增 `instruction_memory_section(context)` 和 `long_term_memory_section(context)`，放在 behavior rules 之后、available tools 之前。fingerprint 应包含文件路径、mtime 或内容 hash、target paths、entrypoint 内容 hash 和 prompt version。空内容不渲染。

第四步是实现相关性选择。`services/memory/scan.py` 扫描 `.onecode/memory/**/*.md`，排除 `MEMORY.md`，最多读取 200 个 topic 文件的 frontmatter，按 mtime 倒序。`services/memory/selector.py` 提供 async 函数，例如 `select_relevant_memories(model_client, messages, catalog, max_items=5) -> tuple[Path, ...]`。它构造一个 provider-neutral side-query：输入是最近对话摘要和 catalog，每条 catalog 包含 filename、name、description、type 和 mtime；输出要求模型返回 JSON object，例如 `{"selected_memories": ["user_role.md"]}`。只接受 catalog 中存在的相对文件名，最多 5 个。若 side-query 抛出 provider error、返回非 JSON、返回未知文件或超时，应记录 trace 并返回空 tuple，不做 keyword fallback。

第五步是把相关记忆正文注入 context。推荐新增一个 memory-aware context preparer，包装现有 `AttachmentContextPreparer` 或在 CLI 装配中组合 preparer。该 preparer 在每轮模型调用前读取当前消息和 state，调用 selector，读取选中文件正文，并追加 durable 或 synthetic attachment message，type 为 `relevant_memories`。现有 `services/attachments/projector.py` 会把该 attachment 投影为 user notice。读取正文时每个文件限制 200 行或 4096 字符，总注入预算建议 60KB；超限时添加简短 warning。为避免同一文件每轮重复占据预算，state metadata 可记录 `long_term_memory_surface_paths`，selector 入参可以过滤已经 surfaced 的路径，但第一版不强制。

第六步是权限和 guard 豁免。编辑 `services/guard` 或 `services/permissions/policy.py` 中适合的边界，让 `.onecode/memory/` 被视作 runtime 管理的 memory directory。普通主 agent 在显式 remember/forget 时仍通过 `read_file`、`write_file` 和 `edit_file` 调用，仍要经过 schema validation、classification、guard、permission 和 hook。豁免只应避免 `.onecode` 受保护目录 ask 阻断 memory 的正常读写；项目级 deny、工具级 deny、guard deny 和 read-only agent deny 仍必须生效。需要补测试证明：项目 settings deny `write_file` 时主 agent 不能写 memory；普通写 `.onecode/settings.json` 仍 ask 或 deny；写 `.onecode/memory/foo.md` 在未被 deny 时允许。

第七步是记录本轮主 agent memory 写入。`RegistryToolExecutor` 已在工具成功后更新文件状态和 trace。扩展 executor side effects 或新增 hook，检测成功的 `write_file` / `edit_file` 目标是否在 `.onecode/memory/` 内。若是，更新 `RuntimeState.metadata["long_term_memory_writes"]`，至少记录 `turn_count`、`message_count`、`path` 和时间。这个记录用于自动提取互斥。

第八步是实现长期记忆自动提取，并扩展现有 hooks。编辑 `services/hooks/events.py`，在现有 `HookEvent` 中新增一个自然停止后的事件，例如 `TURN_STOPPED = "TurnStopped"` 或更明确的 `MEMORY_EXTRACTION_CHECK = "MemoryExtractionCheck"`。优先选择表达生命周期位置的名称，而不是只表达 memory 行为；推荐 `TURN_STOPPED`，因为它对应参考 `query.ts` 中自然停止后的 stop hook 阶段，未来也可供其他 stop-time 扩展复用。不要新建另一套 hook registry。

在 `core/loop.py` 的自然停止路径运行这个现有 `HookRegistry` 事件。触发点应在确认没有 tool calls、准备设置 `TransitionReason.COMPLETED` 之前或之后，但必须只在模型产生真实 assistant response 且本轮没有实际 tool calls 时触发；不能在 API error、tool-use continuation、fork compact 或内部 memory extraction child 中误触发。payload 必须包含 `messages`、`state`、`assistant_message`、`tool_calls`、`usage`、`query_source` 或等价 metadata、以及本轮 memory 写入记录。该 hook 的职责是让注册的长期记忆 callback 判断是否应触发提取，并调用 `LongTermMemoryExtractionService`；它不负责把记忆正文注入上下文。

`services/memory/extraction.py` 定义 `LongTermMemoryExtractionPolicy`、`LongTermMemoryExtractionService` 和 `should_extract_long_term_memory()`。服务由注册到现有 `HookRegistry` 的 callback 调用，而不是作为 `AgentLoop` 的专用依赖字段直接挂入主循环。不要在 `core/loop.py` 写长期记忆细节；loop 只发布新的 hook event 和 provider-neutral payload。第一版触发条件是：主 agent、非 compact/fork child、auto memory enabled、最后响应没有工具调用或达到轮次间隔、且本轮主 agent 没有写 `.onecode/memory/`。由于用户确认第一版阻塞，hook callback 调用 extraction service 时应 `await subagent_runner.run(...)`。

提取服务必须维护 cursor，语义等同参考实现的 `lastMemoryMessageUuid`。OneCode message 若没有真实 uuid，可用 message metadata 中的 transcript uuid，缺失时退化为 message index。每次成功提取或因主 agent 已写 memory 而跳过时，把 cursor 推进到最新消息。若提取正在运行且又被调用，第一版可以简单跳过并记录 `skipped_running`；因为它是阻塞实现，正常不会重入。后续 background 化时再实现 stash trailing run。

第九步是扩展 `SubagentRunner`。在 `services/subagents/runner.py` 中识别 `request.metadata["purpose"] == "long_term_memory_extraction"`。child state 应设置 `long_term_memory_extraction_agent=True`、`allowed_memory_dir=<workspace>/.onecode/memory`，并隐藏 `agent`、`skill`、所有 MCP 工具和任何非必要工具。child registry 第一版只暴露 `read_file`、`grep`、`glob`、`write_file` 和 `edit_file`；如果保留 `bash`，必须只允许 read-only Bash，而当前 BashTool 只保守分类有限 AST，建议第一版不暴露 bash，避免扩大权限面。`PermissionPolicy` 增加长期记忆提取决策：读工具允许读取 workspace 内文件和 memory 目录，写工具只能写 `.onecode/memory/` 内 `.md` 文件，不能写 `.onecode/settings.json`、session transcript 或其他 `.onecode` 目录。

第十步是提取 prompt。构造 prompt 时预先注入 existing memory manifest，避免 child 浪费 turn 做 `ls`。prompt 要求 child 只基于 cursor 之后的新消息更新长期记忆；不要调查代码来验证用户说法；先并行读取可能更新的 memory 文件，再并行 write/edit；最多 5 turns。保存规则与 system prompt 一致：topic file 使用 frontmatter，`MEMORY.md` 只添加一行索引；更新已有 topic 优先于新增；显式 forget 应删除或清空相关 topic 并重建 index；不要保存当前任务计划、短期 todo、可从源码推导的事实或敏感秘密。

第十一步是 CLI 装配。编辑 `ui/cli/app.py::build_runtime()`，创建 `LongTermMemoryStore`、instruction provider、long-term memory prompt provider、selector/context preparer 和 extraction service。把 extraction service 作为 callback 注册到现有 `hooks: HookRegistry` 的新自然停止事件上。`DynamicPromptAssembler` 构造时注入 providers。`ContextEngine` 的 preparer 应组合 compaction、attachment projection和 memory relevant attachment；组合顺序建议为：先 compaction-aware preparer 处理消息治理，再 memory preparer 添加 relevant memory attachment，最后 attachment projector 在 provider payload 前投影。若现有 `AttachmentContextPreparer` 已包装 compaction service，则新增组合类以保持顺序清晰。

第十二步是 CLI 状态。编辑 `ui/cli/types.py`，在 `CliRuntime` 上增加 `long_term_memory_store` 和 `long_term_memory_extractor` 或稳定状态对象。编辑 `ui/cli/renderer.py`，让 `/status` 显示 memory dir、`MEMORY.md` 是否存在、topic file count、最近 selector 状态和最近 extraction 状态。不要在 status 中打印完整 memory 内容。

第十三步是测试。新增 `tests/test_long_term_memory_instruction_loader.py`，覆盖加载顺序、用户/project/local 层、`@include` 深度、include 循环、HTML 注释过滤、frontmatter `paths:` 条件规则和 glob 相对目录。新增 `tests/test_long_term_memory_store.py`，覆盖 topic frontmatter 解析、`MEMORY.md` 截断、scan 排除 entrypoint、rebuild index 和 workspace `.onecode/memory/` 路径判断。新增 `tests/test_long_term_memory_prompt.py`，覆盖 prompt section 注入、fingerprint 随文件变化失效、空 memory 不渲染或只渲染行为说明。新增 `tests/test_long_term_memory_selector.py`，用 fake model client 验证 JSON selection、最多 5 个、未知文件过滤、provider error 返回空。新增 `tests/test_long_term_memory_extraction.py`，用 fake subagent runner 验证 cursor、主 agent memory 写入互斥、metadata purpose、allowed memory dir 和阻塞执行。扩展 `tests/test_tool_registry_and_executor.py` 或新增 permission 测试，覆盖 memory 目录豁免和 deny-first。

## Concrete Steps

从仓库根目录运行所有命令：

    cd D:\study\OneCode

先确认工作树状态，避免覆盖他人变更：

    git status --short

预期可能看到此前 active plan 移动到 completed 的未提交变化。本计划实现时不要还原这些变化，除非用户明确要求。

创建服务模块和测试文件后，运行定向测试：

    uv run python -m pytest tests/test_long_term_memory_instruction_loader.py -q
    uv run python -m pytest tests/test_long_term_memory_store.py -q
    uv run python -m pytest tests/test_long_term_memory_selector.py -q
    uv run python -m pytest tests/test_long_term_memory_extraction.py -q

运行编译检查：

    uv run python -m compileall core services infrastructure prompts tools ui

最后运行全量测试：

    uv run python -m pytest tests -q

手动 CLI 验证需要 `.env` 中已有可用 provider 设置。创建一个临时 memory 场景：

    mkdir .onecode\memory

写入 `.onecode\memory\MEMORY.md`：

    - [User prefers terse summaries](user_terse_summaries.md) - User dislikes repeated end-of-turn summaries.

写入 `.onecode\memory\user_terse_summaries.md`：

    ---
    name: user_terse_summaries
    description: User dislikes repeated end-of-turn summaries
    type: feedback
    ---

    User prefers concise final responses and does not want repeated summaries of obvious diffs.

启动 CLI：

    uv run python -m ui.cli.app

输入 `/status`，预期看到长期记忆目录和 topic file count。输入一个与“final response style”相关的问题，trace 或 debug 状态应显示 selector 选中了 `user_terse_summaries.md`，模型上下文中出现 `[memory attachment]`。输入 `remember I prefer single quotes in Python examples`，预期主 agent 使用 `write_file` 或 `edit_file` 写 `.onecode/memory/` 下 topic 文件并更新 `MEMORY.md`。同一轮完成后自动提取应因为检测到主 agent memory write 而跳过。

## Validation and Acceptance

功能验收以可观察行为为准。没有 `ONECODE.md`、`.onecode/rules/` 或 `.onecode/memory/` 时，OneCode 正常启动，prompt 构建不报错，`/status` 显示长期记忆为空或未初始化。

Layer 1 验收要求：用户 `~/.onecode/ONECODE.md`、根目录 `ONECODE.md`、子目录 `.onecode/ONECODE.md`、`.onecode/rules/*.md` 和 `ONECODE.local.md` 按低到高优先级拼接；`@include` 文件先于包含它的文件进入结果或有明确稳定顺序；超过 5 层 include 停止；HTML comment 不进入模型可见内容；带 `paths:` 的 rule 只在目标路径匹配时进入 prompt。

Layer 2 读链路验收要求：`.onecode/memory/MEMORY.md` 进入 system prompt，超过 200 行或 25KB 时被截断并附加 warning；topic files 不直接进入 system prompt；LLM selector 每轮最多选择 5 个 topic；被选中的 topic 正文作为 `relevant_memories` attachment 进入模型可见 messages；selector 失败时本轮继续，只记录 trace。

显式写入验收要求：当用户要求 remember，主 agent 能通过正常 `write_file` 或 `edit_file` 写 `.onecode/memory/*.md` 和 `MEMORY.md`，不被 `.onecode` 受保护目录 ask 阻断；当项目设置 deny `write_file` 或 `edit_file` 时，deny 仍生效；当主 agent 本轮写过 memory，自动 extraction 不再重复处理同一段消息。

自动提取验收要求：自然停止后，现有 `HookRegistry` 发布新增 hook event，并由注册的长期记忆 callback 决定是否调用 `LongTermMemoryExtractionService`。满足条件时，service 阻塞运行 fork child；child request metadata 包含 `purpose="long_term_memory_extraction"` 和 `allowed_memory_dir`；child registry 只包含允许工具；child 写 memory 目录外文件被 permission policy deny；成功后 cursor 推进；失败时记录 trace，不破坏主会话消息链。

安全验收要求：长期记忆能力不在 `core/loop.py` 中添加工具名特例；guard deny、project deny、tool deny、read-only subagent deny 都不能被 memory 目录豁免覆盖；`.onecode/settings.json`、`.onecode/<session>/messages.jsonl` 和 `.git` 仍不能被长期记忆提取 agent 修改。

测试验收要求：新增和修改的 tests 在变更前应至少有一部分失败，变更后通过。最终运行 `uv run python -m pytest tests -q` 应通过；若存在与本计划无关的已知失败，必须在本计划的 `Surprises & Discoveries` 和 `Outcomes & Retrospective` 中记录命令、失败名称和判断依据。

## Idempotence and Recovery

所有文件扫描和目录创建必须幂等。`LongTermMemoryStore.ensure_exists()` 可以重复运行，不能清空已有 memory。`rebuild_entrypoint()` 应从当前 topic files 生成稳定排序索引，重复运行不产生重复行。写 topic 文件时，slug 冲突应更新已有文件或生成稳定后缀，但不能覆盖无关文件。

如果 `MEMORY.md` 损坏或 topic frontmatter 不完整，scan 应跳过坏文件并记录 trace，不应阻止 CLI 启动。实现者可以提供修复建议或在 `/status` 中显示 warning，但第一版不要自动删除用户文件。

如果 LLM selector 或 extraction provider 调用失败，主 agent 对话应继续。selector 失败等价于本轮没有相关 topic 正文。extraction 失败只更新 extraction status 和 trace，不推进 cursor，下一轮可以重试。

如果权限策略误拦截 `.onecode/memory/`，优先修正 permission policy 测试，不要通过在 tool handler 内绕过 guard 或 permission 来解决。所有写入仍必须走普通 executor。

## Artifacts and Notes

参考实现行为摘要：

    Layer 1:
      User -> Project root-to-cwd -> Local root-to-cwd -> AutoMem index
      @include max depth: 5
      conditional rules: frontmatter paths
      HTML comments filtered
      MEMORY.md truncated at 200 lines or 25KB

    Layer 2:
      memory dir: .onecode/memory/
      entrypoint: MEMORY.md
      topic files: Markdown + frontmatter
      types: user, feedback, project, reference
      relevant selection: LLM side-query, max 5
      automatic write: restricted fork child, max 5 turns
      first version excludes: team memory, Dream, background task lifecycle

OneCode adaptation notes:

    The reference implementation uses ~/.claude/projects/<slug>/memory/.
    This plan intentionally uses <workspace>/.onecode/memory/ because the user selected workspace-local storage.

    The reference implementation can know a target file path from nested directory memory loading.
    OneCode first version derives target paths from attachments, files_read, files_changed, and known tool targets because the runtime has no editor-current-file concept.

    The reference implementation has background extraction.
    OneCode first version blocks until extraction completes because current subagent architecture does not yet provide background lifecycle.

## Interfaces and Dependencies

In `services/memory/paths.py`, define:

    def memory_paths(workspace: Path | str) -> MemoryPaths
    def is_auto_memory_path(path: Path | str, workspace: Path | str) -> bool
    def is_auto_memory_markdown_path(path: Path | str, workspace: Path | str) -> bool

In `services/memory/instruction_loader.py`, define:

    class InstructionMemoryLoader:
        def load(self, state: RuntimeState, cwd: Path, target_paths: tuple[Path, ...] = ()) -> InstructionMemoryResult: ...

`InstructionMemoryResult` should expose rendered text and a fingerprint. The rendered text is what prompt sections include; the fingerprint is used by prompt cache.

In `services/memory/auto_store.py`, define:

    class LongTermMemoryStore:
        @property
        def memory_dir(self) -> Path: ...
        @property
        def entrypoint_path(self) -> Path: ...
        def ensure_exists(self) -> None: ...
        def read_entrypoint(self) -> str: ...
        def scan(self) -> tuple[LongTermMemoryFile, ...]: ...
        def read_topic(self, relative_path: str | Path, *, max_lines: int = 200, max_chars: int = 4096) -> str: ...

In `services/memory/selector.py`, define:

    class RelevantMemorySelector:
        async def select(self, messages: tuple[dict[str, Any], ...], state: RuntimeState, catalog: tuple[LongTermMemoryFile, ...]) -> tuple[LongTermMemoryFile, ...]: ...

The selector depends on the existing provider-neutral `ModelClient`; it must not import a concrete OpenAI or Anthropic adapter.

In `services/hooks/events.py`, extend the existing enum:

    class HookEvent(StrEnum):
        ...
        TURN_STOPPED = "TurnStopped"

The existing `services/hooks/registry.py::HookRegistry` remains the only hook registry. Do not create `services/memory/hooks.py`.

In `services/memory/extraction.py`, define:

    class LongTermMemoryExtractionService:
        async def maybe_extract_after_model_response(
            self,
            messages: tuple[dict[str, Any], ...],
            state: RuntimeState,
            *,
            assistant_message: dict[str, Any],
            tool_calls: tuple[Any, ...],
            usage: Any | None = None,
        ) -> None: ...

The signature intentionally mirrors `SessionMemoryExtractionService`, but it should be invoked by a callback registered on the existing `HookRegistry` for `HookEvent.TURN_STOPPED`, not by a memory-specific field on `AgentLoop`.

In `services/subagents/runner.py`, add an internal mode:

    request.metadata["purpose"] == "long_term_memory_extraction"

This mode should configure child metadata:

    child_state.metadata["long_term_memory_extraction_agent"] = True
    child_state.metadata["allowed_memory_dir"] = str(memory_dir.resolve())

In `services/permissions/policy.py`, add a corresponding deny-first branch after tool/project/guard deny checks and before ordinary ask/allow handling. The branch must allow only the intended read tools and memory-dir Markdown writes.

## Revision Notes

- 2026-06-07 / Codex: 初始版本。根据用户确认的范围撰写完整中文 ExecPlan，选择 OneCode 原生命名、workspace-local `.onecode/memory/`、主 agent 显式写与 fork 自动提取互斥、阻塞式第一版提取、LLM selector、固定四类记忆，并明确不实现团队记忆和 Dream consolidation。
- 2026-06-07 / Codex: 按用户修订改为扩展项目已有 `services/hooks` 机制，在 `HookEvent` 中新增自然停止后的 hook event；规定 `LongTermMemoryExtractionService` 由注册到现有 `HookRegistry` 的 callback 调用，而不是新增 `services/memory/hooks.py` 或作为 `AgentLoop` 的专用依赖字段。
