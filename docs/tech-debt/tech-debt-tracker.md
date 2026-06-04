# Tech Debt Tracker

最近审阅日期：2026-06-04

本台账记录当前已实现的 OneCode runtime 骨架中可由代码证据支持的技术债。条目依据 `architecture.md`、`docs/design-docs/core-beliefs.md`、`docs/exec-plans/active/` 和当前代码边界整理。

## 活跃技术债

| 债务 ID | 标题 | 类型 | 区域 | 优先级 | 状态 |
|:---|:---|:---|:---|:---|:---|
| TD-004 | 恢复类 transition 已定义，但 provider 和工具错误仍会绕过 loop 恢复流程 | 架构 / 测试 | `core/loop.py`, `core/transitions.py`, `core/runtime_state.py` | 中 | 已识别 |
| TD-005 | 上下文治理已有基础 transcript，但缺少 compaction、projector 和通用 result store | 架构 / 测试 | `services/context/`, `core/context_engine.py` | 中 | 部分缓解 |
| TD-006 | 工具 metadata 已驱动结果预算，但只读策略和并发执行策略仍未落地 | 架构 / 测试 | `services/tools/types.py`, `services/tools/executor.py`, `tools/read_file/tool.py`, `tools/grep/tool.py` | 低 | 部分缓解 |
| TD-007 | CLI 主界面已落地，但缺少结构化运行事件、streaming 和权限交互 | UI / 架构 | `ui/cli/`, `services/observability/`, `core/loop.py` | 中 | 已识别 |
| TD-008 | 动态 prompt 已落地，但可见工具裁剪尚未接入完整 permission policy | 架构 / 安全 | `prompts/`, `services/tools/registry.py`, `services/guard/` | 中 | 已识别 |

---

### TD-004: 恢复类 transition 已定义，但 provider 和工具错误仍会绕过 loop 恢复流程

- **类型：** 架构 / 测试
- **区域：** `core/loop.py`, `core/transitions.py`, `core/runtime_state.py`
- **优先级：** 中
- **状态：** 已识别
- **影响：** 可重试 provider failure、context limit failure、max-output interruption 和工具执行错误目前可能直接逃出 loop，而不是变成可观测的 transition 并进入受控恢复流程。

**描述：**
`TransitionReason` 已包含恢复状态，`RuntimeState` 也包含 reactive compact 和 max-output recovery 计数器。`AgentLoop.run_loop()` 会递增 turn 并直接调用 `model_client.send(snapshot)`，但没有捕获 provider-neutral error、分类 retryability、调用 compaction，或把工具失败转换为结构化结果。

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

### TD-005: 上下文治理已有基础 transcript，但缺少 compaction、projector 和通用 result store

- **类型：** 架构 / 测试
- **区域：** `services/context/`, `core/context_engine.py`
- **优先级：** 中
- **状态：** 部分缓解
- **影响：** 长会话已经有基础 JSONL transcript 可恢复，但模型可见上下文仍没有 compact boundary、structured projector、summary memory 或通用 result store。这会让后续改动仍难以围绕 context limit 和恢复行为进行完整验证。

**描述：**
`MessageStore` 现在是内存优先且带 JSONL transcript 的 append-only store，`services/context/transcript.py` 会把消息定时写入 `.onecode/<session_id>/messages.jsonl`，并把超过 50KB 的工具结果外置到 `tool-results/` 后在恢复时读回。`ContextEngine` 默认仍使用 `NoOpContextPreparer`，`ContextSnapshot` 中的 `usage_hints` 和 `transcript_refs` 字段也没有由已实现服务填充。目标架构要求 projector、compaction service 和通用 result store 分离，这些治理能力仍未落地。

**引入原因：**
骨架阶段需要一个简单 session state，让 loop 和 provider 测试能先运行。基础 transcript 已作为第一步补齐，但 compaction/projector/result store 仍依赖后续设计。

**修复方向：**
实现 `services/context/projector.py`、`services/compaction/service.py` 和通用 result store。让 `ContextEngine` 在每次模型调用前编排这些服务，并补充模型可见大型工具结果替换、transcript refs、compact summaries 和 reactive compact retry 的测试。

