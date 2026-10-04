import { useEffect, useRef } from "react";
import type { CSSProperties, MouseEvent } from "react";
import Icon from "./Icon";

/* 首页开场：白瓷闸阀 + 一句标题。往下滑时开场画面钉在屏幕上，闸阀拆成 10 个零件、转半圈、
   再按装配顺序装回去，滑过这一段后放开滚动，接「我们的系统」。
   序列帧、第 0 帧线稿和各帧零件位置由 tools/blender/build_hero.py（S）生成，在 public/cover/hero-valve/。 */

const DIR = "/cover/hero-valve";
const HEAD = ["一根管子断了，", "整张管网怎么救？"];
/** 标注的零件，按「靠太近时优先留谁」排序；阀杆螺母、填料压盖、阀盖螺栓、垫片太小，不标 */
const LABELS: [string, string][] = [
  ["body", "阀体"],
  ["handwheel", "手轮"],
  ["bonnet", "阀盖"],
  ["yoke", "支架"],
  ["wedge", "闸板"],
  ["stem", "阀杆"],
];

interface Seq {
  frames: number;
  parts: string[];
  /** 每帧各零件在画面上的位置（0~1，左上为原点），顺序同 parts */
  points: [number, number][][];
  /** 第 0 帧装配态的上下沿（0~1），开场“显影”从上沿扫到下部 */
  bounds0?: [number, number];
}

