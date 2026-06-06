# Tech Debt Tracker

最近审阅日期：2026-06-06

本台账记录当前已实现的 OneCode runtime 骨架中可由代码证据支持的技术债。条目依据 `architecture.md`、`docs/design-docs/core-beliefs.md`、`docs/exec-plans/active/` 和当前代码边界整理。

## 活跃技术债

| 债务 ID | 标题 | 类型 | 区域 | 优先级 | 状态 |
|:---|:---|:---|:---|:---|:---|
| TD-004 | 恢复类 transition 已定义，但 provider 和工具错误仍会绕过 loop 恢复流程 | 架构 / 测试 | `core/loop.py`, `core/transitions.py`, `core/runtime_state.py` | 中 | 已识别 |
| TD-007 | CLI 主界面已落地 streaming，但缺少恢复 UI 和实时 trace 订阅 | UI / 架构 | `ui/cli/`, `services/observability/`, `core/loop.py` | 中 | 部分缓解 |
| TD-008 | 动态 prompt 已落地，但可见工具裁剪尚未接入完整 permission policy | 架构 / 安全 | `prompts/`, `services/tools/registry.py`, `services/guard/`, `services/permissions/` | 中 | 部分缓解 |
| TD-009 | BashTool 第一版只支持 Git Bash 和有限 Bash AST 子集 | 架构 / 安全 / 测试 | `tools/bash/`, `services/permissions/policy.py`, `ui/cli/permissions.py` | 中 | 已识别 |
| TD-010 | Subagent 第一版缺少 background、worktree 和自定义 agent 加载 | 架构 / 测试 | `services/subagents/`, `tools/agent/`, `ui/cli/app.py` | 中 | 已识别 |
| TD-014 | Full compact 通过可用工具的 fork subagent 摘要上下文，只靠 prompt 禁止工具调用 | 架构 / 安全 | `services/compaction/service.py`, `services/subagents/definitions.py`, `services/subagents/runner.py` | 高 | 已识别 |
| TD-016 | 附件系统已有 backend 投影，但 CLI/UI 缺少附件可视化渲染 | UI / 可观测性 | `ui/cli/renderer.py`, `services/attachments/types.py`, `ui/cli/app.py` | 低 | 已识别 |

---

### TD-004: 恢复类 transition 已定义，但 provider 和工具错误仍会绕过 loop 恢复流程

- **类型：** 架构 / 测试
- **区域：** `core/loop.py`, `core/transitions.py`, `core/runtime_state.py`
- **优先级：** 中
- **状态：** 已识别
- **影响：** 可重试 provider failure、context limit failure、max-output interruption 和工具执行错误目前可能直接逃出 loop，而不是变成可观测的 transition 并进入受控恢复流程。

**描述：**
`TransitionReason` 已包含恢复状态，`RuntimeState` 也包含 reactive compact 和 max-output recovery 计数器。`AgentLoop.stream()` 会递增 turn 并消费 `model_client.stream(snapshot)`，但没有捕获 provider-neutral error、分类 retryability、调用 compaction，或把模型 streaming error 映射为受控恢复 transition。

**引入原因：**
第一版 loop 实现聚焦 happy path：模型调用、工具调用、工具结果回填和最终停止。Transition enum 先于恢复行为被加入。

**修复方向：**
在 loop 或独立 recovery service 中加入显式错误处理，将 `ProviderError` 和 output interruption 状态映射为 transition。补充 rate-limit retry、network retry exhaustion、context-limit reactive compaction、max-output recovery 和 tool error result continuation 的测试。

**关联代码：**
- `core/loop.py:L41` - model call 没有恢复处理。
- `core/transitions.py:L10` - 已存在恢复类 transition 名称。
- `core/runtime_state.py:L17` - 已存在恢复状态字段，但 loop 未消费。
- `infrastructure/providers/http.py:L112` - provider HTTP error 已暴露 retryable metadata。

**架构约束：**
错误应成为 runtime state transition 和可观测事件，而不是 uncaught exception 或 core 中的 provider-specific 分支。

