# 实现只读工具并发执行与编辑工具串行执行

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本计划遵守仓库根目录的 `PLANS.md`。本文是一个完整、自包含的实现说明；执行者只需要当前工作树和本文件，就能完成、验证并维护这项变更。

## Purpose / Big Picture

OneCode 当前能执行模型返回的多个工具调用，但 `services/tools/executor.py` 会按 provider 返回顺序逐个串行执行。这样在一次模型响应同时请求多个文件读取、glob 或 grep 时，运行时间会被不必要地拉长。完成本计划后，OneCode 会继续保持 provider 返回顺序的语义：编辑、写入、未知副作用或不可并发工具仍然串行执行；连续的、已经通过权限预检且 `concurrency_safe=True` 的工具 handler 会并发运行。用户可以通过新增测试看到两个慢速只读工具在同一批次中同时开始，而 `edit_file` 这类写入工具仍然独占执行。

这项变更还会把 `files_read` 这类会话状态更新从具体工具 handler 移到 executor 的串行后处理阶段。这样 `read_file` 即使被并发执行，也不会在 worker 线程里直接修改 `RuntimeState.metadata`；executor 会按最终结果顺序串行记录已读文件，后续 `edit_file` 仍能依赖“已存在文件必须先读后改”的安全规则。

## Progress

- [x] (2026-06-05 15:35+08:00) 阅读 `AGENTS.md`、`PLANS.md`、`architecture.md`、`docs/design-docs/tool-design-guidelines.md`、技术债 TD-006 和 `docs/references/Tools_full` 中的工具机制参考。
- [x] (2026-06-05 15:45+08:00) 与用户确认关键设计：以 `concurrency_safe` 为调度依据；串行完成 permission preflight 后只并发真正 allowed 的 handler；并发批次中单个失败不取消兄弟调用；默认最大并发数为 10；`files_read` 更新移动到 executor 串行后处理。
- [x] (2026-06-05 16:20+08:00) 实现 executor 内部的分批调度、串行 preflight、并发 handler 执行和顺序后处理。
- [x] (2026-06-05 16:20+08:00) 移除 `tools/read_file/tool.py` 与 `tools/edit_file/tool.py` 中对 `RuntimeState.metadata["files_read"]` 的直接写入，并由 executor 根据成功工具结果串行更新。
- [x] (2026-06-05 16:25+08:00) 增加覆盖并发批次、串行边界、permission preflight、结果顺序、失败 sibling 和 `files_read` 迁移的测试。
- [x] (2026-06-05 16:30+08:00) 运行目标测试、全量测试和 compileall，并根据结果更新本计划的证据与回顾。

## Surprises & Discoveries

- Observation: `docs/design-docs/tool-design-guidelines.md` 已经明确规定连续分批策略，并要求分类异常、schema 异常或未知工具按不可并发处理。
  Evidence: `docs/design-docs/tool-design-guidelines.md` 的 `Concurrency` 小节说明只有 `classify_input().concurrency_safe == True` 的调用可以进入并发批次，非并发调用单独成批。

- Observation: 当前 `read_file` 虽然被分类为只读并发安全，但 handler 会写入 `runtime.state.metadata["files_read"]`。
  Evidence: `tools/read_file/tool.py` 的 `_handle()` 成功读取后直接更新 `files_read` set；这会让并发安全工具在 worker 线程中修改共享 runtime state。

- Observation: 当前 `edit_file` 在 handler 内读取 `files_read` 来强制“先读后改”，并在创建或编辑成功后继续把文件标为已读。
  Evidence: `tools/edit_file/tool.py` 中 `_was_read()` 和 `_mark_read()` 都直接访问 `runtime.state.metadata["files_read"]`。

## Decision Log

- Decision: 并发调度只以本次调用最终分类中的 `ToolCallClassification.concurrency_safe` 为准，不以工具名称或 `read_only` 字段硬编码判断。
  Rationale: OneCode 的工具设计要求 input-aware classification 是权威元数据。只读通常意味着可并发，但未来可能出现只读但不安全的工具；也可能出现不接触文件系统但仍需要串行的工具。
  Date/Author: 2026-06-05 / User and Codex

