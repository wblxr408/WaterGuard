# 三人共用契约 · 0.1.0

第一天的共同前置已实现：一个网络、一个明确的故障、一套冻结条件、A 失败/B 改善的样例，以及可执行的契约检查、指标复算、Mock Adapter 和只读 HTTP/SSE。

**所有随附数据均为 Fixture。没有运行 AGH、Agnes、WNTR 或 EPANET。** INP 是项目编写的小型调试拓扑；时序是人工设定的开发样例，不能用来证明该 INP 的实际水力行为。接入真实引擎后，应保存新产物和实际模式，保留这套 Fixture 用于回归测试。

## 从干净仓库跑起来

要求 Python 3.11、Node.js 22；从仓库根目录执行。Python 和 npm 依赖已固定版本。

Windows PowerShell（无需激活环境）：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r packages/contracts/requirements.txt
npm ci --ignore-scripts
npm run contracts:types:check
.venv/Scripts/python.exe packages/contracts/scripts/check.py
.venv/Scripts/python.exe -m unittest discover -s packages/contracts/tests -v
.venv/Scripts/python.exe packages/contracts/scripts/demo.py
.venv/Scripts/python.exe packages/contracts/scripts/mock_server.py
```

macOS/Linux：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r packages/contracts/requirements.txt
npm ci --ignore-scripts
npm run contracts:types:check
npm run contracts:check
npm test
npm run demo
npm run mock
```

最后一条启动 `http://127.0.0.1:8000`，按 Ctrl+C 关闭。使用 `--port 8001` 可换端口。已激活 Python 环境时，Windows 也可使用根目录的 npm 命令。

## 冻结案例

| 项目 | 固定值 |
|---|---|
| Schema 版本 | `0.1.0` |
| 网络 | `wg-fixture-loop-v1`，revision 1；R1 水源，J1/J2/J3 用户节点 |
| 关键节点 | J2，合成 overlay；不代表真实医院 |
| 任务 | `task-day1-closure`，revision 1 |
| 故障 | P_MAIN 在 `[3600,10800)` s 强制关闭；`kind=closure` |
| 时间轴 | `[0,10800)` s，1800 s 一个区间；`[0,3600)` 预热，`[3600,10800)` 评估 |
| 压力阈值 | 所有用户节点 ≥20 m；容差 0.000001 m |
| 供水阈值 | 每节点每区间供需比 ≥0.95；容差 0.000000001 |
| 动作域 | P_BYPASS 的 `link_open/link_close`；最多 2 次；3600–9000 s；间隔 ≥1800 s |
| 候选 A | 空动作，故障后 J2/J3 无供水 |
| 候选 B | 在 3600 s 打开 P_BYPASS；父候选为 A |
| 待复验 | `demand-plus-10pct`，尚未执行 |

这是**管道关闭案例**。泄漏、断裂、隔离在 Schema 中是不同枚举，必须各自实现物理映射；当前 Fixture Adapter 只支持这个关闭案例。泵动作存在于共享动作枚举，但未加入本案例可操作目录。

预期输出（只计算评估窗口，隔离用户需求仍保留在分母）：

| 指标 | A | B |
|---|---:|---:|
| 缺供体积 m³ | 28.8 | 0 |
| 供需体积比 | 1/3 | 1 |
| 最低压力 m | 0 | 24 |
| 关键节点达标时长比 | 0 | 1 |
| 操作成本，合成单位 | 0 | 1 |
| 名义约束 | fail | pass |
| 指定扰动复验 | pending | pending |
| 推荐准入 | fail | fail |

`simulation_valid=pass` 在 Fixture 模式下仅表示样例结构和数据有效性检查通过。`diagnostics.converged=null` 明确表示未测收敛，不能展示成真实水力计算收敛。运行 `COMPLETED` 表示样例流程结束，`conclusion=INCOMPLETE`、`selected_candidate_id=null`，不能展示为完成工程推荐。

## 文件与使用方式

