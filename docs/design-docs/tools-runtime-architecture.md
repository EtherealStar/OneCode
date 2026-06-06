# Tools Runtime Architecture

本文描述 `services/tools/` 的架构边界。该模块是工具运行时，不放具体工具实现；具体工具位于顶层 `tools/<tool_name>/`。

## 模块职责

`services/tools/types.py` 定义 provider-neutral 工具类型，包括 `ToolCall`、`ToolExecutionResult`、`ToolRuntime`、`ToolDescriptor`、`ValidationResult`、`ToolTarget`、`ToolResultPolicy` 和 `ToolCallClassification`。

`services/tools/registry.py` 管理当前启用的工具集合，并基于同一个可见工具视图生成 provider schema 和 prompt 工具说明。

`services/tools/schema.py` 将内部 `ToolDescriptor` 投影为 provider tool schema。当前实现是 OpenAI Chat Completions compatible function schema。

`services/tools/executor.py` 实现 `RegistryToolExecutor`，负责工具调用的完整执行链路、权限 preflight、hook、并发调度、结果预算和 trace。

## ToolDescriptor

`ToolDescriptor` 是工具的唯一事实来源。每个工具通过 descriptor 提供：

- `name`：snake_case 工具名，作为 registry key 和 provider-visible function name。
- `description`：provider schema 的短描述。
- `input_schema`：JSON Schema object。
- `output_schema`：内部工具结果对象 schema。
- `prompt`：system prompt 中的工具使用说明。
- `search_hint`：未来 deferred tool search 的能力提示。
- `validate_input`：JSON Schema 之外的值校验。
- `classify_input`：本次调用的 input-aware 分类。
- `handler`：具体执行函数。

新增工具不得要求修改 `core/loop.py`，也不应让 `services/tools/` 静态 import 顶层工具目录。

## Input-Aware Classification

每次工具调用在执行前必须通过 `classify_input()` 生成 `ToolCallClassification`。

分类字段：

- `read_only`：本次调用是否只读取状态。
- `modifies_filesystem`：本次调用是否会写入、创建、删除或移动文件系统内容。
- `concurrency_safe`：本次调用是否可与相邻安全调用并发。
- `targets`：本次调用触达的 `ToolTarget` 集合。
- `result_policy`：结果大小预算、预览和持久化意图。
- `permission_subject`：权限、hook 或审计可使用的紧凑 subject。

分类默认 fail closed：非只读、会修改文件系统、不可并发。

## ToolTarget

`ToolTarget` 是 guard 和 permission policy 的统一资源描述。文件、目录、glob、命令、URL、session state 或外部服务都应表达为 target，而不是让 executor 判断具体工具名。

当前 executor 对 `kind in {"file", "directory"}` 且 operation 为 `read`、`write`、`list`、`delete` 的 target 调用 `SandboxGuard`。`command/execute` target 由 `PermissionPolicy` 判断是否需要 ask。

## ToolRegistry

`ToolRegistry` 保持 descriptor 集合稳定排序，确保 provider payload、prompt section 和测试输出稳定。

可见性来源：

- registry 构造期的 `disabled_tools` 和 `denied_tools`。
- `RuntimeState.metadata["disabled_tools"]`、`denied_tools`、`hidden_tools`。
- 注入的 `PermissionPolicy.is_tool_visible()`。

`visible_descriptors(state)` 是 schema 和 prompt 的共同入口。被隐藏、禁用或拒绝的工具不会进入 `tool_schemas(state)`，也不会进入 `tool_prompt_sections(state)`。

## Execution Pipeline

`RegistryToolExecutor` 当前执行顺序：

```text
lookup descriptor
validate input_schema
validate_input
classify_input
collect guard policies from targets
evaluate PermissionPolicy or fallback guard decision
ask PermissionPrompter when required
run PreToolUse hook
if hook updates input, repeat validation/classification/guard/permission
execute handler
apply ToolResultPolicy
run PostToolUse or ToolError hook
apply executor-owned success side effects
record trace
return ToolExecutionResult
```

guard deny 或 permission deny 会在 handler 前返回结构化 tool error。hook 更新输入后必须重新校验和重新授权。

## Concurrency

当前 executor 已实现基于 `concurrency_safe` 的连续分批并发调度。

示例：

```text
[read A, grep B, edit C, read D]
  -> read A + grep B 并发批次
  -> edit C 串行
  -> read D 单调用批次
```

executor 会先做保守候选分类，再对批次内每个调用串行 preflight。若 preflight 后发现任何调用不再并发安全，则该批次退回 provider 顺序串行执行。并发 handler 的结果仍按 provider 原始顺序 finalize 和输出。

并发上限由构造参数或 `ONECODE_MAX_TOOL_CONCURRENCY` 控制，默认 10。

## Result Policy

`ToolResultPolicy` 描述结果预算：

- `max_result_size_chars`
- `persist_when_exceeded`
- `preview_chars`

当前 executor 已消费 `max_result_size_chars`。超出预算时，它返回 JSON 预览 payload，并在 metadata 中记录 `result_truncated`、`original_size_chars` 和 `max_result_size_chars`。

durable result store 尚未实现，因此 `persist_when_exceeded` 当前只表达目标意图。通用持久化应在 result store / compaction 边界落地后接入 executor，而不是让每个工具手写不同外置格式。

## Executor-Owned State Effects

工具 handler 不应直接修改主循环状态。当前 executor 在成功结果后统一维护 `RuntimeState.metadata["files_read"]`，记录 `read_file` 和 `edit_file` 成功触达的路径。这让 read handler 可以保持并发安全，也让 `edit_file` 的 read-before-edit 规则有稳定状态来源。

## 错误形态

执行入口将未知工具、schema 错误、工具级校验错误、分类错误、guard 错误、permission ask required、用户拒绝和 handler exception 都转换为 `ToolExecutionResult(is_error=True)`。这些错误作为 tool result 回填给模型，而不是让主循环直接崩溃。
