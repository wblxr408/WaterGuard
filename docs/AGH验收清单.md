# AGH 真实闭环验收清单

本清单仅用于模型 Key 配置完成后的真实运行核验。Fixture、Mock、Benchmark 或人工补写 JSON 不能替代这里的原生会话证据。

## 已验证的前置条件

- 本机 AGH 守护进程可达，WaterGuard MCP 连接状态为 `ready`。
- MCP 目录已发现 12 项受限 `wg_*` 工具。
- 工具服务只允许明确的 Python 可执行文件和脚本参数；没有 shell 转发能力。
- 网关、独立仿真作业、验证器、事件/证据存储和约束测试均可在不调用模型时运行。

2026-10-06 已使用官方 SDK、保存的赛事 Agnes 账号与 `agnes-3.0-flash` 完成 Live 记录。正常闭环为 `run-02486d5ab8ac4de6923797c578f07c4d`；结构化失败—修正闭环为 `run-a7edfb58a2d64781aa48b84527514139`；取消记录为 `run-a4f363c241b04d6ca11f59695b80068c`；服务重启后的后继记录为 `run-60cae9bde25e49b7bdceabe26c290cd6`。这些本地产物默认被 Git 忽略，应按操作手册重新生成并导出 ZIP 后提交比赛材料。

## 已实现的实测流程

1. 在 AGH Web 的模型设置中配置赛事提供的 Key；不要把 Key 写入仓库、命令行、截图、JSONL 或 evidence 文件。
2. 新建 live 会话，记录模型名称、版本、实际调用时间与 AGH 原生会话引用。
3. 由 Agnes 调用 `wg_inspect_network`，再提交冻结的 `TaskSpec`。保留原生工具调用记录。
4. 令 Agnes 提交空动作基线，依次调用 `wg_submit_plan`、`wg_simulate_candidate`、`wg_get_simulation` 与 `wg_verify_candidate`。
5. 使用 `acceptance_scenario=repair_demo` 选择受控非法候选。服务端返回结构化 `INVALID_ACTION` 且不创建仿真；Agnes 必须读取该反馈、调用候选生成工具并自行提交下一版合法候选。
6. 对通过名义验证的候选，使用 `wg_create_perturbation`、再次仿真和验证；再调用 `wg_compare_candidates` 与 `wg_build_evidence`。
7. 导出原生 AGH trace、工具调用关联表、TaskSpec、候选、原始时序、VerificationReport 和 EvidenceManifest。确认 `live_export_allowed=true` 前，不得称为正式 live 证据。

## 必测异常与恢复

- 提交不在动作目录内的设施：应返回 `INVALID_ACTION`，不会派发仿真。
- 用过期 `constraint_hash` 或任务版本提交计划：应返回版本不匹配。
- 消耗完仿真预算或并发槽：应返回 `BUDGET_EXHAUSTED`，不再启动子进程。
- 在计算期间取消：AGH 会话与工作进程都停止；迟到结果只能归档，不能覆盖 `CANCELLED`。
- 关闭/重启服务后恢复：旧运行保留 `SERVER_RESTARTED` 与取消结果；`POST /api/runs/{id}/recover` 建立 `resumed_from_run_id` 后继会话并记录 `fresh_agh_session` 边界，不伪装为原会话续接。

## 验收证据

每次 live 运行目录至少包含：

```text
artifacts/{run_id}/
  agh/native/                 # AGH 原生导出
  agh/association.json         # 原生调用与应用事件映射
  agh/probe.json               # 版本、守护进程、MCP 工具目录快照（无密钥）
  continuity.json              # 仅后继运行：前驱、回答/原因与新会话边界
  events.jsonl
  candidates/
  simulations/
  verifications/
  evidence-manifest.json
```

证据包中缺少任一原生记录时，必须在 `missing_items` 中保留缺失原因，而不是补造记录。
