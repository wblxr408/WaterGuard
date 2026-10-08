# WaterGuard · GPT 前端

唯一前端位于 `apps/web-gpt`，React + TypeScript + Vite。智能体页提供任务创建、运行取消、AGH 模型账号配置、持续 SSE 工作流与 Info log。旧 HTML 工作台和 `apps/web` 原型已移除。

当前视觉为 **v4 工业工作台**：石墨色全局栏、紧凑侧栏、灰白全宽工作区和青绿操作色；工况与约束集中在可展开区域，三项关键指标、管网和实体详情组成统一工作面。设备展台与首页拆装序列使用团队自建 Blender 资产。研究来源、布局规则和验证见 [视觉重构记录](design/ui-refinement-v4.md)。

## 当前电脑运行

在 PowerShell 中：

```powershell
cd "D:\WaterGuard\apps\web-gpt"
npm ci
pwsh -ExecutionPolicy Bypass -File .\scripts\start-demo.ps1
```

访问 **http://127.0.0.1:5173/**。启动脚本会解压仓库交付的真实计算证据，启动三个只读后端、8010 任务 API 和 Vite；已运行且匹配的后端会复用。Ctrl+C 结束前端并清理本脚本创建的后端，保留原有服务。日志保存在 `.cache/backend-*.log`。仓库根目录可执行 `npm run start:web`。

如果已经启动后端，可直接在本目录执行 `npm run dev`。5173 被其他前端占用时，先结束对应开发服务器；不自动抢占或换端口。

智能体页使用独立的任务运行服务，默认端口 8010。需要 AGH 会话时在仓库根目录另开终端运行 `npm start`；它复用已启动的 API。智能体页可独立打开，不依赖 A/B 只读服务。可用 `http://127.0.0.1:5173/?run=<run_id>` 直接查看指定运行。8010 根路径跳转到新版，查询参数保留；其他前端部署地址可通过 API 的 `WG_FRONTEND_URL` 配置。

## 全新环境准备

Node.js 22，Python 3.11，PowerShell 7（`pwsh`）；从**仓库根目录**：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r services/api/requirements.txt -r services/hydraulics/requirements.txt
cd apps/web-gpt
npm ci
pwsh -ExecutionPolicy Bypass -File .\scripts\start-demo.ps1
```

无需重跑水力计算：脚本读取 `benchmarks/student1/verified-results.zip` 与 `feedback-pair.zip` 的已有结果。依赖安装不需要 OpenAI API Key。

## 界面与数据

| 页面 | 功能 |
| --- | --- |
| 首页（默认） | 深色主题，两块。开场是团队自建的闸阀渲染图配一句标题；往下滑时画面钉住，闸阀拆成 10 个零件、转半圈、再装回去。第二块「我们的系统」：四个模块的真实界面截图，可切换；点进入时用「波光粼粼」的水面转场 |
| 智能体 | 默认选择任务运行服务，持续 SSE 同步所选运行的阶段、原生工具调用、候选及独立验收。支持分支导图、流程总览、事件追踪、逐条回放和节点详情。Info log 提供级别/来源筛选、搜索、跟随最新、事件定位、原文查看和 JSON 导出。人工样例回放需显式选择；未观察到原生调用时，智能体角色显示无调用记录 |
| 应急工作台 | 顶部「智能体运行」条（运行阶段、方案提出者和预算用量，来自实际运行记录）； 故障任务、真实指标、可缩放平移的拓扑（悬停数值卡、编号搜索定位、全屏）、压力/供需比/流量图层、节点/管段详情、时间回放、区间保持曲线、五层准入、违规定位 |
| 方案对照 | 同任务 A/B 比较；场景库正常/故障工况明确标记为不同任务；显示服务端推荐结论和原始验收 |
| 建模与仿真 | 泵站设备、管道剖面两张 Blender 渲染图，可下载 GLB（未配置渲染图时退回可交互的 SVG 示意）；计算参数、守恒检查、来源与实体映射 |
| 证据中心 | 文件索引与完整哈希、接口结果查看、搜索、证据索引和 JSON 读取副本导出 |

数据通过 Vite 同源代理：

- `/ab` → 8002，默认真实 Net3 A/B 对照。
- `/bm` → 8001，两个公开网络、22 个真实工况。
- `/fx` → 8000，人工 Fixture，命名 SSE 固定事件回放。
- `/wg` → 8010，任务运行 API，支持 Live、Benchmark、Replay、Fixture 的真实运行记录。可在 `apps/web-gpt/.env.local` 设置 `WATERGUARD_API_URL=http://127.0.0.1:<端口>`，重启 Vite 后生效。

