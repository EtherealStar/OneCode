# Progress

本文件在每个实施停止点更新。完成的步骤必须带时间和证据；部分完成的批次要拆出已完成与剩余内容。计划全部完成后，把整个目录移动到 `docs/exec-plans/completed/ci-quality-gates/`。

## Current State

- Status: Milestone 1 complete (blocked from commit by explicit user request); Milestones 2–3 not started
- Current milestone: Milestone 1 — 建立可复现且全绿的质量基线
- Last updated: 2026-09-24 CST

## Milestones And Batches

- [x] (2026-09-24 12:06 CST) 完成只读现状检查并创建执行计划包。
- [x] Milestone 1: 建立可复现且全绿的质量基线
  - [x] Batch 1: 锁定工具依赖与扫描边界
  - [x] Batch 2: 清理 Ruff、格式、Pyright 和 pytest 基线
- [ ] Milestone 2: 建立快速的本地开发、commit 和 push 门禁
  - [ ] Batch 1: 建立并验证 testmon 增量测试数据库
  - [ ] Batch 2: 配置 pre-commit 与 pre-push hooks
- [ ] Milestone 3: 建立远端全量 GitHub CI 和合并门禁
  - [ ] Batch 1: 添加只读、可复现的 GitHub Actions workflow
  - [ ] Batch 2: 验证失败路径并配置所需检查

## Surprises And Discoveries

- Observation: 当前 Ruff 0.16.8 的默认规则集比调研报告描述的早期默认集更广，因此“采用默认规则”不是一个低诊断量迁移。
  Evidence: `uvx ruff check --isolated application core infrastructure prompts services tools ui utils tests --statistics` 报告 528 errors，其中 348 fixable。

- Observation: 项目已有较大格式化迁移成本，不能把 formatter 门禁直接打开。
  Evidence: `uvx ruff format --isolated --check application core infrastructure prompts services tools ui utils tests` 报告 209 files would be reformatted、163 files already formatted。

- Observation: 当前全量测试不是绿色，且耗时足以证明每个 commit 都执行全量测试会拖慢开发内环。
  Evidence: `uv run python -m pytest tests -q` 在 Python 3.12 下得到 815 passed、5 failed in 143.79s；包含环境命令总耗时 156.21s。

- Observation: 当前仓库没有 GitHub Actions、pre-commit、Ruff 或 Pyright 配置。
  Evidence: `pyproject.toml` 的 dev group 只有 pytest；`.github/workflows/` 与 `.pre-commit-config.yaml` 不存在。

- Observation: 当前工作区已有大量用户修改和未跟踪文件，基线自动修正必须等这些工作稳定并逐步审查。
  Evidence: 2026-09-24 的 `git status --short` 显示多个生产、测试和文档文件已修改；本计划撰写过程没有修改这些既有文件。

- Observation: Ruff 0.16.8 实际默认启用约 413 条规则，包含 BLE001、S110、RUF、FLY、TRY、DTZ、SIM、ASYNC、PYI、PIE、PLW、PLC 等；因此“零诊断”需要大量语义处理，而不能只靠安全自动修复。
  Evidence: `ruff check --show-settings` 的 `linter.rules.enabled` 列表；修复后剩余 183 条中 97 条为 BLE001、14 条为 S110。

- Observation: Ruff 的 F401 安全修复会删除 facade 模块中未在本文件引用的“转导出”导入，从而破坏 `from ui.cli import renderer` 的调用方。
  Evidence: 修复后 `tests/test_cli_commands.py`、`tests/test_cli_resume.py`、`tests/test_cli_terminal.py` 等 32 个测试因 `ui.cli.renderer` 缺少 `render_to_text`/`render_memory`/`render_banner` 等到属性而失败；`ui/cli/renderer.py` 中的 8 个转导出被删除。

- Observation: 实施前 Pyright `basic` 在生产八目录报告 140 个错误，但大多来自少数根因，而非 140 个独立缺陷。
  Evidence: `CliRuntime | None`（repl.py 31 个）、`ToolHandler` 仅为同步签名（8 个异步 handler）、`ToolExecutor` Protocol 的 `state: object` 与 `file_state_cache` 缺失（多个实现不匹配）；修复根因后降至个位数。

- Observation: TTY 信任提示 `default_trust_prompt` 返回 `"t"`/`"s"`，而 `application.runtime` 只把 `"trust"` 视为信任，导致 TTY `/connect` 首配路径永远按跳过处理。
  Evidence: `application/runtime.py:880` 的 `response == "trust"` 判断与 `ui/cli/terminal/trust_prompt.py` 的 `ConfirmOption("t", ...)`；已在 M1 修复为返回 `TrustChoice`。

- Observation: `tests/test_conversation_view.py::test_unmounted_message_reenters_with_full_content` 依赖运行顺序与初始滚动位置，是既有不稳定测试，且 M1 的排序/格式改动曾短暂放大它。
  Evidence: 单独运行偶发失败；在同一进程先运行任意其他 `ConversationApp` 测试后必失败。诊断（临时 worktree 对比 HEAD）显示失败在“重新挂载”断言：`scroll_to(y=0)` 是异步的，初始滚动位置也可能已在底部，断言前必须等待滚动/挂载结算。现改为显式 `scroll_to(0)` + 轮询等待；full suite 连续两次 820 passed。

## Validation Evidence

### 实施前只读基线（2026-09-24）

- Command: `uv run python -m pytest tests -q --collect-only`
  Result: 820 tests collected in 33.27s。

- Command: `uv run python -m pytest tests -q`
  Result: 815 passed、5 failed；失败位于 `tests/test_cli_terminal.py`（2）、`tests/test_mcp_manager.py`、`tests/test_path_sandbox_guard.py` 和 `tests/test_search_tools.py`。

