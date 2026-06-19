# .onecode Directory Architecture

本文描述项目级 `.onecode/` 目录的目标结构和职责边界。`.onecode/` 是 OneCode 在单个 workspace 内的运行时状态目录，承载会话恢复、上下文治理、项目级配置、长期记忆、任务图和本地缓存。

它不是用户级全局配置目录。用户级配置、全局指令和全局 skill 应属于 `~/.onecode/`，不放进项目 `.onecode/`。

## 设计原则

- 会话数据按 session 聚合，便于恢复、导出、清理和调试。
- 项目级状态与会话级状态分离，避免跨会话能力被误删。
- 完整事实来源使用 append-only 或可重建文件，索引只作为缓存。
- 大输出、trace、错误日志和 session memory 与 transcript 保持同 session 生命周期。
- `tasks/` 和 `memory/` 不放入单个 session 目录，因为它们有跨会话或父子 agent 共享语义。
- `.onecode/` 是受保护项目目录，工具访问必须继续经过 guard 和 permission policy。

## 推荐目录结构

```text
.onecode/
  settings.json
  ONECODE.md
  rules/
    *.md

  sessions/
    index.jsonl
    active.json
    <session-id>/
      session.json
      messages.jsonl
      trace.jsonl
      errors.jsonl
      session-memory.md
      tool-results/
        <result-id>.txt
      background-tasks/
        <task-id>.output
      file-history/
        <file-snapshot-id>
      env.json

  tasks/
    <task-list-id>/
      .highwatermark
      <task-id>.json

  memory/
    MEMORY.md
    <topic>.md

  plans/
    <plan-id>.md

  cache/
  paste-cache/
  shell-snapshots/
```

## 顶层职责

| 路径 | 职责 |
|:---|:---|
| `settings.json` | 项目级设置，例如权限规则、MCP trust、本地运行偏好。 |
| `ONECODE.md` | 项目级指令记忆，参与 instruction memory 分层加载。 |
| `rules/*.md` | 项目级规则片段，按 instruction memory 规则加载。 |
| `sessions/` | 所有可恢复会话的事实来源和会话局部 artifacts。 |
| `tasks/` | durable task graph。以 `task_list_id` 分组，可被父子 agent 或多个 session 共享。 |
| `memory/` | 项目级长期记忆，跨 session 注入和更新。 |
| `plans/` | 运行时临时计划或用户保存的计划，不替代仓库内 `docs/exec-plans/`。 |
| `cache/` | 可删除缓存。不得作为恢复事实来源。 |
| `paste-cache/` | 大段粘贴内容缓存。应可由引用重新读取或安全失效。 |
| `shell-snapshots/` | shell 启动状态快照。用于诊断或后续 shell 恢复能力。 |

## Session 目录

每个用户可恢复会话使用一个目录：

```text
.onecode/sessions/<session-id>/
```

该目录内的文件共享同一个 session 生命周期。删除该目录意味着删除该会话的 transcript、trace、错误日志、session memory、大工具结果和后台任务输出。

| 文件或目录 | 职责 |
|:---|:---|
| `session.json` | 会话元信息，例如 `session_id`、`created_at`、`updated_at`、`cwd`、`model`、`provider`、`task_list_id`、恢复状态。 |
| `messages.jsonl` | 完整 transcript，恢复模型上下文的事实来源。 |
| `trace.jsonl` | 结构化 runtime trace，只记录短小事件和 span，不记录完整 prompt 或工具输出。 |
| `errors.jsonl` | 不可恢复错误和调试证据，和 trace 分离。 |
| `session-memory.md` | 单 session 压缩连续性记忆。不是跨会话长期记忆。 |
| `tool-results/` | 大工具结果外置内容，由 transcript、compaction 和 executor 共享引用。 |
| `background-tasks/` | 当前 session 启动的后台 bash、agent 或 dream 输出。 |
| `file-history/` | 本 session 文件编辑前快照，用于 diff、恢复或审计。 |
| `env.json` | 会话启动环境快照。应脱敏，不保存 API key、token 或完整敏感 env。 |

### `messages.jsonl`

`messages.jsonl` 是恢复会话的主事实来源。每条 record 应包含稳定的 `uuid`、`parent_uuid`、`session_id`、`timestamp`、`cwd` 和内部 message payload。内部 message role 保持 provider-neutral，例如 `user`、`assistant`、`tool_result`、`attachment`。

大工具结果不应直接无限写入 JSONL。超过预算的 `tool_result` 内容写入 `tool-results/`，JSONL 中保留引用、预览和恢复 metadata。

### `session.json`

`session.json` 是轻量 metadata，不替代 transcript。建议字段：

```json
{
  "version": 1,
  "session_id": "<session-id>",
  "created_at": "2026-06-19T00:00:00Z",
  "updated_at": "2026-06-19T00:00:00Z",
  "cwd": "D:\\study\\OneCode",
  "model": "provider/model",
  "provider": "provider-name",
  "title": "first user prompt preview",
  "task_list_id": "<task-list-id>",
  "status": "active"
}
```