---

### TD-007: CLI 主界面已落地 streaming，但缺少恢复 UI 和实时 trace 订阅

- **类型：** UI / 架构
- **区域：** `ui/cli/`, `services/observability/`, `core/loop.py`
- **优先级：** 中
- **状态：** 部分缓解
- **影响：** 第一版 CLI 能启动真实 runtime、执行普通 prompt、增量渲染 assistant delta、展示工具/状态/历史/trace、恢复 JSONL transcript 并清空会话；现在也能在工具 ask 时显示 async 权限面板并做 session 级临时授权。运行中已有本地 JSONL trace 事实来源，但仍缺少 provider recovery UI、debug log、实时 trace 订阅和 future compact 状态展示。

**描述：**
`services/observability/` 已提供 `TraceRecorder`、JSONL sink、noop sink 和 metadata sanitizer；CLI 会写 `.onecode/<session_id>/trace.jsonl`，`/trace [n]` 能展示最近 trace 摘要，`/status` 能显示 trace 文件路径。`core/loop.py` 已记录 interaction、context prepare、model call 和 transition，并通过 `AgentLoop.stream(prompt)` 向 CLI 输出 assistant delta 和工具事件；`RegistryToolExecutor` 已记录 tool batch、preflight、permission wait、tool execution 和 tool result；`HookRegistry` 已记录 hook span。剩余问题是 provider recovery UI、debug log、实时 UI trace 订阅和 compact 状态展示尚未落地。

**引入原因：**
CLI 第一版刻意保持轻量标准库实现，优先完成可运行主界面、固定工具装配、slash commands、JSONL 恢复、本地 trace 和 streaming 渲染；恢复 UI 和实时订阅需要后续 transition recovery、compaction 和 UI 订阅能力继续演进。

**修复方向：**
在现有 `TraceRecorder` 基础上继续扩展实时订阅或 UI sink，让 CLI 渲染 provider recovery、compact 和长期任务状态。后续如需 debug log，应与 trace 和 transcript 保持分离，并继续走 metadata 清洗和显式启用策略。

**关联代码：**
- `services/observability/trace.py:L1` - 第一版本地 trace recorder 和 span helper。
- `services/observability/sinks.py:L1` - 本地 JSONL trace sink 写入 `.onecode/<session_id>/trace.jsonl`。
- `core/loop.py:L13` - loop 已发布 interaction、context/model call 和 transition trace。
- `services/tools/executor.py:L73` - executor 已发布工具批次、权限等待、执行和结果 trace。
- `services/hooks/registry.py:L26` - hook registry 已发布 hook trace。
- `ui/cli/commands.py:L34` - CLI 已提供 `/trace [n]` 查看最近 trace 摘要。

**架构约束：**
CLI 不应直接实现 runtime recovery、权限策略或 provider-specific 分支；应消费 core/services 发布的 provider-neutral 状态和事件。

---

### TD-008: 动态 prompt 已落地，但可见工具裁剪尚未接入完整 permission policy

- **类型：** 架构 / 安全
- **区域：** `prompts/`, `services/tools/registry.py`, `services/guard/`, `services/permissions/`
- **优先级：** 中
- **状态：** 部分缓解
- **影响：** 第一版 `ToolRegistry.visible_descriptors(state)` 已让 tool schema 和 tool prompt section 使用同一个可见工具视图，并已接入 `PermissionPolicy` 的工具级 deny/disabled。真实项目权限、用户规则、组织策略、多来源规则合并和路径级 guard policy 仍不能在模型调用前完整裁剪工具能力。

**描述：**
`DynamicPromptAssembler` 会从 `ToolRegistry.visible_descriptors(state)` 读取工具 prompt，`tool_schemas(state)` 也基于同一视图生成 provider-visible schema。当前可见性接口用于保持 prompt/schema 一致，并会消费注入的 `PermissionPolicy` 来裁剪工具级 deny/disabled。它还没有把路径级 guard、项目配置、用户配置、组织策略或持久规则合并成完整工具可见性判断。执行入口仍依赖 `RegistryToolExecutor` 对具体 `ToolTarget` 重复执行 guard 和 permission policy 检查。

