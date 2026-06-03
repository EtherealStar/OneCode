# 通过路径检验实现 guard / 路径安全计划

## 背景

OneCode 的沙箱边界不能依赖模型自觉或字符串前缀匹配。架构原则要求真正的安全边界发生在工具执行前，由 runtime 负责路径校验、写入范围限制、权限规则、人工确认和审计。路径判断必须先经过规范化、解析和边界检查，再进入具体工具行为。

参考资料中的沙箱边界实现给出了三个关键方向：

- 文件系统层提供纯路径 helper：Windows 路径归一、绝对路径解析、realpath 消解、glob pattern 规范化、`relative` 语义的包含/重叠判断。
- instance context 层定义项目边界：`directory` 是当前工作目录，`worktree` 是 git worktree；非 git 项目中 `worktree === "/"` 不能把整个文件系统视为项目内部。
- external directory guard 在工具执行前判断目标路径是否越界；越界时生成稳定的外部目录 permission pattern 并进入 ask/deny 流程。

## 目标

实现一个统一的路径安全 guard，使所有读写型工具在执行真实文件系统操作前都经过同一套路径检验。

该 guard 需要做到：

- 输入路径先规范化为稳定绝对路径，再做安全判断。
- 已存在路径优先使用 realpath，避免符号链接把沙箱内路径导向沙箱外。
- 不存在的写入目标也要解析为绝对路径，并基于父目录与最终目标判断是否越界。
- Windows 下归一 `/C:/...`、`/c/...`、`/cygdrive/c/...`、`/mnt/c/...` 等等价路径，并统一分隔符语义。
- 使用路径库的 `relative(parent, child)` 语义判断包含关系，不使用 `startsWith`。
- 明确区分 `inside workspace`、`inside worktree but outside cwd`、`external directory`、`denied path`。
- `denied path` 直接失败，不进入 ask 或人工确认。
- 外部目录访问生成稳定、可审计、可重复授权的 permission pattern。

## 非目标

- 不在本阶段重写完整权限系统。
- 不把所有工具逻辑迁入 guard；guard 只负责路径判定、权限入口和结果表达。
- 不依赖 prompt 文案作为安全边界。
- 不通过 ad hoc 字符串规则替代路径库解析。

## 设计

### 1. 路径规范化 API

新增或收敛到一个文件系统路径模块，提供以下纯函数：

- `windowsPath(input)`：仅在 Windows 下归一 `/C:/x`、`/c/x`、`/cygdrive/c/x`、`/mnt/c/x` 到本机盘符路径。
- `resolvePath(input)`：将输入解析为绝对路径；路径存在时使用 realpath；目标不存在时返回规范化后的绝对路径。
- `resolveWriteTarget(input)`：面向写入目标，解析目标路径，并同时返回最近可解析父目录、目标最终绝对路径和父目录 realpath 信息。
- `normalizePathPattern(pattern)`：用于 permission/glob pattern，尤其处理 `dir/*` 在 Windows 下的盘符和分隔符。
- `containsPath(parent, child)`：基于 `relative` 判断 child 是否在 parent 内。
- `overlapsPath(a, b)`：基于双向 `relative` 判断两个路径边界是否重叠。

路径模块需要尽量保持纯函数，便于单元测试覆盖不同平台输入。涉及 realpath 的函数可以封装同步文件系统调用，但不得吞掉除不存在以外的关键错误。

### 2. 项目边界模型

定义 guard 使用的边界上下文：

```py
type SandboxBoundary = {
  cwd: string
  worktree: string
  extraAllowedDirs: string[]
  deniedPatterns: string[]
}
```

边界初始化时将 `cwd`、有效 `worktree` 和 `extraAllowedDirs` 全部规范化。`worktree === "/"` 只在 POSIX 的真实根目录项目场景中谨慎使用；非 git 项目退化为 `/` 时，不参与“项目内部”判断。

路径分类结果建议使用 discriminated union：

```py
type SandboxDecision =
  | { kind: "inside_workspace"; path: string }
  | { kind: "inside_worktree"; path: string }
  | { kind: "inside_extra_allowed"; path: string; root: string }
  | { kind: "external_directory"; path: string; parentDir: string; pattern: string }
  | { kind: "denied"; path: string; reason: string; pattern?: string }
```

### 3. 判定顺序

guard 的判定顺序保持保守：

1. 解析输入路径为规范化绝对路径。
2. 匹配 deny pattern；命中后直接返回 `denied`。
3. 判断是否位于 `cwd` 内；命中为 `inside_workspace`。
4. 判断是否位于有效 `worktree` 内；命中为 `inside_worktree`。
5. 判断是否位于显式允许的额外目录内；命中为 `inside_extra_allowed`。
6. 其余路径归类为 `external_directory`，生成父目录级 permission pattern。

写入操作需要在第 1 步中对不存在目标额外处理：

- 如果目标已存在，使用目标 realpath。
- 如果目标不存在，解析父目录；父目录存在时使用父目录 realpath 拼接目标 basename。
- 如果父目录链也不存在，向上寻找最近存在父级，确认其 realpath 没有越界，再对最终目标做包含判断。

