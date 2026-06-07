# 实现 OneCode Skill Loading 和 Skill 工具

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本计划必须按照仓库根目录的 `PLANS.md` 维护。本文是自包含计划：后续贡献者应能只阅读本文件，并结合文中点名的源码文件，完成 skill loading、skill catalog、inline skill 使用和 fork skill 使用的端到端实现。


## Purpose / Big Picture

完成本计划后，OneCode 会拥有一套按需加载的技能系统。用户或项目可以把长篇工作流、领域规范和工具使用说明放到 `SKILL.md` 文件里；模型在每轮系统提示词中只看到简短技能目录，真正需要时通过 `skill` 工具加载完整内容。这样可以避免把所有技能全文塞进系统提示词，同时让技能调用经过工具 registry、权限策略、附件投影和 transcript 这些已有 runtime 边界。

用户可观察到的行为是：项目目录下存在 `.onecode/skills/code-review/SKILL.md` 时，OneCode 启动后会在模型可见 prompt 中宣布 `code-review` 技能；当模型调用 `skill` 工具输入 `{"skill": "code-review"}` 后，父消息链会得到一个简短工具结果 `Launching skill: code-review`，随后下一轮上下文中会出现该技能全文，模型再按技能说明继续工作。若技能声明 `context: fork`，它会在干净子 agent 上下文中运行，父链只收到 fork 子 agent 的最终摘要。


## Progress

- [x] (2026-06-07 01:35+08:00) 已阅读 `PLANS.md`、`AGENTS.md`、`architecture.md`、相关 design docs、附件系统代码和 `docs/references/s07_skill_loading` 参考材料。
- [x] (2026-06-07 01:40+08:00) 已与用户确认第一版范围：技能来源为 bundled + 用户目录 + 项目 `.onecode/skills`；同名优先级采用 `project > user > bundled`；inline skill 尽量复用附件系统；`allowed-tools` 自动合并进权限上下文；fork skill 使用干净上下文加 skill prompt；frontmatter 字段按本计划收窄实现。
- [x] (2026-06-07 01:50+08:00) 已创建本活跃 ExecPlan，尚未实现 runtime 代码。
- [x] (2026-06-07 12:20+08:00) 实现 `services/skills/` 的技能模型、frontmatter 解析、bundled 注册、文件发现、缓存和优先级覆盖。
- [x] (2026-06-07 12:25+08:00) 实现 prompt 中的技能目录 section，并接入 `DynamicPromptAssembler`。
- [x] (2026-06-07 12:35+08:00) 实现 `tools/skill/` 工具 descriptor、输入校验、权限分类、inline 调用和 fork 调用。
- [x] (2026-06-07 12:40+08:00) 扩展工具结果协议，使工具可以追加 durable attachment messages，并让主循环在 tool result 后追加这些 follow-up messages。
- [x] (2026-06-07 12:42+08:00) 扩展附件投影，支持 `type="skill"` 的 skill attachment。
- [x] (2026-06-07 12:50+08:00) 扩展权限策略，支持 skill-specific deny 位置和 `allowed-tools` 自动降低后续 ask。
- [x] (2026-06-07 12:55+08:00) 将 skill descriptor 接入 CLI runtime 和 subagent base descriptors。
- [x] (2026-06-07 13:05+08:00) 补充 loader、prompt、tool、attachment、permission、fork 和 runtime integration 测试。
- [x] (2026-06-07 13:15+08:00) 运行全量测试，并把结果写入 `Outcomes & Retrospective`。


## Surprises & Discoveries

- Observation: OneCode 已经有 durable attachment role，且 `AttachmentContextPreparer` 会在 provider 调用前隐藏 raw `role="attachment"` message。
  Evidence: `services/attachments/types.py` 定义 `AttachmentMessage.to_message()`，`services/attachments/context_preparer.py` 先运行内部 preparer 再调用 `AttachmentProjector.project()`，`services/attachments/projector.py` 把 `file` attachment 投影为 synthetic assistant tool call 和 synthetic `tool_result`。

- Observation: 当前 `ToolExecutionResult` 只能表达一个工具结果，不能表达参考实现里的 `newMessages` 或 `contextModifier`。
  Evidence: `services/tools/types.py` 中 `ToolExecutionResult` 只有 `tool_call_id`、`tool_name`、`content`、`is_error` 和 `metadata`；`core/loop.py` 在工具执行后只调用 `message_store.append_tool_results(result_blocks)`。

- Observation: 当前 subagent runner 支持干净上下文和 fork 上下文，但只根据内置 `AgentDefinition` 查找 agent 类型。
  Evidence: `services/subagents/runner.py::run()` 通过 `_definition_for_request()` 调用 `get_agent_definition(request.subagent_type or "fork")`；非 fork 分支会用 `child_store.seed_messages(({"role": "user", "content": request.prompt},))` 创建干净上下文。

- Observation: 当前权限策略已经是 deny-first，且 read-only subagent、工具 deny/disabled、guard deny 都在 ask/session allow 之前判断。
  Evidence: `services/permissions/policy.py::evaluate()` 先处理 `read_only_agent`、`is_tool_denied()`、`is_tool_disabled()` 和 guard deny，然后才计算 ask 与 session allow。

