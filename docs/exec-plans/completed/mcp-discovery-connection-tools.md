# MCP 发现、连接与工具调用集成

本文是一个活文档，后续实现过程中必须持续更新 `Progress`、`Surprises & Discoveries`、`Decision Log` 和 `Outcomes & Retrospective`。本计划遵守仓库根目录的 `PLANS.md`，并且必须保持自包含：只阅读本文件和当前工作树的新人，也应该能完成该功能。

## Purpose / Big Picture

完成本计划后，OneCode 可以在启动时读取项目级 `.mcp.json`，批量连接 enabled MCP server，并把远端 MCP tools 动态注册成 OneCode 可见工具。用户可以配置一个本地 stdio MCP server 或远程 SSE / Streamable HTTP MCP server，然后在普通对话里让模型调用形如 `mcp__server__tool` 的工具。第一版只实现 `stdio`、`sse` 和 `http` 三种 transport；不实现 `type: "sdk"` 进程内 server、不实现 OAuth、不实现自写 MCP 协议栈。

MCP 是 Model Context Protocol 的缩写。它是一个让外部进程或远程服务向 agent 暴露工具、资源和提示词的协议。本计划第一版只把 MCP 的 tools 接入 OneCode：连接后调用 `tools/list` 发现工具，模型调用时再走 `tools/call`。`resources/list`、`resources/read`、`prompts/list` 只作为后续扩展方向记录，不在第一版交付。

用户可见的验证方式是：在项目根目录放一个 `.mcp.json`，启动 CLI 后运行 `/mcp` 能看到 server 状态和发现到的工具；运行 `/tools` 能看到 `mcp__...` 工具；让模型调用 MCP 工具后，CLI 展示普通 tool result summary，模型收到 MCP 返回内容并继续回答。

## Progress

- [x] (2026-06-07 18:30+08:00) 读取 `AGENTS.md`、`architecture.md`、`docs/references/s19_mcp_plugin/`、工具运行时文档和当前代码，确认 MCP 应作为动态工具来源接入 `ToolRegistry`，而不是进入 `core/loop.py`。
- [x] (2026-06-07 18:40+08:00) 与用户确认第一版范围：实现配置发现和能力发现；启动时批量连接 enabled server；只做 `stdio`、`sse`、`http`；不实现 `type: "sdk"`、OAuth 或自写协议栈；允许静态 headers；MCP 工具权限按 readOnly/destructive/unknown 分类。
- [x] (2026-06-07 18:55+08:00) 撰写本中文 ExecPlan，明确实现边界、模块落点、验证方式和测试范围。
- [x] (2026-06-07 20:20+08:00) 新增 `services/mcp/` 配置、名称规范化、连接管理、工具包装和结果转换代码。当前实现包含 `config.py`、`names.py`、`manager.py`、`tool_factory.py`、`results.py`、`types.py` 和稳定 `__init__.py` 导出。
- [x] (2026-06-07 20:20+08:00) 将官方 Python MCP SDK 加入依赖，并用它处理 MCP JSON-RPC、stdio、SSE 和 Streamable HTTP transport。`pyproject.toml` 现在依赖 `mcp>=1.27.2`，`uv.lock` 已更新。
- [x] (2026-06-07 20:20+08:00) 在 CLI runtime 构建时加载项目 `.mcp.json`、批量连接 enabled server、注册 MCP 工具，并把 MCP manager 和 connected server instructions 放入 runtime/state。连接失败 server 会进入 `/mcp` failed 状态，不阻止 CLI 启动。
- [x] (2026-06-07 20:20+08:00) 为 CLI 增加 `/mcp` 状态命令，并扩展 prompt/schema 动态可见性测试。`/mcp tools` 可展示 provider-visible descriptor 到原始 server/tool 的映射，system prompt 只注入 connected server instructions。
- [x] (2026-06-07) 增加 fake stdio、fake SSE、fake HTTP MCP server 测试，覆盖连接、发现、调用、权限、断线重连和错误结果。真实 SDK stdio、SSE 和 Streamable HTTP fake server 端到端测试均已完成；重连专项测试覆盖第一次调用失败后断开并重连一次的路径。
- [x] (2026-06-07 20:20+08:00) 运行定向测试、compileall 和全量测试，并把实际输出摘要记录到本计划。

## Surprises & Discoveries

- Observation: 当前仓库没有 MCP 实现痕迹，`pyproject.toml` 也没有 MCP 依赖；MCP 需要作为新服务模块和 CLI 装配能力加入。
  Evidence: `rg -n "MCP|mcp|Model Context Protocol" services tools infrastructure core ui prompts tests pyproject.toml` 只命中文档和通用 `mcp_meta` 说明，没有运行时代码。
