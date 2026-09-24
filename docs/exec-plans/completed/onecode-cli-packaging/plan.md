# 为 OneCode 增加 `onecode` 终端命令（打包与安装）

## Purpose

安装后，用户在任意工作目录的终端输入 `onecode` 即可启动 OneCode：交互终端进入现有全屏 TUI，管道输入走现有 batch 路径。用户不再需要 `cd` 到仓库根、激活 `.venv` 并执行 `uv run python -m ui.cli.app`。可观察结果是：同一条 `onecode` 命令从任意目录启动同一运行时，当前工作目录决定 workspace、`.env` 与 `.onecode/`，退出码与流式输出与旧入口一致。

本文件是执行计划包入口，依照 [PLANS.md](../../../../PLANS.md) 维护。宏观范围与验收在本文件；实施批次与命令见 [execution.md](execution.md)，执行决策见 [decisions.md](decisions.md)，实际状态与证据见 [progress.md](progress.md)。写完计划不代表已经打包。

## Scope

覆盖 `pyproject.toml` 的构建元数据（`[build-system]`、`[project.scripts]`、显式包发现、包数据）、`.gitignore`、中英文 README 与 `docs/design-docs/cli-architecture.md` 的启动说明，以及新增打包契约测试。用户可见行为只增加“可以用 `onecode` 启动”，不改变 TUI 与 batch 的内部行为。

不覆盖：发布到 PyPI 或搭建 CI 发布流水线；PyInstaller / 单文件原生二进制；把仓库重构成 `src/` 布局或重命名 `core`、`services`、`ui` 等顶层包；为 `onecode` 增加子命令或 `--help`/`--version` 参数解析（当前 `main()` 忽略 argv，本次保持）；修改 provider 配置、guard、权限或工具语义。

## Design References

- [core-beliefs](../../../design-docs/core-beliefs.md)：依赖方向与“薄 loop”约定；打包不得引入新的运行时分支。
- [cli-architecture](../../../design-docs/cli-architecture.md)：`ui/cli/app.py::main` 是唯一终端入口，按 stdin/stdout 是否为 TTY 分流 TUI 与 batch；打包只改变“如何被调用”。
- [architecture.md](../../../../architecture.md)：分层与 `ui -> application -> core / services / infrastructure` 的依赖方向；打包配置不得改变导入方向。

## Context And Orientation

`pyproject.toml`（仓库根）目前只有项目元数据与依赖：没有 `[build-system]`、没有 `[project.scripts]`、没有包发现配置。`uv.lock` 中 `onecode` 记录为 `source = { virtual = "." }`，即项目本身没有被当作可安装包构建。

当前唯一入口是 `ui/cli/app.py::main(argv)`（返回 `int`，忽略 argv）：`sys.stdin.isatty()` 为真时启动 `ui.tui.app::run_tui`，否则调用 `ui/cli/batch.py::run_batch`。启动命令写在 `README.md:72`、`README_en.md:72` 和 `docs/design-docs/cli-architecture.md:7`。

关键事实：`uv run python -m ui.cli.app` 之所以能 import `ui` 与 `core`，是因为 `uv run` 在仓库根执行、`python -m` 把当前目录加入了 `sys.path`；`.venv` 里既没有指向仓库根的 `.pth`，也没有安装 `onecode`。因此从其他目录运行 `import ui` 会失败——这正是需要打包的原因。

运行时资源基本都在工作目录或用户目录（`.onecode/`、`.env`、`~/.onecode/skills`），不需要打包。唯一的包内资源是 `ui/tui/app.py:73` 的 `CSS_PATH = "onecode.tcss"`，对应 `ui/tui/onecode.tcss`；Textual 相对模块文件解析它，所以发行产物必须包含该文件。

本计划改变一条历史决策：`docs/exec-plans/completed/cli-main-ui-plan.md:41` 曾记录“启动入口使用 `uv run python -m ui.cli.app`，不先添加 console script，避免第一版修改打包配置”。现在 TUI 已是默认交互入口且需求明确要求终端命令，本计划逆转该决定，理由见 decisions。

## Interfaces And Dependencies

最终产物与接口：

- `pyproject.toml` 新增 `[project.scripts] onecode = "ui.cli.app:main"`。
- 构建后端为 setuptools（`setuptools.build_meta`）。顶层包通过 `[tool.setuptools.packages.find]` 显式 include：`application*`、`core*`、`infrastructure*`、`prompts*`、`services*`、`tools*`、`ui*`、`utils*`；并 exclude `reference*`、`tests*`、`docs*`、`lessons*`、`assets*`。
- `[tool.setuptools.package-data]` 必须让 `ui.tui` 包含 `*.tcss`。
- 安装方式：`uv tool install .`（隔离的全局命令，推荐）或项目/临时 venv 内 `uv pip install .`；两种方式都必须产生可执行的 `onecode`。
- 保持不变：`ui.cli.app.main` 的签名与返回类型、TUI / batch 分流、`Path.cwd()` 作为 workspace、`.env` 从当前工作目录读取。

