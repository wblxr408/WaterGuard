import { useEffect, useRef } from "react";
import type { CSSProperties } from "react";
import { prefersReducedMotion } from "./motion";

/* 闸阀拆解（滚动固定）：Blender 72 帧序列。
   滚动只给目标进度，画面在 rAF 里按时间常数平滑逼近；相邻两帧交叉过渡，不会一格一格跳；
   标注和引线每帧直接改 DOM，不触发 React 重渲。后半段换成青色全息线稿，按关阀的先后顺序讲五个零件。 */

interface Anchors {
  frames: number;
  width: number;
  height: number;
  parts: string[];
  points: [number, number][][];
}

const PARTS = [
  {
    id: "handwheel",
    name: "手轮",
    spec: "Ø480 · 红色环氧",
    side: "r",
    step: 1,
    act: "转动手轮",
    why: "人工或电动执行器施加扭矩",
  },
  {
    id: "stem_nut",
    name: "阀杆螺母",
    spec: "青铜 · 梯形螺纹",
    side: "r",
    step: 2,
    act: "螺母把转动变成升降",
    why: "手轮带螺母转，阀杆不转只走直线",
  },
  { id: "yoke", name: "支架", spec: "明杆结构，阀杆外露", side: "r", step: 0 },
  { id: "gland", name: "填料压盖", spec: "两颗压盖螺栓", side: "r", step: 0 },
  { id: "bonnet_bolts", name: "阀盖螺栓", spec: "8 × M20", side: "r", step: 0 },
  { id: "bonnet", name: "阀盖", spec: "圆顶 · 8 孔法兰", side: "l", step: 0 },
  { id: "gasket", name: "阀盖垫片", spec: "橡胶密封", side: "l", step: 0 },
  {
    id: "stem",
    name: "阀杆",
    spec: "梯形螺纹",
    side: "l",
    step: 3,
    act: "阀杆下行",
    why: "带着闸板一起往下走",
  },
  {
    id: "wedge",
    name: "闸板",
    spec: "楔式 · 两面铜密封圈",
    side: "l",
    step: 4,
    act: "闸板落位",
    why: "楔面压紧两侧铜密封圈",
  },
  {
    id: "body",
    name: "阀体",
    spec: "DN300 · 端法兰 8 孔",
    side: "l",
    step: 5,
    act: "通孔被堵住",
    why: "管段流量为 0，就是仿真里的「关闭管段」",
  },
] as const;
const RIGHT = PARTS.filter((p) => p.side === "r"),
  LEFT = PARTS.filter((p) => p.side === "l");
/** 每个零件在所在一侧的序号，用于排标注和错开淡入 */
const ORDER = new Map<string, number>([
  ...RIGHT.map((p, i) => [p.id, i] as [string, number]),
  ...LEFT.map((p, i) => [p.id, i] as [string, number]),
]);
const N_DEFAULT = 72;
const frameUrl = (i: number) =>
  `/cover/valve/f${String(i).padStart(2, "0")}.webp`;
const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const smooth = (t: number) => t * t * (3 - 2 * t);
const circled = ["", "①", "②", "③", "④", "⑤"];

