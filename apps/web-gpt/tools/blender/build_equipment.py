"""WaterGuard 设备模型（Blender 5.2，后台运行）。

生成四个可复用设备并导出 GLB，供三维沙盘按 hydraulic_element_id 实例化：
  Tank       水箱：Tank_Body + Tank_Water（原点在水体底部，沿竖直方向缩放表示水位）
  Pump       水泵：Pump_Body + Pump_Fan（绕 X 轴旋转表示运行）
  Valve      闸阀：Valve_Body + Valve_Wheel（绕竖直轴旋转表示开关动作）
  Reservoir  水源水池：Reservoir_Body + Reservoir_Water

只做设备，不做管网：管道由前端按管网数据程序化生成（见 docs/前端设计.md 4.1）。

用法（仓库根目录）：
  blender -b --python apps/web/tools/blender/build_equipment.py
输出：
  apps/web/public/models/equipment.glb          （前端运行时加载）
  apps/web/assets-src/equipment-preview.png     （预览图，不随网页发布）
  apps/web/assets-src/equipment.blend
"""
import math
from pathlib import Path

import bpy

HERE = Path(__file__).resolve()
WEB = HERE.parents[2]
OUT_GLB = WEB / "public" / "models" / "equipment.glb"
OUT_PNG = WEB / "assets-src" / "equipment-preview.png"
OUT_BLEND = WEB / "assets-src" / "equipment.blend"

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


# ---------------------------------------------------------------- materials
def material(name, rgb, metallic=0.0, roughness=0.5, alpha=1.0, emission=None, strength=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Alpha"].default_value = alpha
    if emission is not None:
        bsdf.inputs["Emission Color"].default_value = (*emission, 1.0)
        bsdf.inputs["Emission Strength"].default_value = strength
    if alpha < 1.0:
        try:
            m.surface_render_method = "BLENDED"
        except AttributeError:
            pass
    return m


M = {
    "steel": material("WG_Steel", (0.56, 0.62, 0.70), metallic=0.85, roughness=0.32),
    "iron": material("WG_DarkIron", (0.10, 0.13, 0.17), metallic=0.55, roughness=0.48),
    "cobalt": material("WG_PumpCobalt", (0.07, 0.24, 0.62), metallic=0.35, roughness=0.32),
    "shell": material("WG_TankShell", (0.82, 0.90, 1.00), metallic=0.0, roughness=0.12, alpha=0.32),
    "ring": material("WG_TankRing", (0.22, 0.36, 0.55), metallic=0.75, roughness=0.28),
    "water": material("WG_Water", (0.05, 0.45, 0.95), metallic=0.0, roughness=0.06, alpha=0.88,
                      emission=(0.05, 0.40, 0.95), strength=0.35),
    "concrete": material("WG_Concrete", (0.36, 0.40, 0.47), metallic=0.0, roughness=0.88),
    "wheel": material("WG_ValveWheel", (0.86, 0.22, 0.20), metallic=0.25, roughness=0.38),
    "fan": material("WG_Fan", (0.78, 0.83, 0.90), metallic=0.6, roughness=0.3),
}


# ---------------------------------------------------------------- helpers
def _finish(obj, mat, bevel=0.0, smooth=True):
    obj.data.materials.append(mat)
    if bevel > 0:
        mod = obj.modifiers.new("Bevel", "BEVEL")
        mod.width = bevel
        mod.segments = 2
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    if smooth:
        try:
            bpy.ops.object.shade_smooth_by_angle(angle=math.radians(35))
        except Exception:
            bpy.ops.object.shade_smooth()
    obj.select_set(False)
    return obj


def cyl(r, depth, loc, mat, axis="Z", verts=48, bevel=0.0, name=None):
    rot = {"Z": (0, 0, 0), "X": (0, math.pi / 2, 0), "Y": (math.pi / 2, 0, 0)}[axis]
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=r, depth=depth, location=loc, rotation=rot)
    o = bpy.context.active_object
    if name:
        o.name = name
    return _finish(o, mat, bevel)


def box(size, loc, mat, bevel=0.0, rot=(0, 0, 0), name=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc, rotation=rot)
    o = bpy.context.active_object
    o.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if name:
        o.name = name
    return _finish(o, mat, bevel, smooth=False)


def torus(major, minor, loc, mat, axis="Z", name=None):
    rot = {"Z": (0, 0, 0), "X": (0, math.pi / 2, 0), "Y": (math.pi / 2, 0, 0)}[axis]
    bpy.ops.mesh.primitive_torus_add(major_radius=major, minor_radius=minor, major_segments=40,
                                     minor_segments=8, location=loc, rotation=rot)
    o = bpy.context.active_object
    if name:
        o.name = name
    return _finish(o, mat)


def cone(r1, r2, depth, loc, mat, name=None):
    bpy.ops.mesh.primitive_cone_add(vertices=48, radius1=r1, radius2=r2, depth=depth, location=loc)
    o = bpy.context.active_object
    if name:
        o.name = name
    return _finish(o, mat)


