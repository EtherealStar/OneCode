# 实现基于 Tree-sitter AST 的 BashTool

本 ExecPlan 是一个活文档。实现过程中必须持续维护 `Progress`、`Surprises & Discoveries`、`Decision Log` 和 `Outcomes & Retrospective`。

本计划遵守仓库根目录的 `PLANS.md`。本文把必要背景、用户决策、实现边界、接口形状、测试和验收步骤写入同一文件，使后续执行者只阅读本文和当前工作区也能完成实现。

## Purpose / Big Picture

完成本改动后，OneCode agent 可以通过一等 `bash` 工具运行 Git Bash 命令。BashTool 不再依赖手写正则来判断命令是否安全，而是先用 Tree-sitter Bash 解析命令，得到可信的 AST，再从 AST 提取 simple commands、argv、redirection 和命令组合结构。运行时会在 AST 上执行 `check_semantics()`，拒绝第一版无法静态分析的 shell 结构；然后派生文件系统 `ToolTarget`，接入现有 sandbox guard 和权限交互机制。只读命令会自动放行；写入、删除、未知副作用或需要用户确认的命令会通过已有 `PermissionPolicy` 和 CLI 权限面板询问用户后再执行。

用户可以通过 CLI 观察这一行为：模型请求 `bash` 执行 `git status`、`ls`、`cat README.md`、`rg pattern .` 这类只读命令时，命令自动执行并返回 stdout/stderr/exit code；请求 `mkdir build`、`touch file.txt`、`rm file.txt`、`echo ok > file.txt` 或未知命令时，CLI 会显示 Bash 权限确认面板；如果命令尝试访问 sandbox 外路径或命中 denied pattern，现有 guard 决策仍优先生效。实际执行使用 Git Bash；如果找不到 Git Bash，工具返回结构化错误，提示用户安装 Git for Windows 或把 Git Bash 加入 PATH。

本计划不实现 PowerShell、cmd.exe、WSL shell 或远程 shell。第一版只支持 Git Bash。也不迁移参考实现中的 regex-based `bashSecurity.ts` 安全验证；允许的自动放行能力来自 Tree-sitter AST、语义检查、argv/redirect 路径提取、只读命令 allowlist 和现有 guard/permission service。

## Progress

- [x] (2026-06-04) 阅读 `AGENTS.md`、`PLANS.md`、`architecture.md`、`docs/design-docs/tool-design-guidelines.md`、`docs/tech-debt/tech-debt-tracker.md`、当前 `services/tools`、`services/guard`、`services/permissions`、CLI runtime，以及参考 `docs/references/Tools_full/BashTool/`。
- [x] (2026-06-04) 阅读用户新增的 `docs/references/Tools_full/BashTool/ast.ts`，确认参考实现的核心思想是 Tree-sitter AST allowlist、fail closed、`SimpleCommand(argv, envVars, redirects, text)`、`ParseForSecurityResult` 和 `checkSemantics()`。
- [x] (2026-06-04) 与用户确认关键决策：AST 语义检查和退出码语义解释都要实现；按新增 `ast.ts` 参考重建 Python 版；权限确认 UI 已在当前 active plan 中实现；实际执行使用 Git Bash；不做正则安全验证；第一版支持顶层 `&&`、`||`、`;`、`|`，拒绝复杂结构；实现后接入 CLI runtime。
- [x] (2026-06-05) 增加 Tree-sitter Bash Python 依赖并同步环境；`uv sync --dev` 安装 `tree-sitter==0.25.2` 和 `tree-sitter-bash==0.25.1`。
- [x] (2026-06-05) 新增 `tools/bash/` 工具目录、prompt、parser、AST 模型、语义检查、只读分类、路径提取、runner 和 descriptor。
- [x] (2026-06-05) 接入 `ToolRegistry`、CLI runtime 和已有 permission policy；非只读 `command/execute` target 会触发 ask，CLI 已有 Bash 权限面板。
- [x] (2026-06-05) 补充 parser、semantics、classification、permission、runner、CLI 和 registry/prompt tests。
- [x] (2026-06-05) 更新 `architecture.md` 和 `docs/tech-debt/tech-debt-tracker.md`，记录 BashTool 当前实现和第一版限制。
- [x] (2026-06-05) 运行 focused tests、compile check 和全量 tests，并在本计划记录结果。

## Surprises & Discoveries

- Observation: OneCode 当前已经有权限交互机制的 active plan 和实现草案，`RegistryToolExecutor` 可注入 `PermissionPolicy` 与 `PermissionPrompter`，`ToolRuntime` 已有 `approved_guard_policies`。
  Evidence: `ui/cli/app.py` 当前创建 `PermissionPolicy(permission_store)`、`CliPermissionPrompter()`，并传给 `RegistryToolExecutor`；`services/tools/types.py` 定义 `is_guard_policy_allowed()`，用于让 handler 内的重复 guard 接受 executor 已批准的 ask。

- Observation: 当前 executor 只对 `ToolTarget.kind in {"file", "directory"}` 执行 sandbox guard；`command/execute` target 不会触发路径 guard。
  Evidence: `services/tools/executor.py` 的 `_check_guard()` 跳过非文件系统 target。因此 BashTool classifier 必须从 AST 派生具体文件或目录 targets，不能只返回一个 `command/execute` target。

