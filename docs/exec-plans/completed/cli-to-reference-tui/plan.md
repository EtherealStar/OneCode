# 将交互式 CLI 迁移为参考 Textual TUI

## Purpose

用户仍通过 `uv run python -m ui.cli.app` 启动 OneCode，但交互终端进入参考 UI 的全屏对话界面：在同一屏幕输入、查看流式 Markdown、浏览历史、展开工具结果和回答权限请求。并发工具始终留在声明位置，先完成的工具立即显示结果；向上阅读历史时新输出不抢滚动位置。退出恢复原终端，不重放整段聊天。管道输入继续使用纯文本 batch。

本文件是执行计划包入口，依照 [PLANS.md](../../../../PLANS.md) 维护。宏观范围和验收在本文件；实施批次及恢复步骤见 [execution.md](execution.md)，执行决策见 [decisions.md](decisions.md)，实际状态及证据见 [progress.md](progress.md)。计划编写完成不代表迁移已实现。

## Scope

迁移包括应用层 SessionController、运行事实及中断持久化、完整历史读取、ConversationProjection、ConversationView、Composer、状态与命令视图、权限/问答/trust/连接/恢复 Modal，以及 TTY 与 batch 入口收敛。Modal 指 Textual 同一应用中的模态面板，不是另一套终端应用。保留附件、计划模式、会话恢复、MCP、后台任务和现有命令业务。

最终只有一条 TTY 展示路径。过渡期间旧入口可以运行，但在最终切换后删除内联 REPL、静态 checkpoint、结果释放队列和 transient 界面。batch 可以保留纯文本 renderer，不依赖 Textual 或旧交互终端栈。

不移植参考项目的 SessionEngine、journal、repository、provider 配置体系、TodoStore、工具实现或独立引用存储；不新增 Web UI、远程会话协议或聊天导出能力。参考中存在但 OneCode 没有结构化数据的 reasoning 等功能不伪造。工具已经产生的文件副作用不随取消回滚。

## Design References

