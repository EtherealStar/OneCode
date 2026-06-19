# 实现 OneCode CLI 真正流式输出

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This document follows `PLANS.md` in the repository root. Any contributor who changes implementation scope, discovers new behavior, or completes a milestone must update this file in the same change.

## Purpose / Big Picture

用户现在在 OneCode CLI 中看到的“流式输出”不是严格实时输出。模型 provider 可以产生 `content_delta`，CLI 也有动态预览区域，但 `services/model/retry.py` 会先缓存一次模型请求的全部事件，`core/loop.py` 又会先收集完整 `model_events`，确认没有重试或输出截断恢复后才把文本 delta 释放给 UI。因此用户体感上常常是模型结束后再快速回放文本，而不是模型生成时同步显示。

完成本计划后，OneCode CLI 应支持真正实时的 assistant 文本流式输出：模型每产生一段文本，终端动态区就尽快显示；工具参数生成、工具执行、最终 assistant message 写入 transcript 仍保持受治理；如果发生重试、取消或输出截断，UI 会明确显示状态而不是静默丢弃已经展示过的内容。用户可以通过运行 CLI、发出一个会持续生成多行文本的 prompt，观察终端按行增量出现内容来确认效果。本计划不保留旧两层缓冲的兼容路径，也不保留只为迁移而存在的隐藏开关。

本计划参考 `docs/references/ui/screens/REPL.tsx`、`docs/references/ui/components/Markdown.tsx`、`docs/references/ui/components/Messages.tsx` 和 `docs/references/ui/utils/messages.ts` 中的机制，但目标不是移植 React/Ink，而是把其中的运行时事件、临时 streaming UI state、Markdown 稳定块渲染、取消保留 partial output 等思想迁移到当前 Python、Rich、prompt_toolkit 架构。

## Progress

- [x] (2026-06-15 23:45+08:00) 研究 OneCode 当前流式路径，确认 provider、runtime、CLI 已有事件链但被 retry/recovery 缓冲。
- [x] (2026-06-15 23:45+08:00) 阅读参考实现 `Markdown.tsx`，确认 `StreamingMarkdown` 使用 stable prefix / unstable suffix 算法减少重复 Markdown parse。
- [x] (2026-06-15 23:45+08:00) 阅读参考实现 `REPL.tsx`、`Messages.tsx` 和 `messages.ts`，确认参考实现把 `streamingText`、`streamingToolUses`、`streamingThinking` 作为临时 UI state，而不是立刻写入正式 message 列表。
- [x] (2026-06-15 23:45+08:00) 创建本 ExecPlan，明确 OneCode 应如何分阶段实现真正实时流式输出。
- [x] (2026-06-16 00:05+08:00) 阅读参考实现 `docs/references/ui/components/MarkdownTable.tsx`，确认表格渲染应使用终端宽度感知布局、ANSI-aware wrapping、过窄纵向格式和 safety margin，而不是只补 Rich theme 样式。
- [x] (2026-06-15 22:00+08:00) 重写 `services/model/retry.py`，删除 `buffer: list[ModelStreamEvent]` 缓冲结构；事件逐条 yield，已经显示的 partial output 不再被吞回。
- [x] (2026-06-15 22:30+08:00) 重写 `core/loop.py`，删除 `model_events.append` 二次缓冲；`content_delta` 立即 yield `assistant_delta`，`tool_call_completed` 立即 yield `tool_call_ready`，`tool_call_delta` 转发为新的 `tool_call_delta` 事件。
- [x] (2026-06-15 22:45+08:00) 新增 `core/stream_events.py` 中的 `tool_call_delta` 事件类型并把 `tool_call_delta` 串入 `AgentEventType` Literal。
- [x] (2026-06-15 23:00+08:00) 新增 `ui/cli/terminal/markdown_rendering.py`：GFM 表格解析 + 宽度感知列宽分配 + 窄终端纵向 fallback + safety margin，宽度用 `wcwidth` 测量，CJK/emoji 不会错位。
- [x] (2026-06-15 23:15+08:00) 新增 `ui/cli/terminal/stream_session.StreamingMarkdownState`：stable prefix / unstable suffix 跟踪，仅在边界前进时重渲染新增段落；未闭合 code fence、行尾不完整、表格未稳定都留在 unstable 段。
- [x] (2026-06-15 23:30+08:00) 修改 `ui/cli/terminal/static_output.py` 的 `print_assistant_markdown`，由它直接打印 `onecode> ` 前缀避免调用方忘记。
- [x] (2026-06-15 23:45+08:00) 重写或删除依赖旧 buffer 语义的旧测试：retry 失败 attempt 的 partial delta 现在可见，`max_output_tokens_escalate` 持久化截断 assistant，`rate_limit_retry` transition 携带 `partial_output_visible=True`。
- [x] (2026-06-15 23:55+08:00) 新增 `tests/test_markdown_rendering.py`、`tests/test_streaming_markdown_state.py`、`tests/test_loop_realtime_streaming.py` 三个测试文件，覆盖 GFM 表格解析、列宽分配、纵向 fallback、稳定块前进、实时 streaming 时序。
- [x] (2026-06-16 00:10+08:00) 全量测试 443 通过、2 个 pre-existing 失败（与本计划无关）；聚焦测试 124 通过无 warning；`compileall` 通过。手工验证 fake model 第一个 `assistant_delta` 在 16ms 到达，第二个在 125ms（provider `asyncio.sleep(0.1)` 后），`message_completed` 在 235ms 才到达。

## Surprises & Discoveries