- Observation: 当前项目没有 Tree-sitter 依赖。
  Evidence: `pyproject.toml` runtime dependencies 当前包含 `pydantic>=2.0` 和 `python-dotenv>=1.0.1`，dev dependencies 只有 `pytest>=8.0.0`。实现 BashTool 需要新增 `tree-sitter` 和 Bash grammar 包。

- Observation: 参考 `ast.ts` 不是 sandbox，也不声称判断命令一定安全；它只回答“能否可信提取每个 simple command 的 argv”。
  Evidence: `docs/references/Tools_full/BashTool/ast.ts` 顶部注释明确说该模块不阻止危险命令运行，只在 Tree-sitter AST allowlist 上 fail closed，无法理解的 node type 进入 too-complex。

- Observation: Python 可用的 PyPI 包包括 `tree-sitter` 和 `tree-sitter-bash`。
  Evidence: PyPI 显示 `tree-sitter-bash` 是 Bash grammar package，当前版本页面要求 CPython 3.10+；`tree-sitter` PyPI 页面展示 Python binding 的 `Language` / `Parser` 用法。OneCode 要求 Python >=3.11，版本范围兼容。

- Observation: 当前安装的 Python binding 版本是 `tree-sitter==0.25.2`，`tree-sitter-bash==0.25.1`，`tree_sitter_bash.language()` 返回 PyCapsule，需要包装为 `Language(...)` 后赋给 `Parser.language`。
  Evidence: `uv sync --dev` 输出安装版本；`uv run python -c "from tree_sitter import Language, Parser; import tree_sitter_bash; ..."` 成功解析 Bash AST。

- Observation: Python Bash grammar 的实际 AST 会在 `command` 下用 `command_name(word)` 包住 argv[0]，数字参数使用 `number` 节点，redirect 通过 `redirected_statement` 包一层 `command` 和 `file_redirect`。
  Evidence: 解析 `git status && rg foo . | head -20` 初次返回 `Unsupported shell structure: command_name`；打印 AST 后更新 walker 支持 `command_name`、`number` 和 `redirected_statement` redirect 合并。

## Decision Log

- Decision: BashTool 第一版使用 Python `tree-sitter` + `tree-sitter-bash`，而不是移植 TypeScript/WASM parser。
  Rationale: OneCode 是 Python runtime，具体工具也在 `tools/<name>/tool.py` 中实现。Python 包可以直接在 tool classifier 和 validator 内解析命令，减少跨语言边界。PyPI 上已有独立 Bash grammar package，满足 Python >=3.11 约束。
  Date/Author: 2026-06-04 / Codex

- Decision: 同时实现两个“语义”层：AST `check_semantics()` 和执行后 `interpret_exit()`.
  Rationale: 用户明确要求两个都做。`check_semantics()` 在执行前判断 AST 和 argv 是否能被静态理解；`interpret_exit()` 在执行后解释 grep/rg/diff/test 等命令的特殊退出码，避免把“无匹配”或“文件不同”误报为工具错误。
  Date/Author: 2026-06-04 / User + Codex

- Decision: 不实现 regex-based 安全验证，也不迁移参考 `bashSecurity.ts` 的正则扫描路径。
  Rationale: 用户明确要求“不需要正则安全验证”。第一版安全边界来自 AST fail-closed、语义检查、路径 guard、permission policy 和实际执行前的工具 runtime checks。只读命令 allowlist 可以基于 argv 和 flag parser，但不得把 regex 当作安全边界。
  Date/Author: 2026-06-04 / User

- Decision: 第一版命令组合只支持顶层 `&&`、`||`、`;`、`|`，拒绝 subshell、brace group、function definition、command substitution、process substitution、heredoc、for/while/if/case 等复杂结构。
  Rationale: 用户同意这个范围。它覆盖常见 code-agent 命令链，同时保持路径提取和权限判断可解释。无法静态分析的结构进入 permission required 或 invalid/too-complex，不靠字符串猜测。
  Date/Author: 2026-06-04 / User + Codex

- Decision: 实际执行使用 Git Bash。
  Rationale: 用户确认使用 Git Bash。Windows 下应查找 `bash.exe`，优先 PATH，再查常见安装位置，例如 `C:\Program Files\Git\bin\bash.exe`、`C:\Program Files\Git\usr\bin\bash.exe`。找不到时返回结构化 `git_bash_not_found` 错误，提示安装 Git for Windows 或配置 PATH。
  Date/Author: 2026-06-04 / User

- Decision: 只读命令自动放行；写入、删除、未知副作用和非只读命令接入已有 permission policy。
  Rationale: 用户确认权限弹窗已开始实现。BashTool 应复用 `PermissionPolicy` 和 `CliPermissionPrompter`，不在工具内部另写交互流程。只读自动放行仍必须经过 AST、语义检查和路径 guard。
  Date/Author: 2026-06-04 / User + Codex

- Decision: BashTool 必须接入 CLI runtime 的固定 registry。
  Rationale: 用户确认要接入。否则工具实现后不会进入 provider schema、dynamic prompt 或真实 agent loop。
  Date/Author: 2026-06-04 / User

## Outcomes & Retrospective

