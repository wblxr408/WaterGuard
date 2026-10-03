# 前端交付验收

日期：2026-10-03（Asia/Shanghai）。分支：gpt-web。最终视觉：v3，顶部导航、全系统明亮珍珠白 / 淡紫 / 暖光背景。只修改 apps/web-gpt。

## 通过项

- `npm run typecheck` 通过；`npm run build` 通过。
- 构建 JS 约 279 kB，gzip 约 87 kB；CSS 约 43 kB，gzip 约 9 kB。生成概念图另计，不包含在 JS 包体内。
- `scripts/start-demo.ps1 -CheckOnly` 实际执行通过：三个现有后端模式匹配并复用。Vite 已在 127.0.0.1:5173 实际运行。
- 浏览器工作流 25 项断言通过，v3 结构改造后复跑通过：真实 A/B、负压保留、候选/时刻/实体联动、管段故障结束恢复、违规定位、模型视角/分解、实体映射、Esc、证据搜索/物理结果/下载、Fixture 模式和 8 个命名 SSE 事件、推荐结论边界、真实工况选项及手机溢出。
- v3 新增布局脚本 40 项通过：1600 / 1440 / 768 / 390 宽度下，四个页面无页面溢出且图片成功加载；默认管网节点全部在画布内；仅展示三项核心指标。
- 前一版边界脚本 27 项通过（本次 API 层未改动）：22 个真实工况逐项载入，模式头缺失/模式不符/503 清空旧结果、重试恢复、768px 平板无页面横向溢出。
- 桌面 1600×1150、手机 390×844、平板 768×1024；桌面和手机实际截图已检查。图片、长哈希和引擎字符串有边界处理；表格在窄屏独立横向滚动。
- 最新页面没有意外控制台错误。边界脚本故意注入的 HTTP 503 会产生浏览器资源错误，属于预期测试；非应用崩溃。
- 原始输入、时序、验收和证据下载均来自后端；索引导出确认 mode=benchmark。

## 已知边界

- 没有 AGH Live / Replay 接口，未验证实时 Agent 决策和写入任务。当前 Benchmark 是固定脚本真实计算。
- Blender 实际成果尚未接入；两个交互对象是程序几何示意。预留渲染图与源文件下载配置；没有 GLB 实时加载器。
- 后端未开放全部原始 Benchmark 文件下载，灰色文件仅提供索引/哈希。JSON 导出为接口读取副本，不保证与原始证据字节一致。
- 没有现场校准；示意坐标、试验假设与水锤边界已在页面或原始资料中标明。
- 未部署、未推送远端；全新电脑上的依赖首次安装与 Ctrl+C 进程清理尚未端到端测试。当前机器的后端读取、脚本复用检查、构建与浏览器联调已验证。
- 不宣称正式无障碍认证、跨浏览器性能测试或完整生产业务完成。

## 截图

- `output/playwright/01-workbench.png` / `01-workbench-full.png`
- `output/playwright/02-model-studio.png` / `02-model-studio-full.png`
- `output/playwright/03-comparison.png`
- `output/playwright/04-evidence.png`
- `output/playwright/05-mobile-viewport.png` / `05-mobile-workbench.png`
- `output/playwright/06-mobile-viewport.png` / `06-mobile-models.png`
- `output/playwright/07-unavailable.png`（上一版错误状态截图）

## 任务反思

- What Worked：把结果、管网和实体详情合并为统一工作面；生产图片按界面背景与光照制作；真实浏览器验证覆盖四种宽度。
- Issues：前期配色方向与用户偏好有偏差；实际 Blender 成果仍待接入。
- Candidate Project Rule：None。
- Candidate Global Rule：None。

不自动写入 AGENTS.md。
