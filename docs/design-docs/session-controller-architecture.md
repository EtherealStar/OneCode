# SessionController Architecture

状态：目标设计，设计决策已确认，尚未实现。本文定义完整替换交互式 CLI 时的会话 Module，不是执行计划，也不表示当前代码已经满足这些契约。

相关文档：[根架构](../../architecture.md)、[核心设计信念](core-beliefs.md)、[ConversationProjection](conversation-projection-architecture.md)、[ConversationView](conversation-view-architecture.md)、[当前 CLI](cli-architecture.md)、[上下文与存储](context-architecture.md)。

## 定位与职责

SessionController 是应用层的会话 Module。它把用户输入、命令、权限回答和取消操作转成对 OneCode runtime 的调用，并向调用方交付完整会话快照及有序更新。TUI 和非交互 batch 是这个 Interface 的两个 Adapter。

它管理一个当前会话，同一时刻最多有一个前台 turn。这里的 turn 指一次用户输入引起的完整 agent 执行，可以包含多次模型调用和并发工具调用，不等同于 runtime 中某个计数器。

| Module | 拥有的状态 | 不承担的职责 |
|:---|:---|:---|
| SessionController | 当前 runtime、前台执行、输入队列、交互请求、初始化和关闭状态 | 工具卡片、Markdown、消息布局和滚动 |
| ConversationProjection | 可重建的消息展示结构、归属与顺序 | 执行工具、修改存储、权限决策 |
| ConversationView | 视口、展开状态、滚动锚点、缓存和刷新 | 会话调度、历史恢复和文件读取 |

Controller 内部复用 `AgentLoop`、MessageStore、附件收集、权限、计划模式、后台任务和 MCP 等现有能力。工具执行和安全策略仍由原 Module 负责。Controller 不发展成第二个 agent loop。

## Seam 与目录归属

目标结构：

```text
application/
  session.py          # SessionController 与会话生命周期
  types.py            # Interface 的快照、更新、请求与返回类型
  runtime.py          # 从 build_runtime / CliRuntime 提取的装配与重绑定
  commands.py         # 命令注册、分类与业务操作
  interactions.py     # permission / question / trust 等请求的协调
  history.py          # transcript 的用户可见历史读取

ui/
  cli/app.py          # TTY / batch 入口分流
  cli/batch.py        # 文本输入输出 Adapter
  tui/app.py          # Textual 装配、输入和事件转交
```

这些文件是同一个应用 Module 的内部实现划分，不逐个暴露为调用者必须装配的 Interface。生产代码不 import `reference/`。

依赖方向：`ui -> application -> core / services / infrastructure`。`core`、`services`、`infrastructure` 不反向依赖 application 或 Textual。应用层允许装配具体 provider 和工具，但不能把具体能力分支搬进 `core/loop.py`。

## Interface

以下为目标 Python 契约草图，类型名表示语义，不要求提前创建空实现。

```python
class SessionController:
    async def submit(self, text: str) -> SubmissionReceipt: ...
    async def withdraw(self, input_id: str) -> WithdrawalResult: ...
    async def resume_queue(self) -> None: ...
    async def cancel_active(self) -> CancelResult: ...
    async def respond(
        self, request_id: str, answer: InteractionAnswer
    ) -> ResponseResult: ...
    async def load_detail(self, ref: DetailRef) -> DetailResult: ...
    def watch(self) -> AsyncIterator[SessionUpdate]: ...
    async def close(self) -> None: ...
```

运行时工厂、配置和存储依赖在装配时注入。异步上下文管理负责启动及最终 `close()`；进入上下文后即可观察初始化状态，不等待用户回答 trust 才返回。bootstrap 在同一事件循环的独立任务中进行，待回答请求属于快照，因此观察者稍后接入也不会错过它。

