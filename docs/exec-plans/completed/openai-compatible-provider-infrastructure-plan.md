# OpenAI 兼容大模型 provider 基础设施实现计划

## 背景

OneCode 的模型边界要求 provider 被隔离在 `infrastructure/`，核心 runtime 只理解内部结构，例如 `LLMResponse`、`ToolCall`、`ModelUsage` 和 provider-neutral error。当前仓库已经有主循环、上下文重建、`services/model/client.py` 和 `services/model/types.py` 的最小骨架，但还没有真实 provider、provider 配置、模型列表发现或 UI 可消费的模型目录接口。

本计划实现一层基础设施：通过 OpenAI Chat Completions 兼容 HTTP 协议访问不同大模型服务商，不使用任何 provider SDK。默认服务商地址可以在代码中硬编码，例如 OpenAI、Claude OpenAI 兼容网关、DeepSeek、GLM/BigModel、MiniMax、硅基流动、Gemini 等；同时允许用户配置自定义 base URL。基础设施还需要支持查询服务器可提供的模型列表，后续 UI 可以直接消费这一能力来展示 provider/model 选项。

配置策略已经收敛为单一路径：模型 provider 运行时配置只从项目根目录 `.env` 文件读取，不读取系统环境变量，不读取 JSON/TOML 项目配置，也不通过 `/connect` 或 factory 直接注入明文 API key。provider 基础设施需要和项目运行环境一起收敛——依赖管理、测试入口、配置模板和 provider client 不能各自形成一套约定；项目应以 `uv` 固定 Python 环境和依赖入口，以 `.env` 固定本地模型网关配置入口，再由 `infrastructure/` 把配置解析、provider catalog、HTTP transport 和 Chat Completions 适配组合起来。

## 参考依据

- `architecture.md`
  - `services/model/` 定义 OneCode 内部模型边界。
  - 具体 provider 适配放在 `infrastructure/providers/`。
  - `infrastructure/config/` 负责环境变量和项目配置读取。
  - provider-specific 字段不能泄露到主循环、工具或 compaction。

- `docs/design-docs/core-beliefs.md`
  - OneCode 不应绑定某个模型 SDK。
  - provider 协议、字段、错误和 usage 信息应被归一化为内部结构。
  - 主循环只依赖内部 `LLMResponse`、`ToolCall` 和错误类型。

- `docs/exec-plans/active/project-main-loop-implementation.md`
  - 当前阶段主循环通过注入的 `ModelClient` 调用模型。
  - 真实 Chat Completions provider 当时被明确列为后续独立计划。
  - 主循环续轮只看 `LLMResponse.tool_calls`，不看 provider-specific stop reason。

## 目标

实现 OpenAI 兼容模型 provider 基础设施：

- 不使用 OpenAI、Anthropic、DeepSeek 或其他厂商 SDK。
- 使用标准 HTTP 请求访问 OpenAI 兼容接口。
- 支持内置 provider catalog，硬编码常见服务商的默认 base URL。
- 支持自定义 provider base URL。
- 支持按 provider 配置 API key、默认模型和请求参数。
- 为 CLI `/connect` 指令预留交互接口。
- 实现 Chat Completions 兼容的 `ModelClient`。
- 将 provider 响应归一化为 `LLMResponse`、`ToolCall`、`ModelUsage`。
- 支持查询 provider 的模型列表，作为后续 UI 接口的数据来源。
- 给 provider、模型列表和错误归一化建立单元测试。

## 非目标

- 不实现完整 UI，只定义 `/connect` 指令所需的 CLI 调用边界和交互流程。
- 不实现流式输出。
- 不实现 Responses API 或非 OpenAI 兼容协议。
- 不实现 fallback model、自动重试、负载均衡或 provider 路由策略。
- 不实现真实密钥管理 UI。
- 不把 provider-specific 字段暴露给 `core/loop.py`。
- 不在本计划中重写工具 schema 系统；只适配现有 `ContextSnapshot.tool_schemas`。

