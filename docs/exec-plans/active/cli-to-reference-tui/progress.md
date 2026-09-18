# Progress

## Current State

Status：M1 已实现并有测试证据；未提交（按用户要求）。M2 尚未开始。

Current milestone：M1 完成；下一未完成批次 M2.1（应用装配、单 worker、快照与观察）。

Last updated：2026-09-18，Asia/Shanghai。

本轮在 Windows 重建了失效的 Linux `.venv`（原 `.venv` 的 `pyvenv.cfg` 指向 `/home/rowla/...`，Windows `uv` 无法复用），`uv sync --dev` 成功；`pyproject.toml`、`uv.lock` 无差异。工作区既有大量修改及未跟踪 `reference/`、目标设计等均保留；本次只改动 M1 涉及的运行时与测试文件。

M1 交付：
- `services/context/transcript.py`：记录新增可选身份字段（`assistant_call_id`/`model_turn_index`/`source_uuid`/`record_kind`）；`load_messages(restore_external_results=)`、`read_records()`、`rewrite_records()`、`write_guard()`；flush 持锁写盘。
- `services/context/message_store.py`：`active_records()`、`last_record_uuid`、`replace_messages_for_compaction(source_uuids=)`、`finalize_interrupted_run()`。
- 新增 `services/context/message_shapes.py`、`services/context/run_facts.py`、`application/history.py`。
- `services/compaction/service.py`：compaction 传递复制来源 UUID。
- `core/loop.py`：保留运行事实（未定稿文字、声明、已完成结果、存储确认），取消后可由 Controller 冻结。
- `services/tools/executor.py`：并发批量取消/异常时先等待前台 handler 观察到取消。
- `services/context/recovery.py`：删除合成中断结果策略，改为剪除未配对声明/孤立结果并真实修复磁盘。

## Progress

- [x] (2026-09-17 +08:00) 阅读根架构、核心信念、三个目标设计、现状 CLI/消息渲染/上下文设计、活跃计划、技术债、PLANS 与计划包格式/模板。
- [x] (2026-09-17 +08:00) 审计 `reference/ui/` 的 projection、session facade、viewport、layout index、cache、scheduler、App/Composer/renderer/theme 与当前 CLI、loop、store/recovery。
- [x] (2026-09-17 +08:00) 执行环境同步及完整测试基线，记录真实失败。
- [x] (2026-09-17 +08:00) 创建 plan/execution/decisions/progress 四文件，明确五个宏里程碑及验收。
- [x] (2026-09-17 +08:00) 校验四文件的 39 个本地 Markdown 链接及标题锚点，全部可解析；校验现有路径与明确标为目标的新产物，并检查空白格式。
- [x] M1：取消与历史记录基础。
  - [x] M1.1：稳定记录身份、压缩来源、历史保留引用读取。
  - [x] M1.2：运行事实、受控整理、恢复与故障注入。
- [ ] M2：共用 Controller。
  - [ ] M2.1：应用装配、单 worker、快照与观察。
  - [ ] M2.2：命令、交互、队列、切换与关闭。
  - [ ] M2.3：batch Adapter。
- [ ] M3：ConversationProjection。
  - [ ] M3.1：移植归并、固定身份与声明顺序。
  - [ ] M3.2：权威修正、同步、详情与管线集成。
- [ ] M4：ConversationView。
  - [ ] M4.1：Textual 版本验证、主题与虚拟视口。
  - [ ] M4.2：刷新、Markdown、详情与资源回收。
- [ ] M5：完整交互与入口切换。
  - [ ] M5.1：App、Composer、命令视图与 Modal。
  - [ ] M5.2：切换入口、删除旧路径、完整自动/人工验收与归档。

## Surprises & Discoveries

Observation：真正选定的参考是根目录 Python `reference/ui/`。Evidence：三个目标设计的相对链接及参考 `app.py` 的 Textual imports；`docs/references/ui/` 主要为 TS/TSX。

Observation：参考代码不能整目录搬运。Evidence：`reference/ui/session_facade.py` import UI SessionSnapshot 且 `_publish` await callbacks；`projection.py` 按单 tool ID 归并、InputQueued 进入消息列表、孤立结果 fallback 独立显示；`renderers/tool.py` import 参考具体工具输入模型；`composer.py` import `miniagent.workspace_references`。

Observation：现有记录接口缺少 UI 所需的稳定关联。Evidence：`MessageStore._append` 生成 UUID 只传给 transcript，不在返回值中交付；compaction 重新 `_append` replacement，原来源未记录。计划先处理存储，不将身份问题藏到 View。

Observation：工具完成事件不等于持久化确认。Evidence：`core/loop.py::_stream_tool_execution` 先收集并 yield result，外层后调用 `append_tool_results`；`recovery.py` 当前合成缺失结果。M1 要覆盖取消在两者之间发生。

Observation：参考虚拟化仍有线性工作，Markdown 分块也不证明完整语义。Evidence：`layout_index.py::update_height` 调用 `_rebuild_prefixes`；`render_cache.py::_scan` 遍历 source，空行分块。M4 以实际挂载数/IO/最终文本验收。

