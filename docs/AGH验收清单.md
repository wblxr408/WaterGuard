# AGH 真实闭环验收清单

本清单仅用于模型 Key 配置完成后的真实运行核验。Fixture、Mock、Benchmark 或人工补写 JSON 不能替代这里的原生会话证据。

## 当前已验证的前置条件

- 本机 AGH 守护进程可达，WaterGuard MCP 连接状态为 `ready`。
- MCP 目录已发现 11 项受限 `wg_*` 工具。
- 工具服务只允许明确的 Python 可执行文件和脚本参数；没有 shell 转发能力。
- 网关、独立仿真作业、验证器、事件/证据存储和约束测试均可在不调用模型时运行。

## 配置模型后必须实测的流程

1. 在 AGH Web 的模型设置中配置赛事提供的 Key；不要把 Key 写入仓库、命令行、截图、JSONL 或 evidence 文件。
2. 新建 live 会话，记录模型名称、版本、实际调用时间与 AGH 原生会话引用。
3. 由 Agnes 调用 `wg_inspect_network`，再提交冻结的 `TaskSpec`。保留原生工具调用记录。
4. 令 Agnes 提交空动作基线，依次调用 `wg_submit_plan`、`wg_simulate_candidate`、`wg_get_simulation` 与 `wg_verify_candidate`。
5. 选择一个被验证器拒绝的动作或约束违反案例。Agnes 必须读取结构化 `INVALID_ACTION` 或验证反馈，并自行提交下一版候选；不能由 API 后台替它选择修正方案。
6. 对通过名义验证的候选，使用 `wg_create_perturbation`、再次仿真和验证；再调用 `wg_compare_candidates` 与 `wg_build_evidence`。
7. 导出原生 AGH trace、工具调用关联表、TaskSpec、候选、原始时序、VerificationReport 和 EvidenceManifest。确认 `live_export_allowed=true` 前，不得称为正式 live 证据。

## 必测异常与恢复

- 提交不在动作目录内的设施：应返回 `INVALID_ACTION`，不会派发仿真。
- 用过期 `constraint_hash` 或任务版本提交计划：应返回版本不匹配。
- 消耗完仿真预算或并发槽：应返回 `BUDGET_EXHAUSTED`，不再启动子进程。
- 在计算期间取消：AGH 会话与工作进程都停止；迟到结果只能归档，不能覆盖 `CANCELLED`。
- 关闭/重启 AGH 后恢复：续接真实会话；若 AGH 不支持续接，建立新的关联会话并把连续性缺失写入 evidence。

## 验收证据

每次 live 运行目录至少包含：

```text
artifacts/{run_id}/
  agh/native/                 # AGH 原生导出
  agh/association.json         # 原生调用与应用事件映射
  agh/probe.json               # 版本、守护进程、MCP 工具目录快照（无密钥）
  events.jsonl
  candidates/
  simulations/
  verifications/
  evidence-manifest.json
```

证据包中缺少任一原生记录时，必须在 `missing_items` 中保留缺失原因，而不是补造记录。