- Observation: OneCode 当前 `OpenAICompatibleChatCompletionsClient.stream()` 已经从 provider stream 中读取 `delta.content` 并产出 `ModelStreamEvent.content_delta`。
  Evidence: `infrastructure/providers/chat_completions.py` 中 `content = delta.get("content")` 后调用 `yield ModelStreamEvent.content_delta(content)`。

- Observation: OneCode 当前不是严格实时输出，因为 retry runner 会缓存一次 attempt 的全部 model events，成功后才释放。
  Evidence: `services/model/retry.py` 中 `buffer.append(event)` 收集事件，随后 `for event in buffer: yield event`。

- Observation: OneCode 当前 loop 还会再次收集 `model_events`，等 `message_completed` 到达并完成 max-output recovery 判断后才把 `content_delta` 转成 `AgentEvent(type="assistant_delta")`。
  Evidence: `core/loop.py` 中 `model_events.append(model_event)`，之后在 `for model_event in model_events` 中 yield `assistant_delta`。

- Observation: 参考实现的 `REPL.tsx` 只显示到最后一个换行前的 streaming text，避免用户看到逐字符抖动。
  Evidence: `visibleStreamingText = streamingText.substring(0, streamingText.lastIndexOf('\n') + 1) || null`。

- Observation: 参考实现取消时会把已经显示的 partial assistant text 固化为 assistant message，避免用户看到过的内容从 UI 中消失。
  Evidence: `REPL.tsx` 中取消路径检查 `if (streamingText?.trim())`，然后 `createAssistantMessage({ content: streamingText })`。

- Observation: `docs/references/ui/components/MarkdownTable.tsx` 存在，并且提供了比“补 Rich 样式名”更完整的表格渲染策略。它按终端宽度计算列宽，保留 ANSI 样式，避免截断，必要时把表格转成 key-value 纵向格式。
  Evidence: `MarkdownTable.tsx` 定义 `SAFETY_MARGIN = 4`、`MIN_COLUMN_WIDTH = 3`、`MAX_ROW_LINES = 4`，用 longest word 计算最小列宽，用 ideal width 计算理想列宽，用 `wrapAnsi` 包装单元格，并在 `maxLineWidth > terminalWidth - SAFETY_MARGIN` 时回退到 vertical format。

- Observation: `docs/references/ui/components/MarkdownTable.tsx` 的 safety margin 不是视觉偏好，而是为了避免终端宽度变化或父级缩进导致表格越界后反复裁剪和重绘。
  Evidence: 文件注释说明 safety margin 用于处理 parent indentation 和 terminal resize races，避免表格溢出 layout box 后产生 alternating-frame clipping 和 scrollback flicker。

## Decision Log

- Decision: 将“真正实时释放 provider text delta”和“优化动态 Markdown 渲染”拆成两个独立 milestone。
  Rationale: 参考实现的 `StreamingMarkdown` 解决的是 UI 增量渲染性能和稳定性；OneCode 当前真正阻塞实时输出的是 `services/model/retry.py` 与 `core/loop.py` 的事件缓冲。拆分后可以先让事件实时到达，再让动态区渲染足够稳定。
  Date/Author: 2026-06-15 / Codex

- Decision: 删除旧的两层缓冲实现，不再保留任何默认、兼容或迁移开关。
  Rationale: 用户明确要求不要为了迁移安全保留影响性能的旧缓冲代码。OneCode 的目标是 CLI code-agent runtime，而不是隐藏 partial output 的 batch wrapper；一旦选择真正流式输出，retry 和 max-output recovery 必须围绕“已经显示的内容不可撤回”重新设计。旧测试中断言 failed attempt partial delta 不外显的用例应删除或改写为新语义测试，不能在计划中继续作为保留项。
  Date/Author: 2026-06-16 / Codex

- Decision: `services/model/retry.py` 不再负责缓存完整 attempt；它应边转发 provider events 边处理 retryable exception，并在失败时发出或促成可观察的 retry transition。
  Rationale: attempt 级缓存是第一层阻塞。保留它会让任何上层 UI 优化都只能看到回放式流式输出。retry runner 可以继续负责指数退避、retry 计数和错误日志，但不能用完整事件 buffer 阻塞文本 delta。
  Date/Author: 2026-06-16 / Codex

- Decision: `core/loop.py` 不再把所有 `ModelStreamEvent` 收集到 `model_events` 后统一 replay；它应在收到 `content_delta` 时立即 yield `assistant_delta`。
  Rationale: loop 中的 `model_events` replay 是第二层阻塞。删除 retry runner 缓冲后，如果 loop 仍二次缓存，用户仍看不到真正实时输出。loop 只应保留最终写入 message store、工具执行和 recovery 判断所需的最小状态，例如 final text accumulator、completed tool calls 和 completed message。
  Date/Author: 2026-06-16 / Codex

- Decision: 第一版实时流式只实时输出普通 assistant text；tool call delta 先作为 UI 状态展示，不提前执行工具。
  Rationale: OpenAI-compatible tool call arguments 是增量 JSON，只有完整且通过解析、校验、guard、permission policy 后才能执行。提前执行 partial JSON 会破坏工具安全边界。
  Date/Author: 2026-06-15 / Codex

- Decision: 不把 streaming text 直接写入 `MessageStore`；只在 provider 完成、用户取消且选择保留 partial、或 recovery exhausted 时写入正式 assistant message。
  Rationale: 参考实现把 `streamingText` 作为临时 UI state，final message 到达时清空临时状态并追加正式消息。OneCode 的 `MessageStore` 是模型上下文和 transcript 的事实来源，不能被不完整文本污染。
  Date/Author: 2026-06-15 / Codex