- Observation: `SubagentRunner` 可以直接支持动态 skill definition，而不需要把复杂对象塞进 `SubagentRequest.metadata`。
  Evidence: `services/subagents/runner.py::run_skill()` 直接构造 `AgentDefinition`、干净 `MessageStore`、child `ToolRegistry` 和 `AgentLoop`，并把第一条 user message 设置为 skill prompt。


## Decision Log

- Decision: 第一版技能来源是 bundled skills、用户目录 skills 和项目 `.onecode/skills`，不实现 MCP skills、插件 skills、legacy `.claude/commands` 或远程 skill search。
  Rationale: 用户明确要求“用户目录 + 项目 `.onecode/skills`”，并要求先实现 skill loading 和使用 skill 的主流程。收窄来源能把复杂度集中在 OneCode 架构边界：loader、catalog、Skill 工具、权限和附件投影。
  Date/Author: 2026-06-07 / User and Codex

- Decision: 文件技能同名时采用 `project > user > bundled` 优先级。更高优先级覆盖更低优先级，覆盖事件应写入 trace/debug metadata，测试应固定该行为。
  Rationale: 项目技能最贴近当前仓库，用户技能代表个人偏好，bundled 技能是默认能力。这个顺序符合“局部配置覆盖全局默认”的常见预期。
  Date/Author: 2026-06-07 / User and Codex

- Decision: Inline skill 的完整内容通过 durable `skill` attachment 注入，而不是直接塞进普通 tool result。
  Rationale: 现有附件系统已经解决“持久保存结构化上下文，但在 provider 调用前投影为合法消息”的问题。Skill 全文和文件附件一样，是 runtime 注入的结构化上下文；作为 attachment 保存可以避免 transcript 把它误记成模型真实工具读取结果。
  Date/Author: 2026-06-07 / User and Codex

- Decision: `ToolExecutionResult` 需要新增 follow-up message 能力，用来承载 Skill 工具调用后要追加的 attachment messages。
  Rationale: SkillTool 是普通工具，不能绕过 executor 和主循环直接写 `MessageStore`。新增 follow-up messages 可以保持工具仍通过 executor 返回，同时让 loop 统一追加工具引发的新上下文。
  Date/Author: 2026-06-07 / Codex

- Decision: `allowed-tools` 在 skill 成功调用后自动合并进权限上下文，但不能覆盖任何 deny。
  Rationale: 用户明确要求“自动允许，合并进权限上下文”。OneCode 的安全原则要求 deny-first，因此这个自动允许只能把本来需要 ask 的后续工具调用降为 allow，不能绕过 `denied_tools`、`disabled_tools`、guard deny、read-only subagent 或 internal memory extraction 限制。
  Date/Author: 2026-06-07 / User and Codex

- Decision: `context: fork` 的 skill 使用干净上下文加 skill prompt，不继承父消息链。
  Rationale: 用户明确要求 fork skill 用“干净上下文 + skill prompt”。这和普通 omitted `subagent_type` fork 不同，因此实现不应复用父消息链 fork，而应为 skill 创建一个动态 clean child request。
  Date/Author: 2026-06-07 / User

- Decision: 第一版 frontmatter 支持 `name`、`description`、`when_to_use`、`allowed-tools`、`context`、`model`、`user-invocable`、`disable-model-invocation` 和 `paths`。
  Rationale: 这些字段覆盖目录展示、模型调用、权限、inline/fork 分支和未来条件激活。`hooks`、`shell`、legacy 命令插值和远程 skill 可后置，以免在第一版引入未验证的执行面。
  Date/Author: 2026-06-07 / User and Codex


## Outcomes & Retrospective

已完成第一版实现。实际落地内容包括：

- `services/skills/` 文件技能 loader、frontmatter parser、catalog provider 和缓存。
- `# Available Skills` prompt section，只展示 skill 摘要，不展示全文。
- `tools/skill` descriptor，支持 inline skill attachment 和 `context: fork` clean child runner。
- `ToolExecutionResult.followup_messages`，主循环在 tool result 后追加 successful follow-up attachments。
- `AttachmentProjector` 支持 `type="skill"`，provider snapshot 不包含 raw `role="attachment"`。
- session 级整工具 allow 和 specific skill deny 位置，`allowed-tools` 成功加载后自动进入 session allow，且不覆盖 deny-first。
- CLI runtime 注册 `skill` 工具，并让新 session 的 prompt assembler 继续持有 skill provider。

验证结果：

    uv run python -m compileall core services infrastructure tools ui
    passed

    uv run python -m pytest tests\test_skill_loader.py tests\test_skill_prompt_listing.py tests\test_skill_tool.py tests\test_skill_permissions.py tests\test_loop.py tests\test_attachment_projector.py tests\test_dynamic_prompt_assembler.py tests\test_tool_registry_and_executor.py tests\test_permission_policy.py tests\test_subagent_runner.py -q
    53 passed

    uv run python -m pytest tests -q
    233 passed

本轮没有新增技术债；`paths` frontmatter 目前按计划只解析保存，尚未作为条件激活规则使用。


## Context and Orientation

