# Tech Debt Tracker

最近审阅日期：2026-06-08

本台账记录当前已实现的 OneCode runtime 骨架中可由代码证据支持的技术债。条目依据 `architecture.md`、`docs/design-docs/core-beliefs.md`、`docs/exec-plans/active/` 和当前代码边界整理。

## 活跃技术债

| 债务 ID | 标题 | 类型 | 区域 | 优先级 | 状态 |
|:---|:---|:---|:---|:---|:---|
| TD-007 | CLI 主界面已落地 streaming，但缺少恢复 UI 和实时 trace 订阅 | UI / 架构 | `ui/cli/`, `services/observability/`, `core/loop.py` | 中 | 部分缓解 |
| TD-008 | 动态 prompt 已落地，但可见工具裁剪尚未接入完整多来源 permission policy | 架构 / 安全 | `prompts/`, `services/tools/registry.py`, `services/guard/`, `services/permissions/` | 中 | 部分缓解 |
| TD-009 | BashTool 第一版只支持 Git Bash 和有限 Bash AST 子集 | 架构 / 安全 / 测试 | `tools/bash/`, `services/permissions/policy.py`, `ui/cli/permissions.py` | 中 | 已识别 |
| TD-016 | 附件系统已有 backend 投影，但 CLI/UI 缺少附件可视化渲染 | UI / 可观测性 | `ui/cli/renderer.py`, `services/attachments/types.py`, `ui/cli/app.py` | 低 | 已识别 |
| TD-018 | Skill `allowed_tools` 会转成共享 session 级工具授权 | 安全 / 权限 / 架构 | `tools/skill/`, `services/subagents/runner.py`, `services/tools/executor.py`, `services/permissions/` | 高 | 已识别 |
| TD-020 | 文件、附件和搜索工具会先整文件读取或整目录扫描，再分页/截断 | 性能 / 可用性 | `tools/read_file/`, `tools/grep/`, `tools/glob/`, `services/attachments/` | 中 | 已识别 |

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

### TD-008: 动态 prompt 已落地，但可见工具裁剪尚未接入完整多来源 permission policy

- **类型：** 架构 / 安全
- **区域：** `prompts/`, `services/tools/registry.py`, `services/guard/`, `services/permissions/`
- **优先级：** 中
- **状态：** 部分缓解
- **影响：** 第一版 `ToolRegistry.visible_descriptors(state)` 已让 tool schema 和 tool prompt section 使用同一个可见工具视图，并已接入 `PermissionPolicy` 的工具级 deny/disabled 和项目级整工具 deny。项目级 `.onecode/settings.json` 已支持 `permissions.allow`、`permissions.deny`、`permissions.ask`，并能在执行入口匹配内容规则。用户规则、组织策略、多来源规则合并和路径级 guard policy 仍不能在模型调用前完整裁剪工具能力。

**描述：**
`DynamicPromptAssembler` 会从 `ToolRegistry.visible_descriptors(state)` 读取工具 prompt，`tool_schemas(state)` 也基于同一视图生成 provider-visible schema。当前可见性接口用于保持 prompt/schema 一致，并会消费注入的 `PermissionPolicy` 来裁剪工具级 deny/disabled 和项目级整工具 deny。`services/permissions/project_settings.py` 已从 `.onecode/settings.json` 加载项目级规则；`services/permissions/policy.py` 已在执行入口处理项目级 ask、allow 和内容 deny。它还没有把路径级 guard、用户配置、组织策略或更多规则来源合并成完整工具可见性判断。执行入口仍依赖 `RegistryToolExecutor` 对具体 `ToolTarget` 重复执行 guard 和 permission policy 检查。

**引入原因：**
动态 prompt 架构需要先有统一可见工具视图，才能避免 schema 和 prompt 看到不同工具集合。第一版 permission policy 已接入工具级裁剪、session 临时授权和项目级持久规则，但完整规则来源、优先级、审计和路径级预裁剪仍需要后续设计。