- Decision: Markdown 表格处理分两层推进：先补 Rich theme 样式作为止血，随后实现 OneCode 专用 Markdown table renderer，并把专用 renderer 作为最终目标。
  Rationale: `MarkdownTable.tsx` 证明参考实现并不依赖通用 Markdown table renderer；它用宽度感知列宽、ANSI-aware wrapping 和过窄纵向格式保证表格在终端稳定可读。仅补 `table.header` 能消除当前异常，但不能解决窄终端截断、宽字符测量、表格行过高和 resize flicker。因此止血修复和完整 renderer 应分别落地。
  Date/Author: 2026-06-15 / Codex

## Outcomes & Retrospective

实现完成。总结如下。

### 实际改动

- `services/model/retry.py` 整体重写：删除 `buffer.append` + 成功后 `for event in buffer: yield event` 结构。`ModelRetryRunner.stream` 现在逐条 `async for event in operation(): yield event`；`ProviderError` 处理保留重试退避和 `RetryExhaustedError`，但 `partial_output_visible` 信息会通过 trace metadata 暴露给上游。
- `core/loop.py` 整体重写：删除 `model_events.append` 二次缓冲。`content_delta` 收到即 yield `assistant_delta`；`tool_call_completed` 收到即 yield `tool_call_ready`；新增的 `tool_call_delta` 事件被透传为 `AgentEvent(type="tool_call_delta")`。retry transition 现在携带 `partial_output_visible` 标记。`max_output_tokens_escalate` 后立即把截断 assistant 持久化（用户已经看到）。
- `core/stream_events.py` 加入 `tool_call_delta` 事件类型。
- `ui/cli/terminal/markdown_rendering.py` 新增：GFM 表格解析、列宽分配（min / ideal / 比例缩放）、hard wrap、纵向 fallback、safety margin、ANSI 感知宽度测量（用 `wcwidth`）。
- `ui/cli/terminal/stream_session.py` 重写：新增 `StreamingMarkdownState`（stable prefix / unstable suffix 算法），`StreamingSession` 用它在动态区复用稳定块的渲染结果；`partial_visible_text` 镜像参考实现只显示最后一个换行前的完整行。`consume_event` 处理新增的 `tool_call_delta`。
- `ui/cli/terminal/static_output.py` 修改：`print_assistant_markdown` 自己打印 `onecode> ` 前缀。
- `tests/test_model_retry.py`、`tests/test_loop.py` 中三处断言旧“隐藏 partial output”语义的测试被重写为新语义。

### 测试结果

- 聚焦测试：`test_async_loop`、`test_async_cli_streaming`、`test_cli_terminal`、`test_loop`、`test_model_retry`、`test_markdown_rendering`、`test_streaming_markdown_state`、`test_loop_realtime_streaming`、`test_openai_compatible_provider`、`test_openai_compatible_provider_streaming`、`test_model_stream_events` 共 124 个测试全部通过，无 warning。
- 全量测试 443 个通过；2 个 pre-existing 失败（`test_bash_tool.test_bash_descriptor_schema_and_prompt` 关于 Tree-sitter、`test_search_tools.test_registry_generates_search_tool_schemas_and_prompts` 关于 prompt 头）和本计划无关，已在 main 分支验证。
- `python -m compileall core services infrastructure ui utils` 通过。

### 手工验证（fake slow model 时序）

用 `asyncio.sleep(0.1)` 在每个 delta 之间停顿的 fake model，实测 `AgentLoop.stream` 消费时序：

- 16ms 收到 `assistant_delta` "hello "
- 125ms 收到 `assistant_delta` "world"
- 235ms 才收到 `assistant_message_completed`

即 consumer 在 provider 还没完成下一次 yield 时就拿到上一段 delta，证实真正实时流式。

### 仍保留的限制

- 取消时 `partial_output_visible` 已经写入 `StreamBuffer.cancelled_partial`，但 CLI runtime 还没有把中断的 partial text 写回 `MessageStore`。当前保留 partial text 在 scrollback，transcript 不被污染。这与计划第 9 阶段“第一版可先只把 partial 输出提交到静态 scrollback”的退路一致。
- `print_assistant_start` 仍存在于 `static_output.py` 中，但已经不再被新代码调用；保留为兼容导出以避免外部用户直接 import 时中断。

## Context and Orientation

OneCode 是 Python code agent runtime。当前 CLI 位于 `ui/cli/`，主循环位于 `core/loop.py`，模型 provider 协议位于 `services/model/`，OpenAI-compatible provider adapter 位于 `infrastructure/providers/chat_completions.py`。

本计划使用以下术语：

“provider stream” 指模型服务端返回的流式响应。当前 OneCode 的 OpenAI-compatible adapter 在 `infrastructure/providers/chat_completions.py` 中使用 async HTTP transport 读取 JSON lines，并把 provider 私有字段转成 provider-neutral 的 `ModelStreamEvent`。

“ModelStreamEvent” 是 OneCode 内部的模型流事件，定义在 `services/model/stream.py`。其中 `content_delta` 表示模型新增了一小段 assistant 文本，`tool_call_delta` 表示工具调用参数正在增长，`tool_call_completed` 表示工具调用 JSON 已完整可解析，`message_completed` 表示一次 assistant message 完成。

“AgentEvent” 是 runtime 发给 CLI 的事件，定义在 `core/stream_events.py`。其中 `assistant_delta` 是 CLI 可以显示的 assistant 文本增量，`tool_call_ready`、`tool_started`、`tool_result` 用于工具展示，`completed` 表示当前用户 turn 结束。