- Command: `uvx ruff check --isolated application core infrastructure prompts services tools ui utils tests --statistics`
  Result: 528 errors，348 条可由安全 `--fix` 修复，另有 23 个隐藏的 unsafe fixes；本计划禁止自动启用 unsafe fixes。

- Command: `uvx ruff format --isolated --check application core infrastructure prompts services tools ui utils tests`
  Result: 209 files would be reformatted，163 files already formatted。

- Command: `find .github -type f -maxdepth 4 -print` 与 pre-commit 配置搜索
  Result: 未发现现有 workflow 或 pre-commit 配置。

### Milestone 1 执行证据（2026-09-24，Windows，Python 3.14.5，Ruff 0.16.8，Pyright 1.1.414）

- Command: `uv add --dev ruff pyright pre-commit pytest-testmon`
  Result: 解析 68 packages；`pyproject.toml` 的 dev group 现为 pre-commit、pyright、pytest、pytest-testmon、ruff；`uv.lock` 仅新增条目（+320/-1 行），未改动既有依赖版本。

- Command: `uv run ruff --version` / `uv run pyright --version` / `uv run pre-commit --version` / `uv run python -m pytest --help`
  Result: `ruff 0.16.8`、`pyright 1.1.414`、`pre-commit 4.6.2`，pytest `--help` 正常；Batch 1 完成条件满足。

- Command: `uv run python -m pytest tests -q`（基线重采集，实施前）
  Result: 818 passed、2 failed（`tests/test_conversation_view.py` 的挂载 flake 与 `tests/test_search_tools.py` 的 prompt 前缀）；计划记录的 5 个失败中，ANSI/stdio/权限相关 3 个在 Python 3.14/Windows 下不复现。

- 注意（偏差）：本里程碑在仓库开发解释器 Python 3.14.5 + Windows 上执行。Ruff/Pyright 均为版本锁定且与解释器无关；Python 3.11 的干净环境全量验证按计划由 Milestone 3 的 GitHub Actions 承担。计划记录的实施前基数为 528（`--isolated`，显式目录），本配置下为 546，差异来自根目录维护脚本进入扫描范围与配置 include/exclude 不同。

- Command: `uv run ruff check . --statistics`（配置后、修复前）
  Result: 546 errors；首次 `--fix` 修复 410，`ruff format .` 重排 209 文件；随后 `ruff check --add-noqa` 为 97 处刻意的宽泛异常捕获添加行级 `# noqa: BLE001`（其中 14 处同时 `S110`）。

- Command: `uv run ruff check .`（最终）
  Result: `All checks passed!`，退出码 0。

- Command: `uv run ruff format --check .`（最终）
  Result: `373 files already formatted`，退出码 0。

- Command: `uv run pyright`（最终）
  Result: `0 errors, 1 warning, 0 informations`，退出码 0。唯一 warning 是 `ui/tui/conversation/__init__.py` 的惰性 `__all__` 静态不可解析提示。

- Command: `uv run python -m pytest tests/test_import_boundaries.py -q`
  Result: 8 passed。

- Command: `uv run python -m pytest tests -q`（最终）
  Result: 820 passed in 30.67s，退出码 0。

- Command: `git diff --check`
  Result: 退出码 0。

- Command: `uv sync --locked --dev` 与 `uv lock --check`
  Result: 均退出码 0，第二次 sync 不修改 `pyproject.toml`/`uv.lock`。

- Command: `git check-ignore .ruff_cache .testmondata .venv`
  Result: 三者均被忽略；`git status --short` 中无工具缓存产物。

## Artifacts And Notes

- 计划入口：[plan.md](./plan.md)
- 具体实施：[execution.md](./execution.md)
- 已定决策：[decisions.md](./decisions.md)
- 调研依据：[Ruff 与 Pyright 引入 CI 调研](../../../references/ruff-pyright-ci-research.md)
- 当前基线来自包含用户未提交改动的工作树；实施时必须重新采集，不能把这里的数量当成永久预期。

## Outcomes And Retrospective

Milestone 1 已完成并全绿：Ruff 默认规则零诊断且格式检查无 diff，Pyright `basic` 零错误，全量 820 个测试零失败，`uv sync --locked --dev`/`uv lock --check`/`git diff --check` 均通过。

实施约束与偏差：

- 按用户明确要求，Milestone 1 的改动**未提交**；工作区保留全部改动供审查。原计划允许在批次完成时提交，此处以用户指令为准。
- BLE001/S110 与运行时的“故障隔离”语义冲突（工具、MCP、UI、后台任务边界刻意吞掉宽泛异常以保活），因此按 `decisions.md` 记录，采用**行级 `# noqa: BLE001/S110`**（97 处）而不是全局 ignore；新代码仍受规则约束。
- Pyright 修复以根因（类型别名/Protocol/可空性）为主，仅对鸭子类型第三方对象使用少量 `# type: ignore[...]`（如 prompt_toolkit 动态属性、tree-sitter Parser）。
- 发现并修复了 TTY 信任提示的真实缺陷；更新了两处过时/不稳定的测试（search 工具 prompt 前缀、REPL 退出信号、conversation-view 挂载等待），没有删除任何测试。
- 机械格式化（209 文件）与语义修复在同一工作树中；两批改动可通过 `git diff` 按类型区分，但仍共享一次未提交的变更集。

后续：启用 hooks/workflow 前需先完成 Milestone 2 的 testmon 基线与 `.pre-commit-config.yaml`，以及 Milestone 3 的 GitHub Actions。
