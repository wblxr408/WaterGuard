import type { Workspace } from "./data";

/* 首页背景舞台：把真实管网画成深色背景上的发光「全息沙盘」。
   只读当前接口结果（拓扑、各方案逐帧压力与流量、故障、动作、剩余供水缺口节点）；
   镜头和状态由外部给目标值，这里每帧按时间常数平滑逼近，滚动再快画面也是连续的。 */

export interface StageFrame {
  t: number;
  d: number;
  /** [方案 A, 方案 B] 各节点压力（缺数据为 NaN） */
  p: [Float32Array, Float32Array];
  /** [方案 A, 方案 B] 各管段流量（m³/s，正为起点→终点） */
  q: [Float32Array, Float32Array];
  /** [方案 A, 方案 B] 各管段是否过水 */
  open: [Uint8Array, Uint8Array];
}
export interface StageData {
  nodes: { id: string; kind: string; x: number; y: number }[];
  links: { id: string; kind: string; a: number; b: number; len: number }[];
  frames: StageFrame[];
  minP: number;
  fault: { link: number; start: number; end: number } | null;
  actionLinks: number[];
  violationNodes: number[];
  qmax: number;
}

export function buildStageData(w: Workspace): StageData | null {
  const A = w.candidates[0],
    B = w.candidates.at(-1)!;
  const fa = A.series?.frames,
    fb = B.series?.frames;
  if (!fa?.length || !fb?.length) return null;
  const xs = w.scene.nodes.map((n) => n.x_m),
    ys = w.scene.nodes.map((n) => n.y_m);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2,
    cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  const s =
    Math.max(
      Math.max(...xs) - Math.min(...xs),
      Math.max(...ys) - Math.min(...ys),
    ) / 2 || 1;
  const nodeIndex = new Map(w.scene.nodes.map((n, i) => [n.id, i]));
  const nodes = w.scene.nodes.map((n) => ({
    id: n.id,
    kind: n.kind,
    x: (n.x_m - cx) / s,
    y: (n.y_m - cy) / s,
  }));
  const links = w.scene.links
    .filter((l) => nodeIndex.has(l.from_node) && nodeIndex.has(l.to_node))
    .map((l) => {
      const a = nodeIndex.get(l.from_node)!,
        b = nodeIndex.get(l.to_node)!;
      return {
        id: l.id,
        kind: l.kind,
        a,
        b,
        len: Math.hypot(nodes[a].x - nodes[b].x, nodes[a].y - nodes[b].y),
      };
    });
  const linkIndex = new Map(links.map((l, i) => [l.id, i]));
  const sample = (frames: typeof fa, i: number) => {
    const f = frames[Math.min(i, frames.length - 1)];
    const p = new Float32Array(nodes.length).fill(NaN);
    for (const n of f.nodes) {
      const k = nodeIndex.get(n.node_id);
      if (k != null) p[k] = n.pressure_m;
    }
    const q = new Float32Array(links.length);
    const open = new Uint8Array(links.length).fill(1);
    for (const l of f.links) {
      const k = linkIndex.get(l.link_id);
      if (k == null) continue;
      q[k] = l.flow_m3s;
      open[k] = l.status === "open" ? 1 : 0;
    }
    return { p, q, open };
  };
  let qmax = 1e-6;
  const frames: StageFrame[] = fb.map((f, i) => {
    const a = sample(fa, i),
      b = sample(fb, i);
    for (const v of [...a.q, ...b.q]) qmax = Math.max(qmax, Math.abs(v));
    return {
      t: f.time_s,
      d: f.duration_s,
      p: [a.p, b.p],
      q: [a.q, b.q],
      open: [a.open, b.open],
    };
  });
  const fault = B.task.fault;
  return {
    nodes,
    links,
    frames,
    minP: B.task.hard_constraints.min_pressure_m,
    fault:
      fault && linkIndex.has(fault.link_id)
        ? {
            link: linkIndex.get(fault.link_id)!,
            start: fault.start_s,
            end: fault.end_s,
          }
        : null,
    actionLinks: B.plan.actions.flatMap((a) =>
      linkIndex.has(a.link_id) ? [linkIndex.get(a.link_id)!] : [],
    ),
    violationNodes: [
      ...new Set(
        B.verification.violations.flatMap((v) =>
          nodeIndex.has(v.node_id) ? [nodeIndex.get(v.node_id)!] : [],
        ),
      ),
    ],
    qmax,
  };
}

