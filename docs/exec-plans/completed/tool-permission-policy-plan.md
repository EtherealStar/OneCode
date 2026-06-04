# 实现结构化工具许可机制

本 ExecPlan 是一个活文档。实现过程中必须持续维护 `Progress`、`Surprises & Discoveries`、`Decision Log` 和 `Outcomes & Retrospective`。本计划遵守仓库根目录的 `PLANS.md`，并把必要背景写入本文，使后续执行者只阅读本文和当前工作区也能完成实现。

## Purpose / Big Picture

完成本改动后，OneCode 的工具执行入口会在真正调用 handler 前执行统一许可判断。用户可以在项目根目录的 `onecode.permissions.json` 中声明基于结构化 `ToolTarget` 的 `allow`、`ask` 和 `deny` 规则；deny 永远优先，ask 在 `ask_user` 工具尚未接入前返回结构化 tool error 并阻止执行，allow 可以让符合规则的请求继续执行。外部 sandbox 路径这类需要用户确认的请求被未来 `ask_user` 允许后，应写入当前 session 的临时 allow 规则，让同一 session 内相同结构化目标不再重复询问。

用户能通过测试看到三个核心行为：命中项目 deny 的工具调用不会执行；命中项目 ask 的工具调用在没有 `ask_user` 时返回 `permission_ask_required`；把一条 session 临时 allow 写入 `RuntimeState.metadata` 后，相同结构化 target 的 ask 请求可以继续执行。模型可见工具 schema 也会按 blanket deny 规则裁剪，避免被明确禁用的工具继续暴露给模型。

## Progress

- [x] (2026-06-04 10:15Z) 讨论并确认产品决策：ask_user 尚未实现时返回错误；权限规则来自项目配置文件；blanket deny 应裁剪 schema；用户允许后写入 session 临时规则；规则只支持结构化 target，不支持字符串 subject 兼容层；不更新已完成 ExecPlan 的历史描述。
- [x] (2026-06-04 10:25Z) 在计划前清理无效工具分类字段，移除当前代码和前瞻设计文档中的 `ToolCallClassification.destructive`。
- [ ] 实现 `services/permissions/` 的类型、项目配置加载和规则匹配。
- [ ] 将权限策略接入 `ToolRegistry.tool_schemas()`，让 blanket deny 的工具不进入模型可见 schema。
- [ ] 将权限策略接入 `RegistryToolExecutor`，在 guard 和 hook 之间执行 deny/ask/allow 判断，并在 hook 更新输入后重复检查。
- [ ] 增加 session 临时 allow 规则写入和匹配能力，先提供 service 方法和测试入口，未来由 `ask_user` 调用。
- [ ] 更新工具设计指南、架构文档和技术债，记录结构化 target 权限机制。
- [ ] 运行 focused tests、compile check 和全量测试。

## Surprises & Discoveries

- Observation: 当前 executor 已经在 `PreToolUse` hook 前执行 JSON Schema 校验、工具校验、input-aware classification 和 sandbox guard，并且 hook 修改输入后会重新执行这些步骤。
  Evidence: `services/tools/executor.py` 的 `_execute_one()` 先调用 `_prepare_input()`，hook updated input 后再次调用 `_prepare_input()`。权限机制应插入同一准备阶段，避免 hook 改写绕过许可检查。

- Observation: sandbox guard 已经把外部目录映射为 `ask`，但当前没有用户确认路径，所以 executor 把它转换为 `path_guard_ask_required` tool error。
  Evidence: `services/guard/policy.py` 的 `SandboxGuard.check_path()` 对 `external_directory` 返回 `GuardPolicy(action="ask", ...)`，`services/tools/executor.py` 的 `_guard_error_result()` 对 ask 写入 `path_guard_ask_required`。

- Observation: `destructive` 分类字段没有参与任何执行决策。
  Evidence: 移除前只有工具 classifier 和测试断言写入该字段，`RegistryToolExecutor` 没有读取它。参考资料 `docs/references/s03_permission/README.en.md` 也指出 CC 的类似 destructive 元数据只用于 UI 展示，不参与权限判断。

## Decision Log

- Decision: 第一版 `ask` 不调用交互输入，统一返回结构化 tool error。
  Rationale: 用户明确要求在 `ask_user` 工具添加前返回错误。这样权限层可以 fail closed，同时为未来交互确认保留稳定接口。