- Observation: OneCode 现有工具运行时已经适合承载 MCP 工具，不需要改主循环。`ToolDescriptor`、`ToolRegistry.visible_descriptors()`、`RegistryToolExecutor`、`PermissionPolicy` 和 `ToolResultPolicy` 已提供 schema、prompt、执行、权限和大结果预算边界。
  Evidence: `services/tools/types.py` 定义 descriptor、classification、target 和 result policy；`services/tools/registry.py` 统一生成 prompt 与 provider schema；`services/tools/executor.py` 已支持 async handler、permission ask 和结果持久化。
- Observation: MCP 当前标准 transport 是 stdio 和 Streamable HTTP；旧 HTTP+SSE 是兼容路径。第一版仍实现用户要求的 `sse`，但应把 `http` 映射为 Streamable HTTP。
  Evidence: 官方 MCP transport 规格 2025-06-18 说明 stdio 通过子进程 stdin/stdout 传 newline-delimited JSON-RPC，Streamable HTTP 使用单个 HTTP endpoint，SSE 是旧版兼容 transport。
- Observation: 当前安装的 Python SDK API 与计划中的名称一致，`mcp==1.27.2` 可导入 `ClientSession`、`StdioServerParameters`、`stdio_client`、`sse_client` 和 `streamablehttp_client`。
  Evidence: `uv run python -c "import mcp; from mcp import ClientSession, StdioServerParameters; from mcp.client.stdio import stdio_client; from mcp.client.sse import sse_client; from mcp.client.streamable_http import streamablehttp_client; print('ok')"` 输出 `ok`。
- Observation: SDK 的 stdio transport 会把 `errlog` 作为真实 subprocess stderr 文件描述符使用，纯内存 `write()/flush()` sink 不够。
  Evidence: 初始 `tests/test_mcp_manager.py::test_mcp_connection_manager_discovers_and_calls_stdio_tools` 失败，状态为 `AttributeError: '_LimitedTextLog' object has no attribute 'fileno'`；改为带 `fileno()` 的临时文件 sink 后该测试通过。
- Observation: Python MCP SDK 1.27.2 同时提供旧别名 `streamablehttp_client` 和新接口 `streamable_http_client`；旧别名支持直接传 `headers`、`timeout` 和 `sse_read_timeout`，但会触发弃用警告；新接口要求调用方传入预配置的 `httpx.AsyncClient` 来承载 headers 和 timeout。
  Evidence: `uv run python -c "import inspect; from mcp.client.streamable_http import streamable_http_client, streamablehttp_client; print(inspect.signature(streamable_http_client)); print(inspect.signature(streamablehttp_client))"` 显示新接口签名为 `(url, *, http_client=None, terminate_on_close=True)`，旧别名签名包含 `headers` 和 `timeout`。改用新接口并通过 `AsyncExitStack` 管理 `httpx.AsyncClient` 后，`tests/test_mcp_manager.py` 无警告通过。

## Decision Log

- Decision: 第一版使用官方 Python MCP SDK 的 client APIs，而不是自己实现 JSON-RPC、transport framing、初始化握手和 tool result 类型。
  Rationale: 用户只排除了 `type: "sdk"` 进程内 MCP server，没有排除客户端依赖。官方 Python SDK 已支持 stdio、SSE 和 Streamable HTTP，并能显著减少协议细节风险。OneCode 仍然不暴露 `sdk` transport 配置。
  Date/Author: 2026-06-07 / Codex

- Decision: 项目级 MCP server 配置只从项目根目录 `.mcp.json` 读取；不读取用户级配置，不读取 claude.ai connectors，不读取插件配置。
  Rationale: 用户确认“项目级，不需要用户级配置”。`.mcp.json` 是 MCP 生态常见文件名，适合作为 server config 来源；`.onecode/settings.json` 继续只承担 OneCode 项目权限规则，避免把配置来源混在一起。
  Date/Author: 2026-06-07 / Codex

- Decision: 第一版只交付 MCP tools，不交付 MCP resources 或 prompts。
  Rationale: 用户接受这个范围。OneCode 当前已经有成熟工具 registry 与 executor；resources 需要新增 `list_mcp_resources`/`read_mcp_resource` 或 context attachment 语义，prompts 需要 slash command 或 prompt catalog 语义，放入第一版会扩大边界。
  Date/Author: 2026-06-07 / Codex

