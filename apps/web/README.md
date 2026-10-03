# WaterGuard 前端 · 初始界面（Claude 版）

按 [前端设计](../../docs/前端设计.md) 第 11 节的优先级实现第一步：**真实二维视图 + 运行记录面板 + 完整工作台布局**。三维（Three.js / Blender 设备资产）尚未实现。

界面里的所有数字都来自接口，前端不生成仿真数字。

## 三个数据源

| 顶栏按钮 | 代理前缀 | 后端 | 模式 | 内容 |
|---|---|---|---|---|
| 真实对照 · Net3 A/B | `/ab` | 8002 | Benchmark | 同学 1 的真实 A/B 反馈样例：Net3 管段 123 关闭，A 不处置、B 启动泵 10 |
| 真实场景库 · 22 次计算 | `/bm` | 8001 | Benchmark | Net1/Net3 × 正常/关闭/泄漏/断裂/隔离/泵启停/管段启闭，自动配正常工况对照 |
| 开发样例 · Fixture | `/fx` | 8000 | Fixture | day1 冻结样例，含 SSE 事件流 |

浏览器只访问 5173，三个后端走 Vite 代理（见 `vite.config.ts`）。

## 本地运行

在仓库根目录执行（Python 3.11、Node 22）：

```powershell
# 1. Python 环境（只需一次）
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r services/hydraulics/requirements.txt

# 2. 真实结果：解压同学 1 已提交的证据包（只需一次，artifacts/ 已在 .gitignore）
.venv/Scripts/python.exe -c "import zipfile; zipfile.ZipFile('benchmarks/student1/feedback-pair.zip').extractall('artifacts/feedback-pair'); zipfile.ZipFile('benchmarks/student1/verified-results.zip').extractall('artifacts/student1')"

# 3. 三个只读后端（各开一个终端）
.venv/Scripts/python.exe packages/contracts/scripts/mock_server.py
.venv/Scripts/python.exe -m services.hydraulics.server --output artifacts/student1 --port 8001
.venv/Scripts/python.exe -m services.hydraulics.server --output artifacts/feedback-pair --port 8002

# 4. 前端
cd apps/web
npm install
npm run dev        # http://127.0.0.1:5173
```

只开了其中一个后端也能用，切到没开的数据源时页面会提示该启动哪个。

## 已实现

- 顶栏：数据源切换、运行模式标识（取自响应头 `X-WaterGuard-Mode`）、AGH 状态（目前固定显示“未接入 · 执行来源待核验”）。
- 左栏：故障、保供硬约束及来源原文、可操作范围、计算设定、控制覆盖、假设；场景库模式下可选管网 / 引擎 / 工况。
- 指标卡：缺供水量、最低压力、供需比、违规条数、处置动作，显示相对另一方案的差值；点击可查看数字来源（字段、仿真编号、验收版本、引擎、输入哈希、接口地址）。
- 管网图（SVG）：节点按压力 / 供需比上色（同时用圆的大小区分），管段按实际物理状态显示（关闭、泄漏、断裂、隔离），故障点、漏水点、处置动作、水箱水位；“相对另一方案的变化”视图；点击节点或管段联动检视面板和曲线。
- 时间轴：预运行段、故障段、动作刻度、违规刻度；播放；键盘 ← → 逐帧、空格播放。
- 压力曲线（ECharts）：所有方案叠加、阈值线、预运行与故障时段底色、当前时刻线。
- 右栏：五层准入（“待定”用斜纹，不当作通过）、阻碍原因、违规定位（点击跳到节点和时刻）、运行记录（Fixture 用 SSE 实时接收；Benchmark 明确说明没有 AGH 记录并原样显示数据自述）、证据与物理检查。
- 方案比较 / 工况对照。

## 已知限制

- 还没有三维视图和 Blender 设备资产。
- 写操作（创建任务、启动、取消、修订）和 Live 事件流要等同学 2 的作业接口。
- 打包体积 806 KB（gzip 266 KB），主要是 ECharts，后续可拆成按需加载。
- Net3 坐标是示意布局，界面已标注，不代表真实距离。
