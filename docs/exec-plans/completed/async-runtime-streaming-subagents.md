# 异步化 Runtime 和流式输出重构

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本计划遵守仓库根目录的 `PLANS.md`。本文是自包含的：后续执行者只需要阅读本文件和当前工作树，就能在不依赖先前对话的情况下完成这项重构。

## Purpose / Big Picture

OneCode 当前会把一个用户 prompt 送入同步模型调用。CLI 先打印 `Running...`，等待 provider 返回完整 assistant message，然后执行工具，最后打印 assistant 文本。这种结构阻塞了逐 token 可见输出和对流式工具调用的早期处理。完成这项变更后，runtime 将改为 async-first。CLI 会在模型 delta 到达时渲染 assistant 文本，loop 会消费 provider stream event 而不是等待完整响应，工具执行会成为可以 yield 进度和结果的 async generator。

用户可见的验证方式是：在 CLI 中输入一个会生成长回答的 prompt，回答文本会在模型响应完成前开始打印。第二个验证方式是：输入一个会产生多个只读工具调用的 prompt，trace 记录和 CLI 输出会显示工具进度与结果，同时 async loop 保持响应。

这是一项大型重构。目标不是继续把旧阻塞模型接口保留为主路径。旧的 `ModelClient.send(snapshot) -> LLMResponse`、`AgentLoop.run(prompt) -> str` 和 `RegistryToolExecutor.execute(...) -> list[...]` 契约应被 async-first 契约替换。临时兼容 helper 只能存在于测试或短期迁移胶水中，不应保留为 runtime 架构。

## Progress

- [x] (2026-06-05 23:30+08:00) 已阅读 `AGENTS.md`、`architecture.md`、设计文档、活跃 observability 计划、技术债 tracker、当前 loop/model/tool/CLI 代码，以及用户指定的参考材料。
- [x] (2026-06-05 23:45+08:00) 已识别相关参考模式：async generator 主循环、流式 provider event 消费、streaming tool executor 队列、连续 concurrency-safe 工具批次和 abort 传播。
- [x] (2026-06-05 23:55+08:00) 已撰写本活跃 ExecPlan，供讨论和后续实现。
- [x] (2026-06-05 23:59+08:00) 基线测试通过：`uv run python -m pytest tests -q` 得到 `141 passed in 5.27s`。
- [x] (2026-06-05 23:59+08:00) 已添加 `httpx` 依赖，新增 `services/model/stream.py`、`core/stream_events.py`、`HttpxAsyncHttpTransport` 和 OpenAI-compatible Chat Completions streaming parser，并用 fake async transport 覆盖文本 delta、tool-call delta 累积和 provider error。
- [x] (2026-06-05 23:59+08:00) 已添加 `AgentLoop.stream(prompt)` async generator；loop 现在能 yield `assistant_delta`、`tool_call_ready`、`tool_started`、`tool_progress`、`tool_result`、`transition` 和 `completed` event，并在 streaming message 完成后才写入 assistant transcript。
- [x] (2026-06-05 23:59+08:00) 已将 CLI 普通 prompt 分支改为 `asyncio` 输入循环和 `async for runtime.loop.stream(line)` 渲染；文本 delta 会在 final completion 前打印，slash commands 仍保持同步处理。
- [x] (2026-06-06 00:35+08:00) 已将 hooks、permissions 和 tool executor 重构为 async-first generator 接口；`RegistryToolExecutor.execute()` 现在 yield `ToolExecutionUpdate`，`HookRegistry.run()` 和 `PermissionPrompter.request_permission()` 都是 awaitable。
- [x] (2026-06-06 00:35+08:00) 已移除临时兼容 helper：`AgentLoop.run()` / `run_loop()` 同步 drain helper、provider `send()`、显式注入同步 transport 时的 stream fallback，以及同步 `ContextEngine.build_for_model()`。
- [x] (2026-06-06 00:35+08:00) 已移除旧阻塞模型调用 runtime path；测试中的 final-text drain 和 tool-result collect helper 仅保留在测试文件内。
- [x] (2026-06-06 00:35+08:00) 已按当前范围删除子代理相关计划内容；本计划不再要求实现多 agent/task service 或 child loop 测试。

## Surprises & Discoveries