- Decision: 权限规则来自项目配置文件，计划路径为仓库根目录 `onecode.permissions.json`。
  Rationale: 用户要求项目配置文件。该文件不是模型 provider `.env`，也不应放入已忽略的 `.onecode/` 会话产物目录；根目录 JSON 文件更适合被项目版本管理和审查。


- Decision: 权限规则只支持结构化 `ToolTarget` 匹配，不提供 `permission_subject` 字符串兼容规则。
  Rationale: 用户明确选择结构版并禁止“两者都支持”。结构化匹配能基于 `kind`、`operation`、`value` 和规范化路径做确定性判断，避免字符串 subject 变成第二套权限语言。


- Decision: deny 优先级最高，不能被项目 allow、session allow、hook 或未来 ask_user 覆盖。
  Rationale: `AGENTS.md`、`architecture.md` 和 `docs/design-docs/core-beliefs.md` 都要求 deny-first；安全拒绝应在动态组装和执行入口同时生效。


- Decision: blanket deny 规则应裁剪模型可见 tool schema；content-specific deny/ask 不裁剪 schema，只在执行前判断。
  Rationale: 用户同意该边界。只有明确禁止整个工具的规则能在没有具体 input 的 schema 组装阶段判断；target/value 规则必须等模型给出工具输入后才能匹配。


- Decision: 用户允许一次 ask 后，写入当前 session 的临时 allow 规则。
  Rationale: 用户明确要求“写入临时规则”。临时规则属于运行时会话状态，不写回项目配置，避免把一次人工授权永久化。


- Decision: 删除 `ToolCallClassification.destructive`，不再让工具维护这个无效元数据。
  Rationale: 该字段没有执行语义，容易让权限实现误以为它可作为安全依据。权限应基于结构化 target、operation、guard 和规则。


## Outcomes & Retrospective

尚未实现。本节应在每个主要阶段完成后更新，记录哪些行为已经可运行、哪些能力仍然只在计划中。

## Context and Orientation

OneCode 是 Python code-agent runtime。当前主循环在 `core/loop.py`，它只负责接收用户 prompt、构建上下文、调用模型、执行工具和回填工具结果。主循环不应知道具体工具名、具体权限规则或项目配置格式。

工具运行时位于 `services/tools/`。`services/tools/types.py` 定义内部工具调用结构：`ToolCall` 是模型请求的一次工具调用，`ToolExecutionResult` 是返回给模型的工具结果，`ToolDescriptor` 是工具注册信息，`ToolCallClassification` 是根据本次 input 产生的执行分类。分类当前包含 `read_only`、`modifies_filesystem`、`concurrency_safe`、`targets`、`result_policy` 和 `permission_subject`。本计划不使用 `permission_subject` 做规则匹配；它可以继续作为审计摘要存在。

`ToolTarget` 是权限机制的核心输入。它描述工具本次触达的资源，例如 `ToolTarget(kind="file", operation="read", value="src/app.py")`。规则匹配必须基于 `ToolTarget.kind`、`ToolTarget.operation` 和归一化后的 target value，而不是工具私有字符串。文件路径 target 应先经过 `SandboxGuard` 或同等 path resolver 规范化，避免 Windows 盘符、符号链接和相对路径造成绕过。

`services/guard/` 是 sandbox 边界。`SandboxGuard.check_path()` 将路径分成 `allow`、`ask` 或 `deny`。denied pattern 命中是 hard deny。外部目录是 ask。workspace、worktree 和 extra allowed 目录是 allow。权限层不能削弱 guard deny；它只能把 guard ask 转成同一套 permission ask 流程，或者在 session 临时 allow 命中时允许已经被确认过的 ask target。

`services/hooks/` 提供 `PreToolUse`、`PostToolUse` 和 `ToolError`。hook 是扩展点，不是安全边界。当前 executor 会在 hook 前先 guard，hook 更新 input 后重新校验和 guard。实现权限层时必须保持这个性质：原始 input 被 deny 或 ask 阻断时 handler 不执行；hook 更新 input 后必须重新运行权限检查。

`RuntimeState` 位于 `core/runtime_state.py`，保存当前会话状态和 `metadata`。session 临时 allow 规则应保存在 `RuntimeState.metadata` 下的稳定 key，例如 `permission_session_allows`，并且只影响当前 session。`RuntimeState.start_new_session()` 已用于新会话语义；新会话应清空临时 allow。

