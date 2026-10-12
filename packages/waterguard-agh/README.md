# WaterGuard 原生 AGH 工具

官方 AGH 加载 `waterguardTools` Cordis 插件并负责所选模型的运行循环。默认赛事 Agnes，也支持 DeepSeek、GLM。插件调用本机 WaterGuard API，提供确定性水力计算与独立验证。

| 工具 | 行为 |
|---|---|
| `wg_inspect_network` | 读取网络、目录及物理来源 |
| `wg_get_task` | 读取用户已冻结的任务与约束 |
| `wg_generate_candidates` | 返回有限候选供 AGH 选择 |
| `wg_jev_decide` | 根据冻结任务、剩余预算和真实验证结果调用 TypeSafe Jev，返回候选路由概率、置信度与风险；未配置时返回实际连接状态 |
| `wg_submit_plan` | 按冻结任务预检一个 ActionPlan，保存待计算记录 |
| `wg_simulate_candidate` | 运行实际水力进程并读取结果 |
| `wg_verify_candidate` | 按原始结果独立计算指标与违规项 |
| `wg_create_perturbation` | 按冻结任务声明的需求增幅 x 创建同方案实际复验，倍率从任务读取 |
| `wg_diagnose_failure` / `wg_revise_plan` | 从实际失败的节点和时段提取反馈，注册模型提出的许可动作与时序修订 |
| `wg_build_evidence` | 读取证据清单与下载地址 |

工具只支持 loopback API。提交、计算、验证仅产生本地计算文件；它们不操作现场设备。Agent 无法通过这些工具重写任务约束或填写验证通过标志。仿真与验证分别调用，便于模型根据结果决定后续步骤。

工具产生的水力子记录使用 Benchmark 标记，Agnes 模型组织的父运行使用 Live。每个父运行通过官方 SDK 的 `sessionKey=waterguard:<run_id>` 建立独立会话。已验证的子结果即时供工作台读取；模型请求数取自原生 `request/sent`。2026-10-06 中国管网实际 Live 闭环通过，复现与证据见 [实时运行与 Jev 接入](../../docs/实时运行与Jev接入.md)。

安装、启动与源码更新见 [AGH 基座与启动](../../docs/AGH基座与启动.md)。

逐节点正常供水时间、目标顺序与任意需求增幅的定义见 [调度与复验](../../docs/逐节点保供调度与可配置复验.md)。优化指标和最终准入由独立报告决定；改善但仍违规的候选不能获得通过推荐。
