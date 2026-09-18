# Decisions

## Decision Log

### 2026-09-17：以目标设计约束参考移植

Decision：主要参考为根目录 `reference/ui/`，保留 UiProjection 的统一归并结构和 View 的虚拟化组织方式；OneCode 的三个目标设计决定行为。`docs/references/ui/` 仅用于既有计划模式背景。

Context：Python 参考使用自己的 domain/SessionEngine/journal/repository，App 直接装配 provider 和工具；UiProjection 使用单一 tool ID、把排队消息写进列表、保留孤立结果并生成取消 notice。这些都与目标契约不同。

Rationale：复用已明确选择的 TUI 技术与表现，同时避免让参考项目的领域模型成为 OneCode 第二套运行时。

Consequences：M2 不复制 SessionEngine；M3 必须适配复合身份与待执行区；M4 不把 tool renderer 的具体工具 imports 搬入 Projection；生产代码不得 import reference 或 miniagent。

Date/Author：2026-09-17 / Codex。

### 2026-09-17：五个宏里程碑，入口最后切换

Decision：先存储/运行事实，再 Controller、Projection、View，最后交互和入口；允许迁移期旧入口向 application 工厂委托，最终删除旧 TTY。

Context：取消、历史、批处理和 UI 目前共享问题但分散实现；只替换渲染器会把生命周期继续留在旧 REPL。

Rationale：每个边界都有不依赖真实终端的验收，默认入口切换时已经具备完整业务。过渡兼容不得反向污染应用层依赖。

Consequences：新计划包放在 `docs/exec-plans/active/cli-to-reference-tui/`，四个文件承担不同事实来源；不新增长期双 TTY 开关。计划完成后整体归档。

Date/Author：2026-09-17 / Codex。

### 2026-09-17：中断整理是第一阶段交付，不作为 UI 后续优化

Decision：M1 必须实现普通 assistant 半段持久化、真实配对保留、实际 transcript 受控整理和失败停止；M2 负责调用并等待。停止工具不能通过向缺失调用补 error result 达成。

Context：当前 loop 的结果事件早于整批追加，recovery 会合成中断结果；直接复制参考投影无法修正磁盘事实。

Rationale：取消后继续、resume 和历史/实时一致都依赖同一真实记录；只在 View 隐藏会留下错误模型上下文。

Consequences：涉及 core/services 的改动仅为运行事实和存储协议，不加入 UI 分支。重写和 flush 共用串行写入边界，成功后才发布终态；故障注入为必需验收。

Date/Author：2026-09-17 / Codex。

### 2026-09-17：旧 transcript 兼容按可证明的记录语义处理

Decision：新写入记录具有稳定来源；旧普通 transcript 用持久化 UUID 与实际工具声明范围建立关联。对于当前版本生成的 compact 记录，优先用 `metadata.compaction`、parent 链和文件序号区分替换活动链与原始历史；被标为 compaction 的复制/摘要不当新聊天加入，原始记录保留。不按文本相同去重，也不为旧记录猜测来源 UUID。

Context：`replace_messages_for_compaction` 为每个 replacement 添加 compaction metadata，却没有原 UUID；旧分支和手工编辑文件可能不足以无损判断。

Rationale：仓库自身生成的旧普通/压缩格式需要可验证兼容，而真正相同正文的重复发送必须保持。信息缺失不能靠显示层猜测掩盖。

Consequences：M1.1 必须用当前实现生成普通、两次压缩、恢复后继续和工具配对 fixture 验证以上规则。无法确定归属的非标准旧分支不得静默合并或删除：返回结构化历史读取诊断、保留原文件；在 M1 决策记录中补明确可支持范围。若当前版本常规生成的历史也不能满足无重复/完整性，M1 不算完成，先修来源/分支方案再推进，不能降级成只显示 active chain。

Date/Author：2026-09-17 / Codex。

### 2026-09-17：交互与 batch 保留 OneCode 业务语义

Decision：命令分类按具体 invocation；查看即时响应，修改在安全点串行，生命周期控制不等队尾。计划审批仍由 OneCode 状态/命令驱动，questions 保留结构化回答。batch 继续单条 prompt+文本问答协议，EOF 不视为同意。

Context：活跃计划模式计划的相关功能已在代码中实现；参考 UI 的权限面板不能覆盖这些协议。现有 batch 直接装配并运行 loop，需要一起迁移以避免双生命周期。

Rationale：新 UI 应改变展示及交互载体，不绕过权限、计划模式和附件约束。

