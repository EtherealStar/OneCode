# 建立动态 system prompt 架构骨架

本 ExecPlan 是一个活文档。实现过程中必须持续维护 `Progress`、`Surprises & Discoveries`、`Decision Log` 和 `Outcomes & Retrospective`。

本计划遵守仓库根目录的 `PLANS.md`，并把必要背景写入本文，使后续执行者只阅读本文和当前工作区也能完成实现。

## Purpose / Big Picture

完成本改动后，OneCode 不再依赖空的静态 system prompt，也不再把 prompt 规则散落在 CLI 或主循环中。OneCode 会拥有长期可演化的 `prompts/` 架构骨架：稳定 section、动态 section、工具 prompt section、workspace 状态 section 和 section 级缓存都通过统一 assembler 组装成模型可见 system prompt。

用户可通过 CLI 或测试看到行为变化：启动 runtime 后，`ContextEngine.build_for_model()` 生成的 `ContextSnapshot.system_prompt` 包含 OneCode 身份、行为规则、当前工作目录、已读文件、可用工具和工具使用规则。CLI 运行时会使用这个新 assembler，而不是继续装配旧的 `StaticPromptAssembler`。如果工具 registry 或 runtime state 改变，下一次模型调用会得到反映真实状态的新 prompt；如果状态没有改变，section 缓存会复用已经组装好的 section 文本。

本计划是重构计划。它不为了兼容旧的空静态 prompt 路径而保留无用代码。完成后，旧的默认空 prompt 装配应被删除或降级为测试专用 helper，生产 CLI 必须走新的 `prompts/` assembler。

## Progress

- [x] (2026-06-04 11:30Z) 阅读 `AGENTS.md`、`PLANS.md`、`architecture.md`、`docs/design-docs/core-beliefs.md`、`docs/design-docs/tool-design-guidelines.md`、当前 active plan 状态、tech debt、`core/context_engine.py`、`services/tools/registry.py`、`services/context/snapshot.py` 和现有工具 prompt。
- [x] (2026-06-04 11:30Z) 记录用户决策：建立长期 prompt 架构骨架；包含行为规则；同步推进 deny-aware prompt/schema 裁剪；workspace 状态只放对项目开发有用的信息；暂不实现用户偏好和语言偏好；采用 section 化；增加快照测试；接入 CLI 并删除旧无用路径；第一版实现 section 缓存。
- [x] (2026-06-04 12:10Z) 新增 `prompts/` 包，定义 prompt runtime context、section 类型、section cache 和 assembler。
- [x] (2026-06-04 12:10Z) 扩展工具 registry，使工具 schema 与工具 prompt section 能基于同一个 visible descriptor 视图保持一致。
- [x] (2026-06-04 12:10Z) 更新 `core/context_engine.py` 的默认装配，使未显式传入 prompt assembler 时也生成非空动态 system prompt；`StaticPromptAssembler` 仅保留为测试 helper。
- [x] (2026-06-04 12:10Z) 更新 `ui/cli/app.py` 和 `ui/cli/types.py`，让 CLI runtime 与 resume 后的新 loop 都使用新的动态 prompt assembler。
- [x] (2026-06-04 12:10Z) 补充单元测试和快照式字符串断言，覆盖 section 顺序、缓存命中、workspace 状态、工具 prompt、deny-aware 裁剪和 CLI resume 装配。
- [x] (2026-06-04 12:15Z) 更新 `architecture.md` 和技术债记录，反映 `prompts/` 第一版已落地及剩余限制。
- [x] (2026-06-04 12:20Z) 运行 compile checks、focused tests 和 full tests。

## Surprises & Discoveries

- Observation: `prompts/` 当前完全不存在，但 `ContextEngine` 已经有可注入的 `PromptAssembler` 协议。
  Evidence: `architecture.md` 把 `prompts/assembler.py`、`prompts/sections.py` 和 `prompts/runtime_context.py` 标为目标模块；`core/context_engine.py` 当前定义 `PromptAssembler.assemble(state)`，默认实现是返回固定字符串的 `StaticPromptAssembler`。

