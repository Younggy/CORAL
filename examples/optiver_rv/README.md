# Optiver RV on native CORAL

本任务从 EvalForge v0.1.3（`0409503`）迁入，只适配 Optiver 数据与评测。
CORAL 上游基准为 `0123dfb939b35228cf2c1fde224cd0561e727408`；迁移时只适配任务，后续 v0.2.0 增加 Antigravity runtime。
调度、分支、笔记、反思、停滞处理与候选记录均使用原版机制。
适配器不导入 `evalforge`、旧 controller、family/frontier、审核或 run_boundary。

## 接口

- `seed/` 的三个文件逐字取自上一轮初始提交 `90a13aef6126708f1fcce87b1ed4612fcbc6c000`。
- `features.combine(book, trade)` 为单只股票返回以 `time_id` 为索引的特征。
- `train.py` 接收四个路径：TRAIN 特征、推理特征、配置、输出目录。可增加候选辅助模块。
- 产出 `predictions.csv`，列为 `stock_id,time_id,prediction`，以及 `config.execution.model_artifact` 指定的模型。
- scorer 独立校验完整键覆盖、重复、有限值和非负预测，并计算全局 RMSPE。分数越低越好。失败返回 null，不能用 0 代替。
- 沿用已有 time-group-hash 划分。TRAIN 为 278,753 行；research_eval 为 64,284 行。没有重新切分；没有释放 shadow/final。

## 本机环境

本 fork 的独立 CORAL 环境在 `.venv/`，editable 安装指向本项目。
原生 agent 沙箱与 DSH 只读复用 EvalForge 中已有的本地 runtime。
候选训练只读复用旧 ML Python、依赖和已开放的输入；实际执行另受 Seatbelt 限制。
准备和验证读取旧初始提交、隐藏标签及原始指标公式，不修改旧目录。
本机依赖仍位于 `~/Projects/evalforge`，完成环境和数据迁移前不能删除或改名。
本任务尚未做跨机器移植。新验证和未来运行产物保存在本 fork 的 `results/`。

## 模型与本机 binding（v0.2.1）

| Binding | Runtime | 模型 | 推理强度 |
| --- | --- | --- | --- |
| `codex-research` | `codex` | `gpt-6.1-sol` | `xhigh`（极高） |
| `dsh-research` | `dsh` | `deepseek-flash` | `high` |
| `gemini-research` | `antigravity` | `gemini-3.8-flash-high` | 由模型 ID 选择 High |

`task.yaml` 使用前两项；`task.antigravity.yaml` 使用三项。两份任务配置直接记录模型与参数，
无需读取本机 binding 即可解析。Binding 用于其他任务复用同一组选择。
DSH 的 `runtime_options.reasoning_effort: high` 与模型名一起写入最后一层 model patch，
映射到原生 `agent-default-model.reasoningEffort`，避免被模型配置覆盖。

