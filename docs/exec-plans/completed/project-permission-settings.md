# 实现项目级 Permission Rules 持久化

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本计划必须按照仓库根目录的 `PLANS.md` 维护。本文是轻量但自包含的实现计划：后续贡献者应能只阅读本文件，并结合文中点名的源码文件，完成项目级权限规则从 `.onecode/settings.json` 加载、写入和执行生效的第一版实现。


## Purpose / Big Picture

完成本计划后，OneCode 可以把用户选择的 `allow`、`deny`、`ask` 权限规则保存到项目级 `.onecode/settings.json`，并在后续 session 自动加载。用户可以把整工具规则写成 `bash` 或 `edit_file`，也可以把内容规则写成 `bash(npm run:*)`、`bash(rm -rf:*)` 这类字符串。项目级 `deny` 仍是最高优先级：它不能被 session allow、用户确认、hook 或模型旧 tool call 覆盖。

可观察行为是：项目中存在 `.onecode/settings.json` 且内容包含 `{"permissions": {"deny": ["edit_file"], "ask": ["bash"]}}` 时，`edit_file` 不进入模型可见 tool schema 或 prompt，历史 `edit_file` tool call 会在 executor 入口返回 `permission_denied`；`bash` 仍可见，但调用时会进入权限确认流程。


## Progress

- [x] (2026-06-07 16:00+08:00) 已阅读 `AGENTS.md`、`PLANS.md`、`architecture.md`、权限相关设计文档、现有 `services/permissions/` 代码和 `docs/references/s03_permission/permissions.ts`。
- [x] (2026-06-07 16:10+08:00) 已根据用户提供的参考流程确定第一版规则形态：使用 `permissions.allow`、`permissions.deny`、`permissions.ask` 字符串数组，并通过 `PermissionUpdate` 写入 settings。
- [x] (2026-06-07 16:20+08:00) 已创建本轻量 ExecPlan，尚未修改 runtime 实现代码。
- [x] (2026-06-07 17:10+08:00) 已实现权限规则 parser、serializer 和 `PermissionUpdate` 数据类型，并补充 parser 往返测试。
- [x] (2026-06-07 17:20+08:00) 已实现 `.onecode/settings.json` 的项目级权限 settings store，支持加载、幂等新增、删除、替换和坏 JSON fail-closed。
- [x] (2026-06-07 17:35+08:00) 已将项目级规则接入 `PermissionPolicy`、`ToolRegistry` 和 `RegistryToolExecutor` 的现有权限流程。
- [x] (2026-06-07 17:45+08:00) 已扩展 CLI 权限确认，Bash 面板支持写入项目级 allow 前缀规则。
- [x] (2026-06-07 18:30+08:00) 已按用户要求删除项目权限管理 CLI 入口、命令渲染和对应 command 测试；项目级规则仍通过 settings 文件和权限面板写入流程生效。
- [x] (2026-06-07 18:05+08:00) 已补充测试、设计文档和技术债状态更新。


## Surprises & Discoveries

- Observation: 当前 OneCode 已有 `PermissionPolicy.is_tool_visible()`，并且 `ToolRegistry.visible_descriptors(state)` 已经用它统一裁剪 schema 和 prompt。
  Evidence: `services/tools/registry.py` 中 `visible_descriptors()` 同时服务 `tool_schemas()` 和 `tool_prompt_sections()`。

- Observation: 当前 session 授权只存在 `SessionPermissionStore` 中，并且 `/clear`、`/resume` 会清理它。
  Evidence: `ui/cli/types.py::CliRuntime.with_session()` 调用 `self.permission_store.clear()`。

- Observation: 参考实现不是直接保存结构化对象，而是把 `{toolName, ruleContent}` 序列化为字符串，例如 `Bash(npm run:*)`，再追加到 settings 的 `permissions.allow[]`、`permissions.deny[]` 或 `permissions.ask[]`。
  Evidence: 用户提供的粘贴文本描述 `permissionRuleValueToString()` 和 `addPermissionRulesToSettings()` 流程。

- Observation: 第一版 guard policy 不保存 workspace boundary，因此项目规则匹配使用工具 target 的原始值、`normalized_value` 和 guard policy 的原始/规范化路径；当模型输入本身是相对路径时，workspace-relative 规则可以命中。
  Evidence: `services/guard/boundary.py` 的 `InsideWorkspace`/`ExternalDirectory` 等决策对象只保存 path，不保存 `SandboxBoundary`。

- Observation: 计划中的 `bash(npm run:*)` 和 `bash(rm -rf:*)` 需要按命令前缀匹配，而不是把冒号当作普通 shell 字符。
  Evidence: `tests/test_permission_policy.py::test_project_bash_content_rules_are_deny_first` 验证 `npm run test` 命中 `npm run:*`，`rm -rf build` 命中 `rm -rf:*`。


