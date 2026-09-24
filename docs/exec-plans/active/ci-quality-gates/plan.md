# 引入本地质量门禁与 GitHub CI

本执行计划是活文档。实施过程中必须持续更新 [progress.md](./progress.md)、[decisions.md](./decisions.md) 和 [execution.md](./execution.md)，并遵循仓库根目录的 [PLANS.md](../../../../PLANS.md)。当全部验收条件满足后，将整个 `docs/exec-plans/active/ci-quality-gates/` 目录移动到 `docs/exec-plans/completed/ci-quality-gates/`。

## Purpose

完成后，OneCode 开发者在日常编辑和提交代码时会得到快速、分层的质量反馈：开发中与提交前只运行受当前改动影响的测试，Ruff 会在提交前自动执行安全修正和格式化；每次 push 前会执行 Ruff、Pyright 和全量测试；代码 push 到 GitHub 或更新 pull request 后，GitHub Actions 会在干净的 Python 3.11 环境中重新执行不可修改代码的全量验证。

开发者可以通过 `uv run pre-commit run --all-files` 和 `uv run pre-commit run --hook-stage pre-push --all-files` 验证本地门禁，并在 GitHub commit 或 pull request 页面看到稳定命名的 `quality` 与 `tests` 检查。任一检查失败时，提交、push 或合并应停在对应边界，并给出可直接在本地复现的命令。

## Scope

本计划覆盖 `pyproject.toml` 与 `uv.lock` 中的开发工具依赖、Ruff 与 Pyright 配置、`.gitignore` 中的工具缓存、`.pre-commit-config.yaml` 中的 commit/push hooks、`.github/workflows/ci.yml` 中的远端工作流，以及 `README.md` 中面向贡献者的安装和验证说明。实施期间还要清理现有 Ruff、格式、Pyright 和 pytest 基线，使新门禁从启用之日起保持绿色。

Ruff 检查项目生产 Python 包、根目录维护脚本和 `tests/`，不检查 `docs/`、`reference/`、`lessons/` 等参考材料。Pyright 第一阶段只检查生产包 `application/`、`core/`、`infrastructure/`、`prompts/`、`services/`、`tools/`、`ui/` 和 `utils/`，不检查测试。GitHub CI 只验证，不自动提交或推送修复。发布、构建产物上传、多操作系统矩阵、覆盖率阈值、Ruff preview/额外规则、Pyright `standard`/`strict` 和测试并行化不在本计划范围内。

## Design References

- [Ruff 与 Pyright 引入 CI 调研](../../../references/ruff-pyright-ci-research.md)：记录现有仓库状态、工具职责、安装方式和初始配置建议。本计划在其基础上补充本地增量测试、Git hooks、基线迁移和远端工作流。
- [AGENTS.md](../../../../AGENTS.md)：规定 OneCode 使用 uv、验证命令、依赖边界和不覆盖无关工作区改动等仓库级约束。
- [PLANS.md](../../../../PLANS.md)：规定本计划必须保持自包含、可恢复、可观察并随执行更新。

## Context And Orientation

OneCode 是一个以 uv 管理环境的 Python 项目。`pyproject.toml` 当前声明 Python `>=3.11`，`[dependency-groups].dev` 只有 pytest，`uv.lock` 已提交。仓库当前没有 `.github/workflows/`、`.pre-commit-config.yaml`、Ruff 配置或 Pyright 配置。测试位于 `tests/`；当前版本控制中有 372 个 Python 文件，pytest 收集到 820 个测试。

“受影响测试”指依赖当前改动所触及代码的测试，而不是仅按测试文件名猜测。计划使用 `pytest-testmon`：它在 `.testmondata` 中记录每个测试实际执行过的代码，后续比较代码变化并选择可能受影响的测试。这个数据库只服务本地快速反馈，不提交到 Git，也不作为远端 CI 的正确性依据。首次使用或数据库失效时必须运行一次全量测试建立基线。

“安全修正”指 Ruff 无需 `--unsafe-fixes` 就愿意执行的修改。commit hook 先运行 `ruff check --fix`，再运行 `ruff format`，因为 lint 修正可能产生需要重新格式化的代码。自动修改文件后，pre-commit 会阻止本次提交，开发者检查并重新暂存修改后再次提交。

当前工作区包含大量用户改动，不能在这些改动尚未稳定时直接进行全仓格式化。2026-09-24 的只读基线显示 Ruff 0.16.8 默认规则在生产目录和 tests 中报告 528 条诊断，其中 348 条可安全修复；209 个文件需要格式化。全量 pytest 在 Python 3.12 环境中得到 815 passed、5 failed，耗时约 156 秒。Pyright 基线尚未采集。因此，启用门禁前必须先完成独立、可审查的基线迁移。

## Plan Of Work

具体批次、命令和每批完成条件见 [execution.md](./execution.md)。工作按依赖关系分成三个里程碑：先得到可锁定的绿色基线，再建立依赖该基线的本地 hooks，最后让 GitHub 在干净环境中重复全量验证。

