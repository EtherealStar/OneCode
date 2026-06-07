# write_file 工具轻量实现计划草稿

本文是 `write_file` 工具第一版的轻量计划草稿，不按 `PLANS.md` 的完整 ExecPlan 模板维护。它用于记录已经确认的产品/架构方向、实现切入点和测试范围，后续正式实现时可直接据此拆任务。

## 背景

OneCode 当前已有 `read_file` 和 `edit_file`：

- `read_file` 负责受 guard 保护地读取文本文件，并由 executor 在成功后更新 `RuntimeState.metadata["files_read"]` 和 `FileStateCache`。
- `edit_file` 负责 exact string replacement；已有文件要求先读后改，新文件可通过 `old_string=""` 创建。
- `services/tools/executor.py` 中的 `FILE_STATE_TOOL_NAMES` 已包含 `write_file`，说明 executor side effect 已为写入工具预留缓存更新位置。

参考材料 `docs/references/Tools_full/FileWriteTool/` 的核心语义是：`Write` 创建或整文件覆盖本地文件；已有文件必须先读；写入前检查文件没有在读后被外部修改；结果区分 create/update，并对 update 展示 diff。

OneCode 第一版应吸收这些核心语义，但不照搬参考实现里的产品侧能力，例如 LSP 通知、VSCode 通知、analytics、file history、team memory secret guard 和动态 skill path 激活。

## 已确认方向

- 工具名使用 `write_file`，符合 OneCode snake_case 工具命名。
- 允许 workspace-relative 路径；不强制模型传绝对路径。路径解析和边界判断交给 `SandboxGuard`。
- 已存在文件需要读过，但第一版不严格要求“模型完整看过全文件”。实现可依赖当前 `FileStateCache` 的 `partial=False` 语义。
- 覆盖已有文件时返回短 diff，不把完整新内容作为模型可见结果回填。
- 成功写入后进入 executor 的文件状态缓存，与 `read_file` / `edit_file` 保持一致。
- CLI 权限确认增加 `write_file` 专用展示，而不是只走 fallback panel。

## 第一版范围

新增顶层工具目录：

```text
tools/write_file/
  __init__.py
  tool.py
  prompt.py
```

`write_file` 输入 schema：

```python
{
    "type": "object",
    "properties": {
        "file_path": {"type": "string"},
        "content": {"type": "string"},
    },
    "required": ["file_path", "content"],
    "additionalProperties": False,
}
```

行为：

- 如果目标不存在：创建父目录并写入 `content`。
- 如果目标存在且是文件：要求该路径在当前 session 中已经被读取，并且 `FileStateCache` 中记录不是 partial view。
- 如果目标存在且是目录：返回 `path_is_directory` 错误。
- 如果读后目标文件 mtime 变化，比较缓存内容和当前内容；若当前内容已不同，返回 `file_unexpectedly_modified`，要求重新读取。
- 写入使用 UTF-8 文本；第一版不处理二进制、编码保留或平台换行自动转换。
- 写入后返回 create/update 成功结果，并让 executor side effect 记录 `files_read` 与 `FileStateCache` 快照。

## Descriptor 与权限分类

`ToolDescriptor`：

- `name="write_file"`
- `description="Create or overwrite a local text file."`
- `search_hint="create or overwrite files"`
- `prompt=PROMPT`
- `validate_input=_validate`
- `classify_input=_classify_input`
- `handler=_handle`

`classify_input()`：

- `read_only=False`
- `modifies_filesystem=True`
- `concurrency_safe=False`
- `targets=(ToolTarget(kind="file", operation="write", value=file_path),)`
- `result_policy=ToolResultPolicy(max_result_size_chars=50_000, persist_when_exceeded=True, preview_chars=4_000)`
- `permission_subject=f"write_file:{file_path}"`

`handler()` 内部仍必须调用 `runtime.guard.check_write_target()` 作为兜底。executor 入口的 guard/permission 已经先跑一遍，但 handler 不应假设它一定被正确装配。

## Prompt 草稿

`tools/write_file/prompt.py` 只放模型可见规则，建议内容：

```text
Create or completely overwrite a sandboxed text file.

Use this tool when creating a new file or replacing the entire contents of an existing file.
Prefer edit_file for localized changes because it sends a smaller, safer diff.
For existing files, read the file first in this session before using write_file.
Do not create documentation files such as *.md or README files unless the user explicitly asks for them.
Avoid emojis in written files unless the user explicitly asks for them.
```

这里保留参考实现的“优先 edit_file”“已有文件先读”“不要主动创建文档”“不要主动写 emoji”规则。

## 文件状态与 stale-write 检查

实现应优先使用 `RegistryToolExecutor.file_state_cache` 已有机制，而不是新增一套读写状态。

当前约束：

- `ToolRuntime` 只携带 `state`、`guard`、`approved_guard_policies`、`tool_call_id`，不直接暴露 `FileStateCache`。
- 因此 `write_file` handler 若要读取 cache，需要先扩展 `ToolRuntime`，或在 executor side effect 之前执行一个更专用的 stale-write preflight。

