import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import type { Violation, RunEvent } from "@waterguard/contracts";
import type { Candidate, Workspace } from "./data";
import {
  actionText,
  conclusionText,
  download,
  eventText,
  gates,
  num,
  networkName,
  recommendationBlockerText,
  recommendedDispatch,
  runStatusText,
  statusText,
  time,
} from "./data";
import Icon from "./Icon";
import ModelDemo from "./ModelDemo";
import ServiceOutcome from "./ServiceOutcome";
import { modelAssets, studioImage } from "./model-assets";

export type Inspect = (title: string, data: unknown) => void;
export function Admission({
  candidate: c,
  compact = false,
}: {
  candidate: Candidate;
  compact?: boolean;
}) {
  return (
    <div className={`admission ${compact ? "compact" : ""}`}>
      {gates.map(([key, label], i) => {
        const value = c.verification.admission[key];
        // 五层按顺序依次亮起（--i 控制延迟），表达逐层验收。
        return (
          <div
            className={`gate ${value}`}
            key={key}
            style={{ "--i": i } as CSSProperties}
          >
            <span className="gate-symbol">
              {value === "pass" ? (
                <Icon name="check" size={13} />
              ) : value === "fail" ? (
                <Icon name="close" size={13} />
              ) : (
                <Icon name="clock" size={13} />
              )}
            </span>
            <span>{label}</span>
            <small>{statusText[value]}</small>
            {!compact && i < gates.length - 1 && (
              <span className="gate-connector" />
            )}
          </div>
        );
      })}
    </div>
  );
}
export function Violations({
  candidate: c,
  onSelect,
  limit = 8,
}: {
  candidate: Candidate;
  onSelect: (v: Violation) => void;
  limit?: number;
}) {
  const [filter, setFilter] = useState("all");
  const [page, setPage] = useState(0);
  useEffect(() => {
    setPage(0);
    setFilter("all");
  }, [c.id]);
  const rows = c.verification.violations.filter(
    (v) => filter === "all" || v.code === filter,
  );
  return (
    <section className="violations-panel" id="remaining-violations">
      <div className="panel-heading">
        <div>
          <h2>
            验收反馈{" "}
            <span className="count">{c.verification.violations.length}</span>
          </h2>
          <span>点击记录定位节点与时段</span>
        </div>
        <select
          aria-label="筛选违规类型"
          value={filter}
          onChange={(e) => {
            setFilter(e.target.value);
            setPage(0);
          }}
        >
          <option value="all">全部约束</option>
          <option value="MIN_PRESSURE">压力约束</option>
          <option value="MIN_SUPPLY_RATIO">供水约束</option>
        </select>
      </div>
      {!rows.length ? (
        <p className="empty-small">
          {c.verification.validity === "invalid"
            ? "仿真数据无效，请查看无效原因。"
            : "当前没有此类违规。"}
        </p>
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>节点</th>
                  <th>评估时段</th>
                  <th>未满足的约束</th>
                  <th>计算值</th>
                  <th>要求值</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.slice(page * limit, (page + 1) * limit).map((v, i) => (
                  <tr key={`${v.node_id}-${v.start_s}-${v.code}-${i}`}>
                    <td>
                      <button
                        className="entity-link"
                        onClick={() => onSelect(v)}
                      >
                        {v.node_id}
                      </button>
                    </td>
                    <td className="mono">
                      {time(v.start_s)} – {time(v.end_s)}
                    </td>
                    <td>
                      {v.code === "MIN_PRESSURE"
                        ? "最低节点压力"
                        : "最低供需比"}
                    </td>
                    <td className="negative mono">
                      {v.unit === "m"
                        ? `${num(v.observed)} m`
                        : `${num(v.observed * 100)}%`}
                    </td>
                    <td className="mono">
                      ≥{" "}
                      {v.unit === "m"
                        ? `${num(v.threshold, 0)} m`
                        : `${num(v.threshold * 100, 0)}%`}
                    </td>
                    <td>
                      <button
                        className="icon-button"
                        aria-label={`定位节点 ${v.node_id} ${time(v.start_s)}`}
                        onClick={() => onSelect(v)}
                      >
                        <Icon name="arrow" size={16} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pagination">
            <span>
              共 {rows.length} 条 · 第 {page + 1} /{" "}
              {Math.ceil(rows.length / limit)} 页
            </span>
            <div>
              <button
                disabled={page === 0}
                onClick={() => setPage((p) => p - 1)}
              >
                上一页
              </button>
              <button
                disabled={(page + 1) * limit >= rows.length}
                onClick={() => setPage((p) => p + 1)}
              >
                下一页
              </button>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
export function Compare({
  w,
  selected,
  onCandidate,
  onViolation,
  inspect,
}: {
  w: Workspace;
  selected: Candidate;
  onCandidate: (id: string) => void;
  onViolation: (v: Violation) => void;
  inspect: Inspect;
}) {
  const sameTask = w.candidates.every(
    (c) => c.task.constraint_hash === w.candidates[0].task.constraint_hash,
  );
  const dispatch = recommendedDispatch(w);
  return (
    <div className="page-enter">
      <div className="section-intro">
        <h1>候选方案对照</h1>
        <p>
          {sameTask ? '比较候选方案的压力、供需比与验收结果。' : '正常与故障工况对照。'}
        </p>
      </div>
      <ServiceOutcome candidate={selected} workspace={w} />
      <div className="comparison-layout">
        {w.candidates.map((c, i) => (
          <article
            key={c.id}
            className={`candidate-sheet ${selected.id === c.id ? "chosen" : ""}`}
          >
            <div className="candidate-sheet-top">
              <span className="candidate-letter">
                {String.fromCharCode(65 + i)}
              </span>
              <span
                className={`badge ${dispatch?.candidate.id === c.id ? (dispatch.tier === 'fully_compliant' ? 'pass' : 'pending') : c.verification.admission.recommendation_eligible}`}
              >
                {dispatch?.candidate.id === c.id
                  ? dispatch.tier === 'fully_compliant' ? '完全达标推荐' : '应急处置推荐'
                  : c.verification.admission.recommendation_eligible === "pass"
                  ? "完整准入通过"
                  : c.verification.admission.recommendation_eligible ===
                      "pending"
                    ? "待验证"
                    : "未完全达标"}
              </span>
            </div>
            <h2>{c.name}</h2>
            <p className="reason">{c.plan.actions.length ? c.plan.actions.map((action) => `${actionText[action.kind]} ${action.link_id}`).join('；') : '保持设施状态'}</p>
            <div className="comparison-metrics">
              <div>
                <span>缺供体积</span>
                <strong>
                  {num(c.verification.metrics.shortage_m3)}
                  <small>m³</small>
                </strong>
              </div>
              <div>
                <span>最低节点压力</span>
                <strong
                  className={
                    c.verification.admission.nominal_constraints_pass === "fail"
                      ? "negative"
                      : ""
                  }
                >
                  {num(c.verification.metrics.min_pressure_m)}
                  <small>m</small>
                </strong>
              </div>
              <div>
                <span>逐节点平均正常供水时间</span>
                <strong>
                  {c.verification.metrics.mean_node_time_pass_ratio == null
                    ? "—"
                    : num(c.verification.metrics.mean_node_time_pass_ratio * 100)}
                  <small>%</small>
                </strong>
              </div>
            </div>
            <p>最差节点正常供水时间：{c.verification.metrics.min_node_time_pass_ratio == null ? '—' : `${num(c.verification.metrics.min_node_time_pass_ratio * 100)}%`}</p>
            <p>全程完全达标节点：{c.verification.metrics.fully_compliant_node_count ?? '—'} / {c.verification.metrics.assessed_node_count ?? '—'}</p>
            {dispatch?.candidate.id === c.id && dispatch.tier === 'best_effort' && <p>推荐用于应急尽力挽救 · 已测方案中优先保障基本供水，仍未完全达标，详见下方准入与违规。</p>}
            {c.task.emergency_floor && <p>应急降级服务（≥{c.task.emergency_floor.min_pressure_m} m／≥{num(c.task.emergency_floor.min_supply_ratio * 100)}%）：全程覆盖 {c.verification.metrics.nodes_reaching_emergency_service ?? '—'} 个节点；平均 {c.verification.metrics.emergency_mean_node_time_pass_ratio == null ? '—' : `${num(c.verification.metrics.emergency_mean_node_time_pass_ratio * 100)}%`}，最差 {c.verification.metrics.emergency_min_node_time_pass_ratio == null ? '—' : `${num(c.verification.metrics.emergency_min_node_time_pass_ratio * 100)}%`}。</p>}
            {w.mitigation?.candidate_id === c.id && <details><summary>相对无动作基线的逐节点改善{w.mitigation.any_node_regressed ? '（含回退节点）' : ''}</summary><table><thead><tr><th>节点</th><th>基线正常秒数</th><th>挽救正常秒数</th><th>变化（秒）</th><th>降级覆盖变化（秒）</th></tr></thead><tbody>{w.mitigation.per_node.map(row => <tr key={row.node_id}><td>{row.node_id}</td><td>{num(row.baseline_normal_s, 0)}</td><td>{num(row.selected_normal_s, 0)}</td><td>{num(row.delta_s, 0)}</td><td>{row.emergency_delta_s == null ? '未配置' : num(row.emergency_delta_s, 0)}</td></tr>)}</tbody></table></details>}
            {c.verification.metrics.node_availability && <details><summary>各节点正常供水时长与占比</summary><table><thead><tr><th>节点</th><th>正常供水 / 评估时长（秒）</th><th>占比</th></tr></thead><tbody>{c.verification.metrics.node_availability.map(row => <tr key={row.node_id}><td>{row.node_id}</td><td>{num(row.normal_supply_duration_s, 0)} / {num(row.assessed_duration_s, 0)}</td><td>{row.normal_supply_time_ratio == null ? '—' : `${num(row.normal_supply_time_ratio * 100)}%`}</td></tr>)}</tbody></table></details>}
            <div className="action-list">
              <h4>处置动作</h4>
              {c.plan.actions.length ? (
                c.plan.actions.map((a, j) => (
                  <div key={j}>
                    <Icon name="pulse" size={16} />
                    <span>
                      {actionText[a.kind]} <b>{a.link_id}</b>
                    </span>
                    <time>{time(a.simulation_time_s)}</time>
                  </div>
                ))
              ) : (
                <div>
                  <Icon name="minus" size={16} />
                  <span>无处置动作，保留故障基线</span>
                </div>
              )}
            </div>
            <Admission candidate={c} compact />
            <div className="candidate-sheet-bottom">
              <button
                className="text-button"
                onClick={() => inspect(`${c.name} · 原始验收`, c.verification)}
              >
                查看验收报告
                <Icon name="arrow" size={16} />
              </button>
              <button
                className="button secondary"
                onClick={() => onCandidate(c.id)}
              >
                {selected.id === c.id ? "当前方案" : "查看此方案"}
              </button>
            </div>
          </article>
        ))}
      </div>
      <div className="feedback-banner">
        <Icon name="shield" size={25} />
        <div>
          <strong>
            {dispatch?.candidate.id === selected.id && dispatch.tier === 'best_effort'
              ? '应急处置推荐 · 完全达标准入未通过'
              : selected.verification.admission.recommendation_eligible === "pass"
                ? "完全达标推荐准入通过" : "完全达标推荐准入未通过"}
          </strong>
          <p>
            {selected.verification.recommendation_blockers.length
              ? selected.verification.recommendation_blockers.map((reason) => recommendationBlockerText[reason] ?? reason).join(" · ")
              : "请查看五层准入与原始验收报告。"}
          </p>
          {selected.verification.verifier_version.startsWith('waterguard-dense-verifier/') && (
            <p>验收指标与违规区间按全部内部水力时刻计算；图中曲线采用 {num(selected.task.time_window.step_s / 60)} 分钟公共采样。供水量不含漏损，压力复验必须沿用同一动作方案。</p>
          )}
        </div>
        <button
          className="text-button"
          onClick={() => inspect("完整验收结果", selected.verification)}
        >
          原始结果
          <Icon name="arrow" size={16} />
        </button>
      </div>
      <Violations candidate={selected} onSelect={onViolation} limit={10} />
    </div>
  );
}

export function Modeling({
  w,
  c,
  inspect,
}: {
  w: Workspace;
  c: Candidate;
  inspect: Inspect;
}) {
  const physics = c.physics,
    options = c.task.hydraulic_options;
  return (
    <div className="page-enter">
      <div className="section-intro model-intro">
        <img className="studio-scene" src={studioImage.src} alt={studioImage.alt} />
        <span className="studio-scene-caption">{studioImage.caption}</span>
        <div>
          <h1>
            设备与管道
            <br />
            模型展台
          </h1>
          <p>
            储水罐、水泵、闸阀、清水池与爆管管段
            <br />
            均为团队自建的 Blender 模型
          </p>
        </div>
      </div>
      <div className="model-grid">
        {modelAssets.map((asset) => (
          <ModelDemo key={asset.id} asset={asset} />
        ))}
      </div>
      <div className="model-disclaimer">
        <Icon name="cube" size={17} />
        <p>
          三维管网与设备模型，可下载 GLB 文件。
        </p>
      </div>
      <section className="model-context">
        <div>
          <h2>模型背后的物理依据</h2>
          <p>
            公开管网、明确假设、独立物理检查。每次计算保留原始输入与实体映射。
          </p>
          <div className="context-tags">
            <span>{networkName(w.scene.network_id)}</span>
            <span>{c.result ? "WNTR / EPANET 水力模型" : "人工开发样例"}</span>
            <span>
              {w.manifest.source_kind === "public"
                ? w.scene.network_id === "china-zhijiang" ? "中国城市研究模型 · 未现场校准" : "公开示例 · 未现场校准"
                : "合成网络"}
            </span>
          </div>
          {w.scene.network_id === "china-zhijiang" && <p>基于实际中国供水系统的历史研究模型。画面使用原始模型布局，压力与流量来自当前仿真。</p>}
          <button
            className="text-button"
            onClick={() => inspect("管网来源与许可", w.manifest)}
          >
            查看来源与许可
            <Icon name="arrow" size={16} />
          </button>
        </div>
        <div className="foundation-mark" aria-hidden="true">
          <Icon name="network" size={72} />
          <span>模型 → 物理检查 → 证据</span>
        </div>
      </section>
      <div className="model-data-grid">
        <section className="data-section">
          <div className="panel-heading">
            <h2>计算参数</h2>
            <span>冻结任务输入</span>
          </div>
          <dl className="detail-list">
            <div>
              <dt>用水节点</dt>
              <dd>
                {w.scene.nodes.filter((n) => n.kind === "junction").length}
              </dd>
            </div>
            <div>
              <dt>管网连接</dt>
              <dd>{w.scene.links.length}</dd>
            </div>
            <div>
              <dt>压力要求</dt>
              <dd>≥ {num(c.task.hard_constraints.min_pressure_m, 2)} m</dd>
            </div>
            <div>
              <dt>输出步长</dt>
              <dd>{c.task.time_window.step_s} s</dd>
            </div>
            <div>
              <dt>水力步长</dt>
              <dd>
                {options ? `${options.hydraulic_timestep_s} s` : "未提供"}
              </dd>
            </div>
            <div>
              <dt>需水倍数</dt>
              <dd>{options ? num(options.demand_multiplier, 1) : "未提供"}</dd>
            </div>
            <div>
              <dt>引擎版本</dt>
              <dd className="engine-version">
                {c.result?.artifact.engine_version ?? "无真实引擎执行"}
              </dd>
            </div>
          </dl>
          <button
            className="text-button"
            onClick={() => inspect("冻结任务与建模假设", c.task)}
          >
            完整任务参数
            <Icon name="arrow" size={15} />
          </button>
        </section>
        <section className="data-section">
          <div className="panel-heading">
            <h2>物理一致性检查</h2>
            <span
              className={`badge ${physics?.passed === true ? "pass" : physics?.passed === false ? "fail" : "pending"}`}
            >
              {physics?.passed === true
                ? "检查通过"
                : physics?.passed === false
                  ? "检查未通过"
                  : "未提供"}
            </span>
          </div>
          <dl className="detail-list">
            <div>
              <dt>最大节点守恒残差</dt>
              <dd>
                {physics?.mass_balance_max_abs_m3s?.toExponential(3) ?? "—"}{" "}
                <small>m³/s</small>
              </dd>
            </div>
            <div>
              <dt>守恒容差</dt>
              <dd>
                {physics?.mass_balance_tolerance_m3s?.toExponential(1) ?? "—"}{" "}
                <small>m³/s</small>
              </dd>
            </div>
            <div>
              <dt>孔口定律相对误差</dt>
              <dd>
                {physics?.leak_law_max_relative_error?.toExponential(3) ?? "—"}
              </dd>
            </div>
            <div>
              <dt>泵曲线拟合误差</dt>
              <dd>
                {physics?.pump_curve_max_fit_error_m?.toExponential(3) ?? "—"}{" "}
                <small>m</small>
              </dd>
            </div>
          </dl>
          <button
            className="text-button"
            disabled={!physics}
            onClick={() => inspect("原始物理检查", physics)}
          >
            原始物理报告
            <Icon name="arrow" size={15} />
          </button>
        </section>
      </div>
      <section className="data-section mapping-section">
        <div className="panel-heading">
          <div>
            <h2>故障模型与实体映射</h2>
            <span>
              当前任务：
              {c.task.fault
                ? `${c.task.fault.kind} · 管段 ${c.task.fault.link_id}`
                : "正常工况"}
            </span>
          </div>
          <button
            className="button secondary"
            disabled={!c.mapping}
            onClick={() => inspect("真实实体映射", c.mapping)}
          >
            查看实体映射
            <Icon name="arrow" size={16} />
          </button>
        </div>
        <div className="fault-definitions">
          {(
            [
              ["closure", "管段关闭", "关闭既有管段，无泄漏出流。", null],
              [
                "leak",
                "孔口泄漏",
                "拆分管段，按孔口定律计算出流。",
                "Q = Cd · A · √(2gh)",
              ],
              [
                "rupture",
                "双端断裂",
                "两个断口分别向大气出流，断口间无流通。",
                null,
              ],
              [
                "isolation",
                "区域隔离",
                "关闭精确边界，保留区域需水计入缺供。",
                null,
              ],
            ] as const
          ).map(([kind, title, text, formula]) => (
            <div
              key={kind}
              className={c.task.fault?.kind === kind ? "current" : ""}
            >
              <span>
                {kind}
                {c.task.fault?.kind === kind ? " · 当前任务" : ""}
              </span>
              <h3>{title}</h3>
              <p>{text}</p>
              {formula && <code>{formula}</code>}
            </div>
          ))}
        </div>
        <p className="subtle-note">
          延时水力模型与故障工况。
        </p>
      </section>
    </div>
  );
}

const available: Record<string, string> = {
  "task.json": "task",
  "plan.json": "plan",
  "result.json": "",
  "series.json": "series",
  "verification.json": "verification",
  "physical-checks.json": "physics",
  "entity-map.json": "mapping",
  "evidence-manifest.json": "evidence",
};
export function Evidence({
  w,
  c,
  inspect,
}: {
  w: Workspace;
  c: Candidate;
  inspect: Inspect;
}) {
  const [query, setQuery] = useState("");
  const dataFor = (ref: string): unknown =>
    (
      ({
        "task.json": c.task,
        "plan.json": c.plan,
        "result.json": c.result,
        "series.json": c.series,
        "verification.json": c.verification,
        "physical-checks.json": c.physics,
        "entity-map.json": c.mapping,
        "evidence-manifest.json": c.evidence,
      }) as Record<string, unknown>
    )[ref];
  const rows = c.evidence.artifacts.filter((a) =>
    a.ref.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <div className="page-enter">
      <div className="section-intro">
        <h1>计算证据中心</h1>
        <p>冻结输入、原始时序与独立验收，绑定同一运行记录。</p>
      </div>
      <div className="evidence-summary">
        <div>
          <Icon name="file" size={30} />
          <div>
            <h2>计算证据索引</h2>
            <p className="mono">{c.evidence.bundle_id}</p>
          </div>
        </div>
        <div style={{ gap: 12, flexWrap: 'wrap' }}>
          <button
            className="button secondary"
            onClick={() =>
              download(`${c.evidence.bundle_id}-manifest.json`, c.evidence)
            }
          >
            <Icon name="download" size={17} />
            下载证据索引
          </button>
          {w.source === 'runtime' && w.run?.run_id === c.evidence.run_id && c.evidence.live_export_allowed && (
            <a className="button primary" download
              href={`${w.base}/api/runs/${encodeURIComponent(c.evidence.run_id)}/evidence/download`}>
              <Icon name="download" size={17} />
              下载完整证据包
            </a>
          )}
        </div>
      </div>
      <div className="evidence-info">
        <span>
          <b>{c.evidence.artifacts.length}</b> 个证据文件
        </span>
        <span>
          <b>{w.mode.toUpperCase()}</b> 数据模式
        </span>
        <span>
          <b>SHA-256</b> 服务端文件哈希
        </span>
        <span className="badge pending">
          AGH 原生日志{c.evidence.native_trace_ref ? "已提供引用" : "未提供"}
        </span>
      </div>
      <div className="notice">
        <Icon name="help" size={18} />
        <p>
          可读取的结果支持导出 JSON 副本；文件哈希对应原始产物。
        </p>
      </div>
      <div className="evidence-toolbar">
        <h2>证据文件</h2>
        <label className="search-field">
          <Icon name="search" size={17} />
          <input
            placeholder="搜索文件名称…"
            aria-label="搜索证据文件"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <button
          className="button secondary"
          onClick={() => inspect("完整证据清单", c.evidence)}
        >
          查看完整清单
        </button>
      </div>
      <div className="table-wrap evidence-table">
        <table>
          <thead>
            <tr>
              <th>文件</th>
              <th>格式</th>
              <th>SHA-256</th>
              <th>读取状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => {
              const readable =
                w.source === "fixture" ||
                (a.ref in available && dataFor(a.ref) != null);
              return (
                <tr key={a.ref}>
                  <td>
                    <Icon name="file" size={17} />
                    <span>{a.ref}</span>
                  </td>
                  <td>
                    <span className="file-type">{a.kind.toUpperCase()}</span>
                  </td>
                  <td>
                    <button
                      className="hash-button mono"
                      title={a.sha256}
                      onClick={() =>
                        inspect(`${a.ref} · 服务端原始字节哈希`, {
                          ref: a.ref,
                          sha256: a.sha256,
                        })
                      }
                    >
                      {a.sha256.slice(0, 19)}…{a.sha256.slice(-8)}
                    </button>
                  </td>
                  <td>
                    <span className={readable ? "positive" : "muted"}>
                      {readable ? "可读取" : "仅索引"}
                    </span>
                  </td>
                  <td>
                    {w.source === "fixture" ? (
                      <a
                        className="text-button"
                        href={`${w.base}/api/artifacts/${a.ref.split("/").map(encodeURIComponent).join("/")}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        查看原始文件
                        <Icon name="arrow" size={14} />
                      </a>
                    ) : readable ? (
                      <button
                        className="text-button"
                        onClick={() => inspect(a.ref, dataFor(a.ref))}
                      >
                        查看结果
                        <Icon name="arrow" size={14} />
                      </button>
                    ) : (
                      <button
                        className="icon-button"
                        aria-label={`查看 ${a.ref} 索引`}
                        onClick={() => inspect("文件索引", a)}
                      >
                        <Icon name="file" size={16} />
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!rows.length && <p className="empty-small">没有匹配的证据文件。</p>}
      </div>
    </div>
  );
}

const originText: Record<string, string> = {
  agh: "AGH",
  gateway: "工具网关",
  worker: "计算进程",
  verifier: "验收程序",
  user: "用户",
};
export function FixtureEvents({
  w,
  inspect,
}: {
  w: Workspace;
  inspect: Inspect;
}) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [ended, setEnded] = useState(false);
  const [error, setError] = useState(false);
  useEffect(() => {
    if (w.source !== "fixture") return;
    setEvents([]);
    setEnded(false);
    setError(false);
    const es = new EventSource(`${w.base}/api/runs/run-day1-fixture/events`);
    const handler = (e: MessageEvent) => {
      try {
        const event = JSON.parse(e.data) as RunEvent;
        if (event.mode !== w.mode) {
          setError(true);
          es.close();
          return;
        }
        setEvents((rows) =>
          rows.some((r) => r.sequence === event.sequence)
            ? rows
            : [...rows, event].sort((a, b) => a.sequence - b.sequence),
        );
        if (event.type === "run.completed") {
          setEnded(true);
          es.close();
        }
      } catch {
        setError(true);
        es.close();
      }
    };
    for (const name of [
      "run.started",
      "candidate.submitted",
      "simulation.completed",
      "verification.completed",
      "run.completed",
    ])
      es.addEventListener(name, handler as EventListener);
    es.onerror = () => {
      setError(true);
      es.close();
    };
    return () => es.close();
  }, [w]);
  if (w.source !== "fixture") return null;
  return (
    <section className="fixture-events">
      <div className="panel-heading">
        <div>
          <h2>样例事件回放</h2>
          <span>样例事件回放</span>
        </div>
        <span className="badge neutral">
          {error ? "读取异常" : ended ? "回放完成" : "读取中"}
        </span>
      </div>
      {w.run && (
        // 契约要求：COMPLETED 只表示流程结束，必须同时显示结论（样例为 INCOMPLETE，未完成工程推荐）。
        <p className="run-summary">
          <span>
            运行记录 <b>{runStatusText[w.run.status] ?? w.run.status}</b>
          </span>
          <span>
            结论 <b>{conclusionText[w.run.conclusion] ?? w.run.conclusion}</b>
          </span>
          <span>
            {w.run.selected_candidate_id
              ? `选定 ${w.run.selected_candidate_id}`
              : w.run.mitigation_candidate_id ? `应急处置推荐 ${w.run.mitigation_candidate_id}（尽力挽救，未完全达标）`
              : "未选定推荐方案"}
          </span>
        </p>
      )}
      <div className="event-list">
        {events.map((e, i) => (
          <button
            key={e.sequence}
            style={{ "--i": i } as CSSProperties}
            onClick={() => inspect(`事件 ${e.sequence}`, e)}
          >
            <span className="event-sequence">{e.sequence}</span>
            <div>
              <strong>{eventText[e.type] ?? e.type}</strong>
              <small>
                {e.type} · {e.candidate_id ?? e.run_id}
              </small>
            </div>
            <span>{originText[e.origin] ?? e.origin}</span>
            <Icon name="chevron" size={15} />
          </button>
        ))}
      </div>
    </section>
  );
}