OneCode 是 Python code agent runtime。主循环在 `core/loop.py`，它接收用户输入，调用 `ContextEngine` 构建模型上下文，把模型回复写入 `MessageStore`，再通过 `RegistryToolExecutor` 执行模型请求的工具调用。主循环必须保持薄，不能硬编码具体工具名或技能名。

工具系统由 `services/tools/types.py`、`services/tools/registry.py` 和 `services/tools/executor.py` 组成。`ToolDescriptor` 是一个工具的事实来源，包含工具名、描述、输入 schema、prompt、输入校验、分类函数和 handler。`ToolRegistry` 决定哪些工具对模型可见，并从同一组可见工具生成 provider tool schema 和 prompt 工具说明。`RegistryToolExecutor` 执行每个工具调用，顺序是 schema 校验、工具级校验、输入分类、guard、permission、hook、handler、结果预算和 trace。

权限系统在 `services/permissions/policy.py`。这里的“deny-first”意思是任何拒绝都优先于 allow 或用户确认。当前策略先拒绝 read-only subagent 的状态改变工具，再拒绝工具级 denied/disabled，再拒绝 guard deny，然后才考虑 ask 和 session allow。Skill loading 必须保持这个顺序。

附件系统在 `services/attachments/`。附件是 internal message，也就是 OneCode 自己保存但不直接发给 provider 的消息。`AttachmentMessage.to_message()` 会生成 `role="attachment"` 的 durable message。`AttachmentContextPreparer` 在每次 provider 调用前调用 `AttachmentProjector`，把 raw attachment 投影成 provider 可以接受的 `user`、`assistant` 或 `tool_result` messages。当前 `file` attachment 会临时投影成 synthetic `read_file` assistant tool call 和 matching synthetic tool result，但这些 synthetic messages 不写回 transcript。Skill inline loading 应复用这条路径，新增 `type="skill"` 的 attachment projection。

Subagent 系统在 `services/subagents/` 和 `tools/agent/`。普通 `agent` 工具通过 `SubagentRunner` 创建 child runtime。当前 runner 支持两种形态：显式 `subagent_type` 使用干净上下文，省略 `subagent_type` 使用父消息链 fork。本计划中的 fork skill 不应使用父消息链 fork，而应使用干净上下文，并把完整 skill prompt 作为 child 的第一条 user message。

动态 prompt 组装在 `prompts/assembler.py` 和 `prompts/sections.py`。`DynamicPromptAssembler` 根据当前 `RuntimeState`、cwd、可见工具和已读文件列表构建 system prompt。Skill catalog 应作为新的 prompt section 或由 assembler 可注入的 skill listing provider 生成；它只包含简短目录，不包含技能全文。

CLI runtime 组装在 `ui/cli/app.py::build_runtime()`。这里创建 `RuntimeState`、`MessageStore`、`PermissionPolicy`、`ToolRegistry`、`ContextEngine`、`SubagentRunner`、`RegistryToolExecutor` 和 `AgentLoop`。Skill descriptor、skill service、prompt catalog provider 和 subagent clean skill runner 的依赖都应在这里装配，而不是塞进 `core/loop.py`。

本计划会使用“command”这个词表示一个已加载的 skill 描述对象，不是 shell command。一个 `SkillCommand` 包含技能名、描述、全文、来源、权限字段和执行上下文。`context: inline` 表示 Skill 工具把技能全文注入父对话，让主 agent 继续执行；`context: fork` 表示 Skill 工具启动一个干净 child agent 运行技能，并把 child 的最终摘要返回给父 agent。


## Plan of Work

首先创建 `services/skills/`。该包应包含技能领域模型、frontmatter 解析、bundled registry、文件发现和缓存。建议新增 `services/skills/types.py`，定义 `SkillCommand` dataclass。`SkillCommand` 的字段应至少包括 `name`、`description`、`when_to_use`、`content`、`source`、`root`、`allowed_tools`、`context`、`model`、`user_invocable`、`disable_model_invocation` 和 `paths`。`source` 使用字符串值 `bundled`、`user` 和 `project`。`context` 使用 `inline` 或 `fork`，默认是 `inline`。

然后新增 `services/skills/frontmatter.py`。它负责从 `SKILL.md` 文件开头解析 YAML-like frontmatter。不要引入第三方依赖；第一版只需要支持简单的 `key: value` 行和简单列表值。若文件没有 frontmatter，则技能名来自目录名，描述来自 markdown 第一行标题或第一段非空文本。解析 `allowed-tools` 时应支持逗号分隔字符串和 YAML 简单列表两种形式，并归一化为空白去除后的工具名列表。解析 `context` 时只有值等于 `fork` 才设置为 fork，其他值按 inline 处理。

接着新增 `services/skills/loader.py`。它应暴露 `init_bundled_skills(registry)`、`get_commands(cwd)`、`load_all_commands(cwd)` 和 `clear_skill_caches()`。`init_bundled_skills()` 是编程式注册入口，第一版可注册一个或两个测试用 bundled skill，或允许调用方传入 bundled skill definitions。`load_all_commands(cwd)` 扫描用户目录和项目目录。用户目录采用 `Path(os.environ.get("ONECODE_HOME", Path.home() / ".onecode")) / "skills"`；项目目录采用 `Path(cwd) / ".onecode" / "skills"`。文件格式只支持目录格式：`<skills-dir>/<skill-name>/SKILL.md`。单个 `.md` 文件、legacy command 和 MCP skill 不在第一版范围内。