- Observation: OneCode 在 Python 中已经有一部分工具并发设计。
  Evidence: `services/tools/executor.py` 使用 `ThreadPoolExecutor`、`ToolCallClassification.concurrency_safe` 和有序 finalize。这里应转换为 async orchestration，而不是从头重新设计。

- Observation: 当前 observability 实现已经能很好支持 async 迁移。
  Evidence: `services/observability/trace.py` 提供 span/event API，`services/observability/sinks.py` 会缓冲 JSONL 写入。async 计划应添加 stream-specific event，而不是替换 trace。

- Observation: 参考主循环是 async generator，而不是只返回 final text 的函数。
  Evidence: `docs/references/主循环和重建上下文/query.ts` 定义了 `export async function* query`，用 `for await` 消费 `deps.callModel(...)`，yield 流式 message，收集 `toolUseBlocks`，并在之后用 tool result 递归继续。

- Observation: 参考 streaming tool executor 可以在模型 streaming 期间，只要 tool-use block 可用就启动工具，同时在需要时按顺序缓冲最终 tool result。
  Evidence: `docs/references/Tools_full/services/tools/StreamingToolExecutor.ts` 跟踪 queued/executing/completed/yielded tool，条件允许时启动 concurrency-safe tool，立即 yield progress，并暴露 `getRemainingResults()` 来 drain 未完成调用。

- Observation: Python 标准库没有适合 SSE streaming 的实用 async HTTP client。继续使用 `urllib` 会迫使 provider 调用阻塞或进入线程。
  Evidence: `infrastructure/providers/http.py` 使用 `urllib.request.urlopen`；`pyproject.toml` 当前没有 `httpx` 或 `aiohttp`。

- Observation: 当前测试和部分装配仍大量注入同步 fake transport、fake model client 和同步 tool executor。
  Evidence: `tests/test_openai_compatible_provider.py` 给 `OpenAICompatibleChatCompletionsClient` 注入 `FakeTransport` 并断言 `send()`；`tests/test_loop.py` 的 `FakeModelClient` 只实现 `send()`；大量工具测试仍直接调用 `RegistryToolExecutor.execute()`。第一步实现保留兼容 helper，使新增 async path 可测且全量测试保持通过。

## Decision Log

- Decision: 模型边界改为 async streaming first。新的主接口是 provider-neutral stream event 的 async iterator，而不是同步 `send()`。
  Rationale: 流式输出需要 runtime 能观察部分模型输出，并在 provider 生成数据时保持可取消。


- Decision: 添加 `httpx` 作为 provider 调用和 streaming 的 async HTTP 依赖。
  Rationale: OneCode 需要 async POST 和 SSE line iteration。只用标准库实现可靠的 async HTTPS 与 streaming 风险更高，也会模糊 runtime 重构本身。


- Decision: 替换阻塞 runtime 契约，而不是把它们保留为一等架构。
  Rationale: 用户明确希望进行大型 async 重构，并且不希望兼容压力继续保留旧的阻塞模型调用形态。


- Decision: 工具 permission、guard、schema validation 和 classification 在逻辑顺序上保持 deny-first，但接口变为 awaitable。
  Rationale: Async 不能削弱安全性。这些步骤通常 CPU 开销较轻，但 hooks、UI prompt 和未来外部 policy check 都需要 await 点。


- Decision: 第一版实现可以在 completed tool call block 之后执行工具，而不是在任意 partial JSON delta 后执行工具。
  Rationale: Chat Completions 会增量流式输出 tool-call arguments。只有当工具 JSON input 完整并通过校验后才能运行工具。streaming event accumulator 应在 tool call 完整后尽早暴露 completed tool call；如果 provider delta 不能提供可靠完成边界，第一版仍可以在模型 message 结束后 drain remaining tools。


- Decision: 第一批代码保留同步兼容 helper，但把新增行为和 CLI 主路径接到 async stream。
  Rationale: 现有工作树有 141 个 passing tests，且大量测试夹具仍是同步形态。直接删除所有同步接口会把 provider、loop、tool executor、CLI 和 command tests 同时打碎，降低每个 milestone 的可验证性。兼容 helper 被明确列为后续删除项，不作为最终架构验收。


## Outcomes & Retrospective