export interface StageView {
  /** 透视倾角（度）：0 俯视，60 像全息沙盘 */
  tilt: number;
  zoom: number;
  /** 镜头对准的点（管网归一化坐标） */
  fx: number;
  fy: number;
  /** 画面中心相对视口宽度的水平偏移（文字在左时把管网推到右边） */
  shift: number;
  /** 画面中心相对视口高度的竖直偏移（窄屏文字在上或在下时让开） */
  lift: number;
  /** 仿真时刻（秒） */
  time: number;
  /** 0 = 方案 A，1 = 方案 B，中间值为两者混合 */
  plan: number;
  /** 故障警报（冲击波、红色脉冲）强度 */
  alarm: number;
  dim: number;
  pump: number;
  violations: number;
  scan: number;
  labels: number;
  /** 0~1：管线从中心向外「点亮」 */
  boot: number;
}

const DEFAULT: StageView = {
  tilt: 58,
  zoom: 1,
  fx: 0,
  fy: 0,
  shift: 0.16,
  lift: 0,
  time: 0,
  plan: 0,
  alarm: 0,
  dim: 0,
  pump: 0,
  violations: 0,
  scan: 1,
  labels: 0,
  boot: 0,
};

const COLORS = {
  ok: [70, 214, 255],
  low: [255, 181, 71],
  bad: [255, 77, 94],
  pipe: [92, 150, 255],
};

function sprite(rgb: number[], size = 48) {
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const g = c.getContext("2d")!;
  const grad = g.createRadialGradient(
    size / 2,
    size / 2,
    0,
    size / 2,
    size / 2,
    size / 2,
  );
  grad.addColorStop(0, `rgba(${rgb},1)`);
  grad.addColorStop(0.18, `rgba(${rgb},0.85)`);
  grad.addColorStop(0.45, `rgba(${rgb},0.22)`);
  grad.addColorStop(1, `rgba(${rgb},0)`);
  g.fillStyle = grad;
  g.fillRect(0, 0, size, size);
  return c;
}

export class StageRenderer {
  view: StageView = { ...DEFAULT };
  target: StageView = { ...DEFAULT };
  /** 每帧画完后回调，外部用它更新计数器等文字（不触发 React 重渲） */
  onFrame: ((v: StageView, d: StageData | null) => void) | null = null;
  /** 画布完全透明时置 true：照常插值和回调，但不画（开场时把性能留给渲染图的动画） */
  hidden = false;
  private ctx: CanvasRenderingContext2D;
  private data: StageData | null = null;
  private shown: Float32Array = new Float32Array(0);
  private particles: { link: number; u: number }[] = [];
  private dust: { x: number; y: number; r: number; ph: number }[] = [];
  private glow = {
    ok: sprite(COLORS.ok),
    low: sprite(COLORS.low),
    bad: sprite(COLORS.bad),
  };
  private raf = 0;
  private last = 0;
  private w = 0;
  private h = 0;
  private dpr = 1;

  constructor(
    private canvas: HTMLCanvasElement,
    private reduce: boolean,
  ) {
    this.ctx = canvas.getContext("2d")!;
    for (let i = 0; i < 140; i++)
      this.dust.push({
        x: Math.random(),
        y: Math.random(),
        r: Math.random() * 1.2 + 0.3,
        ph: Math.random() * 6.28,
      });
  }

  setData(d: StageData | null) {
    this.data = d;
    if (!d) return;
    this.shown = new Float32Array(d.nodes.length).fill(NaN);
    this.particles = [];
    d.links.forEach((l, i) => {
      const n = Math.max(1, Math.min(10, Math.round(l.len / 0.05)));
      for (let k = 0; k < n; k++)
        this.particles.push({ link: i, u: (k + Math.random() * 0.6) / n });
    });
  }

  setTarget(v: Partial<StageView>) {
    Object.assign(this.target, v);
  }

  /** 立即跳到目标（首次进入或减少动画时） */
  snap() {
    this.view = { ...this.target };
  }

