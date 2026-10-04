import { useMemo } from "react";
import type { MouseEvent } from "react";
import type { Workspace } from "./data";
import CoverHero from "./CoverHero";
import CoverSystem from "./CoverSystem";
import { prefersReducedMotion } from "./motion";
import { playWaterTransition, rippleFrom } from "./water-transition";
import "./cover.css";

/* 首页只有两块：开场（闸阀拆开再装回）和「我们的系统」（四个模块的界面）。
   事故经过、方案对照、验收、证据在后面各页；智能体单独成页（待定）。
   从首页进入各模块时，用「波光粼粼」的水面转场。 */

type Target = "overview" | "compare" | "model" | "evidence";

export default function Cover({
  openPage,
}: {
  w: Workspace | null;
  openPage: (p: Target) => void;
}) {
  const reduce = useMemo(() => prefersReducedMotion(), []);
  const enter = (p: Target, e: MouseEvent<HTMLElement>) => {
    const [x, y] = rippleFrom(e);
    playWaterTransition(x, y, () => openPage(p));
  };
  return (
    <div className={`cover ${reduce ? "reduce" : ""}`}>
      <CoverHero
        reduce={reduce}
        onSystem={() =>
          document
            .getElementById("cv-system")
            ?.scrollIntoView({ behavior: reduce ? "auto" : "smooth" })
        }
        onWorkbench={(e) => enter("overview", e)}
      />
      <CoverSystem reduce={reduce} onEnter={enter} />
    </div>
  );
}
