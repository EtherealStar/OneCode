# Execution

## 执行约定与基线

从仓库根目录执行以下命令。先阅读 [plan.md](plan.md) 的设计引用和 [progress.md](progress.md) 的最近证据。下文命名的新测试文件是各批次必须交付的产物，不是现有测试；每个文件应以公开行为为主要断言。旧测试迁移时保留业务断言，只有与明确退役机制绑定的断言可删除。

    uv sync --dev
    uv run python -m pytest tests -q

基线失败要记录用例及环境原因，先确认是否影响本批次。不能通过永久 ignore 将失败伪装成通过。工作区已有大量修改，执行者开始前记录自己的差异范围，不 reset 或批量格式化无关文件。模型配置仅遵守现有 `.env` 规则；自动测试注入 fake 配置和 runtime，不复制真实凭证，不连接真实 MCP。

## M1：取消与历史具有可靠的记录基础

### M1.1：记录身份、压缩来源与只读历史

修改 `services/context/message_store.py`、`transcript.py` 和必要的 `services/compaction/service.py` 调用点，使存储保存消息记录 UUID 与活动消息的来源映射。持久化身份和 `core/stream_events.py` 的 `assistant_call_id/model_turn_index` 是两类身份，建立显式关联；不能从列表下标或正文 hash 推断。保留 `current_messages()` 的 provider-neutral 消息语义，内部记录元数据不泄漏进 provider wire format。

给 compaction 复制的旧记录保存来源关联，标明内部压缩产物；普通 append/compaction 保持既有追加语义。提供保留 `LoadedTranscriptMessage` 身份且不恢复外置正文的读取能力，在新建 `application/history.py` 将历史记录转成用户可见记录。模型恢复继续读取当前活动链；聊天读取沿来源和分支规则保留压缩前消息、过滤复制项及内部 attachment 提示，但把真实附件摘要关联给对应用户输入。

旧 transcript 的处理按 decisions 的兼容性门槛实施：没有来源时禁止按文本去重，先用既有 compaction/parent 信息判断；对信息不足的 fixture 记录无法无损推断的边界，不杜撰确定身份。新旧普通会话都必须能恢复，M5 前解决或明确纳入验收的历史限制。

新增 `tests/test_conversation_history.py`，覆盖相同正文两次提交、两次 compact、压缩复制来源、附件摘要、真实工具失败、重复 tool ID 和外置结果缺失。对 artifact 读取函数设置计数/禁止读取的 spy，证明获取历史摘要读取次数为零；不能只比较耗时。扩展 JSONL 与 compaction 测试覆盖来源保存和活动链隔离。

    uv run python -m pytest tests/test_conversation_history.py tests/test_jsonl_session_persistence.py tests/test_compaction_service.py tests/test_context_recovery.py -q

完成条件：新增历史测试通过；读取历史不读全部 artifact，模型快照不混入历史展示元数据，普通追加/压缩测试仍通过。

### M1.2：运行事实与可等待的中断收尾

在 `core/loop.py` 及 provider-neutral 运行状态协作处保留本次调用的未定稿文字、声明、已完成结果和存储确认，直到收尾完成。特别覆盖 `_stream_tool_execution` 已收到真实结果而 `append_tool_results` 尚未执行的窗口；不能只从最后一次 yield 或 UI buffer 恢复。工具 executor 的取消协作必要时在 `services/tools/executor.py` 补齐，保证取消返回后本次前台写入者已结束；不在 loop 增加具体工具或 Textual 分支。后台任务按自身归属管理，不误判为当前前台悬挂写入者。

