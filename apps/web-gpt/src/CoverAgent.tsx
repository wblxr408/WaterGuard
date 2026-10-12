import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { PHASES } from "./AgentRun";
import type { Workspace } from "./data";
import { actionText, loadWorkspace, num, time } from "./data";
import Icon from "./Icon";

/* 智能体简介：三个角色、八个运行阶段（含「没过就修改」的回路）、哪些已经在真实运行、哪些在等接入，
   再附一个真实计算结果。原在首页，用户 10-03 反馈首页不要这类介绍、智能体要单独成页且要详细版，
   首页已不引用；内容留给智能体页面复用，确认后删或并入。
   角色、权限边界、工具名出自 docs/Agent设计.md 第 4–6 节；阶段顺序出自契约 RunPhase。
   AGH 接入前不展示任何「运行记录」，不把设计说成已经实现。 */

/** 「一个真实计算结果」：取自真实 A/B 对照，数字全部来自接口。 */
interface Example {
  network: string;
  faultLink: string;
  tF: number;
  tE: number;
  minP: number;
  action: string;
  shortA: number;
  shortB: number;
  /** 方案 B 仍低于压力要求的节点数 */
  lowNodes: number;
  recommended: boolean;
}

function buildExample(w: Workspace): Example | null {
  if (w.candidates.length < 2) return null;
  const A = w.candidates[0],
    B = w.candidates.at(-1)!;
  const fault = B.task.fault;
  if (!fault) return null;
  return {
    network: w.scene.network_id.toUpperCase(),
    faultLink: fault.link_id,
    tF: fault.start_s,
    tE: fault.end_s,
    minP: B.task.hard_constraints.min_pressure_m,
    action:
      B.plan.actions
        .map((x) => `${actionText[x.kind]} ${x.link_id}`)
        .join("、") || "不处置",
    shortA: A.verification.metrics.shortage_m3,
    shortB: B.verification.metrics.shortage_m3,
    lowNodes: new Set(
      B.verification.violations
        .filter((v) => v.code === "MIN_PRESSURE")
        .map((v) => v.node_id),
    ).size,
    recommended: B.verification.admission.recommendation_eligible === "pass",
  };
}

/** 例子固定用真实 A/B 对照；当前选的数据源若不是它，就单独读一份。 */
function useExample(w: Workspace | null) {
  const [own, setOwn] = useState<Workspace | null>(null);
  const reuse = w?.source === "pair" ? w : null;
  useEffect(() => {
    if (reuse || own) return;
    const c = new AbortController();
    loadWorkspace("pair", "", c.signal)
      .then((x) => !c.signal.aborted && setOwn(x))
      .catch(() => undefined);
    return () => c.abort();
  }, [reuse, own]);
  const ws = reuse ?? own;
  return useMemo(() => (ws ? buildExample(ws) : null), [ws]);
}

const ROLES = [
  {
    name: "任务建模",
    does: "读管网，把事故、保供要求和允许的操作整理成一份冻结的任务。",
    tools: ["wg_inspect_network", "wg_propose_task"],
  },
  {
    name: "方案规划",
    does: "提出关阀、启泵等操作组合，交给仿真和验收；没过就按反馈修改，再算一轮。",
    tools: ["wg_submit_plan", "wg_simulate_candidate", "wg_verify_candidate"],
  },
  {
    name: "反例审查",
    does: "按任务设置的需水增幅复验有希望的方案，检查保供是否仍然达标。",
    tools: ["wg_create_perturbation"],
  },
];
/** 回路：在「验证」没过时回到「方案规划」 */
const LOOP_FROM = PHASES.findIndex(([p]) => p === "VERIFYING"),
  LOOP_TO = PHASES.findIndex(([p]) => p === "PLANNING");

interface Geo {
  w: number;
  h: number;
  base: string;
  loop: string;
  run: string;
  note: [number, number] | null;
}

