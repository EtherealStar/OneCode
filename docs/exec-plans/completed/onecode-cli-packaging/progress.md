# Progress

在每个停止点更新本文件，包括部分完成的工作。已完成与剩余工作必须显式可读。

## Current State

- Status: in progress（M1–M3 已完成，待最终归档与人工 TUI 烟测）
- Current milestone: M3 完成（契约测试与文档）；下一步整体归档到 completed
- Last updated: 2026-09-19, Asia/Shanghai

M1 的构建元数据、显式包发现、包数据与自包含产物验证均已完成；M2 已证明隔离环境与全局工具安装下 `onecode` 在任意工作目录等价于现有入口，并按 cwd 解析 `.env`；M3 已新增打包契约测试并更新中英文 README 与 CLI 设计文档的启动说明。工作区其他既有修改与未跟踪目录（`assets/`、`lessons/`、`reference/`、若干设计文档等）本计划未触碰。全局工具目录在本机由 `uv` 配置为 `D:\uv\tools`，可执行文件在 `D:\uv\bin\onecode.exe`（非默认 `%USERPROFILE%\.local\bin`）。唯一未闭环的是需真实 TTY 的 TUI 人工烟测。

## Milestones And Batches

- [x] M1：OneCode 可构建为自包含发行包
  - [x] M1.1：构建元数据与显式包发现
  - [x] M1.2：验证产物自包含并可在干净环境导入
- [ ] M2：`onecode` 命令在任意工作目录等价于现有入口
  - [x] M2.1：隔离环境内的命令行为与 cwd 语义
  - [x] M2.2：全局工具安装与卸载
- [x] M3：打包契约被测试与文档固定
  - [x] M3.1：打包契约测试
  - [x] M3.2：文档、忽略规则与边界回归

## Surprises And Discoveries

- Observation：`uv build` 在没有任何 `[build-system]` 时仍默认走 setuptools legacy 后端，并因扁平布局多顶层包报错。
  Evidence：`error: Multiple top-level packages discovered in a flat-layout: ['ui', 'core', 'assets', 'lessons', 'prompts', 'services', 'reference', 'application', 'infrastructure'].` 因此必须显式配置 `packages.find`，且发现候选里包含 `assets`、`lessons`、`reference` 等非运行时目录。
- Observation：`uv run python -m ui.cli.app` 能工作的原因是当前目录出现在 `sys.path`。
  Evidence：`.venv/Lib/site-packages` 下没有指向仓库根的 `.pth`，也没有已安装的 `onecode`；`uv.lock` 中 `onecode` 为 `source = { virtual = "." }`。
- Observation：唯一的包内非 Python 资源是 TUI 样式表。
  Evidence：`ui/tui/app.py:73` 为 `CSS_PATH = "onecode.tcss"`；全仓库检索到的运行时可分发资源只有 `ui/tui/onecode.tcss`，其余 `.md`/`.json` 都在工作目录或用户目录。
- Observation：历史上曾明确决定不添加 console script。
  Evidence：`docs/exec-plans/completed/cli-main-ui-plan.md:41-42`。
- Observation：M1 实现后 `uv.lock` 中 `onecode` 从 `virtual` 变为 `editable`，`uv sync --dev` 会构建并安装本地 `onecode`。
  Evidence：`uv.lock:359-361` 为 `version = "0.1.0"` + `source = { editable = "." }`；`uv sync --dev` 输出 `Built / Installed onecode==0.1.0`。
- Observation：全量测试收集数为 771，低于 execution 记录的基线 797；差异来自本计划之后落地的提交 `0eef316 test(tests): 按 TDD 反模式精简测试并改写耦合用例` 精简了测试，而非本计划。
  Evidence：`git log --oneline` 显示 `0eef316 2026-09-18`；`git status` 相对本计划未删除任何测试文件；失败集合是基线 3 个既有失败的子集。

## Validation Evidence

Command：`uv build`（仓库根，2026-09-18）。
Result：失败。`Multiple top-level packages discovered in a flat-layout: ['ui', 'core', 'assets', 'lessons', 'prompts', 'services', 'reference', 'application', 'infrastructure']`；未产出 wheel。失败时生成的空 `dist/` 已删除。