## 设计

### 1. uv 运行环境

OneCode 应使用 `uv` 作为项目 Python 环境和依赖的唯一入口。provider 基础设施依赖 `python-dotenv` 解析 `.env`，测试依赖 `pytest`，这些依赖应进入 `pyproject.toml`，并由 `uv.lock` 锁定。

`pyproject.toml` 应保持小而明确：

- project metadata 只描述 OneCode 当前包名、版本、Python 版本要求和基础描述。
- runtime dependencies 包含 provider 基础设施运行所需依赖，例如 `python-dotenv`。
- dev dependency group 包含测试依赖，例如 `pytest`。
- pytest 配置可以放在 `pyproject.toml`，让测试入口稳定指向 `tests`。

本地环境流程应统一为：

- `uv sync --dev` 同步 `.venv`。
- Windows 手动激活时使用 `.\.venv\Scripts\Activate.ps1`。
- 常规测试使用 `uv run python -m pytest tests -q`。
- 编译检查使用 `uv run python -m compileall core services infrastructure`。

代码、文档和 agent 指引都应围绕这些命令组织，避免同时维护 pip、requirements、手动 venv 或系统 Python 的替代路径。

### 2. .env 配置

`.env.example` 是模板，提交到仓库；`.env` 是本地真实配置文件，被 `.gitignore` 忽略。

必填变量：

- `ONECODE_PROVIDER_ID`
- `ONECODE_MODEL`
- `ONECODE_API_KEY`

按需变量：

- `ONECODE_BASE_URL`
- `ONECODE_TIMEOUT_SECONDS`
- `ONECODE_EXTRA_HEADERS`
- `ONECODE_DEFAULT_PARAMS`

`custom` 和 `claude-openai-compatible` provider 必须提供 `ONECODE_BASE_URL`。

`.env.example` 应体现默认配置形状，但不能包含真实密钥。推荐模板以 `custom` 为默认 provider，留空 `ONECODE_MODEL`、`ONECODE_BASE_URL` 和 `ONECODE_API_KEY`，并提供保守的 timeout、headers 和 default params 示例：

- `ONECODE_TIMEOUT_SECONDS=60`
- `ONECODE_EXTRA_HEADERS={}`
- `ONECODE_DEFAULT_PARAMS={}`

`.env` 只承担本地模型 provider 配置职责，不应扩展为通用项目配置文件。后续如果 CLI 提供 `/connect` 或图形化连接流程，也只能展示 provider 选项、引导用户编辑 `.env` 或生成等价本地配置，不能把 API key 注入 runtime factory 或写入其他配置格式。

### 3. 安全约束

- 不从 `os.environ` 读取模型网关变量。
- `python-dotenv` 读取时关闭 interpolation，避免 `.env` 间接引用系统环境变量。
- `ResolvedProviderConfig.api_key` 使用 `repr=False`，避免常规对象打印泄露密钥。
- API key 只在 provider HTTP header 构造阶段使用。
- `.env` 不提交，`.env.example` 不包含真实密钥。
- factory、connection service、CLI command 和测试 helper 都不应新增绕过 `.env` 的密钥传入路径。

### 4. Provider Catalog

新增 `infrastructure/providers/catalog.py`，定义内置 provider 和用户自定义 provider 的配置结构。

建议结构：

```python
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderDefinition:
    id: str
    display_name: str
    base_url: str
    api_key_env: str | None = None
    models_path: str = "/models"
    chat_completions_path: str = "/chat/completions"
    default_headers: dict[str, str] = field(default_factory=dict)
    notes: str | None = None
```

内置 catalog 先覆盖：

- `openai`
  - `https://api.openai.com/v1`
  - 默认密钥环境变量：`OPENAI_API_KEY`
- `deepseek`
  - `https://api.deepseek.com`
  - 默认密钥环境变量：`DEEPSEEK_API_KEY`