Consequences：M2 把适配器和交互协调分开；M5 对所有已注册命令及子命令建立覆盖。用户未配置时可打开配置和退出；guard deny 永远不会变成可确认放行的 Modal。

Date/Author：2026-09-17 / Codex。

### 2026-09-17：输入键位以参考为起点，撤回保护草稿

Decision：Enter 提交/选补全，Ctrl+Enter 换行，Tab 补全，Ctrl+C 取消前台，Esc 关闭补全/Modal，`/exit` 退出；M5 为不支持 Ctrl+Enter 的终端实现并展示替代换行键，优先选 TextArea 可稳定识别的 Ctrl+J，并在目标终端验证。撤回时已有草稿不被覆盖，撤回原文保留在可选择编辑的承接区。

Context：旧 CLI 使用 Esc 取消运行，参考 Composer 的 Esc 只处理浮层；目标设计要求运行中仍可输入且撤回不能丢草稿。

Rationale：将关闭面板与取消执行区分，避免退出补全时误停工具，同时保持参考主要键位。

Consequences：README 与界面提示同时更新；真实终端验收必须验证中文输入法确认不会错误提交，若协议不能区分某键，修改键位提示和测试后再切换默认入口。

Date/Author：2026-09-17 / Codex。

### 2026-09-17：性能以可观察边界验证

Decision：以 5,000 条历史、参考 overscan=8、10,000 delta 和默认 40ms 合并窗口作为固定验收场景；不要求历史索引常量内存或宣称所有更新 O(1)。最终 Markdown 正确性优先，必要时定稿整文重解析。

Context：VirtualLayoutIndex 更新高度会重建前缀和，MarkdownBlockCache 每次扫描原文，按空行分块未证明所有跨块语义正确。

Rationale：使用 widget 数、详情 IO 次数、文本完整性、锚点与刷新计数比易波动的绝对 FPS 更可靠。

Consequences：性能优化封装在 View 内，不扩大 Controller Interface；在 progress 记录环境与测量。不能以只保留最后 N 条历史作为虚拟化替代方案。

Date/Author：2026-09-17 / Codex。

### 2026-09-18：M1 记录身份与受控整理的具体取舍

Decision：记录身份保存为 transcript 可选字段与 `MessageStore` 内部元数据，不进入 provider-neutral 的 `message` 字典；compaction 由调用方显式传入 `source_uuids`，不从下标或正文推断。受控整理失败只返回结构化失败结果并保留原文件/暂存文件，`Controller` 的“不可自动续队列”失败状态在 M2 落地（M1 提供 `error_log_recorder` 入口和可观察返回值）。

Context：审查确认旧 compact 记录可能没有 `source_uuid`；同步 handler 运行在线程中，`asyncio` 取消不会终止线程。

Rationale：保持 `current_messages()` wire 语义不变，避免未来源信息被猜造；线程中已发生的工具副作用不承诺回滚，只保证前台 handler 的协程在取消返回前观察到取消且不再由主循环发起新写入。

Consequences：历史读取对无来源字段的旧 compact 记录按 `metadata.compaction` 与边界标记过滤，不用文本去重；同步工具 handler 的线程级中断仍受 Python 限制，M2 的取消契约以“本次前台写入者已结束”为验收而不是强杀线程；恢复路径现在会真实重写磁盘，属于已确认契约变化，测试同步更新。

Date/Author：2026-09-18 / opencode。

### 2026-09-18：M2 装配归属与交互接管方式

Decision：把 `CliRuntime` 与 `build_runtime`/`build_unconfigured_runtime` 整体移入 `application/runtime.py`，`ui/cli/types.py` 仅保留 `CliRuntime = ApplicationRuntime` 别名，`ui/cli/app.py` 委托并保留终端 trust 提示。工具 executor 在装配期捕获 prompter，因此 `build_runtime` 注入 `DeferredPermissionPrompter`/`DeferredUserQuestionPrompter` 代理，由 `SessionController` 在构造后把代理 target 指向 `InteractionCoordinator` 适配器。resume/list/restore 业务移入 `application/sessions.py`。

Context：应用层不得 import UI，但旧 CLI 在 M5 前仍需 `build_runtime`/`InlineRepl` 与相关测试；Controller 必须在 runtime 已构建后接管权限/问答。

Rationale：移动而非复制装配逻辑，避免两套生命周期；代理让“装配期注入、运行期接管”不需要改 services 层工具 executor 的构造契约。旧测试的 monkeypatch 目标随机制迁移同步更新（`application.runtime.create_model_client`、`ui.cli.batch.*` 保留）。