- Decision: 对准备进入并发批次的工具，先串行完成 permission preflight，再只把真正 allowed 且最终仍 `concurrency_safe=True` 的 handler 放入并发执行。
  Rationale: CLI permission prompter 是同步终端交互，不能让多个 worker 线程同时竞争用户输入。preflight 串行也能保持 deny-first、guard、permission 和 PreToolUse hook 的行为清晰可审计。
  Date/Author: 2026-06-05 / User and Codex

- Decision: 并发批次中某个工具失败时，不取消同批次里的其他只读工具。
  Rationale: 首版没有 streaming abort controller 或后台任务生命周期。文件读取、glob、grep 这类只读调用通常彼此独立，一个失败不应让其他结果丢失。取消兄弟调用可留给未来 streaming executor 设计。
  Date/Author: 2026-06-05 / User and Codex

- Decision: 最大并发数默认 10，并允许通过 `ONECODE_MAX_TOOL_CONCURRENCY` 覆盖；测试中显式传入最大并发数，避免环境变量影响。
  Rationale: `docs/references/Tools_full/services/tools/toolOrchestration.ts` 的参考实现默认最大并发数为 10。OneCode 当前没有专门 runtime config 模块承载这个选项，先采用构造参数加环境变量读取的低风险方式。
  Date/Author: 2026-06-05 / User and Codex

- Decision: `files_read` 这类会话状态副作用从具体工具 handler 移到 executor 的成功结果后处理，并按结果顺序串行应用。
  Rationale: 只读 handler 要能安全并发运行，不能在 worker 线程里直接修改共享 `RuntimeState`。executor 已经掌握最终结果、工具名称和 metadata，是统一执行顺序化 runtime side effect 的合适边界。
  Date/Author: 2026-06-05 / User and Codex

## Outcomes & Retrospective

2026-06-05 实现完成。`RegistryToolExecutor` 现在保持公开 `execute()` 协议不变，内部按原始 `concurrency_safe` 分类切出连续候选批次，对候选批次串行运行 schema validation、工具 validation、classification、guard、permission 和 `PreToolUse` hook，然后只把最终仍并发安全且 preflight 成功的 handler 放入 `ThreadPoolExecutor`。结果 finalize 仍按 provider order 串行执行，因此 result policy、`PostToolUse` / `ToolError` hook 和 session side effect 顺序保持可预测。

本次新增或更新的测试覆盖了两个慢速并发安全 handler 同时进入同一批次、handler 完成顺序不同但结果顺序不变、非并发调用隔开前后并发批次、permission preflight 先于并发 handler 串行完成、单个并发 handler 失败不取消兄弟调用，以及 `[read_file, edit_file]` 同轮响应依赖 executor 记录的 `files_read` 成功编辑。TD-006 已更新为“并发调度已落地，durable result store 和完整只读策略仍待实现”。没有发现 `files_read` 以外的并发安全工具 handler 直接写 `RuntimeState.metadata`。

## Context and Orientation

OneCode 是 Python code agent runtime。主循环在 `core/loop.py` 中，负责把用户消息、模型响应和工具结果串起来；它不应该知道具体工具名称，也不应该实现并发调度细节。工具运行时位于 `services/tools/`。具体工具实现位于顶层 `tools/`，例如 `tools/read_file/tool.py` 和 `tools/edit_file/tool.py`。

本文使用的几个术语如下。`tool call` 是模型请求执行某个工具的一次调用，在 OneCode 中由 `services/tools/types.py` 的 `ToolCall` 表示。`handler` 是具体工具执行函数，在 `ToolDescriptor.handler` 中注册。`classification` 是工具根据本次输入返回的执行元数据，由 `ToolCallClassification` 表示，其中 `concurrency_safe` 表示这一次调用是否可以和相邻的并发安全调用并行运行。`permission preflight` 是 handler 执行前的一整套串行准备流程，包括 schema 形状校验、工具自定义校验、input-aware classification、guard 检查、permission policy 决策、必要时询问用户、PreToolUse hook，以及 hook 修改输入后的重新校验和重新分类。`provider order` 是模型在一次响应中返回 tool calls 的顺序；即使 handler 并发执行，OneCode 写回 tool result 时也必须保持这个顺序。