  start() {
    if (this.raf) return;
    this.last = performance.now();
    const loop = (now: number) => {
      this.raf = requestAnimationFrame(loop);
      const dt = Math.min(0.05, (now - this.last) / 1000);
      this.last = now;
      this.step(dt, now / 1000);
    };
    this.raf = requestAnimationFrame(loop);
  }

  stop() {
    cancelAnimationFrame(this.raf);
    this.raf = 0;
  }

  private resize() {
    const dpr = Math.min(1.75, devicePixelRatio || 1);
    const w = this.canvas.clientWidth,
      h = this.canvas.clientHeight;
    if (w !== this.w || h !== this.h || dpr !== this.dpr) {
      this.w = w;
      this.h = h;
      this.dpr = dpr;
      this.canvas.width = Math.round(w * dpr);
      this.canvas.height = Math.round(h * dpr);
    }
  }

  private step(dt: number, now: number) {
    // 指数逼近：与帧率无关，约 0.25 s 走完大部分距离；减少动画时直接到位
    const k = this.reduce ? 1 : 1 - Math.exp(-dt * 5.5);
    const v = this.view,
      t = this.target;
    for (const key of Object.keys(t) as (keyof StageView)[])
      v[key] += (t[key] - v[key]) * k;
    this.resize();
    if (!this.hidden) this.draw(now, dt);
    this.onFrame?.(v, this.data);
  }

  /** 管网归一化坐标 → 屏幕坐标，返回 [x, y, 透视缩放] */
  private project(X: number, Y: number): [number, number, number] {
    const v = this.view;
    const S = Math.min(this.w, this.h) * 0.42 * v.zoom;
    const x = X - v.fx,
      y = Y - v.fy;
    const a = (v.tilt * Math.PI) / 180;
    const ty = y * Math.cos(a),
      tz = y * Math.sin(a);
    const f = 3 / (3 + tz);
    return [
      this.w * (0.5 + v.shift) + x * f * S,
      this.h * (0.52 + v.lift) - ty * f * S,
      f,
    ];
  }

  private frameAt(time: number) {
    const fs = this.data!.frames;
    let i = 0;
    while (i < fs.length - 1 && fs[i + 1].t <= time) i++;
    return fs[i];
  }