- `glm`
  - `https://open.bigmodel.cn/api/paas/v4`
  - 默认密钥环境变量：`BIGMODEL_API_KEY`
- `minimax`
  - `https://api.minimax.chat/v1`
  - 默认密钥环境变量：`MINIMAX_API_KEY`
- `siliconflow`
  - `https://api.siliconflow.cn/v1`
  - 默认密钥环境变量：`SILICONFLOW_API_KEY`
- `gemini`
  - `https://generativelanguage.googleapis.com/v1beta/openai`
  - 默认密钥环境变量：`GEMINI_API_KEY`
  - Gemini OpenAI 兼容文档中的 base URL 可能带尾部 `/`；配置解析时统一去掉尾部斜杠，再拼接 path。
- `claude-openai-compatible`
  - base URL 先作为可覆盖定义保存。
  - 因 Claude 官方原生 API 不是 OpenAI Chat Completions 协议，本项只代表用户提供的 Claude OpenAI 兼容网关，不假定 Anthropic 原生地址可直接使用。
  - `custom` 和 `claude-openai-compatible` 不应假设默认 base URL，必须由 `.env` 显式提供。
- `custom`
  - 由 `.env` 提供 `base_url` 和 `api_key`。
  - `custom` 不应假设默认 base URL，必须由 `.env` 显式提供。

catalog 只描述 provider 事实，不负责发请求、不持有 runtime state、不读取 `.env`、不保存 API key、不创建 HTTP client。

catalog 还需要提供面向 CLI 的展示顺序和显示名，例如：

```python
CONNECT_PROVIDER_ORDER = (
    "openai",
    "deepseek",
    "glm",
    "minimax",
    "siliconflow",
    "gemini",
    "claude-openai-compatible",
    "custom",
)
```

CLI 不应该手写这份列表，而应通过 provider catalog 或 factory 查询可连接选项。

### 5. Provider 配置加载

新增或扩展：

- `infrastructure/config/env.py`

`infrastructure/config/env.py` 使用 `python-dotenv` 读取项目根目录 `.env`，负责 `.env` 解析、字段校验、默认值合并，输出 `ResolvedProviderConfig`。

定义运行时模型配置：

```python
@dataclass(frozen=True)
class ModelProviderConfig:
    provider_id: str
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    timeout_seconds: float = 60.0
    extra_headers: dict[str, str] = field(default_factory=dict)
    default_params: dict[str, object] = field(default_factory=dict)
```

`ResolvedProviderConfig` 应包含 provider definition、provider id、display name、base URL、model、API key、timeout、headers、default params、models path 和 chat completions path。

配置解析规则：

- `ONECODE_BASE_URL` 优先于 catalog 默认 base URL。
- base URL 应去除尾部 `/`，并验证 scheme 和 host。
- `ONECODE_TIMEOUT_SECONDS` 应解析为数字，缺省为 `60`。
- `ONECODE_EXTRA_HEADERS` 应解析为 JSON object，且 key/value 都必须是字符串。
- `ONECODE_DEFAULT_PARAMS` 应解析为 JSON object，用于附加到 Chat Completions payload。
- 配置错误应抛出 provider-neutral `ProviderError(error_type="configuration_error")`。

配置模块输出完整解析后的 `ResolvedProviderConfig`，provider 实现只消费解析结果，不自己猜环境变量。

### 6. HTTP Client 边界

新增 `infrastructure/providers/http.py` 或在 provider 内封装最小 HTTP transport。

由于本计划不使用 SDK，HTTP 层可先使用 Python 标准库 `urllib.request`，避免新增依赖。若项目之后接受 `httpx` 等依赖，也应通过同一 transport 协议注入，方便测试替换。

建议协议：

```python
class HttpTransport(Protocol):
    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
    ) -> dict[str, object]:
        ...

    def get_json(
        self,
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, object]:
        ...
```

transport 应统一处理：