| 操作 | 契约 |
|:---|:---|
| `submit` | 返回接收、排队、立即处理或拒绝的回执，不等待完整 agent turn。未就绪时拒绝普通 prompt，但配置和退出操作仍可用。空输入不创建消息。 |
| `withdraw` | 只撤回待执行项，成功时返回原输入供 Composer 编辑；与 worker 领取操作串行，已开始项返回不可撤回。重新提交获得新的 input ID。 |
| `resume_queue` | 显式解除暂停并启动可执行项；不能在关闭、切换或收尾失败状态下启动工作。 |
| `cancel_active` | 取消当前运行并等待收尾，暂停但保留队列；返回时不能仍有该前台 turn 的未受控写入。重复取消不重复保存消息。 |
| `respond` | 按 request ID 和请求类型校验回答；已取消或过期请求返回过期结果，不能回答到后续请求。 |
| `load_detail` | 读取已知、带会话归属的详情引用；小结果可直接返回，大结果按需读取。缺失或读取失败为结构化结果，不终止 agent。 |
| `watch` | 第一项是完整快照，后续为有序更新；关闭迭代器只解除观察，不取消会话。 |
| `close` | 停止接收输入，撤销交互，取消并等待前台任务，整理记录，flush 和关闭资源；可重复调用。 |

业务拒绝通过回执表达。不可恢复的执行或存储错误记录到既有 error log，并发布状态更新；不能把内部异常直接变成未处理的 Textual callback 异常。收尾写盘失败不得报告取消或切换成功，也不得自动执行下一条输入。

## 快照与更新契约

Controller 提供运行事实，不交付 Rich renderable 或 `UiMessage`。

| 数据 | 必须表达的内容 |
|:---|:---|
| `SessionSnapshot` | session ID、会话代次、更新序号、用户可见历史记录、当前运行的未定稿内容与工具状态、队列与暂停状态、当前交互、配置与 usage 等状态 |
| 消息记录 | 稳定 message ID、历史顺序、OneCode 内部消息、必要的 assistant/tool 归属、小结果或详情引用 |
| `SubmissionReceipt` | input ID、所属会话、接收状态及拒绝原因；不能把排队成功解释为模型已收到 |
| `InteractionRequest` | request ID、所属会话/运行、类型、原协议允许的选项与必要展示数据 |
| `DetailRef` | 所属会话、消息/工具身份及存储引用；不是允许 View 任意指定文件路径的入口 |

更新只需覆盖快照替换、已有 `AgentEvent` 的运行进度、消息提交/修正/删除、队列变化、交互变化、状态变化及详情加载。使用明确类型，复用 provider-neutral 的 `AgentEvent`，不另造一套 provider 协议。

快照中的当前草稿和工具状态来自 runtime 的运行事实，不能从 Projection 倒读。这样重新挂载 View 或重新订阅也能恢复进行中的画面。

一致性规则：

1. 在同一个串行状态处理点注册观察者并取得初始快照，保证快照与后续事件没有空隙。
2. 消息更新带会话代次和单调序号。同一代次中有序应用；切换会话发布新代次的完整快照，旧任务的迟到事件不能进入新会话。
3. 高频进度可合并，已接受输入、消息提交、删除、交互请求及终态不能静默丢弃。慢观察者需要重新同步时，用包含当前运行状态的完整快照替换积压，不能只丢文本 delta。
4. 只传不可变值或副本；观察者不能借快照修改 runtime。更新投递不等待 Textual 完成渲染。
5. `tool_result` 执行事件不等于结果已持久化。Controller 必须区分运行进度与消息存储确认，收尾后发布权威修正。

## 队列与命令

前台执行状态和队列暂停状态分开保存。暂停不是一个永远占据 `running` 的假任务。

| 情况 | 行为 |
|:---|:---|
| 空闲且队列未暂停，提交 prompt | 开始前台 turn |
| 正在运行或队列已暂停，提交 prompt | 加入当前会话 FIFO |
| 正常完成 | 自动执行下一项 |
| 用户取消或不可恢复错误 | 暂停队列，保留待执行项 |
| 撤回待执行项 | 移出队列并返回原文，供编辑后重新提交 |
| 切换会话或 `/clear` | 直接丢弃队列，不询问，不迁移到新会话 |

待执行输入不是已提交给模型的 user message，不提前写入对话 transcript。队列由 Controller 管理；Composer 不保存另一份可执行队列。Composer 中尚未提交的草稿属于输入界面。

命令分类由内部注册表按具体 invocation 决定，不能只看命令名。例如 `/permissions` 是查看，`/permissions add ...` 是状态修改。