已交付第一版 BashTool：`tools/bash/` 使用 Tree-sitter Bash AST 解析 simple command、顶层组合和 redirect，生成稳定 `BashAnalysis`，在 argv 层执行 `check_semantics()` 和 wrapper stripping，基于 allowlist 做只读分类，派生文件系统 `ToolTarget`，并通过 Git Bash runner 执行命令。`PermissionPolicy` 已扩展为对非只读 `command/execute` target 触发 ask，CLI 已增加 Bash 权限面板，`ui/cli/app.py` 已注册 `bash` descriptor。

仍保留的限制已进入 `TD-009`：第一版只支持 Git Bash，不支持 PowerShell/cmd/WSL，不支持后台任务、持久 Bash prefix allow、完整 Bash 语言、profile loading 或 durable result store。复杂 AST 和 runtime expansion 会 fail closed 或进入权限确认路径。

已运行 focused tests：

    uv run python -m pytest tests\test_bash_parser.py tests\test_bash_semantics.py tests\test_bash_tool.py -q
    15 passed in 0.96s

已运行权限和 prompt 相关 tests：

    uv run python -m pytest tests\test_permission_policy.py tests\test_tool_registry_and_executor.py tests\test_cli_permissions.py tests\test_dynamic_prompt_assembler.py -q
    28 passed in 1.11s

已运行 compile check：

    uv run python -m compileall core services infrastructure tools ui prompts
    通过

已运行 full test：

    uv run python -m pytest tests -q
    129 passed in 1.68s

## Context and Orientation

OneCode 是 Python code agent runtime。主循环在 `core/loop.py`，只负责编排用户消息、上下文重建、模型调用、工具调用和结果回填。新增 BashTool 不能修改主循环，也不能在主循环里硬编码工具名。

工具 runtime 在 `services/tools/`。`services/tools/types.py` 定义 `ToolDescriptor`、`ToolCallClassification`、`ToolTarget`、`ToolResultPolicy`、`ToolRuntime` 和 `ToolExecutionResult`。具体工具放在 `tools/<tool_name>/`，从 `tool.py` 导出 `descriptor()`，从 `prompt.py` 导出模型可见使用说明。`ToolDescriptor.classify_input()` 必须根据本次输入返回 read-only、是否修改文件系统、是否可并发、targets、result policy 和 permission subject。

`services/tools/executor.py` 的 `RegistryToolExecutor` 是工具执行入口。它查找 descriptor、做 JSON Schema 形状校验、运行工具级 validator、分类输入、检查 guard targets、调用 permission policy、运行 hooks、调用 handler、应用 result policy、触发 PostToolUse 或 ToolError。Hook 更新输入后，executor 会重新执行 schema validation、tool validation、classification、guard 和 permission policy。BashTool 必须服从这个顺序。

路径 guard 在 `services/guard/`。`SandboxGuard.check_path()` 根据 `SandboxBoundary` 返回 `GuardPolicy(action="allow"|"ask"|"deny")`。denied pattern 优先，外部目录 ask，workspace/worktree/extra allowed 默认 allow。当前 executor 只对 `ToolTarget.kind` 为 `file` 或 `directory` 的 target 运行 guard；因此 BashTool 的 classifier 必须从命令 AST 中派生出具体文件系统 targets，例如 `cat foo.txt` 派生 `file/read:foo.txt`，`mkdir out` 派生 `directory/write:out`，`echo ok > a.txt` 派生 `file/write:a.txt`。

权限机制在 `services/permissions/`。`PermissionPolicy.evaluate()` 消费 tool call、descriptor、classification、guard policies 和 runtime state。它执行 deny-first 策略，能把 ask 交给 `PermissionPrompter`。CLI 中的 prompter 位于 `ui/cli/permissions.py`，当前已为 `read_file`、`edit_file`、`glob`、`grep` 和 fallback tool 提供面板。BashTool 应复用 fallback，或在本计划中增加 bash 专属面板。

CLI 装配在 `ui/cli/app.py`。当前 `build_runtime()` 注册 `read_file`、`edit_file`、`glob`、`grep`，创建 `PermissionPolicy`、`CliPermissionPrompter`、`SandboxGuard` 和 `RegistryToolExecutor`。实现 BashTool 后，需要在这里注册 `bash_descriptor()`，让 `DynamicPromptAssembler` 和 provider schema 都能看见该工具。

参考 BashTool 在 `docs/references/Tools_full/BashTool/`。最重要的参考文件是 `ast.ts`、`pathValidation.ts`、`readOnlyValidation.ts`、`commandSemantics.ts`、`bashCommandHelpers.ts` 和 `prompt.ts`。本计划不照搬 React UI、background task、sandbox adapter、analytics、regex `bashSecurity.ts` 或 TypeScript permission rule 系统。参考 `ast.ts` 的结构即可：Tree-sitter parse 后返回 `simple`、`too-complex` 或 `parse-unavailable`；simple 结果包含 `SimpleCommand(argv, envVars, redirects, text)`；`checkSemantics()` 在 argv 层拦截 eval-like builtins、shell keywords、无法静态确定 wrapper command 等。

## Plan of Work

