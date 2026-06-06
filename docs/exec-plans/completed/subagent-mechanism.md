# 实现内置 Subagent 机制

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本文遵守仓库根目录 `PLANS.md`。后续执行者只需要阅读本文和当前工作树，就能实现、验证并维护 OneCode 的第一版 subagent 机制。本文是中文计划，不依赖本次对话上下文。

## Purpose / Big Picture

完成本计划后，OneCode 的主 agent 可以通过一个新的 `agent` 工具把子任务交给内置 subagent 执行。普通 subagent 使用干净的消息链，适合搜索、研究和计划；fork subagent 在模型省略 `subagent_type` 时隐式触发，并继承父 agent 的完整上下文和父轮次已经渲染的 system prompt 字节，以便在需要“从当前上下文分叉继续做事”时不丢失父 agent 已经知道的信息。

用户可见行为是：在 CLI 中请求“用 Explore agent 搜索某个实现位置”，父 agent 会调用 `agent` 工具，子 agent 独立运行并只把最终摘要作为工具结果返回父 agent；在父 agent 调用 `agent` 工具但省略 `subagent_type` 时，系统会创建 fork child，fork child 能看到父会话完整历史，但不会把自己的中间消息写回父消息链。Explore 和 Plan 是硬性只读：即使模型试图写文件或运行可能修改状态的 bash 命令，运行时也必须拒绝，而不是只依赖 system prompt 约束。

## Progress

- [x] (2026-06-05 23:20+08:00) 已阅读 `AGENTS.md`、`PLANS.md`、`architecture.md`、设计文档、当前 active ExecPlan、技术债 tracker、`docs/references/s06_subagent/` 教学版和 `AgentTool` 参考实现。
- [x] (2026-06-05 23:25+08:00) 已核对当前 OneCode 代码状态：`core/loop.py` 已有 async `AgentLoop.stream()`；`services/tools/executor.py` 已是 async generator；`services/observability/` 已提供 trace；CLI 已装配 registry-backed 工具。
- [x] (2026-06-05 23:30+08:00) 已和用户确认第一版范围：省略 `subagent_type` 始终触发 fork；只实现四个内置 agent；fork system prompt 必须字节级继承；child 直接隐藏 `agent` 工具；共享临时授权并实现 bubble 权限询问；不做 background；不做 worktree；Explore/Plan 用硬限制只读。
- [x] (2026-06-06 +08:00) 实现 `services/subagents/` 的类型、内置定义、fork 消息构造和 runner。
- [x] (2026-06-06 +08:00) 新增 `tools/agent/` 工具，并把它注册进 CLI runtime。
- [x] (2026-06-06 +08:00) 扩展 loop/executor 所需的父上下文传递机制，让 fork child 能读取父消息链和父轮次已渲染 system prompt；`ToolRuntime` 也携带当前 tool call id，供 `agent` 工具记录父调用来源。
- [x] (2026-06-06 +08:00) 增加普通、Explore、Plan、fork、权限 bubble 和递归隐藏相关测试。
- [x] (2026-06-06 +08:00) 运行全量测试与 compile check，并根据实际输出更新本文的 `Artifacts and Notes` 与 `Outcomes & Retrospective`。

## Surprises & Discoveries

- Observation: OneCode 当前已经不是纯同步 runtime，subagent 不应按旧的阻塞 `run(prompt) -> str` 设计。
  Evidence: `core/loop.py` 中主入口是 `async def stream(self, prompt: str) -> AsyncIterator[AgentEvent]`，CLI 已经用 `async for event in runtime.loop.stream(line)` 渲染流式输出。

- Observation: fork agent 在参考实现中不是干净上下文 subagent，而是继承父上下文的特殊路径。
  Evidence: `docs/references/s06_subagent/AgentTool/forkSubagent.ts` 的 `FORK_AGENT` 注释说明省略 `subagent_type` 会触发隐式 fork；`buildForkedMessages()` 会保留父 assistant message，并用 placeholder tool result 修复 tool call 前缀。

- Observation: 参考实现的 cache 命中要求 system prompt、tools、model、message prefix 和 thinking config 等都一致；本计划只要求 system prompt 字节级继承，因为用户明确要求 child 直接隐藏 `agent` 工具。
  Evidence: `docs/references/s06_subagent/AgentTool/forkedAgent.ts` 的 `CacheSafeParams` 注释说明 cache key 依赖多个字段；用户确认的第一版语义是“字节级”继承父 system prompt，同时“直接隐藏” agent 工具。

