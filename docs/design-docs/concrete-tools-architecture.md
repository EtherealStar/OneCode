# Concrete Tools Architecture

本文描述顶层 `tools/` 下具体工具的职责。工具实现以目录为单元组织，工具运行时能力由 `services/tools/` 提供。

## 工具目录约定

每个工具使用：

```text
tools/<tool_name>/
  __init__.py
  tool.py
  prompt.py
```

`tool.py` 导出 `descriptor()`，提供 schema、validator、classifier 和 handler。

`prompt.py` 导出模型可见的工具使用说明。工具 prompt 通过 `ToolDescriptor.prompt` 进入 `DynamicPromptAssembler`。

工具实现可以依赖 `services.tools` 公共类型和 `ToolRuntime`，但不能依赖 `core/loop.py`。路径类工具必须通过 `ToolRuntime.guard` 或 `services/guard/` 做兜底检查。

## read_file

`tools/read_file/` 读取 sandbox 内 UTF-8 文本文件，返回带行号内容。

输入包括：

- `file_path`
- `offset`
- `limit`

分类为只读、可并发、`file/read` target。结果策略为无限制且不持久化，因为该工具通过 offset/limit 自限流。

执行成功后，executor 会把规范化路径记录到 `RuntimeState.metadata["files_read"]`。

## edit_file

`tools/edit_file/` 对 sandbox 内文本文件执行 exact string replacement。

输入包括：

- `file_path`
- `old_string`
- `new_string`
- `replace_all`

分类为 `file/write` target，不可并发。编辑已有文件前要求目标文件已在本 session 中被读取；目标文件不存在且 `old_string` 为空时可以创建新文件。多重匹配默认要求更精确上下文或 `replace_all=true`。

## glob

`tools/glob/` 按路径通配模式发现 sandbox 内文件。

输入包括：

- `pattern`
- `path`
- `head_limit`
- `offset`

分类为只读、可并发、directory/list target。handler 会对搜索根和候选结果执行 guard 检查，结果按修改时间降序分页返回。

## grep

`tools/grep/` 通过 `rg` 搜索 sandbox 内文件内容。

输入包括：

- `pattern`
- `path`
- `glob`
- `output_mode`
- 上下文参数
- 大小写参数
- 分页参数

分类为只读、可并发、文件系统 read target。handler 会对搜索根和 ripgrep 结果执行 guard 过滤，并把 ripgrep 失败转换为结构化工具错误。

`grep` 已设置 20KB 结果预算；超过预算时当前 executor 返回结构化预览，durable result store 仍是目标能力。

## bash

`tools/bash/` 通过 Git Bash 执行 shell 命令。

输入包括：

- `command`
- `timeout_ms`
- `description`

分类基于 `tree-sitter` / `tree-sitter-bash` 解析 Bash AST，再从 simple command、argv 和 redirect 派生文件系统 target。`check_semantics()` 拒绝无法静态理解的 wrapper、eval-like builtin 和动态代码执行形态。

只读 allowlist 命令可自动执行，但仍受 guard 约束。写入、删除、未知副作用或 parse/semantic failure 会生成 `command/execute` target，并由 `PermissionPolicy` 触发权限确认。

runner 使用 Git Bash 的 `bash --noprofile --norc -lc`。找不到 Git Bash 时返回结构化 `git_bash_not_found` 错误。

当前限制：

- 只支持 Git Bash。
- 只理解有限 Bash AST 子集。
- 不支持后台任务生命周期。
- 不支持持久 Bash prefix allow rule。
- 大 stdout/stderr 尚未接入 durable result store。

## agent

`tools/agent/` 将 subagent 作为普通工具暴露给父 agent。

输入包括：

- 必填 `prompt`
- 可选 `subagent_type`

省略 `subagent_type` 触发 fork；显式 `general-purpose`、`Explore` 或 `Plan` 使用对应内置定义。

工具 handler 调用 `SubagentRunner`，返回 child 最终摘要和安全 metadata，不把 child 完整消息链写回父消息链。

## 目标工具

`write_file` 仍属于目标工具，目前顶层 `tools/write_file/` 尚未实现。新增工具应先遵循 `docs/design-docs/tool-design-guidelines.md`，必要时为复杂功能编写 ExecPlan。