`onecode.permissions.json` 是本计划新增的项目配置文件。第一版只读取仓库根目录这个文件；如果文件不存在，权限层使用空项目规则，只保留 guard 和 session 临时 allow。配置解析失败应 fail closed：执行入口返回结构化权限配置错误，并且 schema provider 在无法可靠判断时不应暴露被配置可能禁用的工具。为了降低 schema 阶段复杂度，第一版可以让配置加载错误在构造权限策略时抛出，并由应用装配或测试显式处理；如果当前没有应用装配层，测试直接实例化 loader 并断言错误。

## Plan of Work

第一阶段新增 `services/permissions/` 包。创建 `services/permissions/types.py`，定义 `PermissionBehavior`、`PermissionDecision`、`PermissionRule`、`PermissionTargetPattern` 和 `PermissionPolicy`。`PermissionBehavior` 是 `"allow" | "ask" | "deny"`。`PermissionDecision` 至少包含 `behavior`、`reason`、`source`、`rule_id`、`target` 和 `metadata`。`PermissionTargetPattern` 只表达结构化 target 匹配：`tool_name` 可选，`kind` 必填或可为 `"*"`, `operation` 必填或可为 `"*"`, `value` 可选，`value_match` 可为 `"exact"` 或 `"glob"`。不要实现 subject string 规则。

第二阶段定义项目配置格式并实现 loader。创建 `services/permissions/config.py`，从 `onecode.permissions.json` 读取 JSON。推荐格式如下：

    {
      "version": 1,
      "rules": [
        {
          "id": "deny-all-edit-file",
          "behavior": "deny",
          "tool_name": "edit_file",
          "target": {"kind": "*", "operation": "*"}
        },
        {
          "id": "ask-external-file-writes",
          "behavior": "ask",
          "tool_name": "edit_file",
          "target": {"kind": "file", "operation": "write", "value": "D:/tmp/*", "value_match": "glob"}
        }
      ]
    }

`version` 第一版必须是 `1`。`rules` 必须是 list。每条 rule 的 `behavior` 必须是 allow、ask 或 deny。`tool_name` 可选；缺失表示适用于所有工具。`target.kind` 和 `target.operation` 支持具体值或 `"*"`。`target.value` 可选；缺失表示只按 kind/operation 匹配。`value_match` 缺省为 `"exact"`。无效配置应返回或抛出清晰错误，不应静默忽略。

第三阶段实现结构化匹配。创建 `services/permissions/policy.py`，让 `PermissionPolicy.decide(tool_name, classification, guard_policies, state)` 返回 `PermissionDecision`。决策顺序固定为：先检查 guard deny；再检查项目 deny；再检查项目 ask；再检查 guard ask；再检查 session allow；再检查项目 allow；最后默认 allow。这里的 “guard_policies” 可以是 executor 在检查每个 filesystem target 时得到的结果，或是权限层调用 guard 后得到的结果；具体实现可选择最少改动方案，但必须保证 guard deny 在所有 allow 前生效。

第四阶段接入 executor。编辑 `services/tools/executor.py`，在 `_prepare_input()` 中 schema validation、tool validation、classification 之后执行 guard，并把 guard 结果交给权限 policy。当前 `_check_guard()` 遇到 ask 会立即返回 `_PreparedInputError`；实现后应改成收集每个 target 的 `GuardPolicy`，由权限层决定是否 ask-required、deny 或 allow。若返回 deny，executor 返回 `permission_denied` tool error；若返回 ask，executor 返回 `permission_ask_required` tool error；若返回 allow，继续进入 PreToolUse hook。hook updated input 后重复完整流程。

第五阶段接入 `ToolRegistry.tool_schemas()`。编辑 `services/tools/registry.py`，让 registry 可选接收 `PermissionPolicy`，或让 `tool_schemas(state)` 通过 state/策略对象判断 blanket deny。推荐保持 registry 轻：新增一个 provider-neutral wrapper 或在 registry 构造时传入 `permission_policy: PermissionPolicy | None = None`。只有 rule 明确 `behavior="deny"`、指定 `tool_name`、且 target 缺失或 `kind="*"`/`operation="*"`/无 value 时，才把整个工具从 schema 中移除。target-specific deny 不能在 schema 阶段判断，不应隐藏整个工具。

第六阶段实现 session 临时 allow。创建 `services/permissions/session.py` 或放在 policy 中，提供 `grant_session_allow(state, tool_name, targets, reason)` 方法。该方法把结构化 allow entry 写入 `state.metadata["permission_session_allows"]`。entry 至少包含 `tool_name`、`targets`、`created_at` 和 `reason`。匹配时必须仍然先跑 guard deny 和项目 deny；session allow 只能覆盖 ask，不能覆盖 deny。未来 `ask_user` 允许某次请求时调用这个方法；第一版测试直接调用该方法模拟用户允许。

