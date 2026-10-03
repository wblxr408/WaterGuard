# Blender 资产交接入口

由 Claude 或建模同学把实际成果放在这个目录，并修改 `src/model-assets.ts`。

推荐路径：`pump-station.webp`、`pipe-cutaway.webp`（渲染预览），`pump-station.glb`、`pipe-cutaway.glb`（导出模型），可额外保留 `.blend`。

当前两项是 SVG 软件投影的程序几何示意，支持视角与分解交互，不是 Blender 作品。`previewUrl` 有值时展示真实渲染图，`sourceUrl` 有值时开放源文件下载；未配置保持明确的待接入状态。建议注明作者，保留实体 ID 映射。没有 Three.js 依赖，GLB 实时查看器应替换 `ModelDemo.tsx` 中的 SVG 区域后独立验证。

建议导出：米为单位、应用缩放、低复杂度材质、适合网页的模型体积。设备命名可关联水力实体（例如 `pump_10`），但艺术几何不能反向替代水力参数。
