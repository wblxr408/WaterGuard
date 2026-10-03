import { useId, useMemo, useState } from "react";
import type { LinkSample } from "@waterguard/contracts";
import type { Candidate, Workspace } from "./data";
import { num, time } from "./data";
import Icon from "./Icon";

export type Entity = { kind: "node" | "link"; id: string };
export type Layer = "pressure" | "supply" | "flow";
type Level = "ok" | "low" | "bad" | "na";
export type LinkState = "open" | "closed" | "leaking" | "ruptured" | "isolated";

/** 管段在当前帧的物理状态：优先读 0.3.0 契约的 physical_state，没有时退回 status。 */
export function linkState(
  s: LinkSample | undefined,
  initial: "open" | "closed",
): LinkState {
  if (!s) return initial;
  const p = s.physical_state;
  if (p === "leaking" || p === "ruptured" || p === "closed" || p === "isolated")
    return p;
  if (s.status === "broken") return "ruptured";
  return s.status === "closed" ? "closed" : "open";
}
export const linkStateText: Record<LinkState, string> = {
  open: "开启",
  closed: "关闭",
  leaking: "泄漏中",
  ruptured: "断裂",
  isolated: "已隔离",
};
const dashFor: Partial<Record<LinkState, string>> = {
  closed: "6 5",
  isolated: "2 5",
  ruptured: "10 4",
};
/** 估算 SVG 标签宽度（中文约 12.5，西文约 7 个单位），让底框贴合文字。 */
const textWidth = (s: string) =>
  [...s].reduce((n, ch) => n + (ch.charCodeAt(0) > 255 ? 12.5 : 7), 0);