- 请求体 JSON 编码，默认补充 `Content-Type: application/json`。
- 响应必须解析为 JSON object；非 JSON 或非 object 响应应转换为 `ProviderError(error_type="invalid_response")`。
- HTTP 401/403 应转换为 authentication error。
- HTTP 429 应转换为 rate limit error，并标记 retryable。
- HTTP 5xx 应转换为 server error，并标记 retryable。
- 网络错误和超时应转换为 retryable network error。

测试使用 fake transport，不访问真实网络、不需要 API key。

### 7. Chat Completions Provider

新增 `infrastructure/providers/chat_completions.py`，实现 `services.model.client.ModelClient`。

职责：

- 从 `ContextSnapshot` 构造 OpenAI 兼容请求 payload。
- 设置 `Authorization: Bearer <api_key>`。
- 拼接 `base_url + /chat/completions`。
- 透传 `model`、`messages`、`tools` 和基础参数。
- 解析 `choices[0].message`。
- 将 tool calls 转换为 `services.tools.types.ToolCall`。
- 将 usage 转换为 `ModelUsage`。
- 将最终文本和 assistant message 转换为 `LLMResponse`。
- 将 provider HTTP 错误、JSON 错误和协议缺失字段转换为 provider-neutral error。

请求 payload 初版：

```python
{
    "model": config.model,
    "messages": [
        {"role": "system", "content": snapshot.system_prompt},
        *snapshot.messages,
    ],
    "tools": snapshot.tool_schemas,
    **config.default_params,
}
```

当 `snapshot.system_prompt` 为空时可以不注入 system message。`tools` 为空时可以省略，避免部分兼容服务对空数组行为不一致。

### 8. Tool Call 解析

OpenAI 兼容 tool call 通常位于：

```json
{
  "tool_calls": [
    {
      "id": "call_x",
      "type": "function",
      "function": {
        "name": "read_file",
        "arguments": "{\"path\":\"a.txt\"}"
      }
    }
  ]
}
```

解析规则：

- `function.name` 映射到 `ToolCall.name`。
- `function.arguments` 必须按 JSON 对象解析为 `ToolCall.input`。
- arguments 为空字符串时按 `{}` 处理。
- arguments 不是 JSON 对象时返回 provider error，不把坏输入交给工具执行器。
- 缺失 id 时生成稳定 fallback id，例如 `call_0`，并在 trace/error metadata 中标记 provider 缺失字段。

### 9. 文本与 Assistant Message 归一化

`LLMResponse.assistant_message` 应保存 OneCode 内部 message store 可继续投影的 assistant message。初版可以保留 OpenAI 兼容结构：

```python
{
    "role": "assistant",
    "content": content,
    "tool_calls": raw_tool_calls_if_any,
}
```

`LLMResponse.final_text` 从 `message.content` 提取：

- 字符串 content：直接使用。
- list content：拼接其中 `type == "text"` 的 `text` 字段。
- 无 content 但有 tool calls：`final_text` 为空字符串。

### 10. Usage 归一化

OpenAI 兼容 usage 通常提供：

```json
{
  "prompt_tokens": 10,
  "completion_tokens": 20,
  "total_tokens": 30
}
```

映射为：

- `prompt_tokens` -> `ModelUsage.input_tokens`
- `completion_tokens` -> `ModelUsage.output_tokens`
- `prompt_tokens_details.cached_tokens` -> `cache_read_input_tokens`，如果 provider 提供。

未知 usage 字段不进入 core，但可以保留在 provider 内部 debug metadata 或未来 observability 事件中。

### 11. 模型列表发现

新增 `infrastructure/providers/model_catalog.py`，提供 provider 模型列表查询能力。

建议类型：

```python
@dataclass(frozen=True)
class ProviderModel:
    id: str
    display_name: str | None = None
    owned_by: str | None = None
    raw: dict[str, object] = field(default_factory=dict)


class ModelCatalogClient:
    def list_models(self, provider_id: str | None = None) -> tuple[ProviderModel, ...]:
        ...
```