- Observation: 当前 `ToolRuntime` 只包含 `RuntimeState`、guard 和已批准 guard policy；`agent` 工具要实现 fork 必须能访问父 `MessageStore` 和父轮次 `ContextSnapshot`。
  Evidence: `services/tools/types.py` 的 `ToolRuntime` 没有 message store 或 snapshot 字段；`core/loop.py` 在每轮调用模型前拿到 `snapshot = await self.context_engine.build_for_model(self.state)`，但执行工具时没有把这个 snapshot 暴露给工具 handler。

- Observation: CLI `/resume` 复用已有 `ToolRegistry`，所以 `agent` descriptor 闭包里的 runner 不能永久绑定初始父 `MessageStore`。
  Evidence: `ui/cli/types.py` 的 `CliRuntime.with_session()` 会重建 `ContextEngine` 和 `AgentLoop`，但不会重建 registry；实现中给 `SubagentRunner` 增加 `bind_parent_message_store()`，在 resume/clear 后更新 fork 来源消息链。

- Observation: `agent` 工具需要父 tool call id，但 handler 原先只能通过 `ToolRuntime` 看到 state、guard 和已批准 guard policy。
  Evidence: `services/tools/executor.py` 在 preflight 时已经持有 `ToolCall`，实现中把 `tool_call.id` 写入 `ToolRuntime.tool_call_id`，保持默认空字符串以兼容现有工具测试。

## Decision Log

- Decision: `subagent_type` 省略时始终触发 fork path，不做 feature gate，也不回退到 `general-purpose`。
  Rationale: 用户要求“始终触发”。这让模型可以通过省略类型表达“从当前上下文分叉继续”，而显式 `subagent_type="general-purpose"` 才表示干净上下文的通用 agent。
  Date/Author: 2026-06-05 / Codex

- Decision: 第一版只实现四个内置 agent：`fork`、`general-purpose`、`Explore` 和 `Plan`。
  Rationale: 用户明确要求“内置四类”。不实现 user/project/plugin agent loader，避免把文件格式、配置优先级和权限规则混进第一版 runtime 机制。
  Date/Author: 2026-06-05 / Codex

- Decision: fork child 的 system prompt 必须使用父轮次已渲染的原始字符串，不重新调用 prompt assembler。
  Rationale: 用户要求“字节级”。重新组装 system prompt 可能因为时间、状态、工具可见性或 future prompt section cache 差异而改变字节。
  Date/Author: 2026-06-05 / Codex

- Decision: 所有 child agent 默认隐藏 `agent` 工具，fork child 也隐藏。
  Rationale: 用户在 fork 工具池问题上选择“直接隐藏”。这比保留 `agent` 后在执行时拒绝递归更简单、更安全，也符合第一版不支持嵌套 subagent 的目标。
  Date/Author: 2026-06-05 / Codex

- Decision: 子 agent 共享父 session 的临时授权和同一个 permission prompter，权限询问以 bubble 方式回到父 CLI。
  Rationale: 用户要求“共享临时授权，实现 bubble”。OneCode 当前的 `SessionPermissionStore` 是内存 session 授权；第一版 child runner 应复用同一个 store、policy 和 prompter，而不是复制出独立授权状态。
  Date/Author: 2026-06-05 / Codex

- Decision: 第一版不实现 `run_in_background`，不实现 worktree 隔离。
  Rationale: 用户明确回答“不做”和“不需要”。这让第一版可以保持同步等待子 agent 完成，先验证上下文隔离、fork 继承、权限和工具裁剪。
  Date/Author: 2026-06-05 / Codex

- Decision: Explore 和 Plan 的只读限制必须由代码强制执行。
  Rationale: 用户要求“使用硬限制”。System prompt 仍会提醒模型只读，但真正边界必须在工具可见性、工具执行分类或 permission policy 中生效。
  Date/Author: 2026-06-05 / Codex

## Outcomes & Retrospective

