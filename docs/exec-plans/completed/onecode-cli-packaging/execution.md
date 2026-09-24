# Execution

从仓库根目录执行以下命令。开始前先读 [plan.md](plan.md) 与 [progress.md](progress.md)。

基线（2026-09-18，Windows 11 / CPython 3.14.5）：`uv run python -m pytest tests -q` 为 `3 failed, 794 passed in 32.84s`，3 个失败是活跃计划已记录的既有平台失败（`test_bash_tool::test_bash_descriptor_schema_and_prompt`、`test_openai_compatible_provider::test_catalog_contains_builtin_providers`、`test_search_tools::test_registry_generates_search_tool_schemas_and_prompts`），与本计划无关。`uv build` 当前失败，错误为扁平布局发现多个顶层包。

    uv sync --dev
    uv run python -m pytest tests -q

## M1：自包含发行包

### M1.1：构建元数据与显式包发现

编辑 `pyproject.toml`，在 `[project]` 之后加入：

    [build-system]
    requires = ["setuptools>=68"]
    build-backend = "setuptools.build_meta"

    [project.scripts]
    onecode = "ui.cli.app:main"

    [tool.setuptools.packages.find]
    include = [
        "application*",
        "core*",
        "infrastructure*",
        "prompts*",
        "services*",
        "tools*",
        "ui*",
        "utils*",
    ]
    exclude = ["reference*", "tests*", "docs*", "lessons*", "assets*"]

    [tool.setuptools.package-data]
    "ui.tui" = ["*.tcss"]

`main()` 已返回 `int`，setuptools 的 console_scripts 包装会执行 `sys.exit(main())`，无需修改 `ui/cli/app.py`。

同步 `.gitignore`：追加 `dist/` 与 `build/`（`*.egg-info/` 已存在）。更新 lock：

    uv lock
    uv sync --dev

完成条件：`uv lock` 与 `uv sync --dev` 成功，`uv.lock` 中 `onecode` 不再是 `source = { virtual = "." }`；`uv run python -m ui.cli.app` 仍能启动（源码内运行不回归）。

### M1.2：验证产物自包含并可在干净环境导入

    uv build

检查 wheel 清单（入口点与包数据）：

    python -c "import glob,zipfile; p=glob.glob('dist/onecode-*.whl')[0]; z=zipfile.ZipFile(p); names=z.namelist(); print('\n'.join(sorted(n for n in names if n.endswith('.py') or n.endswith('.tcss') or n.endswith('entry_points.txt'))))"

预期能看到 `ui/cli/app.py`、`ui/tui/app.py`、`ui/tui/onecode.tcss`，以及 `onecode-0.1.0.dist-info/entry_points.txt`；不得出现 `reference/`、`assets/`、`lessons/`。

在仓库外创建干净 venv 并安装 wheel：

    uv venv "C:/Users/rowla/AppData/Local/Temp/opencode/onecode-pkgtest" --python 3.11
    uv pip install --python "C:/Users/rowla/AppData/Local/Temp/opencode/onecode-pkgtest/Scripts/python.exe" dist/onecode-0.1.0-py3-none-any.whl

从仓库外目录导入并检查 CSS：

    "C:/Users/rowla/AppData/Local/Temp/opencode/onecode-pkgtest/Scripts/python.exe" -c "import ui.cli.app, ui.tui.app, importlib.resources as r; p=r.files('ui.tui').joinpath('onecode.tcss'); print(p.is_file())"

在 `C:/Users/rowla/AppData/Local/Temp/opencode` 或任意非仓库目录执行。预期输出 `True`；若报 `ModuleNotFoundError: No module named 'ui'` 则 M1 未完成。

完成条件：wheel 含全部运行时包与 `onecode.tcss`，且不含非运行时目录；干净 venv 从仓库外目录导入成功且 `onecode.tcss` 存在。

## M2：`onecode` 命令

### M2.1：隔离环境内的命令行为与 cwd 语义

使用 M1 的临时 venv 验证命令与入口元数据：

    ls "C:/Users/rowla/AppData/Local/Temp/opencode/onecode-pkgtest/Scripts"
    "C:/Users/rowla/AppData/Local/Temp/opencode/onecode-pkgtest/Scripts/python.exe" -c "from importlib.metadata import entry_points; eps=entry_points(group='console_scripts'); print([e.value for e in eps if e.name=='onecode'])"

预期输出 `['ui.cli.app:main']`。