实现方式：

- 请求 `GET {base_url}/models`。
- 使用相同认证 header。
- 解析 OpenAI 兼容响应 `{"data": [{"id": "..."}]}`。
- 结果按 `id` 排序，保持 UI 稳定。
- HTTP 失败时返回结构化错误，不返回半解析数据。
- `/models` 返回值缺少 data list 时应转换为 invalid response。

后续 UI 可以消费一个更上层的 provider registry service：

```python
@dataclass(frozen=True)
class ProviderStatus:
    provider_id: str
    display_name: str
    base_url: str
    configured: bool
    models: tuple[ProviderModel, ...] = ()
    error: str | None = None
```

本计划先实现 infrastructure 能力和 tests，不实现 UI 渲染。

### 12. `/connect` 指令预留接口

后续 CLI 增加 `/connect` 指令时，交互流程应由 UI 负责，provider 事实和配置解析由 infrastructure 负责。

用户输入 `/connect` 后，CLI 显示 provider catalog 中可选择的服务商名和 `custom` 选项。显示内容应来自 `ProviderDefinition.display_name`，而不是 CLI 内部硬编码。

建议交互：

```text
/connect

Select provider:
1. OpenAI
2. DeepSeek
3. GLM / BigModel
4. MiniMax
5. SiliconFlow
6. Gemini
7. Claude OpenAI-compatible
8. Custom
```

当用户选择内置服务商：

1. CLI 读取对应 `ProviderDefinition`。
2. CLI 提示输入 API key。
3. 输入不回显，且不写入普通日志。
4. infrastructure 用 provider 默认 base URL 和用户输入的 API key 生成 `ResolvedProviderConfig`。
5. 可选调用 `list_models()` 获取模型列表，供 CLI 或未来 UI 选择默认模型。

当用户选择 `custom`：

1. CLI 提示输入 OpenAI-compatible base URL。
2. 用户回车后，CLI 再提示输入 API key。
3. infrastructure 校验 URL 非空、协议为 `http` 或 `https`，并规范化尾部斜杠。
4. 用自定义 base URL 和 API key 生成 `ResolvedProviderConfig`。
5. 可选调用 `GET {base_url}/models` 获取模型列表。

建议为 CLI 预留的应用层接口：

```python
@dataclass(frozen=True)
class ConnectOption:
    provider_id: str
    display_name: str
    requires_base_url: bool = False


@dataclass(frozen=True)
class ConnectRequest:
    provider_id: str
    api_key: str
    base_url: str | None = None


class ProviderConnectionService:
    def list_connect_options(self) -> tuple[ConnectOption, ...]:
        ...

    def connect(self, request: ConnectRequest) -> ResolvedProviderConfig:
        ...
```

`ProviderConnectionService` 放在 `infrastructure/providers/connection.py`。它只负责提供 provider 选项列表，为未来 CLI `/connect` 做准备。connection service 不读取 `.env`，不写 `.env`，不接受 API key，不创建 provider client。

### 13. Client Factory

新增 `infrastructure/providers/factory.py`。

factory 只接受 `.env` 路径，调用 config loader 后创建 model client 或 model catalog client。factory 可以接受测试 transport 注入，但不能接受裸 API key、裸 model 配置或系统环境变量。该 factory 可由未来 CLI/UI 调用，但 core 不直接调用。

### 14. 错误类型

扩展 `services/model/types.py`，增加 provider-neutral 错误：

```python
class ProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        provider_id: str | None = None,
        status_code: int | None = None,
        error_type: str | None = None,
        retryable: bool = False,
    ) -> None:
        ...
```

错误分类：

- `authentication_error`
- `rate_limit_error`
- `server_error`
- `network_error`
- `invalid_response`
- `invalid_tool_arguments`
- `configuration_error`

主循环现阶段可以让这些异常冒泡到调用方；后续错误恢复计划再把它们映射为 transition，例如 `rate_limit_retry`。

### 15. 依赖方向