“attempt” 指一次向 provider 发起的模型请求。当前 retry runner 为了隐藏可恢复错误，会把一个 attempt 的所有 `ModelStreamEvent` 缓存在内存中，直到该 attempt 成功结束才释放给 loop。本计划要求删除这层缓存。

“实时流式输出” 指普通 assistant 文本在 provider 请求尚未结束时直接进入 CLI 动态区。它不是可选兼容模式，而是本计划完成后的唯一主路径。实时流式不能撤回已经显示的文本，所以重试和截断恢复需要明确 UI 语义。

“临时 streaming UI state” 指仅用于屏幕显示、尚未写入 `MessageStore` 的状态。参考实现中它们是 `streamingText`、`streamingToolUses` 和 `streamingThinking`。OneCode 当前相近结构是 `ui/cli/terminal/stream_session.py` 中的 `StreamBuffer.text`、`active_tool_ids` 和 `current_tool_label`。

当前 OneCode 流式路径如下：`OpenAICompatibleChatCompletionsClient.stream()` 产生 `ModelStreamEvent.content_delta`；`ModelRetryRunner.stream()` 缓冲这些事件；`AgentLoop._run_loop_async()` 收集这些事件到 `model_events`；若没有 recovery，loop 再把 `content_delta` 转成 `AgentEvent(type="assistant_delta")`；`StreamingSession.consume_event()` 把 `assistant_delta` 追加到 `StreamBuffer.text`；`render_preview_ansi()` 用 Rich Markdown 渲染当前完整文本。

参考实现的相关文件如下：

`docs/references/ui/screens/REPL.tsx` 管理流式状态。它有 `streamingText`、`streamingToolUses`、`streamingThinking`，并把 `visibleStreamingText` 传给 `Messages`。它只显示最后一个换行之前的 streaming text，减少逐字符抖动。取消时，它把已经显示的 `streamingText` 保存为 assistant message。

`docs/references/ui/utils/messages.ts` 的 `handleMessageFromStream()` 是 stream event reducer。它把 `text_delta` 追加到 `streamingText`，把 `input_json_delta` 追加到对应 streaming tool use 的 `unparsedToolInput`，在完整 message 到达时清空 streaming text 并 append final message。

`docs/references/ui/components/Messages.tsx` 把正式 messages 和临时 streaming state 合成屏幕输出。它把 `streamingText` 放在消息列表尾部，并用 `StreamingMarkdown` 渲染；它把 still-streaming tool use 转成 synthetic assistant message 参与 UI 渲染，但不污染正式消息列表；它也显示近期 streaming thinking。

`docs/references/ui/components/Markdown.tsx` 提供普通 Markdown 和 streaming Markdown。普通 Markdown 先判断文本是否含 Markdown 语法，若没有则跳过 lexer；有语法则 lex 并缓存 token。Streaming Markdown 使用 stable prefix / unstable suffix：已完成的块成为稳定前缀，不再重复解析；最后一个正在增长的块作为不稳定尾部，每次 delta 只重渲染它。

`docs/references/ui/components/MarkdownTable.tsx` 提供 Markdown table 专用渲染。它接收 marked 产生的 `Tokens.Table`，把每个单元格 token 格式化成 ANSI 字符串，然后用去除 ANSI 后的显示宽度计算列宽。它先计算每列的最小宽度，最小宽度来自该列所有单元格中最长单词的显示宽度，且不低于 3；再计算理想宽度，理想宽度是未换行内容的显示宽度。若总理想宽度能放进终端，就使用理想宽度；若放不下但总最小宽度能放进终端，就按每列 overflow 比例分配剩余空间；若连最小宽度都放不下，则按比例缩小并允许 hard wrap。它还会计算每行换行后的最大高度，若任何行超过 4 行，就放弃横向表格，转成 key-value 纵向格式。最终渲染前，它检查最大行宽是否超过 `terminalWidth - SAFETY_MARGIN`，若接近边界也回退到纵向格式。这些机制都适合 OneCode，因为 OneCode CLI 同样在终端中渲染 Markdown，且当前 Rich Markdown table 已暴露 `table.header` 样式问题。

## Plan of Work

第一阶段是重写测试保护，明确新目标是删除旧两层缓冲。新增或扩展 `tests/test_async_loop.py`，构造 fake model client，让它在两个 `content_delta` 之间 `await asyncio.sleep()`，并记录消费 `AgentLoop.stream()` 时事件到达时间。新测试必须证明第一段 `assistant_delta` 在 provider 继续等待、`message_completed` 尚未产生时已经到达。测试名称建议为 `test_loop_yields_content_delta_before_message_completed`。同时删除或重写旧的“retryable provider error hides partial delta”测试；新语义应断言 partial delta 已经显示，随后 CLI 或 loop 发出 retry transition，最终 message store 只持久化成功 attempt 的最终 assistant message 或显式标记的 interrupted partial message。

第二阶段是重写 `services/model/retry.py`，删除 attempt 级完整事件缓冲。移除 `buffer: list[ModelStreamEvent]`、`buffer.append(event)` 和成功后 `for event in buffer: yield event` 的结构。`ModelRetryRunner.stream()` 应直接 `async for event in operation(): yield event`。如果 provider 在已经 yield 过部分文本后抛出 retryable `ProviderError`，retry runner 应记录错误、调用 `on_retry`、等待退避时间，然后启动新的 attempt。它不再尝试隐藏已 yield 的文本。非文本事件也不需要为了隐藏失败 attempt 而完整缓存；工具执行安全由 `core/loop.py` 控制，loop 只有在完整 `message_completed` 和合法 `tool_call_completed` 之后才会执行工具。