第一批实现已落地 async streaming runtime 的基础：模型 provider 现在有 provider-neutral stream event、OpenAI-compatible Chat Completions SSE parser 和 async HTTP transport；`AgentLoop.stream(prompt)` 能在 assistant 文本 delta 到达时立即 yield runtime event；CLI 普通 prompt 分支已在 `asyncio` 下消费这些 event 并增量打印。新增 tests 证明 delta 会早于 final completion 被观察到，streamed tool call 会驱动 loop continuation。

第二批实现已完成 async-first runtime 迁移：executor、hook 和 permission 都改为 awaitable，旧 `send()` / `run()` / 同步 context build helper 已删除。按最新范围，本计划不再实现多 agent/task service；后续如果需要这类能力，应另开独立 ExecPlan。

## Context and Orientation

OneCode 是一个 Python code-agent runtime。当前主 runtime loop 位于 `core/loop.py`。它接收用户 prompt，将其追加到 `services/context/message_store.py`，通过 `core/context_engine.py` 构建 `services/context/snapshot.py` 中的 `ContextSnapshot`，调用模型客户端，追加 assistant message，通过 `services/tools/executor.py` 执行模型请求的工具，追加 tool result，并不断重复直到不再有 tool call。

当前模型接口是 `services/model/client.py::ModelClient.stream(snapshot) -> AsyncIterator[ModelStreamEvent]`。具体 provider 是 `infrastructure/providers/chat_completions.py::OpenAICompatibleChatCompletionsClient`，它构建 OpenAI-compatible Chat Completions streaming 请求，并通过 `HttpxAsyncHttpTransport` 消费 SSE chunks。

当前工具 executor 接口是 `services/tools/executor.py::ToolExecutor.execute(tool_calls, state) -> AsyncIterator[ToolExecutionUpdate]`。registry-backed executor 已经完成 schema validation、工具专属 validation、input-aware classification、guard 检查、permission 检查、`PreToolUse` hook、handler 执行、结果预算处理、`PostToolUse` 或 `ToolError` hook，以及 state side effect。它也会划分连续 `concurrency_safe=True` 调用，并用 async orchestration 并发执行这些 handler。

当前 CLI 位于 `ui/cli/app.py`。它使用 `asyncio` 主循环，通过 `await asyncio.to_thread(input, "onecode> ")` 读取用户输入，提交普通 prompt 后用 `async for runtime.loop.stream(line)` 渲染 assistant delta、tool result 和 final completion。Permission prompt 通过 `ui/cli/permissions.py::CliPermissionPrompter.request_permission` 的 async 协议等待。

当前 observability service 位于 `services/observability/`。它提供 `TraceRecorder`、JSONL sink 和 metadata sanitization。它记录 interaction、context prepare、model call、transition、tool batch、tool preflight、permission wait、tool execution、tool result 和 hook spans/events。async 重构应增加 `model_stream_start`、`model_content_delta`、`model_tool_call_delta`、`model_message_completed` 和 `tool_progress` 等 event，同时保留隐私规则：不记录 prompt 文本、源码文本、tool output 文本和 secret。

本计划使用几个术语。async function 是用 `async def` 声明的 Python 函数；它可以在 `await` 处暂停而不阻塞 event loop。async generator 是使用 `yield` 的 `async def` 函数；调用方用 `async for` 消费，并随时间接收 event。stream event 是一个很小的 provider-neutral 对象，描述 runtime 进度的一部分，例如 text delta、completed tool call、tool result 或 final message。

用户特别要求参考 `docs/references/主循环和重建上下文`，以及 `docs/references/Tools_full` 中非具体工具的部分。这些参考材料中适用于本计划的模式包括：

- `query.ts` 使用 async generator 主循环，在模型和工具运行时 yield messages 与 request-start events。
- `query.ts` 不只依赖 provider stop reason 判断工具使用；它检测实际 tool-use blocks。
- `query.ts` 在启用 streaming tool execution 时启动 `StreamingToolExecutor`，随着 tool block 到达将其加入 executor，在 streaming 期间 yield completed/progress results，并在继续前 drain remaining results。
- `query.ts` 包含 reactive compact 和 max-output recovery 这些 async recovery branch，因此现在就把 OneCode 的 `ContextEngine` 和未来 compaction 设计为 async 是合理的。
- `Tool.ts` 定义了带 abort controller、permission context、progress callbacks、concurrency-safety metadata、interrupt behavior 和 result size policy 的 tool context。
- `toolOrchestration.ts` 将 tool call 划分为单个 non-concurrency-safe 调用或连续 concurrency-safe 批次，安全批次并发运行，并在批次后应用 context modifier。
- `StreamingToolExecutor.ts` 跟踪工具队列状态，在条件允许时启动工具，立即 yield progress，缓冲 final result 以保持必要顺序，并通过 abort controller 传播取消。
- `toolExecution.ts` 将 tool execution 建模为 async generator，因此 validation error、permission denial、hook message、progress 和 final tool result 都走同一个 event channel。

