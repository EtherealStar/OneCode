# Prompt Input 光标与补全轻量计划

## 目标

改进 CLI 交互式 prompt 输入体验：

- OneCode prompt 输入激活时隐藏终端原生光标。
- 根据 `BufferState.cursor` 渲染 OneCode 自己的光标。
- 让 `Enter` 和 `Tab` 都能接受当前激活的 `/` 命令建议。

这是纯 CLI 交互层改动。实现应限制在 `ui/cli/prompt_input/` 和聚焦测试内。

## 参考说明

`docs/references/ui/components/PromptInput/` 下的参考 PromptInput 实现适合借鉴交互规则，不适合照搬结构。

可借鉴点：

- 把光标位置作为输入状态（`cursorOffset`），并传入渲染逻辑。
- 在普通提交前先处理 suggestion 和 selection 的冲突。
- 当 suggestions 可见时，避免 `Enter` 在同一步既接受 UI 选择又提交 prompt。

不要复制 React/Ink 组件形态。OneCode 已经有更清晰的 reducer 与 terminal driver 边界。

## 实现草图

1. 更新 `ui/cli/prompt_input/terminal.py`。
   - 使用 ANSI `?25l` 和 `?25h` 增加终端光标隐藏/显示 helper。
   - 交互 session 开始时隐藏原生光标。
   - 在正常结束、取消、`Ctrl-C`、`Ctrl-D` 和异常退出时恢复原生光标。
   - 在 prompt/text/password 模式下，根据 `state.buffer.cursor` 渲染自定义光标。
   - 对光标所在字符使用反色视频；当光标在行尾时渲染一个反色视频空格。

2. 更新 `ui/cli/prompt_input/reducer.py`。
   - 保持 `Tab` 接受当前激活 suggestion 的行为。
   - 让 `Enter` 在普通提交前先接受当前激活的 suggestion。
   - 确保接受 suggestion 后关闭 suggestions，并且不会因为精确匹配立即重新打开。
   - 保持 text、password、select、confirm 和 page 模式的现有行为。

3. 保持职责边界不变。
   - `editor.py` 继续负责光标感知的 buffer 编辑。
   - `reducer.py` 继续负责输入语义。
   - `terminal.py` 继续负责原始终端 I/O 和渲染。
   - 不修改 `core/loop.py`、工具、provider、权限或 prompt 组装。

## 边界情况

- `/sta` + `Enter` 应变成 `/status`，并继续停留在编辑状态。
- 接受 suggestion 后，`/status` + `Enter` 应提交命令。
- `/sta` + `Tab` 应继续可用。
- `read @sr` + `Enter` 应接受当前激活的文件/目录 suggestion。
- password 输入不能泄露已输入的 secret 文本。
- 即使输入被中断，也必须恢复终端原生光标。

## 测试

新增或更新聚焦测试：

- `tests/test_cli_prompt_input_state.py`
  - `Enter` 能接受命令 suggestions。
  - 接受 suggestion 后关闭 suggestion dropdown。
  - 第二次 `Enter` 提交已接受的 slash command。
  - 现有 `Tab` 补全仍然可用。

- `tests/test_cli_prompt_input_terminal.py`
  - 渲染后的 prompt 在 `BufferState.cursor` 位置显示自定义光标。
  - 交互 session 前后会输出原生光标隐藏/显示转义序列。
  - 输入中断时会恢复原生光标。
  - password 渲染仍然不包含 secret 文本。

建议验证：

```powershell
uv run python -m pytest tests/test_cli_prompt_input_state.py tests/test_cli_prompt_input_terminal.py tests/test_cli_prompt_input_suggestions.py -q
```
