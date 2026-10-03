# 物理建模与数值质量实算

2026-10-03 在 Windows / Python 3.11 / WNTR 1.5.0 实际计算，全部为 Benchmark；模型来源真实，不包含现场校准、Live 数据或 AGH 决策。

| 文件 | 含义 |
|---|---|
| `summary.json` | 26 次实际运行、全部有效/失败分母、方程检查、能耗估算、Newton 残差和迭代、细化差值与场景比较 |
| `verified-results.zip` | 26 次运行的完整冻结输入、编译模型、实际 CSV、内部诊断、环境和逐文件哈希 |
| `verified-results.zip.sha256.json` | ZIP 本身字节 SHA-256 |

实验包括 300/150/75 s 的 Net3 断裂时间细化、一组 Newton 容差对比、12 种单因素物理参数变化、WNTR/EPANET 共同支持的关闭工况，以及四种场景下同条件 A/B。所有计算重新启动引擎；没有复用 Fixture 数值。运行失败使实验命令报错，失败目录保留供诊断。

物理覆盖、具体数字、数值误差与现实不确定性的区别，见 [物理模型与算法评估](../../docs/物理模型与算法评估.md)。场景不是概率分布；固定 B 改善不等于最优或压力达标。水质、气穴、水锤、土壤反压及现场验证尚未实现。

```powershell
.venv/Scripts/python.exe -m services.hydraulics.physics_study --output artifacts/physics-study --summary artifacts/physics-summary.json --export artifacts/physics-study.zip
```

代码版本、参数、环境及原生引擎都绑定到证据身份；修改后必须重新计算，不能只更新摘要。0.4 公共 Series 仍为 900 s 展示采样，`hydraulic-analysis.json` 和原始 CSV 是内部时间数据。正式验证需在内部时间网格复算约束；当前公共参考 VerificationReport 不替代这一验证。