只读数据源请求核对 `X-WaterGuard-Mode`；任务服务核对 RunRecord、事件、候选及验收的运行模式与冻结任务绑定。模式或任务绑定不符会停止展示；不回退到静态样例。数字来自接口，展示时取舍小数；缺供改善百分比按同一冻结任务的首个候选计算。曲线按 `interval_start_hold` 绘制阶梯，不裁剪负压力。

智能体页“新建任务”提交管网、故障、阈值和预算，由服务端冻结任务并启动 Benchmark 或 Live，随后自动选择该运行并订阅。“取消当前运行”只在可取消状态出现。“模型账号”默认赛事 Agnes，通过 AGH 实际提供商和模型目录测试后保存，Key 不进入浏览器持久存储。真实模型驱动的 AGH Live 仍需有效凭据验收。Net3 的示意坐标不代表真实地理位置。最终推荐严格读取 `VerificationReport.admission`；B 改善而未达标的真实结果保留。

持续接口为 `GET /api/runs/{run_id}/stream`，同一订阅发送 `workflow`、`info`、`run`，以持久化序号作为 SSE `id`，自动重连通过 `Last-Event-ID` 续读。旧事件历史会补齐。提交候选的仿真结束后保留订阅，直到该候选的独立验收事件送达；错误、取消和预算终止直接结束订阅。终态同步完成后发送 `stream.complete` 并关闭。心跳与禁用代理缓冲保持长连接。前端最多保留最近 1000 条日志；取消“跟随最新”只停止滚动，切换运行会关闭旧订阅并清空记录。日志记录运行状态和工具摘要，不包含模型正文、工具参数或凭据。

## Blender 接入给 Claude

配置文件：`src/model-assets.ts`。资产目录：`public/models/`，详见其中 `README.md`。

```ts
{
  id: 'pump',
  title: '泵站设备',
  subtitle: '实际模型的说明',
  previewUrl: '/models/pump-station.webp',
  sourceUrl: '/models/pump-station.glb',
  author: '建模同学',
}
```

填入实际渲染图后，组件展示 Blender 渲染成果；填入源文件后开放下载。没有 GLB 实时解析器；需要实时三维时，替换 `ModelDemo.tsx` 的 SVG 区域。可关联设备命名与水力实体 ID，不能用艺术几何替代水力参数。

**已接入（claude-polish）**：两张模型卡都显示团队自建 Blender 模型的 Cycles 渲染图，可下载 `equipment.glb`。泵站设备用 `public/models/blender-pump-pearl.webp`；管道剖面用 `blender-pipe-burst.webp`，内容是球墨铸铁管剖切和爆管喷水，喷水为示意；建模页首图用四件设备合影 `blender-equipment-pearl.webp`；工作台入口卡片用爆管图。模型由 `tools/blender/build_equipment.py` 生成，透明底渲染图由 `tools/blender/render_pearl.py` 输出，源文件 `assets-src/equipment.blend`。复核与改动说明见 [REVIEW.md](REVIEW.md)。

## 生图素材

使用内置 image_gen 完成构图探索及设备展台的材质、取景、光照迭代。最终提示词及用途在 `design/image-prompts-v3.json`。

- `public/images/waterguard-studio-pearl-v3.png`：v3 曾用作工作台入口及建模页的设备展台，AI 概念素材，非实际 Blender 成果；claude-polish 起改用 Blender 渲染图，此文件保留不再引用。
- `design/workbench-reference-v3.png`：构图参考，不在应用中渲染，其中数值不进入业务界面。
- 旧版本和探索素材保留作设计过程记录，当前界面不引用。

交互、拓扑、曲线和文字均由可编辑代码实现，未将整张 UI 图作为实际页面。

## 验证与截图