## Decision Log

- Decision: 第一版 `.onecode/settings.json` 使用 `permissions.allow`、`permissions.deny`、`permissions.ask` 三个字符串数组。
  Rationale: 这与用户提供的参考流程一致，也比结构化 JSON 对象更接近成熟权限规则格式，便于手写和 diff。
  Date/Author: 2026-06-07 / Codex

- Decision: 新增 `PermissionUpdate` 作为写入 settings 的统一入口，而不是让 CLI 直接拼 JSON。
  Rationale: 权限弹窗、slash command 和未来 hook 都可以生成同一种 update，再由 settings store 负责序列化、去重和写入。
  Date/Author: 2026-06-07 / Codex

- Decision: 整工具 deny 影响模型可见工具；内容规则只在执行入口判断。
  Rationale: `deny: ["edit_file"]` 能在模型调用前隐藏整个工具，但 `deny: ["bash(rm -rf:*)"]` 不能隐藏 `bash`，因为是否命中取决于本次命令输入。
  Date/Author: 2026-06-07 / Codex

- Decision: 项目级规则保存在 `.onecode/settings.json`，`/clear` 和 `/resume` 不清理它。
  Rationale: 项目级规则是跨 session 的项目配置，和 session 临时授权的生命周期不同。
  Date/Author: 2026-06-07 / Codex

- Decision: `PermissionPolicy` 每次从 `ProjectPermissionSettingsStore.load_rules()` 读取项目规则，而不是引入独立缓存和刷新 API。
  Rationale: 第一版规则文件很小，直接读取让权限面板写入和手工编辑 settings 后的后续判断自然生效，避免新增缓存失效路径。
  Date/Author: 2026-06-07 / Codex

- Decision: `prefix:*` 内容规则除普通 `fnmatch` 外，还按命令前缀匹配 `prefix` 或 `prefix ` 开头的值。
  Rationale: 计划的示例 `bash(npm run:*)` 期望匹配 `npm run test`，而字面 `fnmatch` 不会把冒号解释成空格分隔符。
  Date/Author: 2026-06-07 / Codex


## Outcomes & Retrospective

已完成第一版项目级权限规则持久化。`services/permissions/rules.py` 提供规则 parser/serializer；`services/permissions/project_settings.py` 读写 `.onecode/settings.json`；`PermissionPolicy` 现在合并项目整工具 deny、内容 deny、ask 和 allow；`ToolRegistry.visible_descriptors(state)` 通过 policy 自动隐藏项目级整工具 deny；Bash 权限面板可写入项目级 allow 前缀规则。按用户要求，本计划不再新增项目权限管理 CLI 入口。

已运行并通过以下验证：

    uv run python -m pytest tests/test_permission_rule_parser.py tests/test_project_permission_settings.py tests/test_permission_policy.py tests/test_cli_permissions.py tests/test_cli_commands.py -q
    33 passed in 2.74s

    uv run python -m pytest tests/test_tool_registry_and_executor.py tests/test_runtime_integration.py tests/test_bash_tool.py -q
    25 passed in 1.28s

最终全量验证也已通过：

    uv run python -m compileall core services infrastructure tools ui
    Compiling 'ui\\cli\\app.py'...

    uv run python -m pytest tests -q
    232 passed in 2.45s

遗留范围：第一版没有实现用户级、组织级、CLI flag、更多 rule source 或原子写 settings；guard policy 也没有携带 workspace boundary，因此绝对路径转 workspace-relative 的匹配仍可在后续路径策略增强中完善。

Plan update note, 2026-06-07 / Codex: recorded final implementation outcome and validation evidence so the living plan reflects the completed first version.


## Context and Orientation

OneCode 的权限系统目前集中在 `services/permissions/`。`services/permissions/types.py` 定义 provider-neutral 的 `PermissionDecision`、`PermissionRequest`、`PermissionResponse` 和 `PermissionOption`。`services/permissions/session.py` 定义 `SessionPermissionStore`，它只保存当前 session 的临时目录授权和工具 deny/disabled，不写磁盘。`services/permissions/policy.py` 定义 `PermissionPolicy`，它把工具级规则、guard 结果、危险目录、可疑路径、session allow 和用户确认合并为最终决策。

工具可见性由 `services/tools/registry.py` 处理。`ToolRegistry.visible_descriptors(state)` 会读取 `PermissionPolicy.is_tool_visible()`；被整工具 deny 或 disabled 的工具不会进入 `tool_schemas(state)`，也不会进入 `tool_prompt_sections(state)`。因此项目级整工具 deny 应接入 `PermissionPolicy`，而不是在 prompt assembler 或 CLI 中另写裁剪逻辑。

