# AGH 比赛验收操作手册

本手册用于重新生成可人工核查的 WaterGuard AGH 执行记录。模型 Key 只保存在 AGH 私有目录；不要把 Key 写入命令、截图、Git 或证据包。

## 启动与检查

在 `waterguard-backend` Conda 环境中启动 API，并使用 Node 24+ 启动 AGH。设置 `WG_AGH_HOME` 指向私有目录后，运行 `scripts/configure_agh.mjs` 更新、信任和启用 WaterGuard 插件。配置脚本返回 `READY_FOR_MODEL_SMOKE` 后，工作台或 API 才允许 `mode=live`。

检查项目 API 为 `http://127.0.0.1:8010/healthz`，AGH daemon 可达，插件列表包含 12 个 `wg_*` 工具。若修改插件，必须重新运行配置脚本后再做 Live 验收。

## 四类必留记录

1. 正常闭环：创建 Net1 正常任务，启动 `mode=live`；确认任务检查、候选、仿真、独立验证、1.1 倍需求压力测试和证据构建均出现在原生 trace。
2. 失败—修正：启动 Live 时传入 `acceptance_scenario: "repair_demo"`。确认首个 `wg_submit_plan` 返回 `accepted:false`、`code:"INVALID_ACTION"`；随后必须有 `wg_generate_candidates`、合法 `wg_submit_plan`、仿真与验证。
3. 取消：仅在测试环境设置 `WG_TEST_MODE=1` 与 `WG_TEST_SIMULATION_DELAY_S=20`，启动 Live，等待子仿真预留后调用 `POST /api/runs/{id}/cancel`。确认运行保持 `CANCELLED`、原生 trace 有 `aborted`，迟到结果未覆盖终态。
4. 恢复：在运行中重启 API。旧运行应为 `ERROR` 且错误码 `SERVER_RESTARTED`；调用 `POST /api/runs/{id}/recover` 创建新 `run_id`。确认旧运行 `SUPERSEDED`、新运行带 `resumed_from_run_id`，两侧 `continuity.json` 的 `continuity_boundary` 为 `fresh_agh_session`。

## 导出与核查

每个终态运行调用 `GET /api/runs/{id}/evidence`，再调用 `GET /api/runs/{id}/evidence/download` 下载 ZIP。正式正常/修正记录必须满足 `live_export_allowed=true`、`missing_items=[]`，并包含 `agh/native/events.jsonl`、`agh/probe.json`、`agh/association.json`、候选、原始时序、验证报告、推荐摘要与 `evidence-manifest.json`。

取消记录是异常处理证据，不应因缺少完整关联或压力验证而被标为正式推荐。重启记录必须同时提交旧运行与后继运行的证据包，证明没有伪造原会话续接。

## 已知边界

比赛版采用单受限 AGH 会话分阶段执行，不声称已实现可信多代理身份隔离。API Key、内部账号标识和本机绝对路径不应进入提交材料；必要时只提交已脱敏的证据 ZIP 与对应哈希。