  private draw(now: number, dt: number) {
    const ctx = this.ctx,
      v = this.view,
      d = this.data;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.w, this.h);
    // 星尘
    ctx.globalCompositeOperation = "lighter";
    for (const s of this.dust) {
      const tw = this.reduce ? 0.5 : 0.35 + 0.35 * Math.sin(now * 0.8 + s.ph);
      ctx.fillStyle = `rgba(150,180,255,${tw * 0.5})`;
      ctx.fillRect(s.x * this.w, s.y * this.h, s.r, s.r);
    }
    this.drawGrid();
    if (!d) return;
    const dim = 1 - 0.62 * v.dim;
    const frame = this.frameAt(v.time);
    const plan = v.plan;
    // 节点压力：两方案按 plan 混合，再平滑逼近，颜色过渡不跳
    for (let i = 0; i < d.nodes.length; i++) {
      const pa = frame.p[0][i],
        pb = frame.p[1][i];
      const target = Number.isNaN(pa)
        ? pb
        : Number.isNaN(pb)
          ? pa
          : pa + (pb - pa) * plan;
      const cur = this.shown[i];
      this.shown[i] = Number.isNaN(cur)
        ? target
        : cur + (target - cur) * (this.reduce ? 1 : 1 - Math.exp(-dt * 4));
    }
    const pts = d.nodes.map((n) => this.project(n.x, n.y));
    const bootR = v.boot * 1.7;
    const faultOn = d.fault && v.time >= d.fault.start && v.time < d.fault.end;
    // 管线：先画宽而淡的辉光，再画细亮的芯
    for (const pass of [0, 1]) {
      for (let i = 0; i < d.links.length; i++) {
        const l = d.links[i];
        const r = Math.hypot(
          (d.nodes[l.a].x + d.nodes[l.b].x) / 2,
          (d.nodes[l.a].y + d.nodes[l.b].y) / 2,
        );
        if (r > bootR) continue;
        const [x1, y1] = pts[l.a],
          [x2, y2] = pts[l.b];
        const isFault = d.fault?.link === i;
        const open = plan < 0.5 ? frame.open[0][i] : frame.open[1][i];
        let rgb = COLORS.pipe,
          alpha = pass ? 0.7 : 0.16,
          width = pass ? 1.15 : 4.5;
        if (l.kind === "pump") {
          rgb = COLORS.ok;
          width = pass ? 2.2 : 7;
          alpha = pass ? 0.9 : 0.2;
        }
        if (isFault && faultOn) {
          rgb = COLORS.bad;
          const pulse = 0.55 + 0.45 * Math.sin(now * 6);
          alpha =
            (pass ? 0.95 : 0.35) *
            (0.4 + 0.6 * v.alarm * pulse + 0.6 * (1 - v.alarm));
          width = pass ? 2.4 : 10;
        } else if (!open) {
          alpha *= 0.3;
        }
        const edge = Math.min(1, (bootR - r) * 4);
        ctx.strokeStyle = `rgba(${rgb},${alpha * dim * edge})`;
        ctx.lineWidth = width * ((pts[l.a][2] + pts[l.b][2]) / 2);
        ctx.setLineDash(isFault && faultOn && pass ? [4, 4] : []);
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();
      }
    }
    ctx.setLineDash([]);
    // 水流粒子：方向取计算流量的符号，速度按相对流量（示意）
    const q = plan < 0.5 ? frame.q[0] : frame.q[1];
    const open = plan < 0.5 ? frame.open[0] : frame.open[1];
    for (const p of this.particles) {
      const l = d.links[p.link];
      const flow = q[p.link];
      if (!open[p.link] || Math.abs(flow) < d.qmax * 0.004) continue;
      const r = Math.hypot(
        (d.nodes[l.a].x + d.nodes[l.b].x) / 2,
        (d.nodes[l.a].y + d.nodes[l.b].y) / 2,
      );
      if (r > bootR) continue;
      if (!this.reduce) {
        const speed =
          (0.06 + 0.5 * Math.sqrt(Math.abs(flow) / d.qmax)) /
          Math.max(0.02, l.len);
        p.u = (p.u + Math.sign(flow) * speed * dt * 0.25 + 1) % 1;
      }
      const [x1, y1, f1] = pts[l.a],
        [x2, y2] = pts[l.b];
      const x = x1 + (x2 - x1) * p.u,
        y = y1 + (y2 - y1) * p.u;
      const size = 7 * f1;
      ctx.globalAlpha = 0.85 * dim;
      ctx.drawImage(this.glow.ok, x - size / 2, y - size / 2, size, size);
    }
    ctx.globalAlpha = 1;
    // 节点
    for (let i = 0; i < d.nodes.length; i++) {
      const n = d.nodes[i];
      if (Math.hypot(n.x, n.y) > bootR) continue;
      const pr = this.shown[i];
      const level = Number.isNaN(pr)
        ? "ok"
        : pr < 0
          ? "bad"
          : pr < d.minP
            ? "low"
            : "ok";
      const [x, y, f] = pts[i];
      const big = n.kind !== "junction";
      const size = (big ? 22 : level === "ok" ? 11 : 17) * f;
      ctx.globalAlpha = (level === "ok" ? 0.75 : 1) * dim;
      ctx.drawImage(this.glow[level], x - size / 2, y - size / 2, size, size);
      if (big) {
        ctx.globalAlpha = dim;
        ctx.strokeStyle = "rgba(200,225,255,0.8)";
        ctx.lineWidth = 1;
        ctx.strokeRect(x - 4.5, y - 4.5, 9, 9);
      }
    }
    ctx.globalAlpha = 1;
    // 故障冲击波
    if (d.fault && v.alarm > 0.02 && !this.reduce) {
      const l = d.links[d.fault.link];
      const mx = (d.nodes[l.a].x + d.nodes[l.b].x) / 2,
        my = (d.nodes[l.a].y + d.nodes[l.b].y) / 2;
      for (let k = 0; k < 3; k++) {
        const ph = (now * 0.55 + k / 3) % 1;
        this.ring(
          mx,
          my,
          0.05 + ph * 0.75,
          `rgba(${COLORS.bad},${(1 - ph) * 0.55 * v.alarm})`,
          1.6,
        );
      }
    }
    // 开场扫描环
    if (v.scan > 0.02 && !this.reduce) {
      const ph = (now * 0.18) % 1;
      this.ring(
        0,
        0,
        0.1 + ph * 1.5,
        `rgba(${COLORS.ok},${(1 - ph) * 0.28 * v.scan})`,
        1.2,
      );
    }
    ctx.globalCompositeOperation = "source-over";
    // 动作设施的瞄准框
    if (v.pump > 0.02)
      for (const li of d.actionLinks) {
        const l = d.links[li];
        const [x1, y1] = pts[l.a],
          [x2, y2] = pts[l.b];
        this.reticle(
          (x1 + x2) / 2,
          (y1 + y2) / 2,
          now,
          v.pump,
          `${l.kind === "pump" ? "水泵" : "管段"} ${l.id}`,
        );
      }
    // 剩余供水缺口节点红圈
    if (v.violations > 0.02)
      for (const ni of d.violationNodes) {
        const [x, y] = pts[ni];
        const r = 11 + (this.reduce ? 0 : 3 * Math.sin(now * 4 + ni));
        ctx.strokeStyle = `rgba(${COLORS.bad},${0.9 * v.violations})`;
        ctx.lineWidth = 1.6;
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.stroke();
        this.label(
          x + 14,
          y - 10,
          `节点 ${d.nodes[ni].id}`,
          `rgba(255,170,178,${v.violations})`,
        );
      }
    // 事故管段与设施标签
    if (v.labels > 0.02) {
      if (d.fault) {
        const l = d.links[d.fault.link];
        const [x1, y1] = pts[l.a],
          [x2, y2] = pts[l.b];
        this.label(
          (x1 + x2) / 2 + 12,
          (y1 + y2) / 2 + 18,
          `管段 ${l.id} · 停止过水`,
          `rgba(255,150,160,${v.labels})`,
        );
      }
      d.nodes.forEach((n, i) => {
        if (n.kind === "junction") return;
        const [x, y] = pts[i];
        this.label(
          x + 9,
          y - 9,
          `${n.kind === "tank" ? "水箱" : "水源"} ${n.id}`,
          `rgba(190,215,255,${0.75 * v.labels * dim})`,
        );
      });
    }
  }

  private drawGrid() {
    const ctx = this.ctx,
      v = this.view;
    const a = Math.min(1, v.tilt / 50);
    if (a < 0.05) return;
    ctx.lineWidth = 1;
    for (let i = -8; i <= 8; i++) {
      const g = i / 5;
      const fade = 0.07 * a * (1 - Math.abs(i) / 9);
      ctx.strokeStyle = `rgba(90,140,255,${fade})`;
      const [ax, ay] = this.project(g, -1.7),
        [bx, by] = this.project(g, 1.7);
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.stroke();
      const [cx, cy] = this.project(-1.7, g),
        [dx, dy] = this.project(1.7, g);
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(dx, dy);
      ctx.stroke();
    }
  }

  private ring(X: number, Y: number, r: number, color: string, width: number) {
    const ctx = this.ctx;
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.beginPath();
    for (let k = 0; k <= 64; k++) {
      const t = (k / 64) * Math.PI * 2;
      const [x, y] = this.project(X + r * Math.cos(t), Y + r * Math.sin(t));
      if (k) ctx.lineTo(x, y);
      else ctx.moveTo(x, y);
    }
    ctx.stroke();
  }

  private reticle(x: number, y: number, now: number, k: number, text: string) {
    const ctx = this.ctx;
    const s = 16 + (this.reduce ? 0 : 3 * Math.sin(now * 3));
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(this.reduce ? 0 : now * 0.6);
    ctx.strokeStyle = `rgba(255,190,90,${k})`;
    ctx.lineWidth = 2;
    for (let q = 0; q < 4; q++) {
      ctx.rotate(Math.PI / 2);
      ctx.beginPath();
      ctx.moveTo(s, s - 7);
      ctx.lineTo(s, s);
      ctx.lineTo(s - 7, s);
      ctx.stroke();
    }
    ctx.restore();
    this.label(x + s + 10, y + 4, text, `rgba(255,205,130,${k})`);
  }

  private label(x: number, y: number, text: string, color: string) {
    const ctx = this.ctx;
    ctx.font = '12px "Cascadia Mono", Consolas, monospace';
    ctx.fillStyle = color;
    ctx.fillText(text, x, y);
  }
}
