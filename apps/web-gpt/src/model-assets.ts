// Blender 渲染图与源文件的接入配置；路径对应 public/models。
// 泵站设备已接入团队自建的 Blender 模型（tools/blender/ 下的脚本生成、Cycles 渲染）；
// 管道剖面暂无 Blender 模型，保留可交互的程序几何示意。GLB 实时加载器可替换 ModelDemo 的 SVG 区域。
export interface ModelAsset {
  id: "pump" | "pipe";
  title: string;
  subtitle: string;
  previewUrl: string | null;
  sourceUrl: string | null;
  author: string | null;
}
export const modelAssets: ModelAsset[] = [
  {
    id: "pump",
    title: "泵站设备",
    subtitle: "电机、泵壳、进出水管与底座",
    previewUrl: "/models/blender-pump-pearl.webp",
    sourceUrl: "/models/equipment.glb",
    author: "团队自建 · Blender 5.2 建模 · Cycles 渲染",
  },
  {
    id: "pipe",
    title: "管道剖面",
    subtitle: "管段、接头与断口结构",
    previewUrl: null,
    sourceUrl: null,
    author: null,
  },
];

/** 建模页首图与工作台入口卡片：四件设备合影（水箱、水泵、闸阀、水源）。 */
export const studioImage = {
  src: "/models/blender-equipment-pearl.webp",
  alt: "Blender 建模的水箱、水泵、闸阀与水源水池，浅色棚拍渲染",
  caption: "Blender 自建模型 · Cycles 渲染",
  teaser: "水箱 · 水泵 · 闸阀 · 水源，Blender 自建",
};