第一阶段是新增依赖和最小 parser spike。编辑 `pyproject.toml`，加入 runtime dependencies `tree-sitter` 和 `tree-sitter-bash`。运行 `uv sync --dev` 更新 `uv.lock`。新增 `tests/test_bash_parser.py`，先写一个小测试证明可以解析 `git status && rg foo . | head -20`，并能拿到 root node。这个阶段只验证依赖和 Windows 环境安装，不实现工具逻辑。如果 `uv sync --dev` 因网络受限失败，按开发者权限说明请求网络/安装 approval。

第二阶段是建立 `tools/bash/` 目录。创建 `tools/bash/__init__.py`、`tools/bash/prompt.py`、`tools/bash/ast_model.py`、`tools/bash/parser.py`、`tools/bash/semantics.py`、`tools/bash/readonly.py`、`tools/bash/paths.py`、`tools/bash/runner.py`、`tools/bash/tool.py`。`__init__.py` 导出 `descriptor`。`prompt.py` 描述 BashTool 的用途、Git Bash 依赖、路径和权限规则、只读自动放行、避免用 Bash 代替专用文件工具的建议，以及不支持复杂 shell 结构的约束。不要把参考 prompt 中的 background task、dangerouslyDisableSandbox、GitHub workflow 大段说明全量移植。

第三阶段实现 AST 模型。`tools/bash/ast_model.py` 定义 Python dataclass：

    @dataclass(frozen=True)
    class Redirect:
        op: Literal[">", ">>", "<", "<<", ">&", ">|", "<&", "&>", "&>>", "<<<"]
        target: str
        fd: int | None = None

    @dataclass(frozen=True)
    class SimpleCommand:
        argv: tuple[str, ...]
        env_vars: tuple[EnvVar, ...]
        redirects: tuple[Redirect, ...]
        text: str

    @dataclass(frozen=True)
    class BashAnalysis:
        commands: tuple[SimpleCommand, ...]
        operators: tuple[str, ...]
        has_pipeline: bool
        has_cd: bool

    @dataclass(frozen=True)
    class BashParseError:
        kind: Literal["parse_unavailable", "too_complex"]
        reason: str
        node_type: str | None = None

`operators` 记录顶层 `&&`、`||`、`;`、`|`，用于 command splitting、permission subject 和退出码解释。不要把 raw Tree-sitter node 暴露给工具 handler 或 tests；handler 只消费 OneCode 自己的稳定模型。

第四阶段实现 parser。`tools/bash/parser.py` 封装 Tree-sitter。建议接口：

    def parse_bash(command: str) -> BashAnalysis | BashParseError: ...

内部创建 Tree-sitter `Parser` 并设置 Bash language。实现时要按当前 `tree-sitter` Python API 写兼容代码；`tree_sitter_bash.language()` 通常返回 language capsule，需要包装成 `Language(...)` 后传给 parser。解析前做少量 AST 差异预检，但这些预检不是 regex 安全验证：只拦截控制字符、Unicode whitespace、backslash escaped whitespace 这类 Tree-sitter 与 Bash tokenization 会不同步的输入。空 command 返回 empty analysis，由 validator 再拒绝或执行 no-op 取决于 input schema。

第五阶段实现 AST walker。walker 应使用 node type allowlist，fail closed。允许的结构节点包括 `program`、`list`、`pipeline`、`redirected_statement` 和 `command`。允许的 separator token 包括 `&&`、`||`、`;`、`|` 和 newline。第一版遇到 `subshell`、`compound_statement`、`for_statement`、`while_statement`、`until_statement`、`if_statement`、`case_statement`、`function_definition`、`command_substitution`、`process_substitution`、`expansion`、`simple_expansion`、`brace_expression`、`heredoc_redirect`、`herestring_redirect` 或 Tree-sitter `ERROR` 时返回 `too_complex`。如果 Tree-sitter grammar 的实际 node type 名称与参考 TS 不一致，以测试固定 Python 包的实际输出，并在 `Surprises & Discoveries` 记录。

第六阶段实现 argv 和 redirect 提取。`SimpleCommand.argv` 必须是 quote-resolved 后的 argv。第一版应支持普通 word、single quoted string、double quoted string 中无 expansion 的 literal、concatenated string literal，以及常见 escaped character。若某个 argv 元素包含 runtime expansion，返回 `too_complex`。`Redirect.target` 必须来自 AST，不通过字符串搜索。output redirects `>`、`>>`、`>|`、`&>`、`&>>`、`>&file` 需要作为 write target；fd duplication 例如 `2>&1` 不派生文件 path；input redirects `<` 作为 read target；heredoc 和 herestring 第一版 too-complex。

第七阶段实现 `check_semantics()`。`tools/bash/semantics.py` 定义：

    @dataclass(frozen=True)
    class SemanticResult:
        ok: bool
        reason: str | None = None

    def check_semantics(analysis: BashAnalysis) -> SemanticResult: ...

语义检查消费 `SimpleCommand.argv`，不是 raw command string。它应至少拒绝空 argv[0]、argv[0] 以 `-`、`|`、`&` 开头、shell keyword 作为 command name、`eval`、`source`、`.`、`exec`、`alias` 写别名、`trap`、`enable`、`fc` 执行模式、`command` 非 `-v`/`-V` 模式、`bash -c`、`sh -c`、`zsh -c`、`python -c` 等显式 code execution 形态的自动放行。注意：语义检查失败不一定意味着永远不能执行；它意味着不能自动分类为安全/只读。如果已有 permission policy 能询问用户，BashTool 可以返回 ask；如果无法生成可信路径 targets，则应 fail closed，不执行。

