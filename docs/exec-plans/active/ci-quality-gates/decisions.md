# Decisions

这里记录本次 CI 引入的执行级决策。长期开发规范若在实施后成为稳定项目约定，应同步到 `README.md` 或适当的设计文档；不要让本文件成为唯一入口。

## 2026-09-24: 使用三层质量门禁

Decision: commit 前执行 Ruff 自动修正、格式化和受影响测试；push 前执行 Ruff、Pyright 和全量测试；GitHub 在 push 与 pull request 后再次执行只读全量检查。

Context: 当前测试套件有 820 个测试，全量运行约 156 秒。每次 commit 都跑全量会显著破坏反馈速度，但 Git hooks 可以用 `--no-verify` 绕过，不能单独保证主分支质量。

Rationale: 分层让内环快速，同时在本地 push 边界和可信远端环境保留完整正确性检查。

Consequences: 本地需要维护 testmon 缓存；pre-push 较慢；GitHub CI 不信任本地增量结果。

## 2026-09-24: Ruff 使用锁定版本的默认规则

Decision: 不配置 `select`、`extend-select`、preview 或全局 ignore，使用 uv.lock 锁定的 Ruff 默认规则；自动修复只使用安全 fixes，并在其后运行 formatter。

Context: Ruff 0.16.8 的默认规则已比早期版本宽，当前基线有 528 条诊断，其中 348 条可安全修复。Ruff 官方允许在小版本中调整默认规则，因此工具升级本身可能带来新诊断。

Rationale: 这符合“先使用默认规则，之后再适当添加”的要求。uv.lock 让日常开发稳定，显式工具升级则成为重新评估默认规则的自然边界。

Consequences: 启用前必须完成较大的基线迁移；升级 Ruff 时要单独审查新默认诊断。禁止为快速转绿整类关闭默认规则。

## 2026-09-24: Ruff 只覆盖项目 Python 代码

Decision: Ruff 覆盖生产包、tests 和根目录 Python 维护脚本，排除 `docs/`、`reference/` 和 `lessons/` 中的历史或参考代码，并把 include 限制为 Python/pyi 文件。

Context: 仓库包含大量参考实现和 Markdown 资料，它们不是 OneCode 可发布代码，也不应在首次接入时产生无关格式 churn。

Rationale: 检查边界与 setuptools 的项目包边界以及 pytest 测试边界一致，同时保留对根目录维护脚本的检查。

Consequences: 参考材料中的 Python 不受 CI 约束；若未来某个脚本成为生产或维护入口，应移入受检查范围或调整配置。

## 2026-09-24: Pyright 使用 uv 锁定的 Python 包装器与 basic 模式

Decision: 将 PyPI `pyright` 包加入开发依赖，通过 `uv run pyright` 执行，目标 Python 3.11，第一阶段 `basic` 只检查八个生产目录。

Context: 官方 Pyright CLI 原生通过 npm 分发，但仓库目前只有 uv/Python 工具链。Pyright 官方文档承认社区维护的 Python 包装器；使用它可把版本统一锁进 uv.lock。

Rationale: 避免新增 package.json、npm lock 和全局 Node 安装，使本地、hook 与 GitHub 使用同一命令和版本。

Consequences: 包装器本身不是 Microsoft 发布物；若以后要求只用官方 npm 包，需要独立迁移依赖和缓存策略。tests、`standard` 与 `strict` 延后到基线稳定后的独立决策。

## 2026-09-24: pytest-testmon 只服务本地增量反馈

Decision: commit 前用 pytest-testmon 选择受影响测试；pre-push 用 `--testmon-noselect` 全量执行并刷新数据库；GitHub CI 使用普通全量 pytest，不缓存 `.testmondata`。

Context: 按文件名映射无法可靠覆盖 OneCode 的跨模块调用，而 testmon 可以通过实际执行覆盖关系选择测试。但缓存可能缺失、陈旧或受环境差异影响。

Rationale: testmon 适合优化开发反馈，不适合成为唯一合并门禁。两次全量边界吸收选择算法的漏测风险。

Consequences: 新环境第一次需要全量初始化；`.testmondata*` 必须被忽略；testmon 不可靠时可以重建缓存而不影响远端正确性。

## 2026-09-24: 使用 pre-commit 框架和 uv 中的本地 hooks

Decision: `.pre-commit-config.yaml` 使用 `repo: local`、`language: system` 和 `uv run --frozen`，同时安装 pre-commit 与 pre-push stages。

Context: Ruff 官方提供独立的 ruff-pre-commit 仓库，但那会在 uv.lock 外再维护一个 Ruff revision。原生 `.git/hooks` 脚本又会带来安装、跨平台和可发现性问题。

