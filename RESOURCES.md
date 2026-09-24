# OneCode 项目面试资源

## Knowledge

- [OneCode 根架构](architecture.md)
  项目的最高优先级架构来源。用于理解项目定位、六层结构、核心抽象、运行流程和依赖方向。
- [Core Beliefs](docs/design-docs/core-beliefs.md)
  解释薄主循环、动态组装、deny-first、上下文治理、错误恢复和可观测性背后的设计动机。
- [README](README.md)
  面向使用者的项目价值主张，也是本轮九项候选技术亮点的来源。
- [模块设计文档](docs/design-docs/)
  用于核实每个亮点的机制、职责边界、运行顺序和当前限制。
- [活跃 ExecPlan](docs/exec-plans/active/plan-mode-full-implementation.md)
  用于区分当前方向、正在演进的能力和已经稳定的主架构。
- [技术债台账](docs/tech-debt/tech-debt-tracker.md)
  用于校准回答边界，避免在 CLI、权限预裁剪、Bash、附件 UI 和大仓库 IO 等方面说过头。

## Wisdom (Communities)

- 真实或模拟技术面试
  用于检验回答是否能让不了解仓库的面试官快速建立心智模型，并暴露容易被追问但尚未讲清的权衡。

## Gaps

- 并发工具调用的 `400ms+ → 125ms+` 与 SubAgent 吞吐提升 `40%` 尚缺可复现的基准配置、原始样本和统计口径。
- SubAgent 的低成本模型路由在当前实现中未落地；运行时仍复用父级模型客户端。
