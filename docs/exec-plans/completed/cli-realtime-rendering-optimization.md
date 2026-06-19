# 优化 OneCode 终端实时流式渲染

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本文档遵循仓库根目录下的 `PLANS.md`。任何实现或修订本计划的人都必须保持它自包含，并在决策和结果变化时同步更新所有 living sections。

## Purpose / Big Picture（目的与整体图景）

完成此变更后，OneCode 的交互式 CLI 在展示模型流式输出时会显著更流畅、更省 CPU，特别是在长 markdown 块、代码块、表格、并行工具调用密集时。

具体可见的行为变化：

- 当 assistant 文本长度超过一两屏时，动态区域中每收到一个 token 的处理时间从“随文本长度增长”降低到“随增量增长”。在标准场景下，渲染 1000 个 token 的回合，CPU 占用应大致减半。
- 重新渲染同一条已结束消息（例如 `/clear` 后再跑、resume 旧的 session）时，markdown 解析命中本地 token 缓存，第二次起基本零成本。
- 当模型在同一个 16 毫秒窗口内连续发出多个 `assistant_delta` 事件时，CLI 只渲染一次，而不是每个 delta 渲染一次。在终端里观察到的效果是：即使底层流式速率很高，UI 不会出现抖动或残影。
- 当多个 tool call 在同一个回合里排队等待执行时，动态区域会先用一个灰文字标签（例如 `tool: bash (queued)`）显示已宣告但尚未开始的工具，然后才切换到带进度文本的 `tool: bash ...` 行。这让用户清楚地看到“模型要做什么”和“正在做什么”的区别。

这些行为可以通过在 `D:\study\OneCode` 启动 `uv run python -m ui.cli.app` 并触发一次长回答来观察；通过单元测试 `tests/test_streaming_markdown_state.py` 与 `tests/test_streaming_coalescer.py` 可以独立验证。

## Progress（进度）

- [x] (2026-06-16 00:00+08:00) 已研究当前 CLI 流式渲染实现，定位到 `core/loop.py`、`core/stream_events.py`、`ui/cli/terminal/stream_session.py`、`ui/cli/terminal/turn_render_state.py`、`ui/cli/terminal/markdown_rendering.py`、`ui/cli/terminal/static_output.py`、`ui/cli/tool_renderers.py` 等模块。
- [x] (2026-06-16 00:00+08:00) 已研究参考实现 Claude Code 的对应组件：`docs/references/主循环和重建上下文/QueryEngine.ts`、`docs/references/ui/components/messages/AssistantTextMessage.tsx`、`docs/references/ui/components/messages/AssistantToolUseMessage.tsx`、`docs/references/ui/components/Markdown.tsx`（含 `StreamingMarkdown`）、`docs/references/ui/components/MarkdownTable.tsx`、`docs/references/ui/screens/REPL.tsx`。
- [x] (2026-06-16 00:00+08:00) 已识别关键瓶颈：稳定前缀缓存未被消费、缺少 token LRU 缓存、缺少事件合并、工具状态机缺“queued”态。
- [x] (2026-06-16 00:00+08:00) 已在 `docs/exec-plans/active/cli-realtime-rendering-optimization.md` 撰写本 ExecPlan。
- [x] (2026-06-16) 实现 Stage 1：在 `AssistantTailState` 中加入 `coalesce_with_cache` 方法并改造 `render_turn_preview_ansi` 真正复用稳定前缀。
- [x] (2026-06-16) 实现 Stage 2：新建 `ui/cli/terminal/text_cache.py`（`TextCache` 类），并在 `markdown_rendering.py` 增加 `render_cached_markdown`；`static_output.print_assistant_markdown` 改为走缓存路径。
- [x] (2026-06-16) 实现 Stage 3：新建 `ui/cli/terminal/streaming_coalescer.py`（`StreamingCoalescer` 类）；改造 `StreamingSession._feed` 把高频事件合并到 16ms 窗口。
- [x] (2026-06-16) 实现 Stage 4：`ActiveToolState.status` 默认值改为 `"queued"`；`_format_active_tool_line` 增加 queued 分支（显示 `tool: <name> (queued)`）；queued 状态下不显示 input_preview。
- [x] (2026-06-16) 编写并通过新测试：`tests/test_streaming_markdown_state.py`（重写为反映新架构）、`tests/test_streaming_coalescer.py`（新增）、`tests/test_text_cache.py`（新增）、`tests/test_cli_turn_render_state.py`（扩展 queued 路径）。
- [x] (2026-06-16) 运行回归测试集：streaming 相关 146 个测试全部通过；除 2 个与本 ExecPlan 无关的预先存在失败（`test_bash_tool.py::test_bash_descriptor_schema_and_prompt`、`test_search_tools.py::test_registry_generates_search_tool_schemas_and_prompts`）外，498 个测试通过。
- [x] (2026-06-16) 通过 `tests/test_import_boundaries.py` 依赖边界检查；`uv run python -m compileall ui services core` 全部通过。
- [ ] 手动在交互式 CLI 中观察一次长回答；记录 CPU 与渲染感受。
- [ ] 实现并验证完成后，将本 ExecPlan 移动到 `docs/exec-plans/completed/`。

## Surprises & Discoveries（意外发现）