Rationale: pre-commit 提供一致的文件暂存处理和 hook 生命周期，uv 保持工具版本的单一事实来源。

Consequences: 开发者必须先执行 `uv sync --locked --dev` 再安装 hooks；`--no-verify` 仍可绕过本地门禁，因此 GitHub CI 必须保留。

## 2026-09-24: GitHub 对所有 push 和 pull request 执行全量 CI

Decision: 初始 workflow 同时监听全部 `push` 和 `pull_request`，在 Python 3.11 上并行运行稳定命名的 `quality` 与 `tests` jobs。

Context: 用户要求 push 前全量测试，push 后也自动测试。只监听 main push 与 pull request 会让尚未打开 PR 的功能分支缺少远端验证。

Rationale: 首先满足每次 push 都有可信远端结果。Python 3.11 是 `requires-python` 的最低版本，因此最能保护兼容承诺。

Consequences: 同仓库已打开 PR 的分支可能同时产生 push 与 pull_request 运行，带来重复成本。待积累运行耗时和 GitHub 用量后，可以另行优化触发条件，但不能牺牲 fork PR 或分支 push 覆盖。

## 2026-09-24: CI 只读且固定第三方 action

Decision: CI 不运行 Ruff 写模式、不提交修复，权限只给 `contents: read`，第三方 actions 使用完整 commit SHA 固定并带版本注释。

Context: 自动修正适合本地开发，但远端静默改写或自动 push 会改变贡献者提交并扩大令牌权限。浮动 action tag 也会使历史构建不可复现。

Rationale: CI 应只判断提交是否满足仓库契约；修复由开发者在本地查看并提交。

Consequences: 格式错误会让 CI 红灯而不是自动修好；action 升级需要显式更新 SHA。

## 2026-09-24: 先清理基线再启用门禁

Decision: 把 Ruff 格式化、lint/type 修复和现有测试失败处理放在 hooks/workflow 启用之前，并尽量将机械格式变化与语义修复分开审查。

Context: 当前工作区有大量用户改动，Ruff 报告 528 条诊断、209 个文件待格式化，全量 pytest 有 5 个失败。立即启用门禁会使所有提交和 CI 必然失败，并让批量格式化混入功能开发。

Rationale: 先建立绿色基线才能让后续红灯表示新回归，而不是历史噪声。

Consequences: Milestone 1 是实施前置条件；若当前用户工作尚未稳定，应暂停自动格式化而不是覆盖或回滚其改动。

## 2026-09-24: Ruff BLE001/S110 采用行级 noqa 例外

Decision: 对运行时的故障隔离边界保留 `except Exception` 宽泛捕获，并对这些既有位置添加行级 `# noqa: BLE001`（其中 14 处同时加 `# noqa: S110`），不配置 `[tool.ruff.lint].ignore`，也不使用 per-file-ignores。

Context: Ruff 0.16.8 默认启用 BLE001（blind-except）与 S110（try-except-pass），共命中 97 处，分布在运行时边界：工具执行/预检（`services/tools/executor.py`、`tools/{bash,glob,grep}/tool.py`）、MCP 生命周期（`services/mcp/manager.py`）、后台任务（`services/background_tasks/manager.py`）、上下文/记忆/压缩（`services/{context,memory,compaction}/…`）、可观测性写入（`services/observability/{error_log,sinks,trace}.py`）、应用会话（`application/session.py`、`application/commands.py`）与 UI 事件循环（`ui/cli/…`、`ui/tui/…`）。这些位置刻意吞掉任意异常，以保证单个失败不中断 agent 或界面。

Rationale: 计划禁止“为快速转绿整类关闭默认规则”，也禁止全局 ignore。行级 noqa 是最窄的例外，只覆盖既有位置；新代码仍完整受 BLE001/S110 约束。

Paths（按目录分组；S110 同时出现在 `application/session.py`、`services/background_tasks/manager.py`、`ui/cli/terminal/stream_session.py`、`ui/cli/tool_renderers.py`、`ui/tui/app.py`、`ui/tui/renderers/tool.py`）：

- `application/`：`commands.py`、`session.py`
- `infrastructure/`：`providers/http.py`
- `services/`：`background_tasks/manager.py`、`compaction/{service,session_memory}.py`、`hooks/registry.py`、`mcp/manager.py`、`memory/{extraction,selector}.py`、`observability/{error_log,sinks,trace}.py`、`permissions/policy.py`、`subagents/runner.py`、`tools/executor.py`
- `tools/`：`bash/tool.py`、`glob/tool.py`、`grep/tool.py`
- `ui/cli/`：`app.py`、`batch.py`、`commands.py`、`terminal/{connect_flow,interaction_host,page,repl,stream_session}.py`、`tool_renderers.py`、`views/permissions.py`
- `ui/tui/`：`app.py`、`composer.py`、`renderers/tool.py`、`theme.py`