- Observation: 工具 descriptor 已携带 prompt，但 registry 只暴露 provider schema，不暴露工具 prompt section。
  Evidence: `services/tools/types.py` 中的 `ToolDescriptor` 已包含 `prompt` 字段；`services/tools/registry.py` 当前只有 `tool_schemas(state)`，没有 `tool_prompt_sections(state)`。

- Observation: 设计文档要求 deny 优先于 prompt/schema 组装，但当前 deny 主要发生在工具执行期。
  Evidence: `docs/design-docs/core-beliefs.md` 明确写到被 deny 的工具不进入 tool schema，也不出现在 system prompt 的可用工具说明中；当前 `RegistryToolExecutor` 会在执行工具前检查 guard targets，而 `ToolRegistry.tool_schemas()` 没有接收 guard 或 permission view。

- Observation: workspace 状态必须谨慎注入。
  Evidence: 用户明确要求只包含对项目开发有用的信息，例如 cwd、已读文件、可用工具；CLI 模式、session id 等主要给程序使用的信息不应发送给 AI。

- Observation: `glob-grep-tools-plan` 已经在当前工作区修改了 `services/tools/registry.py` 和 `ui/cli/app.py`，并新增了 `glob` / `grep` 工具注册。
  Evidence: `git status --short` 显示这些文件已修改或新增；`ui/cli/app.py` 当前注册 `read_file`、`edit_file`、`glob` 和 `grep`，`services/tools/registry.py` 已有 `tool_prompt_sections(state)`。

- Observation: `DynamicPromptAssembler` 可以在初始化时持有 cwd 和 registry，让 `ContextEngine` 的 `PromptAssembler.assemble(state)` 协议保持不变。
  Evidence: `prompts/assembler.py` 通过 `_build_context(state)` 读取 cwd、`ToolRegistry.visible_descriptors(state)` 和 `RuntimeState.metadata["files_read"]`，`core/context_engine.py` 仍只调用 `assemble(state)`。

## Decision Log

- Decision: 本计划建立长期 `prompts/` 架构骨架，而不是只填一个静态 system prompt 字符串。
  Rationale: OneCode 的设计方向是动态组装胜过硬编码。长期骨架可以承载后续 memory、skill、task、compaction 状态和用户偏好，同时避免 system prompt 退化为不断膨胀的单个字符串。
  Date/Author: 2026-06-04 / Codex

- Decision: 第一版 system prompt 必须包含行为规则。
  Rationale: OneCode 是 code agent runtime，模型需要稳定理解基本行为边界，例如先理解代码再改动、优先使用工具读取事实、遵守 sandbox/guard、不臆造文件内容、不把安全交给自觉承诺。
  Date/Author: 2026-06-04 / Codex

- Decision: 同步推进 deny-aware prompt/schema 裁剪。
  Rationale: 设计文档把 deny-first 作为核心约束。若工具被 deny 后仍出现在 schema 或 prompt 中，模型会看到与真实能力不一致的世界，后续执行只能不断失败。
  Date/Author: 2026-06-04 / Codex

- Decision: workspace 状态 section 只注入对项目开发有直接帮助的信息。
  Rationale: cwd、已读文件和可用工具能帮助模型规划下一步；CLI 模式、session id 等内部运行信息通常不会提升代码任务质量，反而增加无关上下文和泄露内部实现细节。
  Date/Author: 2026-06-04 / Codex

- Decision: 第一版暂不实现用户偏好、语言偏好、memory、skill 和 task 状态。
  Rationale: 这些能力需要各自的数据来源和生命周期规则。当前目标是先把 prompt 组装边界、section 缓存、工具 prompt 和 workspace 状态稳定下来。
  Date/Author: 2026-06-04 / Codex

- Decision: prompt 使用 section 化结构，并用快照测试固定输出。
  Rationale: section 化让新增 prompt 行为成为可组合单元。快照测试能及时暴露 section 顺序、标题、空 section 过滤和工具 prompt 变化，降低 prompt 漂移风险。
  Date/Author: 2026-06-04 / Codex

- Decision: 新 assembler 接入 CLI，并删除生产路径中旧的无用静态 prompt 装配。
  Rationale: 这是重构，不是并行保留旧路径。CLI 是当前实际应用装配入口，如果 CLI 仍使用旧 prompt，`prompts/` 模块就只是未被产品路径消费的代码。
  Date/Author: 2026-06-04 / Codex

