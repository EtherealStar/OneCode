# Tech Debt Tracker

最近审阅日期：2026-06-04

本台账记录当前已实现的 OneCode runtime 骨架中可由代码证据支持的技术债。条目依据 `architecture.md`、`docs/design-docs/core-beliefs.md` 和 `docs/exec-plans/active/path-sandbox-guard-plan.md` 中定义的目标边界与设计约束整理。

## 活跃技术债

| 债务 ID | 标题 | 类型 | 区域 | 优先级 | 状态 |
|:---|:---|:---|:---|:---|:---|
| TD-001 | 工具结果消息使用了与 provider 不兼容的内部形态 | 架构 / 代码 | `services/context/message_store.py`, `infrastructure/providers/chat_completions.py` | 高 | 已识别 |
| TD-002 | Sandbox guard 已实现，但尚未被真实文件系统工具执行路径强制使用 | 安全 / 架构 | `services/guard/`, `services/tools/`, 缺失的 `tools/` 实现 | 高 | 已识别 |
| TD-003 | 工具 runtime 缺少 descriptor、registry、校验和元数据驱动的 schema 生成 | 架构 | `services/tools/`, `core/context_engine.py` | 中 | 已识别 |
| TD-004 | 恢复类 transition 已定义，但 provider 和工具错误仍会绕过 loop 恢复流程 | 架构 / 测试 | `core/loop.py`, `core/transitions.py`, `core/runtime_state.py` | 中 | 已识别 |
| TD-005 | 上下文治理仍是内存 no-op，缺少 compaction、transcript 和 result store | 架构 / 测试 | `services/context/`, `core/context_engine.py` | 中 | 已识别 |

---

### TD-001: 工具结果消息使用了与 provider 不兼容的内部形态

- **类型：** 架构 / 代码
- **区域：** `services/context/message_store.py`, `infrastructure/providers/chat_completions.py`
- **优先级：** 高
- **状态：** 已识别
- **影响：** 真实工具接入后，OpenAI-compatible 的工具调用循环可能生成无效的后续 chat payload，因为工具结果被存储为包含 `tool_result` block 的 user message，而不是被投影为 provider 合法的 tool message。

**描述：**
`MessageStore.append_tool_results()` 当前把工具结果追加为 `{"role": "user", "content": [...]}`。Chat Completions adapter 随后会把 `snapshot.messages` 直接转发进 provider payload。这使内部消息结构绑定到类似另一个协议的 content-block 形态，并让 OpenAI-compatible adapter 缺少把内部工具结果投影为 `role="tool"` 和 `tool_call_id` 的步骤。

**引入原因：**
当前 runtime 仍是薄骨架，测试中使用 fake tool executor。这个简化消息形态让 loop 测试可以先表达工具调用续轮行为，而无需先实现 provider-neutral message model 和 provider-specific projector。

**修复方向：**
在 `services/context` 中引入 provider-neutral 的内部 message/tool-result 类型，再由各 provider adapter 投影为外部协议。对 OpenAI-compatible Chat Completions，应在 `_build_payload()` 发送前把工具结果转换为合法 tool message。补充 loop/provider 测试，断言工具调用后的第二次真实请求 payload。

**关联代码：**
- `services/context/message_store.py:L28` - `append_tool_results()` 将工具结果存储为 user message。
- `infrastructure/providers/chat_completions.py:L37` - `_build_payload()` 没有做 provider-specific message projection，直接转发 `snapshot.messages`。
- `tests/test_loop.py:L122` - 测试固定了当前 user-message 工具结果形态。

**架构约束：**
Provider-specific 字段必须留在 `infrastructure/providers/` 内。Core 和 context services 应使用内部结构，而不是 provider wire format。

---

### TD-002: Sandbox guard 已实现，但尚未被真实文件系统工具执行路径强制使用

- **类型：** 安全 / 架构
- **区域：** `services/guard/`, `services/tools/`, 缺失的 `tools/` 实现
- **优先级：** 高
- **状态：** 已识别
- **影响：** 路径沙箱可以独立分类和拒绝路径，但当前没有真实文件系统工具入口保证 read、write、list、delete 或 glob 操作在接触文件系统前调用 `SandboxGuard`。

**描述：**
`SandboxGuard` 和 `classify_path()` 已实现目标路径分类，以及 deny/ask/allow 的结果形态。但 `services/tools/executor.py` 目前只是 protocol，顶层 `tools/` 目录尚不存在，也没有 concrete executor 在文件系统副作用发生前调用 guard。直到完成集成前，路径安全只是已测试的库行为，还不是被 runtime 强制执行的安全边界。