export function Network({
  workspace: w,
  candidate: c,
  frameIndex,
  entity,
  onSelect,
}: {
  workspace: Workspace;
  candidate: Candidate;
  frameIndex: number;
  entity: Entity;
  onSelect: (s: Entity) => void;
}) {
  const patternId = useId().replaceAll(":", "");
  const [layer, setLayer] = useState<Layer>("pressure");
  const [zoom, setZoom] = useState(1);
  const [labels, setLabels] = useState(false);
  const frame = c.series?.frames[frameIndex];
  const samples = new Map(frame?.nodes.map((n) => [n.node_id, n]));
  const links = new Map(frame?.links.map((n) => [n.link_id, n]));
  const tanks = new Map(frame?.tanks?.map((t) => [t.node_id, t]));
  const positions = useMemo(() => {
    const nodes = w.scene.nodes,
      xs = nodes.map((n) => n.x_m),
      ys = nodes.map((n) => n.y_m);
    const xmin = Math.min(...xs),
      ymin = Math.min(...ys),
      xmax = Math.max(...xs),
      ymax = Math.max(...ys);
    // 绘图区留出上方说明（约 40）和下方图例（约 80）的位置，扁平的小管网也不会压住图例。
    const scale = Math.min(700 / (xmax - xmin || 1), 330 / (ymax - ymin || 1));
    const ox = (860 - (xmax - xmin) * scale) / 2,
      top = 40 + (330 - (ymax - ymin) * scale) / 2;
    return new Map(
      nodes.map((n) => [
        n.id,
        {
          x: ox + (n.x_m - xmin) * scale,
          y: top + (ymax - n.y_m) * scale,
        },
      ]),
    );
  }, [w.scene]);
  const valid =
    c.verification.validity === "valid" &&
    c.verification.admission.simulation_valid === "pass";
  const fault = c.task.fault;
  const faultActive =
    !!fault &&
    !!frame &&
    frame.time_s >= fault.start_s &&
    frame.time_s < fault.end_s;
  // 当前帧落在违规时段 [start, end) 内的节点：由验收报告给出，不由前端判定。
  const violating = new Set(
    frame
      ? c.verification.violations
          .filter((v) => frame.time_s >= v.start_s && frame.time_s < v.end_s)
          .map((v) => v.node_id)
      : [],
  );
  const maxFlow = Math.max(
    1e-9,
    ...[...links.values()].map((l) => Math.abs(l.flow_m3s)),
  );
  const along = (linkId: string, fraction = 0.5) => {
    const l = w.scene.links.find((x) => x.id === linkId);
    const a = l && positions.get(l.from_node),
      b = l && positions.get(l.to_node);
    return a && b
      ? { x: a.x + (b.x - a.x) * fraction, y: a.y + (b.y - a.y) * fraction }
      : null;
  };
  const faultAt = fault
    ? along(fault.link_id, fault.location_fraction ?? 0.5)
    : null;
  const leaks = valid ? (frame?.leaks ?? []) : [];
  const leakTotal = leaks.reduce((n, l) => n + l.leak_flow_m3s, 0);
  function level(id: string): Level {
    const s = samples.get(id);
    if (!s || !valid) return "na";
    if (layer === "supply")
      return s.expected_demand_m3s <= 0
        ? "na"
        : s.delivered_demand_m3s / s.expected_demand_m3s >=
            c.task.hard_constraints.min_supply_ratio
          ? "ok"
          : "low";
    return s.pressure_m < 0
      ? "bad"
      : s.pressure_m < c.task.hard_constraints.min_pressure_m
        ? "low"
        : "ok";
  }
  return (
    <section className="network-panel">
      <div className="panel-heading">
        <div>
          <h2>供水管网</h2>
          <span>
            {w.scene.network_id.toUpperCase()} <i /> 示意拓扑
          </span>
        </div>
        <div className="segmented">
          {(
            [
              ["pressure", "节点压力"],
              ["supply", "供需比"],
              ["flow", "管段流量"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setLayer(key)}
              aria-pressed={layer === key}
              className={layer === key ? "active" : ""}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="network-canvas">
        <div className="map-caption">
          <span className="tiny-dot" />
          {valid ? "计算结果已载入" : "无有效仿真结果"}
          <span className="mono">{frame ? time(frame.time_s) : "—"}</span>
        </div>
        <div className="map-tools">
          <button
            aria-label="放大管网"
            onClick={() => setZoom((z) => Math.min(z + 0.2, 2))}
          >
            <Icon name="plus" size={17} />
          </button>
          <button
            aria-label="缩小管网"
            onClick={() => setZoom((z) => Math.max(z - 0.2, 0.6))}
          >
            <Icon name="minus" size={17} />
          </button>
          <button aria-label="重置视图" onClick={() => setZoom(1)}>
            <Icon name="target" size={17} />
          </button>
          <button
            aria-label="切换实体标签"
            aria-pressed={labels}
            className={labels ? "active" : ""}
            onClick={() => setLabels(!labels)}
          >
            <Icon name="layers" size={17} />
          </button>
        </div>
        <svg
          viewBox="0 0 860 450"
          className="network-svg"
          role="img"
          aria-label="接口驱动的供水管网拓扑，点击节点或管段查看详情"
        >
          <defs>
            <pattern
              id={patternId}
              width="28"
              height="28"
              patternUnits="userSpaceOnUse"
            >
              <path
                d="M28 0H0V28"
                fill="none"
                stroke="#a7b8d4"
                strokeWidth=".55"
                opacity=".3"
              />
              <circle cx="1" cy="1" r=".65" fill="#c4cfe2" opacity=".5" />
            </pattern>
          </defs>
          <rect width="860" height="450" fill={`url(#${patternId})`} />
          <g
            className="map-zoom"
            style={{
              transform: `translate(430px, 225px) scale(${zoom}) translate(-430px, -225px)`,
            }}
          >
            {w.scene.links.map((link) => {
              const a = positions.get(link.from_node)!,
                b = positions.get(link.to_node)!,
                s = links.get(link.id);
              const state = valid ? linkState(s, link.initial_status) : null;
              const isFault = faultActive && link.id === fault?.link_id,
                selected = entity.kind === "link" && entity.id === link.id;
              const flow = Math.abs(s?.flow_m3s ?? 0);
              const width =
                layer === "flow" && s && valid
                  ? 1.6 + (5 * flow) / maxFlow
                  : link.kind === "pump"
                    ? 3.2
                    : 2.5;
              // 流向光带只是示意：方向取计算流量的符号（正值为起点→终点），速度按相对流量，零流量不动。
              const flowing =
                layer === "flow" &&
                !!s &&
                valid &&
                (state === "open" || state === "leaking") &&
                flow > 1e-5;
              const [ux, uy, vx, vy] =
                (s?.flow_m3s ?? 0) >= 0
                  ? [a.x, a.y, b.x, b.y]
                  : [b.x, b.y, a.x, a.y];
              return (
                <g
                  key={link.id}
                  role="button"
                  tabIndex={0}
                  aria-label={`${link.kind === "pump" ? "水泵" : "管段"} ${link.id}${state ? `，${linkStateText[state]}` : ""}`}
                  onClick={() => onSelect({ kind: "link", id: link.id })}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect({ kind: "link", id: link.id });
                    }
                  }}
                  className={`map-entity link st-${state ?? "na"} kind-${link.kind}${isFault ? " is-fault" : ""}${selected ? " is-selected" : ""}`}
                >
                  <line
                    x1={a.x}
                    y1={a.y}
                    x2={b.x}
                    y2={b.y}
                    stroke="transparent"
                    strokeWidth="14"
                  />
                  {(selected || isFault) && (
                    <line
                      className="link-halo"
                      x1={a.x}
                      y1={a.y}
                      x2={b.x}
                      y2={b.y}
                      strokeWidth={isFault ? 12 : 10}
                    />
                  )}
                  <line
                    className="pipe"
                    x1={a.x}
                    y1={a.y}
                    x2={b.x}
                    y2={b.y}
                    strokeWidth={isFault ? 4 : width}
                    strokeDasharray={state ? dashFor[state] : undefined}
                  />
                  {flowing && (
                    <line
                      className="flow-dash"
                      x1={ux}
                      y1={uy}
                      x2={vx}
                      y2={vy}
                      strokeWidth={Math.max(1.4, width * 0.55)}
                      style={{
                        animationDuration: `${(2.6 - 1.9 * Math.sqrt(flow / maxFlow)).toFixed(2)}s`,
                      }}
                    />
                  )}
                  {link.kind === "pump" && (
                    <g
                      className="pump-mark"
                      transform={`translate(${(a.x + b.x) / 2} ${(a.y + b.y) / 2})`}
                    >
                      <rect x="-9" y="-9" width="18" height="18" rx="5" />
                      <path d="M-3-5L5 0-3 5Z" />
                    </g>
                  )}
                  {labels && (
                    <text
                      className="map-label minor"
                      x={(a.x + b.x) / 2 + 3}
                      y={(a.y + b.y) / 2 - 6}
                    >
                      {link.id}
                    </text>
                  )}
                  <title>
                    {link.id} ·{" "}
                    {s && state
                      ? `${linkStateText[state]} · ${num(s.flow_m3s, 5)} m³/s`
                      : "无时序"}
                  </title>
                </g>
              );
            })}
            {w.scene.nodes.map((node) => {
              const p = positions.get(node.id)!,
                selected = entity.kind === "node" && entity.id === node.id,
                s = samples.get(node.id),
                tank = tanks.get(node.id);
              const lv = node.kind === "junction" ? level(node.id) : "na";
              // 状态同时用颜色和大小区分：偏低更大，负压最大并带外圈。
              const r = selected
                ? 7
                : lv === "bad"
                  ? 6.4
                  : lv === "low"
                    ? 5.4
                    : node.critical
                      ? 5
                      : 4.3;
              return (
                <g
                  key={node.id}
                  role="button"
                  tabIndex={0}
                  aria-label={`${node.kind === "junction" ? "节点" : node.kind === "tank" ? "水箱" : "水源"} ${node.id}`}
                  className={`map-entity node${selected ? " is-selected" : ""}`}
                  onClick={() => onSelect({ kind: "node", id: node.id })}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect({ kind: "node", id: node.id });
                    }
                  }}
                >
                  <circle cx={p.x} cy={p.y} r="11" fill="transparent" />
                  {selected && (
                    <circle
                      key={`ping-${node.id}`}
                      className="sel-ping"
                      cx={p.x}
                      cy={p.y}
                      r="14"
                    />
                  )}
                  {node.kind === "junction" ? (
                    <>
                      {violating.has(node.id) && (
                        <circle
                          className="viol-ring"
                          cx={p.x}
                          cy={p.y}
                          r={r + 3.6}
                        />
                      )}
                      <circle
                        className={`node-dot lv-${lv}`}
                        cx={p.x}
                        cy={p.y}
                        r={r}
                        strokeWidth={selected ? 2.5 : 1.5}
                      />
                    </>
                  ) : (
                    <g className={`facility ${node.kind}`}>
                      <rect
                        x={p.x - 10}
                        y={p.y - 10}
                        width="20"
                        height="20"
                        rx={node.kind === "tank" ? 4 : 10}
                      />
                      <path
                        d={`M${p.x - 5} ${p.y}h10M${p.x - 4} ${p.y + 4}h8`}
                      />
                      <text className="map-label" x={p.x + 16} y={p.y + 4}>
                        {node.kind === "tank" ? `水箱 ${node.id}` : node.id}
                        {tank && (
                          <tspan className="map-label-value">
                            {" · "}
                            {num(tank.level_m, 2)} m
                          </tspan>
                        )}
                      </text>
                    </g>
                  )}
                  {(labels || selected || node.critical) &&
                    node.kind === "junction" && (
                      <text
                        className={`map-label${selected ? " strong" : ""}`}
                        x={p.x + 10}
                        y={p.y - 9}
                      >
                        {node.id}
                      </text>
                    )}
                  <title>
                    {node.id} ·{" "}
                    {s && valid
                      ? `${num(s.pressure_m)} m${violating.has(node.id) ? " · 当前时段违规" : ""}`
                      : node.kind === "junction"
                        ? "无有效压力数据"
                        : node.kind === "tank"
                          ? tank
                            ? `水箱 · 水位 ${num(tank.level_m, 2)} m`
                            : "水箱"
                          : "水源"}
                  </title>
                </g>
              );
            })}
            {faultActive && faultAt && fault && (
              <g
                className="fault-mark"
                transform={`translate(${faultAt.x} ${faultAt.y})`}
              >
                <circle className="fault-pulse" r="14" />
                <circle className="fault-core" r="12" />
                <path d="M-4-4L4 4M4-4L-4 4" />
                <g className="fault-tag" transform="translate(18 -15)">
                  <rect
                    width={
                      24 +
                      textWidth(`故障 · 管段 ${fault.link_id}`) +
                      (leakTotal > 0
                        ? textWidth(` 漏水 ${num(leakTotal * 1000, 1)} L/s`)
                        : 0)
                    }
                    height="30"
                    rx="7"
                  />
                  <text x="12" y="19.5">
                    故障 · 管段 {fault.link_id}
                    {leakTotal > 0 && (
                      <tspan className="fault-tag-value">
                        {" "}
                        漏水 {num(leakTotal * 1000, 1)} L/s
                      </tspan>
                    )}
                  </text>
                </g>
              </g>
            )}
            {faultAt &&
              leaks.map((lk, i) => (
                <circle
                  key={`${lk.node_id}-${i}`}
                  className="leak-drop"
                  cx={faultAt.x + (i - (leaks.length - 1) / 2) * 10}
                  cy={faultAt.y + 18}
                  r="3.6"
                >
                  <title>
                    漏水点 {lk.node_id}（管段 {lk.source_link_id}）·{" "}
                    {num(lk.leak_flow_m3s * 1000, 2)} L/s ·{" "}
                    {num(lk.pressure_m)} m
                  </title>
                </circle>
              ))}
          </g>
        </svg>
        <div className="map-legend">
          {layer === "supply" ? (
            <>
              <span>
                <i className="dot green" />
                供需达标
              </span>
              <span>
                <i className="dot amber" />
                供需不足（较大圆）
              </span>
              <span>
                <i className="dot gray" />
                无需水／无数据
              </span>
            </>
          ) : layer === "flow" ? (
            <>
              <span>
                <i className="legend-line" />
                线越粗流量越大
              </span>
              <span>
                <i className="legend-flow" />
                流向光带（示意）
              </span>
              <span>
                <i className="legend-dash" />
                关闭／隔离
              </span>
            </>
          ) : (
            <>
              <span>
                <i className="dot green" />≥{" "}
                {num(c.task.hard_constraints.min_pressure_m, 0)} m
              </span>
              <span>
                <i className="dot amber" />
                低于约束（较大圆）
              </span>
              <span>
                <i className="dot coral" />
                负压（最大圆）
              </span>
              <span>
                <i className="dot ring" />
                当前时段违规
              </span>
              <span>
                <i className="dot gray" />
                无数据
              </span>
            </>
          )}
        </div>
        <span className="map-note">
          坐标为示意布局，不代表实际地理位置或距离
        </span>
      </div>
    </section>
  );
}