文件扫描必须是安全且确定性的。扫描目录不存在时返回空列表。读取失败时跳过该 skill 并记录 trace/debug 信息，但不让整个 runtime 崩溃。所有返回列表按技能名稳定排序。聚合时按 `bundled`、`user`、`project` 顺序合并，使后者覆盖前者。覆盖不是错误，但要在 metadata 或 debug 日志中留下来源信息，测试要证明项目 skill 会覆盖同名用户 skill，用户 skill 会覆盖同名 bundled skill。

然后接入 prompt catalog。可以修改 `prompts/runtime_context.py`，让 `PromptRuntimeContext` 增加 `visible_skills` 或 `skill_listing` 字段；也可以让 `DynamicPromptAssembler` 接收一个 `skill_catalog_provider` 协议对象。推荐后一种，因为 skill service 不属于工具 registry。新增或修改 `prompts/sections.py`，加入 `skills_section(context)` 或在 assembler 中追加一个 `Available Skills` section。该 section 必须只展示可调用且未被 deny 的技能，格式类似 `- code-review: Review code changes - Use when asked to review a diff`。总预算默认 8000 characters，单个描述最多 250 characters。预算超出时优先保留 bundled skills，再截断其他描述；第一版也可以保守地按排序截断，但必须有测试固定行为。

接着实现 `tools/skill/`。新增 `tools/skill/__init__.py`、`tools/skill/prompt.py` 和 `tools/skill/tool.py`。Skill 工具 descriptor 名称使用 `skill`。输入 schema 是 object，properties 为 `skill` string 和 optional `args` string，required 为 `skill`，additionalProperties 为 false。工具 prompt 应说明：当用户任务匹配任何 available skill 时，模型必须先调用 `skill` 工具；当用户引用 slash command 或 `/<name>` 时，也应调用该工具；如果当前 turn 已经加载了某个 skill，就不要重复调用同名 skill。

`tools/skill/tool.py` 的 validate 函数要去掉 `skill` 输入的前导 `/`，并拒绝空字符串。它通过 skill service 按名称查找 `SkillCommand`。如果技能不存在，返回 validation failure。如果 `disable_model_invocation` 为 true，返回 validation failure。如果 `user_invocable` 为 false，模型不能直接调用，也返回 validation failure。若技能存在但被权限策略 deny，也应在权限层处理，而不是 validate 层静默隐藏。

Skill 工具的 classify 函数应返回保守分类：`read_only=False`，`modifies_filesystem=False`，`concurrency_safe=False`，target 为 `ToolTarget(kind="session_state", operation="skill_load", value=skill_name)`。这表示 skill loading 改变会话上下文，但不直接读写文件系统。`result_policy.max_result_size_chars` 可设置为 100000，因为 fork summary 或 error 可能稍大，但 inline tool result 本身应该很短。

然后扩展工具结果协议。在 `services/tools/types.py` 中给 `ToolExecutionResult` 增加 `followup_messages: tuple[dict[str, Any], ...] = field(default_factory=tuple)`。这是工具成功后希望追加到 `MessageStore` 的 durable internal messages。修改 `services/tools/executor.py::_finalize_outcome()`，在重新包装 handler result 时保留 `followup_messages`。错误结果不应保留 follow-up messages。修改 `_apply_result_policy()` 时也要保留 follow-up messages，因为结果截断不应丢失 skill attachment。修改 `core/loop.py`，在工具执行结束后先 `append_tool_results(result_blocks)`，再收集所有 successful result 的 `followup_messages` 并调用 `message_store.append_attachments()` 或一个更通用的 `append_messages()`。当前 `MessageStore` 已有 `append_attachments()`，如果它只适合附件，本计划中的 follow-up messages 第一版全部是 attachment，可以直接使用它。

实现 inline skill handler。Skill handler 查找 `SkillCommand`，展开 args，并生成一个 `AttachmentMessage`。Skill attachment payload 形状应为 `{"type": "skill", "skill_name": name, "content": expanded_content, "args": args or "", "source": command.source, "root": str(command.root) if present, "allowed_tools": command.allowed_tools, "model": command.model}`。如果技能有 root，应在内容前加一行 `Base directory for this skill: <root>`，并把 `${ONECODE_SKILL_DIR}` 替换为该目录的字符串表示。Windows 路径可以保留 native 格式，但如果要给 shell 示例使用，可归一化为 forward slash。Inline handler 返回 `ToolExecutionResult(content=f"Launching skill: {name}", metadata={"skill_name": name, "skill_context": "inline"}, followup_messages=(skill_attachment_message,))`。

扩展 `services/attachments/projector.py`，新增 `attachment_type == "skill"` 分支。投影为一条 synthetic user message，内容包含技能边界标记和全文。推荐格式是：

    [skill loaded: code-review]
    Arguments: ...
    Source: project

    <skill content>