export function CoverTeardown() {
  const sectionRef = useRef<HTMLElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const copyRef = useRef<HTMLDivElement>(null);
  const rulerRef = useRef<HTMLDivElement>(null);
  const captionRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const section = sectionRef.current,
      stage = stageRef.current,
      canvas = canvasRef.current,
      svg = svgRef.current;
    if (!section || !stage || !canvas || !svg) return;
    const reduce = prefersReducedMotion();
    const ctx = canvas.getContext("2d")!;
    let anchors: Anchors | null = null;
    const frames: (HTMLImageElement | null)[] = Array(N_DEFAULT).fill(null);
    let holo: HTMLCanvasElement | null = null;
    let alive = true;

    // 载入：标注点数据 + 全部序列帧（先解码完再用，避免第一次画时卡顿）+ 线稿染成青色发光
    fetch("/cover/valve/anchors.json")
      .then((r) => r.json())
      .then((a: Anchors) => {
        if (!alive) return;
        anchors = a;
        frames.length = a.frames;
        const order = [
          0,
          a.frames - 1,
          ...Array.from({ length: a.frames - 2 }, (_, i) => i + 1),
        ];
        for (const i of order) {
          const img = new Image();
          img.src = frameUrl(i);
          img
            .decode()
            .then(() => alive && (frames[i] = img))
            .catch(() => undefined);
        }
        const l = new Image();
        l.src = "/cover/valve/lines.webp";
        l.decode()
          .then(() => {
            if (!alive) return;
            const c = document.createElement("canvas");
            c.width = l.naturalWidth;
            c.height = l.naturalHeight;
            const g = c.getContext("2d")!;
            g.drawImage(l, 0, 0);
            g.globalCompositeOperation = "source-in";
            g.fillStyle = "#7fe3ff";
            g.fillRect(0, 0, c.width, c.height);
            holo = c;
          })
          .catch(() => undefined);
      })
      .catch(() => undefined);

    const leaders = new Map<
      string,
      { line: SVGPolylineElement; dot: SVGCircleElement }
    >();
    for (const g of svg.querySelectorAll<SVGGElement>("g[data-id]"))
      leaders.set(g.dataset.id!, {
        line: g.querySelector("polyline")!,
        dot: g.querySelector("circle")!,
      });
    const labels = new Map<string, HTMLElement>();
    for (const li of stage.querySelectorAll<HTMLElement>("li[data-id]"))
      labels.set(li.dataset.id!, li);

    let p = reduce ? 1 : 0,
      last = performance.now(),
      raf = 0,
      w = 0,
      h = 0;
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const r = section.getBoundingClientRect();
      const span = r.height - innerHeight;
      // 减少动画：直接停在全息线稿 + 关阀五步
      const target = reduce ? 1 : clamp(span > 0 ? -r.top / span : 0);
      p += (target - p) * (reduce ? 1 : 1 - Math.exp(-dt * 6));
      // 不在视口附近时不画
      if (r.bottom < -innerHeight || r.top > innerHeight * 2) return;
      const dpr = Math.min(1.75, devicePixelRatio || 1);
      if (stage.clientWidth !== w || stage.clientHeight !== h) {
        w = stage.clientWidth;
        h = stage.clientHeight;
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
      }
      const compact = w < 760;
      const N = anchors?.frames ?? N_DEFAULT;
      const explode = clamp((p - 0.04) / 0.5);
      const holoT = clamp((p - 0.58) / 0.12);
      const chain = clamp((p - 0.7) / 0.26);
      const iw = anchors?.width ?? 760,
        ih = anchors?.height ?? 1400;
      const zoom = 1 + 0.55 * (1 - smooth(clamp(explode / 0.45)));
      const scale = ((h * (compact ? 0.56 : 0.98)) / ih) * zoom;
      const cx = w * 0.5,
        cy = h * (compact ? 0.49 : 0.52);
      const theta = ((compact ? 12 : 30) * smooth(explode) * Math.PI) / 180;
      const fi = explode * (N - 1),
        i0 = Math.floor(fi),
        i1 = Math.min(N - 1, i0 + 1),
        frac = fi - i0;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.translate(cx, cy);
      ctx.rotate(theta);
      const dw = iw * scale,
        dh = ih * scale;
      const pick = (i: number) => {
        for (let d = 0; d < N; d++) {
          const img = frames[i - d] ?? frames[i + d];
          if (img) return img;
        }
        return null;
      };
      const a0 = pick(i0),
        a1 = pick(i1);
      const shaded = 1 - holoT * 0.88;
      if (a0) {
        ctx.globalAlpha = shaded;
        ctx.drawImage(a0, -dw / 2, -dh / 2, dw, dh);
      }
      if (a1 && a1 !== a0 && frac > 0.01) {
        ctx.globalAlpha = shaded * frac;
        ctx.drawImage(a1, -dw / 2, -dh / 2, dw, dh);
      }
      if (holo && holoT > 0) {
        ctx.globalCompositeOperation = "lighter";
        ctx.globalAlpha = holoT;
        ctx.shadowColor = "rgba(90,210,255,0.9)";
        ctx.shadowBlur = 10;
        ctx.drawImage(holo, -dw / 2, -dh / 2, dw, dh);
        ctx.shadowBlur = 0;
        ctx.globalCompositeOperation = "source-over";
      }
      ctx.globalAlpha = 1;
      // 标注：位置跟着零件走（相邻两帧的标注点也做插值）
      const active = chain > 0 ? Math.min(5, 1 + Math.floor(chain * 5)) : 0;
      stage.style.setProperty("--holo", holoT.toFixed(3));
      copyRef.current?.classList.toggle("second", holoT > 0.5);
      const show = explode > 0.16 || reduce;
      const pts0 = anchors?.points[Math.min(i0, anchors.points.length - 1)],
        pts1 = anchors?.points[Math.min(i1, anchors.points.length - 1)];
      const pad = Math.max(40, (w - 1500) / 2);
      for (const part of PARTS) {
        const k = anchors?.parts.indexOf(part.id) ?? -1;
        const lead = leaders.get(part.id),
          li = labels.get(part.id);
        if (!lead || !li) continue;
        const on = show && !compact && k >= 0 && pts0 && pts1;
        li.classList.toggle("on", !!on);
        lead.line.parentElement!.classList.toggle("on", !!on);
        const chainPart = part.step > 0;
        li.classList.toggle("chain", holoT > 0.5 && chainPart);
        li.classList.toggle("muted", holoT > 0.5 && !chainPart);
        li.classList.toggle("now", active > 0 && part.step === active);
        lead.line.parentElement!.classList.toggle(
          "now",
          active > 0 && part.step === active,
        );
        if (!on) continue;
        const u = pts0![k][0] + (pts1![k][0] - pts0![k][0]) * frac,
          v = pts0![k][1] + (pts1![k][1] - pts0![k][1]) * frac;
        const x = (u - 0.5) * dw,
          y = (v - 0.5) * dh;
        const ax = cx + x * Math.cos(theta) - y * Math.sin(theta),
          ay = cy + x * Math.sin(theta) + y * Math.cos(theta);
        const idx = ORDER.get(part.id) ?? 0;
        const ly =
          h * (part.side === "r" ? 0.17 + idx * 0.065 : 0.58 + idx * 0.065);
        const edge = part.side === "r" ? w - pad - 270 : pad + 270;
        const knee =
          part.side === "r"
            ? Math.max(ax + 30, w * 0.6)
            : Math.min(ax - 30, w * 0.4);
        lead.line.setAttribute(
          "points",
          `${edge},${ly} ${knee},${ly} ${ax.toFixed(1)},${ay.toFixed(1)}`,
        );
        lead.dot.setAttribute("cx", ax.toFixed(1));
        lead.dot.setAttribute("cy", ay.toFixed(1));
        li.style.top = `${ly}px`;
        li.style[part.side === "r" ? "right" : "left"] = `${pad}px`;
      }
      // 进度尺与窄屏字幕
      rulerRef.current?.style.setProperty("--p", p.toFixed(4));
      const ruler = rulerRef.current?.querySelector<HTMLElement>(".ruler-text");
      if (ruler) {
        const txt = !anchors
          ? "载入中"
          : holoT > 0.5
            ? active
              ? `关阀 ${active}/5`
              : "全息线稿"
            : `拆解 ${Math.round(explode * 100)}%`;
        if (ruler.textContent !== txt) ruler.textContent = txt;
      }
      const cap = captionRef.current;
      if (cap && compact) {
        const part =
          holoT > 0.5
            ? PARTS.find((q) => q.step === Math.max(1, active))!
            : PARTS[
                Math.min(PARTS.length - 1, Math.floor(explode * PARTS.length))
              ];
        const html =
          holoT > 0.5 && "act" in part
            ? `<b>${circled[part.step]} ${part.act}</b><span>${part.why}</span>`
            : `<b>${part.name}</b><span>${part.spec}</span>`;
        if (cap.dataset.html !== html) {
          cap.innerHTML = html;
          cap.dataset.html = html;
        }
      }
    };
    raf = requestAnimationFrame(loop);
    return () => {
      alive = false;
      cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <section
      className="cv-teardown"
      ref={sectionRef}
      aria-label="闸阀拆解与关阀过程"
    >
      <div className="cv-teardown-stage" ref={stageRef}>
        <canvas ref={canvasRef} aria-hidden="true" />
        <div className="cv-teardown-copy" ref={copyRef}>
          <div className="first">
            <p className="cv-kicker">Blender 5.2 · Cycles 渲染 · 10 个零件</p>
            <h3>DN300 明杆楔式闸阀</h3>
            <p>按常见规格比例逐个零件建模。继续滚动，零件沿阀杆方向拆开。</p>
          </div>
          <div className="second">
            <p className="cv-kicker">关阀 · 五步</p>
            <h3>关阀，就是让闸板落下来</h3>
            <p>仿真里一句「关闭管段」，到了现场就是这五步。</p>
          </div>
        </div>
        <svg ref={svgRef} className="cv-leaders" aria-hidden="true">
          {PARTS.map((p) => (
            <g key={p.id} data-id={p.id}>
              <polyline />
              <circle r={3.2} />
            </g>
          ))}
        </svg>
        <ol className="cv-labels">
          {PARTS.map((p) => (
            <li
              key={p.id}
              data-id={p.id}
              className={p.side}
              style={
                {
                  "--i": (ORDER.get(p.id) ?? 0) + (p.side === "l" ? 5 : 0),
                } as CSSProperties
              }
            >
              <span className="part">
                <b>{p.name}</b>
                <span>{p.spec}</span>
              </span>
              {"act" in p && (
                <span className="step">
                  <b>
                    {circled[p.step]} {p.act}
                  </b>
                  <span>{p.why}</span>
                </span>
              )}
            </li>
          ))}
        </ol>
        <div className="cv-caption" ref={captionRef} aria-hidden="true" />
        <div className="cv-ruler" ref={rulerRef} aria-hidden="true">
          <span className="ruler-bar">
            <i />
          </span>
          <span className="ruler-text">载入中</span>
        </div>
      </div>
    </section>
  );
}
