# WaterGuard · GPT 前端

独立实现于 `gpt-web` 分支、`apps/web-gpt`，React + TypeScript + Vite。未改动队友 `apps/web`、后端、公共契约或原始计算结果。

当前视觉为 **v3**：顶部横向导航，明亮的珍珠白、淡紫与暖光渐变覆盖整个系统；三项关键指标与管网组成统一工作面。设备展台和两张模型卡使用团队自建 Blender 模型的透明底渲染图（claude-polish 接入）。

## 当前电脑运行

在 PowerShell 中：

```powershell
cd "E:\ai-program\互联网比赛\江苏黑客松\WaterGuard\apps\web-gpt"
npm ci
pwsh -ExecutionPolicy Bypass -File .\scripts\start-demo.ps1
```

访问 **http://127.0.0.1:5173/**。启动脚本会解压仓库交付的真实计算证据，并启动三个只读后端和 Vite；已运行且模式匹配的后端会复用。Ctrl+C 结束前端并清理本脚本创建的后端，保留原有服务。日志保存在 `.cache/backend-*.log`。

如果已经启动后端，可直接在本目录执行 `npm run dev`。5173 被其他前端占用时，先结束对应开发服务器；不自动抢占或换端口。需要和 GPT 原版并排对比时，可显式换端口：`npm run dev -- --port 5175`。

本次交付时四个服务已在本机运行。

## 全新环境准备

Node.js 22，Python 3.11；从**仓库根目录**：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r services/hydraulics/requirements.txt
cd apps/web-gpt
npm ci
pwsh -ExecutionPolicy Bypass -File .\scripts\start-demo.ps1
```

无需重跑水力计算：脚本读取 `benchmarks/student1/verified-results.zip` 与 `feedback-pair.zip` 的已有结果。依赖安装不需要 OpenAI API Key。

## 界面与数据

| 页面 | 功能 |
| --- | --- |
| 首页（默认） | 深色主题，两块。开场是团队自建的闸阀渲染图配一句标题；往下滑时画面钉住，闸阀拆成 10 个零件、转半圈、再装回去。第二块「我们的系统」：四个模块的真实界面截图，可切换；点进入时用「波光粼粼」的水面转场 |
| 智能体 | Dify 式工作流画布。流程：开始 → 任务建模 → 冻结任务 → 方案规划 → 水力仿真 → 独立验收 → 达标？（否：回到方案规划）→ 反例审查 → 打包证据 → 结束。节点按类型着色并显示状态；「回放运行」按运行事件逐个点亮节点和连线；点节点，右侧显示它做什么、能调用的工具、权限边界和本次运行的数据，另有「追踪」列表。下方是设计细节。AGH 未接入前数据是人工样例，Agnes 承担的三个角色显示「未接入」 |
| 应急工作台 | 顶部「智能体运行」条（运行阶段、方案提出者、接入状态，均来自契约数据）； 故障任务、真实指标、可缩放平移的拓扑（悬停数值卡、编号搜索定位、全屏）、压力/供需比/流量图层、节点/管段详情、时间回放、区间保持曲线、五层准入、违规定位 |
| 方案对照 | 同任务 A/B 比较；场景库正常/故障工况明确标记为不同任务；显示服务端推荐结论和原始验收 |
| 建模与仿真 | 泵站设备、管道剖面两张 Blender 渲染图，可下载 GLB（未配置渲染图时退回可交互的 SVG 示意）；计算参数、守恒检查、来源与实体映射 |
| 证据中心 | 文件索引与完整哈希、接口结果查看、搜索、证据索引和 JSON 读取副本导出 |

三个数据源通过 Vite 同源代理：

- `/ab` → 8002，默认真实 Net3 A/B 对照。
- `/bm` → 8001，两个公开网络、22 个真实工况。
- `/fx` → 8000，人工 Fixture，命名 SSE 固定事件回放。

每次请求强制核对 `X-WaterGuard-Mode`。缺失、模式不符或任务绑定不符会停止展示；不回退到静态样例。数字来自接口，展示时取舍小数；缺供改善百分比按同一冻结任务的首个候选计算。曲线按 `interval_start_hold` 绘制阶梯，不裁剪负压力。

本次没有 AGH Live / Replay 链路，不能创建、执行、修订或取消任务；不把固定工程脚本展示成 AI 决策。Net3 的示意坐标不代表真实地理位置。最终推荐严格读取 `VerificationReport.admission`；B 改善而未达标的真实结果保留。

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
- `src/AgentPage.tsx` / `agent-page.css`：智能体页面。工作流的节点、连线，以及「运行事件 → 节点状态」的换算在 `agent-flow.ts`。
- 以下文件首页已不引用，等确认后删除：
  - `CoverAgent.tsx`：上一版首页的智能体简介；
  - `cover-stage.ts`：旧版首页的管网全息沙盘；
  - `CoverTeardown.tsx`：旧版闸阀拆解，序列帧在 `public/cover/valve/`；
  - `public/cover/hero-a.webp`、`hero-b.webp`、`hero-c.webp`、`hero-c-lines.webp` 和 `src/cover-hero-anchors.ts`：挑开场方向时的三张备选。

  闸阀零件本身仍由 `tools/blender/build_cover_valve.py` 建模，开场序列也用它。
- `src/AgentRun.tsx` / `agent-run.css`：工作台的智能体运行条。
- `src/styles.css`：统一视觉系统和响应式断点。

生产部署未执行。`npm run preview` 仍需要本机三个后端；正式部署需配置 API 路由，不能把只有静态页面的部署称为完整服务。
