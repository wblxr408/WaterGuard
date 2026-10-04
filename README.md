# WaterGuard · 水脉卫士

面向供水管网突发故障的应急调度、仿真验证与决策证据工作台。

## 当前状态

已拉取官方 AGH 源码作为 Git 子模块基座，并构建官方 Web 与 daemon。WaterGuard 原生插件提供任务读取、候选提交、水力仿真、独立验证和证据工具；由 AGH 承担模型与工具调度。EPA Net1/Net3 的真实计算、任务存储、取消、Fixture、Replay 与证据导出已接入 FastAPI，公共契约为 0.4.0。Agnes 鉴权当前返回 401，模型驱动的 Live 尚未验证。

运行完整仿真请看 [同学 1 接入与复现](docs/同学1交付与复现.md)。[交付证据](benchmarks/student1/) 包含 22 个真实工况及同条件 A/B 改善样例；[数据说明](data/README.md) 保留公开来源和初版 EPANET 基准。

先按 [三人接入与复现说明](packages/contracts/README.md#从干净仓库跑起来) 安装依赖，再运行 `npm run demo`；`npm run mock` 提供本地开发 API。Windows 无需激活环境的完整命令也在该说明中。

### 最新前端

展示前端以 [apps/web-gpt](apps/web-gpt/README.md) 为准：闸阀拆装开场、应急工作台、智能体分支导图与流程总览、方案对照、建模与仿真、证据中心。远程 PR #1 的完整页面与资产均已保留。

```powershell
cd apps/web-gpt
npm ci
pwsh -ExecutionPolicy Bypass -File .\scripts\start-demo.ps1
```

打开 [展示前端](http://127.0.0.1:5173/)。启动脚本使用 8000–8002 的只读数据服务；水力读数来自真实 Benchmark，智能体运行示例仍按页面标注为 Fixture。其真实任务与 AGH 事件接入应在这套前端上继续完成。

展示服务与联调 API 同时运行时，API 请使用独立端口，例如在仓库根目录运行 `npm run start:api -- --port 8010`。AGH 启动器可用 `npm start -- --api-port 8010`；原生插件配置的 API 地址需与所选端口一致，参见 [AGH 基座与启动](docs/AGH基座与启动.md)。

### AGH 与联调

基于官方 AGH 启动（首次构建与插件安装见 [AGH 基座与启动](docs/AGH基座与启动.md)）：

```powershell
cd D:\WaterGuard
npm start
```

打开 [官方 AGH 工作台](http://127.0.0.1:4180/) 使用原生会话与业务工具。[水力工作台](http://127.0.0.1:8000/) 用于冻结任务、运行 Benchmark、比较候选和查看证据；单独启动用 `npm run start:api`，步骤见 [前后端启动与接入](docs/前后端启动与接入.md)。支持 Net1/Net3 的正常、关闭、泄漏、断裂与隔离工况。本地运行数据写入未提交的 `artifacts/`；原生记录与会话关联完成前，API 的 `mode=live` 明确返回 `AGH_UNAVAILABLE`。

现有 0.2 实施设计：

| 文档 | 内容 |
|---|---|
| [前端设计](docs/前端设计.md) | 数据驱动三维管网、Blender 资产、二维分析、AGH 事件和证据界面 |
| [后端设计](docs/后端设计.md) | API、会话宿主、工具网关、水力仿真、契约、治理和证据存储 |
| [Agent 设计](docs/Agent设计.md) | AGH 核心执行、Agnes 三角色、工具权限、反馈修正与评测 |

三份文档是当前实施依据。已冻结的公共字段与状态以 [可执行 Schema](packages/contracts/schema/waterguard.schema.json) 和 [契约说明](packages/contracts/README.md) 为准；后端设计保留架构依据，角色与工具权限以 Agent 设计为准，界面和视觉以前端设计为准。

[二维工作台](design/waterguard-blueprint.html) 沿用初期布局并接入后端，打开本地服务地址使用。其架构、治理和评测页保留为设计参考；工作台和方案页的拓扑、读数与运行记录来自真实后端产物。

## 产品任务

在已知爆管位置、允许操作的设施和保供要求下，生成、比较并验证应急处置方案。展示故障影响、候选动作、验证失败原因、修正过程及结论的适用范围。

## 建议架构

AGH 是核心任务的执行基座。选定的模型在其中承担任务建模、方案规划和反例审查，读取工具反馈并决定修正。默认采用赛事 Agnes，同时支持 DeepSeek 和 GLM。WaterGuard 自有界面通过官方 SDK 接入 AGH 会话，成果展示使用自己的工作台。后端提供确定性计算、程序校验和作业治理。

- 前端：React、TypeScript、Three.js，三维/二维管网、指标与证据联动；Blender 提供少量复用设备资产。
- 后端：Python、FastAPI、AGH 会话宿主、受限工具网关、持久队列和独立计算进程。
- 水力仿真：WNTR / EPANET；使用公开网络模型。
- 运行底座：官方 AGH；默认赛事 Agnes 模型，兼容 DeepSeek、GLM。模型配置与凭据保存使用 AGH 官方服务，接入说明见 [模型接入](docs/模型接入.md)。

仿真模块与公共契约已实现；其余产品架构仍按设计逐步接入。

## 边界治理

- 冻结任务目标、硬约束、软偏好及版本；Agent 不得自行降低保供要求。
- 从明确的设施与动作目录生成方案，校验物理映射、参数及前置条件。
- 运行产物绑定输入和约束版本，旧结果不能覆盖新任务。
- Agent 不能写入验证通过结论；数值和状态来自原始结果与程序检查。
- 分别呈现动作合法、仿真有效、名义工况达标和指定扰动复验状态。
- 设置模型、仿真和时间预算，保留失败与取消的记录。

## 实施顺序

1. 首先核验指定 AGH 的真实工具调用、所选模型的核心决策、原生日志导出与模型配置。
2. 建立公开网络基线、合法动作映射、指标计算和独立验证，作为 AGH 可调用的专业工具。
3. 在 AGH 中完成失败、修正、复验闭环，并接入真实前端。
4. 增加任务建模和反例审查，以相同预算对比固定搜索、单 Agent 与多 Agent。
5. 完成留出场景评测、证据包、演示与复现说明。

## 参考资料

- [赛事规程](https://agnes-ai.cn/hackathon)
- [WNTR 公开模型](https://usepa.github.io/WNTR/libraries.html)
- [WNTR 水力仿真](https://usepa.github.io/WNTR/hydraulics.html)
- [WNTR 阀门图层](https://usepa.github.io/WNTR/layers.html)