**引入原因：**
活跃的 guard 执行计划仍在推进中。项目先实现并测试 guard primitives，再迁移具体工具和 executor 行为。

**修复方向：**
在 `tools/<tool_name>/` 下实现具体文件系统工具，通过 concrete `ToolExecutor` 路由执行，并要求每个文件系统操作边界都先经过 guard 检查。Deny 结果必须返回结构化 tool error，不触发 ask，也不访问文件系统；external directory 结果应进入活跃 guard 计划描述的权限路径。

**关联代码：**
- `services/guard/policy.py:L47` - `SandboxGuard` 暴露 runtime check API。
- `services/guard/boundary.py:L85` - `classify_path()` 生成 sandbox decision。
- `services/tools/executor.py:L10` - executor 目前只是 protocol，不强制 guard 行为。
- `tests/test_path_sandbox_guard.py:L126` - guard denial 只在 guard 层测试。

**架构约束：**
安全必须由工具执行路径保证，不能依赖 prompt 或模型行为。Guard deny 的优先级必须高于 hook、session allow 和 external directory ask。

---

### TD-003: 工具 runtime 缺少 descriptor、registry、校验和元数据驱动的 schema 生成

- **类型：** 架构
- **区域：** `services/tools/`, `core/context_engine.py`
- **优先级：** 中
- **状态：** 已识别
- **影响：** Runtime 尚不能从 metadata 推导工具行为，例如 read-only、是否修改文件系统、是否需要 guard、并发能力、timeout 或 result-size budget。这会限制动态工具组装，并提高后续工具行为滑向 ad hoc 实现的风险。

**描述：**
当前工具层只定义了 `ToolCall` 和 `ToolExecutor` protocol。`ContextEngine` 接收通用 `ToolSchemaProvider`，但还没有 `ToolDescriptor`、concrete `ToolRegistry`、输入校验器、handler contract 或 schema generator。因此工具 schema 与执行行为还没有绑定到同一个事实来源。

**引入原因：**
项目先实现 loop 骨架，使 provider 调用和工具调用续轮可以独立于完整工具 runtime 被测试。

**修复方向：**
在 `services/tools/types.py` 中增加 `ToolDescriptor`、`ToolResult` 和 `ToolRuntime` 等结构；实现 concrete registry 和 schema generator；让 `ContextEngine` 从 registry 获取 schema。工具执行应校验输入，并基于 descriptor metadata 决定 guard、并发和结果处理行为。

**关联代码：**
- `services/tools/types.py:L10` - 目前只定义了 `ToolCall`。
- `services/tools/executor.py:L10` - executor 是 protocol，缺少 registry lookup、输入校验和 metadata 处理。
- `core/context_engine.py:L27` - schema 生成是注入 protocol，还不是已实现的 registry boundary。

**架构约束：**
工具 schema、prompt 摘要和 handler 应从同一个 registry 生成。`core/loop.py` 不能 import 具体工具。

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

### TD-005: 上下文治理仍是内存 no-op，缺少 compaction、transcript 和 result store

- **类型：** 架构 / 测试
- **区域：** `services/context/`, `core/context_engine.py`
- **优先级：** 中
- **状态：** 已识别
- **影响：** 长会话和大型工具输出没有 durable transcript、large-result storage、compact boundary 或 structured projector。这会让后续改动难以围绕 context limit 和恢复行为进行验证。

**描述：**
`MessageStore` 是内存中的 append-only list。`ContextEngine` 默认使用 `NoOpContextPreparer`，`ContextSnapshot` 中的 `usage_hints` 和 `transcript_refs` 字段也没有由已实现服务填充。目标架构要求 message store、projector、compaction service、transcript 和 result store 分离，但当前只有最小消息列表。

**引入原因：**
骨架阶段需要一个简单 session state，让 loop 和 provider 测试能在 compaction/transcript services 引入前运行。

**修复方向：**
实现 `services/context/projector.py`、`services/compaction/service.py`、`services/compaction/transcript.py` 和 `services/compaction/result_store.py`。让 `ContextEngine` 在每次模型调用前编排这些服务，并补充大型工具结果替换、transcript refs、compact summaries 和 reactive compact retry 的测试。

**关联代码：**
- `services/context/message_store.py:L9` - 消息状态只保存在内存中。
- `core/context_engine.py:L31` - 默认 context preparation 是 no-op。
- `services/context/snapshot.py:L14` - usage hints 和 transcript refs 已存在，但未被填充。

**架构约束：**
Context 和 compaction 应保持为由 `core/context_engine.py` 编排的 services；compaction 细节不应进入 main loop。

---

## 已解决条目归档

当前尚未记录已解决的技术债。