## Plan of Work

Milestone 1 引入 provider-neutral async stream types。新增 `services/model/stream.py`，定义 `ModelStreamEvent`、`ModelContentDelta`、`ModelToolCallDelta`、`ModelToolCallCompleted`、`ModelMessageCompleted`、`ModelUsageEvent` 和 `ModelStreamError`；如果一个带 `type` 字段的 dataclass 比多个类更清晰，也可以使用单一 dataclass。具体结构应简单且可序列化。content delta 包含文本和可选 block index。tool call delta 包含 provider call id、index、name delta 和 arguments delta。completed tool call 包含一个完整解析后的 `services.tools.types.ToolCall`。message completed event 包含可追加到 `MessageStore` 的 assistant message、final text、completed tool calls tuple、stop reason、usage 和 output interruption flag。新增 `services/model/client.py::StreamingModelClient`，其接口为 `stream(snapshot: ContextSnapshot) -> AsyncIterator[ModelStreamEvent]`。所有调用点迁移完成后，移除 runtime-facing 的 `ModelClient.send`。

Milestone 2 替换阻塞 HTTP provider transport。将 `httpx>=0.27` 添加到 `pyproject.toml`，并通过 `uv sync --dev` 或仓库依赖更新工作流更新 `uv.lock`。替换或补充 `infrastructure/providers/http.py`，新增 async transport，例如包含 `async post_json(...)` 和 `async stream_json_lines(...)` 的 `AsyncHttpTransport`。对于 Chat Completions streaming，请发送带 `{"stream": true, ...}` 的 payload，并解析 server-sent event lines。以 `data:` 开头的行包含 JSON，除非 payload 是 `[DONE]`。HTTP error、invalid JSON、timeout 和 network failure 都应转换为带现有 provider-neutral 字段的 `ProviderError`。

Milestone 3 将 `infrastructure/providers/chat_completions.py` 重写为 streaming client。`OpenAICompatibleChatCompletionsClient.stream(snapshot)` 应构建和当前相同的 messages 与 tool schemas，加入 `stream=True`，并消费 SSE chunks。它必须累积 assistant content 和 tool call arguments。OpenAI-compatible tool call delta 通常出现在 `choices[0].delta.tool_calls` 下，每项有 `index`、可选 `id`、可选 `function.name` 和部分 `function.arguments`。accumulator 应按 index 追加 argument fragments，只在 tool call 完成或 message 结束时解析 JSON，并且只在得到合法 JSON object input 后 emit completed `ToolCall` record。它应在 content delta 到达时立即 emit `ModelContentDelta`。在 message 结束时，它应 emit 一个 `ModelMessageCompleted`，其中包含可追加到 `MessageStore` 的 assistant message。如果 provider 只在最终 chunk 返回 usage，或完全不返回 usage，则保留 `usage=None`。

Milestone 4 将 context preparation 改为 async。修改 `core/context_engine.py` 中的协议：`prepare(...)` 改为 `async def`；只有当 prompt assembly 需要 async 时，才把 `assemble(...)` 改为 `async def`；只有当 registry visibility 变为 async 时，才把 `tool_schemas(...)` 改为 `async def`。为了缩小这个 milestone 的范围，第一版可以只让 `ContextEngine.build_for_model` 变为 async，而内部仍同步调用 prompt assembly 和 schema projection。重要契约是 loop 现在执行 `snapshot = await context_engine.build_for_model(state)`。这为未来可能读取文件、查询 store 或调用 summarization model 的 compact/projector services 提前建立边界。

