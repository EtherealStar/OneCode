# Harness Engineering: Tech Debt Tracker 撰写指南

## 1. 核心理念

技术债务不可避免，但必须被系统性地管理，而非在出现危机时才被动应对。

核心原则：
- **不可见的债务无法管理**——一切技术债务必须被记录并可见
- **文档即代码**——Tech Debt Tracker 应与代码共存于仓库中，作为版本控制的一等制品（first-class artifact）
- **机器可读**——文档结构应便于 AI Agent 和自动化工具解析，而非面向人类项目管理

---

## 2. Tech Debt Tracker 文档结构

### 2.1 必要字段

每条技术债务条目应包含以下字段：

| 字段 | 说明 | 示例 |
|:---|:---|:---|
| **Debt ID** | 唯一标识符 | `TD-001` |
| **标题 (Title)** | 简明描述 | "用户服务中硬编码的数据库连接串" |
| **描述 (Description)** | 详细说明所采取的捷径或存在的问题 | "在 config 中写死了 DB 连接字符串，未使用 secret manager" |
| **区域/模块 (Area)** | 受影响的系统模块或文件路径 | `core/db/connection.py` / Infrastructure |
| **类型 (Type)** | 债务类别 | Security / Performance / Architectural / Test Debt |
| **引入原因 (Context)** | 为什么会产生这笔债务 | "原型阶段快速验证，未设计抽象层" |
| **影响 (Impact)** | 对系统质量或可维护性的影响 | "新增数据库实例时需手动修改多处配置" |
| **优先级 (Priority)** | 基于技术风险 | High / Medium / Low |
| **状态 (Status)** | 当前状态 | Identified / In Progress / Resolved |
| **修复方向 (Remediation Direction)** | AI 或开发者应遵循的修复思路 | "迁移到 env-based config + secret manager 模式" |
| **关联代码 (Related Code)** | 受影响的文件、模块或标记 | `core/db/connection.py:L45`, `# TODO(TD-001)` |

### 2.2 分类策略

按以下维度对技术债务进行分类：

#### 按意图分类
- **有意债务 (Intentional Debt)**：为快速验证而有意做出的折中；应明确标注预期的修复方向
- **无意债务 (Inadvertent Debt)**：因知识盲区或缺乏标准而产生；应关联到对应的架构原则

#### 按范围分类
- **架构级债务 (Architectural Debt)**：跨模块的结构性问题，修复需要理解系统全局依赖关系
- **代码级债务 (Code-Level Debt)**：局部代码重构，通常可通过单文件或单模块改动修复
- **测试债务 (Test Debt)**：缺失的测试覆盖，导致 AI Agent 无法通过测试验证修改的正确性
- **安全债务 (Security Debt)**：已知但未修复的安全隐患
- **Flag 债务 (Flag Debt)**：遗留的、不再使用的 Feature Flag（Harness 特有关注点）

---

## 3. 模板

### 3.1 Markdown 文件模板 (`tech-debt-tracker.md`)

以下是一个可直接放入代码仓库的 tech debt tracker 模板：

```markdown
# Tech Debt Tracker

## Active Debt Items

### TD-001: [简明标题]

- **Type:** Architectural / Code / Test / Security / Performance
- **Area:** [affected module or file path]
- **Priority:** High / Medium / Low
- **Status:** Identified / In Progress / Resolved
- **Impact:** [对系统质量或可维护性的影响]

**Description:**
[详细描述技术债务的内容]

**Context (Why it was accepted):**
[为什么当初接受了这个折中]

**Remediation Direction:**
[AI 或开发者应遵循的修复思路和目标架构]

**Related Code:**
- `path/to/affected/file.py:L45` — `# TODO(TD-001)`
- `path/to/another/file.py:L120`

**Architectural Constraints:**
[修复时需遵循的架构约束或依赖关系]

---

### TD-002: [下一条]
...

---

## Resolved Items (Archive)

### TD-000: [已解决的示例]

- **Resolution:** [简述如何解决以及最终采用的方案]
- **Related PR:** #456
```

### 3.2 精简表格模板

适合在 README 或文档中快速概览：

| Debt ID | Title | Type | Area | Priority | Status | Impact | Remediation Direction |
|:---|:---|:---|:---|:---|:---|:---|:---|
| TD-001 | 硬编码的 DB 连接串 | Security | `core/db/config.py` | High | Identified | 新增实例需多处修改 | 迁移到 env-based config |
| TD-002 | 缺失的集成测试 | Test | `services/api/` | Medium | In Progress | Agent 无法验证修改正确性 | 补充 pytest 集成测试 |

---

## 4. 关键原则清单

- 文档与代码共存于仓库中，受版本控制
- 使用 linting 和 CI 检查自动标记债务
- 每条债务关联具体的代码路径和 `TODO(TD-xxx)` 标记
- 债务条目必须包含修复方向，使 AI Agent 可直接据此生成修复方案
- 架构约束和依赖关系应明确记录，避免 Agent 在修复时引入新的违规
- 已解决的债务归档保留，作为 Agent 理解代码演进的历史上下文