**关联代码：**
- `services/context/message_store.py:L16` - 消息状态仍以内存为模型上下文来源，持久化 transcript 尚未参与投影策略。
- `services/context/transcript.py:L27` - 基础 JSONL transcript 已落地，但只覆盖主链恢复和工具结果外置。
- `core/context_engine.py:L31` - 默认 context preparation 是 no-op。
- `services/context/snapshot.py:L14` - usage hints 和 transcript refs 已存在，但未被填充。

**架构约束：**
Context 和 compaction 应保持为由 `core/context_engine.py` 编排的 services；compaction 细节不应进入 main loop。

---

### TD-006: 工具 metadata 已驱动结果预算，但只读策略和并发执行策略仍未落地

- **类型：** 架构 / 测试
- **区域：** `services/tools/types.py`, `services/tools/executor.py`, `tools/read_file/tool.py`, `tools/grep/tool.py`
- **优先级：** 低
- **状态：** 部分缓解
- **影响：** 工具 descriptor 已经能表达 `read_only`、`concurrency_safe` 和 `max_result_size_chars`，executor 已消费 `ToolResultPolicy.max_result_size_chars` 生成结构化截断预览，但 `read_only` 和 `concurrency_safe` 仍未驱动运行时策略。后续增加更多工具时，只读权限裁剪和并发调度仍可能退化为分散实现。

**描述：**
`ToolDescriptor` 已包含工具元数据，`RegistryToolExecutor` 也已经使用 `modifies_filesystem` 决定 guard read/write 检查，并通过 `_apply_result_policy()` 对超过 `max_result_size_chars` 的工具结果生成 JSON 预览和截断 metadata。`grep` 声明 20KB 结果预算并依赖该机制。但 `read_only` 目前没有参与策略判断，`concurrency_safe` 没有驱动并发分批，超大结果也还没有持久化或转交 compaction/result store。`read_file` 通过行数上限控制常见输出规模，但没有接入统一 result-size budget。

**引入原因：**
当前文件工具集成优先完成 provider-compatible 工具循环、guard 强制执行和 hooks。完整工具调度与结果预算依赖后续 compaction/result store 能力。

**修复方向：**
让 executor 或工具运行时继续消费 descriptor metadata：基于 `read_only`/权限策略裁剪能力，基于 `concurrency_safe` 批量调度安全工具，基于 `max_result_size_chars` 在现有预览基础上接入 durable result-store 引用。补充覆盖并发调度、只读策略和 result-store 持久化的测试。

**关联代码：**
- `services/tools/types.py:L61` - `ToolDescriptor` 已定义 `read_only`、`concurrency_safe` 和 `max_result_size_chars`。
- `services/tools/executor.py:L224` - executor 消费 guard target 和 result policy，但尚未用 `read_only` 或 `concurrency_safe` 调度。
- `tools/read_file/tool.py:L42` - `read_file` 未设置统一 result-size budget。
- `tools/grep/tool.py:L142` - `grep` 已设置 20KB result policy，但超过预算时仍只有预览，没有 durable result-store 引用。

**架构约束：**
工具行为应由 registry metadata 驱动，避免在具体工具或主循环中散落 tool-name 分支。大结果治理应与 compaction/result store 边界协同。

---

### TD-007: CLI 主界面已落地，但缺少结构化运行事件、streaming 和权限交互

- **类型：** UI / 架构
- **区域：** `ui/cli/`, `services/observability/`, `core/loop.py`
- **优先级：** 中
- **状态：** 已识别
- **影响：** 第一版 CLI 能启动真实 runtime、执行普通 prompt、展示工具/状态/历史、恢复 JSONL transcript 并清空会话，但运行中只能显示同步的 `Running...` 和最终文本。工具调用进度、transition、provider recovery、权限 ask 和 future compact 状态还不能以结构化方式呈现给用户。

**描述：**
`ui/cli/app.py` 当前通过 `AgentLoop.run(prompt)` 同步等待最终结果；`core/loop.py` 没有 streaming 或 observability 订阅接口，`services/observability/` 仍是目标模块。`SandboxGuard` 的 ask 结果会作为工具错误回到模型或最终文本中，CLI 不提供人工确认 flow。provider 错误也只由 CLI 捕获并显示简短错误，不进入 runtime recovery UI。

**引入原因：**
CLI 第一版刻意保持轻量标准库实现，优先完成可运行主界面、固定工具装配、slash commands 和 JSONL 恢复；streaming、权限交互和结构化事件依赖后续 runtime 服务。

