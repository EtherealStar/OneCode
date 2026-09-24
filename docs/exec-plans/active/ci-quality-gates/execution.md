# Execution

所有命令均从仓库根目录 `/mnt/d/study/OneCode` 执行。每完成一个批次，都要立即把命令、退出码、关键输出和任何偏差记录到 [progress.md](./progress.md)。不得在当前大量用户改动上未经审查地批量格式化，也不得用 `git reset --hard`、`git checkout --` 或删除工作区的方式恢复。

## Milestone 1: 建立可复现且全绿的质量基线

### Batch 1: 锁定工具依赖与扫描边界

先运行 `git status --short`，把当前改动状态摘要记录到 `progress.md`。确认 `pyproject.toml`、`uv.lock` 和计划将触及的配置文件没有来自其他工作的冲突修改。使用 uv 将 `ruff`、`pyright`、`pre-commit` 和 `pytest-testmon` 加入 `[dependency-groups].dev`，让具体解析版本进入 `uv.lock`；不要另外使用 Ruff action、npm 全局 Pyright 或独立 pre-commit 环境维护第二套工具版本。

建议命令为：

    uv add --dev ruff pyright pre-commit pytest-testmon
    uv sync --locked --dev

在 `pyproject.toml` 增加 `[tool.ruff]`，设置 `target-version = "py311"`，把输入限制为 `*.py` 与 `*.pyi`，并排除 `docs/`、`reference/`、`lessons/` 和生成目录。不要增加 `[tool.ruff.lint].select`、`extend-select`、`ignore` 或 preview；“默认规则”由 uv 锁定的 Ruff 版本决定，未来升级 Ruff 时应把默认规则变化作为显式升级审查。formatter 保持 Ruff 默认值。

在 `pyproject.toml` 增加 `[tool.pyright]`，设置 `pythonVersion = "3.11"`、`typeCheckingMode = "basic"`，并明确 include 八个生产目录：`application`、`core`、`infrastructure`、`prompts`、`services`、`tools`、`ui`、`utils`。tests 和参考目录不进入第一阶段 Pyright 范围。不要预先配置 `venvPath`、全局诊断关闭或 editable-install 兼容选项；只有实际出现导入解析问题时才能在 `decisions.md` 记录证据后调整。

在 `.gitignore` 增加 `.ruff_cache/` 和 `.testmondata*`。确认 `.venv/`、`.pytest_cache/`、构建目录和 egg-info 仍然被忽略。

完成条件：`uv sync --locked --dev` 成功，`uv run ruff --version`、`uv run pyright --version`、`uv run pre-commit --version` 和 `uv run python -m pytest --help` 均可执行，第二次运行 `uv sync --locked --dev` 不修改 `pyproject.toml` 或 `uv.lock`。

### Batch 2: 清理 Ruff、格式、Pyright 和 pytest 基线

在修改代码前采集一次只读基线，并把统计写入 `progress.md`：

    uv run ruff check . --statistics
    uv run ruff format --check .
    uv run pyright
    uv run python -m pytest tests -q

确认当前用户工作已稳定后，先执行 Ruff 安全修正，再执行 formatter。禁止使用 `--unsafe-fixes`。

    uv run ruff check --fix .
    uv run ruff format .

检查 `git diff --stat`、`git diff --check` 和代表性文件 diff。将纯格式变化保持为可独立审查的变更集；剩余 Ruff 诊断、Pyright 错误和 pytest 失败作为语义修复处理。优先修复真实代码或注解问题；只有规则与项目语义确实冲突时才添加最窄的行级或文件级例外，并在 `decisions.md` 记录规则编号、路径和理由。不得用全局 ignore 消除整类现有错误。

当前已知 pytest 失败分别涉及 `tests/test_cli_terminal.py` 的两个 ANSI 样式断言、`tests/test_mcp_manager.py` 的 stdio MCP 超时、`tests/test_path_sandbox_guard.py` 的根 worktree 权限预期和 `tests/test_search_tools.py` 的 prompt 前缀预期。执行者必须先判断这些失败是否来自尚未完成的用户改动，再在对应所有者模块中修复实现或更新已过时测试，不能仅为 CI 删除测试。

