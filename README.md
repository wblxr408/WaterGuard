# WaterGuard · 水脉卫士

面向供水管网突发故障的应急调度、仿真验证与决策证据工作台。

## 当前状态

已完成三人共同前置的 [contracts](packages/contracts/README.md) 和 [同学 1 的仿真与数据交付](docs/同学1交付与复现.md)：EPA Net1/Net3、真实 WNTR/EPANET、关闭/泄漏/断裂/隔离、管段与泵操作、独立进程、物理检查、回放和证据包。公共 Schema 0.3.0 兼容旧 Fixture；计算标为 Benchmark。Agnes、AGH 和前端工作台仍待接入。

运行完整仿真请看 [同学 1 接入与复现](docs/同学1交付与复现.md)。[交付证据](benchmarks/student1/) 包含 22 个真实工况及同条件 A/B 改善样例；[数据说明](data/README.md) 保留公开来源和初版 EPANET 基准。

[工程可靠性审查](docs/仿真工程可靠性审查.md) 记录本轮进程、协议、原子写入、资源边界与回放环境加固，以及进入工业部署前仍需完成的验证。

先按 [三人接入与复现说明](packages/contracts/README.md#从干净仓库跑起来) 安装依赖，再运行 `npm run demo`；`npm run mock` 提供本地开发 API。Windows 无需激活环境的完整命令也在该说明中。

现有 0.2 实施设计：

| 文档 | 内容 |
|---|---|
| [前端设计](docs/前端设计.md) | 数据驱动三维管网、Blender 资产、二维分析、AGH 事件和证据界面 |
| [后端设计](docs/后端设计.md) | API、会话宿主、工具网关、水力仿真、契约、治理和证据存储 |
| [Agent 设计](docs/Agent设计.md) | AGH 核心执行、Agnes 三角色、工具权限、反馈修正与评测 |

三份文档是当前实施依据。已冻结的公共字段与状态以 [可执行 Schema](packages/contracts/schema/waterguard.schema.json) 和 [契约说明](packages/contracts/README.md) 为准；后端设计保留架构依据，角色与工具权限以 Agent 设计为准，界面和视觉以前端设计为准。

[0.1 历史交互蓝图](design/waterguard-blueprint.html) 保留用于查看初期布局，下载后可直接用浏览器打开。其架构已由上述文档取代；拓扑、读数与运行记录全部是示意数据，不是可参赛运行证据。

## 产品任务

在已知爆管位置、允许操作的设施和保供要求下，生成、比较并验证应急处置方案。展示故障影响、候选动作、验证失败原因、修正过程及结论的适用范围。

## 建议架构

AGH 是核心任务的执行环境。Agnes 在其中承担任务建模、方案规划和反例审查，发起工具调用、读取反馈并决定修正。后端管理权限、预算、版本和作业生命周期，提供确定性计算与程序校验，不能另行包办核心智能流程。

- 前端：React、TypeScript、Three.js，三维/二维管网、指标与证据联动；Blender 提供少量复用设备资产。
- 后端：Python、FastAPI、AGH 会话宿主、受限工具网关、持久队列和独立计算进程。
- 水力仿真：WNTR / EPANET；使用公开网络模型。
- 运行底座：指定 AGH 和 Agnes；MCP/官方工具扩展、角色委派、会话与日志接口需先验证，不能假定 Agnes Code 文档等于赛事版能力。

仿真模块与公共契约已实现；其余产品架构仍按设计逐步接入。

## 边界治理

- 冻结任务目标、硬约束、软偏好及版本；Agent 不得自行降低保供要求。
- 从明确的设施与动作目录生成方案，校验物理映射、参数及前置条件。
- 运行产物绑定输入和约束版本，旧结果不能覆盖新任务。
- Agent 不能写入验证通过结论；数值和状态来自原始结果与程序检查。
- 分别呈现动作合法、仿真有效、名义工况达标和指定扰动复验状态。
- 设置模型、仿真和时间预算，保留失败与取消的记录。

## 实施顺序

1. 首先核验指定 AGH 的真实工具调用、Agnes 核心决策、原生日志导出与模型配置。
2. 建立公开网络基线、合法动作映射、指标计算和独立验证，作为 AGH 可调用的专业工具。
3. 在 AGH 中完成失败、修正、复验闭环，并接入真实前端。
4. 增加任务建模和反例审查，以相同预算对比固定搜索、单 Agent 与多 Agent。
5. 完成留出场景评测、证据包、演示与复现说明。

## 参考资料

- [赛事规程](https://agnes-ai.cn/hackathon)
- [WNTR 公开模型](https://usepa.github.io/WNTR/libraries.html)
- [WNTR 水力仿真](https://usepa.github.io/WNTR/hydraulics.html)
- [WNTR 阀门图层](https://usepa.github.io/WNTR/layers.html)

