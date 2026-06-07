# OneCode Core Beliefs

本文记录 OneCode 作为完整 code agent runtime 持续演化时应坚持的核心信念。

它不是功能清单，也不是短期路线图。它的用途是帮助我们在增加能力、重构模块、引入插件、扩展权限或上下文系统时，判断一个设计是否符合 OneCode 的方向。

## 项目定位

OneCode 是一个小而清晰的 code-agent。

它围绕模型、工具、状态、权限、上下文治理和错误恢复组织起来。它不是简单的 CLI wrapper，也不应该通过不断复制某个成熟产品的表面功能来获得复杂度。

OneCode 的完整性不来自把更多逻辑塞进主循环，而来自把能力拆成可注册、可组合、可治理的层，并让主循环只负责稳定编排。

## 已经落地的核心结构

OneCode 已经把下列结构落地为可运行的运行时。后续演化应加强这些方向，而不是绕开它们：

- 一个薄主循环驱动流式模型调用、工具执行、工具结果回填和 transition 判定；续轮只看实际 tool calls，不依赖 provider 私有 `stop_reason`。
- 工具通过 `ToolRegistry` 暴露 schema 和 handler，由 descriptor、classifier 和 executor 接入，而不是硬编码在 loop 中。
- system prompt 由 `PromptAssembler` 按运行时状态组装成可组合 section，而不是写成不断膨胀的静态字符串。
- hook 承载权限、审计、压缩前后处理、session/long-term memory 提取等横切逻辑，挂在稳定生命周期事件上，而不是散落进主循环。
- deny 在能力装配前（`ToolRegistry` 可见性）就裁剪工具，并在执行入口（guard + permission policy）重复校验。
- compaction、session memory、long-term memory、attachment 投影由 `ContextEngine` 与 context preparer 链编排，是运行时基础设施，不是后期清理功能。
- 429、上下文超限、输出截断等错误被归类为明确 transition，由恢复流程吸收，而不是直接让任务崩溃。
- 模型 provider 隔离在 `infrastructure/`，core 与 services 只依赖 provider-neutral 的 `ModelClient` 协议和 `ModelStreamEvent`。
- trace 与 error log 作为分离的结构化可观测性基础设施记录，供 CLI、测试和未来 UI 共享。

## 核心信念

### 1. 主循环保持薄而稳定

Agent 的最小内核是：

1. 准备上下文。
2. 组装系统提示词。
3. 调用模型。
4. 如果模型请求工具，执行工具并把结果放回上下文。
5. 如果模型不再请求工具，结束当前任务。

主循环应该只表达这个编排结构。新增能力进入系统前，应先判断它属于哪一层：

- 工具能力。
- hook 扩展。
- prompt section。
- compaction layer。
- state transition。
- model client 适配。
- CLI 或 UI 表现。

如果一个能力必须直接改写主循环才能存在，就需要重新审视它的抽象边界。

### 2. 运行时动态组装胜过硬编码

OneCode 应从真实运行状态生成行为，而不是依赖静态硬编码。

工具 schema 应来自当前启用且未被拒绝的 `ToolRegistry`。系统提示词应来自当前 workspace、工具、权限模式、压缩状态和未来的 memory、skill、task 状态。禁用或拒绝一个工具后，它不应继续出现在 schema、prompt 或执行路径中。

动态组装不是为了炫技，而是为了保持系统一致：

- 当前有哪些能力，模型就只看到哪些能力。
- 当前有哪些约束，模型就只接收相关约束。
- 当前状态发生变化，下一轮模型调用就能看到变化后的事实。

这条信念适用于工具、prompt、skill、memory、task、权限规则和未来插件系统。

### 3. 拒绝优先于组装、授权和扩展

deny 是最高优先级。

如果一个工具、路径、命令、插件或skill被任意有效规则拒绝，它就不应该被动态组装进模型可见能力中。拒绝不是“模型可以看到，但执行时失败”的普通分支；拒绝是一种能力裁剪。

这意味着：

- 被 deny 的工具不进入 tool schema。
- 被 deny 的工具不出现在 system prompt 的可用工具说明中。
- 被 deny 的插件或 skill 不参与 catalog。
- 被 deny 的路径能力不应被包装成“可读/可写但需要确认”。
- hook、会话 allow、项目 allow、自动审批或模型判断都不能覆盖 deny。

拒绝优先还必须在执行入口重复校验。动态组装可以减少模型发起非法工具调用的机会，但不能替代执行前检查；历史消息、旧 schema、provider 行为或手写 tool call 都可能带来已经不可见但仍被请求的工具。