Consequences：`ui/cli/session_memory.py`、`ui/cli/resume.py` 变成重导出；`application` 成为 runtime 唯一来源。启动期 MCP trust 的异步编排不在 M2 完成，由 M5.1 在 App 挂载后处理；M2 只交付交互机制与 batch 纯文本回答。

Date/Author：2026-09-18 / opencode。

### 2026-09-18：Controller 的一致性与取消契约实现

Decision：单个前台 worker + FIFO；订阅注册与初始快照在同一同步区段完成，更新带代次与单调序号；慢订阅者队列满时清空并替换为当前完整快照；取消时由 worker 任务接收 `CancelledError`，在异常处理中同步调用 `MessageStore.finalize_interrupted_run`，随后暂停队列并发布修正，`cancel_active` 等待 worker 结束后按需重启；`close` 幂等并唤醒所有交互等待者。

Context：运行事实已由 M1 提供；需要不依赖 Textual 的确定结果。

Rationale：把“取消等待真实收尾”落在 worker 任务边界，取消后不自动 drain；查看命令不等待，修改命令经 `await_safe_point` + 命令锁串行。

Consequences：`snapshot.run.assistant_text` 来自运行事实累加的草稿，不从 Projection 倒读；测试通过可控 fake loop、事件屏障与直接 JSONL/快照断言验证，不依赖付费 provider。

Date/Author：2026-09-18 / opencode。

### 2026-09-18：环境重建说明

Observation：仓库内 `.venv` 为 Linux venv，Windows `uv` 无法复用（`failed to remove .venv/lib64`）。

Decision：删除并 `uv sync --dev` 重建，仅本地环境产物（`.venv/` 已 gitignore），不修改 `pyproject.toml`/`uv.lock`。

Date/Author：2026-09-18 / opencode。

### 2026-09-18：Projection 身份来源与快照重同步

Decision：`ConversationProjection` 消费 M2 契约，但为“实时树与权威历史同一身份”补齐最小事实来源：`HistoryRecord` 暴露 `assistant_call_id`/`model_turn_index` 与结构化工具 `input`；`application.types` 增加 `UserMessageCommitted`，`ToolUpdate`/`ToolRunState` 携带调用归属与 `input`；`core.loop` 在 `interaction_started` metadata 中交付刚持久化用户消息的 UUID；Controller 在取消收尾与 `/compact` 成功后发布权威快照，`apply` 遇到序号缺口只置 `resync_required` 并等待 `replace(snapshot)`，不猜补 delta；旧代次与重复序号直接拒绝。

Context：参考 `UiProjection` 用单一 tool ID 并让孤立结果长期保留，且其快照类型定义在 UI；OneCode 的工具 ID 可跨模型调用重复，用户消息与 assistant 定稿的实时事件原先没有持久化 UUID，取消后也没有权威历史修正通知。

Rationale：身份必须来自 runtime/application 的真实记录，不能从文本、下标或“最近 assistant”推断；工具按 session + assistant 调用 + tool ID 复合归组，结果先到也按明确归属保留。让缺口进入完整快照路径符合“不解析 trace 文本补消息”。

Consequences：工具结果先于持久化仍可展示但不宣称已保存；`HistoryRecord` 新增字段与 `ToolUpdate` 新增默认字段向后兼容；`interaction_started` 成为携带用户记录身份的公共事件；M4 的 View 只需消费 `messages` 与 `ViewChange`，不直接读 runtime。旧记录缺调用标识时，工具结果按“已声明且无结果”的 tool_call_id 回配，属于 M1 兼容规则。

Date/Author：2026-09-18 / opencode。

## 尚待实施验证的选择

Textual 的具体版本在 M4.1 通过参考 API 的最小 headless 兼容验证后写入本文件并锁定；当前项目未声明 Textual，参考目录没有可供直接沿用的 `pyproject.toml`。这是版本兼容性验证，不重新讨论已确认的框架选择。

旧历史兼容规则由 M1.1 fixture 验证，无法证明的非标准分支必须有读取诊断，不能悄悄用正文 hash 去重。目标终端 Ctrl+Enter/Ctrl+J 的实际行为由 M5 验证。二者失败时在相应里程碑内解决并更新证据，不把关键限制留给最终用户猜测。

当前基线有 7 个失败，见 [progress.md](progress.md#validation-evidence)。其中路径 guard 测试和 MCP 连接状态涉及运行安全/初始化，不能未经调查一概归因于平台。计划编写不修复它们；执行时在相关边界改动前定位并记录处置，最终完整测试验收不能隐瞒这些失败。
