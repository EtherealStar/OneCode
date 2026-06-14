# Tool Design Guidelines

本文定义 OneCode 新增工具时应遵守的 metadata、schema、权限目标、结果记录和测试约定。它是设计约定，不是某个工具的实现计划。具体实现步骤需要写入 `docs/exec-plans/active/` 中的 ExecPlan。

## 目标

OneCode 的工具不应只是一个函数。工具 descriptor 必须携带足够的结构化信息，让 runtime 可以动态组装模型可见 schema、注入工具 prompt、执行输入校验、做 deny-first 权限裁剪、分批并发执行、治理大结果、记录 trace，并为后续 UI 或 SDK 暴露稳定数据。

新增工具不得要求修改 `core/loop.py`。工具能力必须通过 `ToolRegistry`、`RegistryToolExecutor`、guard、hook、prompt assembler 和 context/result services 接入。

## 基本原则

1. Descriptor 是工具的唯一事实来源。模型可见 schema、工具 prompt、权限目标、并发分类、结果预算和测试断言都应从 descriptor 或 descriptor 派生结构读取。
2. 工具分类必须 input-aware。只读、写入、并发安全、权限目标和结果预算都必须基于本次调用输入判断。
3. Deny 优先于动态组装、hook、ask 和 allow。被 blanket deny 的工具不得进入模型 schema 或工具 prompt；执行入口仍必须重复校验。
4. Guard 目标必须抽象为统一 target，不保留单路径工具接口。单文件读写只是 `ToolTarget` 的一种特例。
5. Prompt 和 schema 分离。`description` 是 provider schema 的短描述，`tools/<name>/prompt.py` 是 system prompt 中给模型的使用规则。
6. 结果治理是工具契约的一部分。工具必须声明大结果处理策略，runtime 负责统一持久化、预览和引用。
7. 工具名称使用 snake_case。Provider-visible name、registry key、目录名和测试 fixture 应保持一致，例如 `read_file`、`edit_file`、`bash`。

## 工具目录约定

每个内置工具使用一个顶层目录：

```text
tools/<tool_name>/
  __init__.py
  tool.py
  prompt.py
```

`tool.py` 导出 `descriptor()`。`prompt.py` 导出该工具给模型看的使用说明。工具实现不得 import `core/loop.py`，也不得自行绕过 `services/guard/`。

## Descriptor 目标形态

目标 descriptor 应表达工具定义、输入输出、prompt、调用分类和执行入口：

```python
ToolDescriptor(
    name="read_file",
    description="Read a UTF-8 text file from the local filesystem.",
    input_schema={...},
    output_schema={...},
    prompt=PROMPT,
    search_hint="read local text files",
    classify_input=classify_input,
    validate_input=validate_input,
    handler=handle,
)
```

字段约定：

- `name`: snake_case，唯一，作为 registry key 和 provider-visible function name。
- `description`: 一句话短描述，只进入 provider tool schema，不承载复杂使用规则。
- `input_schema`: JSON Schema object，必须关闭不需要的 `additionalProperties`。
- `output_schema`: OneCode 内部工具结果对象 schema；模型可见内容仍由 provider adapter 或 result projector 转换。
- `prompt`: 工具使用规则、约束和必要示例，由 prompt assembler 从 registry 汇总。
- `search_hint`: 未来 deferred tool search 的短能力提示，3 到 10 个词，不能替代 description。
- `classify_input`: 根据本次 input 返回 `ToolCallClassification`。
- `validate_input`: 工具级值校验，负责 JSON Schema 无法表达的规则。
- `handler`: 执行工具，返回结构化工具结果。

不再使用工具级 `read_only`、`modifies_filesystem`、`requires_guard`、`concurrency_safe`、`get_path` 作为权威字段。

## Input-Aware Classification

每次工具调用在通过 JSON Schema 结构校验后，executor 必须调用 `classify_input()` 得到本次调用分类：