第八阶段实现 wrapper stripping。参考 `ast.ts` 和 `pathValidation.ts`，在 argv 层支持 `time`、`nohup`、`timeout`、`nice`、`env`、`stdbuf` 的小集合。wrapper stripping 必须在 `check_semantics()`、只读分类和路径提取中使用同一 helper，例如 `strip_safe_wrappers(argv) -> tuple[str, ...] | WrapperError`。如果 wrapper flags 无法静态判断 wrapped command 位置，返回错误并 fail closed。不要让 `timeout -k X 10 eval ...` 这类命令因为 argv[0] 是 `timeout` 而跳过对 wrapped command 的检查。

第九阶段实现只读判断。`tools/bash/readonly.py` 定义：

    def classify_readonly(analysis: BashAnalysis) -> ReadonlyResult: ...

它只能在 `check_semantics()` ok 后运行。只读自动 allow 的命令集合第一版保持小而明确，包括 `pwd`、`whoami`、`git status`、`git diff`、`git log`、`git show`、`git branch`、`git remote`、`git rev-parse`、`git ls-files`、`ls`、`tree`、`find` 不含写入/exec flags、`cat`、`head`、`tail`、`wc`、`stat`、`file`、`diff`、`grep`、`rg`、`sort`、`uniq`、`cut`、`sed` 只读打印模式、`jq` 不含 `system`/file-loading危险 flags、`python --version`、`python3 --version`、`node --version`、`node -v`。这不是 regex 安全验证；实现应基于 argv token 和 per-command flag parser。遇到未知 command、未知 flag、写入 redirect 或 write operation，则 `read_only=False`。

第十阶段实现路径提取。`tools/bash/paths.py` 维护 command -> operation -> path extractor。第一版支持 `cd`、`ls`、`find`、`cat`、`head`、`tail`、`wc`、`stat`、`file`、`diff`、`grep`、`rg`、`sed`、`jq`、`mkdir`、`touch`、`rm`、`rmdir`、`mv`、`cp`。读类命令派生 `ToolTarget(kind="file"|"directory", operation="read"|"list")`；创建和写入派生 `operation="write"`；删除派生 `operation="delete"`。`create` 统一映射到现有 guard 的 `write`。输出 redirect 派生 `file/write`，输入 redirect 派生 `file/read`。如果某个 path 参数缺失且命令默认读当前目录，例如 `ls`、`find`、`rg pattern`，派生 `directory/list` 或 `directory/read` 的 `.` target。

第十一阶段实现 command splitting 语义。BashTool classifier 不应该把 `cmd1 && cmd2` 当作一个 opaque string。parser 输出的 `SimpleCommand` 列表就是拆分结果。分类规则：所有 simple command 只读且所有 redirects 非写入时，整体 `read_only=True`、`modifies_filesystem=False`、`concurrency_safe=True`；任一命令写入、删除或未知副作用时，整体 `read_only=False`、`modifies_filesystem=True`、`concurrency_safe=False`。pipeline 中每个 segment 都要单独检查；`echo foo | grep foo` 可以只读，`echo foo | tee file.txt` 是写入，因为 `tee` 写文件。顶层 `cd` 和后续写入组合要保守：第一版如果 compound command 中出现 `cd` 且还有非读 operation 或 output redirect，返回 ask 或 too-complex，因为相对路径最终 cwd 难以静态确定。

第十二阶段实现执行后退出码语义。`tools/bash/semantics.py` 或 `tools/bash/runner.py` 定义：

    def interpret_exit(command_name: str, exit_code: int, stdout: str, stderr: str) -> ExitInterpretation: ...

默认非 0 是 error。`grep` 和 `rg` 的 exit 1 表示 no matches，不是工具错误；exit >=2 是 error。`find` exit 1 可视为 partial success，exit >=2 是 error。`diff` exit 1 表示 files differ，不是工具错误；exit >=2 是 error。`test` 和 `[` exit 1 表示 condition false，不是工具错误；exit >=2 是 error。对于 compound command，以最后一个 simple command 的 effective command name 解释退出码，和参考 `commandSemantics.ts` 一致。

第十三阶段实现 Git Bash runner。`tools/bash/runner.py` 定义 `BashRunner` protocol 和 `GitBashRunner`。runner 查找 Git Bash 顺序：显式环境/配置如未来存在则优先；PATH 上 `bash.exe`；常见 Windows Git 安装路径。实现时不要用 shell=True。执行命令建议使用：

    [bash_exe, "--noprofile", "--norc", "-lc", command]

并设置 `cwd=runtime.guard.boundary.cwd`。如果用户要求加载 profile，可后续单独设计；第一版使用 no-profile/no-rc 减少环境差异。`timeout_ms` 默认 120000，最大 600000。捕获 stdout/stderr，text mode 使用 UTF-8 并替换 decode errors。找不到 Git Bash 返回结构化 `git_bash_not_found`，不要抛出 uncaught exception。