**引入原因：**
动态 prompt 架构需要先有统一可见工具视图，才能避免 schema 和 prompt 看到不同工具集合。第一版 permission policy 已接入工具级裁剪和 session 临时授权，但完整规则来源、优先级、持久化、审计和路径级预裁剪仍需要后续设计。

**修复方向：**
继续扩展 provider-neutral permission policy service，加入用户、项目、本地、组织、CLI flag 和持久 session 规则来源。保持 deny-first 顺序：任意有效 deny 都应同时裁剪 schema、prompt 和执行入口。路径参数级判断仍应在工具执行前基于实际输入重复校验，不应由 prompt 组装阶段猜测。

**关联代码：**
- `services/tools/registry.py:L31` - `visible_descriptors(state)` 是 prompt/schema 统一视图入口，并已接入工具级 permission policy。
- `prompts/assembler.py:L33` - assembler 从 registry 读取当前可见工具。
- `services/tools/executor.py:L96` - executor 仍在执行入口检查具体工具调用。
- `services/permissions/policy.py:L1` - 第一版 permission policy 已落地，但只覆盖内存 session 和固定规则。
- `services/guard/policy.py:L19` - guard policy 已能对具体路径返回 allow/ask/deny。

**架构约束：**
Prompt 裁剪只能减少模型看到不可用能力的机会，不能替代执行入口的确定性权限检查。Hook、会话 allow 和用户确认都不能覆盖 deny。

---

### TD-009: BashTool 第一版只支持 Git Bash 和有限 Bash AST 子集

- **类型：** 架构 / 安全 / 测试
- **区域：** `tools/bash/`, `services/permissions/policy.py`, `ui/cli/permissions.py`
- **优先级：** 中
- **状态：** 已识别
- **影响：** BashTool 已能基于 Tree-sitter AST 做 fail-closed 分类、派生文件系统 target 并接入权限确认，但第一版只能运行 Git Bash，不能覆盖 PowerShell、cmd、WSL、后台任务、持久命令授权、完整 Bash 语言或通用 result store。

**描述：**
`tools/bash/` 当前只支持 Git Bash runner，并只自动理解 simple command、顶层 `&&` / `||` / `;` / `|`、静态 argv 和常见 redirect。复杂结构、runtime expansion、subshell、heredoc、command substitution、loop/function/condition 等都会进入非只读 permission ask 或 fail-closed 路径。权限层已让非只读 `command/execute` target 触发 ask，但没有持久 Bash prefix allow rule，也没有 background task lifecycle 或 shell profile 管理。

**引入原因：**
BashTool 第一版优先交付可解释、安全保守的 Git Bash 命令执行能力，避免在没有完整 shell 语言模型、跨 shell runner 和可观测性服务前扩大执行面。

**修复方向：**
后续按独立 ExecPlan 扩展 runner 和权限能力：增加 PowerShell/cmd/WSL 适配时保持 provider-neutral descriptor；引入持久 Bash prefix allow 前先设计审计和撤销；在 result store/compaction 落地后把大 stdout/stderr 外置；如需支持更多 Bash 结构，继续基于 Tree-sitter AST allowlist 扩展，不退回正则安全判断。

**关联代码：**
- `tools/bash/parser.py:L1` - Tree-sitter AST allowlist 和 fail-closed parser。
- `tools/bash/semantics.py:L1` - argv 语义检查、wrapper stripping 和退出码解释。
- `tools/bash/runner.py:L1` - Git Bash-only runner。
- `services/permissions/policy.py:L1` - 非只读 `command/execute` target 触发 ask。
- `ui/cli/permissions.py:L1` - 第一版 Bash 权限面板。

**架构约束：**
BashTool 扩展必须继续通过 descriptor、ToolRegistry、guard 和 PermissionPolicy 接入；不得在 `core/loop.py` 中添加 shell 特例。安全边界应保持 deny-first，用户确认不能覆盖 guard deny。