允许：

- `infrastructure/providers/*` import `services/model/types.py`
- `infrastructure/providers/*` import `services/tools/types.py`
- `infrastructure/providers/*` import `services/context/snapshot.py`
- `infrastructure/config/*` 被 provider 创建代码调用

禁止：

- `core/loop.py` import `infrastructure/providers/chat_completions.py`
- `services/model/client.py` import 具体 provider
- provider import `core/*`
- provider import 具体 `tools/*`

## Provider 目录结构

provider 基础设施应按职责拆分在 `infrastructure/` 下：

- `infrastructure/config/env.py` 负责 `.env` 解析、字段校验、默认值合并和 `ResolvedProviderConfig` 输出。
- `infrastructure/providers/catalog.py` 负责内置 provider catalog，包括 OpenAI、DeepSeek、GLM、MiniMax、SiliconFlow、Gemini、Claude OpenAI-compatible gateway 和 custom。
- `infrastructure/providers/http.py` 负责最小 JSON HTTP transport、HTTP 状态码到 provider-neutral error 的映射、网络错误和超时错误归一化。
- `infrastructure/providers/chat_completions.py` 负责 Chat Completions payload 构造、tool schema 传递、assistant message 保留、tool call 参数解析、usage 归一化和 response error 映射。
- `infrastructure/providers/model_catalog.py` 负责通过同一配置访问 `/models` 并输出 provider-neutral model 列表。
- `infrastructure/providers/factory.py` 负责从 `.env` 路径创建 model client 或 model catalog client；它不接受裸 API key。
- `infrastructure/providers/connection.py` 负责未来 CLI `/connect` 所需的 provider 选项列表；它不读取密钥、不写配置、不创建 client。

依赖方向必须保持为 runtime 依赖 provider-neutral model client，provider adapter 依赖 `services/model/types.py` 和 `services/context/snapshot.py`，但 `core/loop.py` 不直接理解 OpenAI-specific 字段。

## 实施步骤

1. 建立 uv 运行环境
   - 确认 `pyproject.toml` 包含 `python-dotenv` 作为 runtime dependency 和 `pytest` 作为 dev dependency。
   - 运行 `uv sync --dev` 同步 `.venv` 和 `uv.lock`。
   - 创建 `.env.example` 模板，包含所有 provider 必填和可选变量示例。
   - 确认 `.env` 已在 `.gitignore` 中。

2. 增加 provider 配置类型
   - 创建 `infrastructure/providers/catalog.py`。
   - 创建 `infrastructure/config/env.py`。
   - 定义 `ProviderDefinition`、`ModelProviderConfig`、`ResolvedProviderConfig`。
   - 提供内置 provider catalog 和自定义 provider 解析入口。
   - 内置 provider 覆盖 openai、deepseek、glm、minimax、siliconflow、gemini、claude-openai-compatible 和 custom。
   - `infrastructure/config/env.py` 使用 `python-dotenv` 读取 `.env`，关闭 interpolation，输出 `ResolvedProviderConfig`。
   - `ResolvedProviderConfig.api_key` 使用 `repr=False`。

3. 增加 provider-neutral 错误类型
   - 扩展 `services/model/types.py`。
   - 增加 `ProviderError` 和错误分类字段。
   - 不改变现有 `LLMResponse` 行为。

4. 增加 HTTP transport
   - 使用标准库实现 `UrllibHttpTransport`。
   - 提供 `HttpTransport` 协议。
   - 统一处理 JSON 编码、JSON 解码、HTTP status、timeout 和网络错误。

5. 实现 Chat Completions client
   - 创建 `infrastructure/providers/chat_completions.py`。
   - 实现 `OpenAICompatibleChatCompletionsClient.send(snapshot)`。
   - 构造请求 payload。
   - 解析 assistant message、tool calls、usage 和 stop reason。
   - 对无效响应抛出 `ProviderError`。