`session.json` 可以帮助 `/resume` 快速展示列表；如果它缺失或损坏，CLI 应能从 `messages.jsonl` 重建基本摘要。

## Session 索引

`sessions/index.jsonl` 是可重建索引，服务快速列表、排序和搜索。它不是恢复事实来源。

推荐每行包含：

```json
{"session_id":"...","title":"...","updated_at":"...","message_count":12,"path":"sessions/.../messages.jsonl"}
```

索引可以在以下时机更新：

- 新 session 创建。
- transcript flush 后更新 `updated_at` 和 `message_count`。
- `/resume` 扫描发现索引缺失或陈旧时重建。

`sessions/active.json` 可记录当前或最近 session 指针，便于 `/continue`、崩溃恢复或 UI 展示。它同样不是事实来源。

## Tasks 目录

`tasks/` 使用 `task_list_id` 分组，而不是 `session_id`：

```text
.onecode/tasks/<task-list-id>/<task-id>.json
```

原因：

- 父子 agent 需要共享同一 task graph。
- `ONECODE_TASK_LIST_ID` 可以让多个 runtime 显式共享任务。
- 未来跨会话恢复时，一个长期任务可能继续使用已有 task list。

session 与 task list 的关联应记录在 `sessions/<session-id>/session.json` 和 runtime metadata 中。默认情况下，`task_list_id` 可以等于 `session_id`，但存储结构不应假设二者永远相同。

## Memory 目录

`memory/` 是项目级长期记忆：

```text
.onecode/memory/MEMORY.md
.onecode/memory/<topic>.md
```

它服务跨会话的项目事实、用户偏好、反馈和参考材料。它不属于某个 session，不能放进 `sessions/<session-id>/`。

与之相对，`sessions/<session-id>/session-memory.md` 只服务单会话压缩后的连续性。两者名称相似但生命周期和注入方式不同。

## Plans 目录

`.onecode/plans/` 可用于运行时保存的临时计划、用户手动保存的计划或 future plan-mode 输出。

仓库级架构和执行计划仍放在：

```text
docs/exec-plans/active/
docs/exec-plans/completed/
```

`.onecode/plans/` 不应替代项目文档中的 ExecPlan。若一个计划代表真实工程变更，应提升为 `docs/exec-plans/active/` 中的 ExecPlan。

## Global 与 Project 边界

项目 `.onecode/` 只保存当前 workspace 的状态。用户级状态属于：

```text
~/.onecode/
  settings.json
  ONECODE.md
  rules/
  skills/
  plugins/
  cache/
```

用户级目录可以承载全局配置、全局指令、用户 skills、插件和跨项目缓存。项目 `.onecode/` 不应通过 `projects/<project-key>/` 再嵌套项目，因为项目隔离已经由 workspace-local `.onecode/` 提供。

如果未来需要全局会话索引，可在 `~/.onecode/projects/<project-key>/` 建立到项目 `.onecode/sessions/` 的索引或引用，但不要把项目 transcript 的唯一事实来源迁移到用户级目录。

## 当前实现迁移说明

目标 session 结构为：

```text
.onecode/sessions/<session-id>/
```

迁移直接切换到新结构，不保留旧会话记录，不双读旧目录，也不提供自动迁移命令。实现应通过集中路径 helper 完成，避免在各模块散落字符串拼接。优先改造这些入口：

- `JsonlTranscriptStore` 的 session root 解析。
- trace 和 error log sink 的 session path。
- `ToolResultStorage` 的 session dir。
- `SessionMemoryStore` 的 session dir。
- background task output path。
- CLI resume 扫描和 target 解析。
- subagent transcript root。

`/resume` 只扫描：

```text
.onecode/sessions/*/messages.jsonl
```

旧 `.onecode/<session-id>/messages.jsonl` 不再作为合法恢复目标。

## 清理策略

清理必须按生命周期分层：

- 删除某个 session：只删除 `sessions/<session-id>/`，不删除 `tasks/` 或 `memory/`。
- 清理缓存：可删除 `cache/`、`paste-cache/` 中可重建内容。
- 清理长期记忆：必须显式修改 `memory/`，不能作为 session cleanup 的副作用。
- 清理任务：按 `task_list_id` 操作，不能因为 session 被删除而默认删除共享 task graph。

## 安全边界

`.onecode/` 是受保护目录。默认行为：

- 读取 `sessions/<session-id>/tool-results/` 可作为恢复和上下文治理例外。
- 写入 `memory/` 只应由明确授权的长期记忆流程或用户请求触发。
- 写入 `settings.json` 只应由显式配置命令触发。
- 写入 session-local 文件由 runtime 自身负责，普通工具调用不应随意修改 transcript、trace 或 errors。

任何例外都应进入 permission policy，而不是由工具绕过 guard。
