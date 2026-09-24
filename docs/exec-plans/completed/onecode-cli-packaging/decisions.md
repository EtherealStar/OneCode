# Decisions

记录本执行计划特有的选择。持久的模块架构仍属于 `docs/` 下对应文档；此处只记录本次执行的语境与取舍理由。

## Decision Log

### 2026-09-18：用 Python console script 与 wheel，不用独立二进制

Decision：以标准 Python 发行包加 `[project.scripts] onecode = "ui.cli.app:main"` 提供命令，推荐 `uv tool install .` 安装；不做 PyInstaller / 单文件原生二进制。

Context：需求是“终端输入 `onecode` 即可运行”。项目依赖 tree-sitter、tree-sitter-bash、textual 等含平台相关原生扩展，冻结二进制需要额外维护各平台产物与构建脚本。

Rationale：console script 直接复用现有 `main()` 分流与依赖解析，改动最小、跨平台一致，且与仓库既有 `uv` 工作流一致。

Consequences：用户机器需能安装该 wheel 的 Python（`uv tool` 会自动管理解释器）；M1/M2 只需修改 `pyproject.toml` 与安装验证，无需构建脚本。

Date/Author：2026-09-18 / opencode。

### 2026-09-18：setuptools 加显式 packages.find，不重构布局

Decision：构建后端使用 setuptools，并用 `[tool.setuptools.packages.find]` 显式列出 8 个运行时顶层包、排除 `reference/tests/docs/lessons/assets`；不改为 `src/` 布局，不重命名或合并顶层包。

Context：仓库是扁平布局，顶层有 `application/core/infrastructure/prompts/services/tools/ui/utils` 等多个包；`uv build` 当前直接报 “Multiple top-level packages discovered in a flat-layout”，且发现候选里包含 `assets`、`lessons`、`reference` 等非运行时目录。

Rationale：显式发现是最小、可读、可测试的修复；重构布局会波及全部 import、测试与文档，远超本次目标。

Consequences：`uv.lock` 中 `onecode` 从 `virtual` 变为可构建项目，需要 `uv lock` 与 `uv sync --dev`；`tests/test_packaging.py` 锁定 include 与包数据，防止误删。

Date/Author：2026-09-18 / opencode。

### 2026-09-18：`onecode.tcss` 作为包数据分发

Decision：在 `[tool.setuptools.package-data]` 声明 `"ui.tui" = ["*.tcss"]`，让 `ui/tui/onecode.tcss` 进入 wheel 与 sdist，并由测试断言其存在。

Context：`ui/tui/app.py:73` 使用 `CSS_PATH = "onecode.tcss"`，Textual 相对模块文件解析；非 `.py` 文件默认不会进入 wheel。运行时其余资源都在工作目录或用户目录，不需要打包。

Rationale：不加包数据会导致安装后 TUI 启动时找不到样式表，而源码内运行不会暴露这个问题。

Consequences：M1.2 的 wheel 清单检查与 M3.1 的资源断言都必须覆盖该文件。

Date/Author：2026-09-18 / opencode。

### 2026-09-18：保持入口参数行为不变

Decision：本次不修改 `ui/cli/app.py::main`，`onecode` 与 `uv run python -m ui.cli.app` 的参数语义一致（当前 `main(argv)` 忽略 argv，无 `--help`/`--version`）。

Context：需求只要求启动；增加参数解析会引入新行为与测试面。

Rationale：最小改动、避免范围蔓延；`main()` 已返回 `int`，可直接作为 console script。

Consequences：`onecode --help` 目前等同直接启动，作为已知限制记入 progress；若后续需要参数解析，另立计划。

Date/Author：2026-09-18 / opencode。

### 2026-09-18：逆转“不添加 console script”的旧决策

Decision：本计划新增 console script，逆转 `docs/exec-plans/completed/cli-main-ui-plan.md:41` 记录的“启动入口使用 `uv run python -m ui.cli.app`，不先添加 console script”。

Context：旧决策是为第一版 MVP 避免修改打包配置；当前 TUI 已成为默认交互入口，用户明确要求无需停留在仓库根即可启动。

Rationale：依赖边界已稳定，修改打包配置的风险可接受，且需求直接指向终端命令。

Consequences：README 与 CLI 设计文档需同步更新；旧文档保留为历史记录，不再代表当前方向。

Date/Author：2026-09-18 / opencode。

## 尚待实施验证的选择

构建后端在扁平布局下对 editable 安装（`uv sync`）与 wheel 安装（`uv tool` / `uv pip install .`）的行为都需实测；若 editable 下 `ui.tui` 包数据解析异常（源码内运行时 `CSS_PATH` 本就相对模块文件，预期正常），以 wheel 安装为准并记录差异。

Windows 上 `uv tool install` 的 PATH 生效方式（`%USERPROFILE%\.local\bin` 与 `uv tool update-shell`）需在 M2.2 实测记录；不同 shell（PowerShell / CMD / Git Bash）可能需要重开终端。

`onecode` 安装后若与依赖的顶层包名（如 `tools`/`utils`）在共享环境冲突：`uv tool` 为每个工具建隔离 venv，预期无冲突；若用户用系统 Python 全局 `pip install`，则可能出现，应在文档中建议使用 `uv tool` 或 venv。