**修复方向：**
继续扩展 provider-neutral permission policy service，加入用户、本地、组织、CLI flag 和持久 session 规则来源。保持 deny-first 顺序：任意整工具 deny 都应同时裁剪 schema、prompt 和执行入口；内容 deny 必须在执行入口基于实际输入重复校验。路径参数级判断仍应在工具执行前基于实际输入重复校验，不应由 prompt 组装阶段猜测。

**关联代码：**
- `services/tools/registry.py:L31` - `visible_descriptors(state)` 是 prompt/schema 统一视图入口，并已接入工具级 permission policy。
- `prompts/assembler.py:L33` - assembler 从 registry 读取当前可见工具。
- `services/tools/executor.py:L96` - executor 仍在执行入口检查具体工具调用。
- `services/permissions/policy.py:L1` - permission policy 已落地项目级规则、内存 session 和固定规则。
- `services/permissions/project_settings.py:L1` - 项目级 `.onecode/settings.json` 权限规则 store。
- `services/permissions/rules.py:L1` - 持久权限规则 parser 和 serializer。
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

### TD-018: Skill `allowed_tools` 会转成共享 session 级工具授权

- **类型：** 安全 / 权限 / 架构
- **区域：** `tools/skill/`, `services/subagents/runner.py`, `services/tools/executor.py`, `services/permissions/`
- **优先级：** 高
- **状态：** 已识别
- **影响：** skill 执行或 skill 子代理声明的 `allowed_tools` 会写入 session permission grants，后续同一 runtime session 中的其他上下文也可能复用该授权，导致原本应该局限在 skill 范围内的能力扩大。

**描述：**
`SubagentRunner` 会遍历 `skill.allowed_tools` 并调用 session grant；`RegistryToolExecutor` 也会从 skill 工具结果 metadata 读取 `allowed_tools` 并写入 session grant。`PermissionPolicy` 之后会把 session tool grant 作为 allow 来源之一。当前测试 `tests/test_skill_permissions.py::test_allowed_tools_session_grant_turns_command_ask_into_allow` 已把这种行为固化为预期，但从权限边界看，skill 的工具授权更像 scoped execution capability，不应默认提升为整个会话共享的长期授权。

**引入原因：**
Skill 第一版为了让 skill 能声明它运行所需的工具能力，复用了已有 session permission grant 机制；该机制原本更适合用户确认后的临时授权，而不是 skill-local capability。

**修复方向：**
将 skill `allowed_tools` 改为 scoped grant：只在当前 skill 或当前 subagent run 的 tool registry / permission policy context 内生效。执行结束后自动撤销，或通过 child runtime 独立 session 隔离。现有 session grant 仍可保留给用户显式确认，但 skill metadata 不应直接写入共享 session allow。

**关联代码：**
- `services/subagents/runner.py:L160` - skill 子代理把 `skill.allowed_tools` 写入 session grant。
- `services/tools/executor.py:L957` - skill 工具结果 metadata 中的 `allowed_tools` 写入 session grant。
- `services/permissions/session.py:L15` - session 内保存共享 `_allowed_tools`。
- `services/permissions/policy.py:L177` - session tool grant 会把 permission decision 转成 allow。
- `tests/test_skill_permissions.py:L61` - 当前测试把该行为固定为预期。

**架构约束：**
权限能力必须遵循 deny-first；skill-local allow 不能覆盖 guard deny、项目 deny 或 tool disallowed 列表。修复时应保持 tool registry 可见性裁剪、执行入口权限检查和 subagent 工具隔离一致。

---

### TD-020: 文件、附件和搜索工具会先整文件读取或整目录扫描，再分页/截断

