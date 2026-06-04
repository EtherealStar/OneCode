# 实现 glob 和 grep 工具

本 ExecPlan 是一个活文档。实现过程中必须持续维护 `Progress`、`Surprises & Discoveries`、`Decision Log` 和 `Outcomes & Retrospective`。

本计划遵守仓库根目录的 `PLANS.md`，并把必要背景写入本文，使后续执行者只阅读本文和当前工作区也能完成实现。

## Purpose / Big Picture

完成本改动后，OneCode agent 可以通过一等 runtime 工具发现文件和搜索文件内容。模型需要按通配模式查找文件时使用 `glob`，例如 `**/*.py`；需要用正则搜索代码内容时使用 `grep`。两个工具都像现有 `read_file` 和 `edit_file` 一样通过 `ToolRegistry` 注册，通过 sandbox guard 保护，通过动态工具 schema 暴露给模型，并由 focused tests 覆盖。

本计划不实现 `ask_user_question`。该工具依赖未来 UI 交互层来展示问题、收集答案和返回结果；等 UI 能承载用户交互后再单独计划。

## Progress

- [x] (2026-06-04 10:45Z) 阅读 `AGENTS.md`、`PLANS.md`、`architecture.md`、`docs/design-docs/tool-design-guidelines.md`、active plans、tech debt、当前文件工具，以及参考 `GlobTool`、`GrepTool`。
- [x] (2026-06-04 10:45Z) 记录用户决策：工具名使用 snake_case；`grep` 使用 ripgrep；搜索结果需要通过 guard 过滤并用缓存降低性能开销；`grep` 支持参考实现字段。
- [x] (2026-06-04 11:05Z) 按用户要求把计划改为中文，并把校验方案从 JS Zod / 手写 JSON Schema 子集改为 Python Pydantic v2。
- [x] (2026-06-04 12:45Z) 引入 Pydantic v2，并在 `glob` / `grep` 工具模块内用 Pydantic model 生成 provider-visible JSON Schema，同时让工具级 validator 执行复杂约束。
- [x] (2026-06-04 12:45Z) 新增 `tools/glob/` descriptor、prompt、实现和 focused tests，覆盖分页、guard root 阻断、逐项 denied result 过滤和只读 classification。
- [x] (2026-06-04 12:45Z) 新增 `tools/grep/` descriptor、prompt、ripgrep runner、实现和 focused tests，覆盖 fake runner、真实 `rg` smoke、三种输出模式、结构化 ripgrep 错误和 denied result 过滤。
- [x] (2026-06-04 12:45Z) 补充 `ToolRegistry.tool_prompt_sections()`，并用 search tool schema/prompt 测试证明稳定排序和 prompt 暴露。
- [x] (2026-06-04 12:45Z) 更新 `architecture.md` 和 `docs/tech-debt/tech-debt-tracker.md`，记录 `glob` / `grep` 已实现，并保留 durable result store、只读策略和并发调度限制。
- [x] (2026-06-04 12:55Z) 运行 compile checks、focused tests 和 full tests：`uv run python -m compileall core services infrastructure tools tests` 通过，`uv run python -m pytest tests/test_tool_registry_and_executor.py tests/test_search_tools.py -q` 为 20 passed，`uv run python -m pytest tests -q` 为 99 passed。

## Surprises & Discoveries

- Observation: OneCode 是 Python 项目，而参考工具使用 TypeScript 和 Zod。
  Evidence: `pyproject.toml` 当前只定义 Python 依赖，已有工具 descriptor 使用 JSON Schema 形状的字典交给 `services/tools/executor.py` 投影为 provider schema。用户要求使用 Pydantic 替代 JS Zod，因此本计划采用 Pydantic v2 输入模型做运行时校验，并用 Pydantic 生成 provider 可见 JSON Schema。

- Observation: 当前共享 schema validation 很小，不能完整表达 `grep` 的字段约束。
  Evidence: `services/tools/executor.py` 目前只校验 required、unexpected fields、`string`、`boolean`、`integer` 和 `minimum`。它不校验 `enum`、数组、嵌套对象、数字类型、map 字段等。新工具应通过 Pydantic `BaseModel` 做严格校验，而不是继续扩大手写 JSON Schema validator。