当前关键文件如下。`services/tools/types.py` 定义 `ToolCall`、`ToolExecutionResult`、`ToolRuntime`、`ToolDescriptor` 和 `ToolCallClassification`。`services/tools/executor.py` 定义 `ToolExecutor` protocol 和 `RegistryToolExecutor`，现在 `execute()` 直接使用列表推导逐个调用 `_execute_one()`。`services/tools/registry.py` 管理可用工具 descriptor。`services/permissions/policy.py` 负责 deny-first 权限合并。`tools/read_file/tool.py` 当前读取成功后直接写 `RuntimeState.metadata["files_read"]`。`tools/edit_file/tool.py` 当前在 handler 内检查和更新 `files_read`。

参考实现放在 `docs/references/Tools_full/`。其中 `services/tools/toolOrchestration.ts` 是本计划最主要的参考：它将 tool calls 分为连续并发安全批次和单个非并发批次，并保持批次之间的顺序。`services/tools/StreamingToolExecutor.ts` 展示了 streaming 场景下的队列、结果缓冲和 sibling abort；OneCode 首版不实现这些 streaming 细节，只保留未来可扩展边界。

## Plan of Work

第一步重构 `services/tools/executor.py`，把“准备输入”和“执行 handler”拆开。保留现有公开接口 `RegistryToolExecutor.execute(tool_calls, state) -> list[ToolExecutionResult]` 不变，以免改动 `core/loop.py`、provider adapter 或 CLI。新增私有 dataclass，例如 `_ReadyToolCall` 和 `_HandlerOutcome`。`_ReadyToolCall` 应保存原始 `ToolCall`、`ToolDescriptor`、最终 `tool_input`、带 approved guard policies 的 `ToolRuntime`、最终 `ToolCallClassification` 和 guard policies。现有 `_execute_one()` 中 handler 前的逻辑应抽到 `_preflight_one()`，它返回 `_ReadyToolCall` 或现有 `_PreparedInputError` 风格的 immediate error result。handler 执行本身放到 `_run_handler()`，只负责调用 descriptor handler 并捕获异常，不运行 hook、不改 state。

第二步实现分批调度。`execute()` 不再直接列表推导，而是调用一个私有批次执行函数。调度必须按 provider order 行走。非并发调用单独执行。连续的 `concurrency_safe=True` 调用形成一个候选并发批次。对候选并发批次，先对每个调用串行运行 `_preflight_one()`；只有 preflight 成功且最终 classification 仍然 `concurrency_safe=True` 的调用可以进入 handler 并发池。preflight 失败的调用直接产生 error result，占住原结果位置但不启动 handler。如果 PreToolUse hook 修改输入后让某个调用最终不再并发安全，则不得把这个调用放入并发池；最简单安全的首版行为是把当前候选批次从该调用开始退化为 provider order 串行执行。这样不会让 hook 造成不安全并发。

第三步使用标准库 `concurrent.futures.ThreadPoolExecutor` 执行并发 handler。`RegistryToolExecutor.__init__()` 增加可选参数 `max_tool_concurrency: int | None = None`。如果调用者未传入，则从 `ONECODE_MAX_TOOL_CONCURRENCY` 读取正整数；缺失或非法时使用默认值 10。实际 worker 数使用 `min(max_tool_concurrency, len(ready_calls))`。测试应显式传入 `max_tool_concurrency=2` 或其他确定值，避免环境污染。由于现有 handler 是同步函数，线程池比引入 asyncio 更贴合当前代码。

