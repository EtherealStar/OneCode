# ConversationProjection Architecture

状态：目标设计，设计决策已确认，尚未实现。本文描述消息展示投影，与模型上下文的 `services/context/projector.py` 是不同 Module。

相关文档：[根架构](../../architecture.md)、[SessionController](session-controller-architecture.md)、[ConversationView](conversation-view-architecture.md)、[当前消息渲染](cli-message-rendering-architecture.md)。

## 定位

ConversationProjection 把 Controller 交付的历史快照与运行更新转换为统一、可重建的消息展示结构。它不读文件、不写 transcript、不执行工具，也不决定运行是否取消。

实现直接以 [参考 `UiProjection`](../../reference/ui/projection.py) 为基础移植和适配，不围绕旧 `CliStreamUiState` 再实现一套 reducer。保留参考代码的 `replace`、`apply`、`_ingest`、`_merge_tool_results` 和 `_upsert` 组织方式，替换其领域类型与不符合本项目决策的行为。

这个 Module 的 Depth 来自：调用方不需要理解历史与流式形态差异、工具结果归属、草稿定稿和中断修正，只需依次交付快照与更新并读取变化集。

## 目标文件与依赖

```text
ui/tui/
  projection.py       # ConversationProjection，适配自参考 UiProjection
  projection_types.py # UiMessage、UiPart、ViewChange 等展示类型
```

小型类型可先与实现同文件，文件拆分不增加额外公开 Module。依赖 application 的会话数据契约和 OneCode provider-neutral 类型，不依赖 Textual widget、Rich renderer、具体 provider 或具体工具目录。

`SessionSnapshot` 属于 application 的会话 Interface；不能照搬参考实现，把它定义在 UI 后让 SessionController 反向 import UI。

## Interface

```python
class ConversationProjection:
    def replace(self, snapshot: SessionSnapshot) -> ViewChange: ...
    def apply(self, update: SessionUpdate) -> ViewChange: ...

    @property
    def messages(self) -> tuple[UiMessage, ...]: ...
```

快照替换和增量更新都是同步、确定性的内存操作，不做 I/O。`messages` 和其他投影状态以只读值提供。会话、队列、状态及当前交互可提供只读投影，写入仍只经过这两个入口。

`replace` 从权威快照重建投影，不需要调用方先 `clear` 再逐条回放。`apply` 处理同会话代次的有序更新；完整快照更新统一转入 `replace`。不同来源最终复用相同的消息归并实现。

`ViewChange` 表达：

- 受影响的 message IDs，包括更新与删除。
- 是否改变消息序列结构。
- 队列、状态、交互等非消息部分是否变化。
- 是否发生会话重置，以及是否需要立即展示终态或交互变化。

这些是语义变化提示，不包含像素高度、ANSI、滚动坐标或刷新 timer。View 可以内部合并多次变化，不能跳过 Projection 对必要更新的应用。

## 展示数据与稳定身份

| 类型 | 语义 |
|:---|:---|
| `UiMessage` | 稳定 message ID、role、按顺序排列的 parts、当前是否为草稿等内部展示状态 |
| `UiPart` | 正文、工具调用及归并结果、附件摘要等；工具可携带预览、详情引用、执行状态和真实错误标志 |
| 工具键 | session ID、assistant 调用身份、tool call ID 的组合，防止不同调用复用工具 ID 时串结果 |
| `ViewChange` | 局部更新所需的身份集合和结构变化提示 |

不用参考项目的 UUID/Part 层级重写 OneCode 的消息模型。目标展示类型只保留渲染真正需要的数据，原消息和 provider wire format 不暴露给 View。

身份规则：

1. 每次 assistant 模型调用首次出现时建立位置；没有文本、只有工具声明时也建立同一 assistant 容器。
2. `assistant_call_id` 和 `model_turn_index` 用于实时归属，不能假设它们就是 transcript UUID。
3. runtime/application 负责提供草稿到持久化 message ID 的稳定关联。定稿后更新原位置，不能通过“删掉草稿、尾部追加定稿”改变顺序。
4. 历史使用持久化记录 ID 和来源关系；旧记录缺少调用标识时，由历史读取按实际声明和结果范围建立关联，不根据文本内容生成身份。
5. 详情加载、结果更新、定稿及收尾修正均沿用同一工具和消息身份。

参考 `_upsert` 的“已存在则原位替换”语义是这里的核心。可按需要增加 ID 索引，但不能让缓存中的 widget 身份成为消息事实来源。

## 实时更新规则

| 输入事实 | 投影行为 |
|:---|:---|
| 用户输入进入队列 | 更新待执行区；不能提前变成已提交 user message |
| 撤回输入或切换时丢弃 | 移除待执行项 |
| 用户消息正式提交 | 以稳定身份进入聊天序列 |
| assistant 首次 delta 或工具声明 | 创建一次 assistant 草稿位置 |
| `assistant_delta` | 向该消息正文追加文字，保留原位置 |
| `tool_call_ready` | 在对应 assistant 内按声明顺序建立工具 part |
| `tool_started` / `tool_progress` | 原位更新工具状态，正常运行中无需等待结果才展示 |
| `tool_result` | 按复合工具键归并到声明位置，立即显示真实结果预览 |
| assistant 定稿 | 用最终正文和声明核对草稿，不把全文再次追加；已收到结果按键保留 |
| 消息持久化确认 | 更新身份关联及已保存事实，不重复渲染 |
| 中断收尾修正 | 按权威消息修正/删除工具 parts 和消息；保留普通 assistant 文字，不添加中断标记 |
| transition / usage / 任务变化 | 更新状态，不重新排序聊天消息 |
| 详情加载完成或失败 | 原位更新该工具的详情数据或读取状态 |