2026-06-06 实现完成第一版内置 subagent 机制。主 agent 现在可以通过 `tools/agent/` 的 `agent` descriptor 同步运行 child loop；显式 `general-purpose`、`Explore` 和 `Plan` 使用干净 child 消息链，省略 `subagent_type` 会走 fork 并复用父轮次已渲染 system prompt 字符串。所有 child registry 都隐藏 `agent`；`Explore` 和 `Plan` 通过 `PermissionPolicy` 中的 `read_only_agent` deny-first 检查硬拒绝非只读或修改文件系统的工具调用。

实际落地文件包括 `services/subagents/types.py`、`definitions.py`、`forking.py`、`context.py`、`runner.py`、`tools/agent/tool.py`、`tools/agent/prompt.py`、`core/loop.py`、`services/context/message_store.py`、`services/permissions/policy.py`、`services/tools/types.py`、`services/tools/executor.py`、`ui/cli/app.py` 和 `ui/cli/types.py`。新增测试覆盖内置定义、fork message repair、clean/fork runner、递归隐藏、只读硬限制、权限 bubble 和 agent 工具请求投影。

保留风险已记录到 `docs/tech-debt/tech-debt-tracker.md` 的 TD-010：第一版不支持 background subagent、worktree 隔离、用户/项目/插件自定义 agent，也没有完整 provider prompt-cache identical fork 参数校验。

## Context and Orientation

OneCode 是 Python code agent runtime。主循环在 `core/loop.py`，它负责接收用户 prompt、构建上下文、调用模型、执行工具、写回工具结果，然后继续下一轮。主循环必须保持薄，不应该直接知道 `agent` 工具、Explore、Plan 或 fork 的具体分支。

上下文重建在 `core/context_engine.py`。它从 `services/context/message_store.py` 读取当前消息，用 `prompts/assembler.py` 生成 system prompt，并从 `ToolRegistry` 读取当前模型可见工具 schema。`ContextSnapshot` 是一次模型调用的完整输入，包含 system prompt、messages 和 tool schemas。本文中“父轮次已渲染 system prompt 字节”指的就是父 `AgentLoop` 在当前工具调用之前构造的 `ContextSnapshot.system_prompt` 字符串，不能重新组装。

工具运行时在 `services/tools/`。`services/tools/types.py` 定义 `ToolDescriptor`、`ToolCall`、`ToolExecutionResult`、`ToolRuntime` 和 input-aware 的 `ToolCallClassification`。`services/tools/registry.py` 管理当前启用工具，并用同一个可见工具视图生成 provider schema 和 system prompt 中的工具说明。`services/tools/executor.py` 执行工具，顺序是 schema 校验、工具校验、分类、guard、permission、hook、handler、结果预算和 hook。新增 `agent` 工具必须通过这个 descriptor/executor 路径接入，不得在 `core/loop.py` 里写特殊工具名分支。

权限在 `services/permissions/`。`SessionPermissionStore` 保存本 session 临时授权，`PermissionPolicy` 做 deny-first 决策，`PermissionPrompter` 是 CLI 或其他 UI 对用户询问权限的接口。“bubble 权限”在本文中表示：子 agent 遇到 ask 时，不自己创建独立 UI，也不默认拒绝，而是复用父 runtime 的 prompter，在父终端里询问用户；用户允许后写入同一个 `SessionPermissionStore`，父子 agent 后续都能看到这条临时授权。

可观测性在 `services/observability/`。`TraceRecorder` 会把 `interaction`、`model_call`、`tool_batch`、`tool_result` 等事件写入 `.onecode/<session_id>/trace.jsonl`。Subagent 应新增 `subagent_start`、`subagent_completed`、`subagent_error` 这类安全摘要事件，只记录 agent 类型、child session id、parent session id、是否 fork、是否只读、工具调用数量、耗时和 token 计数，不记录 prompt 全文、源码、工具输出全文或 secret。

当前 CLI 装配在 `ui/cli/app.py`。它创建 `RuntimeState`、`MessageStore`、`ToolRegistry`、`DynamicPromptAssembler`、`ContextEngine`、guard、permission、trace、tool executor 和 model client。新增 `agent` 工具时，CLI 是第一版主要装配入口；测试可以手动装配 runner 和 fake model client。