第七阶段更新错误结果格式。`permission_denied` 和 `permission_ask_required` 应通过统一 helper 生成 `ToolExecutionResult`，`is_error=True`，`metadata["error"]` 分别为对应错误码。`content` 是 JSON 字符串，至少包含：

    {
      "error": "permission_ask_required",
      "message": "Tool call requires user approval before execution.",
      "tool_name": "edit_file",
      "decision": "ask",
      "reason": "...",
      "source": "project_rule",
      "rule_id": "ask-external-file-writes",
      "targets": [...]
    }

保留当前 `path_guard_denied` 和 `path_guard_ask_required` 的兼容测试是否改写，需要在实现时统一决定。推荐把 executor 前置 guard error 改成新 permission error；具体工具 handler 内部的二次 guard 仍可能返回旧 path guard error，但正常路径下 handler 不应再收到未经许可的 guard ask/deny。

第八阶段更新文档。编辑 `architecture.md`，把 `services/permissions/` 加入目标目录和运行流程，说明 guard 提供路径边界，permissions 负责项目规则、session allow 和 ask/deny/allow 决策。编辑 `docs/design-docs/tool-design-guidelines.md`，把 execution pipeline 改为 schema validation、validate_input、classification、guard target normalization、permission decision、PreToolUse hook、handler。编辑 `docs/tech-debt/tech-debt-tracker.md`，修正 TD-006 中已过时的字段描述；如果权限策略落地解决只读/权限裁剪的一部分，应把剩余债务描述聚焦到并发分批和通用 result store。

第九阶段增加测试。新增 `tests/test_permissions_policy.py` 覆盖配置 loader、无效配置、deny 优先、ask 返回、session allow 覆盖 ask 但不覆盖 deny、结构化 glob value 匹配和禁止 subject 规则。更新 `tests/test_tool_registry_and_executor.py` 覆盖 executor 不执行被 deny/ask 的 handler。更新 `tests/test_file_tools_guard.py` 覆盖外部路径 ask 在未授权时返回 `permission_ask_required`，写入 session allow 后同一外部路径可以执行，denied path 即使有 session allow 也不能执行。更新 registry schema 测试，证明 blanket deny 的工具不进入 schema。

## Concrete Steps

在仓库根目录执行所有命令：

    cd D:\study\OneCode

开始前查看工作区，确认不要覆盖用户已有改动：

    git status --short

实现建议按以下顺序编辑：

1. 新建 `services/permissions/__init__.py`、`services/permissions/types.py`、`services/permissions/config.py`、`services/permissions/policy.py`。
2. 新增 `tests/test_permissions_policy.py`，先只测试纯 policy，不接 executor。
3. 编辑 `services/tools/executor.py`，把 guard ask/deny 收敛到 permission decision。
4. 更新 executor 和 file guard 测试，使 ask/deny 行为从权限层返回。
5. 编辑 `services/tools/registry.py`，加入 blanket deny schema 裁剪，并更新 registry 测试。
6. 更新 `architecture.md`、`docs/design-docs/tool-design-guidelines.md` 和 `docs/tech-debt/tech-debt-tracker.md`。

每完成一个阶段，运行 focused tests：

    uv run python -m pytest tests/test_permissions_policy.py -q
    uv run python -m pytest tests/test_tool_registry_and_executor.py tests/test_file_tools_guard.py -q

最终运行：

    uv run python -m compileall core services infrastructure tools
    uv run python -m pytest tests -q

## Validation and Acceptance

验收标准一：没有 `onecode.permissions.json` 时，现有 read_file 和 edit_file 在 workspace 内的行为保持不变，现有工具测试通过。

验收标准二：项目配置包含 blanket deny `edit_file` 后，`ToolRegistry.tool_schemas(state)` 不再包含 `edit_file`，并且手写 `ToolCall(name="edit_file", ...)` 进入 executor 时返回 `permission_denied`，handler 不执行，目标文件不改变。

验收标准三：项目配置包含针对 `file/write` target 的 ask rule 后，相同 target 的工具调用返回 `permission_ask_required`，handler 不执行，目标文件不改变。返回 JSON 中包含 rule id、tool name、decision、reason 和结构化 targets。

验收标准四：调用 session allow 写入方法模拟用户允许后，相同 session 内相同 `tool_name` 和结构化 target 的 ask 请求可以继续执行。调用 `RuntimeState.start_new_session()` 后，临时 allow 不再生效。