- Decision: 第一版实现 section 缓存。
  Rationale: 用户明确要求后续需要 prompt 拼装缓存，并决定这次就做 section 缓存。缓存粒度应放在 section，而不是只缓存最终 prompt，以便未来某些动态 section 改变时仍可复用稳定 section。
  Date/Author: 2026-06-04 / Codex

- Decision: 保持 `PromptAssembler.assemble(state)` 协议不变，让 `DynamicPromptAssembler` 在初始化时持有 cwd 和可选 `ToolRegistry`。
  Rationale: 这样 `ContextEngine` 仍是上下文重建边界，只负责调用 assembler，不需要知道每个 prompt section 的输入细节。CLI 可以显式传入 workspace 和 registry，测试或低层调用没有传入时仍会生成包含 identity 和 behavior rules 的非空 prompt。
  Date/Author: 2026-06-04 / Codex

- Decision: 第一版 visible tool view 支持 registry 构造期 disabled/denied 工具名，以及 `RuntimeState.metadata` 中的 `disabled_tools`、`denied_tools` 和 `hidden_tools`。
  Rationale: 当前还没有完整 permission policy service。这个接口先保证 schema 与 prompt 通过同一视图裁剪，并让测试能证明 blanket deny/disabled 的一致性；真实 permission policy 接入作为技术债记录。
  Date/Author: 2026-06-04 / Codex

## Outcomes & Retrospective

已完成第一版实现并通过 focused tests。新增文件结构为 `prompts/__init__.py`、`prompts/runtime_context.py`、`prompts/sections.py`、`prompts/cache.py` 和 `prompts/assembler.py`。system prompt 当前按 identity、behavior rules、workspace state、available tools 和每个工具 prompt section 的顺序输出；workspace state 只包含 cwd、可用工具和已读文件。section cache 通过 section key 和 fingerprint 命中；已读文件或工具可见性变化会让相关 section 重新渲染。真实 permission policy、memory、skill、task、用户偏好和 compaction 状态接入仍未覆盖，已在技术债中记录。

Focused validation 已通过：

    uv run python -m pytest tests\test_dynamic_prompt_assembler.py -q
    4 passed

    uv run python -m pytest tests\test_cli_resume.py -q
    4 passed

    uv run python -m pytest tests\test_search_tools.py tests\test_tool_registry_and_executor.py -q
    20 passed

Final validation 已通过：

    uv run python -m compileall core services infrastructure tools ui prompts
    success

    uv run python -m pytest tests -q
    99 passed

## Context and Orientation

OneCode 是 Python code-agent runtime。它的主循环位于 `core/loop.py`，负责把用户消息写入 `MessageStore`、通过 `ContextEngine` 构建 `ContextSnapshot`、调用模型、执行模型请求的工具、回填工具结果，并在没有工具调用时返回最终文本。主循环不应该拼接 prompt 文本，也不应该知道具体工具说明。

`core/context_engine.py` 是每轮模型调用前的上下文重建边界。它当前从 `MessageStore` 读取消息，调用 `ContextPreparer`、`PromptAssembler` 和 `ToolSchemaProvider`，最后返回 `services/context/snapshot.py` 中的 `ContextSnapshot`。`ContextSnapshot` 包含 `system_prompt`、`messages`、`tool_schemas`、`usage_hints`、`transcript_refs` 和 `transition`。

当前 `core/context_engine.py` 中的 `PromptAssembler` 协议只有 `assemble(state: RuntimeState) -> str`。默认 `StaticPromptAssembler` 返回固定字符串，默认字符串为空。这是一个已经存在的注入边界，但不是完整 prompt 系统。实现本计划时，需要把 prompt 组装所需的运行时输入扩展到一个明确的 `PromptRuntimeContext`，避免 assembler 只能读取 `RuntimeState` 而无法看到 workspace、工具 registry 和 deny-aware 工具视图。