完成条件：以下命令全部以退出码 0 结束，且最后两个只读 Ruff 命令不修改文件：

    uv run ruff check .
    uv run ruff format --check .
    uv run pyright
    uv run python -m pytest tests/test_import_boundaries.py -q
    uv run python -m pytest tests -q
    git diff --check

## Milestone 2: 建立快速的本地开发、commit 和 push 门禁

### Batch 1: 建立并验证 testmon 增量测试数据库

使用全量模式建立 `.testmondata`。`--testmon-noselect` 表示执行全部测试但仍收集依赖关系；首次运行应收集并通过所有测试。

    uv run python -m pytest --testmon-noselect tests -q

在没有代码变化时再运行增量模式：

    uv run python -m pytest --testmon tests -q

预期第二次运行只选择零个或极少量需要重新验证的测试，而不是重复执行 820 个测试。随后在一个明确模块做可撤销的临时编辑，确认对应测试被选中；恢复临时编辑后不要把验证用改动留在工作区。如果 testmon 在本项目动态导入场景中漏选已知依赖测试，记录复现并停止将它接入 commit hook，改为在 `decisions.md` 裁定保守的模块到测试映射；无论如何，pre-push 和远端仍保持全量测试。

完成条件：`.testmondata` 未被 Git 跟踪，首次全量运行通过，随后增量运行明显少于全量，且一个已知生产模块改动能选择至少一个覆盖它的测试。

### Batch 2: 配置 pre-commit 与 pre-push hooks

新增 `.pre-commit-config.yaml`，使用 `repo: local` 和 `language: system`，让 hook 通过 `uv run --frozen` 使用项目锁定环境。不要使用 `astral-sh/ruff-pre-commit`，否则 Ruff 版本会同时存在于 hook revision 与 uv.lock 两处。

pre-commit 阶段按固定顺序定义三个 hooks：

1. `uv run --frozen ruff check --fix`，接收 pre-commit 传入的暂存 Python/pyi 文件。
2. `uv run --frozen ruff format`，接收同一批文件。
3. `uv run --frozen python -m pytest --testmon tests -q`，设置 `pass_filenames: false`，并只在生产 Python 目录、`tests/`、`pyproject.toml` 或 `uv.lock` 变化时触发。

pre-push 阶段定义四个 `always_run: true`、`pass_filenames: false` 的 hooks，分别执行：

    uv run --frozen ruff check .
    uv run --frozen ruff format --check .
    uv run --frozen pyright
    uv run --frozen python -m pytest --testmon-noselect tests -q

不要把四条命令塞进平台相关的 shell 脚本；分开的 hook 名称能明确显示具体失败边界，并能在 Windows、WSL 和 GitHub 开发环境中复用 uv 命令。

实施说明（2026-09-24）：两个 Ruff hook 必须显式声明 `exclude: ^(docs|reference|lessons)/`。pre-commit 以显式文件参数调用 `ruff`，而 Ruff 的 `[tool.ruff].exclude` 不作用于显式传入的文件；缺少该 exclude 时 `reference/` 会被格式化。`pytest-testmon` hook 的 `files` 白名单已限定范围为 `application|core|infrastructure|prompts|services|tools|ui|utils|tests` 目录及 `pyproject.toml`、`uv.lock`，无需额外 exclude。细节见 [decisions.md](./decisions.md)。

安装并验证两种 hook：

    uv run pre-commit install --hook-type pre-commit --hook-type pre-push
    uv run pre-commit run --all-files
    uv run pre-commit run --hook-stage pre-push --all-files

在 `README.md` 的开发说明中写明 `uv sync --locked --dev`、testmon 首次初始化、hook 安装、三层验证命令和 `--no-verify` 会绕过本地检查但不能绕过 GitHub CI。说明 Ruff 自动修改后需要重新检查和暂存文件。

完成条件：干净基线上的两类 hook 以退出码 0 结束；一个临时格式问题会被 Ruff 修改并阻止首次 commit；一个受测生产代码临时改动会触发相关测试；一个临时失败测试会阻止 pre-push；所有验证用临时改动均已安全恢复。

