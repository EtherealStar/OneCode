# Progress

本文件在每个实施停止点更新。完成的步骤必须带时间和证据；部分完成的批次要拆出已完成与剩余内容。计划全部完成后，把整个目录移动到 `docs/exec-plans/completed/ci-quality-gates/`。

## Current State

- Status: Milestones 1–2 complete and committed（`9627fd9`、`ede0a84`）；Milestone 3 Batch 1 已完成本地部分（workflow 文件 + 本地基线校验），Batch 2 远端验证待提交/推送授权。Milestone 3 改动按用户要求**未提交**。
- Current milestone: Milestone 3 — 建立远端全量 GitHub CI 和合并门禁（Batch 1 本地部分完成，远端验收待推送）
- Last updated: 2026-09-24 13:52 CST

## Milestones And Batches

- [x] (2026-09-24 12:06 CST) 完成只读现状检查并创建执行计划包。
- [x] Milestone 1: 建立可复现且全绿的质量基线
  - [x] Batch 1: 锁定工具依赖与扫描边界
  - [x] Batch 2: 清理 Ruff、格式、Pyright 和 pytest 基线
- [x] Milestone 2: 建立快速的本地开发、commit 和 push 门禁
  - [x] Batch 1: 建立并验证 testmon 增量测试数据库
  - [x] Batch 2: 配置 pre-commit 与 pre-push hooks
- [ ] Milestone 3: 建立远端全量 GitHub CI 和合并门禁
  - [~] Batch 1: 添加只读、可复现的 GitHub Actions workflow（文件已新增并本地校验；远端真实运行待推送后确认）
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

- Observation: `ruff format`/`ruff check` 在请求显式文件时会忽略 `[tool.ruff].exclude`，因此 pre-commit 把 `reference/` 文件作为参数传给 hook 时仍会被改写。这会让本应排除的参考代码被格式化。
  Evidence: 首次 `pre-commit run --all-files` 改写了 24 个 `reference/ui/*.py`；在 hook 上增加 `exclude: ^(docs|reference|lessons)/` 后同一命令不再触碰这些文件（`git status` 仅剩待新增配置）。

- Observation: pytest-testmon 2.2.0 按 AST/方法指纹而非原始文本判断改动，纯注释/空白改动不会选择测试；语义改动才会。Python 3.14 下 coverage 使用 sysmon core 会给出 “dynamic contexts … incomplete” 警告，但实测依赖记录与选择仍然有效。
  Evidence: 追加注释后 `--testmon` 报告 `changed files: 0`、`no tests ran`；修改 `PROTECTED_PROJECT_DIRS` 常量后报告 `changed files: 10` 并选择 33 个测试（含 `tests/test_permission_policy.py`）。

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

### Milestone 2 执行证据（2026-09-24，Windows，Python 3.14.5，pytest-testmon 2.2.0，pre-commit 4.6.2，Ruff 0.16.8）

- Command: `uv run python -m pytest --testmon-noselect tests -q`
  Result: `820 passed` in 63.90s（首次）与 57.64s（刷新）；生成 `.testmondata`（684KB，820 test_execution、1803 file fingerprint、2083 依赖边）。

- Command: `uv run python -m pytest --testmon tests -q`（无代码变化）
  Result: `no tests ran in 0.02s`，选择 0 个测试，明显少于全量。

- Command: 修改 `services/permissions/policy.py` 的 `PROTECTED_PROJECT_DIRS` 常量后 `uv run python -m pytest --testmon tests`
  Result: `changed files: 10, unchanged files: 265`；`collected 107 items / 74 deselected / 33 selected`，包含 `tests/test_permission_policy.py`；验证后已 `git checkout` 恢复。
  注意：先前的纯注释改动被正确忽略（`changed files: 0`），说明 testmon 基于 AST 指纹而非文本。

- Command: `uv run pre-commit run --all-files`（修复 hook exclude 后，干净基线）
  Result: `ruff check --fix (staged)`、`ruff format (staged)`、`pytest affected tests (testmon)` 全部 Passed，退出码 0，工作区无额外改动。

- Command: `uv run pre-commit run --hook-stage pre-push --all-files`（干净基线）
  Result: `ruff check (all)`、`ruff format --check (all)`、`pyright`、`pytest full suite (testmon refresh)` 全部 Passed，退出码 0。

- Command: `uv run pre-commit install --hook-type pre-commit --hook-type pre-push`
  Result: 生成 `.git/hooks/pre-commit` 与 `.git/hooks/pre-push`。

- 失败路径验证 1（格式）：暂存一个未格式化的 `_tmp_format_probe.py` 后 `uv run pre-commit run`
  Result: `ruff check --fix` Passed，`ruff format` 改写文件并 `Failed`，退出码 1；确认 hooks 会阻止提交。验证后已 unstage 并删除临时文件。