Consequences: 这些行在未来出现宽泛捕获时仍会通过检查（捕获本身是设计意图）；若某处应改为窄捕获，应删除对应 noqa 并说明原因。

## 2026-09-24: Pyright 以根因修复为主，仅对动态对象使用定向 type: ignore

Decision: Pyright `basic` 的 140 个错误主要通过根因修复解决：为 `ToolHandler` 增加异步返回类型支持、使 `ToolExecutor` Protocol 的 `state`/`file_state_cache` 与 `RegistryToolExecutor` 一致、用局部变量与 `assert` 收窄 `CliRuntime | None`、一次性分配并校验 JSON/元数据类型。仅对无法静态描述的第三方鸭子类型对象使用定向 `# type: ignore[code]`（prompt_toolkit `Completion` 动态属性、tree-sitter `Parser.set_language`、`AsyncExitStack` 上的 stderr 日志持有属性）。

Context: `basic` 模式在干净环境即报告 140 个错误，其中大多数是由共享类型定义不精确引起的重复报告（如 `CliRuntime | None` 一个根因产生 31 条）。

Rationale: 修正共享类型可一次性消除成片错误，并让运行时契约更准确；定向 ignore 只用于确实动态的边界，避免把类型弱化为 `Any`。

Consequences: 升级 `textual`/`tree-sitter`/`prompt_toolkit` 时需复核这些定向 ignore 是否仍必要。

## 2026-09-24: 修复 TTY 信任提示返回值

Decision: 将 `ui/cli/terminal/trust_prompt.py` 的 `default_trust_prompt` 返回值从 `"t"`/`"s"` 改为 `TrustChoice`（`"trust"`/`"skip"`）。

Context: `application/runtime.py` 以 `response == "trust"` 判定是否信任；旧返回值使 TTY `/connect` 首配路径始终把项目 MCP stdio 服务器按“跳过”处理。修复同时消除 Pyright 对 `build_runtime(trust_prompt=...)` 的签名不匹配。

Rationale: 这是类型检查暴露出的真实缺陷，修复实现比放宽类型更正确。

Consequences: TTY 首配现在能正确记录信任；批处理路径的 `_terminal_trust_prompt` 行为不变。

## 2026-09-24: 更新过时与不稳定测试而非删除

Decision: 更新 `tests/test_search_tools.py`（工具 prompt 已改为 `Purpose:` 结构）、修正 `tests/test_conversation_view.py` 的挂载等待为“显式回顶 + 轮询”，并让 `tests/test_cli_terminal.py::test_repl_drain_stops_on_runtime_exit` 使用新的 `_exiting` 退出信号。

Context: 搜索工具 prompt 前缀断言早于 `b9b459e` 的提示词结构重构；conversation-view 测试依赖初始滚动位置与固定 `pilot.pause(0.2)`，在顺序/负载变化时不稳定（诊断确认 `scroll_to(y=0)` 异步且初始可能在底部）；REPL 内部退出信号在 M1 中从 `_runtime = None` 改为 `_exiting`。

Rationale: 计划要求判断失败来自实现还是过时测试，并在所有者模块修复或更新，禁止仅为 CI 删除测试。

Consequences: 未删除任何测试；断言与当前实现一致，并覆盖同样的行为意图。

## 2026-09-24: 按 TRY004 将类型校验的 ValueError 规范为 TypeError

Decision: 将 `services/mcp/trust.py`（`:129,154,162`）、`services/permissions/project_settings.py`（`:88,113`）、`services/tasks/types.py`（`:35,81,87`）和 `services/tasks/store.py`（`:232`）中对 `isinstance` 类型校验失败抛出的 `ValueError` 改为 `TypeError`。

Context: Ruff 默认规则 TRY004 要求“无效类型”使用 `TypeError`。这些分支都在解析项目设置/任务 JSON 时校验“必须是对象/字符串/列表”。

Rationale: 这是 TRY004 明确的语义修复方向；已确认没有测试或调用方按 `ValueError` 捕获这些类型分支（JSON 解析失败的 `JSONDecodeError`→`ValueError` 分支保持不变）。

Consequences: 这些内部校验现在抛 `TypeError`；若未来有调用方依赖旧的 `ValueError`，需要显式适配。

## 2026-09-24: 显式化可选运行时组件的前置条件

