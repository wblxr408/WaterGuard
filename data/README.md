# 公开管网接入

**当前入口（0.3.0）**：[同学 1 完整交付与复现](../docs/同学1交付与复现.md)。已新增真实泄漏、断裂、隔离、泵启停、进程隔离/取消/超时和回放；使用 `PhysicalAdapter`。本页后续运行命令、四工况结果及能力限制记录的是兼容保留的 **0.2 EPANET 基准**，其证据未被覆盖。数据来源、许可与坐标说明继续适用。

已接入 **USEPA/WNTR 1.5.0 发布版中的 EPANET Net1、Net3**，并使用 WNTR 的 EpanetSimulator 调用 EPANET 2.2 实际计算。

这里接入的是公开工程示例模型。INP 包含节点、管段、管径、长度、粗糙度、水源、水箱、泵、需求模式与原有控制。它们不是城市当前的传感器数据，也不能当作现场校准模型。故障和 PDD 参数是本项目明确记录的试验假设。

| 网络 | ID | 节点数 | 连接数 | 用途 |
|---|---|---:|---:|---|
| Net1 | `epa-net1` | 11：9 用户节点、1 水源、1 水箱 | 13 | 小网络调试 |
| Net3 | `epa-net3` | 97：92 用户节点、2 水源、3 水箱 | 119 | 展示与较大网络联调 |

## 来源与许可

上游固定到 WNTR `1.5.0` 的提交 `388958845c3209ace9a5bd1b105097977eec2d67`，不会随 main 变化。