Milestone 5 将 agent loop 重构为 async event stream。将 `AgentLoop.run(prompt) -> str` 替换为 `AgentLoop.stream(prompt) -> AsyncIterator[AgentEvent]`。新增 `core/stream_events.py`，定义 provider-neutral runtime events，例如 `interaction_started`、`assistant_delta`、`assistant_message_completed`、`tool_call_ready`、`tool_started`、`tool_progress`、`tool_result`、`transition` 和 `completed`。loop 应追加 user message，然后反复构建 context、消费 `model_client.stream(snapshot)`、向调用方 yield assistant deltas、追加 completed assistant message、通过 async tool executor 执行 completed tool calls、追加 tool result messages、设置 transition 并继续。loop 必须保持薄：它不理解具体 provider 字段、具体工具名或 UI 渲染细节。

Milestone 6 将 tool executor 转换为 async generator 语义。将 `ToolExecutor.execute` 改为 `async def execute(...) -> AsyncIterator[ToolExecutionUpdate]`，其中 update 可以包含 progress、final `ToolExecutionResult` 或 trace/control metadata。现有 validation、classification、guard、permission、hook 和 result policy 顺序保持不变。第一版实现可以用 `asyncio.to_thread` 执行当前阻塞 handler，以兼容现有具体工具。连续 concurrency-safe 批次应使用 Python 3.11 的 `asyncio.TaskGroup`，或使用带 semaphore 的 `asyncio.gather`，并由 `ONECODE_MAX_TOOL_CONCURRENCY` 限制并发数。Non-concurrency-safe 工具仍然串行。更新 `state.metadata["files_read"]` 这类 side effect 仍必须发生在有序 finalize 阶段，而不是 concurrent handler 内部。

Milestone 7 让 permissions 和 hooks awaitable。将 `PermissionPrompter.request_permission` 改为 `async def request_permission(...)`。修改 `HookCallback` 以允许 async callback，并将 `HookRegistry.run` 改为 async。逻辑继续保持 deny-first：guard deny 不能被覆盖，hook-updated input 必须重新 validate 和 classify，permission allow 不能覆盖 deny。CLI permission prompting 第一版可以使用 `await asyncio.to_thread(input, prompt)`，或使用 blocking `input` 的小型 async wrapper；协议必须是 async，方便未来 UI 等待非阻塞 prompt。

Milestone 8 将 CLI 重构为 `asyncio`。修改 `ui/cli/app.py`，让 `main()` 调用 `asyncio.run(main_async(argv))`。Prompt loop 第一版可以使用 `await asyncio.to_thread(input, "onecode> ")` 读取用户输入，因为第一阶段 streaming milestone 不要求替换整个终端输入栈。提交普通 prompt 后，使用 `async for event in runtime.loop.run(line)` 消费事件。Assistant deltas 应立即渲染，不等待 final message。Tool progress 和 tool results 到达时也应渲染。退出时 flush transcript 和 trace。Slash commands 如果很快，可以继续是同步函数，但 `/resume`、`/clear` 和 `/trace` 应能从 async CLI 中调用，并且不能阻塞长时间工作。

Milestone 9 引入 async cancellation。新增一个小型 cancellation token 对象，或一致使用 `asyncio.Event`/task cancellation。参考代码中的 abort controller 在 Python 中应表现为：runtime 可以取消 model streaming 和 running tools。`AgentLoop` 应捕获 `asyncio.CancelledError`，emit trace event，取消正在运行的 tool tasks，flush transcript/trace，然后重新抛出或返回结构化 abort event。Bash 和 grep 最终应使用 async subprocess API，这样 cancellation 能终止 child processes。第一版中，通过线程运行的 blocking handler 不能总是立即被杀死；如果这个限制仍存在，应记录到技术债 tracker。

Milestone 10 已从本计划移除。当前不实现多 agent/task service、child loop、parent/child trace metadata 或 agent tool。未来如果需要这类能力，应另开独立 ExecPlan，避免把本次 async runtime 迁移扩大为协调系统设计。

Milestone 11 更新 observability 和 transcript streaming 行为。`TraceRecorder` 当前支持同步 span context manager。如有需要，添加 async span context manager 支持；也可以在 async 代码中显式使用 `start_span`/`end`。新增 stream start/end、content delta counts、completed tool call count、tool queue status 和 cancellation 事件。不得将 assistant text deltas、prompt text、source code、stdout/stderr content、API keys 或完整外部路径写入 trace attributes。MessageStore 应在 streaming 开始前追加 user message，并且只在模型 message 完成时追加 assistant message；CLI deltas 是 UI events，而不是已提交 transcript messages。

