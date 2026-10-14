# 官方AGH轨迹导出

赛事原生轨迹须通过AGH官方会话界面的虫子按钮导出。WaterGuard脚本保存的SDK events.jsonl和业务evidence/download ZIP是实际辅助证据，不能替代这份官方界面导出包。

官方会话：waterguard:run-efda67724ab043ccaf9a49a30373f161。打开本机http://127.0.0.1:4177/并选中该会话，点击右上角Report a problem（虫子）→Share diagnostics→勾选Conversation and trace、Logs、System information→Generate package→Save ZIP。

2026-10-14由用户通过官方界面手动保存并提供[agh-diagnostics-runefda6-20261014-131652.zip](official-agh-trace/agh-diagnostics-runefda6-20261014-131652.zip)，272,484字节。文件已归档到仓库docs/submission/official-agh-trace，并原样加入WaterGuard-submission-public-record-under-200MB-20261014.zip的official-agh-trace目录；Release同时提供独立原ZIP附件。

读取原ZIP确认：trace.json的sessionId为waterguard:run-efda67724ab043ccaf9a49a30373f161，包含两轮对话、165个轨迹节点及截至700的序列；附带index.html、system.json、events.jsonl和daemon/host/browser日志。该核对只读取元数据，未修改原文件。

查看时将原ZIP解压至独立目录，用浏览器打开index.html。官方导出中的历史模型解释和原准入状态保留原文，当前挽救效果由项目独立报告另行说明。此次仅补交材料，不重跑方案、不创建新模型任务。

## 关于 `.agh` 文件

本项目固定版本的官方诊断导出界面使用“Save ZIP”。当前报名包含上述官方诊断ZIP，没有后缀为`.agh`的文件。该ZIP保存会话轨迹、事件、日志和查看页；它与`.agh`专用文件格式是否等价，尚无赛事格式说明支持，不能通过更改后缀宣称获得另一种官方导出。