```powershell
npm run typecheck
npm run test:workflow
npm run build
pwsh -File .\scripts\start-demo.ps1 -CheckOnly
```

浏览器验证使用 Playwright CLI，无 `@playwright/test` 依赖：

```powershell
npx --yes --cache .cache/npm --registry https://registry.npmjs.org --package @playwright/cli playwright-cli -s=waterguard-gpt open http://127.0.0.1:5173 --headed
npx --yes --cache .cache/npm --registry https://registry.npmjs.org --package @playwright/cli playwright-cli -s=waterguard-gpt run-code --filename scripts/browser-check.js
npx --yes --cache .cache/npm --registry https://registry.npmjs.org --package @playwright/cli playwright-cli -s=waterguard-gpt run-code --filename scripts/browser-edge-check.js
npx --yes --cache .cache/npm --registry https://registry.npmjs.org --package @playwright/cli playwright-cli -s=waterguard-gpt run-code --filename scripts/browser-visual-check.js
```

CLI 文件是函数表达式，末尾不加分号。浏览器回归覆盖真实 A/B、候选/时间/实体联动、违规定位、两个模型操作、Esc、证据筛选/下载、Fixture SSE、场景切换和窄屏；边界脚本逐一读取 22 工况并注入缺失模式、模式不符与 503，确认清空旧结果及恢复。（claude-polish 起两张模型卡改为渲染图，`browser-check.js` 里针对 SVG 视角与分解的检查需要改写，见 REVIEW.md。）

新增布局脚本覆盖四个宽度、四个页面，检查默认拓扑节点未裁切、三项指标、图片加载和页面溢出。

截图在 `output/playwright/`，包括工作台、建模、对照、证据、手机和不可用状态。`TEST-REPORT.md` 记录本次真实验收结果与未验证范围。

## 文件入口

- `src/App.tsx`：导航、应用状态、工作台、原始数据弹窗。
- `src/data.ts`：公共契约类型、严格 API 读取、三源适配。
- `src/Network.tsx`：真实拓扑（缩放平移、就近点选、悬停数值卡、搜索定位、全屏）、实体选择、阶梯压力曲线。
- `src/Pages.tsx`：对照、建模、证据、验收反馈与 Fixture SSE。
- `src/ModelDemo.tsx` / `model-assets.ts`：交互示意与 Blender 资产入口。
- `src/Cover.tsx` / `cover.css`：首页。
  - `CoverHero.tsx` 是开场的闸阀拆装，序列帧在 `public/cover/hero-valve/`，由 `tools/blender/build_hero.py S` 生成；
  - `CoverSystem.tsx` 是「我们的系统」，截图 `public/cover/system-*.webp` 由 `tools/capture-system.mjs` 生成；
  - `water-transition.ts` 是进入模块时的水面转场。
- `src/AgentPage.tsx` / `agent-page.css`：智能体页面。分支导图的节点、分支和状态换算在 `agent-mindmap.ts`；流程总览的在 `agent-flow.ts`。
- `src/agent-stream.ts` / `AgentLogs.tsx`：共享 SSE 订阅、运行与结果校验、Info log 面板。
- 以下文件首页已不引用，等确认后删除：
  - `CoverAgent.tsx`：上一版首页的智能体简介；
  - `cover-stage.ts`：旧版首页的管网全息沙盘；
  - `CoverTeardown.tsx`：旧版闸阀拆解，序列帧在 `public/cover/valve/`；
  - `public/cover/hero-a.webp`、`hero-b.webp`、`hero-c.webp`、`hero-c-lines.webp` 和 `src/cover-hero-anchors.ts`：挑开场方向时的三张备选。

  闸阀零件本身仍由 `tools/blender/build_cover_valve.py` 建模，开场序列也用它。
- `src/AgentRun.tsx` / `agent-run.css`：工作台的智能体运行条。
- `src/styles.css`：统一视觉系统和响应式断点。

生产部署未执行。`npm run preview` 仍需要本机三个后端；正式部署需配置 API 路由，不能把只有静态页面的部署称为完整服务。

工作台只展示当前操作、运行状态和结果。角色权限、工具契约、开发接入步骤等维护在项目文档中，不作为工作台模块。