| 路径 | 用途 |
|---|---|
| `schema/waterguard.schema.json` | 唯一可编辑的公共结构定义，JSON Schema Draft 7 |
| `generated/index.d.ts` | 从 Schema 生成的前端类型；不手工修改 |
| `fixtures/day1/task.json` | 冻结任务、阈值、动作域、预算、任务哈希 |
| `fixtures/day1/network-manifest.json` | 数据来源、单位、许可描述、原始文件和 overlay 哈希 |
| `fixtures/day1/network.inp` | 原创调试拓扑，尚未完成真实引擎验证 |
| `fixtures/day1/scene.json` | 二维/三维通用实体 ID 与坐标 |
| `fixtures/day1/candidate-{a,b}.json` | 两个 ActionPlan |
| `fixtures/day1/result-{a,b}.json` | 完整 SimulationResult：artifact + series + diagnostics |
| `fixtures/day1/series-{a,b}.json` | 原始样例时序，避免仅有汇总数字 |
| `fixtures/day1/verification-{a,b}.json` | 独立复算的 VerificationReport，含节点、时段、阈值、五层状态 |
| `fixtures/day1/events.jsonl` | 8 个 RunEvent，单调 sequence，无伪造 AGH 字段 |
| `fixtures/day1/run-initial.json` / `run.json` | 启动快照 / 结束快照，事件引用不可变快照 |
| `fixtures/day1/evidence-manifest.json` | 样例文件索引与字节哈希、缺失项；不允许 Live 导出 |
| `waterguard_contracts/core.py` | Python 验证入口、参考指标复算和 FixtureAdapter |
| `examples/consumer.ts` | 前端类型消费示例，并由 tsc 检查 |

主要公共类型：`NetworkManifest`、`TaskSpec`、`ActionPlan`、`ActionCatalog`、`NetworkScene`、`SimulationArtifact`、`SimulationResult`、`Series`、`SeriesPage`、`VerificationReport`、`RunRecord`、`RunEvent`、`EvidenceManifest`。`SimulationArtifact` 是索引元数据，`SimulationResult` 是 Adapter 返回的完整传输对象，两者不混用。

前端从 npm workspace 消费类型：

```ts
import type { TaskSpec, SimulationResult, VerificationReport, RunEvent } from '@waterguard/contracts';
```

Python 通过项目路径导入（无额外打包安装步骤）：

```python
import sys
sys.path.insert(0, 'packages/contracts')
from waterguard_contracts import FixtureAdapter, load, validate
from waterguard_contracts.core import FIXTURES

task = load(FIXTURES / 'task.json')
plan = load(FIXTURES / 'candidate-b.json')
result = FixtureAdapter().simulate(task, plan)
validate('SimulationResult', result)
```

`simulate(task, plan)` 只接受完全相同的冻结输入；未知候选报错，不返回“看起来合理”的结果。每次返回独立数据，修改一个结果不会污染后续候选。`get_series(simulation_id, node_id=None, start_s=None, end_s=None)` 支持节点与半开时间窗过滤，返回 `SeriesPage`；空范围返回空 frames。完整 `Series` 不允许空帧。真实 Adapter 后续实现同一方法形状，进程、超时、取消由作业系统处理。

## Mock HTTP 与 SSE

只读服务，模式响应头为 `X-WaterGuard-Mode: fixture`；仅监听 127.0.0.1。浏览器开发 CORS 允许 `http://localhost:5173` 与 `http://127.0.0.1:5173`。

| GET 路径 | 返回 |
|---|---|
| `/api/fixture` | TaskSpec，开发启动入口 |
| `/api/networks` | NetworkManifest 数组 |
| `/api/networks/wg-fixture-loop-v1/scene` | NetworkScene |
| `/api/tasks/task-day1-closure` | TaskSpec |
| `/api/runs/run-day1-fixture` | RunRecord |
| `/api/runs/run-day1-fixture/candidates` | `{plan, verification}` 数组 |
| `/api/simulations/simulation-b/series?node_id=J2&start_s=3600&end_s=7200` | SeriesPage；同样支持 simulation-a |
| `/api/runs/run-day1-fixture/events` | SSE；命名事件，`id` 是 sequence 的十进制字符串 |
| `/api/runs/run-day1-fixture/evidence` | EvidenceManifest |
| `/api/artifacts/{ref}` | 索引内文件，供 evidence/payload_ref/series_ref 读取 |

所有 ref 都相对 `fixtures/day1/`，HTTP 客户端用 `/api/artifacts/` 解析；不把路径当任意文件访问入口。前端需通过 `addEventListener('verification.completed', ...)` 等读取命名事件。新连接会返回完整固定事件序列；设置 `Last-Event-ID: 6` 仅返回 7、8。超过末尾的 ID 返回 400；等于末尾返回 204，停止 EventSource 自动重连。没有新事件生成，不模拟实时计算耗时。

