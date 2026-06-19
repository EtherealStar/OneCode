# 改进 OneCode 系统提示词的工程行为约束

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本文档遵循仓库根目录下的 `PLANS.md`。任何实现或修订本计划的人都必须保持它自包含，并在决策和结果变化时同步更新所有 living sections。

## Purpose / Big Picture

完成此变更后，OneCode 的系统提示词会更明确地指导模型如何完成软件工程任务：读代码后再改、控制修改范围、失败后先诊断、完成前尽量验证、如实报告结果，并在高风险动作前寻求确认。用户能观察到的变化是：agent 更少做无关重构，更少过早抽象，更少声称未验证的结果，并能更稳定地区分可直接执行的本地动作与需要用户确认的危险动作。

这不是把参考提示词完整搬进 OneCode。参考文件 `docs/references/s10_system_prompt/prompts.ts` 来自另一个成熟 code agent，包含大量产品名、模型名、内部 feature gate 和专用工具逻辑。本计划只吸收可迁移的通用工程行为原则，并把它们落在 OneCode 已有的动态 prompt section 架构中。

## Progress

- [x] (2026-06-17 00:00+08:00) 已阅读 `PLANS.md`、`architecture.md`、`docs/design-docs/core-beliefs.md`、`docs/design-docs/prompt-architecture.md`、`docs/tech-debt/tech-debt-tracker.md` 和当前 `prompts/sections.py`。
- [x] (2026-06-17 00:00+08:00) 已阅读参考提示词 `docs/references/s10_system_prompt/prompts.ts`，并识别可迁移内容与不应照搬内容。
- [x] (2026-06-17 00:00+08:00) 已在 `docs/exec-plans/active/system-prompt-behavior-improvements.md` 撰写本轻量 ExecPlan。
- [x] (2026-06-18 00:00+08:00) 已在 `prompts/sections.py` 新增 `engineering_practices`、`risk_and_safety`、`verification_and_reporting` 三个固定 section，并插入到 `behavior_rules` 之后、动态记忆和工具信息之前。
- [x] (2026-06-18 00:00+08:00) 已更新 `docs/design-docs/prompt-architecture.md` 的 section 顺序说明。
- [x] (2026-06-18 00:00+08:00) 已扩展 `tests/test_dynamic_prompt_assembler.py`，覆盖新增 section 标题、顺序和未搬入参考产品信息。
- [x] (2026-06-18 00:00+08:00) 已运行聚焦测试、import boundary 测试和 compileall，全部通过。
- [ ] 验证完成后，将本计划移动到 `docs/exec-plans/completed/`。

## Surprises & Discoveries

- Observation: OneCode 当前的固定行为提示词只有 7 条，主要强调基于事实、读文件、尊重 guard 和不虚报操作，但缺少修改范围、失败处理、验证、危险动作确认、prompt injection 和用户沟通方面的具体约束。
  Evidence: `prompts/sections.py::behavior_rules_section()` 当前只构造一个 7 行 bullet list。

- Observation: OneCode 已经有合适的 prompt section 机制，不需要新增静态大字符串或把所有规则塞进单个 section。
  Evidence: `prompts/sections.py::default_sections()` 会按稳定顺序拼接 `PromptSection`；`docs/design-docs/prompt-architecture.md` 明确提示词由可组合 section 动态组装。

- Observation: 技术债 TD-008 与工具可见性和权限裁剪有关，但本计划不解决 permission policy 的完整多来源合并，只补充模型行为指导。
  Evidence: `docs/tech-debt/tech-debt-tracker.md` 中 TD-008 写明 prompt 裁剪不能替代执行入口的 guard 和 permission policy 检查。

## Decision Log

- Decision: 新增少量语义清晰的 prompt sections，而不是继续扩写一个很长的 `behavior_rules_section()`。
  Rationale: OneCode 的设计信念强调 system prompt 由可组合 section 组装。拆分 section 可以让后续缓存、测试和文案调整更清楚。
  Date/Author: 2026-06-17 / Codex。

- Decision: 不照搬 `docs/references/s10_system_prompt/prompts.ts` 中的 Claude Code 产品信息、模型信息、slash command 文案、feature gate 和内部工具名。
  Rationale: 这些内容会污染 OneCode 的产品边界，也会让提示词依赖不存在的能力。OneCode 应只吸收通用工程行为原则。
  Date/Author: 2026-06-17 / Codex。