- 失败路径验证 2（测试）：临时新增 `tests/test_tmp_fail_probe.py`（`assert False`）后 `uv run pre-commit run --hook-stage pre-push --all-files`
  Result: 前三个 hook Passed，`pytest full suite` Failed（`1 failed, 820 passed`），退出码 1；确认 pre-push 会阻止推送。验证后已删除临时文件并 `--testmon-noselect` 刷新数据库（`820 passed`）。

- Command: 生产模块临时改动经 pre-commit commit 阶段
  Result: 语义改动 `services/permissions/policy.py` 触发 `pytest affected tests (testmon)` 并 Passed（底层 `--testmon` 选择 33 个测试）；恢复后工作区干净。

- 偏差：本里程碑仍在 Python 3.14.5 + Windows 上验证（与 Milestone 1 一致）；testmon 数据库只服务本地，pre-push 与后续 CI 仍以全量 `--testmon-noselect`/普通 pytest 为事实来源。

### Milestone 3 执行证据（2026-09-24，Windows，本地 Python 3.14.5）

- Command: `git ls-remote` / GitHub API 校验 action 版本
  Result: `actions/checkout` v7.0.1 = `3d3c42e5aac5ba805825da76410c181273ba90b1`、`astral-sh/setup-uv` v9.0.0 = `c771a70e6277c0a99b617c7a806ffedaca235ff9`，与计划一致；SHA 未升级，无需新决策。
  （本地 shell 无直连 GitHub 网络，版本号与 SHA 通过 GitHub REST API 只读校验。）

- 新增 `.github/workflows/ci.yml`：`name: CI`，监听全部 `push` 与 `pull_request`；`concurrency` 以 `${{ github.workflow }}-${{ github.ref }}` 取消同 ref 旧运行；顶层权限仅 `contents: read`；`quality` 与 `tests` 两个 job 各自 `ubuntu-latest`、固定 SHA 的 checkout/setup-uv、启用 uv 缓存。
  Result: 文件为唯一工作区改动（`git status --short` 仅 `?? .github/`）。

- Command: `uv run python -c "import yaml; yaml.safe_load(...)"`
  Result: YAML 解析通过，顶层键为 `name`、`on`、`concurrency`、`permissions`、`jobs`，jobs 为 `quality`/`tests`。

- Command: `uv run ruff check --output-format=github .`
  Result: 退出码 0（无 GitHub 注解输出）。

- Command: `uv run ruff format --check .`
  Result: `373 files already formatted`，退出码 0。

- Command: `uv run pyright`
  Result: `0 errors, 1 warning, 0 informations`，退出码 0。

- Command: `uv run python -m pytest tests -q`
  Result: `820 passed in 45.63s`，退出码 0。

- 待完成（阻塞）：Batch 1 的远端验收与 Batch 2 需要一次真实 `git commit` + `git push` 到 `origin`（`https://github.com/EtherealStar/OneCode.git`）。用户当前要求“先不提交”，且本会话 shell 无直连 GitHub 网络，因此远端运行、失败路径验证和主分支 required status checks 尚未执行。
  待执行项：推送后确认 `quality`/`tests` 在 push 与 pull request 上创建并通过；在临时分支构造 Ruff/Pyright/pytest 失败确认红灯后恢复转绿；如具备仓库管理权限，把 `quality`、`tests` 设为 required status checks。

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

Milestone 2 已完成并验证：`.testmondata` 已建立且未被跟踪，无代码变化时增量运行选择 0 个测试，语义改动能选择覆盖它的测试；`.pre-commit-config.yaml` 的 commit/pre-push 两层 hooks 在干净基线上均退出码 0，格式问题会被改写并阻止提交，失败测试会阻止 push；`README.md` 增补了三层门禁的同步、testmon 初始化、hook 安装、手动复现与 `--no-verify` 说明。

Milestone 2 实施决策与偏差：

- 按用户明确要求，Milestone 2 的改动**未提交**；`README.md`（已修改）与 `.pre-commit-config.yaml`（新增）保留在工作区审核。
- hook 级 `exclude: ^(docs|reference|lessons)/` 是必需的：Ruff 的 `[tool.ruff].exclude` 不作用于显式传入的文件，详见 `decisions.md`。
- 两个失败路径的临时文件与生产代码验证改动均已恢复，工作区除本计划交付物外保持干净。

Milestone 3 Batch 1 已新增 `.github/workflows/ci.yml` 并在本地通过等价质量命令与全量测试校验；action 版本与固定 SHA 已用 GitHub API 复核。远端真实运行（Batch 1 验收）与失败/恢复路径、required status checks（Batch 2）待获得提交/推送授权后执行；本会话 shell 无直连 GitHub 网络。为此计划目录暂不移动到 `completed/`。