第四步统一顺序后处理。无论 handler 是并发还是串行执行，executor 都必须按 provider order 对每个 outcome 做相同处理：把 handler 返回的 `tool_call_id` 和 `tool_name` 规范化为原 call id 和 descriptor name；如果结果不是 error，则应用 `ToolResultPolicy`；如果结果是 error，则运行 `ToolError` hook；如果结果成功，则运行 `PostToolUse` hook；最后调用新的 executor-owned side effect 应用函数，例如 `_apply_success_side_effects(result, state)`。这个函数首版只负责 `files_read`：当成功结果来自 `read_file` 或 `edit_file`，且 `result.metadata["path"]` 是非空字符串时，把该路径加入 `state.metadata["files_read"]`。为减少兼容风险，`files_read` 可以继续使用当前 set 形态；关键是写入发生在 executor 后处理阶段，而不是并发 handler 内。

第五步修改 `tools/read_file/tool.py`。删除 `_handle()` 成功路径中直接更新 `runtime.state.metadata["files_read"]` 的代码，保留返回 metadata 中的 `"path": str(path)`。如果文件读取失败、路径被拒绝或读取目录，则不应产生 executor side effect。

第六步修改 `tools/edit_file/tool.py`。保留 `_was_read()`，因为编辑安全规则仍由 handler 在实际写入前检查。删除成功创建和成功编辑路径中对 `_mark_read()` 的调用，或让 `_mark_read()` 不再被使用后删除该函数。成功结果必须继续返回 metadata 中的 `"path": str(path)` 和 `"replacement_count"`，由 executor 在结果后处理阶段把编辑后的文件标记为已读。这样在同一次模型响应中 `[read_file a.txt, edit_file a.txt]` 仍应工作：第一个批次执行 read_file，executor 后处理按结果顺序记录 `files_read`，随后 edit_file 串行执行并看到文件已读。

第七步补充测试。优先在 `tests/test_tool_registry_and_executor.py` 中新增调度单元测试，因为该文件已经有 registry、executor、permission 和 result policy 的覆盖。必要时在 `tests/test_file_tools_guard.py` 中补一个真实 read/edit 集成测试。测试要证明并发、安全边界和 state side effect 三件事，而不是只检查函数存在。

第八步更新技术债。实现和测试通过后，编辑 `docs/tech-debt/tech-debt-tracker.md` 的 TD-006，说明 `read_only`/`concurrency_safe` 中的并发调度部分已落地，但 durable result store 仍未完成，因此 TD-006 不应被完全关闭，除非另一个计划同时实现 result store。

## Concrete Steps

从仓库根目录 `D:\study\OneCode` 开始。

先运行当前相关测试，确认基线：

    uv run python -m pytest tests\test_tool_registry_and_executor.py tests\test_file_tools_guard.py -q

预期当前基线应通过。如果失败，先记录失败到 `Surprises & Discoveries`，判断是否与本计划相关。不要在未理解失败原因前修改实现。

编辑 `services/tools/executor.py`。添加标准库 import：`os`、`concurrent.futures.ThreadPoolExecutor`、`concurrent.futures.as_completed` 或等价结构。新增默认常量：

    DEFAULT_MAX_TOOL_CONCURRENCY = 10

新增 helper：

    def _resolve_max_tool_concurrency(value: int | None = None) -> int:
        ...

该 helper 接受显式构造参数优先，其次读取 `ONECODE_MAX_TOOL_CONCURRENCY`。返回值必须至少为 1；非法、空字符串或小于 1 时返回 10。

在 `RegistryToolExecutor.__init__()` 中保存 `self._max_tool_concurrency`。改写 `execute()`，使它调用新的批次执行逻辑并返回按输入顺序排列的 result list。保留 `ToolExecutor` protocol 不变。

将当前 `_execute_one()` 拆为三层：`_preflight_one()` 负责 handler 前的准备和 PreToolUse hook；`_run_handler()` 只调用 descriptor.handler 并捕获 exception；`_finalize_outcome()` 按顺序应用 result policy、hook 和 side effects。拆分时要保持现有错误 payload 兼容，例如 `unknown_tool`、`invalid_tool_input`、`tool_classification_error`、`permission_ask_required` 和 `tool_execution_error` 的 JSON 结构不要无故变化。