```python
ToolCallClassification(
    read_only=True,
    modifies_filesystem=False,
    concurrency_safe=True,
    targets=(ToolTarget(...),),
    result_policy=ToolResultPolicy(...),
    permission_subject="read_file:src/app.py",
)
```

字段语义：

- `read_only`: 本次调用是否只读取状态。
- `modifies_filesystem`: 本次调用是否会写入、创建、删除或移动文件系统内容。
- `concurrency_safe`: 本次调用是否可与相邻的并发安全调用并行执行。
- `targets`: 本次调用触达的资源集合。
- `result_policy`: 本次调用的结果预算和持久化策略。
- `permission_subject`: 面向权限规则、hook pattern 或审计日志的紧凑表达。

分类失败必须 fail closed：按非只读、会修改、不可并发、需要权限处理。

## ToolTarget

所有 guard 和权限判断都基于 `ToolTarget`。不再保留 `get_path()` 单路径方式。

```python
ToolTarget(
    kind="file",
    operation="read",
    value="src/app.py",
)
```

建议字段：

- `kind`: `file`、`directory`、`glob`、`command`、`url`、`session_state`、`external_service`。
- `operation`: `read`、`write`、`execute`、`network`、`ask_user`、`mutate_state`。
- `value`: 原始目标值，例如路径、命令、URL 或状态 key。
- `normalized_value`: guard 或 resolver 解析后的稳定值，可由 runtime 填充。
- `metadata`: 可选扩展信息，例如 glob pattern、shell、HTTP method。

示例：

- `read_file`: 一个 `file/read` target。
- `edit_file`: 一个 `file/write` target。
- `grep`: 一个 `glob/read` 或 `directory/read` target。
- `copy_file`: 一个 `file/read` target 和一个 `file/write` target。
- `bash`: 一个 `command/execute` target，必要时由 shell parser 派生额外 file targets。

## Result Policy

工具结果预算由 `ToolResultPolicy` 描述：

```python
ToolResultPolicy(
    max_result_size_chars=math.inf,
    persist_when_exceeded=False,
    preview_chars=4000,
)
```

默认约定：

- `read_file`: `max_result_size_chars=math.inf`。它通过 `offset`、`limit` 等参数自限流，不进入二次持久化，避免 read -> persist -> read 的循环。
- `bash` / `powershell`: 输出超过 30KB 时持久化到结果文件，模型看到预览和引用。
- `grep`: 输出超过 20KB 时持久化到单独结果文件，模型看到预览和引用。
- 其他工具: 输出超过 50KB 时持久化。

持久化后的模型可见结果必须说明：

- 原输出已被截断或外置。
- 预览内容。
- result file path 或 result id。
- 如何重新读取完整结果。

大结果治理属于 result store / compaction 边界，不应由每个工具手写不同格式。

## Prompt And Schema

工具说明分三层：

1. `description`: provider tool schema 的短描述。
2. `input_schema`: provider 参数结构。
3. `prompt`: system prompt 中的工具使用规则。

Registry 应同时提供：

```python
tool_schemas(state) -> tuple[dict, ...]
tool_prompt_sections(state) -> tuple[str, ...]
```

Prompt assembler 从 registry 读取当前启用且未被 deny 的工具 prompt。被 deny 或 disabled 的工具不得出现在 schema 或 prompt 中。

### Tool Prompt Style

工具 prompt 是模型可见的使用规则，不是工具实现说明、权限系统说明或 schema 字段清单。它的目标是帮助模型判断何时选择该工具、如何给出高质量输入、如何理解结果，以及失败后如何恢复。

工具 prompt 应遵守以下原则：

