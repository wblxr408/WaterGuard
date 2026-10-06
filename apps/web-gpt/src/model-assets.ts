// Blender 渲染图与源文件的接入配置；路径对应 public/models。
// 全部为团队自建的 Blender 模型（tools/blender/build_equipment.py 建模、render_pearl.py 渲染）。
// GLB 实时加载器可替换 ModelDemo 的图片区域。
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
    subtitle: "风冷电机、联轴器护罩、蜗壳与进出口法兰",
    previewUrl: "/models/blender-pump-pearl.webp",
    sourceUrl: "/models/equipment.glb",
    author: "团队自建 · Blender 5.2 建模 · Cycles 渲染",
  },
  {
    id: "pipe",
    title: "管道剖面",
    subtitle: "球墨铸铁管、承插接口、砂浆内衬与爆管裂口",
    previewUrl: "/models/blender-pipe-burst.webp",
    sourceUrl: "/models/equipment.glb",
    author: "团队自建 · Blender 5.2 建模 · Cycles 渲染",
  },
];

/** 建模页首图：储水罐（剖切）、水泵、闸阀、清水池合影。 */
export const studioImage = {
  src: "/models/blender-equipment-pearl.webp",
  alt: "Blender 建模的储水罐剖切、水泵、闸阀与清水池，浅色棚拍渲染",
  caption: "Blender 自建模型 · Cycles 渲染",
  teaser: "储水罐 · 水泵 · 闸阀\n清水池 · 爆管管段",
};

/** 工作台入口卡片：爆管管段（单个对象在小卡片里更清楚，也贴合爆管主题）。 */
export const teaserImage = {
  src: "/models/blender-pipe-burst.webp",
  alt: "Blender 建模的球墨铸铁管段：左段剖切露出内衬与水，右段顶部爆管喷水",
  caption: "Blender 自建模型",
};