- Decision: MCP 工具包装成普通 `ToolDescriptor`，名称使用 `mcp__{normalized_server}__{normalized_tool}`。
  Rationale: 这与参考材料 `docs/references/s19_mcp_plugin/README.en.md` 和 Claude Code 行为一致，可以避免 server 之间 tool name 冲突。通过普通 descriptor 接入后，schema、prompt、executor、hook、permission 和 result store 都能复用现有 OneCode 管线。
  Date/Author: 2026-06-07 / Codex

- Decision: MCP 工具权限使用 `ToolTarget(kind="external_service", operation="call", value="{server}/{tool}")`，readOnly 工具默认 allow，destructive 或未知副作用工具默认 ask。
  Rationale: MCP 工具通常不触达本地文件系统，不能用 path guard 判断安全性；但外部服务调用仍可能产生副作用。用 external_service target 可以让项目规则和 session grant 匹配具体 server/tool，同时保持 deny-first 权限模型。
  Date/Author: 2026-06-07 / Codex

- Decision: stdio 进程生命周期第一版做到启动、连接超时、stderr 限量收集、关闭清理和调用时断线重连一次，不做复杂健康监控或 SIGINT/SIGTERM/SIGKILL 阶梯。
  Rationale: 用户接受这个收敛范围。第一版应先证明 discovery 和 tool invocation 能工作；精细化进程终止和长连接恢复可在可观测行为稳定后追加。
  Date/Author: 2026-06-07 / Codex

- Decision: stdio stderr 使用临时文件承载并在读取摘要时限制为 1MB，而不是使用无文件描述符的纯内存 text sink。
  Rationale: Python MCP SDK 的 stdio client 需要把 stderr sink 传给 subprocess，subprocess API 要求 `fileno()`。临时文件能满足 SDK 要求，同时避免把 stderr 直接写到用户终端；读取时限制为 1MB 保持内存风险可控。
  Date/Author: 2026-06-07 / Codex

- Decision: Streamable HTTP transport 使用 SDK 推荐的 `streamable_http_client`，并由 OneCode 创建、配置和关闭 `httpx.AsyncClient`，而不是使用已弃用的 `streamablehttp_client` alias。
  Rationale: 新接口避免 SDK 弃用警告，同时仍支持第一版要求的静态 headers 和 timeout。`httpx.AsyncClient` 被注册到 MCP server connection 的 `AsyncExitStack`，连接关闭时会同步释放。
  Date/Author: 2026-06-07 / Codex

## Outcomes & Retrospective

第一版实现已经完成并通过全量测试。已交付项目级 `.mcp.json` 解析、官方 SDK 连接管理、stdio/SSE/Streamable HTTP transport 接入、MCP tools 到 `ToolDescriptor` 的动态包装、CLI `/mcp` 状态命令、MCP server instructions prompt section、external service 权限 ask 规则，以及配置/命名/结果/权限/CLI/prompt/stdio/SSE/Streamable HTTP/重连测试。剩余非第一版方向是 resources/prompts、OAuth、用户级配置和更细的 stderr/error 展示策略。

## Context and Orientation

OneCode 是一个 Python code agent runtime。主循环在 `core/loop.py`，它只负责编排对话、模型调用和工具调用，不能 import 具体工具或 MCP 特例。每轮模型调用前，`core/context_engine.py` 重建 `ContextSnapshot`，其中 tool schema 来自注入的 tool schema provider。CLI 装配入口是 `ui/cli/app.py::build_runtime()`，这里创建 `RuntimeState`、`MessageStore`、`PermissionPolicy`、`ToolRegistry`、`DynamicPromptAssembler`、`RegistryToolExecutor` 和 `AgentLoop`。

工具运行时位于 `services/tools/`。`services/tools/types.py` 中的 `ToolDescriptor` 是工具事实来源，包含工具名、描述、输入 schema、prompt、输入校验、分类函数和 handler。`ToolRegistry` 管理 descriptor 集合，并通过 `visible_descriptors(state)` 生成同一组可见工具给 prompt 和 provider schema。`RegistryToolExecutor` 执行工具调用，顺序是 lookup、schema validation、tool validation、classification、guard、permission、hook、handler、result policy 和 trace。

权限系统位于 `services/permissions/`。`PermissionPolicy.evaluate()` 是 deny-first：read-only subagent 限制、工具 deny/disabled、guard deny、项目 deny 都先于 ask 和 allow。项目权限存放在 `.onecode/settings.json`。本计划新增的 MCP 工具必须继续经过该权限入口，不能在 MCP handler 内自行绕开。