export function NodeChart({
  candidates,
  nodeId,
  selected,
  frameIndex,
  onFrame,
}: {
  candidates: Candidate[];
  nodeId: string;
  selected: Candidate;
  frameIndex: number;
  onFrame: (i: number) => void;
}) {
  const lines = candidates.map((c) => ({
    c,
    samples:
      c.series?.frames.map((f) => ({
        t: f.time_s,
        p: f.nodes.find((n) => n.node_id === nodeId)?.pressure_m,
      })) ?? [],
  }));
  const values = lines.flatMap((l) =>
    l.samples.flatMap((s) =>
      s.p != null && Number.isFinite(s.p) ? [s.p] : [],
    ),
  );
  const threshold = selected.task.hard_constraints.min_pressure_m;
  const lo = Math.floor(Math.min(...values, 0) / 10) * 10,
    hi = Math.ceil(Math.max(...values, threshold + 10) / 10) * 10;
  const end = Math.max(
    ...candidates.flatMap(
      (c) => c.series?.frames.map((f) => f.time_s + f.duration_s) ?? [],
    ),
    1,
  );
  const x = (t: number) => 48 + (t / end) * 650,
    y = (p: number) => 165 - ((p - lo) / (hi - lo || 1)) * 135;
  const currentTime = selected.series?.frames[frameIndex]?.time_s;
  const fault = selected.task.fault;
  return (
    <div className="node-chart">
      <div className="panel-heading">
        <div>
          <h2>
            节点压力时序 <span className="node-id">{nodeId}</span>
          </h2>
          <span>m · 区间起点值保持</span>
        </div>
        <div className="chart-legend">
          {candidates.map((c) => (
            <span key={c.id}>
              <i className={c.id === selected.id ? "current" : "other"} />
              {c.name.split(" · ")[0]}
            </span>
          ))}
        </div>
      </div>
      {!values.length ? (
        <div className="empty-small">此实体没有节点压力时序。</div>
      ) : (
        <svg
          viewBox="0 0 730 206"
          role="img"
          aria-label={`节点 ${nodeId} 的压力对照曲线`}
        >
          {fault && (
            <rect
              className="chart-fault-band"
              x={x(fault.start_s)}
              y="25"
              width={Math.max(0, x(Math.min(fault.end_s, end)) - x(fault.start_s))}
              height="140"
            />
          )}
          {[0, 1, 2, 3].map((i) => {
            const v = lo + ((hi - lo) * i) / 3;
            return (
              <g key={i}>
                <line
                  className="chart-grid"
                  x1="48"
                  x2="698"
                  y1={y(v)}
                  y2={y(v)}
                />
                <text
                  className="chart-tick"
                  x="36"
                  y={y(v) + 4}
                  textAnchor="end"
                >
                  {num(v, 0)}
                </text>
              </g>
            );
          })}
          <line
            className="chart-threshold"
            x1="48"
            x2="698"
            y1={y(threshold)}
            y2={y(threshold)}
          />
          <text
            className="chart-threshold-label"
            x="695"
            y={y(threshold) - 7}
            textAnchor="end"
          >
            压力约束 {threshold} m
          </text>
          {lines.map(({ c, samples }) => {
            let drawing = false;
            const d = samples
              .map((s, j) => {
                if (s.p == null || !Number.isFinite(s.p)) {
                  drawing = false;
                  return "";
                }
                const part = `${drawing ? `V ${y(s.p)}` : `M ${x(s.t)} ${y(s.p)}`} H ${x(s.t + (c.series?.frames[j]?.duration_s ?? 0))}`;
                drawing = true;
                return part;
              })
              .join(" ");
            const current = c.id === selected.id;
            return (
              <path
                key={c.id}
                className={`chart-line ${current ? "current" : "other"}`}
                d={d}
                fill="none"
                strokeLinejoin="round"
              />
            );
          })}
          {currentTime != null && (
            <line
              className="chart-cursor"
              x1={x(currentTime)}
              x2={x(currentTime)}
              y1="25"
              y2="165"
            />
          )}
          {selected.series?.frames.map((f, i) => (
            <g
              key={f.time_s}
              className="chart-point"
              role="button"
              tabIndex={0}
              aria-label={`查看 ${time(f.time_s)}`}
              onClick={() => onFrame(i)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onFrame(i);
              }}
            >
              <rect
                x={x(f.time_s) - 12}
                y="25"
                width="24"
                height="142"
                fill="transparent"
              />
              {(i % 2 === 0 || i === selected.series!.frames.length - 1) && (
                <text
                  className="chart-tick"
                  x={x(f.time_s)}
                  y="191"
                  textAnchor="middle"
                >
                  {time(f.time_s)}
                </text>
              )}
            </g>
          ))}
        </svg>
      )}
    </div>
  );
}