第十四阶段实现 `tools/bash/tool.py` descriptor。输入模型用 Pydantic v2：

    class BashInput(BaseModel):
        command: str
        timeout_ms: int | None = None
        description: str | None = None

禁止额外字段；`command` trim 后不能为空；`timeout_ms` 必须在 1 到 600000 之间。descriptor name 是 `bash`，description 是 `Execute a Git Bash command with AST-based classification and sandbox-aware permissions.`，search hint 是 `execute git bash commands`，result policy 为 `ToolResultPolicy(max_result_size_chars=30_000, persist_when_exceeded=False, preview_chars=4_000)`。`classify_input()` 解析命令、运行 `check_semantics()`、只读判断和路径提取。parse/semantic failure 必须 fail closed；可以返回 `ToolCallClassification(read_only=False, modifies_filesystem=True, concurrency_safe=False, targets=(ToolTarget(kind="command", operation="execute", value=command, metadata={"parse_error": ...}),), permission_subject=f"bash:{prefix}")`，但若无法派生路径且非只读，handler 必须在执行前确认 permission policy 已 ask/allow，否则不执行。

第十五阶段处理 unknown/non-readonly permission。因为 executor 目前只根据文件系统 guard policies 决定 ask，如果 BashTool 只返回 `command/execute` target，现有 `PermissionPolicy` 可能会 allow 未知命令。因此本计划必须扩展权限层或 BashTool classification metadata，让非只读 command target 进入 ask。推荐做法是在 `PermissionPolicy.evaluate()` 中识别 `ToolTarget(kind="command", operation="execute")` 且 `classification.read_only is False`，返回 ask，原因是 `Command may modify system state or has unknown side effects.`。这样未知命令、写入命令和语义复杂命令都会触发 CLI prompter。只读命令仍自动 allow，但其派生的 file/directory targets 仍经过 guard。

第十六阶段实现 handler。handler 再次解析输入，确保 parse/classification 结果和 executor 阶段一致；为避免重复复杂计算，可以在分类模块里提供 deterministic helper，不依赖 mutable cache。handler 在执行前可检查 `runtime.guard` 存在。文件系统路径由 executor guard/permission 处理；handler 内如需重复 guard，应使用 `is_guard_policy_allowed()`。然后调用 `GitBashRunner.run()`，得到 stdout、stderr、exit_code、duration。用 `interpret_exit()` 判断 `is_error`。返回 content 时包含命令、exit code、stdout、stderr 的简洁文本；metadata 包含 `exit_code`、`duration_ms`、`timed_out`、`read_only`、`command_count`、`semantic_message`、`stdout_chars`、`stderr_chars`。

第十七阶段新增 Bash CLI 权限面板。扩展 `ui/cli/permissions.py` 的 `render_permission_panel()`，为 `tool_name == "bash"` 增加 `_bash_panel()`。面板显示标题 `Bash command permission requested`、reason、command 或 description、read_only flag、派生 target 列表、timeout 和 session allow 说明。`s` 会话允许对 bash 命令要谨慎：第一版可以只实现 allow once；如果复用现有 `allow_session_directory`，仅当 request 中有 guard policies 时授予目录权限，不授予命令 prefix 权限。不要在第一版实现持久 Bash prefix allow 规则。

第十八阶段接入 CLI registry。编辑 `tools/bash/__init__.py` 导出 descriptor，编辑 `ui/cli/app.py` import `tools.bash.descriptor as bash_descriptor`，并在 `ToolRegistry([...])` 中加入 `bash_descriptor()`。确认 `/tools`、dynamic prompt、provider schema 都能看到 `bash`，除非 permission policy 工具级 deny/disabled。不要在 `core/loop.py` 中添加任何 BashTool 特例。

第十九阶段补充测试。新增 `tests/test_bash_parser.py` 覆盖 parse simple command、quotes、redirect、pipeline、`&&`、`;`、too-complex structures、unsupported expansion、Tree-sitter ERROR。新增 `tests/test_bash_semantics.py` 覆盖 eval/source/exec/shell keyword/wrapper stripping/exit interpretation。新增 `tests/test_bash_tool.py` 覆盖 descriptor schema、validator、classification、read-only auto allow、file targets、redirect write targets、unknown command asks、guard deny stops execution、Git Bash not found structured error、fake runner success/error/no-match semantics。新增 CLI permission panel 测试，覆盖 bash panel 文本和 interrupted prompt deny。更新 runtime integration 或 CLI tests，证明 `bash` 出现在 registry schema 和 prompt sections。

第二十阶段更新文档。编辑 `architecture.md`，把 `tools/bash/` 从目标未实现改为已实现，并说明它使用 Tree-sitter AST、check semantics、路径目标派生、Git Bash runner 和 permission policy。编辑 `docs/design-docs/tool-design-guidelines.md` 如需补充 BashTool 分类约定。编辑 `docs/tech-debt/tech-debt-tracker.md`，记录剩余限制：没有 PowerShell/cmd/WSL runner，没有 background tasks，没有 durable result store，没有持久 Bash prefix allow rules，没有完整 shell 语言支持，Tree-sitter parser 包版本和 Windows wheel 需要维护。

## Concrete Steps