Milestone 12 移除阻塞 runtime paths 并更新测试。将测试中的 fake model clients 替换为 async stream fakes。将针对最终 returned text 的断言替换为针对 emitted events 和 stored messages 的断言。测试中可以保留一个 drain async event stream 到 final text 的 helper，但生产代码中不要保留 `send()` 或 blocking `run()` path。实现完成后更新 `docs/tech-debt/tech-debt-tracker.md`，说明已解决的问题和剩余问题。

## Concrete Steps

从仓库根目录开始：

    D:\study\OneCode

首先运行当前 baseline tests，了解 dirty working tree 状态。如果无关的当前变更失败，应在开始重构前把失败记录到本计划中：

    uv run python -m pytest tests -q

如果当前 observability 工作树一致，预期结果类似：

    141 passed in ...s

如果数量不同，请先把真实输出记录到本计划的 `Artifacts and Notes`，再修改代码。

添加 async HTTP 依赖：

    uv add httpx
    uv sync --dev

如果网络访问因为 sandbox 失败，请按本地权限策略使用 approved escalation 重新运行。依赖更新后，`pyproject.toml` 应包含 `httpx`，`uv.lock` 应被更新。

实现 Milestone 1，并新增测试：

    tests/test_model_stream_events.py

测试应构造一个 fake async stream client，并断言 content deltas、completed tool calls 和 final message events 可以用 `async for` 消费。

实现 Milestone 2 和 3，并新增 provider tests：

    tests/test_openai_compatible_provider_streaming.py

使用 fake async transport chunks，而不访问真实网络。覆盖 text deltas、跨多个 event 切分的 tool call name/argument deltas、`[DONE]`、invalid JSON 和 provider errors。断言 provider-specific shape 不泄露到 `core`。

实现 Milestone 4 和 5，并新增 loop tests：

    tests/test_async_loop.py

使用 fake streaming model client。一个测试应 stream text deltas 且不使用工具；它应在 `completed` 前观察到 `assistant_delta` events。另一个测试应 stream 一个 completed tool call；它应断言 assistant message 被追加、tool results 被追加、transition 是 `tool_use`，并且下一次模型请求能在 context 中看到 tool result。

实现 Milestone 6 和 7，并新增工具测试：

    tests/test_async_tool_executor.py
    tests/test_async_hooks_permissions.py

移植现有 concurrency tests，证明 async-safe tools 在时间上重叠，而 result finalization 保持 provider 顺序。测试 async permission prompt 会被 await，hook-updated input 会重新校验。

实现 Milestone 8，并新增 CLI command tests：

    tests/test_async_cli_streaming.py

不要要求真实 terminal。注入 fake input 并捕获 renderer output。断言 streamed content delta 会在 final completion event 前渲染。如果 renderer 仍是 line-oriented，请添加一个最小的 `render_assistant_delta` helper。

每个 milestone 后运行对应目标测试。最后运行：

    uv run python -m pytest tests -q
    uv run python -m compileall core services infrastructure tools ui

最终预期是所有测试通过且 compileall 成功。

## Validation and Acceptance

第一类验收是模型 streaming。使用 fake streaming model 的测试发出 `"hello"`、`" "` 和 `"world"` 三个 delta，并在它们之间加入短暂 await。消费 `runtime.loop.run("say hello")` 时，必须先 yield 三个 assistant delta events，再 yield final completed event。存储的 transcript 应只包含一条 user message 和一条最终 assistant message，而不是三条 partial assistant messages。

第二类验收是工具 continuation。fake model stream 发出一个包含 completed `read_file` tool call 的 assistant message。loop 必须追加 assistant message，通过 async executor 执行工具，追加一个 `tool_result`，将 transition 设置为 `tool_use`，并再次发起模型 stream request。测试应证明 continuation 基于实际 tool calls，而不是只基于 stop reason。

第三类验收是 async tool concurrency。三个连续只读 fake tools 各自 await 一个 barrier 或 sleep。在 max concurrency 为 3 时，测试 wall-clock 时间应明显小于串行时间。在只读调用之间插入一个 non-concurrency-safe fake edit，并证明 edit 前后的调用不会跨越 edit 边界重叠。