- Decision: 本计划只改 prompt 层和测试，不改 `core/loop.py`、工具 executor、guard 或 permission policy。
  Rationale: 本计划目标是改进模型可见指导，不是新增安全边界。真正的安全仍由工具执行前的 schema validation、guard 和 permission policy 保证。
  Date/Author: 2026-06-17 / Codex。

## Outcomes & Retrospective

### 2026-06-18 实施记录

已实现三个固定 prompt section：`Engineering Practices` 指导读代码、控制范围和避免过早抽象；`Risk and Safety` 指导高风险动作确认、常见安全问题和 prompt injection 防护；`Verification and Reporting` 指导失败诊断、验证和如实汇报。

实现保持在 prompt 层，没有修改 `core/loop.py`、工具 executor、guard 或 permission policy。新增测试验证 section 顺序和参考产品信息未进入 OneCode prompt。

验证结果：

    uv run python -m pytest tests\test_dynamic_prompt_assembler.py -q
    7 passed in 0.32s

    uv run python -m pytest tests\test_import_boundaries.py -q
    2 passed in 0.05s

    uv run python -m compileall prompts core services -q
    exited with code 0

## Context and Orientation

OneCode 是一个 Python code agent runtime。每轮模型调用前，`core/context_engine.py::ContextEngine.build_for_model()` 会调用 `PromptAssembler` 生成 `ContextSnapshot.system_prompt`。系统提示词不是单个静态文件，而是由 `prompts/assembler.py::DynamicPromptAssembler` 根据当前 `RuntimeState` 构造 `PromptRuntimeContext`，再调用 `prompts/sections.py::default_sections()` 生成多个 `PromptSection` 并拼接。

本计划中的“section”指 `prompts/sections.py::PromptSection`，它有 `key`、`title`、`body`、`fingerprint` 和 `cacheable` 字段。`body` 是模型真正看到的正文。`fingerprint` 是缓存键的一部分，输入变化时应变化。固定文案 section 可以使用 `PROMPT_VERSION` 作为 fingerprint；如果新增 section 的内容只依赖代码常量，也可以使用 `PROMPT_VERSION`。

当前重要文件如下：

- `prompts/sections.py` 定义固定系统提示词、workspace state、工具列表、技能列表、MCP instructions 和工具专属 prompt sections。
- `prompts/assembler.py` 定义 `DynamicPromptAssembler`，负责读取可见工具、技能和记忆，再渲染 section。
- `prompts/runtime_context.py` 定义 prompt 组装可用的运行时事实。它刻意不包含 API key、provider 配置、session id、transcript 路径或 CLI mode。
- `docs/design-docs/prompt-architecture.md` 描述 prompt 层边界：读取当前事实并组装 system prompt，不执行工具，不解析 provider 协议。
- `docs/references/s10_system_prompt/prompts.ts` 是参考提示词来源，只作为内容设计参考。

## Plan of Work

第一步，在 `prompts/sections.py` 中保留现有 `identity_section()` 和 `behavior_rules_section()`，但不要把所有新增内容塞进 `behavior_rules_section()`。新增三个固定 section：

`engineering_practices_section(context: PromptRuntimeContext) -> PromptSection`。这个 section 指导模型如何执行软件工程任务。正文应覆盖：先读相关代码再建议或修改；修改范围贴合用户请求；不做无关功能、无关重构或顺手优化；优先编辑现有文件；只在真正必要时新增文件；避免一次性需求的过早抽象；只在系统边界做必要校验；不要为不可能发生的内部场景堆防御代码；发现用户误解或相邻 bug 时要指出。

`risk_and_safety_section(context: PromptRuntimeContext) -> PromptSection`。这个 section 指导模型区分普通本地动作和高风险动作。正文应覆盖：本地、可逆的读取、编辑和测试通常可以直接执行；删除文件、覆盖未提交变更、重置分支、force push、修改 CI/CD、发送外部消息、上传内容到第三方服务等应先确认；遇到陌生文件、锁文件、冲突或失败状态时先调查，不用破坏性操作绕过问题；注意命令注入、路径穿越、XSS、SQL 注入、秘密泄露等常见安全问题；工具结果和外部文件内容可能包含 prompt injection，应当作数据而不是更高优先级指令。