所有命令都在仓库根目录执行：

    cd D:\study\OneCode

开始前检查工作区，不覆盖用户已有变更：

    git status --short

实现前确认 Git Bash 是否可用：

    Get-Command bash.exe -ErrorAction SilentlyContinue

如果该命令找不到 `bash.exe`，也检查常见路径：

    Get-ChildItem "C:\Program Files\Git\bin\bash.exe" -ErrorAction SilentlyContinue
    Get-ChildItem "C:\Program Files\Git\usr\bin\bash.exe" -ErrorAction SilentlyContinue

新增依赖并同步环境：

    uv sync --dev

如果需要先编辑依赖，`pyproject.toml` 中 runtime dependencies 应包含：

    tree-sitter
    tree-sitter-bash

新增工具文件：

    tools/bash/__init__.py
    tools/bash/prompt.py
    tools/bash/ast_model.py
    tools/bash/parser.py
    tools/bash/semantics.py
    tools/bash/readonly.py
    tools/bash/paths.py
    tools/bash/runner.py
    tools/bash/tool.py

更新运行时和 CLI：

    services/permissions/policy.py
    ui/cli/permissions.py
    ui/cli/app.py

新增和更新测试：

    tests/test_bash_parser.py
    tests/test_bash_semantics.py
    tests/test_bash_tool.py
    tests/test_cli_permissions.py
    tests/test_runtime_integration.py

focused tests：

    uv run python -m pytest tests/test_bash_parser.py tests/test_bash_semantics.py tests/test_bash_tool.py -q

权限和 CLI 相关 tests：

    uv run python -m pytest tests/test_permission_policy.py tests/test_tool_registry_and_executor.py tests/test_cli_permissions.py -q

compile check：

    uv run python -m compileall core services infrastructure tools ui prompts

全量测试：

    uv run python -m pytest tests -q

如果真实 Git Bash 在测试环境不可用，runner integration test 应 skip，但 fake runner、classification、permission 和 schema tests 必须通过。工具本身在运行时必须返回 `git_bash_not_found` 结构化错误。

## Validation and Acceptance

验收标准一：`ToolRegistry.tool_schemas(RuntimeState())` 在注册 BashTool 后包含 provider-visible `bash` schema，schema 至少包含 `command`、`timeout_ms` 和 `description`，禁止额外字段。`ToolRegistry.tool_prompt_sections(RuntimeState())` 包含 BashTool prompt。CLI `/tools` 能列出 `bash`。

验收标准二：Tree-sitter parser 能解析常见命令。`git status && rg "foo" . | head -20` 解析为多个 `SimpleCommand`，argv 分别包含 `git/status`、`rg/foo/.` 和 `head/-20`，operators 包含 `&&` 和 `|`。`echo ok > out.txt` 提取 output redirect target `out.txt`。`cat < in.txt` 提取 input redirect target `in.txt`。

验收标准三：复杂或无法静态分析的 shell 结构 fail closed。包含 command substitution、process substitution、subshell、brace group、function definition、for/while/if/case、heredoc、runtime expansion 或 Tree-sitter ERROR 的命令不得被当作只读自动执行。若没有用户允许，不执行 runner。

验收标准四：`check_semantics()` 拦截 eval-like 和 wrapper bypass。`eval "rm file"`、`source script.sh`、`. script.sh`、`exec rm file`、`bash -c "..."`、`timeout -k 5 10 eval "..."`、`env -S "rm file"` 都不能自动 allow。`command -v python` 可以按只读处理，`command python script.py` 不自动 allow。

验收标准五：只读命令自动放行但仍受路径 guard。`git status`、`git diff`、`ls .`、`cat allowed.txt`、`rg needle .` 分类为 `read_only=True`、`modifies_filesystem=False`、`concurrency_safe=True`。如果只读命令读取 sandbox 外路径，guard 返回 ask；如果读取 denied path，guard 返回 deny。

验收标准六：写入和删除命令触发 permission ask。`mkdir out`、`touch a.txt`、`rm a.txt`、`cp a b`、`mv a b`、`echo ok > a.txt`、`sed -i ... file` 分类为非只读，并派生 write/delete targets。CLI 显示 Bash 权限面板；用户允许后才执行；用户拒绝后 runner 不被调用。

验收标准七：未知副作用命令触发 permission ask。`npm install`、`python script.py`、`curl https://example.com`、`node build.js` 不能因为没有文件 target 就自动 allow。它们应包含 `command/execute` target，并由 `PermissionPolicy` 触发 ask。

验收标准八：命令拆分正确。`ls && cat a.txt` 若路径 allowed，则整体只读。`ls && touch a.txt` 整体非只读并 ask。`cd sub && touch a.txt` 因 cd 后写入相对路径而 ask，不用原始 cwd 猜测最终路径。`echo foo | grep foo` 可以只读；`echo foo | tee out.txt` 必须写入 ask。

验收标准九：执行后退出码语义正确。`grep` 或 `rg` 返回码 1 时工具结果不是 error，并给出 no matches 说明。`diff` 返回码 1 时不是 error，并说明 files differ。默认命令非 0 是 error。compound command 使用最后一个 effective command 解释退出码。