第三阶段是重写 `core/loop.py` 的模型事件消费，删除 `model_events` 二次缓冲和 replay。当前 loop 总是 `model_events.append(model_event)`，随后统一 replay；这段结构应删除。新的 loop 应在收到 `ModelStreamEvent.type == "content_delta"` 时立即 yield `AgentEvent(type="assistant_delta", text=model_event.text)`。loop 仍需要维护少量局部状态：`completed_message` 保存最终 `message_completed`；`seen_tool_calls` 或 `completed_tool_calls` 保存完整工具调用；`usage` 更新仍在 final event 后处理；`final_text` 由 `message_completed.final_text` 或 provider accumulator 提供。因为不再 replay，不能出现“先实时输出、结束后再输出一遍”的重复文本。

第四阶段是定义重试、context limit、max-output recovery 的新用户可见语义，并删除依赖隐藏 partial output 的旧测试。对于 retryable provider error，已经显示的文本不能撤回。当 retry 发生时，yield 一个 `AgentEvent(type="transition", transition="rate_limit_retry", metadata={"partial_output_visible": True})`，CLI 显示一行短提示，例如 `! provider stream interrupted; retrying`。如果 retry 后成功，继续显示后续文本；最终 transcript 默认只写成功 attempt 的 assistant message，失败 attempt 的 partial output 只存在于 UI scrollback 和 trace metadata 中。对于 context limit，若没有任何文本输出，保持 reactive compact；若已经输出文本后才发现 context limit，应显示 transition，停止隐藏式 compact retry，并把恢复行为记录为可见中断。对于 max-output interruption，删除“先隐藏截断输出、升级 max_output_tokens、重试同一消息链”的旧语义；新语义是保存截断 assistant，追加 continuation prompt，显示 `max_output_tokens_recovery` transition，然后继续生成。相关旧测试如“max-output escalation before persisting truncated output”应删除或改写为“截断内容已经可见且被持久化为 interrupted assistant，后续 continuation 继续输出”。