prompt 组装在 `prompts/assembler.py` 和 `prompts/sections.py`。`DynamicPromptAssembler` 会读取 registry 的可见 descriptor，并把每个 descriptor 的 `prompt` 拼入 system prompt。MCP server instructions 是 server 在初始化后提供的自然语言说明，第一版可以通过 MCP descriptor prompt 或新增 prompt section 注入模型，但必须保证被 denied/disabled 的 MCP server 或工具不会暴露不可用能力。

MCP server 配置第一版只从项目根目录 `.mcp.json` 读取。示例配置如下，实际文件不要求存在；不存在时 OneCode 正常启动且没有 MCP 工具。

    {
      "mcpServers": {
        "docs": {
          "type": "stdio",
          "command": "uv",
          "args": ["run", "python", "examples/mcp_docs_server.py"],
          "env": {"DOCS_ROOT": "docs"}
        },
        "search": {
          "type": "http",
          "url": "https://example.com/mcp",
          "headers": {"Authorization": "Bearer ${TOKEN_VALUE_ALREADY_EXPANDED_BY_USER}"}
        },
        "legacy": {
          "type": "sse",
          "url": "https://example.com/sse",
          "headers": {"X-Api-Key": "static-key"}
        }
      }
    }

第一版不做 shell-style 环境变量展开。`headers` 和 `env` 的值按 JSON 字面值使用。若项目需要 secret，应该通过未提交的 `.mcp.json` 或后续专门的 env expansion 计划处理。配置字段 `enabled` 可以作为可选布尔值加入；缺省为 enabled，`enabled: false` 的 server 只出现在 `/mcp` 状态里，不连接、不注册工具。

官方 Python MCP SDK 的包名是 `mcp`。它提供 `ClientSession`、`StdioServerParameters`、`mcp.client.stdio.stdio_client`、`mcp.client.sse.sse_client` 和 `mcp.client.streamable_http.streamablehttp_client` 或同等 API。实现时必须以当前安装版本的实际 API 为准；如果 API 名称与本计划略有差异，应在本计划的 `Surprises & Discoveries` 和 `Decision Log` 记录实际发现。

## Plan of Work

第一步是加入依赖并建立 MCP 服务模块。编辑 `pyproject.toml`，把 `mcp` 加入 `[project].dependencies`。通过 `uv add mcp` 修改依赖文件；如果网络或索引访问失败，按当前环境规则申请提升权限后重试。新增目录 `services/mcp/`，至少包含 `__init__.py`、`types.py`、`config.py`、`names.py`、`manager.py`、`tool_factory.py` 和 `results.py`。

`services/mcp/types.py` 定义 OneCode 内部 MCP 类型。需要有 `McpServerConfig` 或等价 dataclass，表达三种 config：stdio 需要 `command: str`、`args: tuple[str, ...]`、`env: dict[str, str]`、可选 `enabled: bool`；sse/http 需要 `url: str`、`headers: dict[str, str]`、可选 `enabled: bool`。还需要 `McpServerStatus`，表达 `connected`、`failed`、`disabled`、`pending`；`McpDiscoveredTool` 保存 server 原名、normalized server 名、tool 原名、normalized tool 名、provider-visible descriptor name、description、input schema 和 annotations。不要把 SDK 对象直接暴露到 CLI renderer；renderer 应消费稳定 dataclass 或 dict。

`services/mcp/config.py` 读取项目根 `.mcp.json`。函数建议命名为 `load_project_mcp_config(workspace: Path) -> McpConfigSet`。如果文件不存在，返回空配置；如果 JSON 语法错误或 shape 错误，抛出清晰的配置错误，让 CLI 启动失败并显示错误。只接受 `mcpServers` object。`type` 缺省为 `stdio`，合法值只有 `stdio`、`sse`、`http`。遇到 `sdk`、`ws`、`oauth`、`headersHelper` 或未知字段，应返回配置错误，错误文本要说明第一版不支持该能力。静态 headers 允许，但不做 OAuth 或 token refresh。

`services/mcp/names.py` 实现名称规范化。函数建议为 `normalize_mcp_name(name: str) -> str`，规则是把所有非 `[a-zA-Z0-9_-]` 字符替换为 `_`，然后限制 provider-visible name 长度。由于 OpenAI-compatible tool/function name 常见限制是最多 64 字符，最终 `mcp__server__tool` 应控制在 64 字符内。若 server 和 tool 名太长，应使用稳定短 hash 后缀，而不是直接截断造成碰撞。函数还要维护 `original_name` 映射，便于 handler 调用真实远端 tool 名。