- Observation: executor 已经消费 `ToolResultPolicy`。
  Evidence: `RegistryToolExecutor._apply_result_policy()` 会把过大的结果内容替换成 JSON 预览和 truncation metadata。`grep` 可以依赖该机制实现 20KB 结果预算，即使 durable result store 仍是未来能力。

- Observation: guard 当前只对 `ToolTarget.kind` 为 `file` 或 `directory` 的 target 做文件系统检查。
  Evidence: `RegistryToolExecutor._check_guard()` 跳过非文件系统 target，并接受 `read`、`write`、`list`、`delete` 操作。`glob` 和 `grep` 应先把搜索根声明为 `directory/list`、`directory/read` 或 `file/read` target，再在 handler 中额外过滤每个候选结果。

- Observation: `grep` content 模式不能按第一个 `-` 切路径前缀。
  Evidence: ripgrep context 输出会使用 `path-line-text`，但普通文件名也可能包含连字符。实现改为优先匹配 `:\d+:` 或 `-\d+-` 这类带行号分隔符，并补充 `my-file.py:1:needle` 测试，避免误把 `my-file.py` 切成 `my`。

## Decision Log

- Decision: provider-visible 工具名使用 `glob` 和 `grep`。
  Rationale: 工具设计指南要求 snake_case，并且目录名、registry key、provider-visible function name 和测试 fixture 保持一致。TypeScript 参考实现中的 `Glob` 和 `Grep` 只作为行为参考，不作为 OneCode 命名。
  

- Decision: `grep` 通过 Python wrapper 调用 `rg` ripgrep 可执行文件。
  Rationale: 参考工具基于 ripgrep，用户也明确要求引入 ripgrep。wrapper 可以隔离 subprocess 细节，并让测试用 monkeypatch 或 fake runner 覆盖输出解析。
  

- Decision: `glob` 使用 Python 文件系统遍历和 `fnmatch`，不通过 shell glob expansion。
  Rationale: Python 遍历跨平台、避免 shell 注入风险，并能在返回前对每个候选路径执行 sandbox guard 过滤。
  Date/Author: 2026-06-04 / Codex

- Decision: 搜索 handler 必须对候选结果路径做 guard 过滤，并使用单次调用内缓存。
  Rationale: 只 guard 搜索根目录可能泄露被 deny pattern 命中的文件名。逐项 guard 可以避免泄露；用 call-local cache 按规范化绝对路径和操作缓存结果，可降低 content/count 模式中重复路径的分类开销。
  

- Decision: 使用 Pydantic v2 替代 JS Zod 和大规模手写 JSON Schema validator。
  Rationale: OneCode 是 Python runtime。Pydantic v2 能提供严格字段、枚举、默认值、额外字段禁止、自定义校验和 JSON Schema 生成，更适合承载 `grep` 的复杂输入约束。
  
- Decision: `grep` runner 参数固定加入 `--with-filename`。
  Rationale: 单文件搜索时 ripgrep 默认可能省略文件名前缀，而 OneCode 需要从 content/count 输出中解析路径并做逐项 guard 过滤。强制带文件名让目录和单文件搜索的解析行为一致。
  Date/Author: 2026-06-04 / Codex

- Decision: `grep` 的 `context` 和 `-C` 同时出现时由 Pydantic 解析后优先 `context`。
  Rationale: 计划要求记录这个优先级。实现中 `_build_rg_args()` 先读取 `parsed.context`，仅当它缺失时再使用 `parsed.context_flag`。
  Date/Author: 2026-06-04 / Codex

## Outcomes & Retrospective

已实现 `glob` 和 `grep` 两个只读搜索工具，并接入 CLI 的固定工具 registry。`glob` 使用 Python `Path.rglob()` 与 `fnmatch`，`grep` 使用 subprocess 参数列表调用 PATH 上的 `rg`。两者都通过 executor 的 input validation、classification、guard、hook 和 result policy 流程，并在 handler 内对候选结果做逐项 guard 过滤，避免广泛搜索泄露 denied path。

Focused validation 已通过：

    uv run python -m pytest tests/test_tool_registry_and_executor.py tests/test_search_tools.py -q
    20 passed