第四类验收是 CLI streaming。fake runtime 或 fake model client 应让 `ui/cli/app.py` 在模型 stream 仍打开时渲染 assistant delta output。测试应捕获输出顺序：prompt submitted、running/stream start shown、first text delta shown、later final completion shown。

第五类验收是隐私和 observability。Trace records 应包含 counts、durations、transition names、tool names 和 stream event types。它们不得包含用户 prompt 文本、assistant text deltas、tool output content、edit old/new strings、API keys、headers 或完整外部路径。

## Idempotence and Recovery

这项重构刻意较大，因此每个 milestone 完成后都应提交，或至少保持目标测试通过，再继续下一个 milestone。如果某个 milestone 中途失败，请保留新的 async interfaces 和 tests 可见，而不是隐藏失败。反复运行 `uv run python -m pytest ... -q` 应该是安全的，不应改变 workspace 状态；手动 CLI sessions 产生的 `.onecode` runtime artifacts 除外。

不要使用破坏性 git 命令恢复。如果迁移期间旧 sync loop 和新 async loop 短暂共存，请在计划中标记旧路径为 transitional，并在最终验收前移除。不要让生产代码同时把 `model_client.send()` 和 `model_client.stream()` 作为平等选择保留下来；那会保留本计划要替换的阻塞架构。

如果 `httpx` 依赖安装因网络 sandbox 失败，请停止 provider streaming 实现，并在 `Surprises & Discoveries` 记录失败。若可能，可以继续实现 fake-client loop tests，因为 async runtime shape 可以独立于真实 provider transport 进行验证。

如果 thread-backed tools 的 cancellation 无法立即停止正在运行的 handler，请把它记录为技术债，并优先在后续 milestone 中将 `bash` 和 `grep` 转换为 async subprocess。不要假装 thread cancellation 等同于 process cancellation。

## Artifacts and Notes

实现前的当前架构证据：

    core/loop.py calls self.model_client.send(snapshot) and waits for LLMResponse.
    services/model/client.py exposes only send(snapshot) -> LLMResponse.
    infrastructure/providers/http.py uses urllib.request.urlopen.
    services/tools/executor.py already partitions concurrency_safe tool calls and uses ThreadPoolExecutor.
    ui/cli/app.py calls runtime.loop.run(line) and prints only final assistant text.

用于塑造本计划的参考证据：

    docs/references/主循环和重建上下文/query.ts defines export async function* query.
    query.ts consumes deps.callModel(...) using for await.
    query.ts collects toolUseBlocks from streamed assistant messages.
    query.ts adds streamed tool blocks to StreamingToolExecutor and drains getRemainingResults().
    docs/references/Tools_full/services/tools/toolOrchestration.ts partitions consecutive concurrency-safe tool calls.
    docs/references/Tools_full/services/tools/StreamingToolExecutor.ts queues tools, tracks executing/completed/yielded state, yields progress, and preserves ordering constraints.
    docs/references/Tools_full/Tool.ts includes abortController, agentId, isConcurrencySafe, interruptBehavior, progress callbacks, and result size policy.

第一批 async streaming 实现输出：

    uv add httpx
    Installed httpx==0.28.1 and transitive dependencies.

    uv run python -m pytest tests\test_model_stream_events.py tests\test_openai_compatible_provider_streaming.py tests\test_async_loop.py tests\test_async_cli_streaming.py tests\test_loop.py tests\test_openai_compatible_provider.py -q
    32 passed in 0.53s

    uv run python -m pytest tests -q
    148 passed in 1.40s

    uv run python -m compileall core services infrastructure tools ui
    succeeded

预期最终命令输出：

    uv run python -m pytest tests -q
    <all tests passed>

    uv run python -m compileall core services infrastructure tools ui
    <compileall completes without errors>

## Interfaces and Dependencies

实现应将 `httpx>=0.27` 加入 project dependencies。使用 `httpx.AsyncClient` 进行 async HTTP requests 和 streaming response iteration。除非 `httpx` 无法支持观察到的 provider 行为，否则不要引入第二个 HTTP 依赖。

在 `services/model/stream.py` 中定义 provider-neutral stream events。可以使用一个紧凑的单 dataclass：

    @dataclass(frozen=True)
    class ModelStreamEvent:
        type: Literal[
            "content_delta",
            "tool_call_delta",
            "tool_call_completed",
            "message_completed",
            "usage",
            "error",
        ]
        text: str = ""
        tool_call: ToolCall | None = None
        assistant_message: dict[str, Any] | None = None
        final_text: str = ""
        stop_reason: str | None = None
        usage: ModelUsage | None = None
        output_interrupted: bool = False
        metadata: dict[str, Any] = field(default_factory=dict)