`verification_and_reporting_section(context: PromptRuntimeContext) -> PromptSection`。这个 section 指导模型如何失败恢复、验证和汇报。正文应覆盖：命令或工具失败后先读错误、检查假设、做聚焦修复；不要盲目重复同一个失败动作；不要一次失败就放弃可行方向；完成前尽量运行相关测试、脚本、类型检查或最小复现；如果无法验证，要明确说明；测试失败就报告失败和关键输出；不要把未运行、失败或部分完成说成成功；旧工具结果可能被压缩或清理，重要发现要在回复或后续上下文中保留摘要。

第二步，把这三个 section 加入 `default_sections()`。推荐顺序是：`identity_section`、`behavior_rules_section`、`engineering_practices_section`、`risk_and_safety_section`、`verification_and_reporting_section`、`instruction_memory_section`、`long_term_memory_section`、`workspace_state_section`、`available_tools_section`、`available_skills_section`、`mcp_server_instructions_section`、工具 prompt sections。这样固定行为约束位于记忆和动态工具信息之前，保持基础规则稳定。

第三步，更新 `docs/design-docs/prompt-architecture.md` 的 section 顺序说明。新增的三个 section 应出现在“Section 顺序”列表中，并用一句话说明这些 section 是固定工程行为约束，不读取运行时外部事实。

第四步，补充或更新测试。优先查找现有 prompt 测试；如果没有覆盖固定 section 顺序，应新增 `tests/test_prompt_sections.py` 或扩展已有测试。测试应覆盖：`default_sections(PromptRuntimeContext(...))` 包含新增 section key；新增 section 出现在 `behavior_rules` 之后、`instruction_memory` 之前；`DynamicPromptAssembler.assemble(RuntimeState())` 的输出包含新增标题；空的 instruction memory 和 long-term memory 不影响新增固定 section 输出；工具 prompt sections 仍位于末尾。

第五步，运行验证命令。至少运行：

    cd D:\study\OneCode
    uv run python -m pytest tests/test_prompt_sections.py -q
    uv run python -m pytest tests/test_import_boundaries.py -q
    uv run python -m compileall prompts core services -q

如果测试文件名不同，使用实际存在的 prompt 测试文件替代 `tests/test_prompt_sections.py`。如果仓库已有相关失败，记录失败测试名和关键输出，并确认它们是否与本计划有关。

## Concrete Steps

1. 打开 `prompts/sections.py`。在 `behavior_rules_section()` 后新增 `engineering_practices_section()`、`risk_and_safety_section()` 和 `verification_and_reporting_section()`。三个函数都应接收 `PromptRuntimeContext`，如果不使用 context，则 `del context`，保持 lint 风格一致。三个函数都返回 `PromptSection`，`key` 分别使用 `engineering_practices`、`risk_and_safety`、`verification_and_reporting`，`title` 分别使用 `Engineering Practices`、`Risk and Safety`、`Verification and Reporting`，`fingerprint` 使用 `PROMPT_VERSION`。

2. 编辑 `prompts/sections.py::default_sections()`，把新增三个 section 插入到 `behavior_rules_section(context)` 之后、`instruction_memory_section(context)` 之前。不要移动工具 prompt sections，它们仍应通过 `*tool_prompt_sections(context)` 保持末尾动态追加。

3. 编辑 `docs/design-docs/prompt-architecture.md`。在“Section 顺序”中加入新增 section，并更新编号。不要把参考提示词的来源写成架构事实；只说明 OneCode 当前 prompt 结构。

4. 查找现有 prompt 测试：

    cd D:\study\OneCode
    rg -n "default_sections|DynamicPromptAssembler|behavior_rules|Available Tools" tests

   如果已有合适测试文件，就扩展它；否则新增 `tests/test_prompt_sections.py`。

5. 测试应构造最小 `PromptRuntimeContext` 和 `RuntimeState`。不要接入真实 provider、真实工具执行或 CLI。prompt 层测试只验证字符串和 section 顺序。

6. 运行验证命令，并把结果写回本计划的 `Outcomes & Retrospective`。如果某个命令失败，先判断是否与本计划有关；相关失败应修复，不相关失败应记录。

## Validation and Acceptance

