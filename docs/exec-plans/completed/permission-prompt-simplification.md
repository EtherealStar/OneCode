# 权限请求弹窗三选项化与项目规则入口收敛

## 目标

权限请求 UI 应改为临时性的三选项面板：用户选择后立即消失，不进入静态 scrollback，不保留旧的全屏权限弹窗路径。

权限请求面板只允许三个选择：

1. 允许本次
2. 本会话允许
3. 拒绝

项目级允许、拒绝、询问规则不能出现在临时权限请求面板里，也不能通过权限请求面板写入 `.onecode/settings.json`。项目级权限规则必须进入 `/permissions` 管理。

这个改动只调整 CLI 权限交互和 `/permissions` 入口，不改变 `core/loop.py`、工具执行链、guard、deny-first 语义或项目权限存储格式。

## 当前问题

TTY 权限提示当前在 `ui/cli/terminal/permission_prompt.py` 中用 `Application(full_screen=True)` 实现。它使用备用屏幕展示权限问题，视觉上接近 `/status` 页面，不是一个嵌在当前运行状态中的临时确认面板。

流式运行中已有 `run_in_terminal` 回退，但回退仍打印旧的文本面板并读一行输入；这条路径也不是统一的临时三选项面板。

`ui/cli/permissions.py` 和 `ui/cli/terminal/permission_prompt.py` 还为 bash 暴露了 `p/project` 选项，并构造 `PermissionUpdate(destination="projectSettings")`。这让一次工具授权请求同时承担“本次授权”和“项目级配置编辑”两个职责，和目标交互模型冲突。

底层 `services/permissions/policy.py::request_for_decision()` 已经默认生成三项：allow once、allow session directory、deny。应让 CLI 权限 UI 严格消费这个模型，而不是在 UI 层追加 project 选项。

## 设计原则

权限请求是运行时阻塞确认，不是设置管理页面。它只回答当前 tool call 是否继续，以及是否在当前 session 内记住同类目录授权。

项目级规则是持久配置，必须通过 `/permissions` 显式查看和编辑。这样用户能清楚区分临时会话授权和会写入仓库 `.onecode/settings.json` 的持久规则。

旧权限弹窗必须删除，不能以兼容为理由保留。实现完成后不应同时存在：

- 备用屏幕 full-screen 权限 prompt
- bash 专属 `p/project` 快捷授权
- 旧的 `Allow? [y] once [s] session [p] project [n] deny` 文本路径
- 新三选项面板之外的另一套 TTY 权限选择 UI

非 TTY / batch 场景可以保留纯文本 fallback，但也必须只有三项，并且不能提供 project 选项。

## 涉及文件

- `ui/cli/terminal/permission_prompt.py`
  - 删除旧的 full-screen 权限弹窗实现。
  - 新增或重写为非全屏、可擦除的临时权限面板。
  - 不再构造 project permission update。
  - 不再从 bash 特例里派生 project rule。

- `ui/cli/permissions.py`
  - 收敛为权限请求内容格式化和非 TTY 三选项 fallback。
  - 删除 `_bash_project_rule_content()`。
  - 删除 `_confirm_options()` 中的 project 选项。
  - 删除 `_prompt_line()` 中的 project 提示。
  - 删除 `_bash_panel()` 中的 project 文案。
  - `CliPermissionPrompter` 只解析 allow once、allow session、deny。

- `services/permissions/policy.py`
  - 原则上不需要改。
  - 如果为了让 UI label 更接近截图，可以只调整 `PermissionOption.label` / `description`，保持三项和现有 `record_response()` 语义不变。

- `ui/cli/commands.py`
  - 扩展 `/permissions` 子命令，新增项目级规则管理入口。
  - `/permissions` 无参数继续显示权限状态页。
  - `/permissions add|remove|replace allow|deny|ask <rule...>` 调用 project settings store。

- `ui/cli/views/permissions.py`
  - 继续展示 session grants 和 project rules。
  - 增加简短操作提示，说明项目规则必须通过 `/permissions add/remove/replace ...` 修改。
  - 不承担交互输入，不直接写文件。