验收标准十：Git Bash 缺失时返回结构化错误。找不到 `bash.exe` 时，handler 返回 `is_error=True`，metadata error 为 `git_bash_not_found`，content 提示安装 Git for Windows 或把 Git Bash 加入 PATH，不抛出 uncaught exception。

验收标准十一：结果预算生效。stdout/stderr 合并内容超过 30KB 时，executor 的 `ToolResultPolicy` 生成截断预览和 `result_truncated` metadata。BashTool handler 不手写另一套大结果格式。

验收标准十二：以下命令最终通过：

    uv run python -m compileall core services infrastructure tools ui prompts
    uv run python -m pytest tests -q

## Idempotence and Recovery

实现应 additive-first。先新增 `tools/bash/` 和 focused tests，再接入 CLI registry，最后更新文档。不要修改 `core/loop.py`。不要删除或移动用户已有 active/completed plans。当前工作区可能已有权限机制相关改动，实施时必须只编辑与 BashTool 相关的文件，并在每次修改前阅读目标文件现状。

依赖同步可能修改 `uv.lock`，这是预期变更。若 `uv sync --dev` 因网络失败，需要按开发者权限说明请求 approval，不要手写 lockfile。

测试必须使用 fake runner 覆盖大部分行为，避免真实命令修改开发机器。涉及真实 Git Bash 的测试只运行安全只读命令，例如 `printf 'ok\n'`、`pwd` 或 `true`；写入类真实执行测试必须使用 `tmp_path` workspace，并只写临时目录。

如果 parser 遇到未预期 node type，应 fail closed，并在 `Surprises & Discoveries` 记录 node type 和触发命令。不要通过字符串 split 临时绕过 AST。

如果 command classifier 无法派生路径，但命令非只读，必须触发 command permission ask，而不是 allow。session directory allow 只能覆盖文件系统 ask，不能自动永久允许 command prefix。Bash prefix allow 规则属于后续计划。

如果 Git Bash 命令 timeout，runner 应终止进程并返回 `timed_out=True` 的结构化 error。不要留下后台进程。第一版不实现 background tasks。

## Artifacts and Notes

BashTool 输入示例：

    {
      "command": "git status --short",
      "description": "Show working tree status",
      "timeout_ms": 120000
    }

只读成功结果示例：

    command: git status --short
    exit_code: 0

    stdout:
    M architecture.md

    stderr:

Git Bash 缺失错误示例：

    {
      "error": "git_bash_not_found",
      "message": "Git Bash was not found. Install Git for Windows or add bash.exe to PATH."
    }

Bash 权限面板示例：

    Bash command permission requested
    reason: Command may modify system state or has unknown side effects.
    command: echo ok > out.txt
    operation: execute
    target: out.txt
    normalized: D:\study\OneCode\out.txt
    [y] allow once  [s] allow matching directory targets for this session  [n] deny

Parse too-complex 示例：

    {
      "error": "bash_parse_too_complex",
      "reason": "Unsupported shell structure: command_substitution"
    }

## Interfaces and Dependencies

`tools/bash/parser.py` 应提供：

    def parse_bash(command: str) -> BashAnalysis | BashParseError: ...

`tools/bash/semantics.py` 应提供：

    def check_semantics(analysis: BashAnalysis) -> SemanticResult: ...
    def interpret_exit(command_name: str, exit_code: int, stdout: str, stderr: str) -> ExitInterpretation: ...

`tools/bash/readonly.py` 应提供：

    def classify_readonly(analysis: BashAnalysis) -> ReadonlyResult: ...

`tools/bash/paths.py` 应提供：

    def targets_for_analysis(analysis: BashAnalysis) -> tuple[ToolTarget, ...]: ...

`tools/bash/runner.py` 应提供：

    @dataclass(frozen=True)
    class BashRunResult:
        exit_code: int
        stdout: str
        stderr: str
        duration_ms: int
        timed_out: bool = False

    class BashRunner(Protocol):
        def run(self, command: str, *, cwd: Path, timeout_ms: int) -> BashRunResult: ...

    class GitBashRunner:
        def run(self, command: str, *, cwd: Path, timeout_ms: int) -> BashRunResult: ...

`tools/bash/tool.py` 应提供：

    class BashInput(BaseModel): ...
    def descriptor() -> ToolDescriptor: ...

`services/permissions/policy.py` 应扩展 command target 处理：

    if target.kind == "command" and target.operation == "execute" and not classification.read_only:
        return PermissionDecision(action="ask", ...)

依赖应加入 `pyproject.toml`：

    tree-sitter
    tree-sitter-bash

根据 PyPI 当前信息，`tree-sitter-bash` 提供 Bash grammar package，`tree-sitter` 提供 Python binding 的 `Language` 和 `Parser`。实现时应在代码注释中记录具体 API 适配方式，因为 Tree-sitter Python binding 在版本间有过 API 变化。

2026-06-04 / Codex: 初始中文 ExecPlan 创建，纳入用户确认的七项决策：实现 AST `check_semantics()` 与退出码 `interpret_exit()`；参考新增 `ast.ts`；复用权限确认 UI；使用 Git Bash；不做正则安全验证；第一版只支持顶层 `&&`、`||`、`;`、`|` 并拒绝复杂结构；接入 CLI runtime。