tool result 先于整批持久化时可以展示，但不能因此断言它已保存；中断最终以 runtime 收尾结果为准。

已确认的普通 assistant 半段文字在投影中与其他已保存正文相同，不使用 `interrupted` lifecycle 或 `UiRunNotice` 给它添加标签。不可恢复错误可以有独立的错误呈现，不能把错误文字或状态混进 assistant 正文。

## 固定时间线与工具配对

一次 assistant 声明工具 A、B，B 先完成时：

```text
assistant（固定位置）
  正文
  工具 A：运行中
  工具 B：已完成，可展开结果
```

B 的结果立即更新 B，A 完成后更新 A。不按完成时间排序，也不要求 B 等 A 才显示。删除旧的 static commit 等待机制，不保留 `pending_static_commits` 或用于串行打印的结果释放队列。

工具结果本身不再作为一条新消息追加到时间线末尾。参考 `_merge_tool_results` 的原位合并方式保留；其“无法配对就长期保留独立 tool 消息”的 fallback 不能用于本项目中断后的历史。

只在运行中存在的短暂不完整状态与中断最终状态必须区别处理：正常运行允许等待结果的工具卡片；中断完成后不保留未配对调用或结果。若事件到达时暂时缺少声明，可按归属暂存等待后续权威消息；不能随意匹配最近的 assistant，也不能把孤立结果变成永久聊天。

当 Controller 发布收尾后的消息修正时，Projection 同步移除未配对部分。真实配对包括成功结果和错误结果。Projection 的过滤是展示一致性处理，不能代替实际 transcript 清理。

## 历史、压缩和重新同步

`replace(snapshot)` 消费 Controller 的用户可见完整历史，不直接消费模型 active chain。压缩不让历史消息消失，也不把 compact summary、复制消息或内部提示作为重复聊天展示。

历史与实时必须得到同样的最终消息树：同样的 assistant 顺序、工具声明顺序、工具归并、正文和附件摘要。展开与滚动属于 View，因此不要求快照恢复这些本地浏览状态。

同会话的权威修正保留已有 message IDs；View 据此保留阅读位置。真正切换会话使用新的代次，View 清理旧缓存。Controller 的更新序号用于避免重复应用 delta；出现需要重新同步的情况时消费完整快照，不解析 trace 文本补消息。

所有丢弃规则都以既定消息语义为依据。不能为了“恢复看起来一致”按正文字符串去重，也不能把模型上下文裁剪结果当作完整聊天历史。

## 工具、附件和辅助状态

Projection 保留工具名、输入摘要所需的数据、结果预览、真实错误状态和详情引用。具体工具的摘要排版由 View 内部的 presenter registry 完成；Projection 不导入具体工具的输入模型。

附件摘要消费 OneCode `services/attachments` 提供的结构化事实，不重新解析 `@` 输入或读取目录。参考 workspace reference 类型按 OneCode 的附件契约适配，不顺带移植参考项目的引用存储系统。

参考 todo、reasoning、usage 展示按实际 OneCode 数据适配。OneCode 未提供独立 reasoning 事件时不从正文推断 reasoning；任务状态来自已有任务能力，不引入参考 `TodoStore` 作为第二个事实来源。

队列和 pending interaction 由 Controller 决定，Projection 只形成可读状态。展开状态、Markdown 解析结果和工具详情是否展开不进入这个 Module。

## 参考代码移植要求

| 参考实现 | 目标处理 |
|:---|:---|
| `UiProjection.replace/apply` | 作为主要实现基础，适配 OneCode snapshot/update |
| `_ingest/_upsert` | 保留统一入口及原位更新，补稳定身份关联 |
| `_merge_tool_results` | 保留原位归并，工具键加入调用归属，适配中断后的无孤立记录规则 |
| `Message/Part/Role/RunOutcome` imports | 替换为 OneCode/application 契约，不移植参考 runtime |
| `SessionSnapshot` 定义在 projection | 迁到 application 契约，保持依赖单向 |
| `InputQueued` 直接进入消息列表 | 适配为独立待执行区，正式执行后才进入聊天历史 |
| `_RUN_NOTICE_TEXT` 的取消/中断提示 | 不用于给保存的半段 assistant 添加中断标记 |
| 原 CLI reducer / checkpoint 状态 | 由新投影替换，不维持两套消息归并逻辑 |

## 验证契约

通过 `replace/apply` 验证，不启动终端：

- 同一会话的实时执行与历史快照生成相同的最终消息结构。
- 多轮 assistant 的位置固定；工具乱序完成只更新原 part，不新增独立结果消息。
- 只有工具、没有文本的 assistant 也能建立正确归属；相同 tool ID 在不同模型调用中不串结果。
- 定稿全文不与 delta 重复，迟到定稿不清空已经归并的真实结果。
- 正常运行显示待完成工具；中断收尾后仅保留正文与真实配对，没有中断标签或合成结果。
- 撤回、丢弃队列不改写历史；输入正式提交只出现一次。
- 同会话修正返回正确 dirty IDs；切换、重复更新和旧代次结果不会污染当前投影。
- artifact 缺失、附件摘要、usage 及 pending interaction 变化可被 View 正确感知。
- Projection 不依赖 Textual、文件系统写入、具体 provider 或旧 CLI reducer。
