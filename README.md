# WaterGuard · 水脉卫士

面向供水管网突发故障的应急调度、仿真验证与决策证据工作台。默认展示中国 Zhi Jiang 城市管网研究模型（113 个用水节点、1 个水源、164 条管道），并提供实际水力计算的正常、关闭、泄漏、断裂和隔离工况。来源与复现见 [中国管网接入](docs/中国管网接入.md)。

## 当前状态

2026-10-13提交版：民生优先保供，固定先行处置快速保底、Agnes按需精修。真实混合Live完成8仿真40调用，精修无严格增益，交付固定方案。没有完全达标方案时列为“应急处置推荐（尽力挽救）”，降级76/92、平均93.07%，398剩余供水缺口与完整准入结果公开保留；修改后没有重跑方案。[本科组材料](docs/submission/README.md)提供50字简介、4986字详情及[最新混合验收](docs/submission/民生优先与混合策略验收.md)。源码、原始证据和演示数据库随无视频ZIP提供，入口见[证据索引](docs/submission/证据索引.md)，启动以[最新复现说明](docs/submission/复现与运行.md)为准。

已拉取官方 AGH 源码作为 Git 子模块基座，并构建官方 Web 与 daemon。WaterGuard 原生插件提供任务读取、受限候选、真实水力仿真、独立验证与证据工具；AGH 中的模型负责工具选择和反馈后的下一步决策。EPA Net1/Net3 的真实计算、任务存储、取消、Fixture、Replay 与证据导出已接入 FastAPI，公共契约为 0.4.0。2026-10-06 已使用保存的 `agnes-3.0-flash` 凭据完成真实 Live 闭环、受控 `INVALID_ACTION` 后修正、运行中取消和服务重启后的关联后继运行；正式记录均带原生 trace、探针、关联表与哈希证据包。

运行完整仿真请看 [同学 1 接入与复现](docs/同学1交付与复现.md)。[交付证据](benchmarks/student1/) 包含 22 个真实工况及同条件 A/B 改善样例；[数据说明](data/README.md) 保留公开来源和初版 EPANET 基准。

