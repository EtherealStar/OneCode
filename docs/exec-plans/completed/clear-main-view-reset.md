# /clear 后重置 CLI 主界面

## 目标

用户在 TTY CLI 中执行 `/clear` 后，应看到一个新的主界面：旧对话历史被终端视口自动推到上方，当前视口重新显示 OneCode banner，随后回到输入框。输入框与主界面内容之间固定至少两行空白。

这个改动只处理 CLI 展示层，不改变 agent runtime、`MessageStore`、transcript、compaction、permission 或工具执行逻辑。旧会话仍保存在 `.onecode/<old-session>/`，只是默认从当前视口隐藏。

## 设计取舍

不使用真正清空 scrollback 的控制序列，例如 `ESC[3J`。原因是 OneCode 当前 TTY 架构把定稿内容写入终端 scrollback，用户可以向上滚动回看；强行清除 scrollback 会破坏这个模型，也会让“隐藏历史”和“删除终端历史”混在一起。

采用“下推视口 + 重画主界面”的方式：打印足够多的空行，把旧内容推到当前窗口上方，然后重新打印 banner。这样用户执行 `/clear` 后看到的是新界面；如果确实想回看旧输出，仍可向上滚动，完整事实也仍在 transcript。

## 涉及文件

- `ui/cli/types.py`
  - 扩展 `CommandResult`，增加一个明确的展示意图字段，例如 `reset_main_view: bool = False`。

- `ui/cli/commands.py`
  - 修改 `_clear()`，保持现有新 session 创建与 `runtime.with_session(...)` 重绑定逻辑。
  - 返回 `CommandResult(runtime=cleared, reset_main_view=True, renderable=...)`。
  - 不在 command 层打印、清屏、处理终端高度；command 层只表达“状态已切换，主界面需要重置”。

- `ui/cli/terminal/repl.py`
  - 在 `InlineRepl._handle_command()` 中识别 `result.reset_main_view`。
  - 当 `result.runtime is not None` 时先更新 `self._runtime`。
  - 重建 prompt：`self._prompt = PromptSession(self._runtime, self._queue)`。
  - 调用新的私有方法，例如 `_reset_main_view(result.renderable)`。
  - `/clear` 的 renderable 不再走普通 inline 打印路径，避免旧的“一行提示”路径继续存在。

- `ui/cli/terminal/prompt_session.py`
  - 在输入框布局顶部加入两个空白 spacer，使任何空闲输入框和上方主界面内容至少间隔两行。
  - 这是默认布局，不提供兼容开关。

## 推荐流程

### 1. 扩展命令结果协议

在 `CommandResult` 上增加：

```python
reset_main_view: bool = False
```

这个字段的语义是：当前命令已经导致主界面上下文发生断点式切换，TTY REPL 应重建当前主界面。第一阶段只由 `/clear` 使用；以后如果有类似“新建工作区视图”的命令，也可以复用。

不要把这个字段命名为 `clear_screen`。`clear_screen` 容易暗示真的清除 scrollback，而本需求是“重置主界面并隐藏旧视口内容”。

### 2. 收敛 `/clear` 的职责

`commands._clear()` 继续做现在已经正确的 runtime 工作：

1. 记录 `old_session_id`。
2. `runtime.message_store.flush_transcript()`。
3. `runtime.state.start_new_session()`。
4. `runtime.message_store.clear_for_new_session(new_session_id)`。
5. `runtime.with_session(...)` 重绑 session-scoped services。

然后返回：

```python
return CommandResult(
    runtime=cleared,
    renderable=renderer.render_clear(old_session_id, new_session_id),
    reset_main_view=True,
)
```

这一步不要引入任何终端控制逻辑。`commands.py` 仍然只负责 slash command 分发和 runtime 结果。

### 3. 在 REPL 层执行主界面重置

修改 `InlineRepl._handle_command()` 的处理顺序：

1. `result = dispatch_command(...)`。
2. 处理 selector/connect 这类 interaction。
3. 如果 `result.runtime is not None`，立即替换 `self._runtime`。
4. 如果替换了 runtime，重建 `PromptSession(self._runtime, self._queue)`。
5. 如果 `result.reset_main_view`，调用 `_reset_main_view(result.renderable)` 并 `return`。
6. 否则保持现有 page / inline / exit 逻辑。

建议新增：

```python
def _reset_prompt_session(self) -> None:
    self._prompt = PromptSession(self._runtime, self._queue)
```

以及：

```python
def _reset_main_view(self, renderable: object | None) -> None:
    self._push_previous_view_out()
    self._console.print(renderer.render_banner(self._runtime))
    if renderable is not None:
        self._console.print(renderable)
```

其中 `_push_previous_view_out()` 可以用 `shutil.get_terminal_size((80, 24)).lines` 获取高度，打印 `height` 或 `height + 1` 个空行。这样旧内容会被推到当前视口上方，下一次 `PromptSession.read()` 启动时输入框出现在新 banner 下方。

如果 stdout 不是 TTY，`InlineRepl` 本身一般不会走到这里；但方法可保守处理：拿不到高度时默认 24 行，不需要额外降级分支。