如果多个独立 class 更清楚，也可以使用多个 class，但 event type 名称和字段语义要保持一致。

在 `services/model/client.py` 中，用以下 runtime protocol 替换旧协议：

    class StreamingModelClient(Protocol):
        def stream(
            self,
            snapshot: ContextSnapshot,
        ) -> AsyncIterator[ModelStreamEvent]:
            ...

在 `core/stream_events.py` 中定义面向 runtime/CLI/tests 的事件：

    @dataclass(frozen=True)
    class AgentEvent:
        type: Literal[
            "interaction_started",
            "assistant_delta",
            "assistant_message_completed",
            "tool_started",
            "tool_progress",
            "tool_result",
            "transition",
            "completed",
            "error",
        ]
        text: str = ""
        result: ToolExecutionResult | None = None
        transition: str | None = None
        metadata: dict[str, Any] = field(default_factory=dict)

在 `core/loop.py` 中，主接口应改为：

    class AgentLoop:
        async def run(self, prompt: str) -> AsyncIterator[AgentEvent]:
            ...

如果 `run` 返回 async iterator 在 Python typing 中不方便，可以使用：

    async def stream(self, prompt: str) -> AsyncIterator[AgentEvent]:
        ...

并更新调用方使用 streaming method。不要把旧 blocking model call 保留为生产架构。

在 `core/context_engine.py` 中，使模型 context construction awaitable：

    class ContextPreparer(Protocol):
        async def prepare(...): ...

    class ContextEngine:
        async def build_for_model(self, state: RuntimeState) -> ContextSnapshot:
            ...

在 `services/tools/executor.py` 中定义：

    @dataclass(frozen=True)
    class ToolExecutionUpdate:
        type: Literal["started", "progress", "result", "error"]
        result: ToolExecutionResult | None = None
        tool_call_id: str = ""
        tool_name: str = ""
        content: str = ""
        metadata: dict[str, Any] = field(default_factory=dict)

    class ToolExecutor(Protocol):
        async def execute(
            self,
            tool_calls: tuple[ToolCall, ...],
            state: RuntimeState,
        ) -> AsyncIterator[ToolExecutionUpdate]:
            ...

第一版中，具体 tool handlers 可以继续是同步函数，但 executor 必须通过 async orchestration 调用它们。对于未来原生 async 工具，可以给 `ToolDescriptor` 添加可选 async handler 字段，或允许 handler return value 是 awaitable。Descriptor metadata 必须继续 input-aware 且 deny-first。

在 `services/permissions/prompter.py` 中定义：

    class PermissionPrompter(Protocol):
        async def request_permission(
            self,
            request: PermissionRequest,
        ) -> PermissionResponse:
            ...

在 `services/hooks/registry.py` 中支持 async callbacks：

    HookCallback = Callable[[HookPayload], HookResult | Awaitable[HookResult | None] | None]

    class HookRegistry:
        async def run(self, event: HookEvent, payload: HookPayload) -> HookResult:
            ...

本计划不定义多 agent/task 接口，也不新增相关 runtime foundation。多 agent/task 能力需另开计划。

## Change Note

2026-06-05 / Codex: 创建本 ExecPlan。创建前已阅读 OneCode 架构、当前代码、活跃 observability 工作、`docs/references/主循环和重建上下文`，以及 `docs/references/Tools_full` 中非具体工具的机制。本计划刻意选择 async-first model streaming，并且不保留同步模型调用作为生产 runtime path，以匹配用户明确提出的方向。

2026-06-05 / Codex: 将计划正文翻译为中文，保留 `PLANS.md` 要求的章节名、接口名、代码片段和命令，以便后续实现者可以直接按中文计划执行。

2026-06-05 / Codex: 开始实现计划。新增 async model stream event、runtime event、httpx async transport、OpenAI-compatible streaming parser、async loop stream、CLI streaming 渲染和 focused tests。为保持现有 passing tests 和迁移可验证性，暂留同步兼容 helper，并在 Progress 与 Decision Log 中明确列为后续删除项。
