# Blender 资产

团队自建 Blender 模型的成果都放在这个目录，页面通过 `src/model-assets.ts` 引用。

| 文件 | 用途 |
|---|---|
| `blender-equipment-pearl.webp` | 建模页首图：储水罐（四分之一剖切）、水泵、闸阀、清水池合影 |
| `blender-pump-pearl.webp` | 建模页「设备」卡：卧式离心泵特写 |
| `blender-pipe-burst.webp` | 建模页「管道」卡、工作台入口卡：DN600 球墨铸铁管剖切与爆管喷水（喷水为示意） |
| `blender-tank-cutaway.webp` | 备用，页面不引用：储水罐剖切特写 |
| `equipment.glb` | 可下载的模型：Pump、Valve、Tank、Reservoir、PipeSection 五个根节点，单位米，出水口朝本地 +X |

渲染图都是带透明通道的 WebP，直接叠在浅色卡片上。重新生成见 `tools/blender/`：`build_equipment.py` 生成 `equipment.glb` 和源文件 `assets-src/equipment.blend`，`render_pearl.py` 出渲染图。

`previewUrl` 为空时，模型卡退回 GPT 的 SVG 程序几何示意（可调视角、分解）。设备命名可以关联水力实体（例如 `pump_10`），但模型几何只用于展示，不能反过来替代水力参数。