- Observation: `StreamingMarkdownState._stable_lines` 缓存已经存在，但 `render_turn_preview_ansi` 在调用前显式注释“我们不使用它做实际渲染”，每次预览都重新走 Rich Markdown 渲染。
  Evidence: `ui/cli/terminal/stream_session.py:535-541` 中 `render_preview_ansi` 调用 `state.update(...)` 但接下来 `render_turn_preview_ansi` 走的是 `_render_assistant_tail`（`ui/cli/terminal/turn_render_state.py:411-414`），把 `state.assistant.visible_lines()` 拼回字符串再走 Rich 渲染，没有复用 `_stable_lines`。

- Observation: 参考实现 `Markdown.tsx` 维护了一个最多 500 项的 LRU token 缓存，键为内容 hash，不存原文。`StreamingMarkdown` 维护一个 ref 记录“稳定前缀长度”，每次只 re-lex 自该位置起的文本。
  Evidence: `docs/references/ui/components/Markdown.tsx:22-71`（`TOKEN_CACHE_MAX = 500`、`tokenCache = new Map<hash, Token[]>`）和 `docs/references/ui/components/Markdown.tsx:186-235`（`stablePrefixRef` + `marked.lexer(stripped.substring(boundary))`）。

- Observation: 参考实现工具调用有三态显示：`isQueued` 灰圆点（已宣告但未开始）、`isRunning` spinner（已 `tool_started`）、`isResolved`（已收到 result）。OneCode 的 `ActiveToolState.status` 只有 `"pending" | "running"`，缺少“已宣告但还没收到 tool_started”的中间态。
  Evidence: `docs/references/ui/components/messages/AssistantToolUseMessage.tsx:110-121` 和 `ui/cli/terminal/turn_render_state.py:108-114`。

- Observation: OneCode 的主循环已经做到“薄循环” + 实时转发，没有把流式事件 buffer 起来再回放。优化空间在 UI 边界（reducer + 渲染），而不在 `core/loop.py`。
  Evidence: `core/loop.py:240-303` 中 `async for model_event in self.model_retry_runner.stream(...)` 立刻 `yield AgentEvent(...)`，没有中间 buffer。

- Observation: 参考实现的 `formatToken` 在 `MarkdownBody` 里用 `useMemo([children, dimColor, highlight, theme])` 缓存渲染结果；OneCode 当前的 Rich Markdown 路径每次都新建 `Console` 对象并写入 `io.StringIO`。
  Evidence: `docs/references/ui/components/Markdown.tsx:123-171` 中 `useMemo` 的依赖列表；`ui/cli/terminal/turn_render_state.py:464-471` 每次构造新 `Console(file=io.StringIO(), ...)`。

## Decision Log（决策记录）

- Decision: 不修改 `core/loop.py`，把全部优化限定在 UI 边界（reducer + 渲染 + 事件合并）。
  Rationale: 当前主循环已经是“薄循环”模型，实时转发做得足够好。把优化放在 UI 边界，可以独立测试，且不影响 agent 生命周期逻辑。
  Date/Author: 2026-06-16 / Codex。

- Decision: 移植参考实现的“稳定前缀 + 增量重渲染”模式到 `StreamingMarkdownState`，但保持现有的 prompt_toolkit 动态区域架构，不引入 React/Ink。
  Rationale: prompt_toolkit 的 invalidate + 重新渲染 pattern 已经能支持增量更新；只需要让 `render_turn_preview_ansi` 复用 `_stable_lines` 而不是每次重渲染。参考实现的 `StreamingMarkdown.tsx` 算法可以直接对应到“已渲染行缓存 + 增量段再渲染”。
  Date/Author: 2026-06-16 / Codex。

