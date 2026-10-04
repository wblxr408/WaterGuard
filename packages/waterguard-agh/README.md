# WaterGuard 原生 AGH 工具

官方 AGH 加载 `waterguardTools` Cordis 插件并负责所选模型的运行循环。默认赛事 Agnes，也支持 DeepSeek、GLM。插件调用本机 WaterGuard API，提供确定性水力计算与独立验证。

| 工具 | 行为 |
|---|---|
| `wg_inspect_network` | 读取网络、目录及物理来源 |
| `wg_get_task` | 读取用户已冻结的任务与约束 |
| `wg_generate_candidates` | 返回有限候选供 AGH 选择 |
| `wg_submit_plan` | 按冻结任务预检一个 ActionPlan，保存待计算记录 |
| `wg_simulate_candidate` | 运行实际水力进程并读取结果 |
| `wg_verify_candidate` | 按原始结果独立计算指标与违规项 |
| `wg_build_evidence` | 读取证据清单与下载地址 |

工具只支持 loopback API。提交、计算、验证仅产生本地计算文件；它们不操作现场设备。Agent 无法通过这些工具重写任务约束或填写验证通过标志。仿真与验证分别调用，便于模型根据结果决定后续步骤。

当前工具计算记录使用 Benchmark 标记。正式 Live 需要所选模型账号鉴权、真实模型选择工具的会话及原生记录关联；缺失记录时证据的 `live_export_allowed` 保持 false。直接工具验收不能作为模型调用证据。

安装、启动与源码更新见 [AGH 基座与启动](../../docs/AGH基座与启动.md)。