- 不在 prompt 正文中写 `# Tool: <name>` 标题；`prompts.sections.tool_prompt_sections()` 会统一渲染工具标题。
- 用英文书写，保持与当前 system prompt 和 provider-facing schema 的语言一致。
- 第一段说明工具用途，后续规则聚焦模型调用决策和输入质量。
- 面向模型的工具 prompt 不使用 `sandbox`、`sandboxed` 或“沙箱”等实现边界词；需要描述范围时使用 project、workspace、file、directory 或 path。路径被拒绝时，表述为运行时、guard 或 permission 返回的结果即可。
- 不重复 `input_schema` 已经清楚表达的字段类型；只有字段行为、默认值、前置条件或容易误用的地方才写入 prompt。
- 不把安全边界寄托给 prompt。guard、permission、schema validation 和 handler 仍是执行事实来源；prompt 只提醒模型尊重这些结果。
- 不承诺运行时无法保证的能力。例如工具只读 UTF-8 文本时，不应写支持图片、PDF、notebook 或任意二进制文件。
- 不把通用工具选择规则在每个工具中重复堆叠。跨工具偏好应放在全局 prompt section；单个工具 prompt 只写该工具独有的选择边界。
- 写清楚常见失败后的恢复策略，例如先读取文件、缩小搜索范围、改用路径搜索、重新确认路径或询问用户。
- 保持短小稳定。工具 prompt 是每轮 system prompt 的一部分，应避免产品文案、长篇示例和与当前工具无关的工作流。

推荐模板：

```python
PROMPT = """Purpose:
<One or two sentences explaining what this tool does and when it is the right choice.>

Use when:
- <Concrete trigger for choosing this tool.>
- <Another common trigger, if useful.>

Prefer instead:
- Use `<other_tool>` when <clear boundary>.
- <Omit this section if there is no meaningful alternative.>

Rules:
- <Important calling rule that affects correctness.>
- <Important precondition, default, limitation, or runtime behavior.>
- <Do not restate schema fields unless the behavior is non-obvious.>

Returns:
- <What the model should expect in the result.>
- <Mention pagination, truncation, line numbers, task ids, or structured errors if relevant.>

If it fails:
- <How the model should recover: read first, narrow search, adjust input, ask the user, etc.>
"""
```

简单工具可以省略没有实际内容的小节，但应保持剩余小节顺序稳定。常用精简形态：

```python
PROMPT = """Purpose:
<What this tool does.>

Use when:
- <When to call it.>

Rules:
- <Important usage rule.>

Returns:
- <Result shape.>
"""
```

## Internal Tool Call Record

OneCode 内部应保留 provider-neutral 工具调用记录。Provider adapter 可以将其投影为 OpenAI-compatible、Anthropic-compatible 或其他 wire format。

工具调用记录建议字段：

| 字段 | 来源 | 说明 |
|:---|:---|:---|
| `role` | `"assistant"` | 内部消息角色。 |
| `content[].type` | `"tool_use"` | 内容块类型。 |
| `content[].id` | provider 或 runtime | 工具调用唯一 ID。 |
| `content[].name` | 工具定义 | 工具名称，必须是 snake_case registry name。 |
| `content[].input` | 模型生成 | 工具输入参数。 |
| `uuid` | runtime 自动生成 | 内部消息标识。 |
| `timestamp` | runtime 自动生成 | ISO 8601 时间戳。 |
| `parent_uuid` | 上一条消息 UUID | 消息链。 |

`message.id` 可保留 provider 原始 ID；OneCode 内部链路使用 `uuid`。

## Internal Tool Result Record

工具结果记录建议字段：

| 字段 | 来源 | 说明 |
|:---|:---|:---|
| `role` | `"tool_result"` | OneCode 内部消息角色。Provider adapter 可投影为需要的 wire role。 |
| `tool_call_id` | 对应 `tool_use.id` | 关联到工具请求。 |
| `tool_name` | descriptor name | 工具名称。 |
| `content` | result projector | 模型可见文本结果、预览或引用。 |
| `is_error` | 执行状态 | 出错时为 `true`。 |
| `tool_use_result` | handler result data | 原始工具输出对象，供 UI、trace 或 SDK 使用。 |
| `source_tool_assistant_uuid` | assistant message UUID | 链接到发送工具请求的 assistant 消息。 |
| `metadata` | runtime / tool | 结构化元数据，例如 path、replacement_count、error。 |
| `mcp_meta` | MCP 工具协议 | 仅 SDK 或 MCP 消费，不发给模型。 |
| `is_meta` | runtime 可选 | 标记元消息。 |
| `image_paste_ids` | UI 可选 | 关联粘贴图片。 |
| `timestamp` | runtime 自动生成 | ISO 8601 时间戳。 |
| `uuid` | runtime 自动生成 | 内部消息标识。 |
| `parent_uuid` | 上一条消息 UUID | 消息链。 |