编辑 `tools/read_file/tool.py`。删除成功读取后的以下行为：从 `runtime.state.metadata` 取出或创建 `files_read`，然后 add 当前路径。保留成功 metadata 的 `"path"` 字段。

编辑 `tools/edit_file/tool.py`。删除成功创建文件和成功编辑文件后的 `_mark_read(runtime, path)` 调用。删除不再使用的 `_mark_read()` 函数。保留 `_was_read()`，因为编辑仍必须检查此前是否读过文件。

为 executor 增加测试。建议新增以下测试名称：

    test_executor_runs_concurrency_safe_batch_concurrently_and_preserves_result_order
    test_executor_keeps_non_concurrency_safe_calls_serial_between_parallel_batches
    test_executor_runs_permission_preflight_serially_before_parallel_handlers
    test_executor_does_not_cancel_parallel_siblings_when_one_handler_fails
    test_executor_records_files_read_after_successful_results_in_order

并发测试要避免依赖很长 sleep。可以用 `threading.Barrier(2, timeout=2)` 证明两个 safe handler 同时进入；如果 executor 退回串行，第一个 handler 会超时并产生 error，测试会失败。结果顺序测试可以让第二个 handler 更早结束，但断言返回结果仍是 `call-1`、`call-2`。串行边界测试可以让 safe A/B 写入一个受 lock 保护的事件列表，非并发 C 只能在 A/B 完成后开始，safe D 只能在 C 完成后开始。

为 `files_read` 迁移增加真实工具测试。创建临时 workspace 和文件，执行同一次 `executor.execute()`：

    ToolCall(id="call-1", name="read_file", input={"file_path": "a.txt"})
    ToolCall(id="call-2", name="edit_file", input={"file_path": "a.txt", "old_string": "old", "new_string": "new"})

预期 `read_file` 成功后 executor 记录 `files_read`，随后 `edit_file` 成功写入文件。这个测试能证明 state side effect 在批次之间按结果顺序生效，而不是依赖 `read_file` handler 直接写 state。

运行目标测试：

    uv run python -m pytest tests\test_tool_registry_and_executor.py tests\test_file_tools_guard.py -q

然后运行全量测试：

    uv run python -m pytest tests -q

最后运行 compile check：

    uv run python -m compileall core services infrastructure tools

如果命令输出有失败，把失败摘要记录到 `Surprises & Discoveries`，修复后再次运行。完成后把关键通过输出摘录到 `Artifacts and Notes`。

## Validation and Acceptance

行为验收以测试为准。新增并发测试必须证明两个 `concurrency_safe=True` 的 handler 在同一个批次中并发运行，而不是串行运行。新增结果顺序测试必须证明即使 handler 完成顺序不同，`execute()` 返回的 list 仍按 provider order 排列。新增串行边界测试必须证明非并发调用单独占据批次，前后的并发安全调用不能越过它执行。

权限验收必须证明并发批次的 permission preflight 是串行完成的。具体表现是两个需要 permission ask 的只读调用不会在两个 worker 线程里同时调用 permission prompter；只有 prompter 返回 allow 后，对应 handler 才进入并发池。如果用户或 policy deny 某个调用，该调用返回结构化 tool error，且不启动 handler；同批次其他 allowed safe handler 可以继续执行。

安全验收必须证明 `edit_file` 仍不可并发。`ToolCallClassification.concurrency_safe=False` 的调用必须单独成批，且既不能与前面的 read batch 并发，也不能与后面的 read batch 并发。`[read_file, edit_file]` 同轮响应中，`edit_file` 应在 read result 后处理记录 `files_read` 后成功；`[edit_file]` 未先读时仍应返回 `file_not_read`。

状态验收必须证明 `read_file` handler 本身不再修改 `RuntimeState.metadata["files_read"]`，而 `RegistryToolExecutor` 在成功结果后处理阶段记录该路径。失败的 `read_file` 或失败的 `edit_file` 不应记录已读路径。

全量验收命令如下，均应成功：

    uv run python -m pytest tests -q
    uv run python -m compileall core services infrastructure tools

## Idempotence and Recovery