[根架构的 TUI 目标](../../../../architecture.md#tui-替换的目标设计)规定 `ui -> application -> core / services / infrastructure`；[核心信念](../../../design-docs/core-beliefs.md)规定薄 loop 与 deny-first。三个目标设计优先于旧 CLI 文档中仅描述当前实现的终端规则。

[SessionController Interface](../../../design-docs/session-controller-architecture.md#interface)、[快照与更新](../../../design-docs/session-controller-architecture.md#快照与更新契约)、[中断整理](../../../design-docs/session-controller-architecture.md#中断与记录整理)、[历史读取](../../../design-docs/session-controller-architecture.md#历史读取与详情)分别约束应用入口、观察一致性、受控重写及历史来源。[Projection 身份](../../../design-docs/conversation-projection-architecture.md#展示数据与稳定身份)及[实时更新](../../../design-docs/conversation-projection-architecture.md#实时更新规则)规定历史与流式共享消息树。[View Interface](../../../design-docs/conversation-view-architecture.md#interface)、[滚动](../../../design-docs/conversation-view-architecture.md#滚动规则)及[Markdown 刷新](../../../design-docs/conversation-view-architecture.md#markdown-与刷新)规定视口内部职责。

现状对照见 [CLI](../../../design-docs/cli-architecture.md)、[CLI 消息渲染](../../../design-docs/cli-message-rendering-architecture.md)、[上下文存储](../../../design-docs/context-architecture.md)、[压缩](../../../design-docs/compaction-architecture.md)。集成约束见 [权限](../../../design-docs/permission-architecture.md)、[附件](../../../design-docs/attachment-architecture.md)、[MCP](../../../design-docs/mcp-architecture.md)、[后台任务](../../../design-docs/background-task-architecture.md)。[活跃计划模式计划](../plan-mode-full-implementation.md)已有大量完成记录，本次承接现有命令、审批、问答和附件行为，不重新实施其运行时功能。[技术债](../../../tech-debt/tech-debt-tracker.md)中的 TD-007、TD-016 只在取得对应证据后按[维护指南](../../../../tech_debt_tracker_guide.md)更新。

## Context And Orientation

当前 `ui/cli/app.py::main` 根据 stdin/stdout 是否为交互终端分流，TTY 先同步 `build_runtime`，再启动 `terminal/repl.py::InlineRepl`。`ui/cli/types.py::CliRuntime` 持有装配及 `with_session`、`with_model_config` 重绑定；`ui/cli/commands.py` 同时含业务、Rich 输出和 `_run_async_blocking`。`ui/cli/batch.py` 另外直接运行 loop、收集附件并关闭资源。这些重复的会话责任将收敛到 `application/`。

当前实时链为 `AgentEvent -> StreamingCoalescer -> CliStreamUiState -> TerminalOutputCoordinator -> stdout`。工具 B 即使早于 A 完成，也可能等待 A 的 static commit。新链为 Controller 运行事实 → Projection 消息结构 → View 局部更新，无静态打印释放顺序。

存储是关键前置依赖：`services/context/message_store.py::_append` 创建 transcript UUID，但返回消息不带该记录身份；`replace_messages_for_compaction` 会为活动链重新创建 UUID；`transcript.py::load_messages` 会读取全部外置结果；`recovery.py::_sanitize_chain` 会合成 `interrupted_tool_call`。`core/loop.py` 先存 assistant，工具结果事件产生后才整批追加结果。不能仅改 UI 就保证取消后真实历史正确。活动链是下一次模型使用的上下文，聊天历史则包含压缩前记录，两者必须分开读取。

根目录 [reference/ui/](../../../../reference/ui/) 是本次 Python Textual 移植来源；`docs/references/ui/` 是另一套 TypeScript/React 资料。已经检查的参考实现和目标对应关系如下，用于实施定位，而非生产 import 路径：

- `projection.py::UiProjection` 的 `replace/apply/_ingest/_merge_tool_results/_upsert` 移入 `ui/tui/projection.py`；替换参考领域类型，补复合工具键及定稿保留结果。
- `session_facade.py::RuntimeSession` 仅借鉴单 worker；其等待 callback、UI 中定义快照和可丢失通知策略不能直接复制进 Controller。
- `viewport.py::MessageViewport`、`layout_index.py::VirtualLayoutIndex` 移入 `ui/tui/conversation/`，保留 spacer、可见区与 overscan、message ID 锚点及旧滚动回调失效机制。
- `render_cache.py::MarkdownBlockCache` 和 `refresh_scheduler.py::UiRefreshScheduler` 移入 View 内部；参考按空行分块且每次扫描 source，需补跨块 Markdown 正确性验证，不能宣称全部操作为常量时间。
- `app.py`、`composer.py`、`completion.py`、`slash_completion.py`、`workspace_completion.py`、`status_bar.py` 和 `modals/` 提供布局与交互起点；业务依赖换为 OneCode/application。参考 Composer 引用 token 体系不取代 OneCode `@` 附件。
- `renderers/message.py`、`renderers/tool.py`、`theme.py`、`miniagent.tcss` 提供呈现与视觉 token；移除具体参考工具输入类型和 MiniAgent 产品名，使用 OneCode 工具摘要、通用 fallback 及 `onecode.tcss`。

## Interfaces And Dependencies

`application/session.py::SessionController` 提供设计规定的异步 `submit`、`withdraw`、`resume_queue`、`cancel_active`、`respond`、`load_detail`、`close` 和返回异步迭代器的 `watch`。`application/types.py` 拥有 `SessionSnapshot`、`SessionUpdate`、回执、交互及详情类型。`application/runtime.py`、`commands.py`、`interactions.py`、`history.py` 为内部实现，调用者只装配 Controller，不组装内部 worker。

`ui/tui/projection.py::ConversationProjection` 提供同步 `replace(snapshot) -> ViewChange`、`apply(update) -> ViewChange` 和只读 `messages`。`ui/tui/projection_types.py` 定义 `UiMessage`、`UiPart`、`ViewChange`。`ui/tui/conversation/view.py::ConversationView.update(projection, change) -> None` 隐藏布局、缓存、timer；详情请求通过 App 转交 Controller，再沿更新链返回。会话代次用于隔离切换前后的更新，单调序号用于拒绝重复更新；它们不等于 widget 刷新代次。

新增 Textual 运行依赖并更新 `pyproject.toml`、`uv.lock`，Rich 继续保留。版本选择在 M4 以实际 API 兼容性和 headless 验证为门槛并锁定；不凭参考目录猜版本。pytest 测试可继续以 `asyncio.run` 包装异步场景，在测试外层调用它不等于允许 Textual callback 调用它。新增目录、测试文件和接口均为目标产物，当前尚不存在。

## Plan Of Work

依赖顺序为 M1 → M2 → M3 → M4 → M5。先建立可验证运行事实和存储基础，再交付无终端 Controller，随后移植 Projection 与 View，最后完成所有交互并替换入口；不在半成品阶段让新 TUI 成为默认入口。

### M1：取消与历史具有可靠的记录基础

给运行中的 assistant、已完成工具结果和存储记录建立稳定关联，提供受控中断收尾及保留引用的历史读取。取消时保存普通 assistant 半段文字，保留所有真实工具配对，删除未配对记录；内存、原始 JSONL、再次恢复一致。历史保留压缩前内容而模型仍只用活动链。

独立验收：故障注入覆盖结果已完成尚未批量保存、flush 与重写竞争和重写失败；直接解析磁盘 JSONL 验证 UUID、parent 链及无合成结果。相同正文的两次真实发送均保留，压缩复制不重复展示。历史摘要读取不访问外置结果正文。

### M2：TUI 与 batch 共用可独立运行的 Controller

提取装配、命令、队列、交互及关闭责任，使用同一事件循环。Controller 启动后立即可观察初始化，允许 trust 请求经快照呈现。一次只运行一个前台 turn；查看命令即时响应，状态修改在安全点串行执行；切换与取消优先处理生命周期。先把 batch 接到该入口，证明 Controller 不依赖屏幕。

独立验收：不启动 Textual 的集成测试证明 FIFO、取消暂停、显式继续、撤回竞态、请求过期、订阅无空隙、慢订阅者快照重同步和幂等关闭；切换丢弃队列且不会把旧更新带入新会话。batch 成功、失败、EOF 和权限路径继续可用。

### M3：历史与实时投影得到相同消息树

按参考 UiProjection 的组织方式移植，消费 M2 契约。用会话、assistant 调用及 tool call ID 组成工具键；排队区与已提交聊天分离；定稿原位替换，保留已完成结果；收尾按权威修正删除工具。

独立验收：同一场景逐条 apply 与最终 replace 的消息身份、顺序、正文和工具树一致；B 先完成立即更新 B，A 仍运行；同名 tool ID 跨调用不串结果，定稿不重复正文，没有永久孤立工具消息或中断标签。

### M4：参考全屏虚拟视口可浏览长会话

移植可见区挂载、spacer、高度索引、滚动锚点、Markdown 缓存、40ms 刷新合并和工具 presenter，封装在 ConversationView。使用固定快照与可控事件的 Textual 测试 App 验证，不需要真实模型。沿用参考深色背景、语义色、对话区/状态栏/Composer 布局，并适配 OneCode 名称与数据。

独立验收：5,000 条历史只挂载可见范围及 overscan；浏览中收到输出不跳底，展开/resize/锚点删除有确定回退；10,000 个 delta 无丢字，关键更新不等下一轮节流；工具展开才请求外置详情，缺失文件保留摘要并显示读取状态。

### M5：完整交互迁移、默认入口切换及旧路径退役

把 Composer、补全、队列操作、命令视图和各种 Modal 接入 Controller；挂载后异步启动，未配置可 `/connect`，同一时刻只展示一个业务请求。完整迁移后将原命令的 TTY 分支改为 Textual，batch 保持纯文本并共用 Controller。迁走可复用 helper 后删除旧 TTY 实现及仅验证其静态打印机制的测试，更新现状文档。

独立验收：端到端覆盖配置、对话、并发工具、审批、问答、取消、续队列、compact、resume 和退出；源码依赖检查与全套测试通过；Windows 目标终端及本地 POSIX/WSL 环境有人工烟测证据。仅 headless 通过不能声称真实终端恢复与中文输入已验收。

## Validation And Acceptance

每个里程碑的精确命令、新增测试文件与场景在 [execution.md](execution.md)。所有模型和工具竞态验收使用可控 fake、临时目录及事件屏障，不依赖付费 provider 或不可重现网络时序。实际命令、失败原因和终端证据写入 progress，不沿用历史计划的 passed 数量。

完成必须同时证明三个模块边界、完整业务等价、目标中断语义和全屏体验。不可通过删除失败测试、隐藏未配对记录、先全量加载工具文件再伪装懒加载、保留备用旧 TTY 入口来满足验收。TD-007 含实时 trace 订阅方向，本迁移使用结构化会话更新，不因界面完成就自动宣告其所有范围已解决。

## Recovery

存储改动先在临时 transcript fixture 验证，失败时保留原文件和可恢复暂存文件，阻止继续执行；禁止用当前 active chain 覆盖全历史。默认入口在 M5 才切换。批次失败先修当前边界测试，不推进下游；回退只撤销本计划自己的变更，保护工作区既有修改。完整恢复步骤见 execution。

## Progress

实际勾选状态与第一未完成批次以 [progress.md](progress.md#progress) 为唯一来源。

## Surprises & Discoveries

代码审计发现及证据以 [progress.md](progress.md#surprises--discoveries) 为唯一来源。

## Decision Log

执行期选择与尚待验证项以 [decisions.md](decisions.md#decision-log) 为唯一来源。

## Outcomes & Retrospective

阶段产物、验收差距和最终归档记录以 [progress.md](progress.md#outcomes--retrospective) 为唯一来源。全部验收满足后，将四文件目录整体移到 `docs/exec-plans/completed/cli-to-reference-tui/`，复查相对链接。

2026-09-17：初次编写；依据三个目标设计及当前/参考代码，明确先补持久化和应用边界，再替换 TTY，不把参考 runtime 一并移植。