最终验证也已通过：

    uv run python -m compileall core services infrastructure tools tests
    uv run python -m pytest tests -q
    99 passed

仍保留的限制是 durable result store、真正并发调度和只读策略裁剪尚未实现；这些限制已在技术债 TD-006 中保持为部分缓解而非完全解决。

## Context and Orientation

OneCode 是 Python code-agent runtime。`core/loop.py` 中的主循环保持薄：追加用户消息，构建 `ContextSnapshot`，调用模型，通过注入的 tool executor 执行工具调用，追加工具结果，并循环到完成。新增工具不能硬编码进主循环。

工具 runtime 位于 `services/tools/`。`services/tools/types.py` 定义 `ToolDescriptor`、`ToolCallClassification`、`ToolTarget`、`ToolResultPolicy` 和 `ToolRuntime`。具体工具放在 `tools/<tool_name>/`，并从 `tool.py` 导出 `descriptor()`；工具专属 prompt 放在 `prompt.py`。

`services/tools/registry.py` 管理启用的工具 descriptor。它当前通过 `tool_schemas(state)` 返回 OpenAI-compatible provider schema。实现本计划时，如果 prompt section 支持仍缺失，应补充 `tool_prompt_sections(state) -> tuple[str, ...]`，按稳定 descriptor 顺序返回非空 prompt 字符串。该方法只暴露工具 prompt，不负责组装完整 system prompt。

`services/tools/executor.py` 管理执行流程：查找 descriptor、校验输入形状、运行工具级校验、分类本次调用、对文件系统 target 执行 guard、运行 hooks、调用 handler、应用 result policy、返回结构化错误。新工具不得绕过该流程。

`services/guard/` 管理路径安全。`SandboxGuard.check_path()` 会把路径分类为 allow、ask-required 或 denied。executor 已经会检查 descriptor classification 声明的 `ToolTarget`。搜索工具还必须在 handler 中额外过滤结果路径，因为搜索根可能允许，但某些结果文件可能命中 deny pattern。

`tools/read_file/` 和 `tools/edit_file/` 是当前本地工具实现模式。每个工具都有 `__init__.py`、`tool.py` 和 `prompt.py`；descriptor 包含严格 input schema、prompt、search hint、validator、input-aware classifier 和 handler。

参考 TypeScript 工具位于 `docs/references/Tools_full/GlobTool/` 和 `docs/references/Tools_full/GrepTool/`。它们定义目标产品行为：`Glob` 按通配符查找文件并按修改时间排序；`Grep` 使用 ripgrep，支持 `content`、`files_with_matches`、`count` 三种输出模式，支持文件过滤和上下文参数，默认限制输出，并通过 result-size budget 管理大结果。

## Plan of Work

第一阶段引入 Pydantic v2 校验约定。编辑 `pyproject.toml`，在 runtime dependencies 中加入 `pydantic>=2.0`。新工具的输入 schema 不再手写复杂 JSON Schema，而是在 `tools/glob/tool.py` 和 `tools/grep/tool.py` 中定义 Pydantic `BaseModel`，使用 `model_config = ConfigDict(extra="forbid", strict=True)` 禁止额外字段并启用严格类型。descriptor 的 `input_schema` 由 `InputModel.model_json_schema()` 生成，供 provider schema 使用；descriptor 的 `validate_input` 调用 `InputModel.model_validate(tool_input)`，把 `ValidationError` 转换成 `ValidationResult.failure(...)`。handler 和 classifier 可以复用同一个解析 helper，例如 `_parse_input(tool_input) -> InputModel`，避免重复处理默认值。

第二阶段保留 executor 的轻量 schema validation，但不要把它扩展成完整 JSON Schema 引擎。`services/tools/executor.py` 当前的基础校验仍可提前拦截缺失字段、额外字段和简单类型错误；Pydantic 负责复杂约束，例如 enum、默认值、字段别名、自定义校验和上下文规则。如果 Pydantic 生成的 JSON Schema 包含当前 executor 不理解的关键字，executor 应忽略这些关键字，不应把合法 schema 当作错误。新增测试应证明工具级 Pydantic validator 能拦截 unsupported `output_mode`、负数、错误布尔值、额外字段和空 pattern。