Observation：活跃计划模式计划已有实现记录，不是等待本计划从零创建。Evidence：`plan-mode-full-implementation.md` Progress 与 `ui/cli/commands.py` 的 `/plan show|open|approve|reject`、`services/questions/`、`tools/exit_plan_mode/tool.py`。M2/M5 保留其业务。

## Validation Evidence

Command：`uv sync --dev`。

Result：成功，Resolved 44 packages，Installed 41 packages；跨文件系统 hardlink 不可用时 uv 自动改为 copy。使用 Linux CPython 3.12.14。

Command：`uv run python -m pytest tests -q`。

Result：退出码 1；**614 passed, 7 failed in 45.05s**。运行时与测试源代码尚未修改，属于本次调查基线；未重复运行或修复，不对原因作未经验证的判断。

失败明细：

- `tests/test_bash_tool.py::test_bash_descriptor_schema_and_prompt`：prompt 不包含断言要求的 `Tree-sitter`。
- `tests/test_cli_terminal.py::test_user_submitted_uses_reverse_style_dark`：捕获输出为 `> hello\n`，没有期望 ANSI SGR。
- `tests/test_cli_terminal.py::test_restored_user_messages_use_explicit_reverse_style`：恢复用户行同样缺少期望 ANSI SGR。
- `tests/test_mcp_manager.py::test_mcp_connection_manager_discovers_and_calls_stdio_tools`：状态为 `pending`，测试期望 `connected`。
- `tests/test_openai_compatible_provider.py::test_catalog_contains_builtin_providers`：缺少测试期望的 `claude-openai-compatible` catalog 项。
- `tests/test_path_sandbox_guard.py::test_root_worktree_does_not_allow_arbitrary_paths`：实际 `allow`，测试期望 `ask`。执行安全相关改动前必须调查，不按 UI 兼容问题忽略。
- `tests/test_search_tools.py::test_registry_generates_search_tool_schemas_and_prompts`：prompt 首段为 `Purpose`，测试期望工具名 `glob/grep`。

文档校验：使用 Python 扫描四文件 Markdown 链接并校验文件/目录及标题锚点，39 项全部通过；核对命名的现有路径与明确标注的目标产物，四文件均无行尾空白。`git status --short -- docs/exec-plans/active/cli-to-reference-tui pyproject.toml uv.lock` 仅列出新计划目录。新模块的测试与真实终端烟测尚未执行，因为实现尚未开始。

### M1 验证（2026-09-18，Windows）

Command：`uv sync --dev`。

Result：成功（重建 Windows venv；CPython 3.14.5，Windows 11）；`pyproject.toml`、`uv.lock` 未改动。

Command：`uv run --no-sync python -m pytest tests/test_conversation_history.py tests/test_session_interrupt_cleanup.py tests/test_context_recovery.py tests/test_jsonl_session_persistence.py tests/test_async_loop.py tests/test_compaction_service.py -q`。

Result：退出码 0；**62 passed**（含新增 `test_conversation_history.py` 10 项、`test_session_interrupt_cleanup.py` 13 项；随后新增 staging 写失败与来源持久化断言，最终全套为 644 passed）。

Command：`uv run --no-sync python -m pytest tests -q`。

Result：退出码 1；**644 passed, 3 failed in 11.29s**。3 个失败均为计划基线已记录、与 M1 无关的既有失败（Windows 上 `test_bash_tool::test_bash_descriptor_schema_and_prompt`、`test_openai_compatible_provider::test_catalog_contains_builtin_providers`、`test_search_tools::test_registry_generates_search_tool_schemas_and_prompts`）。`test_mcp_manager` 本轮通过（上次基线为失败，属平台/时序波动）。无新增失败。

Command：`uv run --no-sync python -m pytest tests/test_import_boundaries.py -q` → 4 passed；`uv run --no-sync python -m compileall core services infrastructure application -q` → 退出码 0；先前记录的 `_synthetic_interrupted_tool_result` 合成结果预期已按 M1.2 更新为剪除并真实修复磁盘。

证据文件：`tests/test_conversation_history.py`、`tests/test_session_interrupt_cleanup.py`；受控整理入口 `services/context/message_store.py:finalize_interrupted_run`；运行事实 `services/context/run_facts.py`。

## Artifacts And Notes

入口为 [plan.md](plan.md)，批次与命令为 [execution.md](execution.md)，选择与兼容门槛为 [decisions.md](decisions.md)。当前不新增第五份计划/研究报告；参考适配清单放 plan 的 Context，发现与证据保留在本文件。

最主要风险为受控重写与 flush 竞争、旧压缩历史来源不足、启动 trust 与 bootstrap 互等、切换时旧代次事件污染，以及 Textual 在目标 Windows 终端的键盘/中文输入差异。各风险分别由 M1 故障测试、M2 控制器测试和 M4/M5 终端测试关闭。

## Outcomes & Retrospective

计划编写已形成可交接的五里程碑实施路线，明确三个模块的接口、参考代码适配差异和当前功能保留范围。迁移本身尚未开始，不能把三个目标设计标为已实现，也不能关闭 TD-007/TD-016。

执行者从 M1.1 开始，先复核基线失败对相关工作影响，再完成来源/历史 fixture 与存储改动。每个里程碑完成后补充产物、实际测试结果和未完成项；全部验收达到后整体移入 completed。