OneCode 的权限判断应遵循一个保守顺序：

1. 先合并规则来源。
2. 先判断 deny。
3. 再判断 ask。
4. 最后才考虑 allow 或默认行为。

只要有一个有效 deny 命中，就不能继续被 allow、ask、hook 或动态组装挽回。

### 4. 扩展通过 hook 和 registry 接入

hook 是扩展点，不是第二个主循环。

权限检查、工具日志、工具结果审计、压缩前后处理、未来记忆抽取、任务同步、外部通知等横切逻辑，都应优先挂在显式事件上，而不是直接写入 `AgentLoop`。

当前的稳定事件点按阶段分组：

- 交互与生命周期：`UserPromptSubmit`、`AssistantMessageCompleted`、`TurnStopped`
- 工具执行：`PreToolUse`、`PostToolUse`、`ToolError`
- 压缩：`PreCompact`、`PostCompact`、`CompactFailed`
- 任务：`TaskCreated`、`TaskCompleted`

其中只有 `PreToolUse` 的 `blocking_error` 能阻止工具执行，`TaskCreated`/`TaskCompleted` 能阻断对应任务操作；其余事件为观察或补充上下文。这些事件应保持稳定、语义清晰、数量克制。新增 hook 事件需要回答两个问题：

- 这个事件是否代表 agent 生命周期中的稳定节点？
- 没有这个事件时，扩展是否会被迫侵入主流程？

hook 可以阻断、记录、补充上下文或调整输入，但不应绕过更底层的安全规则。未来如果引入用户配置、项目配置或组织策略，hook 的 allow 不能覆盖 deny 或 ask 规则。

### 5. 元数据驱动工具编排

工具不只是一个函数。工具应包含足够的元数据，让 runtime 能做正确编排：

- 名称和描述。
- 输入 schema。
- 是否只读。
- 是否可以并发。
- 是否修改文件系统。
- 是否需要权限。
- 结果大小预算。
- 超时策略。

工具执行可以从串行起步，但接口不能把系统锁死在串行模型里；当前 executor 已基于 classification 的并发安全性对工具分批执行。

未来的并发执行、权限判断、工具选择、结果压缩、审计日志和 UI 展示，都应优先消费工具元数据，而不是写散落的 `if tool_name == ...` 分支。

### 6. 安全必须由代码路径保证

不能把安全寄托在模型自觉上。

模型可以被 prompt 要求谨慎，但真正的安全边界必须发生在工具执行前。路径校验、危险命令阻断、写入范围限制、权限规则、用户确认和审计日志，都应由 runtime 执行。

OneCode 的安全模型应逐步走向分层：

- 工具自身的输入校验。
- 通用权限规则。
- workspace 边界。
- 用户或项目配置。
- 会话级临时授权。
- 必要时的人工确认。

安全规则的失败结果应作为 tool result 返回给模型，使模型有机会修正计划，而不是让主循环崩溃。

### 7. 沙箱边界必须基于规范化路径

路径安全不是字符串前缀匹配。

OneCode 的文件边界判断应建立在规范化后的路径上：

- 先把输入路径解析为绝对路径。
- 在 Windows 上归一 `/C:/...`、`/c/...`、`/cygdrive/c/...`、`/mnt/c/...` 等等价路径。
- 统一处理路径分隔符，尤其是用于 permission pattern 或 glob pattern 的路径。
- 对已经存在的路径，优先使用 realpath 消除符号链接带来的歧义。
- 对尚不存在的写入目标，也要解析到稳定的绝对路径，再判断父目录和最终目标是否越界。
- 判断 parent 是否包含 child 时，应使用路径库的相对路径语义，而不是 `startswith`。

沙箱边界也不是永远等于当前工作目录。完整项目可能同时存在：

- 当前工作目录。
- git worktree 根目录。
- 显式允许的额外工作目录。
- 被拒绝的目录或文件模式。
- 外部目录访问请求。

路径位于 worktree 内但不在当前工作目录下时，不一定应该被视为外部目录；但如果非 git 项目把 worktree 退化为 `/`，不能因此把整个文件系统都视为项目内部。

因此，边界判断应有明确语义：

- `inside workspace` 表示常规读写可直接进入权限流程。
- `inside worktree but outside cwd` 可以是项目内路径，但仍要遵守 deny 和具体工具权限。
- `external directory` 需要明确的 ask 或 deny 决策。
- `denied path` 直接失败，不进入动态组装或人工确认。