投影结果 metadata 使用 `{"synthetic": True, "source": "attachment", "attachment_type": "skill"}`。不要把 raw `role="attachment"` 传给 provider。不要把 skill attachment 投影成 fake `read_file`，因为技能加载不是模型真实读取文件，而是 runtime 提供的 prompt instructions。

实现 `allowed-tools` 的权限合并。当前 `PermissionPolicy` 只有 `SessionPermissionStore` 的目录 allow 和工具 denied/disabled。需要新增 session 级工具 allow 能力，例如在 `services/permissions/session.py` 中增加 `allow_tool(tool_name: str)` 和 `is_tool_allowed(tool_name: str)`。在 `services/permissions/policy.py::_ask_reasons()` 或 `evaluate()` 中，如果 ask 原因只来自该工具需要确认，并且 session store 标记该工具 allowed，则返回 allow。不要让 `is_tool_allowed()` 覆盖 `is_tool_denied()`、`is_tool_disabled()`、read-only deny 或 guard deny。Skill inline 或 fork 成功后，executor 或 Skill handler 应把 `command.allowed_tools` 合并进 session store。最干净方式是让 `ToolExecutionResult.metadata` 包含 `allowed_tools`，再让 executor 的 `_apply_success_side_effects()` 看到 `tool_name == "skill"` 时调用 permission policy/session store。若 executor 不应依赖 permission store internals，也可以在 `ToolRuntime` 中注入一个受限 permission grant callback；第一版可采用 executor side effect，但要写清测试。

实现 Skill 工具本身的权限。`PermissionPolicy.evaluate()` 应识别 `descriptor.name == "skill"` 和 classification target `operation == "skill_load"`。如果 state 或 session store deny 了 `skill` 工具，直接 deny。若 specific skill 被 deny，也直接 deny。需要为 skill 名称增加规则存储，最小实现可以在 `SessionPermissionStore` 中保存 `allowed_skills` 和 `denied_skills`，或先只通过 `state.metadata["denied_skills"]` 支持测试。第一版 safe auto-allow 规则是：bundled skills 自动 allow；user/project skill 如果只包含本计划支持的安全字段，且没有未来未知高风险字段，则自动 allow。由于 parser 会记录 frontmatter keys，`SkillCommand` 应有 `frontmatter_keys` 字段。安全字段集合为 `name`、`description`、`when_to_use`、`context`、`model`、`user-invocable`、`disable-model-invocation`、`paths`。`allowed-tools` 本身会改变权限上下文，因此存在 `allowed-tools` 的 user/project skill 仍可允许执行，但要在 metadata 中记录它将 grant 哪些工具；如果用户希望更严格，后续可改成 ask。本计划按用户要求自动允许。

实现 fork skill。当前 `SubagentRunner.run()` 只接受 `SubagentRequest` 并查内置 definition。推荐在 `services/subagents/runner.py` 增加一个新方法 `run_clean_prompt(request: SubagentRequest, *, definition: AgentDefinition) -> SubagentResult`，或增加 `SubagentRequest.metadata["dynamic_agent_definition"]` 不推荐，因为 metadata 不应承载复杂对象。更清晰的实现是新增 `services/subagents/skill_runner.py`，它复用 `SubagentRunner` 的依赖和部分 helper，或者重构 `SubagentRunner` 让它可以接收 explicit `AgentDefinition`。无论哪种方式，fork skill child 必须使用干净消息链：第一条 user message 是完整 skill prompt、args 和一段任务说明。它不继承父消息链，也不使用 `CurrentModelContext.snapshot.system_prompt`。child registry 应隐藏 `agent` 和 `skill`，避免递归。child 的 allowed tools 可以来自 skill `allowed-tools`，但仍必须经过 deny-first 权限策略。

Skill fork handler 的返回值应是普通 `ToolExecutionResult`，内容为 JSON 或简洁文本，包含 child `agent_type`、`session_id`、`final_text`、`transition` 和 `tool_result_count`。`metadata` 至少包含 `{"skill_name": name, "skill_context": "fork", "child_session_id": result.session_id, "is_error": result.is_error}`。父消息链不应追加 skill attachment，因为 skill 已经在 child 中消费。若 child 失败，Skill 工具返回 `is_error=True`，让模型有机会修正计划。

最后接入 CLI runtime。在 `ui/cli/app.py::build_runtime()` 中，先创建 skill store/provider，再创建 `skill_descriptor(...)`。`base_descriptors` 应包含 read/edit/glob/grep/bash/skill；创建 `SubagentRunner` 时也把包含 skill descriptor 的 base descriptors 传入，随后 child runner 或 child registry 可以按策略隐藏 skill。主 registry 注册所有 base descriptors，再注册 `agent_descriptor(subagent_runner)`。`DynamicPromptAssembler` 需要拿到 skill catalog provider，以便每轮 prompt 中有技能目录。`ui/cli/types.py::CliRuntime.with_session()` 也要确保新 session 后 prompt assembler 仍持有同一个 skill provider，附件 preparer 仍可投影 skill attachment。