---

### TD-010: Subagent 第一版缺少 background、worktree 和自定义 agent 加载

- **类型：** 架构 / 测试
- **区域：** `services/subagents/`, `tools/agent/`, `ui/cli/app.py`
- **优先级：** 中
- **状态：** 已识别
- **影响：** 主 agent 已能通过 `agent` 工具同步运行四个内置 subagent，并支持 fork prompt 继承、递归隐藏、只读硬限制和权限 bubble；但还不能在后台运行子任务，不能为 child 创建隔离 worktree，也不能从用户、项目或插件目录加载自定义 agent 定义。

**描述：**
当前 subagent 机制只实现内置 `general-purpose`、`Explore`、`Plan` 和隐藏 `fork`。`SubagentRunner` 使用父 model client、父 permission policy/prompter、同一个 sandbox guard 和固定 base descriptors 创建 child runtime。省略 `subagent_type` 会 fork 父消息链并复用父轮次已渲染 system prompt 字符串；显式类型使用干净 child 消息链。第一版没有 `run_in_background` 输入字段，没有独立 worktree 策略，没有用户自定义 agent loader，也没有完整 prompt-cache identical fork 参数校验。

**引入原因：**
第一版刻意收窄范围，先验证 subagent 作为普通工具接入、child 上下文隔离、fork prompt 字节继承、只读权限硬限制和 CLI 权限 bubble。background、worktree 和自定义 agent 都需要额外生命周期、配置优先级、审计和恢复设计。

**修复方向：**
后续按独立 ExecPlan 设计 background task lifecycle、child worktree 创建/清理、agent definition loader 和权限规则来源。若要追求 provider prompt-cache 命中，还需要记录并比较 system prompt、tool schema、model、message prefix 和其他 provider 参数，而不只是复用父 system prompt 字符串。

**关联代码：**
- `services/subagents/definitions.py:L1` - 只定义四个内置 agent。
- `services/subagents/runner.py:L1` - child runtime 同步 drain `AgentLoop.continue_stream()`，没有 background lifecycle 或 worktree 隔离。
- `tools/agent/tool.py:L1` - 输入 schema 只有 `prompt` 和 `subagent_type`，没有 `run_in_background`。
- `services/permissions/policy.py:L1` - read-only subagent 通过 permission deny-first 硬限制写入或未知副作用工具调用。

**架构约束：**
后续扩展仍应保持 subagent 通过 `agent` tool descriptor 和 `SubagentRunner` 接入，不应在 `core/loop.py` 中添加 subagent 工具名分支。任何 worktree 或自定义 agent 能力必须继续经过 guard、permission policy 和 registry 可见性裁剪。

---

### TD-014: Full compact 通过可用工具的 fork subagent 摘要上下文，只靠 prompt 禁止工具调用

- **类型：** 架构 / 安全
- **区域：** `services/compaction/service.py`, `services/subagents/definitions.py`, `services/subagents/runner.py`
- **优先级：** 高
- **状态：** 已识别
- **影响：** Manual、auto full 或 reactive compact 需要的是纯摘要任务，但当前通过 `subagent_type=None` 触发 fork subagent。fork definition 只禁用 `agent`，默认仍可见 read/edit/bash 等 base tools；“Do not call tools” 只是 prompt 文本，不能作为安全边界。内部 compact 可能触发工具调用、权限弹窗，甚至在用户允许后修改 workspace。

**描述：**
`ContextCompactionService._full_compact()` 通过 `SubagentRunner.run(SubagentRequest(..., subagent_type=None))` 创建 fork child，并在 compact prompt 中写入 “Do not call tools”。`services/subagents/definitions.py` 中的 `fork` agent 只设置 `disallowed_tools=("agent",)`，`AgentDefinition.tools` 默认是 `("*",)` 且 `read_only=False`。`SubagentRunner` 只根据 definition 裁剪工具，没有识别 compaction 请求中的 `metadata={"query_source": "compact"}` 来隐藏工具或强制只读。