沙箱的目标不是让所有外部访问都不可能，而是让每一次越界都有可解释、可审计、可拒绝的权限路径。

### 8. 上下文是受管理的工作内存

上下文不是聊天记录的无限累积，而是 agent 的工作内存。

工作内存必须被主动治理：

- 大工具结果应先持久化，再在上下文中保留路径和预览。
- 旧工具结果可以被占位符替换，但应告诉模型如何重新获取细节。
- 长会话应保留目标、用户约束、关键发现、修改文件和下一步。
- 全量压缩前应写 transcript，保证信息有可恢复来源。
- 上下文超限时应走 reactive compact，而不是盲目重试。

压缩会损失细节，所以完整项目需要进一步区分：

- 当前上下文：模型当下推理需要的工作内存。
- transcript：完整历史的可恢复记录。
- tool result store：大输出的外部存储。
- session memory：跨压缩保留当前任务连续性。
- long-term memory：跨会话保留用户偏好和项目事实。

这些层不应混在一个不断膨胀的 message list 中。

### 9. 错误恢复是状态机的一部分

API 错误、上下文超限、输出截断、连接中断和工具失败，都是 agent runtime 的正常路径。

OneCode 不应只把它们视为异常文本，而应把它们归类为明确的 transition（`TransitionReason` 枚举）：

- `tool_use`
- `completed`
- `max_turns`
- `rate_limit_retry`
- `reactive_compact_retry`
- `max_output_tokens_escalate`
- `max_output_tokens_recovery`
- `stop_hook_continue`（已在枚举中定义，作为目标恢复能力，loop 当前未消费）

transition 应服务于三件事：

- 恢复策略：下一步该重试、压缩、继续还是停止。
- 可观测性：用户和开发者知道 agent 为什么这么做。
- 测试覆盖：每种恢复路径都有可验证行为。

错误恢复的目标不是无限坚持，而是可控地继续，并在无法继续时给出清晰的停止原因。

### 10. 模型提供商是可替换边界

OneCode 不应把核心 runtime 绑定到某个模型 SDK。

模型客户端应负责把 provider 的协议、字段、错误和 usage 信息归一化为 OneCode 内部结构。主循环只依赖 provider-neutral 的 `ModelClient.stream(snapshot)` 协议、`ModelStreamEvent` 流式事件和 `ProviderError` 错误类型；续轮判定只看 `message_completed.metadata["tool_calls"]`，不读 provider 私有字段。

这使 OneCode 可以支持不同 OpenAI 兼容服务、流式解析、fallback model 和 provider-specific recovery，而不污染 agent 核心。

### 11. 可观测性是产品能力，不只是 debug

Code agent 做了很多不可见决策：为什么调用某个工具，为什么压缩，为什么重试，为什么停止。

完整项目需要把这些决策变成可观察的事件：

- 工具调用摘要。
- 权限阻断原因。
- 压缩前后状态。
- usage 和上下文占用。
- transition reason。
- transcript 和大结果路径。

OneCode 用结构化 trace（`TraceRecorder` → `.onecode/<session>/trace.jsonl`）记录短小 runtime 事实，用独立 error log（`ErrorLogRecorder` → `errors.jsonl`）记录未恢复失败的调试证据，两者分离且统一脱敏。UI、debug mode 和测试 harness 都应复用这套结构化记录，而不是各自解析文本日志。

### 12. 简洁是演化速度的保护层

OneCode 可以逐步拥有复杂能力，但每一层都应保持局部简单。

复杂系统不可避免，复杂主循环不是。完整项目应该通过清晰边界容纳复杂度：

- 工具系统复杂，主循环不复杂。
- 权限系统复杂，工具执行入口不复杂。
- 压缩系统复杂，消息替换契约不复杂。
- provider 适配复杂，模型响应结构不复杂。
- CLI 体验变丰富，agent runtime 不依赖 CLI。

如果某个模块开始要求其他模块理解它的大量内部细节，就说明边界需要重新整理。

## 设计推论