先按 [三人接入与复现说明](packages/contracts/README.md#从干净仓库跑起来) 安装依赖，再运行 `npm run demo`；`npm run mock` 提供本地开发 API。Windows 无需激活环境的完整命令也在该说明中。

### 最新前端

唯一前端为 [apps/web-gpt](apps/web-gpt/README.md)：闸阀拆装开场、应急工作台、智能体分支导图与流程总览、方案对照、建模与仿真、证据中心。旧 HTML 工作台和早期 `apps/web` 原型已移除。

```powershell
cd apps/web-gpt
npm ci
pwsh -ExecutionPolicy Bypass -File .\scripts\start-demo.ps1
```

打开 [WaterGuard 工作台](http://127.0.0.1:5173/)。启动脚本自动启动 8000–8002 的只读数据服务、8010 的任务 API 和新版前端。智能体页提供新建任务、取消运行和 AGH 模型账号配置；持续 SSE 推送工作流、原生工具摘要和 Info log，支持断线续读、运行选择、日志筛选与导出。人工 Fixture 回放需显式选择。

仓库根目录可执行 `npm run start:fullstack`，或双击 `scripts/start_waterguard.cmd` 一键启动前端、任务 API 和所需数据服务；`npm run start:web` 是兼容命令。单独启动 API 使用 `npm run start:api`，默认 8010；API 根地址重定向到唯一的 5173 前端。自定义端口需同步 Vite 的 `WATERGUARD_API_URL` 和原生插件配置，参见 [AGH 基座与启动](docs/AGH基座与启动.md)。

### AGH 与联调

基于官方 AGH 启动（首次构建与插件安装见 [AGH 基座与启动](docs/AGH基座与启动.md)）：

```powershell
cd D:\WaterGuard
npm start
```

AGH 启动器运行官方执行基座并复用或启动 8010 API；新版前端由上方 `start:web` 启动。[官方 AGH 工作台](http://127.0.0.1:4180/) 用于检查原生会话与插件。任务操作统一在 [WaterGuard](http://127.0.0.1:5173/) 智能体页完成，支持 Net1/Net3 的正常、关闭、泄漏、断裂与隔离工况。本地运行数据写入未提交的 `artifacts/`；缺少有效模型账号时 Live 明确返回 `MODEL_NOT_CONFIGURED`。完整步骤见 [前后端启动与接入](docs/前后端启动与接入.md)。

现有 0.2 实施设计：

| 文档 | 内容 |
|---|---|
| [前端设计](docs/前端设计.md) | 数据驱动三维管网、Blender 资产、二维分析、AGH 事件和证据界面 |
| [后端设计](docs/后端设计.md) | API、会话宿主、工具网关、水力仿真、契约、治理和证据存储 |
| [Agent 设计](docs/Agent设计.md) | AGH 核心执行、Agnes 三角色、工具权限、反馈修正与评测 |

三份文档是当前实施依据。已冻结的公共字段与状态以 [可执行 Schema](packages/contracts/schema/waterguard.schema.json) 和 [契约说明](packages/contracts/README.md) 为准；后端设计保留架构依据，角色与工具权限以 Agent 设计为准，界面和视觉以前端设计为准。

## 产品任务

在已知爆管位置、允许操作的设施和保供要求下，生成、比较并验证应急处置方案。展示故障影响、候选动作、验证失败原因、修正过程及结论的适用范围。

## 建议架构

AGH 是核心任务的执行基座。选定的模型在其中承担任务建模、方案规划和反例审查，读取工具反馈并决定修正。默认采用赛事 Agnes，同时支持 DeepSeek 和 GLM。WaterGuard 自有界面通过官方 SDK 接入 AGH 会话，成果展示使用自己的工作台。后端提供确定性计算、程序校验和作业治理。

- 前端：React、TypeScript、Three.js，三维/二维管网、指标与证据联动；Blender 提供少量复用设备资产。
- 后端：Python、FastAPI、AGH 会话宿主、受限工具网关、持久队列和独立计算进程。
- 水力仿真：WNTR / EPANET；使用公开网络模型。
- 运行底座：官方 AGH；默认赛事 Agnes 模型，兼容 DeepSeek、GLM。模型配置与凭据保存使用 AGH 官方服务，接入说明见 [模型接入](docs/模型接入.md)。

比赛可验收版已实现单受限 AGH 会话分阶段执行；不声称已实现可信多角色隔离、多租户队列或 Windows 容器级资源隔离。这些部署能力仍属于后续工作。

## 边界治理

- 冻结任务目标、硬约束、软偏好及版本；Agent 不得自行降低保供要求。
- 从明确的设施与动作目录生成方案，校验物理映射、参数及前置条件。
- 运行产物绑定输入和约束版本，旧结果不能覆盖新任务。
- Agent 不能写入验证通过结论；数值和状态来自原始结果与程序检查。
- 分别呈现动作合法、仿真有效、名义工况达标和指定扰动复验状态。
- 设置模型、仿真和时间预算，保留失败与取消的记录。

## 实施顺序

1. 按 [AGH 验收操作手册](docs/AGH验收操作手册.md) 复跑正常、失败修正、取消和恢复四类记录。
2. 留出场景评测、成员贡献与全员独立完成声明已完成；2026-10-14补录最新界面回看，赛事表单及本人确认由团队提交。
3. 再评估多角色隔离与同预算收益；没有可信隔离和量化收益时不将其作为已实现能力。

## 历史严格赛事验收

六个真实 AGH 场景已完成，含正常保供、Net3 工程失败与备用泵修正、中国管网断裂、非法动作恢复及两个留出场景。所有内部水力区间用于正式验收，名义和同方案需求 +10% 复验共同决定推荐；物理计算成功而业务未达标时明确拒绝推荐。

[逐项验收与评分分析](docs/赛事逐项验收与评分分析.md) 保留历史六场景、评分证据和实际对照；[提交说明](docs/参赛提交说明.md) 提供当前材料入口。历史原始汇总为 `artifacts/contest-acceptance-v4/summary.json`，场景诊断为 `artifacts/scenario-lab/report.md`；当轮技术包与四分钟视频位于 `artifacts/`。188项检查通过、2项平台跳过属于该轮记录，不能作为当前源码的整套回归结论。团队声明已确认；最新民生验收、功能检查及3分38秒视频见[提交材料](docs/submission/README.md)，赛事回执与评委成绩由团队另行提供。

## 参考资料

逐节点正常供水时间、冻结目标排序和可配置需求增幅 x 的实现与验收入口见 [逐节点保供调度与可配置复验](docs/逐节点保供调度与可配置复验.md)。部署默认参数集中在 `config/dispatch-defaults.json`，任务参数可覆盖并进入冻结哈希。

- [赛事规程](https://agnes-ai.cn/hackathon)
- [WNTR 公开模型](https://usepa.github.io/WNTR/libraries.html)
- [WNTR 水力仿真](https://usepa.github.io/WNTR/hydraulics.html)
- [WNTR 阀门图层](https://usepa.github.io/WNTR/layers.html)


2026-10-14交付同步版的完整源码、原始证据与演示数据库ZIP，以及单独的最新界面视频，统一从[提交版Release](https://github.com/wblxr408/WaterGuard/releases/tag/submission-20261013)下载。使用Release的完整无视频附件；GitHub自动生成的Source code压缩包仅包含仓库源码，不能替代完整证据包。最新混合运行是`run-efda67724ab043ccaf9a49a30373f161`，录制只回看既有记录，没有重跑方案。版本与材料入口见[提交材料](docs/submission/README.md)，视频内容见[时间索引](docs/submission/视频时间索引.md)。赛事报名与上传由团队自行完成。

## 完整提交交付

[两段演示视频](docs/submission/videos/README.md)均在仓库归档。[最新Release](https://github.com/wblxr408/WaterGuard/releases/tag/submission-20261013)包含代码、Windows x64便携安装包、运行说明、双视频和测试样例的完整ZIP；按[完整包运行说明](docs/submission/完整包运行说明.md)离线回看。

## 报名系统200MB限制

当前报名文件改用不含视频的200MB以内精简ZIP，保留源码、便携安装包、运行说明、最新两例回看与必要测试样例；[200MB报名包说明](docs/submission/200MB报名包说明.md)。两段视频保留在仓库与Release。此前526MB完整包是扩展历史交付，不用于本次文件栏上传。
