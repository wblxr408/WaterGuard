import { useId, useMemo, useState } from "react";
import type { Candidate, Workspace } from "./data";
import { num, time } from "./data";
import Icon from "./Icon";

export type Entity = { kind: "node" | "link"; id: string };
export type Layer = "pressure" | "supply" | "flow";
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
  const positions = useMemo(() => {
    const nodes = w.scene.nodes,
      xs = nodes.map((n) => n.x_m),
      ys = nodes.map((n) => n.y_m);
    const xmin = Math.min(...xs),
      ymin = Math.min(...ys),
      xmax = Math.max(...xs),
      ymax = Math.max(...ys);
    const scale = Math.min(680 / (xmax - xmin || 1), 350 / (ymax - ymin || 1));
    const ox = (860 - (xmax - xmin) * scale) / 2,
      oy = (450 - (ymax - ymin) * scale) / 2;
    return new Map(
      nodes.map((n) => [
        n.id,
        {
          x: ox + (n.x_m - xmin) * scale,
          y: 450 - oy - (n.y_m - ymin) * scale,
        },
      ]),
    );
  }, [w.scene]);
  const valid =
    c.verification.validity === "valid" &&
    c.verification.admission.simulation_valid === "pass";
  const faultActive =
    c.task.fault &&
    frame &&
    frame.time_s >= c.task.fault.start_s &&
    frame.time_s < c.task.fault.end_s;
  function color(id: string) {
    const s = samples.get(id);
    if (!s || !valid) return "#aebac8";
    if (layer === "supply")
      return s.expected_demand_m3s === 0
        ? "#aebac8"
        : s.delivered_demand_m3s / s.expected_demand_m3s >=
            c.task.hard_constraints.min_supply_ratio
          ? "#508bc5"
          : "#dd9770";
    return s.pressure_m < 0
      ? "#c46a59"
      : s.pressure_m < c.task.hard_constraints.min_pressure_m
        ? "#dba362"
        : "#508bc5";
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
              <circle cx="1" cy="1" r=".8" fill="#dbe3ed" />
            </pattern>
          </defs>
          <rect width="860" height="450" fill={`url(#${patternId})`} />
          <g
            transform={`translate(430 225) scale(${zoom}) translate(-430 -225)`}
          >
            {w.scene.links.map((link) => {
              const a = positions.get(link.from_node)!,
                b = positions.get(link.to_node)!,
                s = links.get(link.id);
              const fault = faultActive && link.id === c.task.fault?.link_id,
                selected = entity.kind === "link" && entity.id === link.id;
              const stroke = fault
                ? "#cc715c"
                : !valid || !s
                  ? "#bac6d3"
                  : s.status !== "open"
                    ? "#a6b4c4"
                    : "#8cb2d5";
              const flow = Math.abs(s?.flow_m3s ?? 0);
              const width =
                layer === "flow" && s ? Math.min(2 + flow * 20, 7) : 2.2;
              return (
                <g
                  key={link.id}
                  role="button"
                  tabIndex={0}
                  aria-label={`${link.kind === "pump" ? "水泵" : "管段"} ${link.id}`}
                  onClick={() => onSelect({ kind: "link", id: link.id })}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect({ kind: "link", id: link.id });
                    }
                  }}
                  className="map-entity"
                >
                  <line
                    x1={a.x}
                    y1={a.y}
                    x2={b.x}
                    y2={b.y}
                    stroke="transparent"
                    strokeWidth="14"
                  />
                  {selected && (
                    <line
                      x1={a.x}
                      y1={a.y}
                      x2={b.x}
                      y2={b.y}
                      stroke="#2868d3"
                      strokeWidth="10"
                      opacity=".17"
                    />
                  )}
                  <line
                    x1={a.x}
                    y1={a.y}
                    x2={b.x}
                    y2={b.y}
                    stroke={stroke}
                    strokeWidth={fault ? 4 : width}
                    strokeDasharray={s?.status === "closed" ? "5 4" : undefined}
                  />
                  {link.kind === "pump" && (
                    <g
                      transform={`translate(${(a.x + b.x) / 2} ${(a.y + b.y) / 2})`}
                    >
                      <rect
                        x="-9"
                        y="-9"
                        width="18"
                        height="18"
                        rx="5"
                        fill="#edf4fe"
                        stroke="#467cb9"
                      />
                      <path d="M-3-5L5 0-3 5Z" fill="#467cb9" />
                    </g>
                  )}
                  {fault && (
                    <g
                      transform={`translate(${(a.x + b.x) / 2} ${(a.y + b.y) / 2})`}
                    >
                      <circle r="14" fill="#fff3e9" stroke="#ce745f" />
                      <path
                        d="M-4-4L4 4M4-4L-4 4"
                        stroke="#ce745f"
                        strokeWidth="2"
                      />
                      <rect
                        x="20"
                        y="-14"
                        width="92"
                        height="28"
                        rx="5"
                        fill="#fff"
                        stroke="#ebd5c9"
                      />
                      <text
                        x="66"
                        y="4"
                        textAnchor="middle"
                        fill="#a55243"
                        fontSize="11"
                      >
                        故障管段 {link.id}
                      </text>
                    </g>
                  )}
                  {labels && (
                    <text
                      x={(a.x + b.x) / 2 + 3}
                      y={(a.y + b.y) / 2 - 6}
                      fill="#8493a6"
                      fontSize="8"
                    >
                      {link.id}
                    </text>
                  )}
                  <title>
                    {link.id} ·{" "}
                    {s ? `${num(s.flow_m3s, 5)} m³/s · ${s.status}` : "无时序"}
                  </title>
                </g>
              );
            })}
            {w.scene.nodes.map((node) => {
              const p = positions.get(node.id)!,
                selected = entity.kind === "node" && entity.id === node.id,
                s = samples.get(node.id);
              return (
                <g
                  key={node.id}
                  role="button"
                  tabIndex={0}
                  aria-label={`节点 ${node.id}`}
                  className="map-entity"
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
                      cx={p.x}
                      cy={p.y}
                      r="13"
                      fill="#356bd4"
                      opacity=".14"
                    />
                  )}
                  {node.kind === "junction" ? (
                    <circle
                      cx={p.x}
                      cy={p.y}
                      r={selected ? 6 : node.critical ? 5 : 3.8}
                      fill={color(node.id)}
                      stroke={selected ? "#fff" : "#f2f6fb"}
                      strokeWidth={selected ? 2.5 : 1.2}
                    />
                  ) : (
                    <g>
                      <rect
                        x={p.x - 10}
                        y={p.y - 10}
                        width="20"
                        height="20"
                        rx={node.kind === "tank" ? 3 : 10}
                        fill="#e5eef8"
                        stroke="#5d86ae"
                        strokeWidth="1.6"
                      />
                      <path
                        d={`M${p.x - 5} ${p.y}h10M${p.x - 4} ${p.y + 4}h8`}
                        stroke="#5d86ae"
                      />
                      <text
                        x={p.x + 16}
                        y={p.y + 4}
                        fill="#607b99"
                        fontSize="11"
                      >
                        {node.id}
                      </text>
                    </g>
                  )}
                  {(labels || selected || node.critical) &&
                    node.kind === "junction" && (
                      <text
                        x={p.x + 10}
                        y={p.y - 8}
                        fill="#355577"
                        fontSize="10"
                        fontWeight={selected ? 700 : 400}
                      >
                        {node.id}
                      </text>
                    )}
                  <title>
                    {node.id} ·{" "}
                    {s && valid
                      ? `${num(s.pressure_m)} m`
                      : node.kind === "junction"
                        ? "无有效压力数据"
                        : node.kind === "tank"
                          ? "水箱"
                          : "水源"}
                  </title>
                </g>
              );
            })}
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
                供需不足
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
                流量越大，线条越粗
              </span>
              <span>
                <i className="dot gray" />
                关闭／无数据
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
                低于约束
              </span>
              <span>
                <i className="dot coral" />
                负压
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
          {candidates.map((c, i) => (
            <span key={c.id}>
              <i style={{ background: i ? "#356bd4" : "#b1bab5" }} />
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
          {[0, 1, 2, 3].map((i) => {
            const v = lo + ((hi - lo) * i) / 3;
            return (
              <g key={i}>
                <line x1="48" x2="698" y1={y(v)} y2={y(v)} stroke="#e4eaf3" />
                <text
                  x="36"
                  y={y(v) + 4}
                  textAnchor="end"
                  fill="#8797ac"
                  fontSize="11"
                >
                  {num(v, 0)}
                </text>
              </g>
            );
          })}
          <line
            x1="48"
            x2="698"
            y1={y(threshold)}
            y2={y(threshold)}
            stroke="#caa477"
            strokeDasharray="5 4"
          />
          <text
            x="695"
            y={y(threshold) - 7}
            textAnchor="end"
            fill="#b58a5e"
            fontSize="10"
          >
            压力约束 {threshold} m
          </text>
          {lines.map(({ c, samples }, i) => {
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
            return (
              <path
                key={c.id}
                d={d}
                fill="none"
                stroke={i ? "#356bd4" : "#aab8cb"}
                strokeWidth={c.id === selected.id ? 2.8 : 1.7}
                strokeLinejoin="round"
              />
            );
          })}
          {currentTime != null && (
            <line
              x1={x(currentTime)}
              x2={x(currentTime)}
              y1="25"
              y2="165"
              stroke="#5d7fa8"
              strokeDasharray="3 4"
              opacity=".55"
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
                  x={x(f.time_s)}
                  y="191"
                  textAnchor="middle"
                  fill="#8797ac"
                  fontSize="10"
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