### 4. 不保留旧展示路径

`reset_main_view=True` 时，不再执行：

```python
self._console.print(result.renderable)
```

因为 `_reset_main_view()` 已经负责打印 banner 和 clear 结果。这样 `/clear` 不会同时出现旧的一行提示路径和新的主界面路径。

`renderer.render_clear()` 可以先保留，因为它仍是“新 session 提示”的 renderable 来源；但它不再代表 `/clear` 的完整 UI 行为。完整 UI 行为属于 `InlineRepl._reset_main_view()`。

### 5. 输入框顶部固定两行间距

在 `prompt_session.PromptSession._build_application()` 的 `HSplit` 中，当前结构大致是：

```python
body = HSplit([
    _border_window(),
    prompt_window,
    suggestion_panel,
    _hint_window(...),
    _border_window(),
])
```

改为在顶部边框前插入两个 spacer：

```python
body = HSplit([
    _spacer_window(),
    _spacer_window(),
    _border_window(),
    prompt_window,
    suggestion_panel,
    _hint_window(...),
    _border_window(),
])
```

新增：

```python
def _spacer_window() -> Window:
    return Window(height=Dimension(min=1, max=1), char="")
```

这两个空白行属于 prompt_toolkit 动态区，会随 `erase_when_done=True` 一起擦除，不进入静态 scrollback。它会让普通输入、`/clear` 后输入、补全菜单前的输入都有一致间距。

## 预期调用顺序

执行 `/clear` 后的 TTY 路径应是：

```text
PromptSession.read()
  -> 用户提交 "/clear"
InlineRepl._main_loop()
  -> print_user_submitted("/clear")
  -> InlineRepl._handle_command("/clear")
dispatch_command()
  -> commands._clear()
      -> flush old transcript
      -> start new session
      -> clear current message store
      -> runtime.with_session(...)
      -> CommandResult(runtime=cleared, reset_main_view=True, renderable=clear_notice)
InlineRepl._handle_command()
  -> self._runtime = cleared
  -> self._prompt = PromptSession(self._runtime, self._queue)
  -> _reset_main_view(clear_notice)
      -> print blank lines based on terminal height
      -> print render_banner(new runtime)
      -> print clear_notice
next loop iteration
  -> PromptSession.read()
      -> dynamic prompt appears after two spacer lines
```

## 测试建议

### Command 层

更新或新增 `tests/test_cli_commands.py`：

- `/clear` 后 `result.runtime` 仍是新 session runtime。
- `result.reset_main_view is True`。
- 旧 transcript 文件仍存在。
- 新 `message_store.current_messages()` 为空或只包含后续新消息。

这个测试不检查终端空行，因为 command 层不负责 UI。

### REPL 层

在 `tests/test_cli_terminal.py` 或新文件中构造一个假的 `InlineRepl`：

- monkeypatch `dispatch_command` 返回 `CommandResult(runtime=new_runtime, reset_main_view=True, renderable=Text("..."))`。
- 调用 `_handle_command("/clear")`。
- 断言：
  - `repl._runtime is new_runtime`。
  - `repl._prompt` 被重建，并且 completer 间接持有新 runtime。
  - 输出包含新的 banner。
  - 输出包含 clear notice。
  - 输出开头有至少若干空行，或者 `_push_previous_view_out()` 被调用。

为避免测试依赖真实终端高度，可以把 `_terminal_height()` 拆成小方法并 monkeypatch 返回固定值，比如 5。

### Prompt 布局层

已有 `PromptSession` 的 pipe input 测试可以继续复用。新增一个轻量测试检查 `_build_application()` 的 root `HSplit` children 前两个是 spacer 不太稳定，因为 prompt_toolkit 内部结构可能变化。

更稳的选择是把 spacer helper 保持私有，仅通过一个 focused 测试驱动：用 `DummyOutput` 渲染一次 prompt，确认 prompt 提交仍正常。间距本身建议用手工验证，因为这是终端布局行为。

## 手工验证

运行：

```powershell
uv run python -m ui.cli.app
```

步骤：

1. 输入几轮普通对话，让终端中有明显历史。
2. 执行 `/clear`。
3. 预期当前视口顶部附近出现新的 OneCode banner。
4. 旧对话默认不可见，向上滚动仍可看到。
5. banner/clear notice 与输入框之间至少有两行空白。
6. 输入 `/status`，确认 session id 是新 session。
7. 输入 `/` 或 `@`，确认补全仍可用，且使用新 runtime 状态。

## 非目标

- 不删除旧 session 的 transcript。
- 不清空终端真实 scrollback。
- 不修改 `core/loop.py`。
- 不修改 `MessageStore` 的持久化语义。
- 不把 `/clear` 改成备用屏幕页面。
- 不引入新的 UI 框架或全屏主界面。

## 未来可选

如果后续希望“完全不可向上滚动看到旧历史”，可以新增显式配置，例如 `clear_scrollback_on_clear = true`，再使用更强的终端控制序列。但这应是单独的、有风险提示的行为，不作为默认 `/clear` 语义。
