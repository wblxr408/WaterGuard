import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import type {
  FormEvent,
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from "react";
import type { LinkSample } from "@waterguard/contracts";
import type { Candidate, Workspace } from "./data";
import { num, time } from "./data";
import Icon from "./Icon";
import { prefersReducedMotion } from "./motion";

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
const textWidth = (s: string, size = 12) =>
  [...s].reduce(
    (n, ch) => n + (ch.charCodeAt(0) > 255 ? 1.04 : 0.58) * size,
    0,
  );
const searchWords: [string, "node" | "link"][] = [
  ["节点", "node"],
  ["水箱", "node"],
  ["水源", "node"],
  ["管段", "link"],
  ["水泵", "link"],
  ["阀门", "link"],
];
const lps = (m3s: number | undefined) =>
  m3s == null ? "—" : `${num(m3s * 1000, 1)} L/s`;

/* ---- 视图：位置随缩放拉开，节点、线宽、文字保持屏幕大小不变（语义缩放） ---- */
type View = { k: number; x: number; y: number };
const W = 860,
  H = 450,
  FIT: View = { k: 1, x: 0, y: 0 },
  MIN_K = 0.8,
  MAX_K = 8;
const clampK = (k: number) => Math.min(MAX_K, Math.max(MIN_K, k));
/** 以视图坐标 (px, py) 为锚点缩放，锚点下的地物不动。 */
const zoomAt = (v: View, k: number, px: number, py: number): View => {
  const k2 = clampK(k);
  const wx = (px - v.x) / v.k,
    wy = (py - v.y) / v.k;
  return { k: k2, x: px - wx * k2, y: py - wy * k2 };
};
const centerOn = (wx: number, wy: number, k: number): View => {
  const k2 = clampK(k);
  return { k: k2, x: W / 2 - wx * k2, y: H / 2 - wy * k2 };
};
const segDist = (
  px: number,
  py: number,
  ax: number,
  ay: number,
  bx: number,
  by: number,
) => {
  const dx = bx - ax,
    dy = by - ay,
    l2 = dx * dx + dy * dy || 1;
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / l2));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
};

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
  const uid = useId().replaceAll(":", "");
  const svgRef = useRef<SVGSVGElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const [layer, setLayer] = useState<Layer>("pressure");
  const [labels, setLabels] = useState(false);
  const [view, setView] = useState<View>(FIT);
  const [hover, setHover] = useState<Entity | null>(null);
  const [armed, setArmed] = useState(false);
  const [hint, setHint] = useState(false);
  const [query, setQuery] = useState("");
  const [notFound, setNotFound] = useState(false);
  const [searchNote, setSearchNote] = useState("");
  const [full, setFull] = useState(false);
  const [dragging, setDragging] = useState(false);
  const viewRef = useRef(view),
    armedRef = useRef(armed),
    anim = useRef(0),
    tipPos = useRef({ x: 0, y: 0 }),
    drag = useRef<{
      id: number;
      sx: number;
      sy: number;
      v: View;
      moved: boolean;
    } | null>(null),
    touches = useRef(new Map<number, { x: number; y: number }>()),
    pinch = useRef<{ d: number; v: View; mx: number; my: number } | null>(
      null,
    );
  useLayoutEffect(() => {
    viewRef.current = view;
    armedRef.current = armed;
  });

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
    // 绘图区留出上方状态条（约 48）和下方图例（约 80）的位置，扁平的小管网也不会压住图例。
    const scale = Math.min(700 / (xmax - xmin || 1), 322 / (ymax - ymin || 1));
    const ox = (W - (xmax - xmin) * scale) / 2,
      top = 48 + (322 - (ymax - ymin) * scale) / 2;
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
  const worldAlong = (linkId: string, fraction = 0.5) => {
    const l = w.scene.links.find((x) => x.id === linkId);
    const a = l && positions.get(l.from_node),
      b = l && positions.get(l.to_node);
    return a && b
      ? { x: a.x + (b.x - a.x) * fraction, y: a.y + (b.y - a.y) * fraction }
      : null;
  };
  const worldOf = (e: Entity) =>
    e.kind === "node" ? (positions.get(e.id) ?? null) : worldAlong(e.id);
  const faultWorld = fault
    ? worldAlong(fault.link_id, fault.location_fraction ?? 0.5)
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

  // 屏幕（视图）坐标：位置按缩放拉开，符号大小不变。
  const T = (p: { x: number; y: number }) => ({
    x: p.x * view.k + view.x,
    y: p.y * view.k + view.y,
  });

  /* ---------------- 视图操作 ---------------- */
  const animateTo = (target: View) => {
    cancelAnimationFrame(anim.current);
    const from = viewRef.current;
    if (prefersReducedMotion()) {
      setView(target);
      return;
    }
    // 中心点线性移动、比例按对数插值，缩放过程匀速，不会先偏后回。
    const c0 = { x: (W / 2 - from.x) / from.k, y: (H / 2 - from.y) / from.k },
      c1 = {
        x: (W / 2 - target.x) / target.k,
        y: (H / 2 - target.y) / target.k,
      };
    const t0 = performance.now(),
      dur = 460;
    const step = (now: number) => {
      const t = Math.min(1, (now - t0) / dur),
        e = 1 - (1 - t) ** 4;
      const k = from.k * (target.k / from.k) ** e;
      const cx = c0.x + (c1.x - c0.x) * e,
        cy = c0.y + (c1.y - c0.y) * e;
      setView(t >= 1 ? target : { k, x: W / 2 - cx * k, y: H / 2 - cy * k });
      if (t < 1) anim.current = requestAnimationFrame(step);
    };
    anim.current = requestAnimationFrame(step);
    // 后台标签页里 rAF 不运行：到时直接落到目标视图。
    window.setTimeout(() => {
      if (viewRef.current !== target) {
        cancelAnimationFrame(anim.current);
        setView(target);
      }
    }, dur + 150);
  };
  const zoomBy = (f: number) =>
    animateTo(zoomAt(viewRef.current, viewRef.current.k * f, W / 2, H / 2));
  const focus = (e: Entity | null, minK = 2.6) => {
    const p = e && worldOf(e);
    if (p) animateTo(centerOn(p.x, p.y, Math.max(viewRef.current.k, minK)));
  };
  const toViewPoint = (clientX: number, clientY: number) => {
    const svg = svgRef.current,
      m = svg?.getScreenCTM();
    if (!m) return null;
    const p = new DOMPoint(clientX, clientY).matrixTransform(m.inverse());
    return { x: p.x, y: p.y, s: m.a };
  };
  /** 取离指针最近的节点（15px 内），否则最近的管段（8px 内）：密集处也能点准。 */
  const pick = (clientX: number, clientY: number): Entity | null => {
    const p = toViewPoint(clientX, clientY);
    if (!p) return null;
    let best: Entity | null = null,
      bd = 15 / p.s;
    for (const n of w.scene.nodes) {
      const q = T(positions.get(n.id)!);
      const d = Math.hypot(q.x - p.x, q.y - p.y);
      if (d < bd) {
        bd = d;
        best = { kind: "node", id: n.id };
      }
    }
    if (best) return best;
    let ld = 8 / p.s;
    for (const l of w.scene.links) {
      const a = T(positions.get(l.from_node)!),
        b = T(positions.get(l.to_node)!);
      const d = segDist(p.x, p.y, a.x, a.y, b.x, b.y);
      if (d < ld) {
        ld = d;
        best = { kind: "link", id: l.id };
      }
    }
    return best;
  };
  const placeTip = (clientX: number, clientY: number) => {
    const box = canvasRef.current?.getBoundingClientRect();
    if (!box) return;
    // 卡片贴在指针右下方，靠近右、下边缘时翻到另一侧。
    const tw = tipRef.current?.offsetWidth ?? 220,
      th = tipRef.current?.offsetHeight ?? 110;
    let x = clientX - box.left + 16,
      y = clientY - box.top + 16;
    if (x + tw > box.width - 8) x = clientX - box.left - tw - 16;
    if (y + th > box.height - 8) y = clientY - box.top - th - 12;
    tipPos.current = { x, y };
    if (tipRef.current)
      tipRef.current.style.transform = `translate(${x}px, ${y}px)`;
  };

  // 滚轮缩放：点过地图（或按住 Ctrl）才生效，避免浏览页面时滚轮被地图吞掉。
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    let timer = 0;
    const onWheel = (e: WheelEvent) => {
      if (!armedRef.current && !e.ctrlKey) {
        setHint(true);
        window.clearTimeout(timer);
        timer = window.setTimeout(() => setHint(false), 1800);
        return;
      }
      e.preventDefault();
      const m = svg.getScreenCTM();
      if (!m) return;
      const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(m.inverse());
      const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
      cancelAnimationFrame(anim.current);
      setView((v) => zoomAt(v, v.k * Math.exp(-dy * 0.0016), p.x, p.y));
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      svg.removeEventListener("wheel", onWheel);
      window.clearTimeout(timer);
    };
  }, []);
  useEffect(() => {
    const on = () => setFull(document.fullscreenElement === panelRef.current);
    document.addEventListener("fullscreenchange", on);
    return () => document.removeEventListener("fullscreenchange", on);
  }, []);
  useEffect(() => () => cancelAnimationFrame(anim.current), []);
  // 换管网时回到全图。
  useEffect(() => {
    setView(FIT);
  }, [w.scene]);
  // 外部选中（违规列表、搜索）的实体若在视野外，平移过去。
  useEffect(() => {
    const p = worldOf(entity);
    if (!p) return;
    const v = viewRef.current,
      q = { x: p.x * v.k + v.x, y: p.y * v.k + v.y };
    if (q.x < 30 || q.x > W - 30 || q.y < 30 || q.y > H - 30)
      animateTo(centerOn(p.x, p.y, v.k));
  }, [entity.kind, entity.id]);

  const onPointerDown = (e: ReactPointerEvent<SVGSVGElement>) => {
    setArmed(true);
    touches.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (touches.current.size === 2) {
      const [a, b] = [...touches.current.values()];
      const mid = toViewPoint((a.x + b.x) / 2, (a.y + b.y) / 2);
      pinch.current = mid && {
        d: Math.hypot(a.x - b.x, a.y - b.y) || 1,
        v: viewRef.current,
        mx: mid.x,
        my: mid.y,
      };
      drag.current = null;
      return;
    }
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    cancelAnimationFrame(anim.current);
    drag.current = {
      id: e.pointerId,
      sx: e.clientX,
      sy: e.clientY,
      v: viewRef.current,
      moved: false,
    };
  };
  const onPointerMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (touches.current.has(e.pointerId))
      touches.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const pz = pinch.current;
    if (touches.current.size === 2 && pz) {
      const [a, b] = [...touches.current.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      setView(zoomAt(pz.v, (pz.v.k * d) / pz.d, pz.mx, pz.my));
      return;
    }
    const d = drag.current;
    if (d && d.id === e.pointerId) {
      const s = svgRef.current?.getScreenCTM()?.a ?? 1;
      const dx = e.clientX - d.sx,
        dy = e.clientY - d.sy;
      if (!d.moved && Math.hypot(dx, dy) > 4) {
        d.moved = true;
        setDragging(true);
        setHover(null);
      }
      if (d.moved) setView({ k: d.v.k, x: d.v.x + dx / s, y: d.v.y + dy / s });
      return;
    }
    const hit = pick(e.clientX, e.clientY);
    if (hit?.kind !== hover?.kind || hit?.id !== hover?.id) setHover(hit);
    placeTip(e.clientX, e.clientY);
  };
  const onPointerUp = (e: ReactPointerEvent<SVGSVGElement>) => {
    touches.current.delete(e.pointerId);
    if (touches.current.size < 2) pinch.current = null;
    const d = drag.current;
    drag.current = null;
    setDragging(false);
    if (d && d.id === e.pointerId && !d.moved) {
      const hit = pick(e.clientX, e.clientY);
      if (hit) onSelect(hit);
    }
  };
  const onKeyDown = (e: ReactKeyboardEvent<SVGSVGElement>) => {
    const v = viewRef.current,
      step = 48;
    const pan = (dx: number, dy: number) =>
      animateTo({ k: v.k, x: v.x + dx, y: v.y + dy });
    const keys: Record<string, () => void> = {
      ArrowLeft: () => pan(step, 0),
      ArrowRight: () => pan(-step, 0),
      ArrowUp: () => pan(0, step),
      ArrowDown: () => pan(0, -step),
      "+": () => zoomBy(1.5),
      "=": () => zoomBy(1.5),
      "-": () => zoomBy(1 / 1.5),
      "0": () => animateTo(FIT),
    };
    const run = keys[e.key];
    if (run) {
      e.preventDefault();
      e.stopPropagation();
      run();
    }
  };
  /**
   * 解析「节点 123」「管段 123」「123」。Net3 里节点和管段可能同号（如 123），
   * 带类型前缀就按前缀找；只写编号时先找节点，并提示还有同号管段。
   */
  const findEntity = (q: string): { hit: Entity | null; also: string } => {
    let t = q.trim(),
      prefer: "node" | "link" | null = null;
    for (const [word, kind] of searchWords)
      if (t.startsWith(word)) {
        prefer = kind;
        t = t.slice(word.length).trim();
        break;
      }
    const id = t.toLowerCase();
    if (!id) return { hit: null, also: "" };
    const n = w.scene.nodes.find((x) => x.id.toLowerCase() === id),
      l = w.scene.links.find((x) => x.id.toLowerCase() === id);
    if (prefer === "link")
      return { hit: l ? { kind: "link", id: l.id } : null, also: "" };
    if (prefer === "node")
      return { hit: n ? { kind: "node", id: n.id } : null, also: "" };
    if (n)
      return {
        hit: { kind: "node", id: n.id },
        also: l ? `同号管段可输入「管段 ${l.id}」` : "",
      };
    return { hit: l ? { kind: "link", id: l.id } : null, also: "" };
  };
  const runSearch = (value: string) => {
    const { hit, also } = findEntity(value);
    setSearchNote(hit ? also : value.trim() ? "没有这个编号" : "");
    setNotFound(!hit && !!value.trim());
    if (hit) {
      onSelect(hit);
      focus(hit);
    }
  };
  const onSearch = (e: FormEvent) => {
    e.preventDefault();
    runSearch(query);
  };
  const toggleFull = () => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void panelRef.current?.requestFullscreen?.();
  };

  /* ---------------- 派生显示数据 ---------------- */
  const counts = { ok: 0, low: 0, bad: 0, na: 0 };
  w.scene.nodes.forEach((n) => {
    if (n.kind === "junction") counts[level(n.id)]++;
  });
  let flowingLinks = 0,
    closedLinks = 0;
  const near = new Set<string>();
  for (const e of [entity, hover]) {
    if (e?.kind === "node")
      w.scene.links.forEach((l) => {
        if (l.from_node === e.id || l.to_node === e.id) near.add(l.id);
      });
  }
  const showNodeIds = labels || view.k >= 2.2,
    showLinkIds = labels ? view.k >= 1.6 : view.k >= 3.4;
  const selNode =
    entity.kind === "node"
      ? w.scene.nodes.find((n) => n.id === entity.id)
      : undefined;

  const linkGeo = w.scene.links.map((link) => {
    const a = T(positions.get(link.from_node)!),
      b = T(positions.get(link.to_node)!),
      s = links.get(link.id);
    const state = valid ? linkState(s, link.initial_status) : null;
    const flow = Math.abs(s?.flow_m3s ?? 0);
    const flowing =
      !!s && !!state && (state === "open" || state === "leaking") && flow > 1e-5;
    if (flowing) flowingLinks++;
    if (state === "closed" || state === "isolated") closedLinks++;
    const isFault = faultActive && link.id === fault?.link_id;
    const selected = entity.kind === "link" && entity.id === link.id;
    const hovered = hover?.kind === "link" && hover.id === link.id;
    const tone = isFault
      ? "fault"
      : selected
        ? "selected"
        : hovered
          ? "hovered"
          : near.has(link.id)
            ? "near"
            : "";
    const width =
      layer === "flow" && flowing ? 3.2 + (5 * flow) / maxFlow : 3.4;
    return { link, a, b, s, state, flow, flowing, tone, width };
  });

  const hoverInfo = (() => {
    if (!hover) return null;
    if (hover.kind === "link") {
      const g = linkGeo.find((x) => x.link.id === hover.id);
      if (!g) return null;
      const pump = g.link.kind === "pump";
      const running = pump && g.flowing && g.s?.status === "open";
      return {
        title: `${pump ? "水泵" : g.link.kind === "valve" ? "阀门" : "管段"} ${g.link.id}`,
        rows: [
          [
            "状态",
            pump
              ? running
                ? "运行中"
                : "停止"
              : g.state
                ? linkStateText[g.state]
                : "无数据",
            g.state && g.state !== "open" ? "bad" : "",
          ],
          [
            "流量",
            g.s
              ? `${lps(g.s.flow_m3s)}${g.flow > 1e-5 ? (g.s.flow_m3s > 0 ? `（${g.link.from_node}→${g.link.to_node}）` : `（${g.link.to_node}→${g.link.from_node}）`) : ""}`
              : "—",
            "",
          ],
        ] as [string, string, string][],
        note: isFaultLink(g.link.id) ? "故障管段" : "",
      };
    }
    const node = w.scene.nodes.find((n) => n.id === hover.id);
    if (!node) return null;
    if (node.kind === "tank") {
      const t = tanks.get(node.id);
      return {
        title: `水箱 ${node.id}`,
        rows: [
          ["水位", t && valid ? `${num(t.level_m)} m` : "—", ""],
          [
            "净流入",
            t && valid
              ? `${lps(t.net_inflow_m3s)}（${t.net_inflow_m3s >= 0 ? "进水" : "出水"}）`
              : "—",
            "",
          ],
        ] as [string, string, string][],
        note: "",
      };
    }
    if (node.kind === "reservoir")
      return {
        title: `水源 ${node.id}`,
        rows: [["水头（场景数据）", `${num(node.elevation_m)} m`, ""]] as [
          string,
          string,
          string,
        ][],
        note: "",
      };
    const s = samples.get(node.id);
    const lv = level(node.id);
    const ratio =
      s && s.expected_demand_m3s > 0
        ? s.delivered_demand_m3s / s.expected_demand_m3s
        : null;
    return {
      title: `节点 ${node.id}`,
      rows: [
        [
          "压力",
          s && valid
            ? `${num(s.pressure_m)} m（要求 ≥ ${num(c.task.hard_constraints.min_pressure_m, 0)} m）`
            : "—",
          layer === "supply" ? "" : lv,
        ],
        [
          "供需比",
          valid && ratio != null ? `${num(ratio * 100, 1)}%` : "无需水",
          "",
        ],
      ] as [string, string, string][],
      note: violating.has(node.id) ? "当前时段违规（验收报告）" : "",
    };
  })();
  function isFaultLink(id: string) {
    return !!fault && fault.link_id === id;
  }

  const pillWidth = (text: string, value: string | null) =>
    textWidth(text) + (value ? textWidth(` ${value}`) : 0) + 16;
  // 已占用的标签区域（故障标签、水源水箱标签），选中节点的标签避开它们。
  const taken: { x: number; y: number; w: number; h: number }[] = [];
  if (faultActive && faultWorld && fault) {
    const q = T(faultWorld);
    taken.push({
      x: q.x + 18,
      y: q.y - 15,
      w:
        24 +
        textWidth(`故障 · 管段 ${fault.link_id}`) +
        (leakTotal > 0 ? textWidth(` 漏水 ${num(leakTotal * 1000, 1)} L/s`) : 0),
      h: 30,
    });
  }
  w.scene.nodes.forEach((n) => {
    if (n.kind === "junction") return;
    const q = T(positions.get(n.id)!),
      tank = tanks.get(n.id);
    taken.push({
      x: q.x - 12,
      y: q.y - 12,
      w:
        29 +
        pillWidth(
          n.kind === "tank" ? `水箱 ${n.id}` : `水源 ${n.id}`,
          n.kind === "tank" && tank ? `${num(tank.level_m, 2)} m` : null,
        ),
      h: 24,
    });
  });
  const placePill = (q: { x: number; y: number }, width: number) => {
    const options = [
      { x: q.x + 14, y: q.y - 20 },
      { x: q.x + 14, y: q.y + 20 },
      { x: q.x - 14 - width, y: q.y - 20 },
      { x: q.x - 14 - width, y: q.y + 20 },
    ];
    return (
      options.find(
        (o) =>
          !taken.some(
            (b) =>
              o.x < b.x + b.w &&
              o.x + width > b.x &&
              o.y - 11 < b.y + b.h &&
              o.y + 11 > b.y,
          ),
      ) ?? options[0]
    );
  };
  const pill = (
    x: number,
    y: number,
    text: string,
    value: string | null,
    tone = "",
  ) => {
    const w1 = textWidth(text),
      w2 = value ? textWidth(` ${value}`) : 0;
    return (
      <g className={`map-pill ${tone}`} transform={`translate(${x} ${y})`}>
        <rect y="-11" width={w1 + w2 + 16} height="22" rx="11" />
        <text x="8" y="4.5">
          {text}
          {value && <tspan className="map-pill-value"> {value}</tspan>}
        </text>
      </g>
    );
  };

  return (
    <section
      className={`network-panel${full ? " is-full" : ""}`}
      ref={panelRef}
    >
      <div className="panel-heading">
        <div>
          <h2>供水管网</h2>
          <span>
            {w.scene.network_id.toUpperCase()} <i /> 示意拓扑
          </span>
        </div>
        <form className="map-search" role="search" onSubmit={onSearch}>
          <Icon name="search" size={15} />
          <input
            aria-label="按编号定位节点或管段，例如 节点 15 或 管段 123"
            placeholder="定位：节点 15 / 管段 123"
            list={`${uid}-entities`}
            value={query}
            aria-invalid={notFound}
            onChange={(e) => {
              const value = e.target.value;
              setQuery(value);
              setNotFound(false);
              setSearchNote("");
              // 从候选里选中（带类型前缀）就直接定位；手动输入编号按回车定位，打字过程中不乱跳。
              if (searchWords.some(([word]) => value.startsWith(`${word} `)))
                runSearch(value);
            }}
          />
          <datalist id={`${uid}-entities`}>
            {w.scene.nodes.map((n) => (
              <option
                key={`n-${n.id}`}
                value={`${n.kind === "junction" ? "节点" : n.kind === "tank" ? "水箱" : "水源"} ${n.id}`}
              />
            ))}
            {w.scene.links.map((l) => (
              <option
                key={`l-${l.id}`}
                value={`${l.kind === "pump" ? "水泵" : l.kind === "valve" ? "阀门" : "管段"} ${l.id}`}
              />
            ))}
          </datalist>
          {searchNote && (
            <span className={`map-search-miss${notFound ? "" : " info"}`}>{searchNote}</span>
          )}
        </form>
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
      <div className="network-canvas" ref={canvasRef}>
        <div className="map-status" aria-live="polite">
          <span className="map-status-time mono">
            {frame ? time(frame.time_s) : "—"}
          </span>
          {!valid ? (
            <span className="map-chip">无有效仿真结果</span>
          ) : layer === "flow" ? (
            <>
              <span className="map-chip">
                <i className="legend-flow" />
                流动管段 <b>{flowingLinks}</b>
              </span>
              <span className="map-chip">
                <i className="legend-dash" />
                关闭／隔离 <b>{closedLinks}</b>
              </span>
            </>
          ) : layer === "supply" ? (
            <>
              <span className="map-chip">
                <i className="dot green" />
                达标 <b>{counts.ok}</b>
              </span>
              <span className="map-chip">
                <i className="dot amber" />
                不足 <b>{counts.low}</b>
              </span>
            </>
          ) : (
            <>
              <span className="map-chip">
                <i className="dot green" />
                满足 <b>{counts.ok}</b>
              </span>
              <span className="map-chip">
                <i className="dot amber" />
                偏低 <b>{counts.low}</b>
              </span>
              <span className="map-chip">
                <i className="dot coral" />
                负压 <b>{counts.bad}</b>
              </span>
              <span className="map-chip">
                <i className="dot ring" />
                当前违规 <b>{violating.size}</b>
              </span>
            </>
          )}
        </div>
        <div className="map-tools" role="toolbar" aria-label="管网视图">
          <button aria-label="放大管网" title="放大" onClick={() => zoomBy(1.6)}>
            <Icon name="plus" size={17} />
          </button>
          <button
            aria-label="缩小管网"
            title="缩小"
            onClick={() => zoomBy(1 / 1.6)}
          >
            <Icon name="minus" size={17} />
          </button>
          <button
            aria-label="适应全图"
            title="适应全图"
            onClick={() => animateTo(FIT)}
          >
            <Icon name="fit" size={17} />
          </button>
          <button
            aria-label="定位到选中对象"
            title="定位到选中对象"
            onClick={() => focus(entity)}
          >
            <Icon name="target" size={17} />
          </button>
          <button
            aria-label="定位到故障点"
            title="定位到故障点"
            disabled={!fault}
            onClick={() =>
              fault && focus({ kind: "link", id: fault.link_id }, 3)
            }
          >
            <Icon name="warning" size={17} />
          </button>
          <button
            aria-label="显示全部编号"
            title="显示全部编号"
            aria-pressed={labels}
            className={labels ? "active" : ""}
            onClick={() => setLabels(!labels)}
          >
            <Icon name="layers" size={17} />
          </button>
          <button
            aria-label={full ? "退出全屏" : "全屏查看管网"}
            title={full ? "退出全屏" : "全屏查看"}
            aria-pressed={full}
            className={full ? "active" : ""}
            onClick={toggleFull}
          >
            <Icon name="expand" size={17} />
          </button>
        </div>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          className={`network-svg${dragging ? " is-dragging" : hover ? " is-hovering" : ""}`}
          role="application"
          tabIndex={0}
          aria-label="供水管网拓扑。点击节点或管段查看详情；方向键平移，加减号缩放，0 适应全图。"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onPointerLeave={() => {
            setHover(null);
            setArmed(false);
          }}
          onDoubleClick={(e) => {
            const p = toViewPoint(e.clientX, e.clientY);
            if (p)
              animateTo(zoomAt(viewRef.current, viewRef.current.k * 2, p.x, p.y));
          }}
          onKeyDown={onKeyDown}
        >
          <defs>
            <pattern
              id={`${uid}-grid`}
              width="28"
              height="28"
              patternUnits="userSpaceOnUse"
              patternTransform={`translate(${view.x} ${view.y}) scale(${view.k})`}
            >
              <path
                d="M28 0H0V28"
                fill="none"
                stroke="#a7b8d4"
                strokeWidth={0.55 / view.k}
                opacity=".32"
              />
            </pattern>
            <filter
              id={`${uid}-lift`}
              x="-20%"
              y="-20%"
              width="140%"
              height="140%"
            >
              <feDropShadow
                dx="0"
                dy="1"
                stdDeviation="1.3"
                floodColor="#1c2a5a"
                floodOpacity=".28"
              />
            </filter>
          </defs>
          <rect width={W} height={H} fill={`url(#${uid}-grid)`} />

          {/* 管道：先画全部外壁，再画全部内芯，交叉处干净 */}
          <g className="pipes">
            {linkGeo.map((g) =>
              g.state === "closed" || g.state === "isolated" ? null : (
                <line
                  key={`c-${g.link.id}`}
                  className={`pipe-casing st-${g.state ?? "na"} kind-${g.link.kind} ${g.tone}`}
                  x1={g.a.x}
                  y1={g.a.y}
                  x2={g.b.x}
                  y2={g.b.y}
                  strokeWidth={g.width + 3}
                />
              ),
            )}
            {linkGeo.map((g) => (
              <line
                key={`f-${g.link.id}`}
                data-id={g.link.id}
                className={`pipe-core st-${g.state ?? "na"} kind-${g.link.kind} ${g.tone}${layer === "flow" && g.flowing ? " carrying" : ""}`}
                x1={g.a.x}
                y1={g.a.y}
                x2={g.b.x}
                y2={g.b.y}
                strokeWidth={
                  g.state === "closed" || g.state === "isolated" ? 2.2 : g.width
                }
                strokeDasharray={g.state ? dashFor[g.state] : undefined}
              />
            ))}
            {layer === "flow" &&
              linkGeo.map((g) => {
                if (!g.flowing || !g.s) return null;
                // 流向光带只是示意：方向取计算流量的符号（正值为起点→终点），速度按相对流量。
                const [u, v] = g.s.flow_m3s >= 0 ? [g.a, g.b] : [g.b, g.a];
                return (
                  <line
                    key={`d-${g.link.id}`}
                    className="flow-dash"
                    x1={u.x}
                    y1={u.y}
                    x2={v.x}
                    y2={v.y}
                    strokeWidth={Math.max(1.6, g.width * 0.55)}
                    style={{
                      animationDuration: `${(2.6 - 1.9 * Math.sqrt(g.flow / maxFlow)).toFixed(2)}s`,
                    }}
                  />
                );
              })}
            {showLinkIds &&
              linkGeo.map((g) => (
                <text
                  key={`t-${g.link.id}`}
                  className="map-label minor"
                  x={(g.a.x + g.b.x) / 2 + 4}
                  y={(g.a.y + g.b.y) / 2 - 5}
                >
                  {g.link.id}
                </text>
              ))}
          </g>

          {/* 水泵：管段中点的徽标，运行时点亮 */}
          <g className="pumps" filter={`url(#${uid}-lift)`}>
            {linkGeo
              .filter((g) => g.link.kind === "pump")
              .map((g) => {
                const running = g.flowing && g.s?.status === "open";
                return (
                  <g
                    key={`p-${g.link.id}`}
                    className={`pump-badge${running ? " running" : ""}${g.tone ? ` ${g.tone}` : ""}`}
                    transform={`translate(${(g.a.x + g.b.x) / 2} ${(g.a.y + g.b.y) / 2})`}
                  >
                    <circle r="11" />
                    <path d="M-3.5-5.5L5.5 0-3.5 5.5Z" />
                  </g>
                );
              })}
          </g>

          {/* 节点 */}
          <g className="nodes" filter={`url(#${uid}-lift)`}>
            {w.scene.nodes.map((node) => {
              if (node.kind !== "junction") return null;
              const q = T(positions.get(node.id)!);
              const lv = level(node.id);
              const r =
                lv === "bad" ? 6.6 : lv === "low" ? 5.6 : node.critical ? 5.2 : 4.6;
              return (
                <g key={node.id}>
                  {violating.has(node.id) && (
                    <circle className="viol-ring" cx={q.x} cy={q.y} r={r + 3.6} />
                  )}
                  <circle className={`node-dot lv-${lv}`} data-id={node.id} cx={q.x} cy={q.y} r={r} />
                </g>
              );
            })}
          </g>

          {/* 水源、水箱：图标徽标 + 名称和水位 */}
          <g className="facilities">
            {w.scene.nodes.map((node) => {
              if (node.kind === "junction") return null;
              const q = T(positions.get(node.id)!);
              const tank = tanks.get(node.id);
              const tone =
                (entity.kind === "node" && entity.id === node.id) ||
                (hover?.kind === "node" && hover.id === node.id)
                  ? "selected"
                  : "";
              return (
                <g key={node.id} className={`facility-badge ${node.kind} ${tone}`}>
                  <g transform={`translate(${q.x} ${q.y})`} filter={`url(#${uid}-lift)`}>
                    <rect x="-12" y="-12" width="24" height="24" rx={node.kind === "tank" ? 6 : 12} />
                    {node.kind === "tank" ? (
                      <path d="M-5.5-4.5h11v9h-11z M-5.5-1h11 M-2.5 4.5v2.5 M2.5 4.5v2.5" />
                    ) : (
                      <path d="M-6.5-1.5c2-2 3.5-2 5.5 0s3.5 2 5.5 0 M-6.5 3c2-2 3.5-2 5.5 0s3.5 2 5.5 0" />
                    )}
                  </g>
                  {pill(
                    q.x + 17,
                    q.y,
                    node.kind === "tank" ? `水箱 ${node.id}` : `水源 ${node.id}`,
                    node.kind === "tank" && tank && valid
                      ? `${num(tank.level_m, 2)} m`
                      : null,
                    tone,
                  )}
                </g>
              );
            })}
          </g>

          {/* 编号：放大到一定程度或打开「全部编号」才显示，避免密集处糊成一片 */}
          {showNodeIds && (
            <g className="node-ids">
              {w.scene.nodes.map((node) => {
                if (node.kind !== "junction") return null;
                const q = T(positions.get(node.id)!);
                return (
                  <text key={node.id} className="map-label" x={q.x + 8} y={q.y - 8}>
                    {node.id}
                  </text>
                );
              })}
            </g>
          )}

          {/* 故障点与漏水点 */}
          {faultActive && faultWorld && fault && (
            <g className="fault-mark" transform={`translate(${T(faultWorld).x} ${T(faultWorld).y})`}>
              <circle className="fault-pulse" r="14" />
              <circle className="fault-core" r="12" />
              <path d="M-4-4L4 4M4-4L-4 4" />
              <g className="fault-tag" transform="translate(18 -15)">
                <rect
                  width={
                    24 +
                    textWidth(`故障 · 管段 ${fault.link_id}`) +
                    (leakTotal > 0 ? textWidth(` 漏水 ${num(leakTotal * 1000, 1)} L/s`) : 0)
                  }
                  height="30"
                  rx="15"
                />
                <text x="12" y="19.5">
                  故障 · 管段 {fault.link_id}
                  {leakTotal > 0 && (
                    <tspan className="fault-tag-value"> 漏水 {num(leakTotal * 1000, 1)} L/s</tspan>
                  )}
                </text>
              </g>
            </g>
          )}
          {faultWorld &&
            leaks.map((lk, i) => {
              const q = T(faultWorld);
              return (
                <circle
                  key={`${lk.node_id}-${i}`}
                  className="leak-drop"
                  cx={q.x + (i - (leaks.length - 1) / 2) * 10}
                  cy={q.y + 18}
                  r="3.6"
                />
              );
            })}

          {/* 悬停与选中 */}
          {hover?.kind === "node" &&
            (() => {
              const n = w.scene.nodes.find((x) => x.id === hover.id);
              if (!n) return null;
              const q = T(positions.get(n.id)!);
              return <circle className="hover-ring" cx={q.x} cy={q.y} r={n.kind === "junction" ? 10 : 17} />;
            })()}
          {selNode &&
            (() => {
              const q = T(positions.get(selNode.id)!);
              const s = samples.get(selNode.id);
              const text = `节点 ${selNode.id}`,
                value = s && valid ? `${num(s.pressure_m)} m` : null;
              const at = placePill(q, pillWidth(text, value));
              return (
                <g key={`sel-${selNode.id}`} className="sel-mark">
                  <circle className="sel-ping" cx={q.x} cy={q.y} r={selNode.kind === "junction" ? 12 : 19} />
                  {selNode.kind === "junction" && pill(at.x, at.y, text, value, "selected")}
                </g>
              );
            })()}
        </svg>
        {hoverInfo && !dragging && (
          <div
            className="map-tooltip"
            ref={tipRef}
            style={{ transform: `translate(${tipPos.current.x}px, ${tipPos.current.y}px)` }}
            role="status"
          >
            <strong>{hoverInfo.title}</strong>
            {hoverInfo.rows.map(([k, v, tone]) => (
              <div key={k}>
                <span>{k}</span>
                <b className={tone ? `tone-${tone}` : ""}>{v}</b>
              </div>
            ))}
            {hoverInfo.note && <p className="map-tooltip-note">{hoverInfo.note}</p>}
            <p className="map-tooltip-hint">点击查看详情与曲线</p>
          </div>
        )}
        {hint && (
          <div className="map-hint" role="status">
            点击地图后可用滚轮缩放（或按住 Ctrl 滚动）
          </div>
        )}
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
                管越粗流量越大
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
                <i className="dot green" />≥ {num(c.task.hard_constraints.min_pressure_m, 0)} m
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
            </>
          )}
          <span className="map-legend-help">点击后滚轮缩放 · 拖动平移 · 双击放大</span>
        </div>
        <span className="map-note">坐标为示意布局，不代表实际地理位置或距离</span>
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