第三阶段如有必要，补充 registry prompt-section 支持。编辑 `services/tools/registry.py`，新增 `tool_prompt_sections(state)`。返回顺序与 `descriptors()` 一致，跳过空 prompt，不引入 permission/enablement 逻辑。新增测试证明 prompt section 稳定排序并跳过空 prompt。

第四阶段新增 `tools/glob/`。创建 `tools/glob/__init__.py`、`tools/glob/prompt.py` 和 `tools/glob/tool.py`。descriptor name 是 `glob`，description 是一句短描述，search hint 是 `find files by name pattern`，result policy 是 100KB，preview 约 4000 chars。Pydantic 输入模型字段为：

    pattern: str
    path: str | None = None
    head_limit: int | None = None
    offset: int = 0

模型校验规则：`pattern` 去除首尾空白后不能为空；`head_limit` 如果提供必须大于或等于 0；`offset` 必须大于或等于 0；禁止额外字段。`path` 默认为 sandbox cwd。`head_limit` 默认语义是 100；显式 `0` 表示 unlimited，应谨慎使用。

`glob` 的工具级 validator 还应处理文件系统语义：如果提供的 `path` 在 sandbox 内且存在但不是目录，应返回 invalid input。若 `path` 在 sandbox 外，不要在 validator 中直接 stat；让 classification 和 executor guard 返回结构化 ask/deny 结果。classifier 返回 `read_only=True`、`modifies_filesystem=False`、`concurrency_safe=True`，一个 `ToolTarget(kind="directory", operation="list", value=path_or_dot)`，以及 permission subject `glob:<path>:<pattern>`。

`glob` handler 必须要求 `runtime.guard`。它先通过 `guard.check_path(root, operation="list", kind="directory")` 取得允许的规范化 root。然后递归枚举 root 下的文件，用相对 root 的 slash-normalized 路径和 `fnmatch` 匹配 pattern，跳过目录，只收集文件。匹配结果按修改时间降序排序，路径作为稳定 tiebreaker。排序后应用 offset 和 head limit。返回路径尽量相对 `runtime.guard.boundary.cwd`，并统一使用 `/`。每个候选文件返回前都要通过 `guard.check_path(candidate, operation="read", kind="file")` 过滤。使用单次调用内 cache，避免重复分类同一路径。模型可见 content 每行一个文件名，并在分页时附短提示。metadata 至少包含 `num_files`、`total_matches_before_pagination`、`filtered_count`、`applied_limit`、`applied_offset`、`truncated` 和 `path`。

第五阶段新增 `tools/grep/`。创建 `tools/grep/__init__.py`、`tools/grep/prompt.py` 和 `tools/grep/tool.py`。descriptor name 是 `grep`，description 是一句短描述，search hint 是 `search file contents with regex`，result policy 是 20KB，preview 约 4000 chars。Pydantic 输入模型字段支持参考实现：

    pattern: str
    path: str | None = None
    glob: str | None = None
    output_mode: Literal["content", "files_with_matches", "count"] = "files_with_matches"
    before: int | None = Field(default=None, alias="-B")
    after: int | None = Field(default=None, alias="-A")
    context_flag: int | None = Field(default=None, alias="-C")
    context: int | None = None
    show_line_numbers: bool | None = Field(default=None, alias="-n")
    case_insensitive: bool = Field(default=False, alias="-i")
    type: str | None = None
    head_limit: int | None = None
    offset: int = 0
    multiline: bool = False

使用字段 alias 保持 provider-visible 参数名和参考实现一致，例如 `-B`、`-A`、`-C`、`-n`、`-i`。模型配置需要允许通过 alias 校验。默认语义：`output_mode` 默认为 `files_with_matches`；`head_limit` 默认为 250；显式 `head_limit=0` 表示 unlimited；`offset` 默认为 0；`-n` 只在 `content` 模式默认 true；`-i` 和 `multiline` 默认 false。