`services/mcp/manager.py` 是连接管理器。类建议为 `McpConnectionManager`，构造参数包含 `workspace: Path`、`configs: Mapping[str, McpServerConfig]`、`timeout_seconds: float = 30.0`、`max_stdio_concurrency: int = 3`、`max_remote_concurrency: int = 20` 和 `trace_recorder: TraceRecorder | None`。方法建议包括 `async connect_all() -> McpConnectionSnapshot`、`async ensure_connected(server_name: str) -> ConnectedMcpServer`、`async call_tool(server_name: str, tool_name: str, arguments: dict[str, Any], tool_call_id: str) -> McpToolCallResult` 和 `async close_all() -> None`。

连接时，disabled server 不连接，状态为 disabled。enabled server 按 local 和 remote 分组并发连接：stdio 并发 3，sse/http 并发 20。stdio 使用 SDK 的 `stdio_client` 和 `ClientSession.initialize()`，子进程环境为 `os.environ` 加上 config.env，cwd 使用 workspace。sse 使用 SDK 的 legacy SSE client 并传静态 headers。http 使用 SDK 的 Streamable HTTP client 并传静态 headers。每个 server 连接成功后调用 `list_tools()`，保存工具列表和 server capabilities；如果 SDK 提供 server instructions，则截断到 2048 字符保存。连接失败不应使整个 CLI 启动失败，除非配置文件本身无效；失败 server 状态为 failed，`/mcp` 展示错误。

连接生命周期要简单可靠。manager 应保留连接对象和 async context manager exit stack，CLI 退出时调用 `close_all()`。如果工具调用时连接已断开，`ensure_connected()` 尝试重新连接一次，并重新拉取该 server 的 tools；若仍失败，handler 返回 `ToolExecutionResult(is_error=True)`，metadata error 为 `mcp_connection_failed` 或 `mcp_tool_call_failed`。stdio stderr 最多保留 64MB 或更小固定上限；第一版可以用 1MB 上限降低内存风险，只要在计划更新中记录实际选择。

`services/mcp/tool_factory.py` 把远端 tools 转换为 OneCode `ToolDescriptor`。函数建议为 `build_mcp_tool_descriptors(manager: McpConnectionManager) -> tuple[ToolDescriptor, ...]`。每个 descriptor 的 name 是 `mcp__{normalized_server}__{normalized_tool}`；description 使用远端 description，截断到 2048 字符，并在必要时附加 “MCP server: {server}”。input_schema 直接使用远端 `inputSchema`，如果缺失则使用 object 空 schema。handler 是 async function，调用 `manager.call_tool()`，传入真实 server 原名和 tool 原名。prompt 只放简短规则，不重复长 description；server instructions 通过单独 prompt section 或 runtime metadata 注入。

MCP 工具分类函数必须 input-aware 但保守。读取 MCP annotations：如果 `readOnlyHint` 为 true 且 `destructiveHint` 不为 true，则 `read_only=True`、`modifies_filesystem=False`、`concurrency_safe=True`。如果 `destructiveHint` 为 true 或 annotations 缺失，则 `read_only=False`、`modifies_filesystem=False`、`concurrency_safe=False`。所有 MCP 工具都产生一个 `ToolTarget(kind="external_service", operation="call", value="{server}/{tool}", metadata={"server": server, "tool": tool})`。`permission_subject` 使用相同的 `{server}/{tool}`。result policy 使用 `ToolResultPolicy(max_result_size_chars=50_000, persist_when_exceeded=True, preview_chars=4_000)`。

`services/mcp/results.py` 把 MCP tool result 转成模型可见文本。MCP result 通常包含 content blocks，第一版需要支持 text、image、resource、resource_link 和未知 block。text block 直接拼接；image block 第一版不要尝试压缩或传给 provider，转成说明文字，包含 mime type 和字节大小；resource text 可作为文本拼接，resource binary 转成说明；resource_link 转成 URI 文本。若 SDK 返回 `isError`，handler 应返回 `ToolExecutionResult(is_error=True, metadata={"error": "mcp_tool_error", ...})`。若 SDK 返回 structured content 或 `_meta`，放入 metadata 的 `mcp_meta` 或 `structured_content`，但不要把未格式化内部 metadata 泄露给模型。

权限系统需要最小扩展。编辑 `services/permissions/policy.py::_ask_reasons()`，增加对 MCP external service target 的判断：如果 target.kind 是 `external_service` 且 operation 是 `call`，并且 classification 不是 read-only，则添加 “MCP tool may change external service state or has unknown side effects.”。这样 destructive 或未知 MCP 工具会 ask；readOnly MCP 工具无项目 ask/deny 时默认 allow。项目 `.onecode/settings.json` 中已有 allow/deny/ask 规则可以匹配 descriptor name，例如 `mcp__github__create_issue`，也可以通过内容规则匹配 target value，例如 `mcp__github__create_issue(github/create_issue)`。

