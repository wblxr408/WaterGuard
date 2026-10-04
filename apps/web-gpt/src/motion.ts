import { useEffect, useRef, useState } from "react";

const reduceQuery = "(prefers-reduced-motion: reduce)";
export const prefersReducedMotion = () =>
  typeof matchMedia === "function" && matchMedia(reduceQuery).matches;
const easeOutExpo = (k: number) => (k >= 1 ? 1 : 1 - 2 ** (-10 * k));

/**
 * 切换方案时让指标数字从旧值滚到新值（例如缺供 187.31 → 7.70）。
 * 只是显示过渡，终值永远是接口给的数；后台标签页里 rAF 不运行，用定时器兜底落到终值。
 */
export function useTween(target: number | null, duration = 560) {
  const [value, setValue] = useState(target);
  const current = useRef(target);
  useEffect(() => {
    const from = current.current;
    if (
      target == null ||
      from == null ||
      !Number.isFinite(target) ||
      !Number.isFinite(from) ||
      from === target ||
      prefersReducedMotion()
    ) {
      current.current = target;
      setValue(target);
      return;
    }
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const k = Math.min(1, (now - start) / duration);
      const v = k >= 1 ? target : from + (target - from) * easeOutExpo(k);
      current.current = v;
      setValue(v);
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    const settle = window.setTimeout(() => {
      cancelAnimationFrame(raf);
      current.current = target;
      setValue(target);
    }, duration + 120);
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(settle);
    };
  }, [target, duration]);
  return value;
}
