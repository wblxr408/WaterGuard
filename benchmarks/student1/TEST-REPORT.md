# 同学 1 仿真测试报告

本机验证日期：2026-10-03（Asia/Shanghai）。环境：Windows、Python 3.11、WNTR 1.5.0，精确依赖见 `services/hydraulics/requirements.txt`。引擎/实现哈希、各次实际环境与进程信息在证据包中。

## 实际执行结果

| 检查 | 结果 |
|---|---|
| `python -m unittest discover -s services/hydraulics/tests -v` | 27 项全部通过，72.677 秒；含旧 9 项及新 18 项 |
| 随后新增的 `PhysicalAcceptance.test_same_task_feedback_pair_improves_without_claiming_recommendation` | 单独运行通过，61.452 秒（含 22 工况前置重算）；水力测试现共 28 项 |
| `python -m unittest discover -s packages/contracts/tests -v` | 19 项全部通过 |
| `python packages/contracts/scripts/check.py` | Schema、绑定、哈希、事件、来源与独立指标复算通过 |
| `npm run contracts:types:check` | 生成类型与 Schema 一致，TypeScript 检查通过 |
| `python -m services.hydraulics.acceptance` | 22/22 实际计算成功，物理检查通过，参考结果有效 |
| `python -m services.hydraulics.pair` | A/B 均成功，预热一致，B 缺供改善，未冒充约束通过 |
| JSON CLI example + simulate | Net3 泄漏真实计算成功，JSON 输出可解析 |
| ZIP 清单审计 | 2 个 ZIP 总哈希、776 个产物哈希、24 次代码版本绑定全部通过 |

GitHub Actions 将在 Windows/Linux 重新执行全部 28 项水力测试、22 工况及 A/B，并上传独立证据；本文件只陈述已完成的本机验证，不预先声称 CI 通过。

## 检查覆盖

水力正确性检查包括正常基线、四种故障区别、孔口公式、双端流量、水量守恒、断裂期间虚拟连接零流量、隔离缺供仍计入分母、修复恢复、泵/管段关闭零流量和再次开启。预热压力/水箱一致，故障后的水箱历史持续累积。

同输入重算与并发运行使用不同进程及产物目录。真实计算在声明容差内一致；两引擎共同支持工况允许最大压力差 0.5 m、流量差 0.001 m³/s，已全部通过。这个阈值仅适用于本次网络与工况，不推广为任意引擎等价保证。

数据边界覆盖未知/冲突动作、源控制覆盖缺项、故障时间不对齐、PDD 参数无效、超截面积泄漏、不完整隔离边界、禁止将 WNTR 泄漏替换成 EPANET、需求倍数真实作用、输入版本/哈希、篡改证据、实质数值变化、worker 启动失败、引擎失败、超时和取消。失败无成功时序；进程退出后封存诊断。

## 检查中修复的真实问题

1. 验收脚本曾颠倒 scene/catalog 实参，已修正并由全矩阵通过确认。
2. Net3 三点泵曲线出现拟合协方差警告。核对源曲线及 WNTR 实现后，仅允许该精确警告，同时独立检查所有曲线点；原警告保留。收敛等其他警告仍拒绝成功。
3. 跨进程重复计算存在浮点末位差（一次观测压力 `5.684e-14 m`、流量 `4.250e-16 m³/s`）。回放保留原值与完全相等标志，另按绝对 `1e-10`/相对 `1e-12` 判断一致，并输出各字段最大差。

## 结果边界

这是 EPA 公开示例模型上的真实数值计算，不是现场传感器或现场校准结论。破口面积、流量系数、操作权限、手动控制策略与阈值是声明的工程假设。没有水锤、变速泵或阀门节流模型。图形坐标单位未知，不计算地理距离。参考业务指标仅帮助联调，正式独立验证器、AGH 决策/日志、扰动准入及产品交付仍由对应成员实现。
