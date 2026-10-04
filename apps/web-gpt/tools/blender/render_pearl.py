"""把设备模型渲染成浅色界面用的透明底图（Blender 5.2，后台运行）。

读取 assets-src/equipment.blend（由同目录 build_equipment.py 生成）：地面设为阴影捕捉器、背景透明，
用 Blender 自带的棚拍环境贴图（studio.exr）提供金属反射，再加暖色主光、冷色轮廓光与补光。
输出带透明通道的 WebP 到 public/models/：
  blender-equipment-pearl.webp  四件设备合影（建模页首图、工作台入口卡片）
  blender-pump-pearl.webp       水泵特写（建模页「泵站设备」卡片）
  blender-pipe-burst.webp       管段剖面与爆管裂口（建模页「管道剖面」卡片）
  blender-tank-cutaway.webp     储水罐剖切特写（备用）

用法（仓库根目录）：
  blender -b apps/web-gpt/assets-src/equipment.blend --python apps/web-gpt/tools/blender/render_pearl.py -- [preview] [equipment pump pipe tank]
  传 preview 时只用低采样、半分辨率快速出图，检查构图；列出镜头名时只渲染这几张（默认全部）。
"""
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

HERE = Path(__file__).resolve()
WEB = HERE.parents[2]
OUT = WEB / "public" / "models"
ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PREVIEW = "preview" in ARGS
SHOTS = [a for a in ARGS if a in ("equipment", "pump", "pipe", "tank")] or ["equipment", "pump", "pipe", "tank"]
scene = bpy.context.scene

# 地面只留下柔和投影，背景透明，图片能直接叠在浅色卡片上
floor = bpy.data.objects["Preview_Floor"]
floor.is_shadow_catcher = True
scene.render.film_transparent = True

# 环境光：Blender 自带棚拍贴图，金属件有真实反射
world = bpy.data.worlds.new("WG_Studio")
world.use_nodes = True
nt = world.node_tree
env = nt.nodes.new("ShaderNodeTexEnvironment")
datafiles = Path(bpy.utils.resource_path("LOCAL")) / "datafiles" / "studiolights" / "world" / "studio.exr"
env.image = bpy.data.images.load(str(datafiles))
mapping = nt.nodes.new("ShaderNodeMapping")
coord = nt.nodes.new("ShaderNodeTexCoord")
mapping.inputs["Rotation"].default_value = (0, 0, math.radians(120))
nt.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
nt.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
bg = nt.nodes["Background"]
bg.inputs["Strength"].default_value = 1.0
nt.links.new(env.outputs["Color"], bg.inputs["Color"])
scene.world = world
# 环境贴图只给反射（金属、漆面高光），不参与漫反射照明：否则阴影捕捉器会在设备周围压出大片灰雾
try:
    world.cycles_visibility.diffuse = False
except AttributeError:
    world.visibility.diffuse = False


def area_light(name, loc, target, energy, color, size):
    data = bpy.data.lights.new(name, "AREA")
    data.energy = energy
    data.color = color
    data.size = size
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    obj.location = loc
    obj.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    return obj


key = area_light("Key", (-7, -9, 11), (0, 0, 0.8), 3600, (1.0, 0.96, 0.9), 9)
top = area_light("Top", (0, -1, 14), (0, 0, 0), 2300, (0.97, 0.98, 1.0), 16)
rim = area_light("Rim", (8, 9, 6), (0, 0, 1.0), 2400, (0.66, 0.72, 1.0), 7)
fill = area_light("Fill", (3, -14, 3), (0, 0, 1.0), 900, (0.9, 0.93, 1.0), 12)

scene.render.engine = "CYCLES"
scene.cycles.samples = 48 if PREVIEW else 320
scene.cycles.use_denoising = True
scene.render.resolution_percentage = 50 if PREVIEW else 100
scene.view_settings.view_transform = "AgX"
scene.view_settings.exposure = -0.7  # 压住高光，漆面颜色不发白
for look in ("AgX - Medium High Contrast", "Medium High Contrast"):
    try:
        scene.view_settings.look = look
        break
    except TypeError:
        continue