建议第一版采用最小侵入方案：

1. 给 `ToolRuntime` 增加可选 `file_state_cache: FileStateCache | None = None`。
2. `RegistryToolExecutor` 构造 `ToolRuntime` 时注入 `self._file_state_cache`。
3. `write_file` handler 使用 `runtime.file_state_cache.get(path)` 校验已有文件是否读过、是否 partial、mtime/content 是否仍一致。
4. 写入成功后继续由 executor 的 `_apply_success_side_effects()` 统一 snapshot，不让 handler 自己更新主状态。

这样保持“handler 做执行前语义校验，executor 做成功后的统一 side effect”的现有边界。

## 结果形态

创建新文件成功：

```text
Created <path> (<line_count> line(s)).
```

metadata：

```python
{
    "path": str(path),
    "operation": "create",
    "line_count": line_count,
}
```

覆盖已有文件成功：

```text
Updated <path> (<line_count> line(s)).

<short unified diff>
```

metadata：

```python
{
    "path": str(path),
    "operation": "update",
    "line_count": line_count,
    "diff": short_diff,
    "diff_truncated": bool,
}
```

短 diff 可用 `difflib.unified_diff()` 生成，限制在 4KB 左右。模型只需要知道发生了什么，不需要在 tool result 中重复完整文件内容。

## CLI 权限展示

在 `ui/cli/permissions.py` 中新增 `write_file` 分支：

- 标题：`Write file permission requested`
- 展示 reason、target、normalized path、operation。
- 展示 `content` 首行或前 240 字符预览。
- 展示 `content` 行数。
- 选项沿用文件写入类工具的 session directory allow。

第一版不需要 project-level `write_file(...)` 内容规则 UI；项目级整工具 allow/deny/ask 已可通过 `.onecode/settings.json` 生效。

## Runtime 装配

在 `ui/cli/app.py::build_runtime()` 中：

- import `tools.write_file.descriptor`
- 将 `write_file_descriptor()` 加入 `base_descriptors`，位置建议放在 `edit_file` 后、`glob` 前。

Subagent base descriptors 会自然获得 `write_file`。只读 subagent 已在 `services/subagents/definitions.py` 的 `Explore` / `Plan` 中 disallow `write_file`，permission policy 的 read-only agent deny 也会兜底阻断写入。

## 测试范围

新增或扩展 `tests/test_file_tools_guard.py`：

- `write_file` descriptor 分类为 `file/write`、非只读、不可并发。
- 创建新文件成功，并创建父目录。
- 已存在文件未读时返回 `file_not_read`，不修改磁盘。
- 已存在文件只有 partial read cache 时返回 `file_not_fully_read` 或同等错误。
- 已存在文件读后未变化时成功覆盖，并返回短 diff。
- 已存在文件读后被外部修改且内容变化时返回 `file_unexpectedly_modified`，不覆盖用户改动。
- 目标是目录时返回 `path_is_directory`。
- guard deny 不写文件。
- external path ask 且无 prompter 时返回 `permission_ask_required`，不写文件。
- 成功后 `state.metadata["files_read"]` 包含规范化路径，`FileStateCache` 内容更新为新内容。

扩展 registry/runtime 测试：

- `build_runtime()` 注册 `write_file`。
- `/tools` 输出包含 `write_file`。
- project settings 中 `deny: ["write_file"]` 时，schema/prompt 不包含该工具，历史调用在 executor 入口返回 `permission_denied`。

扩展 CLI 权限测试：

- `write_file` permission panel 渲染路径、行数和内容预览。

建议验证命令：

```powershell
uv run python -m pytest tests/test_file_tools_guard.py tests/test_cli_permissions.py tests/test_cli_commands.py tests/test_tool_registry_and_executor.py -q
uv run python -m compileall core services infrastructure tools ui
uv run python -m pytest tests -q
```

## 暂不纳入第一版

- 编码检测与原编码保留。
- 原子写临时文件和 fsync。
- 文件历史备份。
- LSP / VSCode 通知。
- team memory secret guard。
- 写入后自动发现或激活 path-based skills。
- 大 diff 外置为专用附件或 result artifact。
- 富 UI diff 渲染。

这些可以后续独立设计。第一版先把工具语义、安全边界、缓存一致性和测试补齐。

## 需要实现时注意

- 不要在 `core/loop.py` 中添加 `write_file` 特例。
- 不要让 `services/tools/` 静态 import `tools/write_file/`。
- guard deny 不能被用户确认、session allow 或 hook 覆盖。
- hook 如果修改 `file_path` 或 `content`，executor 已会重新 schema validation、tool validation、classification、guard 和 permission policy；`write_file` 自身只需要提供正确 descriptor。
- 如果扩展 `ToolRuntime`，要确认现有测试中手工构造 `ToolRuntime(state=...)` 不被破坏；新增字段必须有默认值。
