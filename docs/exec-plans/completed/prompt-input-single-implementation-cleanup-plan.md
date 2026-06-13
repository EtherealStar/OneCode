# PromptInput 单一交互实现清理计划

这不是 `PLANS.md` 格式的长期 ExecPlan，而是一份轻量清理计划。它的目标不是继续迁移、兼容、兜底或包装旧实现，而是把 CLI 交互输入收敛为一个实现：`ui/cli/prompt_input/`。此前的做法把状态机叠在 prompt-toolkit line reader、Windows fallback、内置 `input()` 和 page selector 之上，导致 Backspace 等基础行为仍受旧后端影响。这类叠层必须删除。

## 目标

`prompt_input/` 成为唯一交互输入实现。所谓唯一，是指所有会读取用户按键的交互路径都必须进入同一套状态机和终端驱动：

- 主 prompt。
- slash command 建议。
- `@file` 建议。
- `/resume` 选择器。
- `/connect` provider 选择、base URL、API key、model 输入。
- 权限确认。
- MCP trust prompt。
- page mode 的滚动、退出和选择。

允许保留非交互 batch 输入，但它不是交互后端，只处理“从 stdin 读入一行文本并提交”，不负责 Backspace、补全、历史、渲染、modal 或密码输入。

## 当前必须清掉的问题

当前 `ui/cli/input.py` 仍然承担太多职责：prompt-toolkit `PromptSession`、`OneCodeCompleter`、同步 fallback、Windows `msvcrt.getwch()`、内置 `input()`、`getpass()`、key binding 和 completion adapter 都混在一个文件里。结果是新的 reducer 并没有真正拥有输入行为，真实运行时仍可能落到旧后端。

`ui/cli/pages.py` 也仍然是独立交互系统。它用 prompt-toolkit `Application` 自己处理 selector 和 page key binding。这和主 prompt 的输入模型分离，后续会继续制造“某个 modal 里按键正常，另一个 modal 里不正常”的问题。

`ui/cli/connect.py`、`ui/cli/permissions.py` 和 MCP trust prompt 现在只是调用了 `read_line_async()` 或 `read_line_sync()`，这不是统一输入系统，只是共享了一个兼容入口。

测试也沿用了迁移式思路：很多测试证明“注入假的 key reader 时 reducer 能工作”，但没有证明真实 CLI 只有一个交互实现。这样的测试会掩盖架构问题，应该删除后重写。

## 重构原则

第一，删除旧交互后端，不做第二后端。

不再保留 `PromptSession.prompt_async()` 作为主 prompt reader；不再保留 `_read_line_with_key_reader()` 作为 Windows fallback；不再维护 `OneCodeCompleter`；不再用 prompt-toolkit `Application` 实现 page selector。prompt-toolkit 如果继续存在，只能作为很薄的终端原始按键/输出能力来源，不能接管编辑、补全、历史、modal 状态或提交语义。

第二，终端驱动只做 I/O 转换。

驱动的职责是把终端事件转成 `PromptInputEvent`，再把 `PromptInputState` 渲染到屏幕。驱动不能自己决定 Backspace 删除什么、Tab 接受什么、Enter 提交什么、Up/Down 是历史还是 selector 移动。这些全部归 reducer。

第三，交互 modal 也是同一个状态机。

不要把主 prompt、selector、confirm prompt 和 page mode 看成不同系统。它们只是不同 mode：

- `prompt`：普通输入、历史、suggestions、提交 `PromptSubmission`。
- `select`：列表选择，Up/Down 改 `selected`，Enter 返回 selected value，Esc cancel。
- `confirm`：有限选项确认，例如权限和 MCP trust。
- `password`：文本输入但渲染隐藏内容。
- `page`：只读内容 viewport，滚动和退出。

这些 mode 可以共用事件、状态、渲染和 session loop。业务层只接收结果，不读键盘。

第四，`input.py` 不再是实现层。

最终 `ui/cli/input.py` 要么删除，要么只保留非常薄的 re-export，例如从 `prompt_input.session` 导出 `read_prompt()`、`read_modal()`。如果没有外部调用需要兼容，直接删除更好。

第五，测试按新架构重写。

不要为了让旧测试继续通过而反复补适配层。删除围绕 `OneCodeCompleter`、`prompt_key_bindings()`、`should_use_prompt_toolkit()`、旧 fallback 的测试。新增测试只验证：

- reducer 对编辑、suggestion、历史、selector、confirm、page 的状态转换。
- terminal driver 把常见真实 key 序列归一为同一种事件。
- session loop 只调用 reducer，不含编辑分支。
- 主 prompt、connect、permission、MCP trust、resume selector 都通过同一个 session API。
- 非交互 batch input 不进入交互驱动。

## 具体清理顺序

先重写 `prompt_input/` 内部结构，而不是先修 `input.py`。

`prompt_input/state.py` 应扩展出统一 modal 状态。保留 `BufferState`、`SuggestionItem` 和 `PromptSubmission`，新增 selector/page/confirm/password 所需字段。不要让这些类型依赖 prompt-toolkit。

`prompt_input/events.py` 应成为唯一按键事件定义。Backspace、Delete、Left、Right、Home、End、Enter、Esc、Tab、Ctrl-C、Ctrl-D、Up、Down、PageUp、PageDown 都在这里归一。终端收到 `\b`、`\x7f`、ANSI sequence、Windows special key 后，只能转成这里的事件。