6. 实现模型列表发现
   - 创建 `infrastructure/providers/model_catalog.py`。
   - 实现 `list_models()`。
   - 解析 `GET /models` 的 OpenAI 兼容响应。
   - 支持 provider 未配置 API key 时返回配置错误。

7. 增加 client factory
   - 创建 `infrastructure/providers/factory.py`。
   - 只接受 `.env` 路径，调用 config loader 后创建 model client 或 model catalog client。
   - 可以接受测试 transport 注入，但不接受裸 API key、裸 model 配置或系统环境变量。
   - 该 factory 可由未来 CLI/UI 调用，但 core 不直接调用。

8. 预留 `/connect` 连接服务
   - 创建 `infrastructure/providers/connection.py`。
   - 提供 `list_connect_options()`。
   - connection service 不读取 `.env`，不写 `.env`，不接受 API key，不创建 provider client。
   - 不在本计划实现终端渲染，但接口应直接满足 CLI `/connect` 调用。

9. 接入主循环
   - `AgentLoop` 应继续通过构造函数接收 model client，保持依赖注入。
   - provider factory 创建出的真实 client 应能直接注入 `AgentLoop`。
   - 对主循环而言，fake model client、测试 client 和真实 OpenAI-compatible client 的行为边界应一致。
   - 工具调用轮仍由 `LLMResponse.tool_calls` 驱动，provider adapter 负责把外部 tool call 形状归一化为内部 `ToolCall`。

10. 增加测试
    - 使用 fake transport 覆盖请求构造和响应解析。
    - 不访问真实服务商。
    - 不需要真实 API key。

## 测试计划

单元测试：

- `test_uv_sync_installs_dependencies`
  - `pyproject.toml` 能通过 `uv sync --dev` 安装运行时和测试依赖。
  - 常规测试入口为 `uv run python -m pytest tests -q`。

- `test_env_example_contains_all_fields`
  - `.env.example` 保留所有 provider 必需字段和可选字段示例，且不包含真实密钥。

- `test_env_parses_provider_config`
  - `.env` 解析 provider、model、base URL、API key、timeout、headers 和默认参数。
  - 缺少 `.env`、缺少 `ONECODE_API_KEY`、custom 缺少 `ONECODE_BASE_URL` 时返回 configuration error。
  - JSON 配置字段必须是 object。
  - dotenv interpolation 保持关闭。

- `test_resolved_config_repr_hides_api_key`
  - `ResolvedProviderConfig` 的 repr 不泄露 API key。

- `test_catalog_contains_builtin_providers`
  - openai、deepseek、glm、minimax、siliconflow、gemini、claude-openai-compatible 和 custom 存在。
  - 每个 provider 有稳定 id 和 base URL。
  - 内置 provider catalog 为 custom gateway 标记必须提供 base URL。

- `test_resolve_provider_config_uses_custom_base_url`
  - 自定义 base URL 覆盖内置 base URL。
  - 自定义 api key env 覆盖内置 env 名。

- `test_chat_completions_payload_includes_messages_and_tools`
  - system prompt 非空时注入 system message。
  - tool schemas 非空时传入 `tools`。
  - `default_params` 被合并。

- `test_chat_completions_omits_empty_tools`
  - tool schemas 为空时 payload 不包含 `tools`。

- `test_chat_completions_parses_text_response`
  - 字符串 content 转为 `final_text`。
  - usage 映射到 `ModelUsage`。

- `test_chat_completions_parses_tool_calls`
  - OpenAI 兼容 tool call 转为内部 `ToolCall`。
  - arguments JSON 字符串转为 dict。
  - 有 tool calls 时 `LLMResponse.tool_calls` 非空。

- `test_chat_completions_rejects_invalid_tool_arguments`
  - arguments 非 JSON 或非对象时抛出 `ProviderError(error_type="invalid_tool_arguments")`。

- `test_list_models_parses_openai_compatible_response`
  - `GET /models` 返回的 `data[].id` 转为 `ProviderModel`。
  - 结果按 id 排序。