工具运行时位于 `services/tools/`。`services/tools/types.py` 定义 `ToolDescriptor`，其中已经包含工具 `prompt` 字段。具体工具位于顶层 `tools/<tool_name>/`，例如 `tools/read_file/prompt.py` 和 `tools/edit_file/prompt.py`。这些工具 prompt 目前很短，但已经证明工具说明应跟随工具目录，而不是集中写死在主循环。

`services/tools/registry.py` 管理启用工具。当前 `ToolRegistry.tool_schemas(state)` 按工具名排序返回 provider-visible schema。它还没有 `tool_prompt_sections(state)`，也没有 deny-aware 的工具视图。本计划要求 schema 和 prompt section 都来自同一个裁剪后的工具集合，使模型看到的工具能力与执行入口一致。

`services/guard/` 管理路径安全和 sandbox 策略。当前 `SandboxGuard` 对具体路径 target 返回 `allow`、`ask` 或 `deny`。deny 不能被 hook、ask、allow 或 prompt 覆盖。工具级路径 deny 当前主要在执行前生效；本计划中的 deny-aware prompt/schema 裁剪只负责 blanket 工具级或可静态判断的工具可见性，不应假装能预判每一次带具体路径参数的动态调用。

`ui/cli/app.py` 是当前实际 runtime 装配入口。它创建 `RuntimeState`、`MessageStore`、`ContextEngine`、provider model client、固定 `read_file` 和 `edit_file` 工具 registry、`SandboxGuard` 和 `RegistryToolExecutor`。实现本计划后，CLI 必须装配新的 dynamic prompt assembler，并删除旧的生产用静态空 prompt 路径。

## Plan of Work

第一阶段创建 `prompts/` 包。新增 `prompts/__init__.py`、`prompts/runtime_context.py`、`prompts/sections.py`、`prompts/cache.py` 和 `prompts/assembler.py`。`runtime_context.py` 定义 `PromptRuntimeContext`，它至少包含 `state: RuntimeState`、`cwd: Path`、`tool_registry: ToolRegistry | None`、`visible_tools: tuple[ToolDescriptor, ...]`、`files_read: tuple[str, ...]` 和可选 `transition: str | None`。不要在该结构中包含 session id、CLI mode、provider id 或其他主要供程序内部使用的信息。

第二阶段定义 section 模型和缓存。`prompts/sections.py` 定义 `PromptSection`，字段建议为 `key: str`、`title: str`、`body: str`、`cache_key: str | None` 和 `cacheable: bool`。也可以使用等价 dataclass，只要语义清晰。section key 是稳定内部标识，例如 `identity`、`behavior_rules`、`workspace_state`、`available_tools` 和 `tool_prompt:read_file`。标题是模型可见标题。body 是模型可见正文。空 body 的 section 必须被过滤。缓存应该位于 `prompts/cache.py`，定义小型 `PromptSectionCache`，支持 `get(key, fingerprint)`、`set(key, fingerprint, text)` 和 `clear()`。fingerprint 应由 section key 和影响该 section 输出的输入组成，例如工具 prompt 文本、cwd、已读文件列表或行为规则版本。

第三阶段实现 section 生成函数。`prompts/sections.py` 应提供稳定 section 构造函数。`identity_section()` 说明 OneCode 的身份是 code agent runtime 中的编码代理，强调当前任务是帮助用户在本 workspace 中完成代码工作。`behavior_rules_section()` 写入稳定行为规则：先根据仓库事实行动；需要了解文件时使用工具；修改前读取相关文件；遵守 sandbox/guard；不要声称执行了未执行的命令；不要把安全边界交给模型承诺；保持主循环、工具、prompt、guard、hook 等边界清晰。`workspace_state_section(context)` 只注入 cwd、已读文件和可用工具摘要，不注入 session id、CLI mode 或 provider 配置。`tool_prompt_sections(context)` 从 visible tools 读取 descriptor prompt，为每个工具生成独立 section，标题包含工具名，例如 `Tool: read_file`。