验收标准五：如果项目 deny 和 session allow 同时命中同一 target，deny 获胜，工具不执行。

验收标准六：外部 sandbox 路径默认仍返回 ask-required，不直接读写；被用户临时 allow 后可以执行；denied pattern 命中时仍然返回 deny，不能被临时 allow 覆盖。

验收标准七：运行以下命令通过：

    uv run python -m compileall core services infrastructure tools
    uv run python -m pytest tests -q

## Idempotence and Recovery

读取 `onecode.permissions.json` 是只读操作。文件不存在时按空规则处理。无效配置不应被自动修改；测试应使用 `tmp_path` 创建临时项目根目录，避免污染真实仓库配置。

session 临时 allow 只写入 `RuntimeState.metadata`，不写回项目配置。重复写入同一 allow entry 应去重或保持匹配语义不变，不能导致规则优先级改变。新会话应清空临时 allow；如果实现使用 `RuntimeState.start_new_session()`，确认该方法会清理相关 metadata。

权限错误返回 tool result，而不是抛出未捕获异常。这样模型可以看到失败原因并调整后续计划，主循环不会因为权限拒绝崩溃。

## Artifacts and Notes

一个最小 blanket deny 配置示例：

    {
      "version": 1,
      "rules": [
        {
          "id": "deny-edit-file",
          "behavior": "deny",
          "tool_name": "edit_file",
          "target": {"kind": "*", "operation": "*"}
        }
      ]
    }

一个 target-specific ask 配置示例：

    {
      "version": 1,
      "rules": [
        {
          "id": "ask-tmp-writes",
          "behavior": "ask",
          "tool_name": "edit_file",
          "target": {
            "kind": "file",
            "operation": "write",
            "value": "D:/tmp/*",
            "value_match": "glob"
          },
          "reason": "Writing outside the project requires approval."
        }
      ]
    }

一个 session allow metadata 示例：

    {
      "permission_session_allows": [
        {
          "tool_name": "edit_file",
          "targets": [
            {
              "kind": "file",
              "operation": "write",
              "value": "D:\\tmp\\outside.txt",
              "normalized_value": "D:\\tmp\\outside.txt"
            }
          ],
          "created_at": "2026-06-04T10:30:00Z",
          "reason": "User approved permission request."
        }
      ]
    }

## Interfaces and Dependencies

`services/permissions/types.py` should define dataclasses similar to:

    PermissionBehavior = Literal["allow", "ask", "deny"]

    @dataclass(frozen=True)
    class PermissionTargetPattern:
        kind: str
        operation: str
        value: str | None = None
        value_match: Literal["exact", "glob"] = "exact"

    @dataclass(frozen=True)
    class PermissionRule:
        id: str
        behavior: PermissionBehavior
        target: PermissionTargetPattern
        tool_name: str | None = None
        reason: str = ""

    @dataclass(frozen=True)
    class PermissionDecision:
        behavior: PermissionBehavior
        reason: str
        source: str
        rule_id: str | None = None
        targets: tuple[ToolTarget, ...] = ()
        metadata: dict[str, Any] = field(default_factory=dict)

`services/permissions/config.py` should expose:

    def load_project_permission_policy(project_root: Path) -> PermissionPolicy:
        ...

`services/permissions/policy.py` should expose:

    class PermissionPolicy:
        def __init__(self, rules: Iterable[PermissionRule] = ()) -> None: ...
        def decide(
            self,
            *,
            tool_name: str,
            classification: ToolCallClassification,
            guard_policies: tuple[GuardPolicy, ...],
            state: RuntimeState,
        ) -> PermissionDecision: ...
        def is_tool_blanket_denied(self, tool_name: str) -> bool: ...
        def grant_session_allow(
            self,
            *,
            state: RuntimeState,
            tool_name: str,
            targets: tuple[ToolTarget, ...],
            reason: str,
        ) -> None: ...

The executor should receive `permission_policy: PermissionPolicy | None = None` in `RegistryToolExecutor.__init__`. `None` means an empty policy that still respects guard deny/ask. This keeps existing tests and composition code easy to migrate.

The registry should either receive the same policy or a narrow schema filter object. Keep the dependency direction inside `services/`: `services/tools` may depend on `services/permissions` because both are runtime services; concrete tools must not import permission config or policy.

2026-06-04 / Codex: 初始计划创建，记录用户确认的 ask/error、项目配置、blanket deny schema 裁剪、session 临时 allow、结构化 target-only 规则和不更新 completed plans 的决策。