- 查看类命令立即读取一致快照并返回结构化数据，不等待正在运行的 turn。
- 普通状态修改在安全执行点串行处理，不与模型调用、工具执行和会话重绑定竞争。
- 会话切换、取消和退出属于生命周期控制，不应被排到所有 prompt 后面。选择目标后先阻止新工作，再取消、收尾、丢弃队列并切换。
- `/connect` 可以先收集配置，但模型替换在串行执行点进行；配置仍使用 OneCode 的 `.env` 规则。
- `/plan`、计划审批、计划附件和用户澄清沿用已有业务契约。`/plan <描述>` 生成的 prompt 经过同一输入入口，不绕过队列和附件收集。

命令结果表达状态数据、通知或带类型的交互请求，不能携带旧的 `presentation="page"`、`reset_main_view`、`replay_messages` 等终端操作。恢复会话通过新快照替换完成。

## 中断与记录整理

这是 runtime 与消息存储的契约，由 Controller 调用和等待；不交给 Projection 修复文件。

### 已确认规则

- 保留已经向用户输出的半段文字，作为普通 `assistant` 消息保存，参与后续模型上下文。
- 不增加展示专用消息类型、第二份聊天日志或“已中断”标记；不向正文插入中断说明。
- 仅在中断收尾时清理该运行未配对的工具调用及孤立结果。正常执行中允许存在等待结果的调用。
- 已配对的调用与真实结果保留，包括真实的工具失败、权限拒绝等 error result；不能把失败结果当成缺少结果。
- 清理落实到内存链和实际 transcript。不能只在 UI 隐藏，也不能只追加一份修正版而留下原始未配对消息记录。
- 不为缺失结果补造 `interrupted_tool_call`，不为了配对伪造工具执行事实。

### 收尾顺序

1. 暂停队列并阻止新的前台 turn，撤销该运行的待回答交互。
2. 取消并等待模型消费、工具执行和该运行的写入者；先确定已完成结果，再冻结本次收尾输入。
3. 使用 runtime 持有的当前 assistant 文本与真实工具结果按调用归属配对。已完成但尚未批量追加的结果也应参与，不能只看磁盘是否已有结果。
4. 保留文字和已配对调用/结果，删除未配对部分；已有 assistant 用相同身份修正，尚未保存的文字只保存一次。删除后无正文、无工具的空消息不保留。
5. 由消息存储在串行写入保护下同步修正内存与 transcript。对于已落盘记录，使用受控重写，保持未受影响记录的 UUID、顺序及内容，修复因删除产生的 `parent_uuid` 引用；更新内存最后节点及待 flush 缓冲，防止旧 timer 再次写回被删除记录。
6. 在同目录暂存完整的新 transcript 并原子替换正式文件，成功后发布消息修正和最终运行状态。失败保留可恢复文件并停止继续执行，不把半次重写作为成功状态。

普通追加及 compaction 仍保留现有语义；受控重写是中断记录整理的明确例外。不能借此用 active chain 覆盖整个 transcript，否则会删除压缩前历史。存储层负责维护同一记录的历史来源映射以及删除涉及的引用；Controller 不手工拼 JSONL。

原始 trace/error log 继续记录运行诊断，但不充当对话消息恢复来源。工具已产生的文件修改不会因删除消息而回滚。取消不自动重跑没有结果的调用。

正常取消、会话切换或可捕获错误都执行上述收尾；强制杀进程无法保证保存尚在内存的最后文字。下一次恢复对磁盘上的未配对记录执行相同的清理规则，不合成结果，也不承诺找回从未写盘的 token。

## 历史读取与详情

聊天历史与模型 active chain 分别读取。压缩后仍展示压缩前的用户可见消息，恢复后也保持完整历史；压缩产物、被复制进新活动链的旧消息和内部 attachment 不能作为重复聊天追加。

`application/history.py` 使用 transcript 的稳定记录身份、来源和分支语义生成历史记录。现有 compaction 会以新 UUID 重写活动链，实施时需要保留原消息来源关系，不能依靠文本相等去重，也不能简单拼接所有 JSONL message。真正重复发送的相同文字仍是两条消息。