第四阶段实现 assembler。`prompts/assembler.py` 定义 `DynamicPromptAssembler`。它应满足 `core/context_engine.py` 中的 prompt assembler 协议，但可以把协议升级为接收 `PromptRuntimeContext`，或在 assembler 初始化时注入构造 context 所需的依赖。推荐避免让 `ContextEngine` 直接知道每个 prompt section 的细节：`ContextEngine` 只负责拿到 assembler 并调用 `assemble(...)`。如果修改协议，应同步更新 tests 和所有调用点，不保留旧生产兼容分支。assembler 生成顺序固定为：identity、behavior rules、workspace state、available tools、每个工具 prompt section。最终 prompt 使用 Markdown 风格标题即可，例如 `# Identity`、`# Behavior Rules`，但必须稳定。

第五阶段扩展工具 registry 的可见工具视图。编辑 `services/tools/registry.py`，新增一个统一的内部方法，例如 `visible_descriptors(state) -> tuple[ToolDescriptor, ...]`。`tool_schemas(state)` 和新增的 `tool_prompt_sections(state)` 都基于它。第一版可以支持 descriptor 级 enablement 和 blanket deny：如果当前已有 permission/guard 结构能表示某个工具整体不可用，就在这里裁剪；如果还没有对应结构，则先定义清晰接口和测试 fixture，使后续 permission policy 可以接入。不要伪造路径参数级别的静态 deny，因为模型调用前还不知道具体路径。执行入口仍必须重复 guard 检查。

第六阶段更新 `core/context_engine.py`。移除或缩小 `StaticPromptAssembler` 的生产角色。可以保留一个测试专用 `StaticPromptAssembler`，但默认 `ContextEngine` 不应再悄悄返回空 system prompt。更合适的行为是：生产装配必须显式传入 `DynamicPromptAssembler`；如果没有传入 assembler，默认使用新的空依赖 dynamic assembler，至少生成身份和行为规则。同步更新类型协议，使它能表达新的 prompt runtime context。如果 `ContextEngine` 负责创建 `PromptRuntimeContext`，则它需要接收 cwd 和 tool registry；如果 assembler 自己负责读取注入依赖，则 `ContextEngine` 保持更薄。优先选择让 assembler 持有 registry/cwd provider，以减少 `ContextEngine` 变复杂。

第七阶段更新 CLI 装配。编辑 `ui/cli/app.py`，在创建 `ContextEngine` 前创建 `ToolRegistry`，再创建 `DynamicPromptAssembler` 并传入 cwd、tool registry 和必要的 runtime state reader。确保 CLI 的 runtime 不再使用旧 `StaticPromptAssembler` 或空 prompt。`/tools` 命令仍从 registry 展示工具，不要从 prompt 文本反向解析工具。不要把 CLI 模式或 session id 注入 prompt。

第八阶段补充测试。建议新增 `tests/test_dynamic_prompt_assembler.py`，并更新现有 context engine 或 CLI tests。测试应覆盖：默认 prompt 包含 identity 和 behavior rules；workspace section 包含 cwd；已读文件来自 `RuntimeState.metadata["files_read"]` 且排序稳定；可用工具来自 registry；工具 prompt 每个工具单独 section；空工具 prompt 被跳过；section 顺序稳定；相同 runtime context 下缓存命中；工具 prompt 或已读文件变化后对应 section cache 失效；deny-aware visible descriptors 同时影响 schema 和 prompt。快照测试可以使用普通字符串断言或 checked-in expected string，关键是输出稳定、可审查。

第九阶段更新文档和技术债。编辑 `architecture.md`，把 `prompts/` 从目标未实现改为第一版已实现，并说明当前支持 identity、behavior rules、workspace state、tool prompt sections 和 section cache，但暂不支持用户偏好、语言偏好、memory、skill、task、compaction 状态。编辑 `docs/tech-debt/tech-debt-tracker.md`，新增或调整技术债：prompt 系统第一版落地后，剩余债务应是与未来 memory/skill/task/compaction 状态接入、permission policy 深度裁剪或缓存失效粒度相关的具体限制，不要继续笼统写“prompts 尚未实现”。

## Concrete Steps

所有命令都在仓库根目录执行：

    cd D:\study\OneCode

开始前查看工作区，确认已有用户变更，不覆盖无关文件：

    git status --short

新增 prompt 包文件：

    prompts/__init__.py
    prompts/runtime_context.py
    prompts/sections.py
    prompts/cache.py
    prompts/assembler.py

