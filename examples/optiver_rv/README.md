# Optiver RV on native CORAL

本任务从 EvalForge v0.1.3（`0409503`）迁入，只适配 Optiver 数据与评测。
CORAL 上游基准为 `0123dfb939b35228cf2c1fde224cd0561e727408`，核心源码不改。
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
- 正式 grader 并发为 1。当前单次执行超时为 1,800 秒，内存 watchdog 为 16 GiB，主机余量为 4 GiB，输出上限为 1 GiB。
- 资源配置以 `task.yaml` 为准。seed 保留的历史 `config.resource_limits` 不作为当前资源权限。
- 线程库配置为 8；这是线程库设置，未承诺严格的全机 CPU 核数上限。内存和磁盘检查是采样检查。
- CORAL 原生 tune 默认执行同一完整评分；tune 不计入原生 real-attempt 停止计数。本任务没有添加另一套预算或搜索策略。
- 任务保留原生 heartbeat 与 research 默认值，没有指定正式运行预算。本轮没有启动正式研究。
- 配置为 Codex、DSH 各一个原生 Agent。正式 grader 为单 worker；没有新增本地训练统一队列。
- 原生 DSH headless 不支持会话 resume；其重启按原版行为开启新会话。本任务没有补写会话机制。
- CPU LightGBM 起始方案已验证。MPS 分支保留原有初始化方式，本版未重新验证 MPS 或任意新模型。
- 单次固定划分结果不证明重复训练稳定性、时间外泛化或交易收益。