`grep` Pydantic validator 应拒绝空 pattern、负数 numeric fields、空 `glob`、空 `type`。上下文参数只允许在 `content` 模式使用；如果 `output_mode` 不是 `content` 且传入 `-A`、`-B`、`-C` 或 `context`，应返回清晰 invalid input，而不是静默忽略。若同时提供 `context` 和 `-C`，优先 `context`，并在实现注释和测试中记录该决策。`path` 可以指向文件或目录；如果在 sandbox 外，让 executor guard 处理 ask/deny，不要在 validator 中直接 stat。

`grep` classifier 返回 `read_only=True`、`modifies_filesystem=False`、`concurrency_safe=True`，以及文件系统 read target。如果 `path` 缺失，target 是 `ToolTarget(kind="directory", operation="read", value=".")`。如果 `path` 存在，默认使用 `kind="directory"` 即可让 guard 安全分类；handler 后续可检测实际是文件还是目录。permission subject 为 `grep:<path_or_dot>:<pattern>`。

`grep` handler 必须要求 `runtime.guard`，先通过 `guard.check_path(path_or_dot, operation="read", kind="directory")` 检查搜索路径，再调用 ripgrep wrapper。wrapper 可以是 `tools/grep/tool.py` 中的私有函数或小型 runner 类。必须使用 `subprocess.run()` 的参数列表，不使用 shell 字符串。ripgrep 参数始终包含降低噪音的选项：`--hidden`、`--max-columns 500`，并排除 VCS 目录 `.git`、`.svn`、`.hg`、`.bzr`、`.jj`、`.sl`。`files_with_matches` 加 `-l`；`count` 加 `-c`；`content` 根据 `-n` 加行号。`multiline` 加 `-U` 和 `--multiline-dotall`；`-i` 加 case-insensitive；`type` 加 `--type <type>`；`glob` 拆成一个或多个 `--glob <pattern>`。如果 regex pattern 以 `-` 开头，用 `-e <pattern>` 传入。

显式处理 ripgrep return code。返回码 0 表示找到匹配。返回码 1 表示没有匹配，应返回非错误结果。返回码 2、缺失 executable 或 subprocess 异常应返回结构化 tool error，metadata error 可为 `ripgrep_error` 或 `ripgrep_not_found`。不要让 subprocess exception 逃出 handler。

`files_with_matches` 模式把 ripgrep 输出解析为路径，对每个路径通过 guard read check 过滤，使用单次调用内 cache，按修改时间降序排序并用路径 tiebreaker，然后应用 offset/head_limit。结果 content 为 `Found N files` 加每行一个路径，路径尽量相对 workspace。`content` 模式解析每行 path prefix，通过同一 cache 过滤路径，relativize path prefix，再对输出行应用 offset/head_limit。`count` 模式解析 `path:count`，过滤路径，relativize，应用 offset/head_limit，并从显示行计算总匹配数。metadata 至少包含 mode、num files、num lines 或 num matches、filtered count、applied limit、applied offset 和 truncated。

第六阶段增加测试。建议新增 `tests/test_search_tools.py`。使用 `tmp_path` workspace 和 `SandboxGuard(SandboxBoundary(cwd=workspace, denied_patterns=...))`。测试覆盖 descriptor schema projection、Pydantic validation、classification、prompt section、guard deny/ask、blocked root 时 handler 不执行、denied result filtering、pagination、stable sorting、grep 三种 output mode。ripgrep 集成测试先检查 `rg --version` 是否可用；可用则跑真实 subprocess 测试，不可用则只 skip 需要 real rg 的测试，保留 parser/handler fake runner 单元测试。

第七阶段更新文档。编辑 `architecture.md`，把 `tools/` 章节中的 `glob` 和 `grep` 标为已实现；`write_file`、`bash` 仍保持 future 或 out of scope。更新 `docs/tech-debt/tech-debt-tracker.md`：如果实现后仍缺 durable result store 或真正并发调度，应保留相关技术债，不要把结果治理或并发能力错误标记为完全解决。

## Concrete Steps

所有命令都在仓库根目录执行：

    cd D:\study\OneCode

开始前查看工作区，确保不覆盖用户已有改动：

    git status --short

确认 ripgrep 是否可用：

    rg --version

如果开发环境中 `rg --version` 失败，仍继续实现 `grep` 工具，使其运行时返回结构化 `ripgrep_not_found`；只 skip 真实 ripgrep 集成测试。除非用户另行批准，不在本计划中安装系统软件。