Decision: 用显式前置条件替代隐式 `None` 传播：`application/runtime.py` 新增 `_require_tool_executor` 并在 `with_session`/`with_model_config` 中对缺失的 executor/registry 抛 `RuntimeError`；`application/session.py` 与 `ui/cli/terminal/repl.py` 在取 `runtime.loop` 后显式判空并抛 `RuntimeError`；`core/loop.py` 把循环内的 `on_retry` 闭包改为 `_make_on_retry` 工厂方法。

Context: Pyright 无法从 `ToolExecutor | None`、`AgentLoop | None` 和循环变量闭包推断出真实契约，原先的 `_runtime = None`、隐式 `None` 传递还会产生 31+ 条重复错误。闭包工厂同时消除了 Ruff B023（循环变量绑定）而无需行级 noqa。

Rationale: 让生命周期/装配前置条件显式化符合 AGENTS.md 对并发与终态明确性的要求，也避免依赖会被 `-O` 剥离的 `assert`。

Consequences: 未完全装配的 runtime 现在会以 `RuntimeError` 快速失败，而不是在更深处触发 `AttributeError`；`with_model_config` 仍通过 `getattr` 容忍缺少 `file_state_cache` 的鸭子类型 executor（测试替身）。

## 2026-09-24: pre-commit 的 Ruff hooks 显式排除参考目录

Decision: 在 `.pre-commit-config.yaml` 的 `ruff-check-fix` 与 `ruff-format` hooks 上增加 `exclude: ^(docs|reference|lessons)/`，而不是只依赖 `pyproject.toml` 的 `[tool.ruff].exclude`。

Context: pre-commit 会把匹配到的文件作为显式参数传给 `ruff check`/`ruff format`；Ruff 官方行为是**显式传入的文件不受 `exclude` 约束**。首次 `pre-commit run --all-files` 因此改写了 24 个 `reference/ui/*.py` 文件，违背“Ruff 只覆盖项目 Python 代码”的既定范围。

Rationale: 检查边界必须由 hook 自身保证，才能在 pre-commit 的显式文件模式下与 Ruff 配置保持一致；这也避免误改 `docs/`、`lessons/` 中的参考代码。

Consequences: 若未来新增需要排除的目录，必须同时更新 `[tool.ruff].exclude` 与两个 hook 的 `exclude`；`pytest-testmon` hook 已用 `files` 白名单限定在生产目录、`tests/`、`pyproject.toml` 和 `uv.lock`，无需额外排除。

## 2026-09-24: testmon 以 AST 指纹选择测试

Decision: 接受 pytest-testmon 2.2.0 基于 AST/方法指纹（而非原始文本）判断改动，纯注释、docstring 与空白改动不触发测试选择；本地 commit 阶段使用 `--testmon`，pre-push 与远端仍为全量。

Context: 验证中发现追加注释后 `--testmon` 报告 `changed files: 0`，而修改常量后才选择测试。Python 3.14 下 coverage 会以 sysmon core 运行并输出 “dynamic contexts … incomplete” 警告，但实测依赖记录（820 test_execution、2083 依赖边）与选择均正常。

Rationale: 指纹粒度只影响“是否重跑”，不会漏掉真实语义改动；全量边界（pre-push/CI）已吸收选择算法风险。

Consequences: 纯文档字符串改写不会触发本地增量测试，需依赖 pre-push/CI 覆盖；若升级 testmon 或 coverage 后选择明显异常，按计划重建 `.testmondata` 并以全量为准。

## 2026-09-24: CI 并发取消与 uv 缓存实现

Decision: `.github/workflows/ci.yml` 的 `concurrency.group` 使用 `${{ github.workflow }}-${{ github.ref }}`，`cancel-in-progress: true`；setup-uv 使用 `enable-cache: true`，只缓存 uv 缓存，不缓存 `.venv` 或 `.testmondata`。

Context: 计划要求同一事件和分支上的旧运行被新 commit 取消，并允许为 uv 缓存启用官方缓存。`github.ref` 对 push 是 `refs/heads/<branch>`、对 pull request 是 `refs/pull/<n>/merge`，因此按 ref 分组既能在各自事件内取消旧运行，又不会让 push 与 PR 运行互相取消。

Rationale: 用 ref 作为分组键精确匹配“同一事件和分支”，同时保留 push 与 pull_request 双触发。

Consequences: 同仓库已打开 PR 的分支仍可能同时产生 push 与 pull_request 运行（预期内的重复成本）；`.venv` 始终由 `uv sync --locked --dev` 重建，不作为正确性来源。

## Unresolved Operational Item

主分支 required status checks 需要 GitHub 仓库管理权限。计划要求把 `quality` 和 `tests` 设为 required；实施者若没有权限，必须在 `progress.md` 记录阻塞、准确 job 名称和需要仓库管理员执行的设置，不能默认为 workflow 文件存在就已经阻止不合格合并。