本文使用的几个术语如下。Subagent 是一个 child agent loop，有自己的 `RuntimeState` 和 `MessageStore`，由父 agent 通过 `agent` 工具启动。普通 subagent 是干净上下文子 agent，初始消息只有本次子任务 prompt。Fork subagent 是特殊 child，它继承父消息链和父 system prompt，并在当前父 assistant tool call 后追加 placeholder tool results 和 fork directive。Fork directive 是给 fork child 的用户消息，告诉它“你是分叉 worker，不要再派生子 agent，直接完成这次指令”。Read-only agent 是硬限制只读的 child，运行时拒绝任何修改文件系统或可能修改状态的工具调用。

## Plan of Work

第一步新增 `services/subagents/types.py`。定义 `AgentDefinition`、`SubagentRequest`、`SubagentResult` 和 `SubagentRunMode`。`AgentDefinition` 至少包含 `agent_type`、`when_to_use`、`source`、`system_prompt`、`tools`、`disallowed_tools`、`max_turns`、`model`、`read_only` 和 `hidden`。第一版 `source` 固定为 `"built-in"`。`SubagentRequest` 至少包含 `prompt`、`subagent_type`、`parent_session_id`、`parent_tool_call_id`、`mode`、`metadata`。`SubagentResult` 至少包含 `agent_type`、`session_id`、`final_text`、`is_error`、`transition`、`usage`、`tool_result_count` 和 `metadata`。

第二步新增 `services/subagents/definitions.py`。在这里定义四个内置 agent。`general-purpose` 的 prompt 应强调复杂搜索、多步研究和简洁报告，工具策略是可用所有普通基础工具但隐藏 `agent`。`Explore` 的 prompt 强调文件搜索和代码阅读，`read_only=True`，隐藏 `agent`、`edit_file` 和未来 `write_file`。`Plan` 的 prompt 强调探索代码并设计实现计划，`read_only=True`，同样隐藏写工具和 `agent`。`fork` 是隐藏 synthetic definition，不出现在模型可选 `subagent_type` 列表中，只由省略 `subagent_type` 触发；它的 `system_prompt` 字段不被使用，因为 fork 必须继承父 prompt。

第三步新增 `services/subagents/forking.py`。实现 `build_forked_messages(parent_messages, directive)`。这个函数接收父 `MessageStore.current_messages()` 的副本和模型给 `agent` 工具的 prompt。它必须复制父完整消息链。如果最后一条 assistant message 含有 `tool_calls`，并且这些 tool call 在父消息链中还没有对应 `tool_result`，就为每个 tool call 追加一个 placeholder `tool_result` 消息，内容固定为 `Fork started - processing in child agent`。然后追加一条 user 消息，内容为 fork boilerplate 加用户 directive。boilerplate 必须明确：child 是 fork worker，不是主 agent；不能再调用 `agent`；必须直接使用工具完成任务；最终回答要简洁、事实化、限定在 directive 范围内。这个函数不能修改父消息对象，必须 deep copy。

第四步新增一个“当前模型上下文”持有对象，让 fork 能拿到父轮次已经渲染的 system prompt。建议新增 `services/subagents/context.py`，定义 `CurrentModelContext` dataclass，字段为 `snapshot: ContextSnapshot | None = None`。修改 `core/loop.py` 的 `AgentLoop.__init__()` 增加可选 `current_model_context` 参数；每次 `_run_loop_async()` 构造出 `snapshot` 后，立即设置 `current_model_context.snapshot = snapshot`。这只是把已存在的 snapshot 暴露给装配层，不改变主循环决策，也不引入工具名分支。

第五步扩展 `MessageStore`，让 child runner 可以安全预置消息。建议在 `services/context/message_store.py` 添加 `seed_messages(messages)`。它只能在当前内存消息为空时调用；对每条传入消息执行 `_append()`，确保 child transcript 也有完整记录。这个方法用于 fork child 预置父上下文，也可用于普通 child 先写入 user prompt 再调用 loop 的现有继续逻辑。

第六步给 `AgentLoop` 增加一个公开的“从已有消息继续运行”入口。建议命名为 `continue_stream()`，签名为 `async def continue_stream(self) -> AsyncIterator[AgentEvent]`，它只包一层 trace span 后调用 `_run_loop_async()`，不追加新的 user prompt。普通父 CLI 仍使用 `stream(prompt)`。Subagent runner 用 `MessageStore.seed_messages()` 写入初始消息后调用 `continue_stream()`，避免 fork child 被额外追加一个重复 prompt。

