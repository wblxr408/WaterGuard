"""把设备模型渲染成浅色界面用的透明底图（Blender 5.2，后台运行）。

读取 assets-src/equipment.blend（由同目录 build_equipment.py 生成），把深色地面改成阴影捕捉器、
背景改为透明，灯光调成与界面一致的珍珠白，输出带透明通道的 WebP 到 public/models/：
  blender-equipment-pearl.webp  四件设备合影（建模页首图、工作台入口卡片）
  blender-pump-pearl.webp       水泵特写（建模页「泵站设备」卡片）

用法（仓库根目录）：
  blender -b apps/web-gpt/assets-src/equipment.blend --python apps/web-gpt/tools/blender/render_pearl.py
"""
from pathlib import Path

import bpy
from mathutils import Vector

HERE = Path(__file__).resolve()
WEB = HERE.parents[2]
OUT = WEB / "public" / "models"
scene = bpy.context.scene

# 地面只留下柔和投影，背景透明，图片能直接叠在浅色卡片上
floor = bpy.data.objects["Preview_Floor"]
floor.is_shadow_catcher = True
scene.render.film_transparent = True

# 环境光：明亮的珍珠白，金属件的反射和页面底色一致
background = scene.world.node_tree.nodes["Background"]
background.inputs["Color"].default_value = (0.94, 0.93, 0.99, 1.0)
background.inputs["Strength"].default_value = 0.9

# 主光偏暖、轮廓光偏紫蓝，呼应界面的暖光与淡紫
for name, energy, color in (
    ("Key", 1300, (1.0, 0.98, 0.95)),
    ("Rim", 800, (0.64, 0.66, 1.0)),
    ("Fill", 420, (0.92, 0.93, 1.0)),
):
    light = bpy.data.objects[name]
    light.data.energy = energy
    light.data.color = color

scene.render.engine = "CYCLES"
scene.cycles.samples = 128
scene.cycles.use_denoising = True
image = scene.render.image_settings
image.file_format = "WEBP"
image.color_mode = "RGBA"
image.quality = 90
try:
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for backend in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = backend
            prefs.get_devices()
            gpus = [d for d in prefs.devices if d.type == backend]
            if gpus:
                for d in prefs.devices:
                    d.use = d.type == backend
                scene.cycles.device = "GPU"
                print(f"render device: {backend} ({gpus[0].name})")
                break
        except Exception:
            continue
except Exception as exc:  # noqa: BLE001
    print("GPU setup skipped:", exc)


def render(path, width, height):
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    print("rendered", path, path.stat().st_size, "bytes")


# 1. 四件设备合影：沿用 build_equipment.py 的预览机位
render(OUT / "blender-equipment-pearl.webp", 1600, 760)

# 2. 水泵特写：只渲染水泵，机位从右前上方看泵壳与电机
for name in ("Tank", "Valve", "Reservoir"):
    root = bpy.data.objects[name]
    for obj in [root, *root.children_recursive]:
        obj.hide_render = True
pump = bpy.data.objects["Pump"]
target = pump.matrix_world.translation + Vector((0.1, 0.0, 0.5))
camera = scene.camera
camera.data.lens = 50
camera.location = target + Vector((2.6, -4.4, 1.8))
camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
render(OUT / "blender-pump-pearl.webp", 1200, 800)
