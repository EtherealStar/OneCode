# Error Handling Architecture

本文描述 OneCode 的错误处理与异常管理架构。核心理念是：统一领域分类、Provider 解耦、重试与治理前置、以及可观测性隔离。

## 设计信念

1. **统一异常分类**：所有由系统或调用链路抛出的错误都必须归一化为领域分类（`ErrorCategory`），隔离具体的底层库实现或第三方供应商结构。
2. **重试机制下沉**：关于能否重试（如限流、网络故障）的决策只依赖标准化分类的 `retryable` 属性，在基础设施边界（如 `ModelRetryRunner`）内自动消化，主循环仅处理重试后的最终确定结果。
3. **Trace 与 Error 日志分离**：控制台不应被满屏 traceback 刷屏。简短的事件记录到 Trace，而未捕获或导致阻断的详细异常日志记录到单独的 Error Log 文件，以供排障使用。
4. **安全与脱敏**：落盘的任何错误栈和日志，必须经过脱敏清洗（替换密钥串、相对化绝对路径），防止隐私泄露。

## 文件职责

主要实现分布在 `services/errors.py`，并与 `services/observability/` 中的 `error_log.py` 和 `sanitize.py` 协同工作。

| 文件 | 职责 |
|:---|:---|
| `services/errors.py` | 定义 `ErrorCategory` 枚举、`OneCodeError` 基类以及具体的错误类型，提供标准化转换工具。 |
| `services/model/client.py` | `ModelRetryRunner` 基于 `retryable` 和错误类别执行模型调用的自动重试。 |
| `services/observability/error_log.py` | `ErrorLogRecorder` 负责拦截并写入不可恢复的错误信息。 |
| `services/observability/sanitize.py` | `sanitize_attributes` 负责对错误栈、消息体内的凭证和文件绝对路径进行脱敏。 |

## 核心抽象

### 1. 错误分类（ErrorCategory）

通过 `ErrorCategory` 枚举定义系统全局认识的领域错误类型：
- `PROVIDER`：供应商侧错误（如认证失败、500 服务错误）
- `NETWORK`：网络连接或超时
- `RATE_LIMIT`：被提供商限流（触发退避重试）
- `CONTEXT_LIMIT`：上下文超限（触发上下文自动压缩治理）
- `FILESYSTEM`：文件读写权限/路径相关（自动捕捉并转化 `OSError` / `FileNotFoundError`）
- `SHELL`：工具脚本执行失败
- `TOOL`：工具本身运行时的异常
- `PERMISSION`：被 Guard / 权限引擎拒绝
- `INTERNAL`：其他框架级内部错误

### 2. 标准异常基类（OneCodeError）

所有的内部抛错需继承 `OneCodeError`。它规定了以下必须实现的属性：
- `category` (`ErrorCategory`): 错误分类。
- `message` (`str`): 原始内部错误信息。
- `safe_message` (`str`): 适合安全展示给前端、不含敏感路径及 token 的文案。
- `retryable` (`bool`): 是否可在当前维度立即进行安全重试。
- `metadata` (`dict`): 附带的现场上下文。

为了方便开发，框架提供了特定的派生类，例如 `AbortError`、`ConfigParseError`、`ShellError`、`ToolRuntimeError` 等。

### 3. ErrorDetails 与自动映射

为了处理 Python 原生异常以及第三方包异常，系统提供了助手函数（如 `onecode_error_details` 和 `to_error`）。该层逻辑通过类名、`errno`、或者已有的鸭子类型属性（如 `retry_after_seconds`），推导并映射出一个标准化的 `ErrorDetails` 对象，确保进入 Observability 层的数据结构恒定。

## 关键机制与运行流程

### 重试与状态恢复（Recovery）

OneCode 不在业务代码中随意 `try-except Exception`，而是利用适配器拦截并转换异常。
- **模型重试**：`ModelRetryRunner` 捕获 `ProviderError`，如果是 `RATE_LIMIT` 或是支持的 `NETWORK` 超时（`retryable=True`），在配置的限制内执行指数退避重试。重试中间环节的失败作为 partial 事件丢给 trace，不中断外层。
- **上下文超额治理**：当截获 `CONTEXT_LIMIT`（上下文长度溢出）异常时，在首次发生时不会直接将报错抛给 CLI，而是触发内置的 `reactive_compact_retry` 事件，让引擎去执行 `ContextEngine` 的自动压缩（Reactive Compact），腾出空间后再战。

### 统一收集与脱敏（Sanitization）

未能在重试中恢复的严重异常、沙箱违例异常、或者 MCP/工具崩溃，最终由 `ErrorLogRecorder.record_error` 吞入：
1. **内容脱敏**：错误 Message 与 traceback 会经过 `sanitize_attributes`，内部用正则表达式遮掩 Bearer Token、`sk-*` 秘钥串。
2. **路径相对化**：`FileNotFoundError` 或 `PermissionError` 带出的绝对路径会被映射为相对于当前工作区（Workspace）的路径（或标识为外部路径），避免暴露本地物理盘结构。
3. **独立存储**：最终序列化写入 `{workspace}/.onecode/sessions/<session_id>/errors.jsonl`，为后续审计或回溯提供确凿的脱敏证据，而不污染 `.trace` 日志。