消息快照默认携带工具结果预览与引用。当前 `load_messages()` 会恢复完整外置结果，需要为历史浏览提供保留引用的读取方式；不能先把所有 artifact 读入再声称按需加载。模型恢复是否读回完整结果由上下文契约决定，不由 TUI 决定。

详情加载结果带原会话代次和工具身份，通过更新交给 Projection。切换后到达的结果不会污染新会话。缺失 artifact 显示可解释的读取状态，已有摘要仍保留。

## 交互与应用生命周期

权限、用户澄清、MCP trust 及计划审批由原协议的 Adapter 接入同一个请求协调实现。每次只展示一个请求，其他请求按到达顺序等待，不叠加弹窗。不同请求仍有不同的回答类型和取消语义，不压成一个通用布尔值。

只把底层允许询问的选项交给 UI。guard deny 不能进入可放行弹窗；用户回答不覆盖 deny-first。取消运行时，所有相关等待者都得到取消结果，迟到回答失效。启动期 trust 使用启动归属，不虚构一个 agent turn。

TUI 先挂载，再异步初始化 runtime 和连接已信任 MCP；未配置时仍能进入 `/connect`。整个生命周期在同一应用事件循环内管理，不能在 Textual callback 中使用 `asyncio.run()` 或 `thread.join()` 等待异步业务。

会话切换前必须完成旧前台 turn 的收尾，成功后统一重绑 MessageStore、compaction、session memory、trace、result store、subagent parent context 等依赖，再发布新快照。已有后台任务仍遵循 BackgroundTaskManager 的归属和关闭规则；不会因为切换 UI 自动变成新会话的任务。

## 当前实现与参考代码的关系

| 来源 | 目标处理 |
|:---|:---|
| `ui/cli/app.py::build_runtime`、`types.py::CliRuntime` | 提取为内部 runtime 装配和重绑定 |
| `terminal/repl.py` 的队列、附件、命令与 shutdown | 收敛到 Controller |
| `commands.py::_run_async_blocking` | 用同事件循环 async 操作替代 |
| `app.py::BatchUserQuestionPrompter`、同步 MCP trust 回调 | 按 TUI/batch Adapter 注入，不在 TUI 内读 stdin |
| `reference/ui/session_facade.py` | 借鉴单 worker 与快照观察设计，不引入其 SessionEngine、journal 或 repository |
| `core/loop.py` 的 assistant 先存、tool result 后批量存 | 增强运行事实与收尾契约，保留完成结果和稳定消息归属 |
| `services/context/recovery.py::_synthetic_interrupted_tool_result` | 目标恢复路径删除此策略，使用真实记录清理 |

当前活跃的 [计划模式执行计划](../exec-plans/active/plan-mode-full-implementation.md) 涉及命令、附件和问答，TUI 替换必须保留这些业务行为。[技术债](../tech-debt/tech-debt-tracker.md) 中 TD-007、TD-016 的 UI 方向可由本设计承接，但文档完成不代表债务已经解决。

## 验证契约

通过 Controller 的 Interface 验证，不依赖 Textual：

- 同时提交多条输入只产生一个前台 worker；取消暂停、正常完成 drain、撤回与领取竞争都有确定结果。
- 运行中查看状态立即返回；修改命令不与运行竞争；切换直接丢弃队列且不出现确认请求。
- 快照订阅无空隙；重新观察可恢复草稿与 pending interaction；旧代次更新及详情不污染新会话。
- 中断发生在纯文本、工具执行、结果已完成未批量保存等阶段时，普通 assistant 不重复，真实配对保留。
- 直接读取整理后的 JSONL 以及再次恢复，两处都不存在该中断运行的未配对调用/结果，不出现合成结果。
- transcript 重写与 flush 竞争、写入失败、重复收尾不破坏其他历史和 parent 链。
- compact 后历史完整且不重复，模型仍使用活动链；历史列表不读取全部外置详情。
- 并发权限请求逐个展示，取消唤醒所有等待者，deny 不能被回答覆盖。
- close 等待真实清理、flush 和资源关闭；TUI 与 batch 通过同一会话契约执行。