Command：`uv run python -m pytest tests -q`（仓库根，2026-09-18）。
Result：`3 failed, 794 passed in 32.84s`。3 个失败与活跃计划 `cli-to-reference-tui` 记录的 Windows 既有失败一致：`test_bash_tool::test_bash_descriptor_schema_and_prompt`、`test_openai_compatible_provider::test_catalog_contains_builtin_providers`、`test_search_tools::test_registry_generates_search_tool_schemas_and_prompts`。作为本计划基线，本次不修复。

### M1.1（2026-09-19）

Command：`uv lock && uv sync --dev`（仓库根）。
Result：成功。`Resolved 48 packages`；`Building/Built onecode @ file:///D:/study/OneCode`；`Installed onecode==0.1.0`。`uv.lock` 中 `onecode` 变为 `source = { editable = "." }`。

Command：`uv run python -m ui.cli.app`（非 TTY）。
Result：输出 “Error: OneCode CLI requires an interactive terminal: stdout is not a TTY. …”，即源码内入口仍按原逻辑运行，无导入回归。

Command：`uv run python -c "import importlib.resources as r; print(r.files('ui.tui').joinpath('onecode.tcss').is_file())"`。
Result：`True`，editable 安装下包数据可解析。

### M1.2（2026-09-19）

Command：`uv build`（仓库根）。
Result：成功，产出 `dist/onecode-0.1.0-py3-none-any.whl` 与 `dist/onecode-0.1.0.tar.gz`。

Command：检查 wheel 清单（`zipfile` 读取 `dist/onecode-0.1.0-py3-none-any.whl`）。
Result：8 个运行时顶层包全部存在 `['application', 'core', 'infrastructure', 'prompts', 'services', 'tools', 'ui', 'utils']`；含 `ui/tui/onecode.tcss`；含 `onecode-0.1.0.dist-info/entry_points.txt`，内容为 `[console_scripts] onecode = ui.cli.app:main`；不含 `reference/`、`assets/`、`lessons/`。

Command：`uv venv "C:/Users/rowla/AppData/Local/Temp/opencode/onecode-pkgtest" --python 3.11 && uv pip install --python .../python.exe dist/onecode-0.1.0-py3-none-any.whl`。
Result：成功。使用 CPython 3.11.16 创建干净 venv，安装 `onecode==0.1.0 (from .../dist/onecode-0.1.0-py3-none-any.whl)` 及 45 个依赖。

Command：从仓库外目录（`C:/Users/rowla/AppData/Local/Temp/opencode`）运行干净 venv 的 `python -c "import ui.cli.app, ui.tui.app, importlib.resources as r; p=r.files('ui.tui').joinpath('onecode.tcss'); print(p.is_file())"`。
Result：`True`；`import ui, core, services, application, infrastructure, prompts, tools, utils` 全部成功（`all imports ok`）。证明产物自包含、不依赖仓库根出现在 `sys.path`。

Command：`uv run python -m pytest tests -q`（仓库根，M1 完成后）。
Result：`2 failed, 769 passed in 29.23s`（共 771 收集）。两个失败 `test_openai_compatible_provider::test_catalog_contains_builtin_providers`、`test_search_tools::test_registry_generates_search_tool_schemas_and_prompts` 均在基线 3 个既有失败之内，无新增失败；基线的 `test_bash_tool::test_bash_descriptor_schema_and_prompt` 因提交 `0eef316` 精简测试而不再存在。

### M2.1（2026-09-19）

Command：`ls .../onecode-pkgtest/Scripts | grep onecode` 与干净 venv 的 `python -c "from importlib.metadata import entry_points; ..."`。
Result：存在 `onecode.exe`；console_scripts 入口元数据为 `['ui.cli.app:main']`。