- 新工具应注册 metadata 和 handler，不应在主循环中新增工具名分支。
- 新 prompt 行为应成为可组合 section，不应追加到一个巨大的静态字符串。
- 新 side effect 应优先挂 hook，不应直接进入 `AgentLoop._run_loop`。
- 新权限能力应进入权限层或 hook 协调层，不应依赖模型承诺；deny 应在动态组装和执行入口都生效。
- 新路径能力应先定义沙箱语义，再实现读写行为；路径判断必须经过 normalize、resolve 和边界检查。
- 新上下文能力应明确自己属于 active context、transcript、tool result store、session memory 还是 long-term memory。
- 新 provider 支持应进入 model client adapter，不应改变 loop 对响应的理解。
- 新恢复路径应有 transition reason，并配套测试。
- 接口应为并发、流式和插件化留出清楚入口；当前已落地流式模型、基于元数据的并发分批和 registry/hook 插件接入。

## 明确反模式

- 在主循环里硬编码具体工具名。
- 在主循环里不断追加权限、日志、通知、记忆抽取等横切逻辑。
- 把所有 skill、memory、项目规则一次性塞进 system prompt。
- 把被 deny 的工具继续暴露给模型，再期待执行阶段兜底。
- 依赖 `stop_reason` 作为唯一续轮信号，而不检查实际 tool calls。
- 让 hook、会话 allow 或自动审批绕过底层 deny 或 ask 规则。
- 用字符串前缀判断路径是否在 workspace 内。
- 在 Windows 上不处理盘符、`/mnt/c`、`/cygdrive/c` 或路径分隔符差异。
- 忽略符号链接和 realpath，导致路径看似在沙箱内、实际指向沙箱外。
- 在上下文过大时直接裁剪消息而不保存 transcript 或大结果。
- 把 provider-specific 字段泄露到 loop、tools 或 compaction 模块。
- 用字符串日志作为唯一事实来源，缺少结构化状态。
- 为短期功能破坏 registry、hook、prompt section、transition 等长期边界。

## 各层的演化方向

下列分层能力大多已经落地（详见 `architecture.md` 的模块文档索引与各 `*-architecture.md`）。这里记录每一层应持续坚持的方向和仍待加强的部分，而不是从零开始的路线图。

### 更完整的工具运行时

- 更严格的 JSON Schema 或类型校验。
- 工具级 `validate_input`。
- 基于 metadata 的并发分批。
- 工具超时、取消和进度事件。
- 大结果持久化的读取和索引能力。

### 更完整的权限系统

- `allow / deny / ask / passthrough` 决策模型。
- 用户、项目、本地、CLI flag、会话规则的优先级。
- deny 优先于动态组装、hook、ask 和 allow。
- 写操作和危险 shell 的更细分类。
- 权限请求和用户确认的可审计记录。

### 更完整的沙箱系统

- workspace、worktree、额外工作目录和外部目录的边界模型。
- Windows、POSIX、WSL、Cygwin 路径形式的统一规范化。
- 对已存在路径使用 realpath，对不存在目标使用解析后的绝对路径和父目录检查。
- external directory permission pattern 的稳定生成。
- denied path pattern 在工具组装和执行入口同时生效。

### 更完整的 prompt 系统

- 稳定 section 与动态 section 分离。
- section 级缓存。
- 根据真实状态注入 memory、skill、task、workspace 和语言偏好。
- 避免 keyword guessing，优先基于 registry 和文件状态。

### 更完整的上下文系统

- token 级估算或 provider usage 优先。
- compact summary 的结构化格式。
- transcript 检索。
- session memory。
- long-term memory。
- 压缩后的 recent file/task/skill 恢复。

### 更完整的任务系统

- Todo 作为当前执行计划。
- Task 作为跨会话、可恢复、可依赖的工作单元。
- 任务 claim、owner、blockedBy、completed 状态。
- 未来支持多 agent 时，任务系统成为协调边界。

### 更完整的插件和 skill 系统

- skill catalog 只暴露摘要。
- skill 内容按需加载。
- 插件通过 registry、hook 和 tool metadata 接入。
- 插件不能绕过权限和 workspace 边界。

### 更完整的可观测性

- 结构化 trace。
- usage 统计。
- transition log。
- tool call timeline。
- compact/transcript 索引。
- 用于回放和测试的 session artifact。

## 如何使用本文

当我们设计一个新能力时，先问：

1. 它应该进入哪一层？
2. 它是否要求主循环理解过多细节？
3. 它能否通过 registry、hook、prompt section、compaction layer 或 transition 接入？
4. 它是否保持了安全边界？
5. 它是否遵守 deny-first，而不是让 allow、hook 或动态组装覆盖拒绝？
6. 它的路径处理是否经过规范化、解析和沙箱边界判断？
7. 它是否让上下文更可治理，而不是更混乱？
8. 它是否让未来调试和测试更容易？

如果答案不清楚，先整理边界，再写代码。