def join(parts, name):
    bpy.ops.object.select_all(action="DESELECT")
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    o = bpy.context.active_object
    o.name = name
    o.data.name = name
    bpy.ops.object.select_all(action="DESELECT")
    return o


def set_origin(obj, point):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    scene.cursor.location = point
    bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
    obj.select_set(False)


def group(name, children):
    root = bpy.data.objects.new(name, None)
    scene.collection.objects.link(root)
    for c in children:
        c.parent = root
    return root


# ---------------------------------------------------------------- Tank
def build_tank():
    parts = [
        cyl(1.16, 0.16, (0, 0, 0.08), M["concrete"], bevel=0.02),
        cyl(1.0, 2.2, (0, 0, 1.26), M["shell"], verts=64),
        cone(1.03, 0.22, 0.38, (0, 0, 2.55), M["ring"]),
        cyl(0.24, 0.08, (0, 0, 2.78), M["ring"]),
    ]
    for z in (0.42, 1.26, 2.1):
        parts.append(torus(1.0, 0.035, (0, 0, z), M["ring"]))
    # 爬梯
    for y in (-0.13, 0.13):
        parts.append(cyl(0.022, 2.35, (1.1, y, 1.3), M["steel"], verts=12))
    for i in range(9):
        parts.append(cyl(0.016, 0.26, (1.1, 0, 0.35 + i * 0.25), M["steel"], axis="Y", verts=10))
    # 出水管与法兰
    parts.append(cyl(0.12, 0.6, (1.3, 0, 0.32), M["steel"], axis="X", verts=32))
    parts.append(cyl(0.2, 0.06, (1.6, 0, 0.32), M["iron"], axis="X", verts=32))
    body = join(parts, "Tank_Body")

    water = cyl(0.95, 2.0, (0, 0, 0.18 + 1.0), M["water"], verts=64, name="Tank_Water")
    set_origin(water, (0, 0, 0.18))
    return group("Tank", [body, water])


# ---------------------------------------------------------------- Pump
def build_pump():
    parts = [
        box((1.9, 0.82, 0.1), (0, 0, 0.05), M["iron"], bevel=0.02),
        cyl(0.33, 0.86, (-0.28, 0, 0.47), M["cobalt"], axis="X", verts=48, bevel=0.01),
        cyl(0.3, 0.1, (-0.76, 0, 0.47), M["steel"], axis="X", verts=48),
        cyl(0.12, 0.16, (0.23, 0, 0.47), M["steel"], axis="X", verts=32),
        cyl(0.38, 0.32, (0.52, 0, 0.47), M["steel"], axis="X", verts=48, bevel=0.015),
        cyl(0.15, 0.45, (0.92, 0, 0.47), M["steel"], axis="X", verts=32),
        cyl(0.24, 0.05, (1.15, 0, 0.47), M["iron"], axis="X", verts=32),
        cyl(0.13, 0.5, (0.52, 0, 0.98), M["steel"], verts=32),
        cyl(0.22, 0.05, (0.52, 0, 1.24), M["iron"], verts=32),
        box((0.16, 0.6, 0.22), (-0.55, 0, 0.18), M["iron"], bevel=0.015),
        box((0.16, 0.6, 0.22), (0.05, 0, 0.18), M["iron"], bevel=0.015),
        box((0.2, 0.62, 0.2), (0.52, 0, 0.17), M["iron"], bevel=0.015),
    ]
    for i in range(7):
        parts.append(torus(0.335, 0.022, (-0.62 + i * 0.1, 0, 0.47), M["cobalt"], axis="X"))
    body = join(parts, "Pump_Body")

    blades = [cyl(0.07, 0.06, (-0.86, 0, 0.47), M["fan"], axis="X", verts=24)]
    for k in range(6):
        a = k * math.pi / 3
        blades.append(box((0.025, 0.09, 0.25), (-0.86, 0.13 * math.sin(a), 0.47 + 0.13 * math.cos(a)), M["fan"],
                          rot=(a, 0, 0)))
    fan = join(blades, "Pump_Fan")
    set_origin(fan, (-0.86, 0, 0.47))
    return group("Pump", [body, fan])


# ---------------------------------------------------------------- Valve
def build_valve():
    parts = [
        cyl(0.26, 0.72, (0, 0, 0.42), M["iron"], axis="X", verts=48, bevel=0.01),
        cyl(0.4, 0.07, (-0.39, 0, 0.42), M["iron"], axis="X", verts=48),
        cyl(0.4, 0.07, (0.39, 0, 0.42), M["iron"], axis="X", verts=48),
        cyl(0.17, 0.42, (-0.64, 0, 0.42), M["steel"], axis="X", verts=32),
        cyl(0.17, 0.42, (0.64, 0, 0.42), M["steel"], axis="X", verts=32),
        box((0.36, 0.3, 0.42), (0, 0, 0.8), M["iron"], bevel=0.03),
        cyl(0.036, 0.52, (0, 0, 1.24), M["steel"], verts=16),
    ]
    for side in (-0.39, 0.39):
        for k in range(8):
            a = k * math.pi / 4
            parts.append(cyl(0.025, 0.1, (side, 0.32 * math.cos(a), 0.42 + 0.32 * math.sin(a)), M["steel"],
                             axis="X", verts=10))
    body = join(parts, "Valve_Body")

    wheel_parts = [torus(0.3, 0.032, (0, 0, 1.5), M["wheel"]), cyl(0.06, 0.07, (0, 0, 1.5), M["wheel"], verts=24)]
    for k in range(4):
        a = k * math.pi / 2
        wheel_parts.append(box((0.3, 0.035, 0.035), (0.15 * math.cos(a), 0.15 * math.sin(a), 1.5), M["wheel"],
                               rot=(0, 0, a)))
    wheel = join(wheel_parts, "Valve_Wheel")
    set_origin(wheel, (0, 0, 1.5))
    return group("Valve", [body, wheel])