本计划的代码编辑是普通 Python 源码修改，可以重复运行测试和 compile check。`ONECODE_MAX_TOOL_CONCURRENCY` 只影响 executor 默认构造行为；测试应显式传入最大并发数，避免本地环境造成不可重复结果。

如果并发测试在 Windows 上偶发失败，优先检查测试是否依赖短 sleep。把并发证明改为 `threading.Barrier` 或 `threading.Event` 协调，而不是加大 sleep。不要为了让测试稳定而降低生产调度规则。

如果拆分 `_execute_one()` 时出现错误 payload 回归，先恢复旧测试期望，再把新调度层包在旧 `_execute_one()` 兼容逻辑之外逐步重构。目标是不改变模型可见错误格式，除非计划明确记录了必要原因。

如果发现其他工具 handler 也写 `RuntimeState.metadata`，不要顺手大范围重构。先在 `Surprises & Discoveries` 记录证据；如果它影响 `concurrency_safe=True` 的工具安全，再把该具体 side effect 纳入本计划，否则另开技术债或后续计划。

## Artifacts and Notes

实现完成后的验证证据：

    tests/test_tool_registry_and_executor.py::test_executor_runs_concurrency_safe_batch_concurrently_and_preserves_result_order PASSED
    tests/test_file_tools_guard.py::test_read_then_edit_same_response_uses_executor_recorded_files_read PASSED
    uv run python -m pytest tests -q
    134 passed in 1.96s
    uv run python -m compileall core services infrastructure tools
    Listing 'core'...
    Listing 'services'...
    Listing 'infrastructure'...
    Listing 'tools'...

## Interfaces and Dependencies

公开接口保持不变：

    class ToolExecutor(Protocol):
        def execute(self, tool_calls: tuple[ToolCall, ...], state: object) -> list[ToolExecutionResult]:
            ...

`RegistryToolExecutor.__init__()` 可以新增可选 keyword-only 参数：

    max_tool_concurrency: int | None = None

这个新增参数不要求现有调用者修改。未提供时 executor 使用 `ONECODE_MAX_TOOL_CONCURRENCY` 或默认 10。

新增私有结构建议放在 `services/tools/executor.py`，不暴露给具体工具：

    @dataclass(frozen=True)
    class _ReadyToolCall:
        tool_call: ToolCall
        descriptor: ToolDescriptor
        tool_input: dict[str, Any]
        runtime: ToolRuntime
        classification: ToolCallClassification
        guard_policies: tuple[GuardPolicy, ...]

    @dataclass(frozen=True)
    class _HandlerOutcome:
        ready: _ReadyToolCall
        result: ToolExecutionResult | None = None
        exception: Exception | None = None

具体名称可以调整，但含义必须保留：preflight 的结果与 handler 的结果分离，handler 不负责 hook、result policy 或 runtime side effect。

新增 helper 建议如下：

    def _apply_success_side_effects(self, result: ToolExecutionResult, state: RuntimeState) -> None:
        ...

首版只处理 `files_read`。它必须只在 `result.is_error is False` 时运行，并且按 provider order 串行调用。

本计划只使用 Python 标准库 `concurrent.futures`、`os` 和测试中的 `threading`。不要引入新的第三方依赖，不要把并发调度放进 `core/loop.py`，也不要让具体工具自行创建线程池。

## Change Note

2026-06-05 / Codex: 新增本 ExecPlan，记录用户确认后的只读工具并发执行设计。计划选择保守的同步 executor 内部分批实现，保持主循环和公开 executor 协议不变，并把 `files_read` 迁移为 executor 顺序后处理，以消除并发 handler 对共享 runtime state 的直接写入。

2026-06-05 / Codex: 完成实现并更新计划状态。实际实现使用 `_ReadyToolCall`、`_HandlerOutcome`、`_preflight_one()`、`_run_handler()`、`_finalize_outcome()` 和 `_execute_concurrency_candidate_batch()` 拆分 executor；验证命令均通过，并同步更新 TD-006，说明并发调度已落地但 durable result store 仍未完成。
