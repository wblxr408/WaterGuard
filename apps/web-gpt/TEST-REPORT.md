# 前端交付验收

日期：2026-10-03（Asia/Shanghai）。分支：gpt-web。最终视觉：v2，顶部导航、雾白与钴蓝。只修改 apps/web-gpt。

## 通过项

- `npm run typecheck` 通过；`npm run build` 通过。
- 构建 JS 约 279 kB，gzip 约 87 kB；CSS 约 43 kB，gzip 约 10 kB。生成概念图另计，不包含在 JS 包体内。
- `scripts/start-demo.ps1 -CheckOnly` 实际执行通过：三个现有后端模式匹配并复用。Vite 已在 127.0.0.1:5173 实际运行。
- 浏览器工作流 25 项断言通过，最终 v2 样式下复跑通过：真实 A/B、负压保留、候选/时刻/实体联动、管段故障结束恢复、违规定位、模型视角/分解、实体映射、Esc、证据搜索/物理结果/下载、Fixture 模式和 8 个命名 SSE 事件、推荐结论边界、真实工况选项及手机溢出。
- 边界脚本 27 项通过：22 个真实工况逐项载入，模式头缺失/模式不符/503 清空旧结果、重试恢复、768px 平板无页面横向溢出。
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
- `output/playwright/07-unavailable.png`

## 任务反思

- What Worked：真实契约驱动联动与验收；将 UI 生图用于视觉探索，将代码用于实际操作；用户复审后移除侧栏并统一视觉。
- Issues：第一版绿色侧栏未符合用户审美；需要重新组织布局。实际 Blender 与 AGH 接口仍待队友交付。
- Candidate Project Rule：展示水力结果前强制核对模式响应头与任务绑定；缺失/不符时清空旧结果，不降级到演示数据。已由本次缺失模式、模式不符和恢复测试验证。
- Candidate Global Rule：None。

以上候选规则只保留在交付记录，不自动写入 AGENTS.md。
