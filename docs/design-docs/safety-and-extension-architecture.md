# Safety And Extension Architecture

本文描述 `services/guard/`、`services/permissions/` 和 `services/hooks/` 的架构。三者都参与工具执行前后的控制，但职责不同：guard 做确定性边界判断，permission policy 做 deny-first 决策合并，hook 做生命周期扩展。

## Guard

`services/guard/` 负责项目访问边界和路径安全策略。

`resolver.py` 作为路径解析 facade，导出 `resolve_path`、`resolve_write_target`、`normalize_path_pattern`、`windows_path` 和相关类型。底层跨平台路径处理位于 `infrastructure/filesystem/paths.py`。

`boundary.py` 定义 `SandboxBoundary` 和路径分类。边界支持：

- cwd
- worktree
- extra allowed dirs
- denied patterns

路径分类结果包括：

- `inside_workspace`
- `inside_worktree`
- `inside_extra_allowed`
- `external_directory`
- `denied`

实现会处理 Windows 等价路径形式、符号链接 realpath、缺失写入目标的父目录解析、根目录 worktree 保护，以及基于 `relative_to` 语义的包含判断。

`policy.py` 定义 `SandboxGuard` 和 `GuardPolicy`。guard 将路径分类映射为：

- `allow`：workspace/worktree/extra allowed 内部路径。
- `ask`：外部目录。
- `deny`：denied pattern 命中。

`GuardPolicy` 同时保留原始路径、规范化路径、operation、target kind、分类结果和 reason，供工具错误和 trace 使用。

## Permission Policy

`services/permissions/` 负责把工具级规则、guard 结果、危险目录规则、可疑路径规则、session 临时授权和 UI 用户确认合并成最终工具调用决策。

`types.py` 定义 provider-neutral 结构：

- `PermissionDecision`
- `PermissionRequest`
- `PermissionResponse`
- `PermissionOption`

这些类型不绑定 CLI；测试、CLI 或未来 UI 都可以实现自己的 prompter。

`session.py` 实现内存中的 `SessionPermissionStore`。它支持本 session 内按工具名、operation 和目录授权，也支持 session 级工具 deny/disabled。它不写磁盘，`/clear` 和 `/resume` 会清理临时授权。

`policy.py` 实现 deny-first `PermissionPolicy`。当前顺序：

1. `read_only_agent` 对 state-changing 调用直接 deny。
2. 工具级 denied/disabled 直接 deny。
3. guard deny 直接 deny。
4. 非只读 `command/execute`、受保护项目目录、可疑 Windows 路径和 guard ask 产生 ask。
5. session allow 只能覆盖 ask，不能覆盖 deny。
6. 无 ask 时 allow。

受保护项目目录当前包括 `.git`、`.vscode`、`.idea`、`.onecode`。

`prompter.py` 定义 async `PermissionPrompter` protocol。当前 CLI 通过终端输入实现权限确认。非交互 executor 未注入 prompter 时，ask 会变成结构化 `permission_ask_required` 工具结果，保持 fail closed。

## Registry 可见性

`PermissionPolicy.is_tool_visible()` 被 `ToolRegistry.visible_descriptors(state)` 消费。被工具级 deny 或 disabled 的工具不会进入 provider schema 或 prompt 工具说明。

路径参数级判断不能在 prompt 组装阶段猜测，仍必须在执行入口基于实际 `ToolTarget` 重复 guard 和 permission policy。

## Hooks

`services/hooks/` 是 runtime 生命周期扩展点。

当前稳定事件：

- `PreToolUse`
- `PostToolUse`
- `ToolError`

`HookRegistry` 管理 callback 注册和顺序执行。callback 可以返回：

- blocking error
- updated input
- metadata

callback 异常会被记录到 hook metadata，不中断 hook 链。

executor 在 `PreToolUse` 前先执行 schema validation、工具 validation、classification、guard 和 permission policy。如果原始输入已经被 deny 或 ask 阻断，handler 不会执行。hook 更新输入后，executor 必须重新执行 schema validation、工具 validation、classification、guard 和 permission policy。

## Guard 与 Hook 的关系

guard 是安全边界，hook 是扩展点。

guard 负责：

- 路径是否越界。
- 路径是否命中 deny。
- 当前 sandbox boundary 是否允许访问。
- 是否需要 ask。

hook 负责：

- 记录工具调用。
- 修改工具输入。
- 工具前后审计。
- 未来 compact 前后处理。
- 未来 stop 阶段继续或停止决策。

冲突时 guard 优先。deny 结果不能被 hook、session allow、permission prompter 或模型请求覆盖。

## 目标扩展事件

未来 hook 事件可覆盖：

- `UserPromptSubmit`
- `PreCompact`
- `PostCompact`
- `Stop`

新增 hook 事件应代表 agent 生命周期中的稳定节点，且没有该事件时扩展会被迫侵入主流程。