在 MessageStore/JsonlTranscriptStore 内提供受控整理入口，接收被冻结的真实运行事实，按 [中断整理契约](../../../design-docs/session-controller-architecture.md#收尾顺序)修正正文与工具配对。同步内存、记录来源、最后 UUID、待 flush 缓冲和 parent 引用；磁盘重写基于完整原 transcript 加必要修正，不基于活动链快照。序列化普通 append、flush timer 与 rewrite 的整个写入临界区，避免现有 `flush` 取出 pending lines 后释放锁再写磁盘的竞态。

先在同目录构造并校验新 transcript，再原子替换，只有成功才提交内存的最终状态和终态通知。失败保留可恢复文件、记录 error log 并进入不能自动续队列的失败状态。重复收尾不能再次追加半段文字；实际成功、失败、权限拒绝结果都属于真实配对。空 assistant 清理、工具声明的两种内部表示、附件和不受影响的历史都要覆盖。

将 `services/context/recovery.py::_synthetic_interrupted_tool_result` 策略替换为同一整理规则；修改现有 `tests/test_context_recovery.py` 中合成结果预期。恢复要真实修复磁盘，而不是只返回过滤后的 messages。兼容 `InMemoryTranscriptStore` 的子 agent 场景，不能因新协议强制临时会话落盘。

新增 `tests/test_session_interrupt_cleanup.py`，用事件屏障分别停在纯文本、assistant 已存、部分并发工具完成、结果未批量存、flush 已取缓冲等时刻。注入暂存写入/替换失败，检查原文件完整、错误状态可观察、无下一轮执行。每次成功收尾后直接 `json.loads` 全部行并重新恢复，再次收尾验证幂等；禁止只通过 Projection 验证。

    uv run python -m pytest tests/test_session_interrupt_cleanup.py tests/test_context_recovery.py tests/test_jsonl_session_persistence.py tests/test_async_loop.py tests/test_conversation_history.py -q

完成条件：半段文字作为普通 assistant 恰好保存一次并进入下轮上下文；真实配对保留，未配对记录从实际 JSONL 删除，无合成结果；未受影响 UUID/顺序/内容保留，parent 链无悬挂，故障时不报告成功。

## M2：共用 SessionController

### M2.1：抽出装配与可观察的单会话执行

新建 `application/session.py`、`types.py`、`runtime.py`，从 `ui/cli/app.py::build_runtime/build_unconfigured_runtime`、`ui/cli/types.py::CliRuntime` 提取装配和重绑定。已有 session memory、compaction、result store、trace、模型、subagent parent context 和后台任务归属随工厂统一管理。应用层不得 import UI；旧 CLI 若暂时需要工厂，改为向 application 委托，不能反向引用。

Controller 的 async context 进入后启动独立 bootstrap task，watch 可立即拿到初始化快照。前台单 worker 收集附件和 plan attachment，调用既有 AgentLoop，依据 M1 的存储确认发布提交或修正；运行草稿来自 runtime，不来自 Projection。submit 返回回执即结束，不等待 turn。未就绪 prompt 拒绝但配置与退出可用。

实现原子订阅快照+后续更新注册、代次与序号、不可变值/副本和慢观察者重新同步。慢观察者积压时替换为包含当前草稿、队列及 pending interaction 的完整快照；不能丢正文 delta 后继续发送不完整增量，也不能 await widget 渲染。关闭 watch 只取消订阅。

新增 `tests/test_session_controller.py`，用可控 fake runtime 验证初始快照期间到来的事件不丢、重新订阅恢复草稿、突发更新慢消费最终一致、订阅者不能修改 runtime、退订不取消执行。通过公开 Interface 证明多个 submit 同时到达时最多一个前台执行。

    uv run python -m pytest tests/test_session_controller.py tests/test_runtime_integration.py tests/test_import_boundaries.py -q

完成条件：无需 Rich/Textual 就可启动、提交、观察并关闭会话；草稿到持久化身份稳定，M1 收尾由 Controller 调用并等待。

### M2.2：命令、交互、队列与生命周期闭环

在 `application/commands.py` 迁移解析、registry 和业务；结果为结构化数据或请求，不含 `renderable/presentation/reset_main_view/replay_messages`。注册具体 invocation 的执行类别：`/status`、`/usage`、`/memory`、`/skills`、`/mcp`、`/permissions` 查看及 `/tasks` 查看、`/plan show` 读取一致快照即时返回；`/permissions add|remove|replace`、`/compact`、`/plan` 状态改变及实际存在的任务修改在安全点串行。`/plan <描述>` 通过同一 submit/附件入口；`/plan open` 使用现有计划定位和打开行为，不把文件读取搬到 View。

`/resume`（含 `/continue` 别名、ID/标题/路径解析）、`/clear`、`/exit` 走生命周期入口。`/resume` 在用户选定目标前不改变会话；选定后停止接收新工作、等待旧 turn 收尾、丢弃队列、统一重绑定并发布新代次快照。失败保留可解释的旧状态，不发布假成功新快照。`/connect` 收集配置可先行，模型替换在安全点完成，仍写现有 `.env` 格式。将 `_run_async_blocking` 相关业务改成 async，不在线程里再开事件循环等待。

队列未执行项不写 transcript。正常完成 drain；取消或不可恢复失败暂停且保留；withdraw 与领取串行，已开始项不可撤回；重新提交得到新 input ID。暂停与前台 running 是两个状态；resume_queue 不能越过关闭/收尾失败。

`application/interactions.py` 适配 `PermissionPrompter`、`UserQuestionPrompter`、MCP trust 及现有计划审批状态。参考没有完整 OneCode 问答/trust 面板，不假定复制 permission modal 即完成。`exit_plan_mode` 当前由 CLI 驱动审批的业务语义须保留，不擅自把它改为模型参数批准。请求按到达顺序仅暴露一个 active，请求带 session/run 或 bootstrap 归属及 request ID；类型不匹配和过期回答不影响其他请求。取消必须唤醒全部相关等待者，guard deny 不创建可放行请求。

新增 `tests/test_session_commands.py`、`tests/test_session_interactions.py`，扩展 Controller 测试，覆盖命令分类、运行中即时查询、safe point 修改、切换丢队列、重绑定、重复 close、交互取消和启动期 trust。背景资源按既有 Manager 关闭契约等待清理；错误写现有 error log，不流出未处理 callback 异常。

    uv run python -m pytest tests/test_session_controller.py tests/test_session_commands.py tests/test_session_interactions.py tests/test_plan_mode.py tests/test_permission_policy.py tests/test_cli_resume.py tests/test_cli_connect.py tests/test_cli_mcp_trust_prompt.py -q

完成条件：竞态结果确定，查看命令不会等模型完成，关闭/切换等待真实收尾；计划模式、附件和权限业务测试不退化。尚依赖旧渲染器的测试在 M5 迁移，但业务预期从本批次起由新测试覆盖。

### M2.3：batch 使用同一会话契约

重写 `ui/cli/batch.py` 为 Controller Adapter，删除其独立 loop、附件收集和 shutdown。保留当前单条输入、流式文本与工具摘要、成功/失败退出码；权限及问答仍用纯文本协议，stdin EOF/中断走已有拒绝或取消语义，不自行同意。交互回答读取与 prompt 读取由 batch 协调，不能让两处同时抢 stdin。Rich 仅在纯文本呈现层使用。

新增 `tests/test_batch_session_controller.py`：fake runtime 的成功流式、工具结果、provider 错误、空输入/EOF、权限 deny、问答中断和 close。替换 Textual App 构造器为会抛异常的哨兵或在子进程拦截 textual import，证明 batch 不启动/导入 TUI；stdout 不含 alternate-screen 控制序列。

    uv run python -m pytest tests/test_batch_session_controller.py tests/test_async_cli_streaming.py tests/test_session_controller.py -q

完成条件：batch 和 Controller 测试通过，纯文本输出及退出码符合原业务；batch 不依赖旧 TTY 运行状态。

## M3：ConversationProjection

### M3.1：移植统一入口与固定工具归并

新建 `ui/tui/projection.py` 和 `projection_types.py`，以参考 `UiProjection` 的五个方法为主体适配。调用关联、记录 ID、会话代次和序号来自 M2；ViewChange 返回受影响/删除 IDs、结构变化和辅助状态变化。工具键包括会话、assistant 调用及 tool ID，工具按声明位置出现；结果比 assistant 定稿先到时也保留，定稿正文是替换不是追加。

只有工具没有文字时也创建 assistant 容器。声明暂未到可按明确归属暂存，等待权威更新，不能匹配“最近 assistant”或永久输出孤立 tool 消息。pending queue 单独投影，正式提交才加入聊天。附件摘要来自结构化 metadata，不解析用户输入；真实错误状态来自结果字段，不解析输出文本。

新增 `tests/test_conversation_projection.py`，复用 M2 场景构造快照/更新：纯文本、工具 A/B 乱序、多次模型调用复用 ID、重复定稿、晚到定稿、只有工具、撤回/清空队列。断言实时与历史消息树相同，B 结果到达时 A 未完成且 B 立即可见。

    uv run python -m pytest tests/test_conversation_projection.py tests/test_conversation_history.py -q

完成条件：不启动 Textual、不读文件即可验证全部身份及排序规则；代码不依赖旧 reducer 或参考领域类型。

### M3.2：权威修正、重新同步及详情

补收尾的修正/删除、同会话快照替换、旧代次和重复序号拒绝、详情加载/失败、usage、任务和 pending interaction。序号缺口必须进入可恢复的完整快照同步路径，而不是猜补 delta。中断文字正常呈现，不引入 interrupted lifecycle 或参考 UiRunNotice 取消标签。

扩展 Projection 测试并新增 `tests/test_conversation_pipeline.py`，把真实 Controller 更新连接到 Projection，验证 compact、取消、resume、慢订阅者同步后与 Controller 权威历史一致。旧会话详情请求在切换后完成时不影响新投影。

    uv run python -m pytest tests/test_conversation_projection.py tests/test_conversation_pipeline.py tests/test_session_interrupt_cleanup.py -q

完成条件：消息 dirty/deleted IDs 正确，状态/交互无消息变化时也可通知 View；所有模型执行与磁盘整理仍由下层负责。

## M4：参考 ConversationView

### M4.1：Textual 依赖、主题和虚拟视口

安装并锁定通过兼容性验证的 Textual；在 `ui/tui/conversation/` 移植 `view.py`、`viewport.py`、`layout_index.py`、`render_cache.py`、`refresh_scheduler.py`，在 `ui/tui/renderers/` 移植消息、工具、状态 renderer。先用固定 Projection 的测试 App 验证 Textual `run_test`、TextArea、Theme、timer、resize、mount/move_child 等参考所用 API；锁版本失败时修改适配层，不能为了兼容退回全量 widget 或静态打印。

主题和 `ui/tui/onecode.tcss` 沿用参考 token：深背景、surface 区分、agent 蓝/工具青/错误红等语义；对话区占剩余高度，状态栏与 Composer 固定，补全层及新内容入口不挤乱布局。参考 renderer 直接 import 多个工具输入模型，目标改为 OneCode 公共事实与 presenter registry。未知/MCP 工具必须有可用 fallback，错误摘要不因缺专用 renderer 隐藏。

虚拟化指只挂载当前可见消息加上下少量预加载消息；保留参考 overscan=8 作为起点。锚点是 message ID + 消息内行偏移，宽度变化使高度测量失效；晚到布局/滚底回调不能覆盖用户新滚动。删除锚点时优先相邻存续消息，无邻居才采用空视口规则。

新增 `tests/test_conversation_view.py` 与 `tests/test_tui_rendering.py`。5,000 条固定高度消息在 120×40 和 60×20 终端尺寸下，布局稳定后挂载的 MessageWidget 数量不超过可见消息数 + 16，不计两个 spacer；从顶部、中部、底部抽样验证。滚动后未挂载消息重进视口时内容/工具结果完整。

    uv sync --dev
    uv run python -m pytest tests/test_conversation_view.py tests/test_tui_rendering.py tests/test_conversation_projection.py -q

完成条件：生产模块不 import reference；测试 App 可以全屏布局并稳定浏览长历史，固定消息顺序不随工具完成改变。

### M4.2：刷新、Markdown、详情与资源回收

40ms scheduler 只合并 ViewChange 的刷新请求，不跳过 Projection.apply。定稿、交互、错误、会话替换允许立即刷新，辅助状态不能因 dirty message IDs 为空被 scheduler 吞掉。测试用可控时间推进证明一个窗口内普通请求合并、终态立即可见；输入 10,000 个 delta 后比较完整正文，不能只看最后一屏。

参考 Markdown 按空行缓存已闭合块，补围栏内空行、混合围栏、跨块列表/引用、文末链接定义、中文/emoji、最终正文改写测试；最终呈现与相同主题的完整 Markdown 语义一致。必要时定稿整文重解析，避免为保留优化而固化错误。解析缓存和宽度测量分开管理，删除消息/切换会话释放 timer 与缓存。

工具展开以工具键记录本地状态，不能直接复制参考的整条消息 reasoning/tool 共用展开开关。发出 DetailRequested 后仅经 App → Controller.load_detail → Projection 更新返回；Controller 限制详情引用所属会话和已知 artifact，不接受 View 任意文件路径。折叠、离屏与换会话可释放大正文，摘要保留；展开已存在的小结果无需磁盘读取。

新增 `tests/test_tui_refresh_scheduler.py`、`tests/test_tui_details.py` 并扩展 View/rendering 测试，覆盖向上阅读时增长、返回最新、连续展开、resize、锚点删除、旧滚底回调、缺失 artifact、详情迟到和卸载后 timer 不回调。对于保持锚点的普通增长，稳定后相同锚点偏移误差不超过一行；resize/删除时验证确定的身份与邻居回退，不要求不可能保持的旧换行绝对行号。

    uv run python -m pytest tests/test_conversation_view.py tests/test_tui_rendering.py tests/test_tui_refresh_scheduler.py tests/test_tui_details.py -q

完成条件：上述行为在 headless runner 可重现通过，记录 5,000 消息运行环境和刷新计数；不以未经测量的固定 FPS 或 O(1) 口号验收。

## M5：完整交互与入口切换

### M5.1：薄 App、Composer、命令视图与 Modal

新建 `ui/tui/app.py` 装配 Controller、Projection 和 View，挂载后启动初始化/观察任务；App 回调只提交意图或更新呈现，不复制 REPL 队列 drain、附件收集和 runtime 重绑定。未配置界面保留输入及 `/connect` 入口，初始化中明确显示状态；MCP trust 可以在启动尚未完成时回答，不造成启动互等。

从参考 Composer/补全层移植文本编辑与菜单呈现，复用 OneCode 命令 registry、`ui/cli/suggestions.py` 的可复用候选查询及 attachment resolver，不携入 miniagent 引用存储。候选查询若涉及文件 IO，异步调度并拒绝旧查询结果，不能阻塞键盘回调；路径中空格、中文和 Windows 路径采用现有 OneCode 语义。提交仅在回执接收后清理对应草稿版本，拒绝或正在编辑的新草稿不得丢失。

按 decisions 记录键位：Enter 提交（候选打开时先选候选）、Ctrl+Enter 换行、Tab 补全、Ctrl+C 请求取消当前执行、Esc 关闭当前补全/Modal。为终端无法传递 Ctrl+Enter 提供可见说明及替代换行键；没有活动 turn 时 Ctrl+C 不退出。退出使用 `/exit`，不复用会误删草稿的隐藏行为。队列有显式继续/撤回操作；已有未提交草稿时，撤回文本进入可选择的编辑承接区，不能直接覆盖。

权限、问答、计划审批、trust 使用统一当前请求驱动的面板；连接和会话选择使用类型化配置/选择请求。适配真实 PermissionResponse 选项、QuestionResponse 的选择/自由文本及取消语义。Modal 关闭恢复原焦点和草稿；请求撤销时关闭面板；并发请求不叠加，过期回答被拒绝。

新增 `tests/test_tui_app.py`、`tests/test_tui_composer.py`、`tests/test_tui_interactions.py`，通过 Textual Pilot 与 fake Controller/真实 Controller+fake runtime 两层覆盖。完整命令 registry 的每个命令与 alias 均有可用入口；不能只迁移 `/status`、`/resume` 而遗漏 `/memory`、`/mcp`、`/permissions` 编辑、`/compact`、`/plan` 子命令。

    uv run python -m pytest tests/test_tui_app.py tests/test_tui_composer.py tests/test_tui_interactions.py tests/test_session_commands.py tests/test_session_interactions.py tests/test_plan_mode.py -q

完成条件：初始化、运行输入、暂停撤回、问答、trust、审批、配置、切换到错误处理全链路可操作，App 中无阻塞 stdin、嵌套 `asyncio.run` 或 `thread.join`。

### M5.2：切换入口、退役旧 TTY 与最终验收

修改 `ui/cli/app.py::main`：stdin/stdout 均 TTY 时延迟 import 并启动 Textual，非 TTY stdin 仍走 batch；stdin 为 TTY 而 stdout 被重定向时保留现有明确错误与非零退出。入口不提前构建 runtime 或等待 trust。保留用户现有启动命令，不增加长期存在的双 TTY feature flag。

把 `ui/cli/connect.py`、`resume.py`、`session_memory.py` 中应用业务迁入 application 或已有 service；仍需共享的纯文本摘要/候选逻辑放在无终端生命周期的模块。移除旧 `ui/cli/terminal/` 中 REPL、stream state/reducer/view/session、output coordinator、static replay、page/selector/permission/trust/connect transient 路径。剩余 helper 若保留须逐个说明使用者，不留下可进入的旧 TTY。确认无生产 import 后移除 prompt-toolkit 直接依赖并更新 lock；若 batch 输入 helper 仍用它，先用等价纯文本实现迁出并保留 EOF 测试。

迁移 `tests/test_cli_commands.py`、`test_cli_resume.py`、`test_cli_connect.py`、`test_cli_permissions.py`、`test_cli_mcp_trust_prompt.py`、`test_cli_prompt_input_suggestions.py` 的业务断言到新模块测试；旧 checkpoint/static commit/output coordinator 测试随旧机制退役，保留工具声明顺序和实时结果显示的行为测试。更新 `tests/test_import_boundaries.py` 检查 core/services/infrastructure 不依赖 application/UI/Textual，application 不依赖 UI，Projection 不依赖 Textual/旧 reducer，生产代码不依赖 reference/miniagent；最好解析 import，而非依赖容易漏报的字符串匹配。

新增 `tests/test_tui_entrypoint.py`，验证三种 TTY 分流、异常关闭和 batch 无 Textual。更新 `architecture.md`、CLI 两份现状设计、上下文设计、三个目标设计的实施状态和 README 运行/按键说明；设计契约变化须显式记录，不能只改“已实现”。按证据处理 TD-007/TD-016，不改无关债务。

    uv run python -m pytest tests -q
    uv run python -m compileall core services infrastructure application ui
    uv run python -m pytest tests/test_import_boundaries.py -q
    rg -n 'InlineRepl|TerminalOutputCoordinator|pending_static_commits|run_in_terminal|replay_messages_to_static|prompt_toolkit|miniagent|from reference|import reference' application ui

预期全套测试通过、compileall 退出码 0、生产目录搜索无旧展示管线/import；`rg` 无匹配的退出码 1 是期望结果。有平台基线失败时必须记录逐项比对且迁移相关测试全通过，不将它报告为全套通过，最终验收仍需在支持平台取得完整结果。

真实终端执行 `uv run python -m ui.cli.app`。先在没有模型配置的临时工作区进入界面并打开/取消 `/connect`，再在已配置环境测试一次普通对话、工具展开和取消；禁止改写用户真实凭证作为测试准备。运行中提交两条输入，取消后应暂停且保留队列，撤回可编辑、继续可 drain。用 `/compact`、`/resume` 验证历史完整；权限拒绝不得执行被拒工具，计划审批/用户问答都能取消返回。退出后提示符、光标、回显恢复且无聊天重放。

在 Windows Terminal 和本地 POSIX/WSL 终端记录终端版本、字体/尺寸、中文输入法预编辑与确认、中文长行、emoji、多行粘贴、60×20 窄窗口、resize、滚轮/键盘浏览、Modal 焦点及 Ctrl+Enter 实际行为。保存简短截图或录屏路径和观察结果到 progress；headless 不能代替这项。批处理在 fake 集成测试中验证后，可在有配置的临时工作区用 `printf '请只回复 OK\n' | uv run python -m ui.cli.app` 烟测，不应启动全屏。真实 provider 烟测只验证集成，不用精确模型措辞替代确定性测试。

完成条件：M1–M5 证据齐全，只有新 TTY+batch 两个 Adapter，所有迁移验收通过；补 Outcomes 后整体归档计划包。

## Idempotence And Recovery

文档和测试命令可重复执行。`uv sync --dev` 使用 lock；不手工升级其他依赖。新测试只写 pytest 临时目录，不整理真实用户 transcript。真实迁移前先对目标 session 目录做只读备份，并在实现的错误日志中记录暂存文件位置，不记录敏感正文。

中断整理失败时保留正式文件、暂存产物和未完成收尾状态，禁止 drain/切换成功。修复原因后通过存储整理入口重试，先验证候选完整 JSONL/parent 链；不得凭文件时间戳盲目替换，不得用旧 timer 重新追加已删除记录。恢复强杀进程场景只清理磁盘已有事实，不重跑缺结果工具，也不承诺恢复未写盘 token。

M1–M4 保持旧默认入口便于逐批验证，但最终验收不能依赖该路径。M5 若切换失败，临时回退本批次入口变更，保留此前已验证的新模块，修好后重新切换；不永久增加兼容 flag。存储语义已经迁移后，回退 UI 不意味着可恢复合成中断结果策略。每次停止更新 progress 当前批次、通过/失败命令和下一步，不能仅写“继续 TUI”。