- `services/permissions/project_settings.py`
  - 原则上不需要改。
  - 已有 `apply_update()` 支持 add/remove/replace。

- `tests/test_cli_permissions.py`
  - 更新旧的 bash project 断言。
  - 新增三选项面板测试。

- `tests/test_cli_commands.py`
  - 新增 `/permissions` 项目规则 add/remove/replace 测试。

## 推荐实现步骤

### 1. 删除旧权限弹窗路径

在 `ui/cli/terminal/permission_prompt.py` 中移除 full-screen 权限 prompt 设计。

新的 TTY prompt 使用 `prompt_toolkit.Application(full_screen=False, erase_when_done=True)`。它应像 streaming/status 动态区一样是临时区域，用户确认后自动擦除。

面板内容保持短小：

```text
Read file

Search(pattern: "**/*", path: "...")

Do you want to proceed?
> 1. Yes
  2. Yes, allow reading from ... during this session
  3. No

Esc to cancel · ↑↓ to select · Enter to confirm
```

具体文案可以按工具类型变化，但选项数量必须固定为三项。Esc 和 Ctrl-C 都返回 deny。

不要保留旧 full-screen 实现作为 fallback。TTY 主路径只能有这一套临时面板。

### 2. 让权限 UI 消费 `PermissionRequest.options`

`PermissionPolicy.request_for_decision()` 已生成 `request.options`。TTY prompt 应从 `request.options` 构建选择，而不是用 CLI 自己的 bash 特例。

推荐映射：

- `allow_once` -> `PermissionResponse(action="allow", scope="once")`
- `allow_session_directory` -> `PermissionResponse(action="allow", scope="session")`
- `deny` -> `PermissionResponse(action="deny")`

如果未来 policy 需要调整 session label，应只改 policy 的 option label/description，UI 不应再新增第四项。

### 3. 删除 project 快捷授权

删除这些旧行为：

- 输入 `p` 生成 project allow
- bash 命令自动压成 `npm run:*`
- bash 权限面板显示 `[p] allow this command prefix for this project`
- 非 bash 工具收到 `p` 时降级为 session allow

删除后，任何 `PermissionResponse.permission_updates` 都不应由权限请求 prompt 产生。项目规则写入只从 `/permissions` 入口发生。

### 4. 保留非 TTY fallback，但只保留三项

`CliPermissionPrompter` 和 `_blocking_confirm()` 可以继续作为 batch / 无法启动 prompt_toolkit 时的 fallback，但提示必须是：

```text
Allow? [y] once  [s] session  [n] deny:
```

不能显示或接受 `p/project`。如果用户输入 `p`，应视为 unrecognized 或 deny，不得写项目设置。

### 5. 扩展 `/permissions` 为项目规则管理入口

当前 `/permissions` 只读展示。新增子命令：

```text
/permissions
/permissions add allow bash(npm run:*)
/permissions add deny edit_file
/permissions add ask read_file(D:\sensitive\*)
/permissions remove allow bash(npm run:*)
/permissions replace deny agent
```

解析规则：

- 第一个参数：`add` / `remove` / `replace`
- 第二个参数：`allow` / `deny` / `ask`
- 后续参数：一个或多个 permission rule 字符串

命令层构造 `PermissionUpdate`：

- `add` -> `type="addRules"`
- `remove` -> `type="removeRules"`
- `replace` -> `type="replaceRules"`
- `destination="projectSettings"`

然后调用 `runtime.permission_policy.project_store.apply_update(update)`。

如果没有 project store，返回错误 renderable。如果 rule 解析失败，返回错误 renderable，不写 settings。

### 6. 更新 `/permissions` 页面说明

`ui/cli/views/permissions.py` 保持只读展示，但需要明确告诉用户项目规则如何编辑。例如在 Project 表下面增加一行：

```text
edit: /permissions add|remove|replace allow|deny|ask <rule>
```