第五阶段是让 CLI 的动态区渲染参考 `StreamingMarkdown`。在 `ui/cli/terminal/stream_session.py` 中新增 `StreamingMarkdownState`。它接收完整 `buffer.text`，维护 `stable_prefix_length`、`stable_rendered_lines` 和 `last_input_text`。每次渲染时，它只分析从 `stable_prefix_length` 开始的 suffix。Python 中没有 `marked.lexer()`，第一版使用保守 block boundary detector：只有在看到至少一个完整块结束时才推进 stable boundary。块结束规则为：两个连续换行结束段落；fenced code block 必须看到闭合 ``` 后再稳定；Markdown 表格必须看到表头分隔行和后续非表格行后再稳定；列表项可以在出现下一个空行后稳定。若无法确定，保持为 unstable suffix。这样最多牺牲性能，不会错误冻结未完成 Markdown。

第六阶段是修复 Markdown 表格。先做止血修复：在 `ui/cli/theme.py` 的 `_base_palette()` 中添加 Rich Markdown 表格可能用到的样式名，例如 `table.header` 和必要的 `markdown.*` 样式。具体需要通过测试复现：让 `print_assistant_markdown()` 渲染一个包含 Markdown 表格的字符串，当前应触发 `table.header` 错误；修复后应正常输出。若 Rich 需要更多内置样式，应在测试输出中补齐最小集合，不要把 theme 改回 `inherit=True`，因为现有设计明确要求 CLI theme 只定义可控前景色且不设背景。

第六阶段的最终目标是实现 OneCode 专用 Markdown table renderer，参考 `MarkdownTable.tsx`。当前 Python CLI 可以先不引入完整 Markdown parser，而是在 `StreamingMarkdownState` 或新的 `ui/cli/markdown_rendering.py` 中识别 GFM 风格表格块。GFM 表格是至少三行的文本块：第一行是 header，第二行是由 `---`、`:---`、`---:`、`:---:` 和竖线组成的分隔行，后续一行或多行是 row。renderer 应把表格块解析成 header、align 和 rows，然后用 Python 的 `wcwidth` 或 Rich 已有测量能力计算显示宽度。若项目没有 `wcwidth` 依赖，应优先检查 Rich 是否提供可复用测量函数；不要用 `len()` 计算宽度，因为中文、emoji 和 ANSI 样式会导致终端列宽错误。

OneCode table renderer 应采用参考实现的约束：保留 `SAFETY_MARGIN = 4` 的概念；设置 `MIN_COLUMN_WIDTH = 3`；设置 `MAX_ROW_LINES = 4`；先按 longest word 计算最小列宽，再按完整内容计算理想列宽；理想宽度可容纳时使用理想宽度；最小宽度可容纳时按 overflow 比例分配剩余空间；最小宽度也不可容纳时允许 hard wrap；若任一 row 包装后超过 4 行，或者最终任一 table line 接近终端边界，则转为纵向 key-value 格式。横向格式应使用 box drawing 字符，例如 `┌─┬┐`、`├─┼┤`、`└─┴┘`，并保证每一行的显示宽度不超过动态区 width。纵向格式应按每一行数据输出 `header: value`，多行 value 用两个空格缩进，并在多行记录之间用一条不超过终端宽度的 `─` 分隔。

专用 table renderer 的第一版可以只处理纯文本单元格和简单 inline emphasis；遇到复杂 Markdown token 时可以降级为纯文本或 Rich Markdown。重要的是 renderer 必须不抛出样式错误、不截断内容、不造成动态区闪烁。后续如果引入 Python Markdown parser，可以把表格解析替换为 parser token，但保留列宽分配和纵向回退策略。

第七阶段是改进 TTY assistant 前缀。当前 `ui/cli/terminal/repl.py` 导入了 `print_assistant_start()` 但 `_run_turn()` 没调用，`print_assistant_markdown()` 注释仍假设前缀已提前打印。实现时应确保每个 final assistant turn 在静态区以 `onecode>` 开头。动态区状态行已经显示 `onecode>`。推荐修改 `commit_final()` 或 `print_assistant_markdown()`，让最终提交负责打印前缀，避免调用者忘记。批处理路径 `ui/cli/batch.py` 已在看到第一个 delta 时打印 `onecode> `，需要保持兼容。

第八阶段是工具调用流式展示。当前 provider adapter 已产生 `ModelStreamEvent.tool_call_delta`，但 `core/loop.py` 不把它转换成 `AgentEvent`。参考实现会用 `streamingToolUses` 保存正在增长的工具 input JSON，并构造 synthetic UI message。OneCode 第一版可以新增 `AgentEventType` 中的 `tool_call_delta`，metadata 包含 tool index、id、name、arguments_delta_chars 或 partial preview。CLI 动态区不需要显示完整 JSON，只显示 `tool input: <name> ...` 或工具名一行；真正工具执行仍等待 `tool_call_completed` 和 executor 权限链。

第九阶段是取消语义。参考实现取消时会保留已流出的 text。OneCode 当前 `StreamingSession.run()` 在 Esc 后取消 feeder 并打印 `已取消`，但没有把 partial assistant 写回 `MessageStore`。live mode 下应新增明确行为：如果用户取消且 `StreamBuffer.text.strip()` 非空，调用 loop 或 runtime 提供的取消接口追加一个 assistant message，metadata 标记 `interrupted_by_user=True`；随后追加 user interruption message 或至少在 transcript 中记录取消事件。若实现取消写回需要更大改动，第一版可先只把 partial 输出提交到静态 scrollback，并在 `MessageStore` 不写入，测试中明确记录这是剩余限制；但最终目标应与参考实现一致。

第十阶段是文档更新。更新 `docs/design-docs/cli-architecture.md` 与 `docs/design-docs/cli-message-rendering-architecture.md`，说明 CLI 已删除旧两层缓冲，普通 assistant text 是实时流式输出；动态区使用 stable block rendering；最终消息仍来自 `MessageStore`；临时 streaming state 不是上下文事实来源。不要新增长期配置开关来恢复旧 buffered 行为；如果实现期间使用临时调试开关，完成本计划前必须移除。

## Concrete Steps

所有命令均在仓库根目录 `D:\study\OneCode` 运行。

先运行当前相关测试，确认基线：

    uv run python -m pytest tests/test_async_loop.py tests/test_async_cli_streaming.py tests/test_cli_terminal.py -q

预期当前测试应通过。如果失败，先确认失败是否与本计划范围相关；不要在实现 live streaming 前修改无关失败。

添加实时流式单元测试。建议先在 `tests/test_async_loop.py` 中新增 fake model client 测试，使用 `asyncio.Event` 或时间记录证明第一段 delta 在 `message_completed` 前被消费。这个测试应成为默认行为测试，不需要启用任何 policy。

修改 `services/model/retry.py`，删除完整 attempt buffer。重写或删除依赖旧 buffer 的测试。新增测试覆盖：文本 delta 能立即 yield；attempt 抛出 retryable error 后已经 yield 的文本不会被撤回；retry transition 能被上层观察；retry 后成功的 final message 仍能完成 loop。

修改 `core/loop.py`，删除 `model_events` 二次缓冲，让它不再等待完整模型响应才 yield `assistant_delta`。保持 tool call execution 和 `message_store.append_assistant()` 仍发生在 `message_completed` 后。为 loop 增加测试，证明 `assistant_delta` 在 fake provider 尚未完成时到达；最终 `MessageStore` 仍只有最终 assistant message，除非测试场景是用户取消或 max-output interruption。

删除或重写旧 recovery 测试。重点搜索并处理这些语义：失败 attempt 的 partial delta 不应出现、max-output escalation 不持久化截断 assistant、loop 在 recovery 前不向 UI flush delta。新测试应改为断言 partial delta 已经可见，transition 明确提示 retry 或 recovery，message store 不混入未标记的失败 attempt 内容。

修改 `ui/cli/terminal/stream_session.py`，新增 streaming Markdown state。保留 `_THROTTLE_INTERVAL = 0.05`，但渲染时优先复用 stable rendered lines。新增测试覆盖：普通段落多次追加时 stable boundary 前进；未闭合 code fence 不稳定；闭合 code fence 后稳定；长文本只返回尾部 `_PREVIEW_MAX_LINES`。

修改 `ui/cli/theme.py`，补足 Rich Markdown 表格样式。新增测试在 `tests/test_cli_terminal.py` 中渲染包含 Markdown 表格的 assistant 文本，断言不会出现 `table.header` parse error。

新增专用表格 renderer 的测试。建议创建或扩展 `tests/test_cli_terminal.py`，覆盖四类表格：宽终端下普通三列表格应横向渲染且每行不超过 width；窄终端下长单词表格应 hard wrap 或转纵向格式；某一 row 换行超过 `MAX_ROW_LINES` 时应转纵向格式；包含中文或 ANSI 样式的单元格应按显示宽度而不是 Python 字符数对齐。若第一版没有完整 ANSI 保留能力，测试应至少覆盖中文宽字符，并在本计划的 Outcomes 中记录 ANSI-aware wrapping 的剩余差距。

修改 TTY final commit 前缀。推荐让 `print_assistant_markdown(text)` 自己打印 `onecode> ` 前缀，或新增 `print_assistant_commit(text)` 替代它，并更新调用点。测试应覆盖 TTY `StreamingSession` final output 包含 `onecode>`。

如果实现 tool call delta UI，修改 `core/stream_events.py` 增加 `tool_call_delta`，修改 `core/loop.py` 从 `ModelStreamEvent.tool_call_delta` 转发，修改 `stream_session.consume_event()` 更新 `current_tool_label` 或新增 `current_tool_input_label`。测试应覆盖 tool call delta 不触发工具执行，只有 `tool_call_completed` 后才进入 executor。

最后运行：

    uv run python -m pytest tests/test_async_loop.py tests/test_async_cli_streaming.py tests/test_cli_terminal.py tests/test_loop.py -q
    uv run python -m pytest tests -q
    uv run python -m compileall core services infrastructure ui

手工验证时，配置一个可用 provider 后运行：

    uv run python -m ui.cli.app

输入：

    请用 8 行逐行解释什么是流式输出，每行后面停顿一下再继续。

用户应看到动态区逐行出现文本，而不是等待全部生成后一次性出现。若 provider 不按 prompt 真实停顿，仍应看到 token 到达时动态区逐步更新。输入一个包含 Markdown 表格的 prompt：

    输出一个三列表格，列名是 feature、current、target。

不应再出现 `Failed to get style 'table.header'`。

## Validation and Acceptance

本计划完成时必须满足以下可观察行为。

默认行为下，fake model 测试能证明 `AgentLoop.stream()` 在 `message_completed` 前 yield 至少一个 `assistant_delta`。测试不能只检查最终文本；必须检查事件顺序或通过 synchronization primitive 证明 provider 尚未结束。

旧 buffered 行为测试不再作为验收要求。任何断言 retryable provider error 不显示失败 attempt partial output 的测试都应删除或重写。新的验收是：partial output 可以显示；retry transition 必须可见；最终 `MessageStore` 不应把失败 attempt 的 partial text 当作普通成功 assistant message 写入，除非它带有明确 interrupted/retry metadata。

CLI 动态区能显示真实增量文本。`tests/test_async_cli_streaming.py` 或 `tests/test_cli_terminal.py` 应覆盖 `StreamingSession.consume_event()` 和 `render_preview_ansi()` 的增量行为。手工运行 CLI 时，长回复应逐步出现。

Markdown 表格不再触发 Rich style 错误。新增测试应直接渲染 Markdown table，并期待无异常。完整实现后，表格还必须在窄终端可读：给定宽度 40 的三列表格，输出不得有任何一行显示宽度超过 40；如果横向表格无法满足这个条件，应自动转成 key-value 纵向格式。

取消行为被明确处理。若实现了 partial assistant 写回，测试应证明取消后 transcript 或 message store 中有一条 metadata 标记为 interrupted 的 assistant message。若第一版暂不写回，文档和测试必须证明 UI 至少保留已经提交到 scrollback 的 partial text，并在本 ExecPlan 的 Outcomes 中记录剩余差距。

工具安全边界不变。即使新增 tool call delta UI，也必须证明工具 handler 只在完整 `tool_call_completed` 后由 `RegistryToolExecutor` 执行，仍经过 validation、guard、permission policy 和 hooks。

## Idempotence and Recovery

本计划的实现应是可重复的，但不要求保留旧 buffered 兼容路径。新增 tests 可以反复运行，不应依赖真实网络或真实 provider。手工验证需要真实 provider API key，但自动测试必须使用 fake async model client 或 fake transport。

如果实时流式实现过程中出现复杂 recovery bug，不要恢复旧两层缓冲作为长期方案。可以临时在分支内缩小 recovery 范围，但最终必须删除旧 buffer 代码和旧 buffer 语义测试。不要为了让实时流式简单而移除 retry、context-limit compact、max-output recovery 或 permission 边界；应把它们改成可见 recovery 语义。

如果 Markdown stable boundary detector 出现错误渲染，回退策略是把更多文本留在 unstable suffix，而不是提前冻结可能未完成的块。保守 detector 的最坏结果只是性能收益较小，不应造成错误显示。

如果 Rich theme 需要补多个内置样式，逐个通过测试补充最小前景色，不要设置背景色，不要引入一整套不可控 theme 继承，除非 Decision Log 记录了为什么 `inherit=True` 更合适并通过 light/dark 终端测试。专用 table renderer 如果出现宽度计算错误，优先回退到纵向格式；纵向格式比越界横向表格更可读，也更不容易触发动态区重绘问题。

## Artifacts and Notes

当前关键代码证据如下。

Provider 已产生文本 delta：

    infrastructure/providers/chat_completions.py
    content = delta.get("content")
    if isinstance(content, str) and content:
        final_text_parts.append(content)
        yield ModelStreamEvent.content_delta(content)

Retry runner 当前缓存 attempt：

    services/model/retry.py
    buffer: list[ModelStreamEvent] = []
    async for event in operation():
        buffer.append(event)
    ...
    for event in buffer:
        yield event

Loop 当前二次缓存 model events：

    core/loop.py
    async for model_event in self.model_retry_runner.stream(...):
        model_events.append(model_event)
    ...
    for model_event in model_events:
        if model_event.type == "content_delta":
            yield AgentEvent(type="assistant_delta", text=model_event.text)

参考实现直接累加 streaming text：

    docs/references/ui/utils/messages.ts
    case 'text_delta': {
      const deltaText = message.event.delta.text
      onUpdateLength(deltaText)
      onStreamingText?.(text => (text ?? '') + deltaText)
      return
    }

参考实现只显示完整行：

    docs/references/ui/screens/REPL.tsx
    const visibleStreamingText =
      streamingText && showStreamingText
        ? streamingText.substring(0, streamingText.lastIndexOf('\n') + 1) || null
        : null

参考实现把 streaming text 放在消息列表末尾：

    docs/references/ui/components/Messages.tsx
    {streamingText && !isBriefOnly && ... <StreamingMarkdown>{streamingText}</StreamingMarkdown>}

参考实现的 streaming Markdown 核心思想：

    docs/references/ui/components/Markdown.tsx
    const boundary = stablePrefixRef.current.length
    const tokens = marked.lexer(stripped.substring(boundary))
    ...
    stablePrefixRef.current = stripped.substring(0, boundary + advance)
    const stablePrefix = stablePrefixRef.current
    const unstableSuffix = stripped.substring(stablePrefix.length)

参考实现的 table renderer 核心思想：

    docs/references/ui/components/MarkdownTable.tsx
    const SAFETY_MARGIN = 4
    const MIN_COLUMN_WIDTH = 3
    const MAX_ROW_LINES = 4
    ...
    if (totalIdeal <= availableWidth) {
      columnWidths = idealWidths
    } else if (totalMin <= availableWidth) {
      columnWidths = minWidths plus proportional extra space
    } else {
      needsHardWrap = true
      columnWidths = proportionally scaled minimum widths
    }
    ...
    if (maxRowLines > MAX_ROW_LINES) {
      return vertical key-value format
    }
    if (maxLineWidth > terminalWidth - SAFETY_MARGIN) {
      return vertical key-value format
    }

## Interfaces and Dependencies

`services/model/retry.py` should no longer expose or implement a buffering mode. `ModelRetryRunner.stream()` should keep the same public shape but stream events through immediately:

    class ModelRetryRunner:
        async def stream(
            self,
            operation: Callable[[], AsyncIterator[ModelStreamEvent]],
            *,
            on_retry: Callable[[ProviderError, RetryDecision], Awaitable[None] | None] | None = None,
        ) -> AsyncIterator[ModelStreamEvent]:
            ...

The implementation must not contain a list that accumulates all `ModelStreamEvent` objects for a successful attempt before yielding them. Small local state for retry counters, logging, and whether any event was already yielded is acceptable.

`core/loop.py` should continue to expose:

    async def stream(self, prompt: str, *, attachments: Iterable[dict[str, Any]] | None = None) -> AsyncIterator[AgentEvent]

No UI-specific API should be added to `AgentLoop`. Real-time text streaming should be the normal provider-neutral behavior, not a CLI-only branch and not a configurable compatibility mode.

`core/stream_events.py` may add:

    "tool_call_delta"

Only if Milestone 8 is implemented. The event metadata should stay provider-neutral and bounded. Do not include full unbounded partial JSON in trace or UI by default; use length and short preview.

`ui/cli/terminal/stream_session.py` should add a local renderer helper, for example:

    class StreamingMarkdownState:
        def update(self, text: str) -> None: ...
        def render_lines(self, *, width: int) -> list[str]: ...

This helper belongs in CLI because it is a rendering optimization, not model context. It should not read files, execute tools, or mutate `MessageStore`.

`ui/cli/theme.py` should continue using foreground-only styles. Any new styles for Rich Markdown tables must avoid background colors unless a later design decision explicitly revises the terminal theme model.

`ui/cli/terminal/stream_session.py` or a new `ui/cli/markdown_rendering.py` should expose a small table rendering helper. Suggested shape:

    @dataclass(frozen=True)
    class MarkdownTableBlock:
        headers: tuple[str, ...]
        alignments: tuple[str, ...]
        rows: tuple[tuple[str, ...], ...]

    def parse_markdown_table_block(text: str) -> MarkdownTableBlock | None: ...

    def render_markdown_table_block(
        block: MarkdownTableBlock,
        *,
        width: int,
        safety_margin: int = 4,
        min_column_width: int = 3,
        max_row_lines: int = 4,
    ) -> list[str]: ...

The helper should return already wrapped terminal lines and must not write to stdout. It belongs in CLI rendering code because it is a presentation concern. If later used by both static commit and dynamic preview, keep it independent of prompt_toolkit so tests can call it directly.

## Revision Notes

- 2026-06-15 / Codex: Initial ExecPlan created after reading `PLANS.md`, OneCode streaming code, and reference UI files. The plan separates runtime event release from Markdown rendering, records why current buffering exists, and describes how to migrate reference mechanisms without importing React/Ink assumptions into the Python CLI.
- 2026-06-16 / Codex: Updated after reading `docs/references/ui/components/MarkdownTable.tsx`. The plan now treats Rich theme table style as a short-term fix and a width-aware dedicated table renderer as the target design, including column width allocation, hard wrapping, vertical fallback, safety margin, and tests for narrow terminal behavior.
- 2026-06-16 / Codex: Updated per user direction to delete the old two-layer buffering implementation instead of preserving it behind a compatibility policy. The plan now requires removing retry-runner attempt buffering, removing loop-level `model_events` replay buffering, and deleting or rewriting tests that assert hidden partial output.