工具执行入口是 `services/tools/executor.py::RegistryToolExecutor`。它在 handler 运行前会验证输入、分类工具调用、收集 guard policies、调用 `PermissionPolicy.evaluate()`，并在需要 ask 时调用 `PermissionPrompter`。项目级内容规则必须在这个执行入口基于本次 `ToolCallClassification.targets` 判断，不能只靠模型 prompt。

CLI 装配在 `ui/cli/app.py::build_runtime()`。当前这里创建 `SessionPermissionStore`、`PermissionPolicy`、`ToolRegistry`、`RegistryToolExecutor` 和 `CliPermissionPrompter`。项目级 settings store 也应在这里创建，并注入同一个 `PermissionPolicy`。


## Plan of Work

第一步，新增规则模型和 parser。创建 `services/permissions/rules.py`，定义 `PermissionBehavior = Literal["allow", "deny", "ask"]`、`PermissionRuleValue`、`PermissionRule` 和 `PermissionUpdate`。实现 `permission_rule_value_from_string(raw)` 和 `permission_rule_value_to_string(value)`。字符串格式为 `<tool_name>` 或 `<tool_name>(<rule_content>)`。括号内容中的反斜杠、左括号和右括号必须支持转义，并且 parse 后再 serialize 应能 round-trip。

第二步，新增项目 settings store。创建 `services/permissions/project_settings.py`，定义 `ProjectPermissionSettingsStore`。它读取 `<workspace>/.onecode/settings.json`，校验 `permissions.allow`、`permissions.deny`、`permissions.ask` 是字符串数组，返回带 `source="project_settings"` 的 `PermissionRule`。`apply_update(update)` 支持 `addRules`、`removeRules` 和 `replaceRules`。写入时保留其他 settings 字段，使用 2 空格缩进和末尾换行。JSON 损坏时不能覆盖原文件，应抛出清晰异常。

第三步，扩展 `PermissionPolicy`。让 `PermissionPolicy.__init__()` 接收可选 `project_store` 或可缓存的 project rules provider。`is_tool_denied()` 应合并项目整工具 deny、session deny 和 runtime metadata denied tools。新增项目 ask/allow 判断：整工具 ask 直接让 `evaluate()` 返回 ask；内容规则在 descriptor name 匹配后，基于 `classification.targets` 判断是否匹配。deny 的顺序必须早于 ask 和 allow。session allow 仍只能覆盖 ask，不能覆盖任何 deny。

第四步，设计第一版内容规则匹配。`bash(...)` 优先匹配 `ToolTarget(kind="command", operation="execute", value=<command>)`。文件类内容规则匹配 target 原始路径、normalized path 字符串，以及 workspace-relative 形式。通配符使用 `fnmatch` 风格，不拼接未转义正则。内容规则只影响执行入口，不参与 `ToolRegistry.visible_descriptors()` 的整工具裁剪。

第五步，扩展 CLI 权限确认。给 `PermissionResponse` 增加 `permission_updates: tuple[PermissionUpdate, ...] = ()`，或者在 metadata 中先承载同等结构，但推荐显式字段。`CliPermissionPrompter` 的 Bash 面板可增加“allow and don't ask again for prefix”路径，构造 `PermissionUpdate(type="addRules", rules=(PermissionRuleValue(tool_name="bash", rule_content="<prefix>"),), behavior="allow", destination="projectSettings")`。`RegistryToolExecutor._evaluate_permission()` 在用户 allow 后继续调用 `permission_policy.record_response(request, response)`，由 policy 或 project store 写入 settings。

第六步，更新装配和文档。编辑 `ui/cli/app.py::build_runtime()`，创建 `ProjectPermissionSettingsStore(workspace / ".onecode" / "settings.json")` 并注入 `PermissionPolicy`。编辑 `services/permissions/__init__.py` 导出新增类型。更新 `docs/design-docs/safety-and-extension-architecture.md` 和 `docs/tech-debt/tech-debt-tracker.md`，说明 TD-008 的项目级规则部分被缓解，但用户级、组织级、完整路径策略和更多 rule source 仍可作为后续范围。


## Concrete Steps

在仓库根目录 `D:\study\OneCode` 中开发。先实现和测试规则 parser：

    uv run python -m pytest tests/test_permission_rule_parser.py -q

再实现 settings store：

    uv run python -m pytest tests/test_project_permission_settings.py -q

接着接入 policy、registry 和 executor：

    uv run python -m pytest tests/test_permission_policy.py tests/test_tool_registry_and_executor.py -q

最后接入 CLI：

    uv run python -m pytest tests/test_cli_permissions.py tests/test_cli_commands.py -q

完成后运行编译检查和全量测试：

    uv run python -m compileall core services infrastructure tools ui
    uv run python -m pytest tests -q