按以下顺序编辑：

1. 编辑 `pyproject.toml`，加入 `pydantic>=2.0` runtime dependency，并按项目习惯运行 `uv sync --dev` 或让 `uv run` 更新锁文件。
2. 为新工具建立 Pydantic 输入模型到 `ToolDescriptor.input_schema` 的生成约定；可以直接在各工具模块内使用 `InputModel.model_json_schema()`，不必先抽象公共 helper。
3. 如需要，更新 `services/tools/executor.py`，确保它不会误拒绝 Pydantic 生成 schema 中当前不理解的关键字。
4. 更新 `services/tools/registry.py`，加入 `tool_prompt_sections()` 和测试。
5. 新增 `tools/glob/__init__.py`、`tools/glob/prompt.py` 和 `tools/glob/tool.py`。
6. 新增 `tools/grep/__init__.py`、`tools/grep/prompt.py` 和 `tools/grep/tool.py`。
7. 新增 `tests/test_search_tools.py`。
8. 更新 `architecture.md` 和 `docs/tech-debt/tech-debt-tracker.md`。

实现过程中先运行 focused validation：

    uv run python -m pytest tests/test_tool_registry_and_executor.py tests/test_search_tools.py -q

运行 compile check：

    uv run python -m compileall core services infrastructure tools

运行全量测试：

    uv run python -m pytest tests -q

实现后可以用一个小 Python 场景做手动 smoke test：注册 `read_file`、`edit_file`、`glob` 和 `grep` descriptors 到同一个 `ToolRegistry`，创建带 workspace guard 的 `RegistryToolExecutor`，执行：

    ToolCall(id="call-glob", name="glob", input={"pattern": "**/*.py", "path": "."})
    ToolCall(id="call-grep", name="grep", input={"pattern": "ToolDescriptor", "path": ".", "glob": "*.py", "output_mode": "files_with_matches"})

第一个调用应返回 Python 文件路径；第二个调用应返回包含 `ToolDescriptor` 的文件。

## Validation and Acceptance

验收标准一：`ToolRegistry.tool_schemas(RuntimeState())` 在注册 `glob` 和 `grep` descriptors 后包含两个工具 schema。schema 使用 snake_case function name、短 description、由 Pydantic 生成的严格参数结构，并禁止额外字段。

验收标准二：如果本计划新增 `ToolRegistry.tool_prompt_sections(RuntimeState())`，它应按稳定 descriptor 顺序返回 `glob` 和 `grep` prompt，并跳过空 prompt。

验收标准三：无效输入在 handler 执行前失败。例子包括缺失 `pattern`、非字符串 `pattern`、不支持的 `output_mode`、负数 `head_limit`、负数 `offset`、非布尔 `-i`、额外字段和空 pattern。复杂约束由 Pydantic validator 负责。

验收标准四：`glob` classification 标记为 read-only、不修改文件系统、concurrency-safe，target 是 `directory/list`，结果预算为 100KB。它返回按修改时间排序的匹配文件，支持分页，并记录 truncation 和 filtering metadata。

验收标准五：`grep` classification 标记为 read-only、不修改文件系统、concurrency-safe，target 是文件系统 read，结果预算为 20KB。它支持 `files_with_matches`、`content`、`count` 输出模式，支持 `glob`、`type`、上下文参数、case-insensitive、multiline、`head_limit` 和 `offset`。

验收标准六：sandbox guard 会在 handler 执行前阻止不安全搜索根。denied 或 external `path` 返回现有结构化 guard error，不枚举文件，也不执行 ripgrep。

验收标准七：允许的广泛搜索不会泄露 denied result path。如果 workspace 中有 `public.txt` 和被 deny 的 `secret.txt`，那么对 `.` 执行 `glob` 或 `grep` 不得返回 `secret.txt`；当 denied 文件本来匹配时，metadata 应显示至少一个 filtered result。

验收标准八：ripgrep 失败是结构化 tool result。没有匹配是非错误结果。缺失 `rg`、非法 regex、timeout 如有实现、或 ripgrep return code 2 都是带有 useful metadata 的 tool error，不逃逸为 Python exception。