实现完成后更新文档。`docs/design-docs/context-and-prompt-architecture.md` 要说明 `skill` attachment 是 internal role 的一种 payload，用于按需注入技能全文。`docs/design-docs/tools-runtime-architecture.md` 要说明 `ToolExecutionResult.followup_messages` 的用途和限制。`docs/design-docs/safety-and-extension-architecture.md` 要说明 `allowed-tools` 的自动允许不能覆盖 deny-first 边界。如果实现留下范围缺口，例如没有持久 skill deny rule 或没有 conditional `paths` 激活，应按 `docs/tech-debt/tech_debt_tracker_guide.md` 新增技术债。


## Concrete Steps

从仓库根目录开始：

    cd D:\study\OneCode
    git status --short

预期输出可能为空，也可能显示其他人留下的无关修改。不要使用 `git reset --hard` 或 `git checkout --`。只修改本计划点名的文件；如果某个文件已有无关改动，阅读并在其基础上追加，不要覆盖。

先阅读相关代码，确认当前实现状态：

    Get-Content services\tools\types.py
    Get-Content services\tools\executor.py
    Get-Content core\loop.py
    Get-Content services\context\message_store.py
    Get-Content services\attachments\projector.py
    Get-Content services\attachments\context_preparer.py
    Get-Content prompts\assembler.py
    Get-Content prompts\sections.py
    Get-Content services\permissions\policy.py
    Get-Content services\permissions\session.py
    Get-Content services\subagents\runner.py
    Get-Content ui\cli\app.py
    Get-Content ui\cli\types.py

第一批先写 loader 测试。新增 `tests/test_skill_loader.py`，用 `tmp_path` 创建临时 workspace 和临时 user home。通过 monkeypatch 设置 `ONECODE_HOME`。覆盖目录不存在、项目 `.onecode/skills/<name>/SKILL.md` 加载、用户目录加载、同名优先级、frontmatter 字段解析和缓存清理。运行：

    uv run python -m pytest tests\test_skill_loader.py -q

实现前预期 import error 或测试失败。实现 `services/skills/` 后，预期该测试通过。

第二批写 prompt 测试。新增 `tests/test_skill_prompt_listing.py`，构造 fake skill provider 和 `DynamicPromptAssembler`，确认 system prompt 包含 `Available Skills`，包含技能名称和描述，不包含 skill 全文，预算截断后不超过设置长度，被 deny 的 skill 不出现。运行：

    uv run python -m pytest tests\test_skill_prompt_listing.py tests\test_dynamic_prompt_assembler.py -q

第三批写 Skill 工具 inline 测试。新增 `tests/test_skill_tool.py`。构造一个 fake skill provider，注册 `skill_descriptor` 到 `ToolRegistry`，执行 `ToolCall(id="call-skill", name="skill", input={"skill": "code-review"})`。断言结果 content 是 `Launching skill: code-review`，metadata 包含 skill name，follow-up messages 中有一条 `role="attachment"` 且 attachment type 是 `skill`。再用 `AttachmentContextPreparer` 或 `AttachmentProjector` 投影这条 attachment，确认模型可见消息包含 skill 全文且没有 raw attachment role。运行：

    uv run python -m pytest tests\test_skill_tool.py tests\test_attachment_projector.py -q

第四批写 runtime integration 测试。新增 `tests/test_skill_runtime.py`，使用 fake model client 第一次返回 Skill 工具调用，第二次检查 snapshot messages 中包含 `[skill loaded: code-review]` 并返回最终文本。断言 `MessageStore.current_messages()` 中有 user、assistant、tool_result 和 durable attachment message，但没有把 skill 全文作为普通 tool result。运行：

    uv run python -m pytest tests\test_skill_runtime.py tests\test_loop.py -q

第五批写权限测试。扩展或新增 `tests/test_skill_permissions.py`。验证 bundled skill auto-allow；project skill auto-allow；`allowed-tools` 调用成功后把对应工具加入 session allow；该 allow 可以让后续普通 ask 工具变成 allow；但当该工具在 `denied_tools` 或 guard deny 中时仍然 deny。运行：

    uv run python -m pytest tests\test_skill_permissions.py tests\test_permission_policy.py -q

第六批写 fork skill 测试。新增 `tests/test_skill_fork.py`。构造 `context: fork` 的 skill，使用 fake subagent runner 或 fake model client，断言 child 请求使用干净 prompt，不包含父消息链历史；child registry 隐藏 `skill` 和 `agent`；父结果只包含 child summary，不追加 skill attachment。运行：

    uv run python -m pytest tests\test_skill_fork.py tests\test_subagent_runner.py -q

接入 CLI 后，扩展 `tests/test_async_cli_streaming.py` 或新增 CLI runtime 测试，确认 `build_runtime()` 注册了 `skill` 工具，`/tools` 或 registry descriptors 中包含 `skill`，prompt assembler 可以列出项目 `.onecode/skills` 中的 skill。运行：

    uv run python -m pytest tests\test_async_cli_streaming.py tests\test_cli_commands.py -q

最后运行编译检查和全量测试：

    uv run python -m compileall core services infrastructure tools ui
    uv run python -m pytest tests -q

实现者必须把最终输出摘要填入本计划的 `Outcomes & Retrospective`。例如：

    compileall: passed
    pytest: 230 passed

实际 passed 数以实现时仓库为准。


## Validation and Acceptance