Command：在空目录 `C:/Users/rowla/AppData/Local/Temp/opencode/onecode-empty` 执行 `printf '只回复 OK\n' | .../onecode-pkgtest/Scripts/onecode.exe`。
Result：退出码 1。异常链为 `ui.cli.batch.run_batch` → `ui.cli.app.build_runtime` → `application.runtime.build_runtime` → `infrastructure.providers.factory.create_model_client` → `ProviderError: Provider .env file is missing or empty: C:\Users\rowla\AppData\Local\Temp\opencode\onecode-empty\.env`。即命令已在仓库外的隔离安装中运行、成功导入全部包、并按当前工作目录查找 `.env`，不是 `ModuleNotFoundError`。追踪栈路径为 `.../onecode-pkgtest/Lib/site-packages/ui/...`，证明使用的是已安装产物而非仓库源码。

Command：TTY 人工烟测（`onecode` 进入全屏 TUI、`onecode.tcss` 加载、`/exit` 退出）。
Result：本会话为无 TTY 的自动化环境，**未执行**，记为待人工验证项。样式表可被 `importlib.resources` 解析已在 M1.2 证明。

附注（非阻塞）：在 Windows 下把错误渲染到管道时，`rich` 输出 `✗`（U+2717）会触发 `UnicodeEncodeError: 'gbk' codec ...`，是在 provider 配置错误之后、退出前的次级渲染问题，与打包无关，也不是本计划范围。

### M2.2（2026-09-19）

Command：`uv tool install --force .`（仓库根）。
Result：成功，`Installed 1 executable: onecode`；`uv tool list` 出现 `onecode v0.1.0`。本机 `uv tool dir` 为 `D:\uv\tools`，可执行文件落在 `D:\uv\bin\onecode.exe`，`which onecode` 解析到 `/d/uv/bin/onecode`。

Command：在空目录通过 PATH 运行 `printf 'hello\n' | onecode`。
Result：退出码 1；追踪栈指向 `D:\uv\tools\onecode\Lib\site-packages\ui\cli\batch.py`，并按 cwd 报 `Provider .env file is missing or empty: .../onecode-empty/.env`。证明全局安装的命令可从任意目录启动、使用隔离工具环境、共享同一 cwd/`.env` 语义。

Command：重复 `uv tool install --force .`。
Result：成功且幂等，`Installed 1 executable: onecode`。

Command：`uv tool uninstall onecode`。
Result：`Uninstalled 1 executable`；`which onecode` 不再解析；`uv tool list` 不再含 `onecode`；随后 `uv run python -c "import ui.cli.app as a; print(callable(a.main))"` 返回 `True`，证明项目 `.venv` 的 editable 安装不受全局卸载影响。验证后再次 `uv tool install .` 恢复全局命令，最终状态 `onecode` 可从 PATH 使用（`D:\uv\bin\onecode.exe`）。

### M3.1（2026-09-19）

新增 `tests/test_packaging.py`（8 个只读断言，无构建、无联网、不写用户目录）：入口点、`build-backend`、`packages.find` 的 include 覆盖 8 个运行时包、exclude 覆盖 5 个非运行时目录、`ui.tui` 的 `*.tcss` 包数据、`ui.cli.app.main` 可导入可调用、`importlib.resources` 能解析 `onecode.tcss`、`OneCodeTuiApp.CSS_PATH == "onecode.tcss"`。

Command：`uv run python -m pytest tests/test_packaging.py -q`。
Result：`8 passed in 0.80s`。

失败注入验证（临时修改 `pyproject.toml` 后立即从备份恢复，未提交）：
- 移除 `[project.scripts]`：`test_console_script_entry_point` 以 `KeyError: 'scripts'` 失败，退出码 1。
- 移除 `[tool.setuptools.package-data]`：`test_tui_stylesheet_is_package_data` 以 `KeyError: 'package-data'` 失败，退出码 1。
恢复后 `git diff --stat -- pyproject.toml` 仍为 23 insertions，`tests/test_packaging.py` 再次 8 passed，确认配置未被残留修改。

### M3.2（2026-09-19）

更新 `README.md`、`README_en.md` 的“启动终端 / Launch Terminal”：主推 `uv tool install .` 后任意目录直接 `onecode`，保留源码内 `uv run python -m ui.cli.app` 作为开发方式；batch 示例改为 `... | onecode` 并给出源码内等价命令。更新 `docs/design-docs/cli-architecture.md:7` 的启动说明，区分“安装后命令 `onecode`”与“源码内 `uv run python -m ui.cli.app`”，说明两者都进入同一 `main()`。`.gitignore:8-9` 已含 `dist/`、`build/`。