第七步新增 `services/subagents/runner.py`。`SubagentRunner` 构造时接收 workspace、父 `MessageStore`、父 `CurrentModelContext`、model client、基础 tool descriptors、permission store/policy/prompter、guard、trace recorder 和 transcript root。Runner 的 `run(request)` 根据 `request.subagent_type` 决定路径：为空时选择 fork；非空时在内置定义中查找。普通路径创建新的 `RuntimeState` 和 `MessageStore`，seed 一条 user prompt，使用 agent 自己的 system prompt 组装 child `ContextEngine`。Fork 路径复制父消息链，调用 `build_forked_messages()`，使用 `StaticPromptAssembler(parent_snapshot.system_prompt)`，并把 `RuntimeState.metadata["is_fork_child"] = True`。两条路径都创建 child `ToolRegistry`，其中必须隐藏 `agent` 工具。Runner drain child `AgentLoop.continue_stream()`，收集最终 `completed` 文本、usage、transition 和工具结果数量，返回 `SubagentResult`。

第八步实现工具过滤和只读硬限制。新增 `services/subagents/tools.py` 或在 runner 内部实现 `build_child_registry(definition, base_descriptors, permission_policy)`。它根据 `tools` 和 `disallowed_tools` 过滤 descriptor，并总是移除 `agent`。对于 `read_only=True` 的 agent，除了隐藏已知写工具外，还必须在执行入口硬拒绝非只读调用。推荐在 `services/permissions/policy.py` 中增加对 `state.metadata["read_only_agent"] is True` 的判断：如果 `classification.read_only` 不是 True，或 `classification.modifies_filesystem` 是 True，则返回 deny，reason 为 `Read-only subagent cannot execute state-changing tool calls.`。这能覆盖 bash 这类 input-aware 工具，即使它仍对模型可见，也不能执行写入或未知副作用命令。

第九步新增 `tools/agent/`。创建 `tools/agent/__init__.py`、`tools/agent/tool.py` 和 `tools/agent/prompt.py`。`descriptor(runner)` 返回 `ToolDescriptor`，名称为 `agent`，输入 schema 包含必填 `prompt` 和可选 `subagent_type`。不要加入 `run_in_background` 字段。handler 从 input 取 prompt 和 subagent_type，创建 `SubagentRequest`，调用 runner。由于当前 `ToolHandler` 是同步 callable，但 executor 已支持 coroutine function，`agent` handler 可以是 `async def`，由 `RegistryToolExecutor._run_handler_async()` await。工具结果 content 应只包含 child final summary 和必要安全摘要，例如 agent type、child session id、是否 fork、工具调用数量、transition；不要返回 child 完整消息链。

第十步把 `agent` 工具注册进 CLI。编辑 `ui/cli/app.py`：先创建不含 `agent` 的 base descriptors；创建 `ToolRegistry`；创建 `CurrentModelContext` 并传给 parent `AgentLoop`；创建 `SubagentRunner`；调用 `registry.register(agent_descriptor(runner))`。注意 prompt assembler 持有同一个 registry，所以注册后 parent 模型会在下一轮看到 `agent` schema 和 prompt。构造 `SubagentRunner` 时复用父 `PermissionPolicy`、`SessionPermissionStore`、`CliPermissionPrompter` 和 `TraceRecorder`，实现共享授权和 bubble 权限。

第十一步增加 trace。Runner 开始时记录 `subagent_start`，属性包括 `agent_type`、`parent_session_id`、`child_session_id`、`is_fork`、`read_only`。完成时记录 `subagent_completed`，属性包括 `transition`、`tool_result_count`、`input_tokens`、`output_tokens`、`duration_ms`。异常时记录 `subagent_error`，只记录错误类型和安全错误消息。不要记录子任务 prompt 全文；可以记录 `prompt_length`。

第十二步补充测试。新增 `tests/test_subagent_definitions.py`，验证四个内置 agent 定义存在、fork hidden、Explore/Plan read_only、普通 agent 非 read_only。新增 `tests/test_subagent_forking.py`，验证 `build_forked_messages()` deep copy 父消息、为未配对 tool calls 插入固定 placeholder、追加 fork directive，并且不修改父消息。新增 `tests/test_subagent_runner.py`，用 fake streaming model client 验证普通 subagent 使用干净消息、fork subagent 使用父 system prompt 的同一个字符串值、child registry 隐藏 `agent`、父消息链不包含 child 中间消息。新增 `tests/test_agent_tool.py`，验证省略 `subagent_type` 触发 fork，显式 `general-purpose` 触发普通路径，Explore/Plan 非只读工具调用被 permission policy deny，权限 ask 复用同一个 fake prompter 和 session store。

