# 中国管网实际计算证据

`verified-results.zip` 包含中国 Zhi Jiang（China 1）的七次实际仿真：WNTR 的正常、关闭、泄漏、断裂、隔离，以及 EPANET 的正常、关闭。全部求解成功，独立验证有效。压缩包 SHA-256 见同目录 `verified-results.zip.sha256.json`。

包内每个运行保留原始模型、许可证、任务、方案、逐帧结果、原始水力 CSV、物理检查、独立验收与完整哈希清单。`threshold-references/` 保留独立计算的无故障阈值参考，`threshold-references.json` 记录算法和输入绑定。默认验收采用无故障基线最低压力与最低节点供需比的 95%，在任务内冻结；可在工作台手动覆盖并创建新任务重算。正常工况缺供仍按原始结果保留，指标为指定场景的仿真结果。

来源、许可、模型范围、参数和复现见 [中国管网接入](../../docs/中国管网接入.md)。重新导出：

```powershell
.venv/Scripts/python.exe -m services.hydraulics.china_demo --ensure --export benchmarks/china-zhijiang/verified-results.zip
```