- [官方模型库说明](https://usepa.github.io/WNTR/libraries.html)
- [Net1 原文件](https://raw.githubusercontent.com/USEPA/WNTR/388958845c3209ace9a5bd1b105097977eec2d67/examples/networks/Net1.inp)
- [Net3 原文件](https://raw.githubusercontent.com/USEPA/WNTR/388958845c3209ace9a5bd1b105097977eec2d67/examples/networks/Net3.inp)
- [上游许可](https://github.com/USEPA/WNTR/blob/388958845c3209ace9a5bd1b105097977eec2d67/LICENSE.md)：WNTR Revised BSD；文件同时保留 EPANET MIT 及其他第三方声明。完整副本位于 `networks/wntr-1.5.0/LICENSE.md`。
- [实际水力引擎说明](https://usepa.github.io/WNTR/hydraulics.html)

原始 INP 和许可按下载字节保存，包括其原始换行；Git 对这些文件禁用文本归一化。校验值见 `services/hydraulics/datasets.py`，每个模型的来源、SHA-256、单位和 overlay 见 `manifests/`。本次未修改上游模型文件。

`overlays/` 保存项目假设：PDD 最小压力 0 m、满足需求压力 20 m、指数 0.5，计算 3 小时、步长 15 分钟、评估后 2 小时。保留上游需求模式、水箱初始状态、泵及控制；首小时是共同预运行过程，不宣称已达到稳态。没有添加医院或关键用户标签。

原始水力单位为 GPM/feet/inches，WNTR 读入后转换成 SI。原始平面坐标没有确认的地理单位，scene 中 `x_m/y_m` 只作任意布局坐标，保留 1:1 图形形状；`display_coordinate_note` 明确它不代表真实距离或 CRS。实际管长和高程用于水力计算，不从画面坐标推断。

## 本机重跑

从仓库根目录执行，Python 3.11：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r services/hydraulics/requirements.txt
.venv/Scripts/python.exe -m services.hydraulics.datasets
.venv/Scripts/python.exe -m services.hydraulics.demo
.venv/Scripts/python.exe -m services.hydraulics.server
```

Linux/macOS 激活 `.venv/bin/activate` 后使用 `python` 代替 `.venv/Scripts/python.exe`。依赖在干净环境安装后无需联网运行；若要显式重新下载并核验上游文件，使用 `python -m services.hydraulics.datasets --fetch`。

输出位于 `artifacts/hydraulics/`，每次调用分配新 simulation_id，不覆盖历史结果。每个候选重新载入原始模型，保留完整预运行时间线，无共享可变网络状态。当前 Adapter 在调用进程内执行，生产超时、取消和并发隔离仍交由同学 2 的独立作业进程接入。

生成可转交证据包：

```powershell
.venv/Scripts/python.exe -m services.hydraulics.demo --export artifacts/public-hydraulics.zip
```

包内包含原始 INP 与许可、实际编译 INP、EPANET 报告/二进制输出、原始 CSV、完整结果、独立复算报告、环境版本、文件哈希和缺失项。`benchmarks/public-networks/` 保存本次实际运行的摘要与压缩证据包，三人也可直接查看，之后重跑会产生新的 ID。

## 本次验证结果

下列是实际 EPANET 2.2 输出，原始时序见证据包。评估窗口为 `[3600,10800)` s，用报告间隔起点保持值积分；它是 15 分钟采样统计，不是事件步长的精确体积积分。

| 网络/工况 | 最低节点压力 m | 缺供体积 m³ | 供需体积比 | 名义约束 |
|---|---:|---:|---:|---|
| Net1 正常 | 78.692 | 0 | 1 | pass |
| Net1 管段 12 关闭 | 78.008 | 0 | 1 | pass |
| Net3 正常 | 4.182 | 0 | 1 | fail |
| Net3 管段 123 关闭 | 1.708 | 8.385 | 0.998466 | fail |

20 m 阈值目前作用于所有 junction，包括零需求连接点，因此 Net3 正常工况也会报压力违反；全网供水比仍可能为 1。生产场景应由任务所有者明确受保供节点，不为让演示通过而临时改阈值。`VerificationReport.violations` 列出具体节点与时段。

本次只验证公开网络载入、正常/关闭计算、动作映射和独立指标复算，没有假定关闭必然造成故障失败、没有伪造 B 改善。管段关闭仍不等于泄漏或完全断裂。

原始需求包含零需求节点，节点供水以引擎用户 demand 单独统计，泄漏流量为 0（本版没有泄漏模型）。仅将 `[-1e-9,0)` m³/s 的浮点需求误差置零并写警告；更大的负值拒绝。原始 CSV 保留未修改值。引擎未收敛、报告警告、缺失和非有限结果返回 FAILED 和诊断，不填造成功时序。

## 接到前后端

服务监听 `http://127.0.0.1:8001`，始终返回 `X-WaterGuard-Mode: benchmark`，与 Fixture 服务 8000 并存。CORS 允许 Vite localhost/127.0.0.1:5173。

| GET 路径 | 内容 |
|---|---|
| `/api/networks` | 两个公开网络的 NetworkManifest |
| `/api/networks/epa-net3/scene` | 保留源 ID 的实际拓扑与示意坐标 |
| `/api/networks/epa-net3/actions` | 由源模型生成的可操作管段目录 |
| `/api/benchmarks/current` | 最近一次计算的 simulation_id、状态和汇总 |
| `/api/simulations/{id}` | 实际 SimulationResult |
| `/api/simulations/{id}/series?node_id=123&start_s=3600&end_s=7200` | 筛选后的真实时序 |
| `/api/simulations/{id}/verification` | 独立指标复算与违反项 |
| `/api/simulations/{id}/evidence` | 产物索引和哈希 |
| `/api/simulations/{id}/task`、`/plan` | 冻结输入 |

运行目录内相对 ref 按 simulation_id 解析；完整下载使用上述 ZIP。写接口返回 405，不提供假的 AGH 事件流。

同学 1 可在 `services/hydraulics/adapter.py` 上补物理模型与公开场景；同学 2 可调用 `EpanetAdapter.simulate(task, plan)` / `get_series(...)`；同学 3 可先读取 8001 的网络与实际结果。动作目录只允许无上游控制冲突的普通管道开闭，止回管道和泵暂未开放操作。

共同 Schema 升为 0.2.0，继续接受 day1 的 0.1.0 Fixture。新增 `fault=null` 表示正常工况，scene `provenance=derived` 表示从上游模型派生，并增加坐标说明。0.1.0 仍要求故障对象和 synthetic 来源，原有冻结示例未改写。

真实水力计算采用 **Benchmark** 模式，因为当前调用是确定性的工程测试；**Live** 仍要求实际 AGH 会话、Agnes 决策与原生日志。所有候选的必要扰动复验依然 pending、推荐准入 fail。这不是最终应急处置建议。

## 自动检查

```powershell
.venv/Scripts/python.exe -m unittest discover -s services/hydraulics/tests -v
.venv/Scripts/python.exe -m unittest discover -s packages/contracts/tests -v
```

9 项真实引擎集成测试覆盖两网络四工况、收敛、预运行状态一致、关闭时刻与零流量、节点质量守恒、重复运行、动作编译、输入/控制边界、失败留证、产物哈希及实际读取 API；原有 19 项公共契约测试保持通过。GitHub Actions 在 Windows/Linux 分别重算并上传证据。