第十三步更新文档和技术债。实现后更新 `architecture.md`，在 `services/` 下加入 `subagents/`，在 `tools/` 下加入 `agent/`，并说明 subagent 不进入主循环工具名分支。更新 `docs/tech-debt/tech-debt-tracker.md`：如果第一版仍没有 background、worktree、用户自定义 agent 和完全 prompt-cache identical fork，就新增或更新一条明确技术债，说明这些是有意推迟的能力。

## Concrete Steps

从仓库根目录开始：

    D:\study\OneCode

先确认当前工作树和基线测试。当前仓库已有较多未提交改动，执行者不能回滚与本计划无关的变更。运行：

    git status --short
    uv run python -m pytest tests -q

如果全量测试因为当前活跃 async 重构未完成而失败，先运行与本计划相关的现有目标测试，记录真实输出到本文：

    uv run python -m pytest tests\test_loop.py tests\test_tool_registry_and_executor.py tests\test_cli_commands.py -q

实现低层类型和定义后，运行：

    uv run python -m pytest tests\test_subagent_definitions.py tests\test_subagent_forking.py -q

预期新增测试通过，且这些测试不需要真实模型 API。

实现 runner 和 `agent` 工具后，运行：

    uv run python -m pytest tests\test_subagent_runner.py tests\test_agent_tool.py -q

预期 fake model client 驱动 child loop 完成，省略 `subagent_type` 的请求被记录为 fork，显式 `Explore`/`Plan` 的写入尝试返回 permission deny。

最后运行：

    uv run python -m pytest tests -q
    uv run python -m compileall core services infrastructure tools ui

如果成功，预期输出形态类似：

    <N> passed in <seconds>s
    Listing 'core'...
    Listing 'services'...
    Listing 'tools'...
    Listing 'ui'...

实际的测试数量可能因当前工作树变化而不同。完成者必须把真实输出摘要写入 `Artifacts and Notes`。

## Validation and Acceptance

第一类验收是普通 subagent 上下文隔离。测试中让父 message store 先包含若干父消息，再调用显式 `subagent_type="general-purpose"` 的 `agent` 工具。child fake model client 收到的第一轮 snapshot.messages 只能包含子任务 user prompt，不能包含父消息。父 message store 在工具执行后只能新增一个 `tool_result`，不能新增 child 的中间 assistant/tool_result 消息。

第二类验收是 fork 继承。测试中构造父 `ContextSnapshot(system_prompt="EXACT_PARENT_PROMPT", messages=...)`，父最后 assistant message 包含 `agent` tool call。调用省略 `subagent_type` 的 `agent` 工具后，child fake model client 收到的 snapshot.system_prompt 必须等于 `"EXACT_PARENT_PROMPT"`，并且 child messages 包含父历史、placeholder tool_result 和 fork directive。这个测试证明 fork 使用父轮次已渲染 prompt，而不是重新组装 prompt。

第三类验收是递归隐藏。无论 child 是 fork、general-purpose、Explore 还是 Plan，child registry 的 visible tool schemas 都不能包含 `agent`。如果模型在 child 历史或 fake response 中手写了 `agent` tool call，executor 应返回 unknown tool 或不可见工具错误，而不是启动第二层 subagent。

第四类验收是只读硬限制。Explore 和 Plan child 中，`edit_file` 不应出现在 schema；bash 如果请求 `touch x.txt`、`git add .`、redirect 写入或其他 classifier 判定为非只读/未知副作用的调用，`PermissionPolicy` 必须返回 deny，handler 不执行，文件系统不改变。测试应在 pytest tmp directory 中断言目标文件不存在。

第五类验收是 bubble 权限。构造一个需要 ask 的 child 工具调用，注入 fake `PermissionPrompter`，断言 prompter 被调用一次，且调用发生在 child 执行期间；允许后同一个 `SessionPermissionStore` 中出现授权，父 runner 之后可以复用这条授权。拒绝时 child 工具结果为 `permission_denied`，父 `agent` 工具结果应说明 subagent failed 或返回包含错误摘要的 final result。

