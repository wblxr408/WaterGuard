// Claude 可在这里填入实际 Blender 渲染图与源文件；路径对应 public/models。
// 当前几何示意不冒充 Blender 建模成果。GLB 实时加载器可替换 ModelDemo 的 SVG 区域。
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
    subtitle: "供水设备与连接关系",
    previewUrl: null,
    sourceUrl: null,
    author: null,
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