- **类型：** 性能 / 可用性
- **区域：** `tools/read_file/`, `tools/grep/`, `tools/glob/`, `services/attachments/`
- **优先级：** 中
- **状态：** 已识别
- **影响：** 大文件、大目录或超大仓库会导致不必要的内存占用、长时间扫描和 UI 等待；分页、limit 或 token budget 主要发生在读取/扫描之后，不能阻止最重的 IO 和遍历成本。

**描述：**
`read_file` 和 attachment collector 使用 `Path.read_text()` 一次性读取文件，再做行范围或内容截断。附件目录摘要会先 `iterdir()` 并排序全部子项。`glob` 使用 `root.rglob("*")` 全量遍历后再匹配、过滤和限制结果。`grep` 已使用 ripgrep，但当前实现会等待进程完成并收集完整 stdout/stderr，再解析、排序和截断；在匹配极多或输出很大时仍会积累较高成本。attachment resolver 的路径搜索也会遍历 workspace。

**引入原因：**
第一版文件和附件能力优先实现简单、确定、易测试的行为；在没有统一 streaming reader、bounded traversal 和 search budget 之前，采用了标准库整读和整目录遍历。

**修复方向：**
引入 bounded IO 策略：文件读取按行或按字节 streaming，并在达到请求范围/预算后停止；目录和 glob 使用 bounded traversal、早停和最大访问节点数；grep 增加 ripgrep 的输出上限、超时和逐行解析，避免完整 stdout 聚合。附件 resolver 应避免对 workspace 做无界 `rglob("*")`，改为索引、候选路径启发式或明确 limit。

**关联代码：**
- `tools/read_file/tool.py:L120` - `read_file` 一次性 `read_text()`。
- `services/attachments/collector.py:L98` - attachment file collector 一次性 `read_text()`。
- `services/attachments/collector.py:L132` - attachment directory collector 读取并排序全部子项名称。
- `tools/glob/tool.py:L151` - glob 对 root 执行 `rglob("*")` 全量遍历。
- `tools/grep/tool.py:L204` - grep 构建 ripgrep 搜索并在后续流程中聚合输出。
- `services/attachments/resolver.py:L64` - attachment 路径 resolver 遍历 workspace。

**架构约束：**
性能优化不能绕过 sandbox guard 和 permission policy；bounded traversal 仍必须对每个最终访问路径保持 guard 约束。工具结果截断和持久化应继续通过 tool metadata/result policy 进入统一结果预算。

---

## 已解决条目归档

### TD-017: Project MCP stdio server 会在 CLI 启动时自动运行并继承完整环境变量

- **解决方式：** 新增 `services/mcp/trust.py`，通过 `.onecode/settings.json` 保存 stdio MCP server 的本地 trust fingerprint；`McpConnectionManager` 在 `connect_all()` 和 `ensure_connected()` 中对未信任 stdio server fail closed，状态标记为 `untrusted`，不发现工具、不注入 instructions。stdio 子进程环境改为基础 allowlist 父环境加 `.mcp.json` 显式 `env`，不再继承完整 `os.environ`。CLI 启动时展示 command、args、cwd、显式 env keys 和基础 env keys，并由用户选择 trust 或 skip。
- **验证：** 补充 MCP trust、manager env/untrusted、CLI 渲染测试；通过 `uv run python -m pytest tests/test_mcp_config.py tests/test_mcp_trust.py tests/test_mcp_manager.py tests/test_mcp_tool_factory.py tests/test_cli_commands.py -q`。

### TD-021: 工具结果持久化逻辑分散在 transcript 和 compaction result store

- **解决方式：** 新增 `utils/toolResultStorage` 作为唯一共享工具结果存储模块，提供 `ToolResultStorage`、`StoredToolResultRef`、幂等持久化、内容 hash 冲突分流、读取、transcript 外置文本、模型引用文本和统一 metadata helper。`services/compaction/result_store.py` 已删除；`JsonlTranscriptStore`、`ContextCompactionService`、`RegistryToolExecutor` 和 CLI session 装配均改为直接使用共享 storage。
- **验证：** 补充 transcript 重复 `tool_call_id` 回归测试，覆盖同 ID 不同内容不覆盖、同 ID 相同内容复用；聚焦测试通过。