第六类验收是 CLI 装配。启动 CLI 后执行 `/tools` 应能看到 `agent` 工具。让 fake 或真实模型调用 `agent` 工具后，CLI 应显示一个工具结果摘要，trace 文件 `.onecode/<session_id>/trace.jsonl` 应包含 `subagent_start` 和 `subagent_completed` 安全摘要事件。默认第一版不会返回 `async_launched`，也不会创建 worktree。

## Idempotence and Recovery

本计划主要是新增模块、给现有构造函数增加可选参数，以及注册一个新工具。重复运行测试是安全的。测试必须使用 pytest temporary directories，不得写真实仓库外路径。

不要使用 `git reset --hard`、`git checkout --` 或删除当前未提交文件来恢复失败。当前工作树已经包含其他活跃重构的改动，执行者只能修改本计划相关文件，并在遇到无关失败时记录失败而不是回滚别人的工作。

如果 `CurrentModelContext.snapshot` 在某些路径为空，fork runner 必须返回结构化 tool error，说明 `fork_context_unavailable`，而不是重新组装父 prompt 冒充字节级继承。普通 `general-purpose`、Explore 和 Plan 不依赖父 snapshot，仍可运行。

如果 read-only enforcement 起初只在 tool registry 隐藏写工具，但 bash 仍可能执行未知副作用命令，不能把计划标记完成。必须在 permission 或 executor 层基于 `ToolCallClassification` 硬拒绝非只读调用。

如果 child loop 抛出 provider error，runner 应捕获并返回 `SubagentResult(is_error=True, final_text=<safe error summary>)`，同时记录 `subagent_error` trace。父主循环不应因为一个 child provider error 崩溃，除非错误发生在 runtime 装配阶段且无法创建 child。

## Artifacts and Notes

初始研究证据：

    docs/references/s06_subagent/AgentTool/loadAgentsDir.ts defines AgentDefinition with tools, disallowedTools, model, permissionMode, maxTurns and source.
    docs/references/s06_subagent/AgentTool/built-in/generalPurposeAgent.ts defines the general-purpose built-in prompt and tools ['*'].
    docs/references/s06_subagent/AgentTool/built-in/exploreAgent.ts and planAgent.ts define read-only built-ins and disallow Agent/Edit/Write tools.
    docs/references/s06_subagent/AgentTool/AgentTool.tsx routes omitted subagent_type to fork when fork is enabled.
    docs/references/s06_subagent/AgentTool/forkSubagent.ts defines FORK_AGENT, buildForkedMessages(), fixed placeholder tool result, and recursive fork guard.
    docs/references/s06_subagent/AgentTool/forkedAgent.ts defines CacheSafeParams and createSubagentContext() isolation defaults.

当前 OneCode 相关代码证据：

    core/loop.py exposes async AgentLoop.stream(prompt).
    core/context_engine.py builds ContextSnapshot with system_prompt, messages and tool_schemas.
    services/context/message_store.py stores user, assistant and tool_result messages and writes transcript JSONL.
    services/tools/executor.py executes tools through async generator updates and already supports coroutine handlers.
    services/tools/types.py ToolRuntime now carries the current tool_call_id so the agent tool can populate parent_tool_call_id, while fork still gets parent MessageStore and ContextSnapshot through SubagentRunner and CurrentModelContext.
    ui/cli/app.py is the first runtime composition point for registering the new agent tool.

实现完成后，在这里粘贴真实验证输出，例如：

    uv run python -m pytest tests\test_subagent_definitions.py tests\test_subagent_forking.py tests\test_subagent_runner.py tests\test_agent_tool.py -q
    10 passed in 0.29s

    uv run python -m pytest tests -q
    158 passed in 2.13s

    uv run python -m compileall core services infrastructure tools ui
    Listing 'core'...
    Listing 'services'...
    Listing 'infrastructure'...
    Listing 'tools'...
    Listing 'ui'...
    succeeded

## Interfaces and Dependencies

本计划不新增第三方依赖。继续使用 Python 标准库、现有 async runtime、现有 model client、现有 tool executor、现有 permission policy 和 trace recorder。