**引入原因：**
Full compact 为了复用 fork 机制和父 prompt 字节继承，直接把 compact summary 作为 fork child 的 prompt；第一版先依赖 prompt 约束模型行为，尚未给内部 compaction child 建立专用 tool visibility 或 no-tools runner。

**修复方向：**
为 compaction 建立专用摘要 runner 或 subagent definition：工具 schema 应为空，或至少设置 read-only 并禁止所有状态改变工具。`SubagentRequest.metadata`/mode 应进入 child runtime state 或 runner policy，用于区分普通 fork 和 internal compact。补充测试验证 full compact child snapshot 的 tool schemas 为空、不会触发 permission prompter、不会执行工具。

**关联代码：**
- `services/compaction/service.py:L332` - full compact 使用 `subagent_type=None` 触发 fork。
- `services/compaction/service.py:L335` - compact 请求 metadata 标记 `query_source=compact`，但 runner 没有消费。
- `services/compaction/service.py:L687` - prompt 文本要求 “Do not call tools”。
- `services/subagents/definitions.py:L53` - synthetic `fork` agent 定义。
- `services/subagents/definitions.py:L56` - `fork` 只禁用 `agent` 工具。
- `services/subagents/types.py:L20` - `AgentDefinition.tools` 默认允许 `("*",)`。
- `services/subagents/types.py:L23` - `read_only` 默认是 `False`。
- `services/subagents/runner.py:L70` - child 只隐藏 `agent`。
- `services/subagents/runner.py:L72` - 只有 definition read-only 时才设置只读限制。

**架构约束：**
内部 runtime 任务的能力裁剪必须由 registry/permission 边界强制，不能依赖 prompt。Compaction 仍应由 context service 编排，不应在 `core/loop.py` 中增加 compact 或 subagent 特例。

---

### TD-016: 附件系统已有 backend 投影，但 CLI/UI 缺少附件可视化渲染

- **类型：** UI / 可观测性
- **区域：** `ui/cli/renderer.py`, `services/attachments/types.py`, `ui/cli/app.py`
- **优先级：** 低
- **状态：** 已识别
- **影响：** 用户输入 `@file` 后，runtime 会收集、持久化并在模型上下文中投影附件，但 CLI 当前只显示普通 running/assistant/tool result 输出，不展示附件卡片、解析状态、目录列表摘要或 edited-file diff 提醒。用户无法从 UI 直接确认哪些附件进入了本 turn。

**描述：**
`AttachmentCollector` 已在 CLI 调用 loop 前收集 attachment messages，`MessageStore` 会持久化 `role="attachment"`，`AttachmentContextPreparer` 会在 provider 调用前投影为合法 messages。`ui/cli/renderer.py` 尚未提供 attachment-specific 渲染函数，`main_loop_async()` 也没有在模型调用前输出收集到的附件摘要。

**引入原因：**
附件系统第一版优先交付 backend 行为和 provider-safe 投影。计划范围明确暂缓 UI 渲染，以避免在结构化 metadata 尚未稳定前固化终端展示样式。

**修复方向：**
为 CLI 增加简洁的附件摘要渲染：文件路径与行范围、目录条目数量、解析失败原因、edited text file diff 状态。渲染应消费 `services/attachments/types.py` 的稳定字段，不重新解析 prompt 或读取文件。

**关联代码：**
- `services/attachments/types.py:L1` - attachment message 的 durable internal shape。
- `services/attachments/collector.py:L1` - CLI 当前收集的 attachment payload 来源。
- `ui/cli/app.py:L192` - CLI 在调用 loop 前收集附件，但不渲染。
- `ui/cli/renderer.py:L1` - 缺少 attachment rendering 入口。

**架构约束：**
UI 渲染不能成为附件投影或安全判断的事实来源；guard、permission 和 provider-safe projection 仍应留在 services/context 边界。

---

## 已解决条目归档

### TD-001: 工具结果消息 provider 投影

- **解决方式：** `MessageStore` 改为存储内部 `tool_result` message，Chat Completions adapter 在发送前投影为合法 `role="tool"` payload，并补充 loop/provider 测试。