实现者必须把最终命令输出摘要记录到本计划的 `Outcomes & Retrospective`。


## Validation and Acceptance

第一，settings 加载可验证。在临时 workspace 创建 `.onecode/settings.json`，内容为 `{"permissions": {"deny": ["edit_file"], "ask": ["bash"]}}`。构建 runtime 后，`edit_file` 不出现在 `registry.tool_schemas(state)` 或 `registry.tool_prompt_sections(state)`；手工执行旧 `edit_file` tool call 返回 `permission_denied`；`bash` 仍出现在 schema 中，但非只读命令触发 ask。

第二，内容规则可验证。配置 `{"permissions": {"deny": ["bash(rm -rf:*)"], "allow": ["bash(npm run:*)"]}}` 后，`bash` 工具仍可见。执行 `npm run test` 命中 allow 并跳过项目级 ask；执行 `rm -rf build` 命中 deny 并返回 `permission_denied`。如果同一调用同时命中 allow 和 deny，deny 胜出。

第三，写入流程可验证。用户通过 Bash 权限面板添加规则后，`.onecode/settings.json` 被创建或更新为包含 `permissions.allow` 数组。重复添加同一规则不会产生重复字符串。删除规则后重新加载 policy，不应保留旧规则。

第四，生命周期可验证。`/clear` 和 `/resume` 后，session allow 被清空，但 `.onecode/settings.json` 中的项目规则仍生效。删除 settings 中的 deny 规则并刷新后，该工具应重新进入 schema/prompt。

第五，错误处理可验证。如果 `.onecode/settings.json` 是非法 JSON，CLI 启动或 reload 应显示明确错误，且不得覆盖原文件。如果 `permissions.allow` 不是字符串数组，应提示配置格式错误。


## Idempotence and Recovery

`addRules` 必须幂等：重复添加同一条规则不会重复写入。去重应基于 parse 后再 serialize 的规范化字符串，而不是原始字符串直接比较。

写 settings 前应先完整读取和解析当前文件。解析失败时不要写入，避免把用户手写但暂时有语法错误的配置覆盖掉。

写入 `.onecode/settings.json` 只修改 `permissions.<behavior>` 对应数组，并保留其他 settings 字段。实现可先直接写完整 JSON 文件；如后续需要更强崩溃恢复，再引入临时文件和原子替换。


## Artifacts and Notes

目标 settings 示例：

    {
      "permissions": {
        "allow": [
          "bash(npm run:*)"
        ],
        "deny": [
          "bash(rm -rf:*)"
        ],
        "ask": [
          "bash",
          "edit_file"
        ]
      }
    }

权限弹窗持久化流程应保持为：

    用户选择 don't ask again
      -> CLI 构造 PermissionUpdate
      -> permission_rule_value_to_string()
      -> ProjectPermissionSettingsStore.apply_update()
      -> 去重合并 permissions.allow/deny/ask
      -> 写入 .onecode/settings.json
      -> 刷新 PermissionPolicy 内存规则


## Interfaces and Dependencies

在 `services/permissions/rules.py` 中定义：

    PermissionBehavior = Literal["allow", "deny", "ask"]
    PermissionUpdateType = Literal["addRules", "removeRules", "replaceRules"]
    PermissionUpdateDestination = Literal["projectSettings", "session"]

    @dataclass(frozen=True)
    class PermissionRuleValue:
        tool_name: str
        rule_content: str | None = None

    @dataclass(frozen=True)
    class PermissionRule:
        source: str
        behavior: PermissionBehavior
        value: PermissionRuleValue

    @dataclass(frozen=True)
    class PermissionUpdate:
        type: PermissionUpdateType
        rules: tuple[PermissionRuleValue, ...]
        behavior: PermissionBehavior
        destination: PermissionUpdateDestination

在 `services/permissions/project_settings.py` 中定义：

    class ProjectPermissionSettingsStore:
        def __init__(self, settings_path: Path) -> None: ...
        def load_rules(self) -> tuple[PermissionRule, ...]: ...
        def apply_update(self, update: PermissionUpdate) -> None: ...

在 `services/permissions/policy.py` 中扩展：

    class PermissionPolicy:
        def __init__(
            self,
            session_store: SessionPermissionStore | None = None,
            *,
            project_store: ProjectPermissionSettingsStore | None = None,
            protected_project_dirs: tuple[str, ...] = PROTECTED_PROJECT_DIRS,
        ) -> None: ...

在 `services/permissions/types.py` 中扩展 `PermissionResponse`：

    permission_updates: tuple[PermissionUpdate, ...] = ()

如果为了避免类型循环，可以把 `PermissionUpdate` import 放在 `TYPE_CHECKING` 分支，运行时使用 `from __future__ import annotations`。