在 `services/subagents/types.py` 中定义：

    @dataclass(frozen=True)
    class AgentDefinition:
        agent_type: str
        when_to_use: str
        system_prompt: str
        source: Literal["built-in"] = "built-in"
        tools: tuple[str, ...] = ("*",)
        disallowed_tools: tuple[str, ...] = ()
        max_turns: int | None = None
        model: str | None = None
        read_only: bool = False
        hidden: bool = False

    @dataclass(frozen=True)
    class SubagentRequest:
        prompt: str
        subagent_type: str | None
        parent_session_id: str
        parent_tool_call_id: str
        metadata: dict[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True)
    class SubagentResult:
        agent_type: str
        session_id: str
        final_text: str
        is_error: bool = False
        transition: str | None = None
        usage: ModelUsage | None = None
        tool_result_count: int = 0
        metadata: dict[str, Any] = field(default_factory=dict)

在 `services/subagents/context.py` 中定义：

    @dataclass
    class CurrentModelContext:
        snapshot: ContextSnapshot | None = None

在 `services/subagents/runner.py` 中定义：

    class SubagentRunner:
        async def run(self, request: SubagentRequest) -> SubagentResult:
            ...

Runner 构造函数应显式接收依赖，而不是从全局变量读取：

    def __init__(
        self,
        *,
        workspace: Path,
        transcript_root: Path,
        parent_message_store: MessageStore,
        current_model_context: CurrentModelContext,
        model_client: ModelClient,
        base_descriptors: tuple[ToolDescriptor, ...],
        guard: SandboxGuard,
        permission_policy: PermissionPolicy,
        permission_prompter: PermissionPrompter | None,
        trace_recorder: TraceRecorder,
    ) -> None:
        ...

在 `tools/agent/tool.py` 中定义：

    def descriptor(runner: SubagentRunner) -> ToolDescriptor:
        ...

`agent` 输入 schema 必须是对象，关闭额外字段：

    {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "subagent_type": {"type": "string"},
        },
        "required": ["prompt"],
        "additionalProperties": False,
    }

`agent` 工具 prompt 必须告诉模型：

省略 `subagent_type` 会 fork 当前上下文；显式 `general-purpose` 用于干净上下文的复杂研究；显式 `Explore` 用于只读搜索；显式 `Plan` 用于只读设计计划；不要期待 background 返回，因为第一版没有 `run_in_background`。

在 `core/loop.py` 中新增可选参数：

    current_model_context: CurrentModelContext | None = None

每轮 `snapshot = await self.context_engine.build_for_model(self.state)` 后执行：

    if self.current_model_context is not None:
        self.current_model_context.snapshot = snapshot

在 `services/context/message_store.py` 中新增：

    def seed_messages(self, messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        ...

它必须在已有消息为空时使用，并通过 `_append()` 写 transcript。

在 `core/loop.py` 中新增：

    async def continue_stream(self) -> AsyncIterator[AgentEvent]:
        ...

它不追加 user message，仅继续当前 message store 中已有上下文。

在 permission 层新增 read-only hard limit。推荐在 `PermissionPolicy.evaluate()` 早期加入：

    if state.metadata.get("read_only_agent") is True:
        if not classification.read_only or classification.modifies_filesystem:
            return PermissionDecision(action="deny", ...)

这个判断必须 deny-first，不能被 session allow、用户确认或 hook 覆盖。

## Change Note

2026-06-05 / Codex: 创建本 ExecPlan。计划吸收 `docs/references/s06_subagent/AgentTool` 的 agent 定义、普通 agent、Explore、Plan、fork message 构造、cache-safe system prompt 和 subagent context 隔离思想，并按用户确认的范围收窄为：省略类型始终 fork、只做四个内置 agent、fork system prompt 字节级继承、child 隐藏 `agent`、共享临时授权并 bubble 权限、不做 background、不做 worktree、Explore/Plan 使用硬性只读限制。

2026-06-06 / Codex: 完成第一版实现并更新架构和技术债。实现过程中保留了“subagent 只是普通工具”的边界，没有在 `core/loop.py` 添加工具名分支；为 CLI resume 增加 `SubagentRunner.bind_parent_message_store()`，为 agent 工具来源追踪增加 `ToolRuntime.tool_call_id`。
