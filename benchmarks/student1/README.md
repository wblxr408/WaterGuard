# 同学 1 实际运行证据

2026-10-03（Asia/Shanghai）在 Windows、Python 3.11、固定 WNTR 1.5.0 环境实际运行；未使用 Fixture 数值。完整方法与接口见 [交付说明](../../docs/同学1交付与复现.md)。

| 文件 | 内容 |
|---|---|
| `summary.json` | 两网络 22 次成功计算的状态、指标、物理检查和引用 |
| `verified-results.zip` | 对应 22 个运行的全部输入、实际输出和逐文件哈希，共 716 个索引产物 |
| `feedback-pair.json` | 同一任务的真实 A/B 比较；B 改善但仍违反压力约束 |
| `feedback-pair.zip` | A/B 的完整运行证据，共 60 个索引产物 |
| `*.zip.sha256.json` | 压缩包字节 SHA-256 |
| `TEST-REPORT.md` | 实际执行的本机检查、结果和适用边界 |

ZIP 中 `index.json` 指向 `sim-*/`；每个目录的 `evidence-manifest.json` 列出该次运行的所有文件与哈希。可直接解压查看 `raw-*.csv`；不要修改源结果再沿用原哈希。证据已核对文件哈希与当前代码版本绑定。重新运行会产生新的 ID、时间与压缩包哈希。

产物均为 **Benchmark**，没有 AGH 原生日志，不具备推荐准入资格。压力阈值作用于所有原始 junction，包含零需求节点。汇总体积按 900 秒报告间隔起点保持值积分。