## Milestone 3: 建立远端全量 GitHub CI 和合并门禁

### Batch 1: 添加只读、可复现的 GitHub Actions workflow

新增 `.github/workflows/ci.yml`，名称使用 `CI`，触发器覆盖所有 `push` 和 `pull_request`。配置 concurrency，使同一事件和分支上的旧运行被新 commit 取消；保持 push 与 pull request 都存在，即使同仓库已打开 PR 的分支可能短暂产生重复运行，先满足每次 push 都有远端验证的要求。

工作流顶层只授予 `contents: read`。定义稳定命名的 `quality` 与 `tests` jobs，均运行在 `ubuntu-latest`，使用带 release 注释的完整 commit SHA 固定 `actions/checkout` 和 `astral-sh/setup-uv`。计划编写时已验证的版本是 checkout v7.0.1（`3d3c42e5aac5ba805825da76410c181273ba90b1`）和 setup-uv v9.0.0（`c771a70e6277c0a99b617c7a806ffedaca235ff9`）；实施时若升级，必须在 `decisions.md` 记录新 SHA、版本与理由。

每个 job 先执行：

    uv python install 3.11
    uv sync --locked --dev --python 3.11

`quality` 随后执行：

    uv run --python 3.11 ruff check --output-format=github .
    uv run --python 3.11 ruff format --check .
    uv run --python 3.11 pyright

`tests` 执行：

    uv run --python 3.11 python -m pytest tests -q

CI 不读取或恢复 `.testmondata`，不执行 `ruff --fix` 或 `ruff format` 写模式，也不需要模型供应商密钥。为 workflow 内 uv 缓存启用 setup-uv 的官方缓存，但不得缓存 `.venv` 或测试选择数据库作为正确性前提。

完成条件：workflow YAML 被 GitHub 接受，一次真实 push 和一次 pull request 更新都创建 `quality` 与 `tests` 检查；两者在当前基线通过，失败日志中的本地复现命令与上述命令一致。

### Batch 2: 验证失败路径并配置所需检查

在临时分支上依次构造一个 Ruff/Pyright 可检测问题和一个确定失败的单元测试，push 后确认 `quality` 与 `tests` 分别失败；恢复临时改动并再次 push，确认两者转绿。临时分支或提交不得合入主分支。

如果执行者拥有 GitHub 管理权限，在主分支保护规则中把 `quality` 和 `tests` 设置为 required status checks，并要求分支在合并前为最新状态。如果没有权限，记录仓库页面、所需 job 名称和负责人的待办；这属于唯一允许的外部配置阻塞，不影响 workflow 文件验收，但计划在保护规则实际启用前不能标记为完全完成。

完成条件：真实远端失败和恢复路径都有 URL 或截图证据记录在 `progress.md`；主分支保护阻止合并失败检查，或 `progress.md` 明确记录缺少的管理权限和待执行配置。

## Idempotence And Recovery

所有依赖和配置修改均由版本控制管理，可重复运行。`uv add` 已完成后不要反复改写依赖约束；使用 `uv sync --locked --dev` 验证锁文件。Ruff 自动修正只能在保存当前工作状态并确认改动范围后执行；若自动修改超出预期，停止并逐文件审查，不使用破坏性 Git 命令清理。

`.testmondata` 与 `.ruff_cache/` 是可删除重建的缓存。testmon 选择看起来不可信时，用 `--testmon-noselect` 重新建立数据库，并以 pre-push/CI 全量测试为最终事实来源。Git hook 安装可重复执行；需要移除时使用 pre-commit 自身的 uninstall 命令，而不是手工覆盖 `.git/hooks`。

GitHub workflow 失败时先用相同 Python 3.11 和 `--locked` 命令本地复现。若 action 下载或 GitHub 服务暂时失败，应重跑 job 并记录基础设施失败，不降低 Ruff/Pyright/pytest 门槛。只有计划中的所有验收证据齐全后，才把整个计划目录移动到 `docs/exec-plans/completed/ci-quality-gates/`。