启动/取消/修订等写请求返回 405 `READ_ONLY_FIXTURE`。这能支持三人前置的读取与展示联调，正式作业 API 和服务端确认取消由同学 2 实现。

## 冻结与一致性规则

- 单位固定为 m、m³/s、s。INP 原生使用 LPS、管径 mm，真实 Adapter 必须转换；展示转换不改变内部字段。
- 时序采用区间起点值保持到下一边界，区间为 `[time_s,time_s+duration_s)`；末端 10800 不额外计一帧。泄漏流量不计入用户供水。
- 五层状态采用 `pass/fail/pending`。`pending` 不得当作 false 或 true；数值无效时名义约束是 pending。零总需求的供需比为 null。
- JSON Schema 检查字段和类型，Python 语义检查再核对 ID、版本、完整时间轴、动作和哈希。TS 类型不能代替运行时校验。非有限值、负需求、未知节点、缺帧和未收敛均不得获得有效结论。
- 网络、overlay、目录和证据文件用 UTF-8 文件字节 SHA-256。仓库强制 LF，避免换行影响哈希。
- `constraint_hash` 绑定除其自身以外的完整 TaskSpec；`plan_hash` 同理绑定完整计划；`input_hash` 绑定完整任务、计划、引擎版本和扰动 ID。本版编码规则为 Python `json.dumps(sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)`；**不是 RFC 8785**。Python 作为唯一哈希写入方，前端转发服务端 hash，不用 JS 自行重算浮点 JSON。
- `evidence-manifest.json` 不把自身放入哈希清单，避免自引用。哈希只证明一致性，不证明物理真实性或来源认证。
- 变更公共字段时由同学 2 统筹、三人确认消费端影响；改 schema_version，更新 Schema、类型、Fixture 与测试。调整阈值/动作/网络必须创建新任务 revision 和哈希，不能修改旧运行含义。

维护者改完源 Schema 后执行 `npm run contracts:types`。修改固定案例时显式运行 `python packages/contracts/scripts/build-fixtures.py`，它会覆盖 day1 示例；日常 `check`、`demo`、`mock` 不修改 Fixture。CI 检查生成类型未漂移、Fixture 可重建、契约和 HTTP/SSE 测试。

参考工具：[JSON Schema 对象约束](https://json-schema.org/understanding-json-schema/reference/object)、[json-schema-to-typescript](https://github.com/bcherny/json-schema-to-typescript)。

## 三人拿到后可以立即开始

| 成员 | 现成输入 | 接下来的独立工作 | 接回共同链路的条件 |
|---|---|---|---|
| 同学 1：仿真与数据 | INP、Manifest、TaskSpec、ActionPlan、SimulationResult、Adapter 形状 | 下载可再分发的公开网络；验证 WNTR/EPANET；输出真实时序、诊断与版本 | 新的真实结果满足契约；物理测试通过，保留真实来源和原始产物 |
| 同学 2：AGH 与后端 | Schema、FixtureAdapter、参考验证器、事件和证据格式 | AGH 探针、FastAPI/作业、权限/预算/版本、生产验证器、真实反馈闭环 | 将 FixtureAdapter 替换为真实进程 Adapter，并接入原生 AGH 证据 |
| 同学 3：前端与交付 | TS 类型、scene、只读 Mock API、事件、违规和比较数据 | React 二维工作台、图层/曲线/事件、五层状态、证据查看 | 固定 Fixture 与真实 API 的读取形状兼容；界面始终明确运行模式 |

本包的 `verify_fixture()` 是可审查的参考实现，只做样例数据有效性和指标计算。生产验证器仍需补齐真实动作编译、引擎收敛判定、控制冲突、任务权限、复验覆盖和证据准入。真实结果不得传给该函数冒充正式验证。

## 来源说明

`network.inp`、坐标、overlay、阈值和时序均为本项目本次创建的开发材料，没有引入第三方管网数据。Manifest 的 `LicenseRef-WaterGuard-Internal-Fixture` 表示随本仓库内部协作使用，未作独立开放许可承诺。公开管网的数据来源、许可及下载哈希仍由同学 1 后续建立。