export default function CoverAgent({
  w,
  reduce,
  onOpen,
}: {
  w: Workspace | null;
  reduce: boolean;
  onOpen: (p: "overview" | "compare") => void;
}) {
  const example = useExample(w);
  const trackRef = useRef<HTMLDivElement>(null);
  const [geo, setGeo] = useState<Geo | null>(null);

  // 按阶段圆点的实际位置画连线、回路和沿线走动的光点（横排 / 窄屏竖排都适用）
  useLayoutEffect(() => {
    const el = trackRef.current;
    if (!el) return;
    const measure = () => {
      const box = el.getBoundingClientRect();
      const pts = [...el.querySelectorAll<HTMLElement>(".stop i")].map((d) => {
        const r = d.getBoundingClientRect();
        return [
          r.left + r.width / 2 - box.left,
          r.top + r.height / 2 - box.top,
        ] as const;
      });
      if (pts.length !== PHASES.length) return;
      const first = pts[0],
        lastPt = pts[pts.length - 1];
      const vertical =
        Math.abs(lastPt[1] - first[1]) > Math.abs(lastPt[0] - first[0]);
      const [bx, by] = vertical ? [-58, 0] : [0, 70]; // 回路鼓向没有文字的一侧
      const [fx, fy] = pts[LOOP_FROM],
        [tx, ty] = pts[LOOP_TO];
      const curve = `C${fx + bx},${fy + by} ${tx + bx},${ty + by} ${tx},${ty}`;
      const line = (a: number, b: number) =>
        pts
          .slice(a, b + 1)
          .map(([x, y]) => `L${x.toFixed(1)},${y.toFixed(1)}`)
          .join(" ");
      setGeo({
        w: box.width,
        h: box.height,
        base: `M${first[0]},${first[1]} ${line(1, pts.length - 1)}`,
        loop: `M${fx},${fy} ${curve}`,
        run: `M${first[0]},${first[1]} ${line(1, LOOP_FROM)} ${curve} ${line(LOOP_TO + 1, pts.length - 1)}`,
        note: vertical ? null : [(fx + tx) / 2, fy + by * 0.75 + 12],
      });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const drop =
    example && example.shortA > 0
      ? (1 - example.shortB / example.shortA) * 100
      : null;

  return (
    <section className="cv-agent" id="cv-agent" aria-labelledby="agent-title">
      <header className="agent-head">
        <h2 id="agent-title">
          <span>智能体分三个角色，</span>
          <span>处理一次爆管</span>
        </h2>
        <p>
          Agnes 做判断，AGH
          负责调用工具；水力计算、验收、打包证据都由工具完成，结论只能引用工具算出的结果。
        </p>
      </header>

      <div className="agent-track" ref={trackRef}>
        {geo && (
          <svg
            className="track-svg"
            width={geo.w}
            height={geo.h}
            viewBox={`0 0 ${geo.w} ${geo.h}`}
            aria-hidden="true"
          >
            <path className="base" d={geo.base} />
            <path className="loop" d={geo.loop} />
            {!reduce && (
              <circle className="runner" r="4">
                <animateMotion
                  dur="9s"
                  repeatCount="indefinite"
                  path={geo.run}
                />
              </circle>
            )}
          </svg>
        )}
        <ol aria-label="运行阶段">
          {PHASES.map(([p, label], i) => (
            <li
              key={p}
              className={`stop ${i >= LOOP_TO && i <= LOOP_FROM ? "in-loop" : ""}`}
            >
              <i />
              <b>{label}</b>
            </li>
          ))}
        </ol>
        <p
          className="loop-note"
          style={
            geo?.note ? { left: geo.note[0], top: geo.note[1] } : undefined
          }
        >
          验证没过：修改方案，再仿真、再验证
        </p>
      </div>

      <div className="agent-roles">
        {ROLES.map((r) => (
          <article key={r.name}>
            <h3>{r.name}</h3>
            <p>{r.does}</p>
            <ul className="tools" aria-label="可调用的工具">
              {r.tools.map((t) => (
                <li key={t}>
                  <code>{t}</code>
                </li>
              ))}
            </ul>
          </article>
        ))}
      </div>

      <div className="agent-foot">
        <dl className="agent-state">
          <div>
            <dt>
              <i className="ok" />
              已经在真实运行
            </dt>
            <dd>水力仿真、五层准入验收、证据打包</dd>
          </div>
          <div>
            <dt>
              <i className="ok" />
              Agnes 实时规划
            </dt>
            <dd>
              模型选择工具，执行仿真并根据验证结果继续规划。
            </dd>
          </div>
        </dl>
        {example && (
          <div className="agent-example">
            <h3>一个真实计算结果</h3>
            <p>
              {example.network} 管段 {example.faultLink} 在 {time(example.tF)}–
              {time(example.tE)} 停止过水。方案 B（{example.action}）把缺水从{" "}
              {num(example.shortA)} m³ 降到 {num(example.shortB)} m³
              {drop != null && `（少了 ${num(drop, 0)}%）`}
              {example.lowNodes > 0
                ? `；但还有 ${example.lowNodes} 个节点压力低于 ${num(example.minP, 0)} m，硬约束没过，不推荐。`
                : example.recommended
                  ? "，各层验收通过，可以推荐。"
                  : "，但验收没有全部通过，不推荐。"}
            </p>
            <button className="agent-link" onClick={() => onOpen("compare")}>
              在方案对照里看
              <Icon name="arrow" size={15} />
            </button>
          </div>
        )}
      </div>

      <div className="agent-cta">
        <button className="hero-btn primary" onClick={() => onOpen("overview")}>
          进入应急工作台
          <span className="sq">
            <Icon name="arrow" size={18} />
          </span>
        </button>
      </div>
    </section>
  );
}