在空的临时工作目录验证 batch 与 cwd（无需真实凭证）：

    mkdir -p "C:/Users/rowla/AppData/Local/Temp/opencode/onecode-empty"
    printf '只回复 OK\n' | "C:/Users/rowla/AppData/Local/Temp/opencode/onecode-pkgtest/Scripts/onecode.exe"

在非 TTY stdin 下预期进入 batch，因该目录没有 `.env` 而打印 provider 配置错误（例如 `Provider .env file is missing or empty`）并以非零退出。关键是错误来自配置读取而非 `ModuleNotFoundError`，证明可执行文件运行且按当前工作目录查找 `.env`。若在临时目录放入可用 `.env` 并配置 provider，可额外人工验证一次真实 batch 回复。

在交互终端（人工）执行 `onecode`：预期进入全屏 TUI，无 `StylesheetError` 或找不到 `onecode.tcss` 的报错；`/exit` 退出后终端恢复。此步无法在无 TTY 的自动化中完成，记为人工证据。

完成条件：入口元数据为 `ui.cli.app:main`；仓库外空目录 batch 得到配置错误而非导入错误；TTY 人工烟测进入 TUI 且样式表加载成功。

### M2.2：全局工具安装与卸载

    uv tool install --force .
    uv tool list

预期 `onecode` 出现在用户工具目录（Windows 默认 `%USERPROFILE%\.local\bin`，POSIX `~/.local/bin`）；必要时按 `uv` 提示执行 `uv tool update-shell` 使 PATH 生效，然后重开终端。在任意目录运行 `onecode` 验证已安装版本，随后：

    uv tool install --force .
    uv tool uninstall onecode

完成条件：`onecode` 可从 PATH 解析并启动；重复安装无副作用；卸载后命令消失且不影响项目 `.venv`。

## M3：测试与文档固定契约

### M3.1：打包契约测试

新增 `tests/test_packaging.py`，用 `tomllib` 读取 `pyproject.toml` 并断言：

- `[project.scripts]` 的 `onecode == "ui.cli.app:main"`；
- `[build-system].build-backend == "setuptools.build_meta"`；
- `[tool.setuptools.packages.find]` 的 include 覆盖全部 8 个运行时包；
- `[tool.setuptools.package-data]` 中 `ui.tui` 含 `*.tcss`；
- `import ui.cli.app` 成功且 `callable(ui.cli.app.main)`；
- `importlib.resources.files("ui.tui").joinpath("onecode.tcss").is_file()` 为真；
- `from ui.tui.app import OneCodeTuiApp` 后 `OneCodeTuiApp.CSS_PATH == "onecode.tcss"`。

测试只读配置与已安装模块，不执行构建、不联网、不写用户目录，保持快速。

    uv run python -m pytest tests/test_packaging.py -q

完成条件：测试通过；临时移除 `[project.scripts]` 或 `"ui.tui"` 包数据后测试失败（可用 git stash 验证一次再恢复）。

### M3.2：文档、忽略规则与边界回归

更新 `README.md`、`README_en.md` 的“启动终端 / Start the terminal”小节：主推 `uv tool install .` 后直接 `onecode`，保留源码内运行 `uv run python -m ui.cli.app` 作为开发方式；batch 示例改为 `echo ... | onecode`。更新 `docs/design-docs/cli-architecture.md:7` 的启动说明，区分“安装后命令”与“源码内运行”。确认 `.gitignore` 含 `dist/`、`build/`。

    uv run python -m pytest tests/test_packaging.py tests/test_import_boundaries.py -q
    uv run python -m pytest tests -q
    uv run python -m compileall core services infrastructure application ui

预期：打包测试与边界测试通过；全量测试相对基线无新增失败（基线 794 passed, 3 failed）；compileall 退出码 0。

完成条件：文档同时给出 `onecode` 与源码内启动；测试结果与基线一致；`git status` 不含 `dist/`、`build/`、`*.egg-info/` 等构建产物。

## Idempotence And Recovery

`uv build`、`uv lock`、`uv pip install`、`uv tool install --force` 均可重复执行。失败时先清理 `dist/`、`build/`、`*.egg-info/` 再重来。包发现漏包时按 `uv build` 报错列出的顶层目录补 include，不放宽为自动发现。全局安装污染用户环境时用 `uv tool uninstall onecode` 回退。临时 venv 位于 `C:/Users/rowla/AppData/Local/Temp/opencode/onecode-pkgtest`，可整套删除重建，不影响仓库与用户工具。每次停止时更新 [progress.md](progress.md) 的当前批次、命令与结果。