编辑工具 registry：

    services/tools/registry.py

编辑 context engine 和 CLI 装配：

    core/context_engine.py
    ui/cli/app.py

新增或更新测试：

    tests/test_dynamic_prompt_assembler.py
    tests/test_tool_registry_and_executor.py
    tests/test_cli_commands.py 或现有 CLI runtime 测试文件

更新文档：

    architecture.md
    docs/tech-debt/tech-debt-tracker.md

实现过程中先运行 focused tests：

    uv run python -m pytest tests/test_dynamic_prompt_assembler.py tests/test_tool_registry_and_executor.py -q

如果修改了 CLI 装配，运行 CLI 相关测试：

    uv run python -m pytest tests/test_cli_commands.py tests/test_cli_resume.py -q

运行 compile check：

    uv run python -m compileall core services infrastructure tools ui prompts

运行全量测试：

    uv run python -m pytest tests -q

如果 `compileall` 发现 `prompts` 目录不存在，说明还未完成第一阶段文件创建。完成实现后该命令应通过。

## Validation and Acceptance

验收标准一：`ContextEngine.build_for_model(RuntimeState())` 生成的 `ContextSnapshot.system_prompt` 非空，并包含稳定 identity section 和 behavior rules section。输出不应依赖 CLI 模式、session id 或 provider-specific 配置。

验收标准二：CLI runtime 使用新的 `DynamicPromptAssembler`。可以通过测试装配 CLI runtime 后构建 snapshot，断言 system prompt 包含 OneCode 行为规则和工具 prompt。生产路径中不应继续使用旧的空 `StaticPromptAssembler`。

验收标准三：workspace state section 只包含对项目开发有用的信息。至少包含 cwd、可用工具摘要和已读文件；如果没有已读文件，应明确省略该列表或显示简短空状态。不得包含 session id、CLI mode、API key、provider headers 或内部 transcript 路径。

验收标准四：工具 prompt section 来自 tool descriptor。注册 `read_file` 和 `edit_file` 后，system prompt 包含两个独立工具 section，并包含对应 `tools/read_file/prompt.py` 和 `tools/edit_file/prompt.py` 中的 prompt 文本。工具排序稳定。

验收标准五：schema 与 prompt 使用同一个 visible tool view。若测试中把某个工具标记为 blanket denied 或 disabled，则 `ToolRegistry.tool_schemas(state)` 不包含该工具，`ToolRegistry.tool_prompt_sections(state)` 和最终 system prompt 也不包含该工具。执行入口仍保留 guard 检查。

验收标准六：section 缓存工作且可验证。相同 context 连续组装时，cache 命中；已读文件、工具 prompt、visible tools 或 cwd 变化时，相关 section 的 fingerprint 变化并重新生成。缓存不应让过期工具 prompt 或旧 workspace 状态泄露到下一轮模型调用。

验收标准七：快照测试固定主要 prompt 输出。测试应覆盖 section 标题、顺序、空 section 过滤、工具 section 格式和 workspace state 格式。prompt 文本变更需要显式更新测试期望。

验收标准八：以下命令通过：

    uv run python -m compileall core services infrastructure tools ui prompts
    uv run python -m pytest tests -q

## Idempotence and Recovery

本计划只新增 `prompts/` 包并修改 prompt 装配、registry、CLI 装配、测试和文档。它不应修改用户项目文件、不应执行模型调用、不应读取 API key，也不应改变 `.env`。

如果实现中发现 `ContextEngine` 协议改动影响范围过大，可以让 `DynamicPromptAssembler` 在初始化时持有 cwd provider 和 tool registry，使 `assemble(state)` 继续满足现有协议。但生产路径仍必须删除空静态 prompt 装配，不能把旧行为作为默认。

如果 deny-aware 裁剪缺少真实配置来源，不要编造复杂权限系统。先实现明确的 visible descriptor 接口和测试用裁剪 hook，使 schema 和 prompt 都通过同一视图；同时在技术债中记录“真实 permission policy 接入 visible tool view”的剩余工作。

section 缓存必须保守。只缓存纯文本 section，不缓存可变对象。fingerprint 必须覆盖会影响 section 输出的输入。若某个 section 的失效条件不清楚，先标记为 `cacheable=False`，不要冒险复用过期 prompt。