### TD-019: Transcript 外置大工具结果按 `tool_call_id` 命名，重复 ID 会覆盖内容

- **解决方式：** transcript 外置改为调用 `ToolResultStorage.persist_tool_result()`。同一 `tool_call_id` 且内容相同复用 `tool-results/<id>.txt`；同一 ID 但内容不同生成稳定内容 hash 后缀，恢复时按每条 message metadata 中的 `tool_result_path` 读取对应完整结果。缺失文件仍保留 `missing_external_tool_result` 标记。
- **验证：** 新增 `tests/test_jsonl_session_persistence.py::test_duplicate_tool_call_id_externalized_results_do_not_overwrite` 和 `test_duplicate_tool_call_id_same_content_reuses_externalized_result`。

### TD-014: Full compact 通过可用工具的 fork subagent 摘要上下文，只靠 prompt 禁止工具调用

- **解决方式：** `SubagentRunner` 现在通过 `_is_compact_request()` 识别 compact 请求（`metadata["query_source"] == "compact"`）。命中时 `_child_descriptors(..., compact=True)` 返回空工具集，使 compact child 的 `tool_schemas(state)` 为空；同时设置 `child_state.metadata["read_only_agent"]=True` 作为执行入口 deny-first 兜底，并把 permission prompter 关闭。能力裁剪由空 registry 和 permission 边界强制，不再依赖 prompt 文本。补充 `tests/test_subagent_runner.py::test_compact_fork_child_has_no_tools_and_never_prompts` 验证 compact child snapshot tool schemas 为空、不会触发 permission prompter、即便模型尝试调用工具也被拦截（unknown_tool），并通过 subagent、compaction、import-boundary 相关测试。

### TD-010: Subagent 第一版缺少 background、worktree 和自定义 agent 加载

- **废弃原因：** background 能力已落地（`tools/agent/tool.py` 的 `run_in_background` 输入字段、`_start_background_agent()` 接入 `BackgroundTaskManager`，runner 通过 `_is_background_agent_request()` 在后台运行时关闭 permission prompter）。
- **废弃原因：** worktree 隔离和自定义 agent（用户/项目/插件目录加载）经评估不在项目目标范围内，不再作为技术债修复目标。`get_agent_definition()` 继续只解析内置 `BUILT_IN_AGENTS` 即满足当前需求。
- **保留说明：** 若未来重新需要 worktree 隔离或自定义 agent loader，应另开 ExecPlan，并继续保持经过 guard、permission policy 和 registry 可见性裁剪的接入方式。

### TD-004: 恢复类 transition 已定义，但 provider 和工具错误仍会绕过 loop 恢复流程

- **解决方式：** 新增 `services/errors.py`、`services/observability/error_log.py` 和 `services/model/retry.py`，让 provider retry、retry exhaustion、context-limit reactive compact、max-output escalation/continuation 和不可恢复错误日志进入统一恢复流程。`core/loop.py` 现在通过 `ModelRetryRunner` 缓冲 streaming attempt，失败或截断 attempt 不会污染 UI 或 message store；CLI、MCP manager 和 tool executor 已接入 `.onecode/<session_id>/errors.jsonl`。补充错误、retry、loop、provider、CLI、MCP 和工具 executor 测试，并通过全量 `uv run python -m pytest tests -q`。

### TD-001: 工具结果消息 provider 投影

- **解决方式：** `MessageStore` 改为存储内部 `tool_result` message，Chat Completions adapter 在发送前投影为合法 `role="tool"` payload，并补充 loop/provider 测试。

### TD-002: 文件工具强制使用 sandbox guard

- **解决方式：** 新增受 `SandboxGuard` 保护的 `read_file` / `edit_file`，通过 `RegistryToolExecutor` 在执行前检查 guard，deny/ask 返回结构化 tool error。