### 4. 工具执行入口

所有读写文件工具在执行真实文件系统操作前调用统一 guard：

```ts
const decision = await sandboxGuard.checkPath(target, {
  operation: "read" | "write" | "list" | "delete",
  kind: "file" | "directory",
})
```

入口行为：

- `inside_workspace`、`inside_worktree`、`inside_extra_allowed`：继续进入具体工具权限流程。
- `external_directory`：发起 `external_directory` ask，metadata 包含规范化路径、父目录和 pattern。
- `denied`：返回结构化 tool error，不执行 ask，不执行文件操作。

guard 不直接决定所有权限，只负责把路径安全分类结果传给权限层。这样可以保持工具执行入口简单，同时让 deny 在动态组装和执行入口都能生效。

### 5. Permission Pattern

外部目录 pattern 以父目录为单位生成：

- 文件目标：`dirname(resolvedTarget)/*`
- 目录目标：`resolvedTarget/*`
- Windows 下通过 `normalizePathPattern` 生成稳定 pattern。
- POSIX 下统一使用 `/` 分隔符。

permission 请求 metadata 至少包含：

```ts
{
  filepath: resolvedTarget,
  parentDir: resolvedParent,
  operation,
  kind
}
```

### 6. 错误与审计

guard 失败不应让主循环崩溃，应作为结构化工具结果返回给模型。错误结果需要包含：

- 原始输入路径。
- 规范化后路径。
- 操作类型。
- 决策类型。
- deny 命中的 pattern 或外部目录 pattern。
- 用户可理解的阻断原因。

日志中记录 guard decision，便于后续审计权限阻断、外部目录 ask 和工具实际执行之间的关系。

## 实施步骤

1. 梳理现有文件工具入口，仅定位读、写、列目录、删除、glob 等真实文件系统操作的调用点。
2. 增加路径规范化 helper，并用单元测试固定 Windows/POSIX 输入、realpath、缺失目标、`relative` 包含判断行为。
3. 增加 `SandboxBoundary` 与 `SandboxDecision` 类型，封装 `checkPath` / `checkWriteTarget`。
4. 接入 deny pattern 判断，确保 deny 优先于 allow、ask 和工具执行。
5. 接入 external directory ask，生成父目录级稳定 permission pattern。
6. 将文件类工具逐个迁移到 guard 入口；每迁移一个工具，补充对应行为测试。
7. 在动态工具组装或工具可见性阶段接入 denied path 能力裁剪，避免被 deny 的路径能力继续暴露为可执行能力。
8. 增加审计日志字段，记录 guard decision 与权限请求结果。

## 测试计划

单元测试：

- `containsPath("/tmp/proj", "/tmp/proj-file")` 必须为 false。
- `containsPath("/tmp/proj", "/tmp/proj/a.txt")` 必须为 true。
- Windows 等价路径输入归一到同一盘符路径。
- `normalizePathPattern("C:\\repo\\*")` 生成稳定 pattern。
- 已存在符号链接指向沙箱外时，realpath 后被判定为 external 或 denied。
- 不存在写入目标位于沙箱内时允许进入权限流程。
- 不存在写入目标通过符号链接父目录越界时被阻断或进入 external ask。
- 非 git 项目 `worktree === "/"` 不会让任意绝对路径变成项目内部路径。

集成测试：

- 工作区内读写正常。
- worktree 内但 cwd 外的路径不触发 external directory ask，但仍经过 deny 和工具权限。
- 外部文件读写触发 `external_directory` ask，pattern 为父目录级。
- deny pattern 命中时直接返回工具错误，不触发 ask。
- 删除或覆盖类写操作必须先通过 guard，再执行工具自身确认逻辑。

回归测试：

- 文件名相似前缀不能绕过边界，例如 `repo` 与 `repo2`。
- `..`、混合分隔符、大小写盘符、WSL/Cygwin 风格路径不能绕过边界。
- glob pattern 生成在 Windows 和 POSIX 上稳定。

## 验收标准

- 所有文件系统工具执行前都有统一 guard 检查。
- 项目内部、worktree 内、额外允许目录、外部目录、deny 路径的行为可由测试验证。
- 代码中不存在用于沙箱边界判断的字符串 `startsWith`。
- denied path 不会进入 ask，也不会执行文件系统操作。
- external directory permission pattern 稳定，重复访问同一父目录能复用授权。
- 安全阻断以结构化 tool result 返回，不导致主循环崩溃。

## 风险与注意事项

- Windows 路径大小写、盘符和 UNC 路径需要专门测试，不能只依赖 POSIX 用例。
- 不存在写入目标的父目录解析是主要绕过风险，需要覆盖符号链接父目录和多级缺失目录。
- `worktree === "/"` 的处理必须区分真实根目录项目和非 git 退化值，否则会错误放大全文件系统权限。
- guard 迁移应按工具逐步推进，避免一次性改动所有文件工具造成权限行为回归。
