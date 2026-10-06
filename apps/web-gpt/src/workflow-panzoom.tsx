import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import Icon from "./Icon";

export function usePanZoom(stageW: number, stageH: number, follow = true) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const measured = useRef({ w: 0, h: 0 });
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(
    null,
  );
  // 适应画布：整张图放得下，最大不放大到 1 倍以上；面板展开收起时自动重排
  const fit = useCallback(() => {
    const el = wrapRef.current;
    if (!el || !el.clientWidth || !el.clientHeight) return;
    const k = Math.min(
      1,
      (el.clientWidth - 24) / stageW,
      (el.clientHeight - 24) / stageH,
    );
    setView({
      k,
      x: (el.clientWidth - stageW * k) / 2,
      y: (el.clientHeight - stageH * k) / 2,
    });
  }, [stageW, stageH]);
  useLayoutEffect(() => {
    if (follow) fit();
    const ro = new ResizeObserver(() => {
      const el = wrapRef.current;
      if (!el || (el.clientWidth === measured.current.w && el.clientHeight === measured.current.h)) return;
      measured.current = { w: el.clientWidth, h: el.clientHeight };
      if (el.clientWidth && el.clientHeight) fit();
    });
    if (wrapRef.current) ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, [fit, follow]);
  const zoom = (f: number) => {
    const el = wrapRef.current;
    if (!el) return;
    setView((v) => {
      const k = Math.max(0.4, Math.min(1.6, v.k * f));
      const cx = el.clientWidth / 2,
        cy = el.clientHeight / 2;
      return {
        k,
        x: cx - ((cx - v.x) * k) / v.k,
        y: cy - ((cy - v.y) * k) / v.k,
      };
    });
  };
  const handlers = {
    onPointerDown: (e: ReactPointerEvent<HTMLDivElement>) => {
      if ((e.target as HTMLElement).closest(".wf-node, .wf-zoom")) return;
      drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    onPointerMove: (e: ReactPointerEvent<HTMLDivElement>) => {
      const d = drag.current;
      if (!d) return;
      setView((v) => ({
        ...v,
        x: d.vx + e.clientX - d.x,
        y: d.vy + e.clientY - d.y,
      }));
    },
    onPointerUp: () => {
      drag.current = null;
    },
    onPointerCancel: () => {
      drag.current = null;
    },
  };
  return {
    wrapRef,
    fit,
    zoom,
    handlers,
    transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})`,
  };
}

export function ZoomBar({ pz }: { pz: ReturnType<typeof usePanZoom> }) {
  return (
    <div className="wf-zoom">
      <button onClick={() => pz.zoom(1.2)} aria-label="放大">
        <Icon name="plus" size={15} />
      </button>
      <button onClick={() => pz.zoom(1 / 1.2)} aria-label="缩小">
        <Icon name="minus" size={15} />
      </button>
      <button onClick={pz.fit} aria-label="适应画布">
        <Icon name="fit" size={15} />
      </button>
    </div>
  );
}
