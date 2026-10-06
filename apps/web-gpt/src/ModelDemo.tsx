import { useMemo, useState } from "react";
import type { ModelAsset } from "./model-assets";
import Icon from "./Icon";

type V = [number, number, number];
type Face = { points: V[]; fill: string; stroke?: string };
// A small directional light gives the demo cylinders readable volume without a 3D dependency.
function shade(color: string, light: number) {
  const channels = color.slice(1).match(/../g)!;
  return (
    "#" +
    channels
      .map((c) =>
        Math.min(255, Math.round(parseInt(c, 16) * light))
          .toString(16)
          .padStart(2, "0"),
      )
      .join("")
  );
}
function box(
  x: number,
  y: number,
  z: number,
  w: number,
  h: number,
  d: number,
  colors: string[],
): Face[] {
  const v: V[] = [
    [x, y, z],
    [x + w, y, z],
    [x + w, y + h, z],
    [x, y + h, z],
    [x, y, z + d],
    [x + w, y, z + d],
    [x + w, y + h, z + d],
    [x, y + h, z + d],
  ];
  return [
    [0, 1, 2, 3],
    [4, 5, 6, 7],
    [0, 4, 7, 3],
    [1, 5, 6, 2],
    [3, 2, 6, 7],
  ].map((f, i) => ({
    points: f.map((k) => v[k]),
    fill: colors[i % colors.length],
  }));
}
function cylinder(
  x: number,
  y: number,
  z: number,
  length: number,
  radius: number,
  vertical = false,
  color = "#a2bbd2",
): Face[] {
  const start: V[] = [],
    end: V[] = [],
    faces: Face[] = [];
  for (let i = 0; i < 24; i++) {
    const a = (i / 24) * Math.PI * 2;
    start.push(
      vertical
        ? [x + Math.cos(a) * radius, y, z + Math.sin(a) * radius]
        : [x, y + Math.cos(a) * radius, z + Math.sin(a) * radius],
    );
    end.push(
      vertical
        ? [x + Math.cos(a) * radius, y + length, z + Math.sin(a) * radius]
        : [x + length, y + Math.cos(a) * radius, z + Math.sin(a) * radius],
    );
  }
  for (let i = 0; i < 24; i++)
    faces.push({
      points: [start[i], start[(i + 1) % 24], end[(i + 1) % 24], end[i]],
      fill: shade(color, 0.66 + 0.34 * Math.cos((i / 24) * Math.PI * 2 - 1.1)),
      stroke: shade(
        color,
        0.66 + 0.34 * Math.cos((i / 24) * Math.PI * 2 - 1.1),
      ),
    });
  faces.push(
    { points: start, fill: shade(color, 0.7) },
    { points: end, fill: shade(color, 1.12) },
  );
  return faces;
}
export default function ModelDemo({ asset }: { asset: ModelAsset }) {
  const [yaw, setYaw] = useState(-32);
  const [explode, setExplode] = useState(false);
  const [zoom, setZoom] = useState(1);
  const faces = useMemo(() => {
    const f: Face[] = [],
      e = explode ? 18 : 0;
    if (asset.id === "pump") {
      f.push(
        ...box(-90, 0, -55, 180, 7, 110, ["#ccd4e5", "#dce1ef", "#e9ecf8"]),
      );
      f.push(...cylinder(-58, 7 + e, 15, 69, 23, true));
      f.push(...cylinder(-58, 76 + e * 1.5, 15, 4, 26, true, "#bdcbd9"));
      for (const z of [-30, 24]) {
        f.push(
          ...box(3, 7 + e, z - 10, 62, 8, 26, [
            "#afbdcf",
            "#d5dee9",
            "#e8edf4",
          ]),
        );
        f.push(...cylinder(18, 28 + e, z + 2, 35, 13, false, "#477aca"));
        f.push(...cylinder(55 + e, 28 + e, z + 2, 22, 11, false, "#8da5bf"));
        f.push(...cylinder(-31, 30 + e, z + 2, 48, 5, false, "#729ac1"));
        f.push(...cylinder(11, 27 + e, z + 2, 40, 4, true, "#729ac1"));
        f.push(...cylinder(11, 67 + e, z + 2, 3, 11, true, "#c6d5e4"));
      }
    } else {
      f.push(
        ...box(-110, -6, -48, 220, 6, 96, ["#ccd4e5", "#dce1ef", "#e9ecf8"]),
      );
      for (const x of [-80 - e, 36 + e])
        f.push(
          ...box(x, 0, -19, 28, 12, 38, ["#acbed1", "#cbd8e6", "#e4ebf4"]),
        );
      f.push(
        ...cylinder(-100 - e, 31, 0, 94, 19),
        ...cylinder(6 + e, 31, 0, 94, 19),
      );
      for (const x of [-86 - e, -12 - e, 6 + e, 80 + e])
        f.push(...cylinder(x, 31, 0, 6, 24, false, "#bdcfe2"));
      // 开口水流截面只是示意，不用于推断供水、压力或泄漏量。
      f.push(
        ...cylinder(-6 - e, 31, 0, 1, 14, false, "#538cbb"),
        ...cylinder(5 + e, 31, 0, 1, 14, false, "#538cbb"),
      );
    }
    return f;
  }, [asset.id, explode]);
  const angle = (yaw * Math.PI) / 180;
  const transform = ([x, y, z]: V) => {
    const xx = x * Math.cos(angle) - z * Math.sin(angle),
      zz = x * Math.sin(angle) + z * Math.cos(angle);
    return {
      x: 260 + xx * 1.48 * zoom,
      y: 225 + (zz * 0.48 - y * 0.88) * 1.48 * zoom,
      depth: zz - y * 0.4,
    };
  };
  const sorted = faces
    .map((f) => ({
      ...f,
      projected: f.points.map(transform),
      depth:
        f.points.map(transform).reduce((n, p) => n + p.depth, 0) /
        f.points.length,
    }))
    .sort((a, b) => a.depth - b.depth);
  return (
    <article className="model-demo">
      <div className="model-demo-head">
        <span className="eyebrow">{asset.id === "pump" ? "设备" : "管道"}</span>
        <span className={`badge ${asset.previewUrl ? "pass" : "neutral"}`}>
          {asset.previewUrl ? "Blender 渲染成果" : "三维模型"}
        </span>
      </div>
      <div className="model-render">
        {asset.previewUrl ? (
          <img src={asset.previewUrl} alt={`${asset.title} Blender 渲染成果`} />
        ) : (
          <svg
            viewBox="0 0 520 330"
            role="img"
            aria-label={`${asset.title}三维模型`}
          >
            <ellipse
              cx="260"
              cy="268"
              rx="167"
              ry="26"
              fill="#9eaad3"
              opacity=".17"
            />
            {sorted.map((f, i) => (
              <polygon
                key={i}
                points={f.projected.map((p) => `${p.x},${p.y}`).join(" ")}
                fill={f.fill}
                stroke={f.stroke ?? "#ffffff50"}
                strokeWidth=".6"
              />
            ))}
            <text x="23" y="306" fill="#8194ac" fontSize="10">
              {asset.id === "pump"
                ? "泵组 · 储水罐 · 管路"
                : "管体 · 法兰 · 开口截面"}
            </text>
          </svg>
        )}
        {!asset.previewUrl && (
          <div className="model-camera">
            <button
              onClick={() => setZoom((z) => Math.min(z + 0.15, 1.4))}
              aria-label={`放大${asset.title}`}
            >
              <Icon name="plus" size={15} />
            </button>
            <button
              onClick={() => setZoom((z) => Math.max(z - 0.15, 0.7))}
              aria-label={`缩小${asset.title}`}
            >
              <Icon name="minus" size={15} />
            </button>
            <button
              onClick={() => {
                setYaw(-32);
                setZoom(1);
                setExplode(false);
              }}
              aria-label={`重置${asset.title}`}
            >
              <Icon name="refresh" size={15} />
            </button>
          </div>
        )}
      </div>
      <div className="model-description">
        <h3>{asset.title}</h3>
        <p>{asset.subtitle}</p>
      </div>
      {!asset.previewUrl && (
        <div className="model-controls">
          <label>
            视角
            <input
              aria-label={`${asset.title}视角`}
              type="range"
              min="-180"
              max="180"
              value={yaw}
              onChange={(e) => setYaw(Number(e.target.value))}
            />
          </label>
          <button
            aria-pressed={explode}
            className={explode ? "active" : ""}
            onClick={() => setExplode(!explode)}
          >
            <Icon name="layers" size={15} />
            {explode ? "还原结构" : "分解查看"}
          </button>
        </div>
      )}
      <div className="model-handoff">
        {asset.author && <span>{asset.author}</span>}
        {asset.sourceUrl ? (
          <a href={asset.sourceUrl} download>
            <Icon name="download" size={14} />
            下载 GLB 模型
          </a>
        ) : (
          <span className="mono">.blend / .glb</span>
        )}
      </div>
    </article>
  );
}
