import { useEffect, useRef, useState } from "react";
import type { MouseEvent } from "react";
import Icon from "./Icon";

/* 首页第二块「我们的系统」：四个模块的真实界面截图，可切换；滚动进来时屏幕从倾斜转正。
   点截图或按钮，用「波光粼粼」转场进入对应模块。
   截图由 tools/capture-system.mjs 从运行中的前端生成，界面改了要重截。 */

type Target = "overview" | "compare" | "model" | "evidence";

const MODULES: { id: Target; label: string }[] = [
  { id: "overview", label: "应急工作台" },
  { id: "compare", label: "方案对照" },
  { id: "model", label: "建模与仿真" },
  { id: "evidence", label: "证据中心" },
];

const clamp = (x: number) => Math.max(0, Math.min(1, x));

export default function CoverSystem({
  reduce,
  onEnter,
}: {
  reduce: boolean;
  onEnter: (p: Target, e: MouseEvent<HTMLElement>) => void;
}) {
  const [tab, setTab] = useState(0);
  const ref = useRef<HTMLElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);

  // 滚动进来：屏幕从后仰、偏小，转到正对、原大
  useEffect(() => {
    const section = ref.current,
      frame = frameRef.current;
    if (!section || !frame || reduce) return;
    let raf = 0,
      last = -1;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const r = section.getBoundingClientRect();
      const e = clamp((innerHeight - r.top) / (innerHeight * 0.85));
      if (Math.abs(e - last) < 0.0005) return;
      last = e;
      const k = 1 - Math.pow(1 - e, 3);
      frame.style.transform = `perspective(1800px) translate3d(0, ${((1 - k) * 70).toFixed(1)}px, 0) rotateX(${((1 - k) * 24).toFixed(2)}deg) scale(${(0.88 + 0.12 * k).toFixed(4)})`;
      frame.style.opacity = (0.35 + 0.65 * k).toFixed(3);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [reduce]);

  const current = MODULES[tab];
  return (
    <section className="cv-system" id="cv-system" ref={ref} aria-label="系统">
      <div className="sys-tabs" role="tablist" aria-label="系统模块">
        {MODULES.map((m, i) => (
          <button
            key={m.id}
            role="tab"
            aria-selected={i === tab}
            className={i === tab ? "on" : ""}
            onClick={() => setTab(i)}
          >
            {m.label}
          </button>
        ))}
      </div>
      <div className="sys-stage">
        <div className="sys-frame" ref={frameRef}>
          <button
            className="sys-screen"
            aria-label={`进入${current.label}`}
            onClick={(e) => onEnter(current.id, e)}
          >
            {MODULES.map((m, i) => (
              <img
                key={m.id}
                src={`/cover/system-${m.id}.webp`}
                alt=""
                className={i === tab ? "on" : ""}
                loading={i === 0 ? "eager" : "lazy"}
                decoding="async"
              />
            ))}
          </button>
        </div>
      </div>
      <div className="sys-cta">
        <button
          className="hero-btn primary"
          onClick={(e) => onEnter(current.id, e)}
        >
          进入{current.label}
          <span className="sq">
            <Icon name="arrow" size={18} />
          </span>
        </button>
      </div>
    </section>
  );
}
