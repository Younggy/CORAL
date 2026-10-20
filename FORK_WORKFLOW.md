# Younggy/CORAL 开发约定

本 fork 的首个固定版本是 `v0.1`，包含从 EvalForge 迁入的原生 Optiver RV 任务。
本 fork 的上游基准为 `0123dfb939b35228cf2c1fde224cd0561e727408`。
`v0.2.0` 在原生研究流程上增加 Antigravity CLI runtime。
`v0.2.1` 更新 Optiver 模型配置与本机 binding 使用说明：Codex 6.1 Sol xhigh、
DeepSeek Flash high、Gemini 3.8 Flash High。

- `feature`：日常开发和快速迭代分支。
- `develop`：验证通过后沉淀的稳定分支。
- 发布流程：在 `feature` 修改并验证，合并到 `develop`，再在 `develop` 对应提交打版本 tag。
- `v0.1` 之后的升级使用新的 tag，不移动或覆盖已有发布 tag。
- `origin` 指向 `Younggy/CORAL`；`upstream` 指向官方仓库。
- `main` 保留为当前上游基准，不作为本 fork 的日常开发分支。

本约定适用于自己的 fork；向官方仓库贡献时仍遵守上游 `dev` 分支的 PR 规则。

## Optiver RV 接入

任务及本机验证方法见 [examples/optiver_rv/README.md](examples/optiver_rv/README.md)。
验证摘要见 [MIGRATION_VALIDATION.json](examples/optiver_rv/MIGRATION_VALIDATION.json)。

当前配置为 Codex、DSH 各一个原生 Agent，使用原生分支、共享发现、笔记和反思机制。
可选三 Agent 配置见 `examples/optiver_rv/task.antigravity.yaml`，增加 Gemini 执行器，
继承原任务、grader、数据划分和资源限制。本机使用已有的 7890 代理端口。
正式 grader 为单 worker；不增加本地训练统一队列或 EvalForge 审批调度。
迁移与 Antigravity 的工具执行、隔离、中断和会话恢复验证已通过。
本次版本发布不启动新的研究；正式运行预算仍需单独确定。

旧数据与运行环境暂时只读复用 `~/Projects/evalforge`，不能删除或改名该目录。
隐藏标签、凭证、模型和运行产物不提交 Git。旧实验不续跑、不改写。