CLI 需要保存和展示 MCP 状态。编辑 `ui/cli/types.py::CliRuntime`，增加 `mcp_manager: McpConnectionManager | None = None` 或稳定状态对象字段。编辑 `ui/cli/app.py::build_runtime()`，在创建 base descriptors 和 registry 之前读取 `.mcp.json` 并连接 MCP。由于 `build_runtime()` 当前是同步函数，而 MCP 连接是 async，建议采用一个同步 wrapper，比如 `McpConnectionManager.connect_all_blocking()`，内部用 `asyncio.run()`；如果未来 CLI build 变成 async，再迁移。连接后把 MCP descriptors 加入 `base_descriptors` 或在 registry 构造后注册。subagent runner 的 `base_descriptors` 也应包含 MCP descriptors，使 subagent 继承父 MCP 配置；read-only subagent 会通过 permission policy 阻断非 read-only MCP 工具。

编辑 `ui/cli/commands.py` 增加 `/mcp` 命令。无参数时显示所有 server 状态、transport、工具数量、错误摘要和 instructions 是否存在。可以支持 `/mcp tools` 展示每个 MCP descriptor name 到原始 server/tool 的映射，但不是第一版必须。编辑 `ui/cli/renderer.py` 增加 `render_mcp_status(...)`。编辑 `renderer.render_help()`，加入 `/mcp` 说明。

prompt 中加入 MCP server instructions。推荐新增 `prompts/runtime_context.py` 字段或使用 `RuntimeState.metadata["mcp_server_instructions"]`，再在 `prompts/sections.py` 新增一个非空时渲染的 section，标题为 `MCP Server Instructions`。内容只包含 connected server 的 instructions，按 server 名稳定排序，每个 server 截断 2048 字符。若一个 server failed 或 disabled，不注入 instructions。不要把 static headers、env 或错误堆栈写入 prompt。

trace 与可观测性要覆盖关键节点。manager 连接每个 server 时记录 `mcp_connect` event/span，包含 server name、transport、status、tool_count 和 error type，不包含 headers/env secret。工具调用时记录 `mcp_tool_call` event/span，包含 server、tool、descriptor name、is_error 和 content size。若 trace sanitizer 已有统一入口，应通过 sanitizer 或手动白名单避免泄露 headers。

最后补测试。测试应尽量不依赖外部网络。stdio 测试可以创建一个最小 Python MCP server fixture；HTTP/SSE 测试可以使用 SDK 的 test server、Starlette app 或轻量 fake server。如果 SDK server fixture 太重，先用 mock SDK transport 验证 OneCode manager 与 tool_factory，再用一个端到端 stdio server 证明真实协议路径。

## Concrete Steps

从仓库根目录执行依赖添加：

    cd D:\study\OneCode
    uv add mcp

预期 `pyproject.toml` dependencies 出现 `mcp`，锁文件如存在也会更新。若网络失败，按执行环境规则申请提升权限后重试。

创建 MCP 服务模块：

    services/mcp/__init__.py
    services/mcp/types.py
    services/mcp/config.py
    services/mcp/names.py
    services/mcp/manager.py
    services/mcp/tool_factory.py
    services/mcp/results.py

实现后运行配置与名称测试：

    uv run python -m pytest tests/test_mcp_config.py tests/test_mcp_names.py -q

预期新增测试通过。测试应覆盖：缺失 `.mcp.json` 返回空配置；合法 stdio/sse/http 配置被解析；`sdk`、`ws`、OAuth 字段和未知字段报错；名称规范化替换非法字符；长名称产生稳定 hash 且最终 descriptor name 不超过 provider 限制。

接入 manager 和 descriptor 后运行工具发现测试：

    uv run python -m pytest tests/test_mcp_manager.py tests/test_mcp_tool_factory.py -q

预期能连接 fake server，发现一个 read-only tool 和一个 destructive tool，并生成 `mcp__...` descriptor。read-only descriptor 分类为 allow-friendly、concurrency_safe；destructive 或 unknown descriptor 分类为 ask-friendly、非并发。

接入 CLI runtime 和 `/mcp` 后运行 CLI 与 registry 测试：

    uv run python -m pytest tests/test_cli_commands.py tests/test_dynamic_prompt_assembler.py tests/test_tool_registry_and_executor.py -q

预期 `/mcp` 能渲染 connected/failed/disabled；`/tools` 或 registry descriptors 包含 MCP 工具；MCP instructions 只在 connected server 存在 instructions 时进入 system prompt。