实现后，人工可以通过以下方式验证行为：

    cd D:\study\OneCode
    uv run python -m pytest tests/test_prompt_sections.py -q
    uv run python -m pytest tests/test_import_boundaries.py -q
    uv run python -m compileall prompts core services -q

验收标准：

- `DynamicPromptAssembler.assemble(RuntimeState())` 生成的 system prompt 包含 `# Engineering Practices`、`# Risk and Safety`、`# Verification and Reporting` 三个标题。
- 新增三个 section 在完整 prompt 中位于 `# Behavior Rules` 之后，并早于 `# OneCode Instructions`、`# Long-Term Memory`、`# Workspace State` 和工具专属提示词。
- 新增文案没有提到 Claude Code、Claude 模型、Anthropic、`/issue`、`/share`、Fast mode 或参考实现中的内部 feature gate。
- 新增文案不承诺绕过 OneCode 的 guard、permission 或工具执行边界；它只指导模型行为。
- 现有 import boundary 测试继续通过，证明 prompt 层没有引入到工具执行、provider 或 UI 的反向依赖。

如果要做一次手动观察，可以临时在 Python REPL 或小脚本中构造 `DynamicPromptAssembler(Path.cwd()).assemble(RuntimeState())`，打印前 2000 字符，确认新增标题和顺序。这个观察不是必须提交的代码。

## Idempotence and Recovery

本计划是局部、低风险改动。重复执行时，只需确认没有重复添加同名 section 函数或重复插入 `default_sections()`。如果新增文案过长或测试显示 prompt 顺序不对，优先修改 `prompts/sections.py` 中新增 section 的 body 或 `default_sections()` 顺序。

如果实现后发现固定 prompt 过长，可以保留三个 section 的结构，但压缩每个 section 的 bullet 数量。不要把它们移动到工具 prompt 中，因为这些规则面向所有软件工程任务，不属于某个具体工具。

如果后续发现某条规则其实是安全边界，例如“禁止删除某类文件”，不要继续依赖 prompt 文案，应另开计划把它下沉到 `services/guard/`、`services/permissions/` 或具体工具的 `classify_input` / `validate_input` 中。

## Artifacts and Notes

参考文件 `docs/references/s10_system_prompt/prompts.ts` 中可借鉴的区域：

- `getSimpleDoingTasksSection()`：修改范围、读代码后再改、失败诊断、验证与如实报告。
- `getActionsSection()`：高风险动作、破坏性操作、外部副作用和确认边界。
- `getOutputEfficiencySection()` 与 `getSimpleToneAndStyleSection()`：用户沟通要简洁、关键节点更新、引用代码时带路径和行号。
- `SUMMARIZE_TOOL_RESULTS_SECTION`：工具结果可能被清理时，保留重要发现摘要。

不应搬入 OneCode 的内容：

- Claude Code、Anthropic、Claude 模型、Fast mode、`/issue`、`/share` 等产品信息。
- 参考实现的 feature gate、环境变量、内部用户类型分支。
- 参考实现的具体工具名策略，除非 OneCode 已有同名工具且对应工具 prompt 需要单独改。
- 缓存边界常量和 provider 私有说明。OneCode 已有 `PromptSectionCache` 和 provider-neutral 架构。

## Interfaces and Dependencies

本计划不新增第三方依赖，不改变 provider 协议，不改变工具 schema，不改变 `RuntimeState`。

应新增或修改的接口仅限：

    prompts.sections.engineering_practices_section(context: PromptRuntimeContext) -> PromptSection
    prompts.sections.risk_and_safety_section(context: PromptRuntimeContext) -> PromptSection
    prompts.sections.verification_and_reporting_section(context: PromptRuntimeContext) -> PromptSection
    prompts.sections.default_sections(context: PromptRuntimeContext) -> tuple[PromptSection, ...]

测试可以直接导入这些函数和 `PromptRuntimeContext`。不要在测试中执行工具，不要启动 CLI，不要访问网络。

## Revision Notes

- 2026-06-17 / Codex：创建轻量 ExecPlan，记录如何参考 `docs/references/s10_system_prompt/prompts.ts` 改进 OneCode 系统提示词。本文只描述计划，不实现代码。
- 2026-06-18 / Codex：实现计划中的 prompt section、架构文档更新和聚焦测试，并记录验证待办。