image = scene.render.image_settings
image.file_format = "WEBP"
image.color_mode = "RGBA"
image.quality = 92
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

ROOTS = ("Tank", "Pump", "Valve", "Reservoir", "PipeSection")


def show(*names):
    for root_name in ROOTS:
        root = bpy.data.objects[root_name]
        hidden = root_name not in names
        for obj in [root, *root.children_recursive]:
            obj.hide_render = hidden


camera = scene.camera


def clean_shadow(path, floor=0.08, margin=0.1):
    """阴影捕捉器会在整片地面留下约 4% 的均匀灰度，贴到卡片上会看出图片的矩形边。
    只处理「暗色且半透明」的地面像素：减掉这层底灰，并在图片四周羽化到 0，只留下设备脚下的接触阴影。"""
    img = bpy.data.images.load(str(path), check_existing=False)
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)
    alpha = px[..., 3]
    lum = 0.2126 * px[..., 0] + 0.7152 * px[..., 1] + 0.0722 * px[..., 2]
    shadow = (alpha < 0.97) & (lum < 0.12)
    ys = np.linspace(0.0, 1.0, h)[:, None]
    xs = np.linspace(0.0, 1.0, w)[None, :]
    ramp = lambda t: np.clip(t / margin, 0.0, 1.0)  # noqa: E731
    fade = ramp(xs) * ramp(1 - xs) * ramp(ys) * ramp(1 - ys)
    fade = fade * fade * (3 - 2 * fade)
    cleaned = np.clip((alpha - floor) / (1 - floor), 0.0, 1.0) * fade
    alpha[shadow] = cleaned[shadow]
    px[..., 3] = alpha
    img.pixels.foreach_set(px.ravel())
    img.filepath_raw = str(path)
    img.file_format = "WEBP"
    img.save()
    bpy.data.images.remove(img)


def shoot(path, width, height, cam_loc, target, lens):
    camera.data.lens = lens
    camera.location = cam_loc
    camera.rotation_euler = (Vector(target) - Vector(cam_loc)).to_track_quat("-Z", "Y").to_euler()
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    clean_shadow(path)
    print("rendered", path, path.stat().st_size, "bytes")


def where(name, offset=(0, 0, 0)):
    return bpy.data.objects[name].location + Vector(offset)


suffix = "-preview" if PREVIEW else ""
# 1. 四件设备合影
if "equipment" in SHOTS:
    show("Tank", "Pump", "Valve", "Reservoir")
    shoot(OUT / f"blender-equipment-pearl{suffix}.webp", 1600, 760, (0.4, -13.6, 4.5), (0.1, 0.5, 1.2), 37)
# 2. 水泵特写：从右前上方看泵壳、电机与联轴器护罩
if "pump" in SHOTS:
    show("Pump")
    pump = where("Pump")
    shoot(OUT / f"blender-pump-pearl{suffix}.webp", 1200, 800, pump + Vector((2.3, -3.7, 1.75)),
          pump + Vector((-0.05, 0, 0.52)), 50)
# 3. 管段剖面与爆管：左段剖切看结构，右段顶部裂口喷水
if "pipe" in SHOTS:
    show("PipeSection")
    pipe = where("PipeSection")
    shoot(OUT / f"blender-pipe-burst{suffix}.webp", 1200, 800, pipe + Vector((-0.6, -4.3, 2.0)),
          pipe + Vector((0.2, 0, 0.82)), 39)
# 4. 储水罐剖切特写
if "tank" in SHOTS:
    show("Tank")
    tank = where("Tank")
    shoot(OUT / f"blender-tank-cutaway{suffix}.webp", 1000, 1000, tank + Vector((3.4, -4.4, 2.9)),
          tank + Vector((0, 0, 1.45)), 46)