最终运行 compile 和全量测试：

    uv run python -m compileall core services infrastructure tools ui prompts
    uv run python -m pytest tests -q

预期 compileall 无语法错误，全量测试通过。若全量测试时间较长，至少在中间里程碑运行上述定向测试，并在本计划 `Progress` 记录未跑全量测试的原因。

手动验证可以创建一个项目级 `.mcp.json` 指向测试 stdio server，然后启动 CLI：

    uv run python -m ui.cli.app

在 CLI 中运行：

    /mcp
    /tools

预期 `/mcp` 显示 server 为 connected 且 tool_count 大于 0；`/tools` 显示 `mcp__...` 工具。随后输入一个自然语言请求，让模型调用 read-only MCP 工具；CLI 应显示 tool result summary，模型最终回答包含 MCP 返回信息。由于真实模型调用依赖 `.env` provider 配置，自动测试不应依赖该手动步骤。

## Validation and Acceptance

功能验收以可观察行为为准。没有 `.mcp.json` 时，OneCode 启动行为与当前一致，`/mcp` 显示没有配置的 MCP server，`/tools` 不出现 MCP 工具。有合法 `.mcp.json` 且 fake stdio server 可启动时，CLI 启动后 `/mcp` 显示 server connected，`/tools` 显示 prefixed MCP tool。禁用 server 配置为 `enabled: false` 时，`/mcp` 显示 disabled，registry 不包含该 server 的工具。

工具发现验收要求：server 返回 tool name `search.docs` 时，OneCode 注册名类似 `mcp__docs__search_docs`，并保留原始 tool name `search.docs` 用于调用远端。两个 server 暴露同名 tool 时，descriptor name 不冲突。名称过长时，最终 provider-visible name 不超过 64 字符，并且相同输入在多次运行中得到相同名称。

权限验收要求：MCP read-only tool 在没有 project ask/deny 时直接执行；destructive 或未知副作用 MCP tool 在有 CLI prompter 时弹出权限确认，在无 prompter 的 executor 测试中返回 `permission_ask_required`。项目 `.onecode/settings.json` 中 deny 具体 MCP descriptor 时，该工具不进入 prompt/schema；历史消息里强行调用该工具时 executor 返回 `permission_denied`。

调用验收要求：成功 MCP tool result 的 text content 被拼接为模型可见文本；MCP `isError` 或 SDK 异常被转换为结构化 tool error，而不是让 `core/loop.py` 崩溃。超大结果走现有 `ToolResultPolicy`，在注入 `ToolResultStore` 时写入 `.onecode/<session>/tool-results/` 并返回引用。

重连验收要求：如果 connected server 在工具调用前断开，下一次调用会尝试重新连接一次。重连成功时调用继续；重连失败时返回 `mcp_connection_failed` tool error，CLI 和 trace 能看到失败状态。

测试验收命令是：

    uv run python -m compileall core services infrastructure tools ui prompts
    uv run python -m pytest tests -q

完成时应在 `Artifacts and Notes` 记录简短输出，例如：

    N passed in X.XXs

## Idempotence and Recovery

本计划的代码改动应是可重复运行的。`.mcp.json` 缺失时返回空配置，不创建文件，不修改用户设置。`uv add mcp` 可以重复运行；如果依赖已经存在，uv 不应重复添加不同版本。测试创建的 fake server 和临时 `.mcp.json` 必须放在 pytest tmp_path 或临时 workspace 中，不污染真实项目根。

连接失败不能阻止 CLI 启动，除非 `.mcp.json` 本身不是合法 JSON 或配置 shape 不合法。这样用户可以通过 `/mcp` 看到失败原因，修复 server 命令或 URL 后重启。stdio 子进程必须在 CLI 退出或测试 teardown 时关闭；如果某个测试失败，fixture 应确保清理进程，避免后台子进程残留。

不要把 headers、env、Authorization token、API key 写入 prompt、trace、tool result 或错误摘要。错误里可以出现 server name、transport 和通用错误类型；完整异常文本若可能包含 secret，应先清洗或截断。

如果官方 Python MCP SDK API 与计划中名称不一致，实现者应先写一个小测试或 scratch script 验证实际 imports 和 session 调用方式，然后更新本计划 `Surprises & Discoveries` 与 `Decision Log`，再继续实现。不要为了绕过 API 差异而手写完整协议栈。

## Artifacts and Notes

参考资料已读：

    AGENTS.md
    architecture.md
    docs/design-docs/tools-runtime-architecture.md
    docs/design-docs/safety-and-extension-architecture.md
    docs/tech-debt/tech-debt-tracker.md
    docs/references/s19_mcp_plugin/README.en.md
    docs/references/s19_mcp_plugin/mcp/types.ts
    docs/references/s19_mcp_plugin/mcp/normalization.ts
    PLANS.md