`prompt_input/reducer.py` 应成为唯一交互语义入口。主 prompt 编辑、suggestion 接受、历史切换、selector 移动、confirm 选择、page 滚动都在 reducer 内完成。

新增 `prompt_input/terminal.py` 或 `driver.py`，只负责读原始 key 和渲染。这里可以使用 prompt-toolkit 的低层能力，也可以使用标准库，但不能使用 `PromptSession.prompt_async()` 或 prompt-toolkit completer。这个文件里不允许出现“删除光标前字符”这种编辑语义。

新增 `prompt_input/session.py` 的统一 API：

```python
async def read_prompt(runtime_provider) -> PromptSubmission
async def read_text(label: str, *, password: bool = False) -> str | None
async def read_confirm(title: str, options: tuple[ConfirmOption, ...]) -> str | None
async def select_item(title: str, items: tuple[SelectionItem, ...]) -> SelectionItem | None
async def show_page(renderable: object, *, title: str | None = None) -> None
```

这些 API 可以分文件实现，但必须共享同一个 reducer/session loop。

然后删除旧入口。

`ui/cli/input.py` 中删除 `PromptSession`、`OneCodeCompleter`、`prompt_key_bindings()`、`read_line_sync()`、`_read_line_with_key_reader()`、`should_use_prompt_toolkit()`、`should_use_editable_fallback()`、`_default_key_reader()` 和所有 completion adapter。若仍需要 batch input，放到一个明确命名的非交互函数里，例如 `read_batch_line()`，并只在 stdin 非交互时使用。

`ui/cli/pages.py` 删除 prompt-toolkit `Application` 实现。`show_page()` 和 `select_item()` 如果保留 public name，也只委托到 `prompt_input.session`，不能自己建 key bindings。

`ui/cli/connect.py` 不再调用 `ui.cli.input.read_line_async()`，改为调用 `prompt_input.session.read_text()` 和 `select_item()`。API key 输入使用同一个 password mode。

`ui/cli/permissions.py` 不再接受普通 `input_func` 作为主路径。测试可以注入 prompter 结果，但真实 CLI 权限确认必须走 `read_confirm()`。

`ui/cli/app.py` 的 MCP trust prompt 不再调用 `read_line_sync()`。如果 build runtime 仍是同步函数，要么在进入 build 前完成 trust 交互，要么把 trust prompt 抽成 async 装配步骤。不要因为同步装配就保留同步交互后端。

最后更新文档。

`docs/design-docs/cli-architecture.md` 必须删除“input.py 兼容层”“prompt-toolkit 读行”“Windows editable fallback”这类描述，改为说明 `prompt_input/` 是唯一交互输入实现，`input.py` 不再承载交互后端。

## 测试重写策略

删除旧输入测试中和兼容层绑定的内容，包括：

- `OneCodeCompleter` 测试。
- `prompt_key_bindings()` 测试。
- `should_use_prompt_toolkit()` 测试。
- fake `read_key` 证明 fallback 能处理 Backspace 的测试。
- 针对 `pages.py` prompt-toolkit selector 的测试。

重写为四组测试：

第一组是纯 reducer 测试。它不 mock terminal，只给事件，验证状态和结果。这组覆盖编辑、Backspace、Delete、光标移动、history、suggestions、select、confirm、page。

第二组是 key normalization 测试。它给 raw key 序列，例如 `\b`、`\x7f`、ANSI delete、Windows special key，验证都归一到同一 `KeyPressed("backspace")` 或对应事件。

第三组是 session loop 测试。它使用内存 terminal driver，验证真实 session API 不走旧模块、不包含多个后端分支。

第四组是 CLI 集成测试。它验证 `main_loop_async()`、`/connect`、权限确认、MCP trust、`/resume` selector 都调用 `prompt_input.session` 的统一 API。这里不需要保留旧测试形状，可以按新架构重写。

## 验收标准

代码层面：

- `ui/cli/input.py` 删除或只剩非交互 batch helper/re-export。
- 没有 `PromptSession.prompt_async()`。
- 没有 `OneCodeCompleter`。
- 没有 `_read_line_with_key_reader()`。
- 没有 `msvcrt.getwch()` 直接散落在 `ui/cli/input.py`；如使用 Windows key reader，只能位于 prompt_input 的 terminal driver。
- `pages.py` 不再创建 prompt-toolkit `Application`。
- `connect.py`、`permissions.py`、MCP trust、resume selector 都不直接读 stdin。

行为层面：

- 运行 `uv run python -m ui.cli.app`，输入 `abc` 后按 Backspace，屏幕和提交值都变成 `ab`。
- `/` 建议、`@` 文件建议、`/resume` selector、`/connect`、权限确认、MCP trust 都在同一交互系统内工作。
- API key 不回显。
- 非交互 batch input 仍能提交一行，但不参与交互逻辑。

测试层面：

- 新输入测试不再引用旧兼容函数。
- 全量测试通过。
- 必须做一次人工交互验收；不能再只用 fake key reader 证明 Backspace。

## 明确不做

不继续修旧 fallback。

不为了让旧测试继续通过保留 `input.py` 的历史实现。

不保留 prompt-toolkit `PromptSession` 作为“临时安全路径”。

不在 connect、permission、MCP trust 里保留同步 `input()` 或 `getpass()` 旁路。

不把 page selector 留成独立 prompt-toolkit app。

这次清理的重点不是增量迁移，而是砍掉重复输入系统，让问题只有一个入口、一个状态机、一个驱动层可以定位。