截至 2026-10-20，官方 [2026-10-17 更新记录](https://api-docs.deepseek.com/updates/)
说明 `deepseek-flash` 对应 DeepSeek-V4.1-Flash-1017。它是服务端滚动别名，不是冻结版本 ID。
本配置沿用 `deepseek-official` API provider；不修改 DSH Desktop 的账户登录设置。

在仓库根目录保存本机 binding（只做配置与静态检查，不请求模型）：

```sh
.venv/bin/coral setup agent --non-interactive \
  --name codex-research --runtime codex --model gpt-6.1-sol \
  --option model_reasoning_effort=xhigh
.venv/bin/coral setup agent --non-interactive \
  --name dsh-research --runtime dsh --model deepseek-flash \
  --command "$PWD/examples/optiver_rv/runtime/dsh" \
  --option reasoning_effort=high
.venv/bin/coral setup agent --non-interactive \
  --name gemini-research --runtime antigravity --model gemini-3.8-flash-high \
  --command "$HOME/.local/bin/agy" --option print_timeout=0s
.venv/bin/coral agents doctor --no-live
```

配置保存在 `~/.config/coral/agents.yaml`，不含凭证，不提交 Git。
新任务可在 `agents.assignments` 中使用 `binding: codex-research` 等名称。
Binding 只包含执行器、模型和相关参数；任务仍需配置自己的沙箱、网络代理和预算。
Optiver 在外层 SRT 内显式设置 DSH `permission_mode: danger-full-access`，
此权限不放入跨任务 binding。

## 只验证，不启动研究

在仓库根目录执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]' -e examples/optiver_rv/grader uv
export PATH="$PWD/.venv/bin:$PATH"
export PYTHONDONTWRITEBYTECODE=1
python examples/optiver_rv/prepare.py
python -m pytest tests/test_optiver_coral.py -q
python examples/optiver_rv/validate.py --output results/optiver_migration/validation-next
```

`prepare.py` 检查 seed 身份，复制已有 research_eval 标签到本任务私有数据目录。不同内容不会覆盖。
`validate.py` 要求一个不存在的证据目录。它校验所有已开放输入和旧环境 manifest 文件，执行原生 SRT 读取探针，
调用 CORAL 原生 `setup_grader_env`、`load_grader` 和 `SubprocessGrader`，重训一次 seed，再独立核算指标。
验证过程中不会构造 AgentManager，不调用 `coral start`，不请求研究模型。
它会使用 research_eval 标签进行一次接线评分；这次评分属于验证，不是新的研究候选。
准备时复制到本任务 `taskdata/` 的隐藏标签不纳入 Git，也不放入 seed。

完整模型、预测和日志保存在验证目录的 `.coral/private/optiver_artifacts/`。
公开反馈只含汇总分数、执行信息和最多各 1 MiB 的 stdout/stderr。完整日志留在私有产物中。
隐藏标签不复制到公开反馈中。

## 边界与已知限制

- agent 隔离使用 CORAL 原生 SRT provider。评分时的候选进程另用任务级 Seatbelt，禁止读隐藏标签、fork 和网络连接。
- DSH 内层使用 `permission_mode: danger-full-access`，由外层 SRT 保持文件隔离。此设置避免嵌套 macOS 沙箱失败，并允许写入本 run 的共享笔记。不要脱离外层 SRT 单独复用此权限配置。
- DSH 通过 `runtime/dsh` 加载 Node 原生 HTTP 错误监听。仅对 `api.deepseek.com` 记录时间、标准错误类型/错误码、syscall 和 errno，写入原生 `diagnostics/<agent>/agent.err` 的 `[dsh-transport]` 行。不记录 message、stack、URL、请求头或正文；未识别的错误码记为 UNKNOWN。不改变重试、超时或停止规则，也不修改旧 runtime 和历史日志。
- 正式 grader 并发为 1。当前单次执行超时为 1,800 秒，内存 watchdog 为 16 GiB，主机余量为 4 GiB，输出上限为 1 GiB。
- 资源配置以 `task.yaml` 为准。seed 保留的历史 `config.resource_limits` 不作为当前资源权限。
- 线程库配置为 8；这是线程库设置，未承诺严格的全机 CPU 核数上限。内存和磁盘检查是采样检查。
- `agents.timeout: 0` 关闭按 stdout 静默时长重启 Agent 的 watchdog。DSH 的工具活动可能不出现在公开日志中，长时间无 stdout 不作为卡死证据。
- `run.stop.max_real_attempts` 与 `score_threshold` 均为 null；不按评测次数或成绩提前终止。整次时限由运行时的截止监督器执行，启动前另行确认。
- CORAL 原生 tune 执行同一完整评分；允许 Agent 在整次时间预算内使用，分别记录 real、tune、grader_error 与本地训练，不能用 real 次数代替全部消耗。
- 原生 heartbeat、反思与 research 保留。正常反思交接与异常退出重启不同；后者由运行配置在首个异常退出后暂停，监督器停止整轮，不自动恢复。
- 配置为 Codex、DSH 各一个原生 Agent。正式 grader 为单 worker；没有新增本地训练统一队列。
- 原生 DSH headless 不支持会话 resume；其重启按原版行为开启新会话。本任务没有补写会话机制。
- CPU LightGBM 起始方案已验证。MPS 分支保留原有初始化方式，本版未重新验证 MPS 或任意新模型。
- 单次固定划分结果不证明重复训练稳定性、时间外泛化或交易收益。