不要在主循环中写 prompt 文本。若实现时需要添加行为规则，放在 `prompts/sections.py`。若工具需要更详细说明，放在对应 `tools/<tool_name>/prompt.py`，并由 registry/assembler 汇总。

## Artifacts and Notes

目标 system prompt 形态示例：

    # Identity
    You are OneCode, a coding agent running inside this workspace.

    # Behavior Rules
    - Use repository facts and tool results as the basis for code changes.
    - Read relevant files before editing them.
    - Respect sandbox and guard decisions. A denied capability is unavailable.

    # Workspace State
    cwd: D:\study\OneCode
    available tools: edit_file, read_file
    files read:
    - D:\study\OneCode\core\context_engine.py

    # Tool: edit_file
    Edit a sandboxed text file by replacing exact strings.

    # Tool: read_file
    Read a UTF-8 text file from the sandbox and return line-numbered content.

实际文本可以调整，但必须保持 section 化、稳定顺序和测试覆盖。工具 section 的正文来自 descriptor prompt，不要在 assembler 中复制工具 prompt。

缓存 fingerprint 示例：

    identity: prompt-version-v1
    behavior_rules: prompt-version-v1
    workspace_state: cwd + sorted(files_read) + sorted(visible_tool_names)
    tool_prompt:read_file: descriptor.name + descriptor.prompt

## Interfaces and Dependencies

`prompts/runtime_context.py` 应定义类似接口：

    @dataclass(frozen=True)
    class PromptRuntimeContext:
        state: RuntimeState
        cwd: Path
        visible_tools: tuple[ToolDescriptor, ...] = ()
        files_read: tuple[str, ...] = ()
        transition: str | None = None

具体字段可以按实现微调，但不得包含 session id、CLI mode、API key 或 provider-specific 内部信息。

`prompts/cache.py` 应定义类似接口：

    class PromptSectionCache:
        def get(self, key: str, fingerprint: str) -> str | None: ...
        def set(self, key: str, fingerprint: str, value: str) -> None: ...
        def clear(self) -> None: ...

缓存可以先是进程内内存缓存，不需要持久化到磁盘。

`prompts/sections.py` 应定义类似接口：

    @dataclass(frozen=True)
    class PromptSection:
        key: str
        title: str
        body: str
        fingerprint: str
        cacheable: bool = True

    def identity_section(context: PromptRuntimeContext) -> PromptSection: ...
    def behavior_rules_section(context: PromptRuntimeContext) -> PromptSection: ...
    def workspace_state_section(context: PromptRuntimeContext) -> PromptSection: ...
    def tool_prompt_sections(context: PromptRuntimeContext) -> tuple[PromptSection, ...]: ...

`prompts/assembler.py` 应定义类似接口：

    class DynamicPromptAssembler:
        def __init__(
            self,
            cwd: Path,
            tool_registry: ToolRegistry | None = None,
            section_cache: PromptSectionCache | None = None,
        ) -> None: ...

        def assemble(self, state: RuntimeState) -> str: ...

如果最终选择让 `ContextEngine` 传入完整 `PromptRuntimeContext`，应同步更新 `core/context_engine.py` 中的 protocol 和所有调用点，保持生产路径只有一种清晰装配方式。

`services/tools/registry.py` 应提供：

    def visible_descriptors(self, state: RuntimeState) -> tuple[ToolDescriptor, ...]: ...
    def tool_schemas(self, state: RuntimeState) -> tuple[dict[str, Any], ...]: ...
    def tool_prompt_sections(self, state: RuntimeState) -> tuple[str, ...]: ...

`tool_schemas()` 和 `tool_prompt_sections()` 必须基于同一个 visible descriptor 集合。若第一版 visible descriptor 只支持 enabled/disabled 或测试用 deny view，应把真实 permission policy 接入作为技术债记录。

2026-06-04 / Codex: 初始中文 ExecPlan 创建，纳入用户关于长期 prompt 架构、行为规则、deny-aware 裁剪、workspace 状态边界、section 化、快照测试、CLI 重构和 section 缓存的决策。