验收标准九：结果预算通过现有 executor 生效。超过 20KB 的大 `grep` 结果会变成非错误的 truncated preview，并带 `result_truncated` metadata。

验收标准十：以下命令通过：

    uv run python -m compileall core services infrastructure tools
    uv run python -m pytest tests -q

## Idempotence and Recovery

所有测试必须使用 `tmp_path` 临时 workspace，只能在临时目录下创建测试文件。除本计划明确编辑的源码文件外，不读写真实项目文件。不要删除用户文件。

新增工具都是只读工具。它们不得写入搜索文件、创建索引、修改文件 metadata，或更新 runtime state；唯一副作用是读取文件内容、读取 stat 用于排序，并返回结果 metadata。

如果 ripgrep 不可用，`grep` runtime 应返回结构化 `ripgrep_not_found` 错误。需要真实 ripgrep 的测试可以 skip，但 schema、Pydantic validation、classification、guard behavior 和 output parsing 的单元测试必须仍然通过。

逐项 guard 过滤必须保守。如果某个结果路径无法解析、无法分类或解析格式可疑，应从模型可见输出中省略，并增加 filtered 或 skipped count。不要因为解析失败就返回可疑路径。

guard cache 只在单次 handler 调用内有效。sandbox policy 可能在调用之间变化，持久缓存会产生过期权限风险。一个 handler 内部的简单字典足够。

## Artifacts and Notes

`glob` 工具结果示例：

    Found 3 files
    core/loop.py
    services/tools/executor.py
    tools/read_file/tool.py

    [Showing results with pagination = limit: 3]

`grep` 的 `files_with_matches` 结果示例：

    Found 2 files
    services/tools/types.py
    tools/read_file/tool.py

`grep` 的 `content` 结果示例：

    services/tools/types.py:82:class ToolDescriptor:
    tools/read_file/tool.py:27:def descriptor() -> ToolDescriptor:

缺失 ripgrep 的结构化错误示例：

    {"error":"ripgrep_not_found","message":"ripgrep executable 'rg' was not found on PATH."}

## Interfaces and Dependencies

`tools/glob/tool.py` 应暴露：

    class GlobInput(BaseModel): ...
    INPUT_SCHEMA: dict[str, Any]
    def descriptor() -> ToolDescriptor: ...

`INPUT_SCHEMA` 来自 `GlobInput.model_json_schema()`，handler 返回 `ToolExecutionResult`，其中 `content` 是模型可读文本，metadata 包含结构化计数。

`tools/grep/tool.py` 应暴露：

    class GrepInput(BaseModel): ...
    INPUT_SCHEMA: dict[str, Any]
    def descriptor() -> ToolDescriptor: ...

它也可以定义一个方便测试的小 runner 接口：

    class RipgrepRunner(Protocol):
        def run(self, args: list[str], cwd: Path) -> RipgrepResult: ...

或等价函数：

    def _run_ripgrep(args: list[str], cwd: Path) -> RipgrepResult: ...

选择最小设计，只要测试可以 monkeypatch ripgrep output，而不必每个 parser case 都调用真实 subprocess。

应在 `pyproject.toml` 加入：

    pydantic>=2.0

不加入 JavaScript `zod` 依赖。OneCode 的工具 schema contract 仍是 `ToolDescriptor.input_schema` 中的 JSON Schema-shaped dict；只是这些 dict 由 Pydantic model 生成，运行时复杂校验也由 Pydantic 执行。如果未来 TypeScript UI 需要 Zod schema，可以在单独计划中从 descriptor contract 生成或镜像。

ripgrep 可以先依赖 PATH 上的 `rg` 可执行文件，不必把 vendored ripgrep 作为 Python 依赖。如果团队未来需要打包分发 ripgrep，应另写环境/依赖计划，因为它涉及安装和平台支持。

2026-06-04 / Codex: 初始 ExecPlan 创建，纳入用户关于 snake_case name、ripgrep、带缓存的 guard result filtering、完整 grep 字段支持，以及暂缓。

2026-06-04 / Codex: 根据用户要求把计划翻译为中文，并把校验方向改为 Pydantic v2；计划保留 provider 可见 JSON Schema，但由 Pydantic model 生成，并由 Pydantic 负责复杂输入校验。