### TD-002: 文件工具强制使用 sandbox guard

- **解决方式：** 新增受 `SandboxGuard` 保护的 `read_file` / `edit_file`，通过 `RegistryToolExecutor` 在执行前检查 guard，deny/ask 返回结构化 tool error。

### TD-003: Registry-backed 工具 runtime

- **解决方式：** 新增 `ToolDescriptor`、`ToolExecutionResult`、`ToolRuntime`、`ToolRegistry`、schema projection 和 concrete executor，`ContextEngine` 可从 registry 获取工具 schema。

### TD-005: 上下文治理已有基础 transcript，但缺少 compaction、projector 和通用 result store

- **解决方式：** 已落地 `services/context/projector.py`、`services/compaction/service.py`、session memory、`ToolResultStore` 和 compaction-aware `ContextEngine` preparer。`PreparedContext` 现在可把 compaction `usage_hints` 与 `transcript_refs` 写入 `ContextSnapshot`，并补充大工具结果替换、stored result refs 和 ContextEngine metadata 投影测试。剩余 compact safety 问题由 TD-014 跟踪；原 TD-015 已因设计取舍废弃。

### TD-006: 工具 metadata 已驱动结果预算和并发调度，但只读策略与 durable result store 仍未完整落地

- **解决方式：** `RegistryToolExecutor` 已消费 `ToolResultPolicy`，在注入 `ToolResultStore` 后将超预算结果写入 durable store；`PermissionPolicy` 已消费 `read_only` 强制 read-only subagent 和非只读命令 ask；`grep` 的 result policy 已改为超过 20KB 时允许持久化，并补充 result store、search tool policy 和 executor 预算测试。

### TD-011: `core.loop` 与 subagent 当前上下文形成反向依赖

- **解决方式：** 将 `CurrentModelContext` 移到 `services/context/current_model_context.py`，`core.loop` 只依赖通用 context service，不再 import `services.subagents.*`；`services/subagents/__init__.py` 不再导出 `SubagentRunner`，需要 runner 的装配点改为直接 import `services.subagents.runner`。补充 import-boundary 测试防止 core 重新依赖 subagent 具体模块。

### TD-012: `/clear` 只切换消息链，未重绑定 session-scoped compaction 服务

- **解决方式：** `/clear` 改为复用 `CliRuntime.with_session()` 并返回新的 `CliRuntime`，统一重绑定 message store、trace recorder、executor result store、compaction service、session memory updater、subagent parent store 和 current model context。补充 CLI 测试覆盖新 session 资源重绑。

### TD-013: Compaction 投影持久化大结果非幂等，会重复写入 result store

- **解决方式：** `ToolResultStore.persist_tool_result()` 改为幂等：同一 `tool_call_id` 且内容相同复用原文件，内容不同使用稳定内容 hash 后缀。补充 result store 和 compaction 连续两次 `prepare_for_model()` 的 refs 稳定性测试。

### TD-015: Session memory 缺少真实 transcript anchor 和文件变更记录

- **废弃原因：** 后续 Session Memory 设计不再依赖真实 transcript UUID 或 `last_summarized_message_uuid` 作为压缩边界。恢复 transcript 后会基于重建出来的当前消息链重新估算 token 增长和工具调用增长，达到阈值时固定触发一次后台 Session Memory 提取。因此不需要补 `MessageStore` message metadata anchor。
- **废弃原因：** 后续 Session Memory 也不要求 executor 维护 `files_changed`，不要求 memory 文件包含 Files Changed 章节。Session Memory 由受限 fork agent 基于当前聊天记录和已有 memory 更新当前会话笔记；文件变更记录不是本设计的必要事实来源。
- **保留说明：** 当前代码里可能仍存在规则版 updater 读取 `files_changed` 或写出 Files Changed 章节的实现细节，但这不再构成技术债修复目标。后续重写 Session Memory 提取时可以删除该章节或改为由 fork agent 自行维护普通文本摘要，不应为旧 TD-015 新增 executor 文件变更 side effect。