第一，技能发现可验证。在临时 workspace 创建 `.onecode/skills/code-review/SKILL.md`，内容包含 frontmatter `description: Review code changes` 和正文 `Follow this review checklist.`。运行 skill loader 测试后，应能看到 `get_commands(workspace)` 返回 `code-review`，其 `source` 是 `project`，其 `content` 包含正文。若用户目录和项目目录都有 `code-review`，返回的 command 必须来自项目目录。

第二，prompt catalog 可验证。构建带 skill provider 的 `DynamicPromptAssembler` 后，system prompt 应包含类似：

    # Available Skills
    - code-review: Review code changes

但不包含 `Follow this review checklist.` 这种技能全文。禁用或 deny 的 skill 不应出现在 catalog。

第三，inline skill 可验证。模型调用 `skill` 工具后，工具结果应是短文本：

    Launching skill: code-review

父 `MessageStore.current_messages()` 应包含一条 durable attachment message：

    {"role": "attachment", "attachment": {"type": "skill", "skill_name": "code-review", ...}}

下一轮 `ContextEngine.build_for_model()` 的 snapshot messages 不应包含 raw `role="attachment"`，而应包含一条 synthetic user message，内容包含：

    [skill loaded: code-review]
    Follow this review checklist.

第四，fork skill 可验证。给 `SKILL.md` frontmatter 加 `context: fork` 后，Skill 工具不追加 skill attachment，而是启动 clean child agent。测试应证明 child 第一条 user message 是 skill prompt，且不包含父消息链里早先的 user prompt。父链只收到 fork child 的最终摘要。

第五，`allowed-tools` 可验证。一个 skill 声明 `allowed-tools: bash` 后，调用该 skill 成功会把 `bash` 加入本 session permission context。后续 bash 调用若原本只是因为普通 command execute 需要 ask，应被 allow；但如果 `bash` 被 `denied_tools` 禁用，或者目标路径触发 guard deny，仍然返回 deny。

第六，provider payload 可验证。任何带 skill attachment 的 runtime snapshot 都不能把 `role="attachment"` 传给 provider adapter。新增测试应直接检查 `ContextSnapshot.messages`，并间接使用 fake OpenAI-compatible provider 证明 payload roles 合法。

本功能不要求真实网络 provider 手动验证。主要验收路径是本计划列出的 focused tests、compileall 和全量 pytest。若本地 `.env` 可用，可以手动启动 CLI，在项目中创建 `.onecode/skills/code-review/SKILL.md` 后输入“使用 code-review 检查当前改动”，观察 trace 或 fake transport 中的模型第二轮上下文包含 skill loaded 消息。


## Idempotence and Recovery

本计划的实现应是可重复运行的。重复调用 `get_commands(cwd)` 应返回缓存结果，不重复扫描磁盘；调用 `clear_skill_caches()` 后应重新扫描。重复投影同一条 skill attachment 不应向 `MessageStore` 追加任何消息，也不应改变 transcript。重复运行测试应只写入 pytest 的临时目录，不污染仓库。

如果 skill 目录不存在，loader 返回空列表，不报错。如果某个 `SKILL.md` 读取失败、frontmatter 格式错误或缺少正文，该技能应被跳过或生成明确 validation error，不能让 CLI 启动失败。若某个 skill 调用失败，错误必须作为 `ToolExecutionResult(is_error=True)` 回填给模型，而不是让主循环崩溃。

如果实现中发现 `followup_messages` 影响了已有工具结果预算或 transcript 外置逻辑，应优先保持旧工具行为不变：旧工具默认 follow-up 为空，现有 tests 不需要改预期。任何 result truncation 都只能处理 tool result content，不能截断或丢弃 follow-up attachment；如果 skill 内容过大，后续应在 attachment projector 或 context compaction 层处理，而不是在 tool result budget 中隐式截断。

不要使用 destructive git commands 进行恢复。若某个改动方向错误，使用 targeted `apply_patch` 删除本计划新增的行或文件。当前仓库可能有用户或其他 agent 的未提交改动，不能 revert 无关文件。


## Artifacts and Notes

参考材料位于 `docs/references/s07_skill_loading/`。其中最重要的行为是两级加载：启动时只宣布 catalog，模型调用 Skill 工具时才加载完整 `SKILL.md`。参考实现还支持 MCP、插件、legacy commands、动态 discovery、hooks、shell 和远程 skill；这些不属于本计划第一版。

OneCode 当前附件系统提供了可以复用的 durable internal message 形状：

    {
        "role": "attachment",
        "content": "",
        "attachment": {
            "type": "skill",
            "skill_name": "code-review",
            "content": "Base directory for this skill: ...\n\n...",
            "args": "",
            "source": "project",
            "allowed_tools": ["bash"],
            "model": null
        },
        "metadata": {
            "attachment_id": "...",
            "attachment_type": "skill",
            "scope": "main_thread",
            "source": "skill_tool"
        }
    }

Skill attachment 的 projected message 可采用以下形状：

    {
        "role": "user",
        "content": "[skill loaded: code-review]\nArguments: \nSource: project\n\nBase directory for this skill: D:\\study\\OneCode\\.onecode\\skills\\code-review\n\nFollow this review checklist.",
        "metadata": {
            "synthetic": true,
            "source": "attachment",
            "attachment_type": "skill"
        }
    }