外部官方资料用于确认当前 SDK 和 transport 事实：

    官方 Python SDK 文档说明 mcp package 可以构建 MCP client，并支持 stdio、SSE 和 Streamable HTTP transport。
    官方 MCP transport 规格 2025-06-18 说明 stdio 和 Streamable HTTP 是当前标准 transport，旧 HTTP+SSE 是兼容路径。

本计划没有实现代码。实现开始后，将关键测试输出、重要 diff 摘要和任何 SDK API 差异记录在本节。

已完成的关键测试输出：

    uv run python -m pytest tests\test_mcp_config.py tests\test_mcp_names.py tests\test_mcp_results.py tests\test_mcp_tool_factory.py tests\test_mcp_manager.py tests\test_dynamic_prompt_assembler.py tests\test_cli_commands.py tests\test_permission_policy.py tests\test_tool_registry_and_executor.py -q
    57 passed in 2.31s

    uv run python -m compileall core services infrastructure tools ui prompts
    compileall completed without syntax errors.

    uv run python -m pytest tests -q
    260 passed in 4.69s

追加验证输出：

    uv run python -m pytest tests\test_mcp_manager.py -q
    4 passed in 2.84s

    uv run python -m compileall core services infrastructure tools ui prompts
    compileall completed without syntax errors.

    uv run python -m pytest tests -q
    263 passed in 6.34s

## Interfaces and Dependencies

新增依赖：

    pyproject.toml
      dependencies 增加 "mcp"

新增服务包：

    services/mcp/__init__.py
      导出稳定入口：load_project_mcp_config、McpConnectionManager、build_mcp_tool_descriptors、normalize_mcp_name。

    services/mcp/config.py
      def load_project_mcp_config(workspace: Path) -> McpConfigSet
      读取 workspace / ".mcp.json"，返回 server 配置集合。不存在返回空集合；非法配置抛出 ValueError 或自定义 McpConfigError。

    services/mcp/names.py
      def normalize_mcp_name(name: str) -> str
      def build_mcp_tool_name(server_name: str, tool_name: str) -> McpToolName
      `McpToolName` 应包含 provider_name、normalized_server、normalized_tool、original_server、original_tool。

    services/mcp/manager.py
      class McpConnectionManager:
          async def connect_all(self) -> McpConnectionSnapshot
          def connect_all_blocking(self) -> McpConnectionSnapshot
          async def ensure_connected(self, server_name: str) -> ConnectedMcpServer
          async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any], tool_call_id: str) -> McpToolCallResult
          async def close_all(self) -> None

    services/mcp/tool_factory.py
      def build_mcp_tool_descriptors(manager: McpConnectionManager) -> tuple[ToolDescriptor, ...]
      只为 connected server 的 discovered tools 生成 descriptor。

    services/mcp/results.py
      def render_mcp_tool_result(result: Any) -> tuple[str, dict[str, Any], bool]
      返回模型可见 content、metadata 和 is_error。

修改现有模块：

    services/permissions/policy.py
      在 _ask_reasons() 中加入 external_service/call 的非 read-only ask 理由。

    ui/cli/types.py
      CliRuntime 增加 mcp_manager 或 mcp_state 字段，with_session() 保持 MCP 连接不因 /clear 或 /resume 丢失。

    ui/cli/app.py
      build_runtime() 加载 .mcp.json，创建 manager，启动连接，注册 MCP descriptors，把 manager 放入 CliRuntime。subagent base_descriptors 应包含 MCP descriptors。

    ui/cli/commands.py
      handle_command() 增加 /mcp。

    ui/cli/renderer.py
      增加 render_mcp_status()，render_help() 增加 /mcp。

    prompts/runtime_context.py 和 prompts/sections.py
      增加 MCP server instructions section，或用现有 runtime metadata 实现等价行为。不得渲染 secret。

新增测试建议：

    tests/test_mcp_config.py
    tests/test_mcp_names.py
    tests/test_mcp_manager.py
    tests/test_mcp_tool_factory.py
    tests/test_mcp_permissions.py
    tests/test_cli_mcp_commands.py

## Revision Notes

- 2026-06-07 / Codex: 初始版本。根据用户确认的范围撰写完整中文 ExecPlan，选择官方 Python MCP SDK 作为客户端协议实现，项目级 `.mcp.json` 作为唯一 MCP server 配置来源，并将第一版交付范围限定为 tools discovery 和 tools/call。