### Milestone 1: 建立可复现且全绿的质量基线

将 Ruff、Pyright、pre-commit 和 pytest-testmon 纳入 uv 的开发依赖并更新锁文件；在 `pyproject.toml` 中配置 Python 3.11 目标、Ruff 扫描边界和 Pyright `basic` 生产代码范围。随后先记录完整诊断，再执行 Ruff 安全自动修复和统一格式化，人工处理剩余 Ruff/Pyright 问题，并修复或协调当前测试失败。机械格式化应与语义修复分开审查，任何现有用户改动都必须保留。

该里程碑完成时，从仓库根目录运行 Ruff lint、Ruff format check、Pyright、依赖边界测试和全量 pytest 都以退出码 0 结束，`uv sync --locked --dev` 不改变锁文件。

### Milestone 2: 建立快速的本地开发、commit 和 push 门禁

使用 `.pre-commit-config.yaml` 管理两种 Git hook。pre-commit 阶段只对已暂存 Python 文件运行 Ruff 安全修正与格式化，并在生产代码、测试或依赖配置变化时运行 pytest-testmon 选择的受影响测试。pre-push 阶段无条件运行全仓 Ruff 检查、格式检查、Pyright 和全量 pytest，同时刷新 testmon 数据库。`README.md` 解释首次同步、testmon 初始化、hook 安装、手动复现和紧急绕过的含义。

该里程碑完成时，干净基线上的两种 hook 都通过；构造一个不符合格式的暂存 Python 改动时，commit hook 会修改文件并阻止提交；修复后 commit hook 只运行受影响测试；pre-push 始终执行全量测试并在失败时阻止 push。

### Milestone 3: 建立远端全量 GitHub CI 和合并门禁

新增 `.github/workflows/ci.yml`，在每次 push 与 pull request 上运行。工作流只授予读取仓库内容的权限，用固定 commit SHA 引用官方 checkout 与 setup-uv actions，在 Python 3.11 下执行锁定同步，并将质量检查与全量测试拆成并行的 `quality` 和 `tests` jobs。远端不使用 testmon、不运行 Ruff `--fix`，因此结果不依赖本地缓存，也不会静默改写贡献者代码。

如果具备 GitHub 仓库管理权限，将 `quality` 和 `tests` 配置为主分支所需状态检查；若没有权限，在 `progress.md` 记录具体的外部阻塞和待执行设置，但工作流文件本身仍须在真实 push 或 pull request 上验证。

该里程碑完成时，GitHub 上一次真实运行的两个 jobs 均通过；人为引入 Ruff、Pyright 或 pytest 失败的临时分支会使对应 job 失败；恢复代码后重跑转绿。主分支保护启用时，不通过这些检查的 pull request 无法合并。

## Validation And Acceptance

整个计划只有在以下行为同时可观察时才算完成：全新或清理后的环境可用 `uv sync --locked --dev` 重建；Ruff 在当前锁定版本的默认规则下零诊断且格式检查不产生 diff；Pyright `basic` 对约定生产目录零错误；全量 pytest 至少覆盖当前 820 个测试且零失败；本地 pre-commit 自动修正/格式化并运行受影响测试；本地 pre-push 无条件运行全量质量检查和测试；GitHub 的 `quality` 与 `tests` 在 push 和 pull request 上执行相同的只读门禁。

验收还要求 `git diff --check` 通过，`uv lock --check` 或当前 uv 对应的锁文件检查通过，并确认 `.testmondata`、`.ruff_cache/`、`.venv/` 等本地产物没有进入版本控制。实际命令与结果必须记录到 [progress.md](./progress.md)。

## Recovery

基线迁移是风险最高的步骤。执行前先用 `git status --short` 记录现有改动，不得用 reset、checkout 或批量覆盖丢弃用户工作。先运行只读 Ruff/Pyright/pytest 命令，再运行 Ruff 安全修正；每次自动修改后检查 `git diff --stat` 和代表性 diff。若格式化与正在进行的功能改动产生难以审查的混合，停止启用 hooks，把基线迁移延后到功能改动稳定后，而不是回滚用户文件。

uv 依赖添加、锁文件生成、Ruff 修正、格式化和测试命令都是可重复执行的。`.testmondata` 损坏或结果可疑时，删除的只是可再生缓存；重新运行 `uv run python -m pytest --testmon-noselect tests -q` 即可建立数据库。远端 CI 与本地结果不一致时，优先用 Python 3.11 和 `uv sync --locked --dev` 在干净工作树复现，不通过增加全局忽略或降低检查范围掩盖错误。

## Related Documents

- [Decisions](./decisions.md)
- [Execution](./execution.md)
- [Progress](./progress.md)

## Revision Note

2026-09-24：创建四文件执行计划包，将已确认的 Ruff/Pyright 基线、testmon 增量策略、本地 Git hooks 和 GitHub 全量 CI 组织为三个依赖有序的里程碑。