const clamp = (x: number, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const smoother = (x: number) => {
  const t = clamp(x);
  return t * t * t * (t * (6 * t - 15) + 10);
};
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
/** 拆开程度，与渲染脚本里镜头拉远用的是同一条曲线：0 装好 → 1 全部拆开 → 0 */
const spreadAt = (t: number) =>
  smoother((t - 0.04) / 0.36) * (1 - smoother((t - 0.6) / 0.36));
const frameUrl = (i: number) => `${DIR}/f${String(i).padStart(2, "0")}.webp`;

export default function CoverHero({
  reduce,
  onSystem,
  onWorkbench,
}: {
  reduce: boolean;
  onSystem: () => void;
  onWorkbench: (e: MouseEvent<HTMLElement>) => void;
}) {
  const ref = useRef<HTMLElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const glowRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const section = ref.current,
      stage = stageRef.current,
      glow = glowRef.current,
      canvas = canvasRef.current;
    if (!section || !stage || !glow || !canvas) return;
    const ctx = canvas.getContext("2d")!;
    const frames: (HTMLImageElement | null)[] = [];
    let seq: Seq | null = null,
      alive = true,
      dirty = true;

    fetch(`${DIR}/anchors.json`)
      .then((r) => r.json())
      .then((s: Seq) => {
        if (!alive) return;
        seq = s;
        if (s.bounds0) {
          const [top, bottom] = s.bounds0;
          stage.style.setProperty(
            "--m0",
            `${((top - 0.03) * 100).toFixed(2)}%`,
          );
          stage.style.setProperty(
            "--m1",
            `${((top + (bottom - top) * 0.78) * 100).toFixed(2)}%`,
          );
        }
        frames.length = s.frames;
        // 按顺序请求，第 0 帧（开场画面）最先到；解码完才用，滚动时不卡
        for (let i = 0; i < s.frames; i++) {
          const img = new Image();
          img.src = frameUrl(i);
          img
            .decode()
            .then(() => {
              if (!alive) return;
              frames[i] = img;
              dirty = true;
            })
            .catch(() => undefined);
        }
      })
      .catch(() => undefined);

    const labels = [...stage.querySelectorAll<HTMLElement>("[data-part]")];
    const copy = section.querySelector<HTMLElement>(".hero-copy"),
      foot = section.querySelector<HTMLElement>(".hero-foot"),
      caption = section.querySelector<HTMLElement>(".hero-caption");
    const pick = (i: number) => {
      for (let d = 0; d < frames.length; d++) {
        const f = frames[i - d] ?? frames[i + d];
        if (f) return f;
      }
      return null;
    };

    let p = 0,
      last = performance.now(),
      raf = 0,
      cw = 0,
      ch = 0,
      vw = 0,
      vh = 0;
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const r = section.getBoundingClientRect();
      if (r.bottom < 0 || r.top > innerHeight) return; // 不在视口里不画
      // 滚动只给目标值，画面按时间常数平滑逼近
      const span = r.height - innerHeight;
      const target = reduce ? 0 : clamp(span > 0 ? -r.top / span : 0);
      const resized = innerWidth !== vw || innerHeight !== vh;
      if (!dirty && !resized && Math.abs(target - p) < 1e-4) return;
      p = reduce ? target : p + (target - p) * (1 - Math.exp(-dt * 7));
      if (Math.abs(target - p) < 1e-4) p = target;
      vw = innerWidth;
      vh = innerHeight;

      const dpr = Math.min(2, devicePixelRatio || 1);
      const sw = stage.offsetWidth,
        sh = stage.offsetHeight;
      if (Math.round(sw * dpr) !== cw || Math.round(sh * dpr) !== ch) {
        cw = canvas.width = Math.round(sw * dpr);
        ch = canvas.height = Math.round(sh * dpr);
      }

      // 开场时闸阀在右侧、文字在左；一开始滑动，文字淡出，闸阀移到正中
      const narrow = vw < 760;
      const move = smoother(p / 0.12);
      const cx = narrow ? vw / 2 : lerp(vw * 0.68, vw * 0.5, move);
      // 窄屏开场：闸阀缩小放在页头下面、标题上面
      const cy = vh * (narrow ? lerp(0.385, 0.5, move) : 0.53);
      const scale = narrow ? lerp(0.6, 1, move) : lerp(0.93, 1, move);
      stage.style.transform = `translate3d(${(cx - sw / 2).toFixed(1)}px, ${(cy - sh / 2).toFixed(1)}px, 0) scale(${scale.toFixed(4)})`;
      glow.style.transform = `translate3d(${(cx - vw / 2).toFixed(1)}px, ${(cy - vh / 2).toFixed(1)}px, 0)`;
      stage.style.setProperty("--reveal", clamp(p / 0.05).toFixed(3));
      const fade = smoother(p / 0.09);
      if (copy) {
        copy.style.opacity = (1 - fade).toFixed(3);
        copy.style.transform = `translate3d(${(-36 * fade).toFixed(1)}px, 0, 0)`;
        copy.style.visibility = fade > 0.999 ? "hidden" : "";
      }
      if (foot) foot.style.opacity = (1 - fade).toFixed(3);
      if (caption)
        caption.style.opacity = smoother((p - 0.1) / 0.08).toFixed(3);

      // 序列帧：装好 → 拆开 → 装回；相邻两帧交叉过渡
      if (!seq) return;
      dirty = false;
      const t = clamp((p - 0.1) / 0.85);
      const N = seq.frames,
        fi = t * (N - 1),
        i0 = Math.floor(fi),
        i1 = Math.min(N - 1, i0 + 1),
        frac = fi - i0;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, cw, ch);
      const a0 = pick(i0),
        a1 = pick(i1);
      if (a0) {
        ctx.globalAlpha = 1;
        ctx.drawImage(a0, 0, 0, cw, ch);
      }
      if (a1 && a1 !== a0 && frac > 0.01) {
        ctx.globalAlpha = frac;
        ctx.drawImage(a1, 0, 0, cw, ch);
      }
      ctx.globalAlpha = 1;

      // 零件名：拆得够开才出现，圆点跟着零件走，名字排在右侧一列；
      // 两个零件靠得太近（装回时闸板贴着阀体）就只留排在前面的那个，名字不叠在一起
      const on = !narrow && spreadAt(t) > 0.62;
      const row0 = seq.points[i0],
        row1 = seq.points[i1];
      const taken: number[] = [];
      for (const el of labels) {
        const k = seq.parts.indexOf(el.dataset.part!);
        const x = k >= 0 ? lerp(row0[k][0], row1[k][0], frac) * sw : 0,
          y = k >= 0 ? lerp(row0[k][1], row1[k][1], frac) * sh : 0;
        const show =
          on && k >= 0 && taken.every((ty) => Math.abs(ty - y) >= 26);
        el.classList.toggle("on", show);
        if (!show) continue;
        taken.push(y);
        el.style.setProperty("--x", `${x.toFixed(1)}px`);
        el.style.setProperty("--y", `${y.toFixed(1)}px`);
        el.style.setProperty(
          "--len",
          `${Math.max(28, sw * 0.9 - x).toFixed(1)}px`,
        );
      }
    };
    raf = requestAnimationFrame(loop);
    return () => {
      alive = false;
      cancelAnimationFrame(raf);
    };
  }, [reduce]);

  return (
    <section ref={ref} className="cv-hero" aria-label="开场">
      <div className="hero-sticky">
        <div className="hero-glow" ref={glowRef} aria-hidden="true" />
        <div className="hero-stage" ref={stageRef} aria-hidden="true">
          <div className="hero-enter">
            <img className="hero-lines" src={`${DIR}/lines.webp`} alt="" />
            <canvas className="hero-canvas" ref={canvasRef} />
            <i className="hero-scan" />
          </div>
          <ol className="hero-parts">
            {LABELS.map(([id, name]) => (
              <li key={id} data-part={id}>
                <span>{name}</span>
              </li>
            ))}
          </ol>
        </div>

        <div className="hero-copy">
          <h1 aria-label={HEAD.join("")}>
            {HEAD.map((line, li) => (
              <span className="ln" key={li} aria-hidden="true">
                {[...line].map((ch, ci) => (
                  <span
                    className="ch"
                    key={ci}
                    style={{ "--i": li * HEAD[0].length + ci } as CSSProperties}
                  >
                    {ch}
                  </span>
                ))}
              </span>
            ))}
          </h1>
          <p className="hero-lead">
            爆管之后关哪只阀、开哪台泵，先在水力仿真里算一遍、逐项验收，过关的方案才交给调度员。
          </p>
          <div className="hero-actions">
            <button className="hero-btn primary" onClick={onWorkbench}>
              进入应急工作台
              <span className="sq">
                <Icon name="arrow" size={18} />
              </span>
            </button>
            <button className="hero-btn ghost" onClick={onSystem}>
              浏览系统
              <Icon name="down" size={17} />
            </button>
          </div>
        </div>

        <div className="hero-caption" aria-hidden={reduce}>
          <b>DN300 明杆楔式闸阀 · 10 个零件</b>
          <p>
            方案里的「关闭管段」，到了现场就是转这只阀的手轮：阀杆带着闸板落下，管段断开。
          </p>
          <small>模型：团队用 Blender 自建</small>
        </div>

        <footer className="hero-foot">
          <p className="hero-scroll">向下滑动，看闸阀拆开再装回</p>
        </footer>
      </div>
    </section>
  );
}