### TD-003: Registry-backed 工具 runtime

- **解决方式：** 新增 `ToolDescriptor`、`ToolExecutionResult`、`ToolRuntime`、`ToolRegistry`、schema projection 和 concrete executor，`ContextEngine` 可从 registry 获取工具 schema。

### TD-005: 上下文治理已有基础 transcript，但缺少 compaction、projector 和通用 result store

- **解决方式：** 已落地 `services/context/projector.py`、`services/compaction/service.py`、session memory、共享 `ToolResultStorage` 和 compaction-aware `ContextEngine` preparer。`PreparedContext` 现在可把 compaction `usage_hints` 与 `transcript_refs` 写入 `ContextSnapshot`，并补充大工具结果替换、stored result refs 和 ContextEngine metadata 投影测试。剩余 compact safety 问题由 TD-014 跟踪；原 TD-015 已因设计取舍废弃。

### TD-006: 工具 metadata 已驱动结果预算和并发调度，但只读策略与 durable result store 仍未完整落地

- **解决方式：** `RegistryToolExecutor` 已消费 `ToolResultPolicy`，在注入 `ToolResultStorage` 后将超预算结果写入 durable store；`PermissionPolicy` 已消费 `read_only` 强制 read-only subagent 和非只读命令 ask；`grep` 的 result policy 已改为超过 20KB 时允许持久化，并补充 result store、search tool policy 和 executor 预算测试。

### TD-011: `core.loop` 与 subagent 当前上下文形成反向依赖

- **解决方式：** 将 `CurrentModelContext` 移到 `services/context/current_model_context.py`，`core.loop` 只依赖通用 context service，不再 import `services.subagents.*`；`services/subagents/__init__.py` 不再导出 `SubagentRunner`，需要 runner 的装配点改为直接 import `services.subagents.runner`。补充 import-boundary 测试防止 core 重新依赖 subagent 具体模块。

### TD-012: `/clear` 只切换消息链，未重绑定 session-scoped compaction 服务

- **解决方式：** `/clear` 改为复用 `CliRuntime.with_session()` 并返回新的 `CliRuntime`，统一重绑定 message store、trace recorder、executor result store、compaction service、session memory updater、subagent parent store 和 current model context。补充 CLI 测试覆盖新 session 资源重绑。

### TD-013: Compaction 投影持久化大结果非幂等，会重复写入 result store

- **解决方式：** 工具结果持久化改为幂等：同一 `tool_call_id` 且内容相同复用原文件，内容不同使用稳定内容 hash 后缀。该能力现由共享 `ToolResultStorage.persist_tool_result()` 提供。补充 result store 和 compaction 连续两次 `prepare_for_model()` 的 refs 稳定性测试。

### TD-015: Session memory 缺少真实 transcript anchor 和文件变更记录

- **废弃原因：** 后续 Session Memory 设计不再依赖真实 transcript UUID 或 `last_summarized_message_uuid` 作为压缩边界。恢复 transcript 后会基于重建出来的当前消息链重新估算 token 增长和工具调用增长，达到阈值时固定触发一次后台 Session Memory 提取。因此不需要补 `MessageStore` message metadata anchor。
- **废弃原因：** 后续 Session Memory 也不要求 executor 维护 `files_changed`，不要求 memory 文件包含 Files Changed 章节。Session Memory 由受限 fork agent 基于当前聊天记录和已有 memory 更新当前会话笔记；文件变更记录不是本设计的必要事实来源。
- **保留说明：** 当前代码里可能仍存在规则版 updater 读取 `files_changed` 或写出 Files Changed 章节的实现细节，但这不再构成技术债修复目标。后续重写 Session Memory 提取时可以删除该章节或改为由 fork agent 自行维护普通文本摘要，不应为旧 TD-015 新增 executor 文件变更 side effect。