这里不要做交互式输入。第一版用子命令足够清晰，也方便测试。

### 7. 文档更新

更新 `docs/design-docs/cli-architecture.md` 的权限部分：

- TTY 权限请求使用可擦除临时面板。
- 权限请求只产生 once/session/deny。
- 项目级规则只能通过 `/permissions` 修改。
- 备用屏幕继续用于 `/status`、`/permissions` 等查看页，不再用于权限请求。

更新 `docs/design-docs/permission-architecture.md` 的 `record_response 与 UI` 段落：

- 删除 bash project prompt 的描述。
- 说明 project settings updates 来自 `/permissions` 命令，而不是权限请求 prompt。

## 测试建议

### 权限面板测试

更新 `tests/test_cli_permissions.py`：

- `render_permission_panel()` 对 bash 不包含 `[p]`。
- `_confirm_options()` 只返回 y/s/n。
- `CliPermissionPrompter(input_func=lambda _: "p")` 不返回 project allow。
- `CliPermissionPrompter(input_func=lambda _: "s")` 仍返回 allow session。
- `CliPermissionPrompter(input_func=lambda _: "y")` 仍返回 allow once。
- `CliPermissionPrompter(input_func=lambda _: "n")` 返回 deny。

新增或更新 TTY prompt 单元测试：

- `_build_choices(request)` 只生成三个 choice。
- 选择第三项或 Esc 返回 deny。
- 选择第二项返回 `scope="session"`。
- 不存在任何 `permission_updates`。

### `/permissions` 命令测试

更新 `tests/test_cli_commands.py`：

- `/permissions` 无参数仍返回 page renderable。
- `/permissions add allow bash(npm run:*)` 写入 `.onecode/settings.json` 的 `permissions.allow`。
- `/permissions add deny edit_file` 写入 `permissions.deny`。
- `/permissions remove allow bash(npm run:*)` 删除对应规则。
- `/permissions replace ask read_file(secret/*)` 替换 `permissions.ask`。
- 非法 behavior / update type / rule 返回错误，不写 settings。

### 策略回归测试

现有 `tests/test_permission_policy.py` 和 `tests/test_project_permission_settings.py` 应继续通过。它们验证 project rules 本身仍有效，只是写入入口从权限 prompt 移到 `/permissions`。

## 手工验证

启动 TTY CLI：

```powershell
uv run python -m ui.cli.app
```

触发一次需要权限的外部文件读取或非只读 bash 命令。

预期：

- 看到一个非全屏临时权限面板。
- 只有 1/2/3 三个选择。
- 选择后面板消失。
- 选择 session 后，同 session 内同类目录请求不再重复询问。
- 面板中没有 project 选项。
- 输入 `p` 不会写 `.onecode/settings.json`。

验证 `/permissions`：

```text
/permissions add allow bash(npm run:*)
/permissions
/permissions remove allow bash(npm run:*)
/permissions
```

预期：

- 第一条命令写入项目规则。
- `/permissions` 页面显示规则。
- remove 后规则消失。
- 这些项目规则仍被 `PermissionPolicy` 消费。

## 非目标

- 不修改 agent 主循环。
- 不改变 guard deny 的优先级。
- 不改变 session directory grant 的含义。
- 不改变 `.onecode/settings.json` 的权限字段格式。
- 不新增组织级或用户级权限来源。
- 不实现复杂的交互式 `/permissions` 列表编辑器。
- 不保留旧权限弹窗作为兼容路径。

## 验收标准

- TTY 权限请求不再使用 full-screen alternate screen。
- 权限请求 UI 只有 allow once、allow session、deny 三项。
- bash 权限请求不再出现 project 选项。
- 权限请求 prompt 不再产生 `PermissionUpdate(destination="projectSettings")`。
- `/permissions` 可以添加、删除、替换 project allow/deny/ask 规则。
- 项目规则仍通过现有 `PermissionPolicy` 生效。
- 旧权限弹窗代码被删除，而不是闲置保留。