# ---------------------------------------------------------------- Reservoir
def build_reservoir():
    w, d, h, t = 3.0, 2.0, 0.6, 0.14
    parts = [
        box((w, d, 0.12), (0, 0, 0.06), M["concrete"], bevel=0.02),
        box((w, t, h), (0, d / 2 - t / 2, h / 2 + 0.06), M["concrete"], bevel=0.02),
        box((w, t, h), (0, -d / 2 + t / 2, h / 2 + 0.06), M["concrete"], bevel=0.02),
        box((t, d, h), (w / 2 - t / 2, 0, h / 2 + 0.06), M["concrete"], bevel=0.02),
        box((t, d, h), (-w / 2 + t / 2, 0, h / 2 + 0.06), M["concrete"], bevel=0.02),
        cyl(0.14, 0.7, (w / 2 + 0.3, 0, 0.32), M["steel"], axis="X", verts=32),
        cyl(0.22, 0.05, (w / 2 + 0.66, 0, 0.32), M["iron"], axis="X", verts=32),
    ]
    body = join(parts, "Reservoir_Body")
    water = box((w - 2 * t - 0.02, d - 2 * t - 0.02, 0.44), (0, 0, 0.12 + 0.22), M["water"], name="Reservoir_Water")
    set_origin(water, (0, 0, 0.12))
    return group("Reservoir", [body, water])


tank = build_tank()
pump = build_pump()
valve = build_valve()
reservoir = build_reservoir()

# 每个设备的根节点留在原点，前端自行定位；导出后再为预览图摆开
OUT_GLB.parent.mkdir(parents=True, exist_ok=True)
# 设备不用贴图，去掉 UV 可减小约四分之一体积
bpy.ops.export_scene.gltf(filepath=str(OUT_GLB), export_format="GLB", export_apply=True, export_texcoords=False)

# ---------------------------------------------------------------- preview render
tank.location = (-4.0, 0.6, 0)
pump.location = (-0.85, -0.2, 0)
valve.location = (1.45, 0.1, 0)
reservoir.location = (4.25, 0.5, 0)
tank.scale = (0.95, 0.95, 0.95)

bpy.ops.mesh.primitive_plane_add(size=400, location=(0, 0, 0))
floor = bpy.context.active_object
floor.name = "Preview_Floor"
floor.data.materials.append(material("WG_Floor", (0.035, 0.07, 0.13), roughness=0.55))

world = bpy.data.worlds.new("WG_World")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.012, 0.03, 0.065, 1.0)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.6
scene.world = world


def light(name, kind, loc, energy, color, size=None, rot=None):
    data = bpy.data.lights.new(name, kind)
    data.energy = energy
    data.color = color
    if size is not None:
        data.size = size
    obj = bpy.data.objects.new(name, data)
    obj.location = loc
    if rot:
        obj.rotation_euler = rot
    scene.collection.objects.link(obj)
    return obj


light("Key", "AREA", (-3, -6, 7), 1500, (0.85, 0.92, 1.0), size=5, rot=(math.radians(50), 0, math.radians(-25)))
light("Rim", "AREA", (5, 6, 4), 900, (0.35, 0.65, 1.0), size=4, rot=(math.radians(-60), 0, math.radians(150)))
light("Fill", "AREA", (0, -8, 2), 250, (0.6, 0.8, 1.0), size=8, rot=(math.radians(80), 0, 0))

cam_data = bpy.data.cameras.new("Cam")
cam_data.lens = 42
cam = bpy.data.objects.new("Cam", cam_data)
cam.location = (0.25, -16.5, 6.4)
cam.rotation_euler = (math.radians(71), 0, 0)
scene.collection.objects.link(cam)
scene.camera = cam

scene.render.engine = "CYCLES"
scene.cycles.samples = 96
scene.cycles.use_denoising = True
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
scene.render.resolution_x = 1600
scene.render.resolution_y = 760
scene.render.film_transparent = False
scene.render.filepath = str(OUT_PNG)
bpy.ops.render.render(write_still=True)

OUT_BLEND.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(OUT_BLEND))
print("GLB:", OUT_GLB, OUT_GLB.stat().st_size, "bytes")
print("PNG:", OUT_PNG)
print("BLEND:", OUT_BLEND)