Skill 工具 provider schema 应类似：

    {
        "type": "function",
        "function": {
            "name": "skill",
            "description": "Load and execute a OneCode skill by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string"},
                    "args": {"type": "string"}
                },
                "required": ["skill"],
                "additionalProperties": false
            }
        }
    }


## Interfaces and Dependencies

不要新增第三方依赖。Frontmatter 解析、文件扫描、缓存和路径处理使用 Python 标准库：`dataclasses`、`pathlib`、`os`、`functools`、`typing` 和必要时 `re`。

在 `services/skills/types.py` 中定义：

    @dataclass(frozen=True)
    class SkillCommand:
        name: str
        description: str
        content: str
        source: Literal["bundled", "user", "project"]
        root: Path | None = None
        when_to_use: str | None = None
        allowed_tools: tuple[str, ...] = ()
        context: Literal["inline", "fork"] = "inline"
        model: str | None = None
        user_invocable: bool = True
        disable_model_invocation: bool = False
        paths: tuple[str, ...] = ()
        frontmatter_keys: frozenset[str] = frozenset()

在 `services/skills/loader.py` 中定义：

    def init_bundled_skills(commands: Iterable[SkillCommand] = ()) -> None: ...
    def get_commands(cwd: Path | str) -> tuple[SkillCommand, ...]: ...
    def load_all_commands(cwd: Path | str) -> tuple[SkillCommand, ...]: ...
    def find_command(name: str, cwd: Path | str) -> SkillCommand | None: ...
    def clear_skill_caches() -> None: ...

`get_commands(cwd)` 是对外入口，内部调用 memoized `load_all_commands(cwd)`。缓存 key 应使用 resolved cwd 字符串。测试应能通过 `clear_skill_caches()` 重置状态。

在 `prompts/assembler.py` 中给 `DynamicPromptAssembler` 增加可选参数：

    skill_provider: SkillCatalogProvider | None = None

协议形状为：

    class SkillCatalogProvider(Protocol):
        def visible_skills(self, state: RuntimeState, cwd: Path) -> Iterable[SkillCommand]: ...

也可以把 provider 放在 `services/skills/catalog.py`。关键是 assembler 不直接扫描磁盘，扫描与缓存属于 skills service。

在 `services/tools/types.py` 中扩展：

    @dataclass(frozen=True)
    class ToolExecutionResult:
        tool_call_id: str
        tool_name: str
        content: str
        is_error: bool = False
        metadata: dict[str, Any] = field(default_factory=dict)
        followup_messages: tuple[dict[str, Any], ...] = field(default_factory=tuple)

在 `tools/skill/tool.py` 中提供：

    def descriptor(
        *,
        skill_provider: SkillProvider,
        permission_grants: SkillPermissionGrantSink | None = None,
        fork_runner: SkillForkRunner | None = None,
        cwd: Path | Callable[[], Path],
    ) -> ToolDescriptor: ...

如果实际实现选择更简单的构造参数也可以，但 descriptor 必须能查找 skill、生成 attachment、授予 allowed tools，并在 fork 时调用 child runner。

在 `services/attachments/projector.py` 中新增：

    if attachment_type == "skill":
        return (_project_skill(attachment),)

`_project_skill()` 返回一条 synthetic user message。

在 `services/permissions/session.py` 中增加 session 工具 allow 能力：

    def allow_tool(self, tool_name: str) -> None: ...
    def is_tool_allowed(self, tool_name: str) -> bool: ...

如需 skill-specific allow/deny，增加：

    def allow_skill(self, skill_name: str) -> None: ...
    def deny_skill(self, skill_name: str) -> None: ...
    def is_skill_allowed(self, skill_name: str) -> bool: ...
    def is_skill_denied(self, skill_name: str) -> bool: ...

第一版可以只实现 allow path，并用 `RuntimeState.metadata["denied_skills"]` 覆盖 deny 测试；但最终设计应保留 skill-specific deny 的位置。

在 `services/subagents/runner.py` 或新增 `services/subagents/skill_runner.py` 中提供一个干净 skill runner。推荐接口：

    @dataclass(frozen=True)
    class SkillForkRequest:
        skill_name: str
        prompt: str
        allowed_tools: tuple[str, ...]
        parent_session_id: str
        parent_tool_call_id: str

    class SkillForkRunner:
        async def run_skill(self, request: SkillForkRequest) -> SubagentResult: ...

该 runner 创建 child `RuntimeState`、`MessageStore`、`ToolRegistry`、`ContextEngine`、`RegistryToolExecutor` 和 `AgentLoop`，但 child store 只 seed 一条 user message。child registry 必须隐藏 `agent` 和 `skill`。


## Revision Notes

2026-06-07 / Codex: 初始计划根据用户确认的 8 条设计约束创建。计划记录了用户目录 + 项目 `.onecode/skills` 的技能来源、`project > user > bundled` 优先级、inline skill 使用附件系统、`allowed-tools` 自动合并权限上下文、fork skill 使用干净上下文，以及第一版 frontmatter 字段范围。