Command：`uv run python -m pytest tests/test_packaging.py tests/test_import_boundaries.py -q`。
Result：`16 passed in 1.34s`。

Command：`uv run python -m compileall core services infrastructure application ui`。
Result：退出码 0。

Command：`uv run python -m pytest tests -q`（M3 完成后）。
Result：`3 failed, 776 passed in 33.73s`（共 779 收集 = 原 771 + 新增 8 个打包测试）。3 个失败：
- `test_openai_compatible_provider::test_catalog_contains_builtin_providers`、`test_search_tools::test_registry_generates_search_tool_schemas_and_prompts`：基线既有失败。
- `test_conversation_view::test_unmounted_message_reenters_with_full_content`：**本计划之外的既有 flaky 测试**。证据：单独运行该测试 3 次为 1 失败 2 通过；`tests/test_cli_commands.py tests/test_conversation_view.py` 联跑 33 passed；`--ignore=tests/test_packaging.py` 后全量仍然复现；测试用固定 `pilot.pause(0.2)` 等待虚拟视口，属时间敏感。本计划只改配置、文档与新增测试文件，不触及 `ui/tui/conversation`，无法造成该失败。

Command：`git status --short`。
Result：不含 `dist/`、`build/`、`onecode.egg-info/`，构建产物均被 `.gitignore` 覆盖。

## Artifacts And Notes

入口为 [plan.md](plan.md)，批次与命令为 [execution.md](execution.md)，选择为 [decisions.md](decisions.md)。主要风险：扁平布局包发现遗漏、`onecode.tcss` 未随包分发、从仓库外目录运行时仍隐式依赖 cwd、Windows PATH 与 `uv tool` 行为。M1 已关闭包发现与包数据；M2 已关闭仓库外 cwd 语义与 `uv tool` 安装/卸载行为；M3 已用 `tests/test_packaging.py` 与文档关闭契约回归风险。四个风险全部有验证证据。

构建产物 `dist/`、`build/`、`onecode.egg-info/` 均为可丢弃文件并已在 `.gitignore` 中忽略（`git status --short` 不显示）。临时 venv 位于 `C:/Users/rowla/AppData/Local/Temp/opencode/onecode-pkgtest`。

本计划尚未归档到 `completed/`：剩余唯一未闭环项是需真实 TTY 的 TUI 人工烟测（M2.1），完成后再整体移动目录并复查相对链接。

## Outcomes And Retrospective

M1 达成：`uv build` 产出完整 wheel 与 sdist，wheel 含 8 个运行时包与 `onecode.tcss`、不含非运行时目录，干净 venv 在仓库外可导入并解析包数据，源码内入口无回归。

M2 达成：隔离 venv 与 `uv tool` 全局安装中，`onecode` 入口元数据均为 `ui.cli.app:main`；在仓库外空目录运行时进入 batch 并按当前工作目录查找 `.env`（provider 配置错误、退出码 1，而非导入错误）；重复安装幂等，卸载可干净移除且不影响项目 `.venv`；最终全局命令保留可用。唯一未闭环的是需真实 TTY 的 TUI 人工烟测。

M3 达成：`tests/test_packaging.py` 锁定入口点、构建后端、包发现、包数据与样式表资源，且在删除 `[project.scripts]` 或 `[tool.setuptools.package-data]` 时会失败；中英文 README 与 CLI 设计文档同时给出 `onecode` 与源码内 `uv run` 两种启动方式；`.gitignore` 覆盖构建产物；`tests/test_import_boundaries.py` 与 `compileall` 无退化。全量测试 776 passed，3 failed（其中 2 个为基线既有，1 个为与本计划无关的既有 flaky 测试）。

剩余工作：在真实交互终端执行一次 `onecode` 人工烟测（进入 TUI、`onecode.tcss` 正常加载、`/exit` 正常退出）。完成后把四文件目录整体移到 `docs/exec-plans/completed/onecode-cli-packaging/`，复查相对链接，并在本文件补最终 Outcomes。