**修复方向：**
落地 `services/observability/` 事件流和 loop 事件发布后，让 CLI 渲染 model call、tool call、transition、usage、compact 和 provider recovery 事件。权限 ask 应接入明确的用户确认协议，并保证 deny 仍优先。未来 streaming model client 可让 CLI 渲染增量 assistant 文本。

**关联代码：**
- `ui/cli/app.py:L55` - 主循环只显示 `Running...` 并同步等待 `AgentLoop.run()` 返回。
- `ui/cli/renderer.py:L1` - renderer 只格式化静态文本和历史摘要，没有消费结构化 trace event。
- `core/loop.py:L41` - loop 调用模型和工具时尚未发布可订阅事件。
- `services/guard/policy.py:L36` - ask policy 已存在，但 CLI 尚无用户确认 UI。

**架构约束：**
CLI 不应直接实现 runtime recovery、权限策略或 provider-specific 分支；应消费 core/services 发布的 provider-neutral 状态和事件。

---

### TD-008: 动态 prompt 已落地，但可见工具裁剪尚未接入完整 permission policy

- **类型：** 架构 / 安全
- **区域：** `prompts/`, `services/tools/registry.py`, `services/guard/`
- **优先级：** 中
- **状态：** 已识别
- **影响：** 第一版 `ToolRegistry.visible_descriptors(state)` 已让 tool schema 和 tool prompt section 使用同一个可见工具视图，但它只支持 registry 构造期的 disabled/denied 工具名和 `RuntimeState.metadata` 中的简单工具名集合。真实项目权限、用户规则、组织策略或 guard policy 还不能在模型调用前完整裁剪工具能力。

**描述：**
`DynamicPromptAssembler` 会从 `ToolRegistry.visible_descriptors(state)` 读取工具 prompt，`tool_schemas(state)` 也基于同一视图生成 provider-visible schema。当前可见性接口用于保持 prompt/schema 一致，并为后续 deny-first policy 接入提供边界；它还没有把路径级 guard、项目配置、会话 allow/deny 或未来 permission policy 合并成完整工具可见性判断。执行入口仍依赖 `RegistryToolExecutor` 对具体 `ToolTarget` 重复执行 guard 检查。

**引入原因：**
动态 prompt 架构需要先有统一可见工具视图，才能避免 schema 和 prompt 看到不同工具集合。完整 permission policy 需要独立设计规则来源、优先级、审计和用户确认 flow，因此未塞进第一版 prompt assembler。

**修复方向：**
设计并实现 provider-neutral permission policy service，让 registry 可见性查询消费该 service 的 blanket deny/disabled 结果。保持 deny-first 顺序：任意有效 deny 都应同时裁剪 schema、prompt 和执行入口。路径参数级判断仍应在工具执行前基于实际输入重复校验，不应由 prompt 组装阶段猜测。

**关联代码：**
- `services/tools/registry.py:L31` - `visible_descriptors(state)` 是 prompt/schema 统一视图入口。
- `prompts/assembler.py:L33` - assembler 从 registry 读取当前可见工具。
- `services/tools/executor.py:L96` - executor 仍在执行入口检查具体工具调用。
- `services/guard/policy.py:L19` - guard policy 已能对具体路径返回 allow/ask/deny。

**架构约束：**
Prompt 裁剪只能减少模型看到不可用能力的机会，不能替代执行入口的确定性权限检查。Hook、会话 allow 和用户确认都不能覆盖 deny。

---

## 已解决条目归档

### TD-001: 工具结果消息 provider 投影

- **解决方式：** `MessageStore` 改为存储内部 `tool_result` message，Chat Completions adapter 在发送前投影为合法 `role="tool"` payload，并补充 loop/provider 测试。

### TD-002: 文件工具强制使用 sandbox guard

- **解决方式：** 新增受 `SandboxGuard` 保护的 `read_file` / `edit_file`，通过 `RegistryToolExecutor` 在执行前检查 guard，deny/ask 返回结构化 tool error。

### TD-003: Registry-backed 工具 runtime

- **解决方式：** 新增 `ToolDescriptor`、`ToolExecutionResult`、`ToolRuntime`、`ToolRegistry`、schema projection 和 concrete executor，`ContextEngine` 可从 registry 获取工具 schema。