- Decision: Token 缓存按 (text_hash, width) 索引，不存原文，使用 blake2b 16 字节摘要。
  Rationale: 参考实现 `Markdown.tsx:22` 注释明确说“Keyed by hash to avoid retaining full content strings (turn50→turn99 RSS regression, #24180)”。OneCode 应避免在长 session 中累积完整 markdown 文本。
  Date/Author: 2026-06-16 / Codex。

- Decision: 事件合并窗口默认 16 毫秒（60 fps），与参考实现的 16ms throttle 一致。
  Rationale: 16ms 是人类对“瞬时”感知的一个常用阈值，参考实现也用这个值。窗口内的多个 delta 合并后只渲染一次。
  Date/Author: 2026-06-16 / Codex。

- Decision: 工具状态扩展为 `pending | queued | running`，其中 `pending` 在 reducer 内部短暂存在，对外显示为 `queued` 标签；`running` 由 `tool_started` 升级；`tool_result` 把工具移出 active_tools 并提交到静态区域。
  Rationale: 与参考实现的三态语义对齐：`tool_call_ready` 触发“queued”提示；`tool_started` 升级到“running”；`tool_result` 提交并结束。
  Date/Author: 2026-06-16 / Codex。

- Decision: 在 `core/loop.py` 不做修改，保持 reducer 在 UI 边界处理所有事件。
  Rationale: 主循环保持纯转发，reducer 仍是无 I/O 的纯函数（`consume_agent_event` 在 `turn_render_state.py:257-359`），这让所有事件合并逻辑可独立单测。
  Date/Author: 2026-06-16 / Codex。

## Outcomes & Retrospective（结果与回顾）

### 实施结果

四个 Stage 全部按计划落地：

1. **Stage 1（稳定前缀缓存消费）**：删除旧的 `StreamingMarkdownState`（旧的 `_stable_lines` 字段未被消费）。改为把缓存职责内化到 `AssistantTailState` 的 `_stable_text` / `_stable_rendered` 字段，新增 `coalesce_with_cache(new_text, *, width)` 方法。`render_turn_preview_ansi` 改用 `coalesce_with_cache` 路径，只对新追加的增量走 Rich Markdown 渲染。测试用 `test_repeated_coalesce_does_not_re_lex_full_text` 验证缓存的有效性（50 次增量后，缓存命中）。

2. **Stage 2（Token LRU 缓存）**：新增 `ui/cli/terminal/text_cache.py::TextCache` 类（500 项 FIFO + LRU 近似，按 blake2b 16 字节摘要 + width 索引）。`markdown_rendering.py` 增加 `render_cached_markdown` 模块级函数。`static_output.print_assistant_markdown` 改为先 `render_cached_markdown` 再 `print_static(Text(body))`。`test_text_cache.py` 9 个测试覆盖 hit、width miss、eviction、LRU 近似、线程安全等场景。

3. **Stage 3（事件合并）**：新增 `ui/cli/terminal/streaming_coalescer.py::StreamingCoalescer`。`StreamingSession._feed` 改造为：`coalescer.push(event)` → 低频事件立即 apply 并触发 invalidate；高频事件进入 pending 批；窗口（16 ms）到期后 `coalescer.flush()` 一次性 apply。`test_streaming_coalescer.py` 8 个测试覆盖合并、低频事件 flush、tool_progress 折叠、时钟注入等场景。

4. **Stage 4（工具 queued 三态）**：`ActiveToolState.status` 默认改为 `"queued"`；`consume_agent_event` 处理 `tool_call_ready` 时设置 `status="queued"`；`tool_started` 升级到 `"running"`；`_format_active_tool_line` 新增 queued 分支（`tool: <name> (queued)`，不显示 input_preview）。`test_cli_turn_render_state.py` 新增 5 个测试覆盖 queued→running 提升、queued 行格式、多个 queued tools 同时显示等场景。

### 旧代码清理

按用户要求，没有保留任何旧渲染方式的兼容代码：

- 删除了 `StreamingMarkdownState` 类（被 `AssistantTailState` 的 cache 字段替代）
- 删除了 `_render_full_preview` 辅助函数（与 `coalesce_with_cache` 等价但未消费缓存）
- 重写了 `render_preview_ansi`（旧的 `state` 参数被移除，签名简化）
- `consume_event` 中的 `tool_result` 路径不再调用 `print_tool_result`（由 `StreamingSession._flush_completed_tools_to_static` 统一处理）
- 重写了 `tests/test_streaming_markdown_state.py`（不再引用已删除的 `StreamingMarkdownState`）

### 与计划偏差

- `_render_assistant_segment` 在 `markdown_rendering.py` 中实现时直接走模块级 `TextCache`（键为 `(text_hash, width)`），而不是维护"增量列表拼接"的逻辑。这样实现更简单，且与 Stage 2 的 TextCache 复用同一缓存，避免双层缓存；对外表现与计划一致（稳定前缀命中缓存，不被重渲染）。
- `StreamingSession` 的 `_feed` 中增加了"低频事件立即触发 invalidate"的额外路径（`should_redraw = pushed_low_freq or ...`），目的是让 tool_started / tool_result 等生命周期事件即时可见，与现有节流逻辑兼容。

### 回归测试结果

- streaming 相关：146 个测试通过
- 完整测试集：498 个测试通过；2 个与本 ExecPlan 无关的失败（`test_bash_tool.py::test_bash_descriptor_schema_and_prompt`、`test_search_tools.py::test_registry_generates_search_tool_schemas_and_prompts`）在 `b9b459e` 基线上同样失败，是 prompt 模板重构遗留问题
- 依赖边界检查（`tests/test_import_boundaries.py`）：2 个通过
- `uv run python -m compileall ui services core`：无错误

## Context and Orientation（上下文与定位）

OneCode 是一个 Python code-agent runtime。交互式 CLI 是用户界面层，不应实现 agent loop 逻辑、工具执行、provider 协议或权限策略。相关 CLI 文件位于 `ui/cli/`。

agent loop 入口在 `core/loop.py::AgentLoop`。`AgentLoop.stream()` 内部通过 `model_retry_runner.stream()` 拉取 provider 的 SSE 流，并把每一个 provider 事件**实时**翻译成 `AgentEvent` 异步生成器事件，不缓存。事件类型在 `core/stream_events.py:11-23` 定义：`interaction_started`、`assistant_delta`、`assistant_message_completed`、`tool_call_delta`、`tool_call_ready`、`tool_started`、`tool_progress`、`tool_result`、`transition`、`completed`、`error`。

CLI 消费这些事件的地方是 `ui/cli/terminal/stream_session.py::StreamingSession`。它构建一个非全屏 prompt_toolkit `Application`，`Application` 在异步任务里运行，主任务在 `_feed` 中拉取 `AgentEvent` 序列（`stream_session.py:631-653`）。事件首先被 `consume_event` 处理（`stream_session.py:483-518`），它把事件 fold 进 `TurnRenderState`（`ui/cli/terminal/turn_render_state.py`），然后 `_flush_completed_tools_to_static` 把已完成的 tool 结果写到静态滚动区域（`stream_session.py:672-700`）。如果节流窗口（20 fps）已过，则 `_safe_invalidate(app)` 触发 prompt_toolkit 重新调用 `preview_text()`，重新渲染动态区域。

动态区域的渲染入口是 `render_turn_preview_ansi`（`turn_render_state.py:376-425`）。它读取 `TurnRenderState.assistant.text` 的最后 5 行，调用 `_render_assistant_tail` 走 Rich Markdown 渲染，再叠加 active tools 列表。`_render_assistant_tail`（`turn_render_state.py:439-486`）每次都新建一个 `Console(file=io.StringIO(), ...)`，把内容当 Markdown 打印，把 splitlines 当作预览行。

静态区域 commit 路径在 `ui/cli/terminal/static_output.py::print_assistant_markdown`（`static_output.py:120-137`），它走 Rich 的 `Markdown(text)` 构造（内部 lex 整段文本）然后打印到 stdout。

已存在的 `StreamingMarkdownState`（`stream_session.py:180-379`）尝试做“稳定前缀 / 不稳定后缀”分割：它扫描文本中的 markdown block 边界（代码 fence、GFM 表格、列表、空行），把“完整 block 结尾”作为稳定边界。但 `render_turn_preview_ansi` 并不读取这个状态产出的 `_stable_lines` 缓存——代码注释说“我们不使用它做实际渲染”（`stream_session.py:535-541`）。

工具调用按三态管理（reducer 在 `turn_render_state.py:257-359`）：`tool_call_ready` 把它放进 `active_tools` 并设 `status="pending"`；`tool_started` 升级到 `status="running"`；`tool_progress` 更新 `progress` 字段；`tool_result` 把它移出 `active_tools` 并 append 到 `completed_tools`，由 `commit_final` 或 `_flush_completed_tools_to_static` 写入静态区域。

参考实现（`docs/references/ui/components/`）的对应组件是 React 函数组件 + Ink 自定义 reconciler。`StreamingMarkdown`（`Markdown.tsx:186-235`）用 `useRef` 保存 `stablePrefixRef` 字符串，每次渲染只对 `stripped.substring(boundary)` 调 `marked.lexer()`，然后用 `<Markdown>` 组件把稳定前缀和增量后缀分别渲染——React 的 `useMemo` 自然保证稳定前缀不被重解析。`MarkdownTable`（`MarkdownTable.tsx`）按三档列宽策略渲染：理想宽度 → 最小宽度等比分配 → 最小宽度按比例缩小并启用硬换行；超出 `MAX_ROW_LINES=4` 时回退到垂直 key-value 格式。`AssistantToolUseMessage`（`docs/references/ui/components/messages/AssistantToolUseMessage.tsx:35-294`）按 `isResolved`、`isQueued`、spinner 三种状态渲染同一行。

OneCode 已经把 `MarkdownTable` 移植过来（`ui/cli/terminal/markdown_rendering.py`），但**没有**移植 `StreamingMarkdown` 的“稳定前缀 + 增量重渲染”模式，也**没有**移植 `Markdown.tsx` 的 token LRU 缓存。本次变更的焦点就是这两个移植，以及对应的事件合并和工具 queued 态扩展。

## Plan of Work（工作计划）

按依赖关系分 4 个阶段。每个 Stage 都先写测试，再写实现。

### Stage 1：让稳定前缀缓存真正被消费（最大头）

**目标**：在 `ui/cli/terminal/turn_render_state.py` 的 `render_turn_preview_ansi` 中，改为复用 `StreamingMarkdownState._stable_lines` 缓存，只对增量部分重新渲染。移植 `StreamingMarkdown.tsx:186-235` 的算法。

**修改文件**：`ui/cli/terminal/turn_render_state.py`、`ui/cli/terminal/stream_session.py`（移除“我不使用它做实际渲染”的注释路径）。

**步骤**：

1. 在 `AssistantTailState`（`turn_render_state.py:67-104`）中新增字段 `_stable_rendered: list[str]` 和 `_stable_text: str`，记录“上次完整渲染过的最后一段文本对应的 ANSI 行列表”。初始值分别是 `[]` 和 `""`。

2. 新增 `AssistantTailState.coalesce_with_cache(new_text: str, width: int) -> list[str]` 方法：返回新的预览行列表。算法：
   - 如果 `new_text == self._stable_text`，直接返回 `self._stable_rendered`。
   - 如果 `new_text.startswith(self._stable_text)`，说明是新增的：取 `delta = new_text[len(self._stable_text):]`。
   - 如果 `not new_text.startswith(self._stable_text)`（文本缩短或被替换），调用 `_reset_cache()` 后从头开始渲染。
   - 对 `delta` 调用 `_render_segment(delta, width)`（复用 `markdown_rendering.py` 中的 Rich 渲染）。注意当 `delta` 跨越行边界时，需要从已缓存行列表中丢弃最后一行（可能是不完整的）。
   - 拼接：`(self._stable_rendered[:-1] 如果跨越行边界 else self._stable_rendered) + delta_lines`。
   - 把 `(new_text, result)` 写回缓存。
   - 调用 `_render_segment` 时仍然走现有的 GFM 表格检测逻辑（`markdown_rendering.parse_markdown_table_block` + `render_markdown_table_block`）和不平衡 fence fallback。

3. 在 `TurnRenderState` 中加 `reset_assistant_cache()` 方法，调用 `self.assistant._reset_cache()`。reducer `consume_agent_event` 不直接调用它；它由 retry 路径（`core/loop.py:215-233` 的 `pending_retry_events`）在外层显式触发，或由 `consume_event` 在 `assistant_message_completed` 之后调。

4. 修改 `render_turn_preview_ansi`（`turn_render_state.py:376-425`）：把 `tail_lines = state.assistant.visible_lines()` + `_render_assistant_tail("\n".join(tail_lines), width=width)` 改为 `tail_rendered = state.assistant.coalesce_with_cache(state.assistant.text, width=width); out_lines.extend(tail_rendered[-ASSISTANT_TAIL_MAX_LINES:])` 或等价逻辑。

5. 修改 `stream_session.py:524-541` 的 `render_preview_ansi`：去掉“我们不使用它做实际渲染”的注释；让 `state.update(buffer.assistant_text)` 真正驱动 `_stable_lines` 的更新（这是已有的逻辑），并让 `render_turn_preview_ansi` 读取它。

6. 单元测试 `tests/test_streaming_markdown_state.py` 新增用例：
   - `test_stable_prefix_not_re_lexed`：构造一个 spy 包住 Rich `Markdown` 构造器；连续 `update("hello ") → "hello world" → "hello world!\n" → "hello world!\nfoo"`，断言 spy 收到的文本总长接近增量之和（≤ 5 倍最终长度），而不是 N 倍。
   - `test_text_shrink_resets_cache`：先 `update("hello world")`，再 `update("retry")`（不以前者开头），断言缓存被重置且新文本完整渲染。
   - `test_unfinished_line_at_boundary`：当 delta 以半行开头时，断言前一行被丢弃。
   - `test_fence_balance_preserved`：当稳定部分以未关闭的 ```` ``` ```` 结尾时，断言增量渲染走纯文本 fallback（不泄露合成 closing fence）。

### Stage 2：Token LRU 缓存（commit 路径加速）

**目标**：在 commit 静态区域时，按 (text_hash, width) 缓存 Rich Markdown 渲染结果。

**修改文件**：新增 `ui/cli/terminal/text_cache.py`、修改 `ui/cli/terminal/static_output.py` 和 `ui/cli/terminal/markdown_rendering.py`。

**步骤**：

1. 新建 `ui/cli/terminal/text_cache.py`，定义 `class TextCache`：
   - 构造参数 `max_size: int = 500`（与参考 `Markdown.tsx:22` 的 `TOKEN_CACHE_MAX` 一致）。
   - 内部 `dict[tuple[str, int], list[str]]`，键是 (blake2b(text, digest_size=16).hexdigest(), width)。
   - 方法 `get_or_render(text: str, *, width: int, render_fn: Callable[[str, int], list[str]]) -> list[str]`：命中且 width 一致时返回缓存；未命中时调 `render_fn(text, width)`，splitlines，写入缓存，超过 `max_size` 时丢弃最旧（FIFO，简化即可；LRU 优化是后续可选项）。
   - 用 `threading.Lock` 保护 `dict` 访问（CLI 是单线程异步，但 `print_static` 可能在 cancel 路径中被多任务访问）。

2. 在 `markdown_rendering.py` 末尾加一个 module-level 的 `render_cached_markdown(text: str, *, width: int, theme) -> list[str]` 函数：内部用 `TextCache` 包装，render_fn 调 Rich `Markdown(text)` 渲染到 `io.StringIO()` 并 splitlines。

3. 修改 `static_output.print_assistant_markdown`（`static_output.py:120-137`）：把 `print_static(Markdown(text))` 改为先 `cached_lines = render_cached_markdown(text, width=...)`，再 `print_static(Text("\n".join(cached_lines)))`。宽度从 `static_console()` 读（`Console.width`），无法读时回退到 80。

4. 单元测试 `tests/test_text_cache.py`：
   - `test_cache_hit_no_rerender`：连续两次 `get_or_render("hello", width=80, render_fn=spy)`，断言 spy 只被调一次。
   - `test_cache_miss_on_width_change`：第一次 width=80，第二次 width=120，断言 spy 被调两次。
   - `test_cache_eviction`：填满 `max_size=2` 后插入新 key，断言最旧的被丢弃。
   - `test_hash_collisions_handled`：两个不同文本在 `digest_size=8` 下 hash 相同（构造特殊用例），仍然按内容区分（用 (hash, len(text)) 复合 key 或全文本 key 都行；推荐用全文本 key 简化）。

### Stage 3：事件合并（coalescing）

**目标**：把高频事件（`assistant_delta`、`tool_progress`、`tool_call_delta`）合并到 16 毫秒窗口内再 apply，触发更少的 reducer + invalidate。

**修改文件**：新增 `ui/cli/terminal/streaming_coalescer.py`、修改 `ui/cli/terminal/stream_session.py`。

**步骤**：

1. 新建 `ui/cli/terminal/streaming_coalescer.py`，定义 `class StreamingCoalescer`：
   - 构造参数 `apply: Callable[[AgentEvent], None]`、`window_seconds: float = 0.016`、`clock: Callable[[], float] = time.monotonic`（便于测试注入假时钟）。
   - 内部状态 `_pending_text: str`、`_pending_progress: dict[str, str]`、`_pending_tool_label: str | None`、`_last_flush: float`。
   - 方法 `push(event: AgentEvent) -> bool`：根据事件类型分派；返回 `True` 表示立即 apply 了低频事件（外部应触发 invalidate），返回 `False` 表示合并到了 pending（外部无需立即 invalidate，但应在 `flush()` 之后 invalidate）。
   - 方法 `flush() -> bool`：把所有 pending 转成 `AgentEvent` 调 `apply`；返回 `True` 表示有 flush 出去。
   - 方法 `should_flush(now: float) -> bool`：`(now - self._last_flush) >= self._window_seconds` 且 pending 非空。

2. 修改 `StreamingSession._feed`（`stream_session.py:631-653`）：用 `StreamingCoalescer` 替换直接的 `consume_event` 调用。
   - `coalescer = StreamingCoalescer(apply=lambda e: consume_event(self.buffer, e))`。
   - 主循环：`for event in events: if self._cancel.is_set(): break; if coalescer.push(event): _safe_invalidate(app); if coalescer.should_flush(time.monotonic()): coalescer.flush(); _safe_invalidate(app); self._flush_completed_tools_to_static()`。
   - `finally: coalescer.flush(); _safe_invalidate(app); _safe_exit(app)`。
   - cancel handler 路径（`stream_session.py:620-625`）也调用 `coalescer.flush()` 一次再读 `buffer.assistant_text`，确保拿到完整文本。

3. 单元测试 `tests/test_streaming_coalescer.py`：
   - `test_assistant_deltas_coalesced`：调 `apply_spy` 喂 100 个 `assistant_delta("a")`，在 16ms 窗口内断言 `apply_spy` 收到 ≤ 5 次合成事件。
   - `test_low_freq_event_flushes_pending`：先 push 50 个 delta，再 push 1 个 `tool_result`，断言下游先收到合并的 `assistant_delta`，再收到 `tool_result`，且合并 delta 的 `text == "a" * 50`。
   - `test_terminal_flush_no_loss`：循环结束后调 `flush()`，断言所有字符都到达（总数 == 100）。
   - `test_progress_overwritten`：连续 10 个 `tool_progress("a")`、`tool_progress("b")` 喂同一个 call_id，合并后只 apply 一次且 text 是最后一个 "b"。
   - `test_clock_injection`：注入假时钟，验证 `should_flush` 在窗口边界正确切换。

### Stage 4：工具调用的 queued 三态显示

**目标**：扩展 `ActiveToolState.status` 加 `"queued"`，区分已宣告但未开始 / 正在运行 / 已完成。

**修改文件**：`ui/cli/terminal/turn_render_state.py`、`ui/cli/tool_renderers.py`（如需调整 queued 标签文案）。

**步骤**：

1. 修改 `ActiveToolState.status` 文档（`turn_render_state.py:108-114`）：注释扩展为 `status: str = "pending"  # "pending" | "queued" | "running"`，但保持 `pending` 为 reducer 内部过渡值。

2. 修改 `consume_agent_event` 处理 `tool_call_ready` 的分支（`turn_render_state.py:275-298`）：把 `status="pending"` 改为 `status="queued"`。注释：这是“已宣告但未开始”的对外状态。

3. 修改 `consume_agent_event` 处理 `tool_started` 的分支（`turn_render_state.py:299-324`）：保留升级到 `"running"` 的行为；`existing.set_status("running")` 已经做了。如果 `existing is None`（call_id 没经过 tool_call_ready），则创建 `ActiveToolState(status="running")`。

4. 修改 `_format_active_tool_line`（`turn_render_state.py:428-436`）：增加 `queued` 分支：
   - `if tool.status == "queued": return f"tool: {label} (queued)"`。
   - `if tool.status == "running" and tool.progress: return f"tool: {label} {tool.progress}"`（保持现状）。
   - `if tool.input_preview: return f"tool: {label} {tool.input_preview}"`（保持现状，但只在 `queued` 之外的 status 才显示 input_preview；queued 不显示具体参数以减少噪音）。

5. 单元测试 `tests/test_turn_render_state.py`（或现有 `test_cli_turn_render_state.py`）新增：
   - `test_queued_then_started`：发 `tool_call_ready` → 状态是 `queued`；发 `tool_started` → 状态是 `running`；input_preview 保留。
   - `test_queued_format_no_input_preview`：queued 状态下渲染的预览行不包含 input_preview（断言 line == "tool: bash (queued)"）。
   - `test_running_with_progress`：running + progress 时渲染 `tool: bash <progress>`。
   - `test_multiple_queued`：连续 2 个 `tool_call_ready` 不同 call_id，active_tools 有 2 个 queued 项；visible_active_tools(limit=3) 返回 2 个。

### 测试与回归

所有 Stage 实现后跑以下命令：

    cd D:\study\OneCode
    uv run python -m pytest tests/test_streaming_markdown_state.py tests/test_streaming_coalescer.py tests/test_text_cache.py tests/test_turn_render_state.py tests/test_cli_turn_render_state.py tests/test_streaming_markdown_state.py -v

期望所有用例通过。运行 streaming 相关回归：

    uv run python -m pytest tests/test_cli_streaming_session_commit.py tests/test_loop_realtime_streaming.py tests/test_markdown_rendering.py tests/test_cli_tool_renderers.py tests/test_cli_terminal.py -v

期望全部通过。

## Concrete Steps（具体步骤）

按 Stage 顺序执行。每个 Stage 完成后跑对应测试再进入下一个。

### Stage 1 步骤

1. 编辑 `ui/cli/terminal/turn_render_state.py:67-104`：在 `AssistantTailState` 末尾加 `_stable_rendered: list[str] = field(default_factory=list)`、`_stable_text: str = ""`、`_reset_cache()` 方法、`coalesce_with_cache(new_text, width)` 方法。
2. 编辑 `ui/cli/terminal/turn_render_state.py:152-198`：在 `TurnRenderState` 加 `reset_assistant_cache()`。
3. 编辑 `ui/cli/terminal/turn_render_state.py:376-425`：把 `render_turn_preview_ansi` 的 `tail_lines` 段改为 `coalesce_with_cache` 路径。
4. 编辑 `ui/cli/terminal/stream_session.py:524-541`：去掉“我不使用它做实际渲染”的注释，确保 `state.update` 真正驱动缓存。
5. 扩展 `tests/test_streaming_markdown_state.py`，加 4 个新测试。
6. 跑 `uv run python -m pytest tests/test_streaming_markdown_state.py -v`，期望通过。

### Stage 2 步骤

1. 新建 `ui/cli/terminal/text_cache.py`，定义 `TextCache` 类。
2. 编辑 `ui/cli/terminal/markdown_rendering.py`，加 `render_cached_markdown` 函数。
3. 编辑 `ui/cli/terminal/static_output.py:120-137`，把 `print_static(Markdown(text))` 改为 `render_cached_markdown` + `print_static(Text("\n".join(cached_lines)))`。
4. 新建 `tests/test_text_cache.py`，加 4 个测试。
5. 跑 `uv run python -m pytest tests/test_text_cache.py -v`，期望通过。

### Stage 3 步骤

1. 新建 `ui/cli/terminal/streaming_coalescer.py`，定义 `StreamingCoalescer` 类。
2. 编辑 `ui/cli/terminal/stream_session.py:631-653`，把 `_feed` 的直接 `consume_event` 路径改为 `coalescer.push` + 定时 `flush`。
3. 编辑 `ui/cli/terminal/stream_session.py:620-625`，cancel handler 路径加 `coalescer.flush()`。
4. 新建 `tests/test_streaming_coalescer.py`，加 5 个测试。
5. 跑 `uv run python -m pytest tests/test_streaming_coalescer.py -v`，期望通过。

### Stage 4 步骤

1. 编辑 `ui/cli/terminal/turn_render_state.py:108-114`：更新 `ActiveToolState.status` 注释。
2. 编辑 `ui/cli/terminal/turn_render_state.py:275-298`：把 `status="pending"` 改为 `status="queued"`。
3. 编辑 `ui/cli/terminal/turn_render_state.py:428-436`：在 `_format_active_tool_line` 加 `queued` 分支。
4. 扩展 `tests/test_turn_render_state.py`（或 `test_cli_turn_render_state.py`），加 4 个测试。
5. 跑 `uv run python -m pytest tests/test_turn_render_state.py -v`，期望通过。

### 验证与回归

    cd D:\study\OneCode
    uv run python -m compileall ui services core
    uv run python -m pytest tests/test_streaming_markdown_state.py tests/test_streaming_coalescer.py tests/test_text_cache.py tests/test_turn_render_state.py tests/test_cli_turn_render_state.py tests/test_cli_streaming_session_commit.py tests/test_loop_realtime_streaming.py tests/test_markdown_rendering.py tests/test_cli_tool_renderers.py tests/test_cli_terminal.py -v

期望 compileall 无错误；测试全部通过。

## Validation and Acceptance（验证与验收）

### 自动化测试

**Stage 1 之前 vs 之后**：
- 之前：`test_stable_prefix_not_re_lexed` 会失败（spy 收到全文 4 次）。
- 之后：spy 收到 1 次（首次）+ 3 次增量（每个 delta 长度对应）。

**Stage 2 之前 vs 之后**：
- 之前：没有 `tests/test_text_cache.py`，无法验证 commit 路径缓存。
- 之后：4 个新测试通过。

**Stage 3 之前 vs 之后**：
- 之前：`_feed` 每次事件都 invalidate。
- 之后：100 个 delta 在 16ms 窗口内只触发 1 次 invalidate；测试 `test_assistant_deltas_coalesced` 验证 apply_spy 调用次数 ≤ 5。

**Stage 4 之前 vs 之后**：
- 之前：queued 与 running 难以区分（都是 tool: bash <something>）。
- 之后：queued 行明确为 `tool: bash (queued)`，running 行有 progress 文本。

### 手动验收

在 `D:\study\OneCode` 跑 `uv run python -m ui.cli.app`，输入 “用 5 句话写一个关于 streaming 的俳句”，观察：

- assistant 文本逐字符/逐 token 出现，UI 不闪烁。
- 当 assistant 写出 ```代码块``` 或表格时，动态区域正确渲染。
- 触发一次 `bash` 工具调用：动态区域先短暂显示 `tool: bash (queued)`，再切换到 `tool: bash <command>` 带 spinner 的行；完成后 commit 到静态区域为 `[bash] exit 0 in ...`。

### 性能基线（可选）

写一个临时脚本 `bench_streaming.py` 模拟 1000 个 `assistant_delta("a")` 事件、跑 10 轮，测量 `_feed` 路径的 wall-clock 时间。优化前 ≈ O(N) 事件处理 + O(N²) 渲染；优化后 ≈ O(N) 事件处理 + O(N) 渲染。

## Idempotence and Recovery（幂等性与恢复）

所有 Stage 都是纯加法 / 局部修改：

- Stage 1 在 `AssistantTailState` 上加新字段、新方法；不改 `consume_agent_event`。
- Stage 2 新增独立文件 `text_cache.py`；只替换 `print_assistant_markdown` 内部一行。
- Stage 3 新增独立文件 `streaming_coalescer.py`；替换 `_feed` 主循环。
- Stage 4 改 3 处行；每处都有相邻测试覆盖。

如果某个 Stage 验证失败，回滚该 Stage 的 commit 即可，之间互不依赖。每个 Stage 提交一个独立 commit。

## Artifacts and Notes（制品与笔记）

### 参考实现关键代码位置

- `docs/references/ui/components/Markdown.tsx:22-71`：token LRU 缓存，按内容 hash 索引。
- `docs/references/ui/components/Markdown.tsx:186-235`：`StreamingMarkdown` 稳定前缀 + 增量再渲染。
- `docs/references/ui/components/messages/AssistantToolUseMessage.tsx:110-121`：工具三态判断（`isResolved` / `isQueued` / running）。
- `docs/references/ui/components/Messages.tsx`：消息列表容器（不在本次变更范围；仅供导航）。

### 当前实现关键位置

- `core/loop.py:240-303`：agent loop 实时转发 `AgentEvent`，不修改。
- `core/stream_events.py:11-33`：事件类型定义。
- `ui/cli/terminal/stream_session.py:180-379`：`StreamingMarkdownState`（缓存已存在但未消费）。
- `ui/cli/terminal/stream_session.py:480-518`：`consume_event` reducer 入口。
- `ui/cli/terminal/turn_render_state.py:67-104`：`AssistantTailState`。
- `ui/cli/terminal/turn_render_state.py:108-114`：`ActiveToolState`（status 字段待扩展）。
- `ui/cli/terminal/turn_render_state.py:257-359`：`consume_agent_event` 纯 reducer。
- `ui/cli/terminal/turn_render_state.py:376-425`：`render_turn_preview_ansi`（待改造）。
- `ui/cli/terminal/turn_render_state.py:428-436`：`_format_active_tool_line`（待加 queued 分支）。
- `ui/cli/terminal/static_output.py:120-137`：`print_assistant_markdown`（待用 TextCache）。
- `ui/cli/terminal/markdown_rendering.py`：GFM 表格宽度感知渲染（保留并复用）。

## Interfaces and Dependencies（接口与依赖）

### 新增模块

`ui/cli/terminal/text_cache.py`：

    import hashlib
    import threading
    from collections.abc import Callable

    class TextCache:
        """LRU 文本→已渲染 ANSI 行列表缓存，键为 (hash, width)。

        参考 docs/references/ui/components/Markdown.tsx:22-71 的 tokenCache 设计。
        缓存只存行列表与摘要，不存原文，避免长 session 的 RSS 膨胀。
        """

        def __init__(self, max_size: int = 500) -> None: ...
        def get_or_render(
            self,
            text: str,
            *,
            width: int,
            render_fn: Callable[[str, int], list[str]],
        ) -> list[str]: ...
        def clear(self) -> None: ...
        def stats(self) -> dict[str, int]: ...

`ui/cli/terminal/streaming_coalescer.py`：

    from collections.abc import Callable
    from core.stream_events import AgentEvent

    class StreamingCoalescer:
        """把高频 assistant_delta / tool_progress / tool_call_delta 合并到 16ms 窗口内。

        窗口内的多个 delta 只触发一次 apply；低频事件（tool_call_ready、tool_started、
        tool_result、transition）立即 apply 并 flush 任何 pending。
        """

        def __init__(
            self,
            *,
            apply: Callable[[AgentEvent], None],
            window_seconds: float = 0.016,
            clock: Callable[[], float] = ...,
        ) -> None: ...
        def push(self, event: AgentEvent) -> bool: ...
        def flush(self) -> bool: ...
        def should_flush(self, now: float | None = None) -> bool: ...

### 修改的函数签名

`ui/cli/terminal/turn_render_state.py::AssistantTailState`：

    @dataclass
    class AssistantTailState:
        text: str = ""
        _stable_rendered: list[str] = field(default_factory=list)
        _stable_text: str = ""
        _reset_cache(self) -> None: ...
        coalesce_with_cache(self, new_text: str, *, width: int) -> list[str]: ...

`ui/cli/terminal/turn_render_state.py::TurnRenderState`：

    def reset_assistant_cache(self) -> None: ...

`ui/cli/terminal/turn_render_state.py::render_turn_preview_ansi`：内部使用 `coalesce_with_cache`；签名不变。

`ui/cli/terminal/static_output.py::print_assistant_markdown`：行为不变，签名不变，调用路径从 Rich `Markdown` 改为 `render_cached_markdown`。

### 测试

新增 `tests/test_text_cache.py`、`tests/test_streaming_coalescer.py`；扩展 `tests/test_streaming_markdown_state.py`、`tests/test_turn_render_state.py`（或 `tests/test_cli_turn_render_state.py`）。

### 依赖

- 全部用标准库 + 现有依赖（`rich`、`prompt_toolkit`）。
- 新增一个第三方依赖？没有。
- Python 版本：与现有 `pyproject.toml` 一致（≥ 3.11）。