## Plan Of Work

依赖顺序为 M1 → M2 → M3。先得到自包含、可构建的发行产物，再证明安装后在任意目录运行等价，最后用测试与文档把契约固定下来。过程中不切换任何运行时行为。

### M1：OneCode 可构建为自包含发行包

加入构建后端、console script、显式包发现与包数据后，`uv build` 能产出 wheel 和 sdist，且 wheel 含全部运行时包与 `ui/tui/onecode.tcss`；把 wheel 安装进干净 venv 后，从任意目录都能 `import ui.cli.app` 与 `import ui.tui.app`。这是后续一切的基础，独立于全局安装。

独立验收：`uv build` 成功且不再出现 “Multiple top-level packages discovered in a flat-layout”；wheel 清单含 8 个运行时顶层包与 `onecode.tcss`，且不含 `reference/`、`assets/`、`lessons/`；干净 venv 安装 wheel 后，在仓库外目录执行 import 检查与 `importlib.resources` 的 CSS 存在性检查通过。

### M2：`onecode` 命令在任意工作目录等价于现有入口

安装后 `onecode` 出现在 PATH。在临时工作目录运行 `onecode`：stdin 为 TTY 时启动现有 TUI 且无样式表缺失错误；stdin 非 TTY 时走 batch；workspace、`.env`、`.onecode/` 都指向该工作目录；从仓库外目录运行不依赖仓库根出现在 `sys.path`。

独立验收：临时 venv 中存在 `onecode` 可执行文件，且 `console_scripts` 入口元数据为 `ui.cli.app:main`；空工作目录里 `printf ... | onecode` 因缺少 `.env` 打印 provider 配置错误并非零退出（证明命令已运行、import 成功、按 cwd 找 `.env`），而不是 `ModuleNotFoundError`；`uv tool install --force .` 重复执行幂等，`uv tool uninstall onecode` 可干净移除。

### M3：打包契约被测试与文档固定

新增测试锁定入口点、构建配置、包发现与包数据，并在配置缺失时失败；同步更新中英文 README、CLI 设计文档的启动说明与 `.gitignore`。完成后，后续改动若误删入口或样式表会被测试立刻发现，且用户文档不再只写 `uv run`。

独立验收：`tests/test_packaging.py` 通过，且在移除 `[project.scripts]` 或包数据配置时会失败；全量测试与 `tests/test_import_boundaries.py` 不退化；README 与 CLI 文档同时给出 `onecode` 与源码内 `uv run` 两种启动方式。

## Validation And Acceptance

整体接受条件：`uv build` 产出完整 wheel；把 wheel 装入干净 venv 后 `onecode` 可从任意目录启动 TUI（人工）并支持 batch（确定性命令）；`.env` 与 `.onecode/` 落在当前工作目录；`tests/test_packaging.py` 与 `tests/test_import_boundaries.py` 通过；全量 `uv run python -m pytest tests -q` 相对基线无新增失败。精确命令与预期输出见 [execution.md](execution.md)，实际结果记录在 [progress.md](progress.md)。

不把“命令存在”当作唯一验收：必须在仓库外工作目录证明 import 与运行时资源解析不依赖仓库根。TUI 无法在无终端环境下稳定自动断言，因此 TUI 只做人工烟测；自动化验收以 batch 路径、入口元数据、wheel 清单与 CSS 资源存在性为准。

## Recovery

若 `[tool.setuptools.packages.find]` 仍报多顶层包错误，逐项抄写错误列出的包名并补进 include/exclude，直到 `uv build` 成功；不要改用 `src/` 布局或重命名包。若安装后 `import ui` 失败，先在干净 venv 中执行 `python -c "import ui, core, services; print(ui.__file__)"` 判断是构建遗漏还是 `sys.path` 问题。若 Textual 报找不到 `onecode.tcss`，检查 `[tool.setuptools.package-data]` 与 wheel 清单。全局安装出问题时用 `uv tool uninstall onecode` 清理后重装；自动化验证优先使用临时 venv，避免污染用户环境。所有步骤可重复执行；`dist/`、`build/`、`*.egg-info/` 是可丢弃产物并加入 `.gitignore`。

## Related Documents

- [Decisions](./decisions.md)
- [Execution](./execution.md)
- [Progress](./progress.md)

2026-09-18：初次编写；确认 `uv build` 当前因扁平布局多顶层包失败，并据此把 M1 定为显式包发现、入口与包数据。