- `test_connect_options_are_derived_from_catalog`
  - `/connect` 可展示的选项来自 provider catalog。
  - custom 选项标记为需要 base URL。

- `test_connect_builtin_provider_requires_only_api_key`
  - 选择内置 provider 时，`ConnectRequest` 不需要 base URL。
  - 解析结果使用内置 base URL 和输入 API key。

- `test_connect_custom_provider_requires_url_then_api_key`
  - custom 缺少 base URL 时返回 configuration error。
  - custom 提供 base URL 和 API key 后生成 resolved config。

- `test_http_errors_are_provider_errors`
  - 401 映射为 authentication error。
  - 429 映射为 rate limit 且 retryable。
  - 5xx 映射为 server error 且 retryable。

集成边界测试：

- `core/loop.py` 不 import `infrastructure/providers/*`。
- 使用 fake HTTP transport 的真实 provider client 可以注入 `AgentLoop` 并返回 final text。
- 使用 fake HTTP transport 的真实 provider client 可以注入 `AgentLoop` 并驱动 tool call 续轮。
- factory 通过临时 `.env` 创建 provider client 并可注入 `AgentLoop`。

## 验收标准

- 不引入任何厂商 SDK。
- 所有 provider 访问都通过 OpenAI 兼容 HTTP JSON 接口。
- `pyproject.toml` 包含 `python-dotenv` 作为 runtime dependency，`pytest` 作为 dev dependency，`uv sync --dev` 可正常同步环境。
- `.env.example` 作为 provider 配置模板提交到仓库，`.env` 被 `.gitignore` 忽略。
- 模型 provider 运行时配置只从 `.env` 读取，不读取 `os.environ`，不读取 JSON/TOML 项目配置。
- `python-dotenv` 读取时关闭 interpolation。
- `ResolvedProviderConfig.api_key` 使用 `repr=False`，不在常规打印中泄露密钥。
- 内置 provider catalog 可列出常见 provider 的默认服务地址。
- 默认 catalog 包含 OpenAI、DeepSeek、GLM/BigModel、MiniMax、硅基流动、Gemini、Claude OpenAI 兼容网关和 custom。
- 自定义 base URL 可覆盖内置服务地址。
- `/connect` 的候选服务商列表来自 provider catalog。
- `/connect` 选择内置服务商时只需要输入 API key。
- `/connect` 选择 custom 时先输入 base URL，再输入 API key。
- Chat Completions provider 可作为 `ModelClient` 注入现有主循环。
- provider 响应被归一化为 `LLMResponse`、`ToolCall`、`ModelUsage`。
- 可以通过 `GET /models` 获取模型列表，并返回 UI 可消费的结构化结果。
- 测试不依赖真实网络和真实 API key。
- provider-specific 字段不泄露到 `core/loop.py`。
- factory、connection service 和测试 helper 都不新增绕过 `.env` 的密钥传入路径。

## 风险与注意事项

- 并非所有服务商的"OpenAI 兼容"细节完全一致，尤其是 tool calls、usage details、错误响应格式和空 `tools` 行为。实现应保守解析，缺失可选字段时降级，缺失关键字段时报 `ProviderError`。
- Claude 原生 API 不等于 OpenAI Chat Completions 协议；如果用户使用 Claude，需要接入 OpenAI 兼容网关或自定义 base URL。本计划不假装原生 Anthropic endpoint 可以直接兼容。
- Gemini 的 OpenAI 兼容 endpoint 与 Google 原生 Gemini endpoint 不同，catalog 应使用 `/v1beta/openai` 兼容入口。
- API key 不应写入 trace、异常字符串或测试 fixture 输出。
- 模型列表接口可能被某些兼容服务关闭或返回非标准格式；UI 后续应能显示 provider configured 但 model discovery failed 的状态。
- `default_params` 需要谨慎透传，避免把不被某些 provider 支持的字段默认发出。初版只应由用户配置显式提供。