模型可见内容只使用 `content` 和必要的 provider tool result fields。`tool_use_result`、`metadata`、`mcp_meta`、`image_paste_ids` 等内部字段不得直接泄露到模型，除非 result projector 明确选择并格式化。

## Output Schema

`output_schema` 描述 handler 的结构化输出对象，而不是 provider wire message。第一版可以只要求以下通用结构：

```python
{
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "is_error": {"type": "boolean"},
        "metadata": {"type": "object"},
        "data": {"type": "object"},
    },
    "required": ["content", "is_error"],
    "additionalProperties": False,
}
```

具体工具可以收紧 `metadata` 和 `data`。例如 `edit_file` 应声明 `metadata.path` 和 `metadata.replacement_count`。如果某个工具只返回文本，也仍应通过统一 `ToolExecutionResult` 包装。

## Execution Pipeline

Executor 的目标顺序固定为：

```text
1. lookup descriptor
2. validate input_schema
3. validate_input
4. classify_input
5. deny-first registry / permission / guard check for targets
6. run PreToolUse hooks
7. if hooks update input, repeat steps 2-5
8. execute handler
9. apply result_policy and result store projection
10. run PostToolUse or ToolError hooks
11. append normalized tool result record
```

Guard deny 不能被 hook 覆盖。Hook 可以阻断或更新输入，但更新后必须重新校验和重新分类。

## Concurrency

并发调度使用连续分批策略：

```text
[read A, read B, grep X, edit C, read D]
  -> batch 1: read A, read B, grep X 并发
  -> batch 2: edit C 串行
  -> batch 3: read D 并发批次但只有一个调用
```

规则：

- 默认不可并发。
- 只有 `classify_input().concurrency_safe == True` 的调用可以进入并发批次。
- 非并发调用单独成批。
- 批次之间严格按 provider 返回顺序执行。
- 分类异常、schema 异常或未知工具都按不可并发处理。

## Naming

工具名称使用 snake_case，并保持以下位置一致：

- `tools/<tool_name>/`
- `ToolDescriptor.name`
- provider-visible function name
- prompt section 标题
- trace event `tool_name`
- tests fixture

不使用 `Read`、`Edit` 这类产品风格名称。若未来因历史消息兼容需要别名，alias 只能用于 lookup 兼容，不应成为模型可见首选名称。

## Required Tests For New Tools

每个新增工具至少提供以下测试：

1. Registry schema projection 包含正确 name、description 和 input schema。
2. Prompt section 只在工具启用且未被 deny 时出现。
3. Invalid JSON shape 返回 `invalid_tool_input`，不调用 handler。
4. `validate_input` 失败返回结构化 tool error。
5. `classify_input` 返回正确 targets、result policy 和 concurrency safety。
6. Guard deny / ask 不执行 handler，也不触碰目标资源。
7. Hook updated input 后会重新 schema validation、tool validation、classification 和 guard。
8. 成功执行返回 normalized tool result record。
9. Handler exception 被转换为 tool error，不逃出 loop。
10. 大结果超过预算时进入 result store，模型只看到预览和引用。
11. 并发测试覆盖连续 concurrency-safe batch 和非并发工具切分。
12. Provider adapter 测试证明内部 tool result 能投影为目标 provider 的合法 wire format。

测试应使用 `uv run python -m pytest ... -q`。文件系统工具必须使用 pytest temporary directories，不得读取或写入真实项目外路径。
