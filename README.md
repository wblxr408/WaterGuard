# WaterGuard · 水脉卫士

面向供水管网突发故障的应急调度、仿真验证与决策证据工作台。

## 当前状态

已完成三人共同前置的 [contracts](packages/contracts/README.md)、[同学 1 的仿真与数据交付](docs/同学1交付与复现.md)，以及后端运行治理基础：EPA Net1/Net3 的真实 WNTR/EPANET、关闭/泄漏/断裂/隔离、独立进程、物理检查、回放和基准证据；任务/运行的 SQLite 状态、JSONL 事件、SSE、有限动作域候选生成、独立验证、预算与证据清单。公共 Schema 已升级至 0.4.0 并兼容旧 Fixture；计算标为 Benchmark。AGH 守护进程与 11 项 WaterGuard MCP 工具已挂载，但真实 Agnes 模型会话、原生轨迹、取消/恢复和 live 反馈修正仍需配置赛事 Key 后实测；二维前端工作台已接入 API、真实水力计算与独立校验。

运行完整仿真请看 [同学 1 接入与复现](docs/同学1交付与复现.md)。[交付证据](benchmarks/student1/) 包含 22 个真实工况及同条件 A/B 改善样例；[数据说明](data/README.md) 保留公开来源和初版 EPANET 基准。

[物理模型与算法评估](docs/物理模型与算法评估.md) 逐项核对现实物理因素，包含局部需求/PDD、背景漏损、能量与储水检查、内部事件采样和 26 组步长/参数敏感性实算；明确水锤、气穴、破口变形和现场校准的缺口。

[工程可靠性审查](docs/仿真工程可靠性审查.md) 记录进程、协议、原子写入、资源边界与回放环境加固，以及进入工业部署前仍需完成的验证。

先按 [三人接入与复现说明](packages/contracts/README.md#从干净仓库跑起来) 安装依赖，再运行 `npm run demo`；`npm run mock` 提供本地开发 API。Windows 无需激活环境的完整命令也在该说明中。

### 最新前端（对外演示以此为准）

最新的完整前端在 [apps/web-gpt](apps/web-gpt/README.md)，包括：
- 首页：闸阀拆装、系统展示；
- 应急工作台、智能体工作流、方案对照、建模与仿真、证据中心。

启动方法：

```powershell
cd apps/web-gpt
npm ci
pwsh -ExecutionPolicy Bypass -File .\scripts\start-demo.ps1
```

浏览器打开 http://127.0.0.1:5173/ 。

脚本会占用 8000–8002 三个只读数据服务端口，其中 8000 和下面的联调服务默认端口相同。两者同时运行时，联调服务请换端口，例如 `npm start -- 8010`。

### 联调服务

`design/waterguard-blueprint.html` 是初期二维蓝图，由联调服务在 `/` 提供。

前后端统一启动（优先使用项目已有 `.venv`，无需激活环境）：

```powershell
cd D:\WaterGuard
npm start
```

打开 [工作台](http://127.0.0.1:8000/) 即可选择管网、创建故障任务、运行 Benchmark、比较候选和查看证据；完整步骤见 [前后端启动与接入](docs/前后端启动与接入.md)。支持 Net1/Net3 的正常、关闭、泄漏和断裂工况，默认固定零动作/单动作枚举。它默认将本地运行数据写入未提交的 `artifacts/`。`POST /api/tasks/{id}/runs` 的 `mode=live` 在 AGH 宿主完成前会明确返回 `AGH_UNAVAILABLE`，不会暗中替换为其他 Agent 循环。

现有 0.2 实施设计：

| 文档 | 内容 |
|---|---|
| [前端设计](docs/前端设计.md) | 数据驱动三维管网、Blender 资产、二维分析、AGH 事件和证据界面 |
| [后端设计](docs/后端设计.md) | API、会话宿主、工具网关、水力仿真、契约、治理和证据存储 |
| [Agent 设计](docs/Agent设计.md) | AGH 核心执行、Agnes 三角色、工具权限、反馈修正与评测 |
| [AGH 验收清单](docs/AGH验收清单.md) | 配置模型 Key 后的真实工具闭环、异常与证据核验步骤 |
| [Agent 设计审查](docs/Agent设计审查-2026-10-04.md) | 当前环境实测、Agent 工具链缺口、优先级与设计优化 |

三份文档是当前实施依据。已冻结的公共字段与状态以 [可执行 Schema](packages/contracts/schema/waterguard.schema.json) 和 [契约说明](packages/contracts/README.md) 为准；后端设计保留架构依据，角色与工具权限以 Agent 设计为准，界面和视觉以前端设计为准。

[二维工作台](design/waterguard-blueprint.html) 沿用初期布局并接入后端，打开本地服务地址使用。其架构、治理和评测页保留为设计参考；工作台和方案页的拓扑、读数与运行记录来自真实后端产物。

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

