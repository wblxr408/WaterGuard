"""WaterGuard 设备模型 v2（Blender 5.2，后台运行）。

按真实设备比例（米）程序化建模，比 v1 细节更多：
  Pump         卧式端吸离心泵 + 全封闭风冷电机：散热筋、接线盒、铭牌、风罩栅格、联轴器护罩、
               轴承架、蜗壳、进出口法兰与螺栓、压力表、底座与地脚螺栓（Pump_Fan 可绕 X 轴转）
  Valve        法兰闸阀：环氧涂层阀体、螺栓、阀盖、支架、明杆、红色手轮（Valve_Wheel 可绕竖直轴转）
  Tank         钢制储水罐（四分之一剖切）：混凝土基础、加强圈、板缝、内衬、锥顶、护栏、通气帽、
               带护笼爬梯、液位计、出水管（Tank_Water 原点在水体底部，沿竖直方向缩放表示水位）
  Reservoir    清水池：混凝土池体、水面、格栅走道与栏杆、出水管（Reservoir_Water 同上）
  PipeSection  DN600 球墨铸铁管段：承插接口、水泥砂浆内衬、剖切展示、顶部爆管裂口与喷水（示意：
               斜向抛物线水柱，过顶点分三股断成水珠；元球建模，整股水是一张无缝曲面）

出水口统一朝本地 +X（三维沙盘约定）。只做设备，管网由前端按数据生成。

用法（仓库根目录）：
  blender -b --python apps/web-gpt/tools/blender/build_equipment.py
输出：
  apps/web-gpt/public/models/equipment.glb      （可下载的模型）
  apps/web-gpt/assets-src/equipment.blend       （源文件，含棚拍布景，供 render_pearl.py 渲染）
"""
import math
import random
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

HERE = Path(__file__).resolve()
WEB = HERE.parents[2]
OUT_GLB = WEB / "public" / "models" / "equipment.glb"
OUT_BLEND = WEB / "assets-src" / "equipment.blend"
random.seed(7)

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
RX = (0, math.pi / 2, 0)  # 圆柱轴沿 X
RY = (math.pi / 2, 0, 0)  # 圆柱轴沿 Y


# ------------------------------------------------------------------ 材质
def material(name, color, metallic=0.0, rough=0.5, coat=0.0, bump=0.0, bump_scale=80.0,
             transmission=0.0, ior=1.45, emission=None, strength=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1.0)
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = rough
    if coat:
        b.inputs["Coat Weight"].default_value = coat
        b.inputs["Coat Roughness"].default_value = 0.06
    if transmission:
        b.inputs["Transmission Weight"].default_value = transmission
        b.inputs["IOR"].default_value = ior
    if emission is not None:
        b.inputs["Emission Color"].default_value = (*emission, 1.0)
        b.inputs["Emission Strength"].default_value = strength
    if bump:
        tex = nt.nodes.new("ShaderNodeTexNoise")
        tex.inputs["Scale"].default_value = bump_scale
        tex.inputs["Detail"].default_value = 10.0
        node = nt.nodes.new("ShaderNodeBump")
        node.inputs["Strength"].default_value = bump
        node.inputs["Distance"].default_value = 0.002
        nt.links.new(tex.outputs["Fac"], node.inputs["Height"])
        nt.links.new(node.outputs["Normal"], b.inputs["Normal"])
    return m


def add_absorption(m, color, density):
    """给透射材质加体积吸收：越厚越蓝，水看得出来是水。"""
    nt = m.node_tree
    vol = nt.nodes.new("ShaderNodeVolumeAbsorption")
    vol.inputs["Color"].default_value = (*color, 1.0)
    vol.inputs["Density"].default_value = density
    out = nt.nodes["Material Output"]
    nt.links.new(vol.outputs["Volume"], out.inputs["Volume"])
    return m


M = {
    "base": material("WG_BaseSteel", (0.11, 0.12, 0.14), metallic=0.3, rough=0.45, coat=0.2),
    "motor": material("WG_MotorBlue", (0.04, 0.2, 0.55), metallic=0.1, rough=0.32, coat=0.6),
    "casing": material("WG_CastIron", (0.15, 0.19, 0.25), metallic=0.25, rough=0.5, bump=0.25, bump_scale=140),
    "steel": material("WG_Steel", (0.62, 0.64, 0.67), metallic=1.0, rough=0.28),
    "zinc": material("WG_Galvanized", (0.58, 0.6, 0.62), metallic=1.0, rough=0.42, bump=0.08, bump_scale=300),
    "guard": material("WG_SafetyYellow", (0.92, 0.6, 0.04), rough=0.38, coat=0.4),
    "plate": material("WG_Nameplate", (0.8, 0.82, 0.84), metallic=0.9, rough=0.25),
    "fan": material("WG_FanGray", (0.55, 0.57, 0.6), rough=0.5),
    "rubber": material("WG_Rubber", (0.03, 0.03, 0.035), rough=0.75),
    "valve": material("WG_EpoxyBlue", (0.02, 0.23, 0.6), rough=0.3, coat=0.7),
    "wheel": material("WG_WheelRed", (0.72, 0.05, 0.04), rough=0.3, coat=0.6),
    "shell": material("WG_TankShell", (0.6, 0.65, 0.72), metallic=0.2, rough=0.35, coat=0.3),
    "lining": material("WG_TankLining", (0.55, 0.72, 0.88), rough=0.4),
    "ring": material("WG_TankRing", (0.42, 0.48, 0.57), metallic=0.6, rough=0.35),
    "concrete": material("WG_Concrete", (0.56, 0.56, 0.54), rough=0.9, bump=0.35, bump_scale=60),
    "water": add_absorption(material("WG_Water", (0.7, 0.88, 1.0), rough=0.02, transmission=1.0, ior=1.333),
                            (0.16, 0.5, 0.92), 1.6),
    # 喷出的水：透明，折射棚拍环境；体积吸收让水柱厚处透出淡蓝
    "spray": add_absorption(material("WG_Spray", (0.8, 0.92, 1.0), rough=0.02, transmission=1.0, ior=1.333),
                            (0.2, 0.55, 0.95), 7.0),
    "pool": material("WG_PoolWater", (0.08, 0.34, 0.58), rough=0.04, coat=0.5, bump=0.05, bump_scale=6),
    "iron": material("WG_DuctileIron", (0.035, 0.04, 0.05), metallic=0.25, rough=0.5, coat=0.15, bump=0.15, bump_scale=120),
    "mortar": material("WG_Mortar", (0.66, 0.66, 0.64), rough=0.95, bump=0.4, bump_scale=200),
    "gauge": material("WG_GaugeFace", (0.95, 0.95, 0.93), rough=0.3),
    "needle": material("WG_Needle", (0.8, 0.05, 0.05), rough=0.4),
    "glass": material("WG_GaugeGlass", (0.95, 0.97, 1.0), rough=0.0, transmission=1.0, ior=1.5),
}


# ------------------------------------------------------------------ 几何工具
def _finish(o, mat, bevel=0.0, segments=2, smooth=True):
    if mat is not None:
        o.data.materials.append(mat)
    if smooth:
        bpy.context.view_layer.objects.active = o
        o.select_set(True)
        try:
            bpy.ops.object.shade_smooth_by_angle(angle=math.radians(32))
        except Exception:
            bpy.ops.object.shade_smooth()
        o.select_set(False)
    if bevel > 0:
        mod = o.modifiers.new("Bevel", "BEVEL")
        mod.width = bevel
        mod.segments = segments
        mod.limit_method = "ANGLE"
        mod.angle_limit = math.radians(35)
        mod.harden_normals = True
    return o


def cyl(r, depth, loc, mat, rot=(0, 0, 0), verts=48, bevel=0.0, fill="NGON", smooth=True, segments=2):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=r, depth=depth, location=loc, rotation=rot,
                                        end_fill_type=fill)
    o = bpy.context.active_object
    return _finish(o, mat, bevel, segments, smooth=smooth)


def box(size, loc, mat, rot=(0, 0, 0), bevel=0.0, segments=2):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc, rotation=rot)
    o = bpy.context.active_object
    o.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return _finish(o, mat, bevel, segments, smooth=False)


def torus(major, minor, loc, mat, rot=(0, 0, 0), seg=(64, 12)):
    bpy.ops.mesh.primitive_torus_add(major_radius=major, minor_radius=minor, major_segments=seg[0],
                                     minor_segments=seg[1], location=loc, rotation=rot)
    return _finish(bpy.context.active_object, mat)


def cone(r1, r2, depth, loc, mat, rot=(0, 0, 0), verts=64, fill="NGON"):
    bpy.ops.mesh.primitive_cone_add(vertices=verts, radius1=r1, radius2=r2, depth=depth, location=loc, rotation=rot,
                                    end_fill_type=fill)
    return _finish(bpy.context.active_object, mat)


def sphere(r, loc, mat, scale=(1, 1, 1), seg=(48, 24)):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=seg[0], ring_count=seg[1], radius=r, location=loc)
    o = bpy.context.active_object
    o.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return _finish(o, mat)


def apply_all(o):
    """应用该物体的全部修改器与旋转缩放。"""
    bpy.ops.object.select_all(action="DESELECT")
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.convert(target="MESH")
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    o.select_set(False)
    return o


def join(parts, name):
    parts = [p for p in parts if p is not None]
    for p in parts:
        apply_all(p)
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


def set_origin(o, point):
    bpy.ops.object.select_all(action="DESELECT")
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    scene.cursor.location = point
    bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
    o.select_set(False)


def group(name, children):
    root = bpy.data.objects.new(name, None)
    scene.collection.objects.link(root)
    for c in children:
        c.parent = root
    return root


def cut(target, cutter, keep_cutter=True):
    """布尔差集（精确求解），用于剖切与爆管裂口。"""
    mod = target.modifiers.new("Cut", "BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cutter
    mod.solver = "EXACT"
    apply_all(target)
    if not keep_cutter:
        bpy.data.objects.remove(cutter, do_unlink=True)
    return target


def bolt_circle(center, axis, radius, count, mat_bolt, size=0.022, length=0.05, start=0.0, yaw=0.0):
    """法兰螺栓：六角螺母 + 露出的螺柱，沿法兰面一圈。axis 为 'X'（可绕 Z 偏转 yaw）或 'Z'。"""
    parts = []
    for i in range(count):
        a = start + i * 2 * math.pi / count
        if axis == "X":
            u, v = radius * math.cos(a), radius * math.sin(a)
            loc = (center[0] - u * math.sin(yaw), center[1] + u * math.cos(yaw), center[2] + v)
            rot = (0, math.pi / 2, yaw)
            parts.append(cyl(size, length * 0.5, loc, mat_bolt, rot=rot, verts=6, bevel=0.003, smooth=False))
            parts.append(cyl(size * 0.55, length, loc, mat_bolt, rot=rot, verts=12, smooth=False))
        else:
            loc = (center[0] + radius * math.cos(a), center[1] + radius * math.sin(a), center[2])
            parts.append(cyl(size, length * 0.5, loc, mat_bolt, verts=6, bevel=0.003, smooth=False))
            parts.append(cyl(size * 0.55, length, loc, mat_bolt, verts=12, smooth=False))
    return parts


def flange(center, axis, r_out, thick, mat_flange, mat_bolt, bolts=8, yaw=0.0):
    rot = (0, math.pi / 2, yaw) if axis == "X" else (0, 0, 0)
    parts = [cyl(r_out, thick, center, mat_flange, rot=rot, verts=64, bevel=0.006)]
    parts += bolt_circle(center, axis, r_out * 0.78, bolts, mat_bolt, size=max(0.014, r_out * 0.1),
                         length=thick * 2.4, start=math.pi / bolts, yaw=yaw)
    return parts


def delete_faces(o, predicate):
    """删除满足条件（按面中心的世界坐标判断）的面，用于做半圆护罩。"""
    apply_all(o)
    bm = bmesh.new()
    bm.from_mesh(o.data)
    doomed = [f for f in bm.faces if predicate(o.matrix_world @ f.calc_center_median())]
    bmesh.ops.delete(bm, geom=doomed, context="FACES")
    bm.to_mesh(o.data)
    bm.free()
    return o


def solidify(o, thickness, offset=0.0, material_offset=0):
    mod = o.modifiers.new("Solidify", "SOLIDIFY")
    mod.thickness = thickness
    mod.offset = offset
    mod.use_even_offset = True
    if material_offset:
        mod.material_offset = material_offset
    return o


def smooth_all(o, mat):
    """整件平滑着色（水的曲面不要按角度分硬边）。"""
    o.data.materials.append(mat)
    bpy.ops.object.select_all(action="DESELECT")
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.shade_smooth()
    o.select_set(False)
    return o


# ------------------------------------------------------------------ 水泵
def build_pump():
    p = []
    ax = 0.56  # 轴心高度：蜗壳底部要高出导轨
    # 底座：钢板 + 两条导轨 + 地脚螺栓
    p.append(box((2.4, 0.86, 0.1), (0.0, 0, 0.05), M["base"], bevel=0.012))
    for y in (-0.31, 0.31):
        p.append(box((2.3, 0.09, 0.07), (0.0, y, 0.135), M["base"], bevel=0.01))
    for x in (-1.08, 0.0, 1.08):
        for y in (-0.36, 0.36):
            p.append(cyl(0.03, 0.04, (x, y, 0.12), M["zinc"], verts=6, bevel=0.003, smooth=False))
            p.append(cyl(0.042, 0.008, (x, y, 0.1), M["zinc"], verts=32))
    # 电机：机座 + 散热筋 + 端盖 + 机脚 + 接线盒 + 铭牌
    mx0, mx1 = -0.98, -0.18
    mlen, mc = mx1 - mx0, (mx0 + mx1) / 2
    p.append(cyl(0.27, mlen, (mc, 0, ax), M["motor"], rot=RX, verts=64, bevel=0.01))
    for i in range(32):
        a = i * 2 * math.pi / 32
        deg = math.degrees(a) % 360
        if 245 < deg < 295 or 75 < deg < 105:  # 下方机脚、上方接线盒处不排筋
            continue
        r = 0.292
        fin = box((mlen - 0.08, 0.012, 0.052), (mc, r * math.cos(a), ax + r * math.sin(a)), M["motor"],
                  rot=(a - math.pi / 2, 0, 0), bevel=0.004)
        p.append(fin)
    for x in (mx0 - 0.02, mx1 + 0.02):
        p.append(cyl(0.285, 0.05, (x, 0, ax), M["motor"], rot=RX, verts=64, bevel=0.012))
    for x in (mx0 + 0.16, mx1 - 0.16):
        p.append(box((0.13, 0.52, 0.2), (x, 0, 0.27), M["motor"], bevel=0.015))
    p.append(box((0.22, 0.2, 0.13), (mc, 0, ax + 0.33), M["motor"], bevel=0.018))
    p.append(box((0.18, 0.16, 0.025), (mc, 0, ax + 0.405), M["motor"], bevel=0.008))
    p.append(cyl(0.024, 0.06, (mc, 0.12, ax + 0.32), M["base"], rot=RY, verts=24))
    p.append(box((0.13, 0.004, 0.08), (mc + 0.05, 0.302, ax + 0.06), M["plate"], bevel=0.002))
    # 风罩（非驱动端）：罩壳 + 同心栅格 + 辐条
    fx = mx0 - 0.13
    shell = cyl(0.265, 0.2, (fx, 0, ax), M["motor"], rot=RX, verts=64, fill="NOTHING")
    solidify(shell, 0.01)
    p.append(shell)
    for rr in (0.07, 0.13, 0.19, 0.245):
        p.append(torus(rr, 0.008, (fx - 0.1, 0, ax), M["motor"], rot=RX, seg=(48, 8)))
    for k in range(6):
        a = k * math.pi / 3
        p.append(box((0.012, 0.012, 0.25), (fx - 0.1, 0.125 * math.cos(a), ax + 0.125 * math.sin(a)), M["motor"],
                     rot=(a - math.pi / 2, 0, 0)))
    body_motor = join(p, "Pump_Body")

    # 风扇（罩内可见，绕 X 轴转）：叶片先绕自身径向扭转一个攻角，再转到各自的方位
    fan_parts = [cyl(0.05, 0.05, (fx + 0.01, 0, ax), M["fan"], rot=RX, verts=32)]
    for k in range(8):
        a = k * math.pi / 4
        blade = box((0.018, 0.2, 0.06), (0, 0, 0), M["fan"])
        blade.rotation_mode = "YXZ"
        blade.rotation_euler = (a, 0.35, 0)
        blade.location = (fx + 0.01, 0.1 * math.cos(a), ax + 0.1 * math.sin(a))
        fan_parts.append(blade)
    fan = join(fan_parts, "Pump_Fan")
    set_origin(fan, (fx + 0.01, 0, ax))

    q = []
    # 轴、联轴器与黄色半圆护罩
    q.append(cyl(0.035, 0.42, (0.0, 0, ax), M["steel"], rot=RX, verts=32))
    q.append(cyl(0.09, 0.1, (-0.02, 0, ax), M["steel"], rot=RX, verts=48, bevel=0.006))
    q.append(cyl(0.012, 0.11, (-0.02, 0, ax), M["rubber"], rot=RX, verts=24))
    guard = cyl(0.17, 0.34, (-0.01, 0, ax), M["guard"], rot=RX, verts=64, fill="NOTHING")
    delete_faces(guard, lambda c: c.z < ax - 0.01)
    solidify(guard, 0.008)
    q.append(guard)
    for x in (-0.17, 0.15):
        q.append(box((0.02, 0.36, 0.18), (x, 0, ax - 0.09), M["guard"], bevel=0.004))
    # 轴承架 + 支脚
    q.append(cyl(0.11, 0.3, (0.31, 0, ax), M["casing"], rot=RX, verts=48, bevel=0.01))
    q.append(box((0.22, 0.3, 0.32), (0.31, 0, 0.32), M["casing"], bevel=0.015))
    # 蜗壳：主盘 + 螺旋鼓包 + 出口竖管与法兰 + 进口轴向管与法兰 + 支脚
    vx = 0.66
    q.append(cyl(0.33, 0.25, (vx, 0, ax), M["casing"], rot=RX, verts=72, bevel=0.06, segments=4))
    q.append(torus(0.25, 0.12, (vx, 0.035, ax + 0.03), M["casing"], rot=RX, seg=(72, 16)))
    q.append(cyl(0.1, 0.32, (vx, 0.12, ax + 0.43), M["casing"], verts=48, bevel=0.01))
    q += flange((vx, 0.12, ax + 0.6), "Z", 0.17, 0.035, M["casing"], M["zinc"], bolts=8)
    q.append(cyl(0.11, 0.26, (vx + 0.25, 0, ax), M["casing"], rot=RX, verts=48, bevel=0.01))
    q += flange((vx + 0.4, 0, ax), "X", 0.19, 0.035, M["casing"], M["zinc"], bolts=8)
    q.append(box((0.3, 0.42, 0.2), (vx, 0, 0.26), M["casing"], bevel=0.02))
    q.append(cyl(0.022, 0.04, (vx, 0, ax - 0.34), M["zinc"], verts=6, smooth=False))
    # 压力表（出口管侧）
    q.append(cyl(0.012, 0.12, (vx, 0.24, ax + 0.48), M["steel"], rot=RY, verts=16))
    q.append(cyl(0.05, 0.03, (vx, 0.31, ax + 0.48), M["steel"], rot=RY, verts=48, bevel=0.004))
    q.append(cyl(0.043, 0.004, (vx, 0.327, ax + 0.48), M["gauge"], rot=RY, verts=48))
    q.append(box((0.004, 0.002, 0.034), (vx + 0.008, 0.33, ax + 0.49), M["needle"], rot=(0, 0.6, 0)))
    casing = join(q, "Pump_Casing")
    return group("Pump", [body_motor, fan, casing])


# ------------------------------------------------------------------ 闸阀
def build_valve():
    p = []
    cz = 0.27  # 法兰下沿刚好落地
    p.append(sphere(0.2, (0, 0, cz + 0.02), M["valve"], scale=(1.05, 0.85, 1.2)))
    p.append(cyl(0.13, 0.8, (0, 0, cz), M["valve"], rot=RX, verts=48, bevel=0.01))
    for x in (-0.42, 0.42):
        p += flange((x, 0, cz), "X", 0.25, 0.05, M["valve"], M["zinc"], bolts=8)
    # 阀盖 + 阀盖法兰
    p.append(cyl(0.15, 0.3, (0, 0, cz + 0.38), M["valve"], verts=48, bevel=0.03))
    p += flange((0, 0, cz + 0.24), "Z", 0.19, 0.035, M["valve"], M["zinc"], bolts=8)
    # 支架（两立臂 + 顶梁）与阀杆螺母
    for x in (-0.1, 0.1):
        p.append(box((0.045, 0.07, 0.4), (x, 0, cz + 0.72), M["valve"], bevel=0.008))
    p.append(box((0.27, 0.09, 0.055), (0, 0, cz + 0.93), M["valve"], bevel=0.01))
    p.append(cyl(0.045, 0.06, (0, 0, cz + 0.98), M["zinc"], verts=6, bevel=0.004, smooth=False))
    # 明杆
    p.append(cyl(0.022, 0.78, (0, 0, cz + 0.9), M["steel"], verts=24))
    body = join(p, "Valve_Body")
    wz = cz + 1.07
    w = [torus(0.26, 0.022, (0, 0, wz), M["wheel"], seg=(72, 12)),
         cyl(0.055, 0.07, (0, 0, wz), M["wheel"], verts=32, bevel=0.008)]
    for k in range(5):
        a = k * 2 * math.pi / 5
        w.append(box((0.24, 0.026, 0.022), (0.13 * math.cos(a), 0.13 * math.sin(a), wz - 0.012), M["wheel"],
                     rot=(0, 0.1, a), bevel=0.004))
    wheel = join(w, "Valve_Wheel")
    set_origin(wheel, (0, 0, wz))
    return group("Valve", [body, wheel])


# ------------------------------------------------------------------ 储水罐（四分之一剖切）
def build_tank():
    R, z0, h = 1.15, 0.2, 2.4
    found = cyl(1.36, 0.2, (0, 0, 0.1), M["concrete"], verts=96, bevel=0.03)
    # 罐壁：外漆 + 内衬（solidify 的内侧用第二个材质）
    shell = cyl(R, h, (0, 0, z0 + h / 2), M["shell"], verts=96, fill="NOTHING")
    shell.data.materials.append(M["lining"])
    solidify(shell, 0.035, offset=-1, material_offset=1)
    bottom = cyl(R - 0.01, 0.04, (0, 0, z0 + 0.02), M["lining"], verts=96)
    rings = [torus(R + 0.012, 0.022, (0, 0, z), M["ring"], seg=(96, 10)) for z in (z0 + 0.6, z0 + 1.2, z0 + 1.8)]
    seams = []
    for k in range(16):
        a = k * 2 * math.pi / 16
        seams.append(box((0.006, 0.03, h - 0.04), ((R + 0.004) * math.cos(a), (R + 0.004) * math.sin(a),
                                                    z0 + h / 2), M["ring"], rot=(0, 0, a)))
    roof = cone(R + 0.04, 0.16, 0.42, (0, 0, z0 + h + 0.21), M["shell"], verts=96, fill="NOTHING")
    roof.data.materials.append(M["lining"])
    solidify(roof, 0.03, offset=-1, material_offset=1)
    vent = [cyl(0.065, 0.2, (0, 0, z0 + h + 0.5), M["shell"], verts=32),
            cone(0.15, 0.03, 0.09, (0, 0, z0 + h + 0.62), M["ring"], verts=48)]
    # 护栏：立柱 + 两道横杆
    rail = []
    for k in range(18):
        a = k * 2 * math.pi / 18
        rail.append(cyl(0.014, 0.42, (1.07 * math.cos(a), 1.07 * math.sin(a), z0 + h + 0.25), M["zinc"], verts=12))
    rail += [torus(1.07, 0.014, (0, 0, z0 + h + 0.46), M["zinc"], seg=(96, 8)),
             torus(1.07, 0.011, (0, 0, z0 + h + 0.3), M["zinc"], seg=(96, 8))]
    # 剖切：去掉面向镜头的四分之一（x>0, y<0），露出内衬与水体；基础不切
    cutter = box((2.0, 2.0, 3.6), (1.0, -1.0, z0 + 1.8 + 0.02), None)
    pieces = [shell, bottom, roof] + rings + seams
    for piece in pieces:
        cut(piece, cutter)
    for piece in rail:
        cut(piece, cutter)
    body_parts = [found, shell, bottom, roof] + rings + seams + vent + rail
    # 爬梯（-X 侧）+ 护笼
    lx = -(R + 0.12)
    for y in (-0.21, 0.21):
        body_parts.append(cyl(0.018, 2.95, (lx, y, z0 + 1.45), M["zinc"], verts=12))
    for i in range(10):
        body_parts.append(cyl(0.012, 0.42, (lx, 0, z0 + 0.25 + i * 0.29), M["zinc"], rot=RY, verts=10))
    for z in (z0 + 1.25, z0 + 1.75, z0 + 2.25, z0 + 2.75):
        hoop = torus(0.36, 0.012, (lx - 0.27, 0, z), M["zinc"], seg=(48, 8))
        delete_faces(hoop, lambda c: c.x > lx - 0.02)
        body_parts.append(hoop)
    for a in (-1.0, -0.4, 0.4, 1.0):
        body_parts.append(cyl(0.01, 1.6, (lx - 0.27 - 0.36 * math.cos(a), 0.36 * math.sin(a), z0 + 2.0), M["zinc"],
                              verts=8))
    # 液位计（侧面竖管 + 浮标）
    ga = math.radians(125)
    gx, gy = (R + 0.1) * math.cos(ga), (R + 0.1) * math.sin(ga)
    body_parts.append(cyl(0.022, 2.1, (gx, gy, z0 + 1.2), M["glass"], verts=16))
    for z in (z0 + 0.2, z0 + 2.2):
        body_parts.append(box((0.12, 0.03, 0.03), ((R + 0.05) * math.cos(ga), (R + 0.05) * math.sin(ga), z),
                              M["zinc"], rot=(0, 0, ga)))
    body_parts.append(box((0.05, 0.05, 0.08), (gx, gy, z0 + 1.7), M["needle"], bevel=0.006))
    # 出水管 + 法兰（+X 偏后，避开剖口）
    oa = math.radians(18)
    ox, oy = math.cos(oa), math.sin(oa)
    body_parts.append(cyl(0.1, 0.38, ((R + 0.17) * ox, (R + 0.17) * oy, z0 + 0.4), M["shell"],
                          rot=(0, math.pi / 2, oa), verts=48))
    body_parts += flange(((R + 0.37) * ox, (R + 0.37) * oy, z0 + 0.4), "X", 0.17, 0.035, M["shell"], M["zinc"],
                         yaw=oa)
    body = join(body_parts, "Tank_Body")
    # 水体：同样剖切，切面能看到水
    water = cyl(R - 0.04, 2.0, (0, 0, z0 + 0.04 + 1.0), M["water"], verts=96)
    cut(water, cutter, keep_cutter=False)
    water.name = "Tank_Water"
    set_origin(water, (0, 0, z0 + 0.04))
    return group("Tank", [body, water])


# ------------------------------------------------------------------ 清水池
def build_reservoir():
    w, d, h, t = 3.2, 2.2, 0.8, 0.15
    basin = box((w, d, h), (0, 0, h / 2), M["concrete"], bevel=0.02)
    inner = box((w - 2 * t, d - 2 * t, h), (0, 0, h / 2 + 0.12), None)
    cut(basin, inner, keep_cutter=False)
    parts = [basin]
    # 走道（池壁一侧外挑）+ 格栅 + 栏杆
    wy = d / 2 + 0.3
    parts.append(box((w, 0.6, 0.05), (0, wy, h + 0.025), M["zinc"], bevel=0.005))
    for i in range(40):
        parts.append(box((0.012, 0.56, 0.03), (-w / 2 + 0.05 + i * (w - 0.1) / 39, wy, h + 0.065), M["zinc"]))
    for i in range(6):
        x = -w / 2 + 0.1 + i * (w - 0.2) / 5
        parts.append(cyl(0.02, 1.0, (x, wy + 0.27, h + 0.55), M["zinc"], verts=12))
        parts.append(box((0.05, 0.05, 0.3), (x, wy - 0.3, h - 0.15), M["zinc"]))
    parts.append(cyl(0.022, w, (0, wy + 0.27, h + 1.05), M["zinc"], rot=RX, verts=16))
    parts.append(cyl(0.016, w, (0, wy + 0.27, h + 0.6), M["zinc"], rot=RX, verts=12))
    # 出水管（+X）
    parts.append(cyl(0.12, 0.7, (w / 2 + 0.35, 0, 0.35), M["concrete"], rot=RX, verts=48))
    parts += flange((w / 2 + 0.72, 0, 0.35), "X", 0.2, 0.04, M["zinc"], M["zinc"])
    body = join(parts, "Reservoir_Body")
    water = box((w - 2 * t - 0.01, d - 2 * t - 0.01, 0.5), (0, 0, 0.12 + 0.25), M["pool"])
    water.name = "Reservoir_Water"
    set_origin(water, (0, 0, 0.12))
    return group("Reservoir", [body, water])


# ------------------------------------------------------------------ 管段剖面 + 爆管
def build_pipe_section():
    L, ro, rl, rb = 3.2, 0.33, 0.302, 0.29  # 管长、外径、内衬外径、内孔
    cz = ro + 0.12
    outer = cyl(ro, L, (0, 0, cz), M["iron"], rot=RX, verts=96, fill="NOTHING")
    solidify(outer, ro - rl, offset=-1)
    lining = cyl(rl, L, (0, 0, cz), M["mortar"], rot=RX, verts=96, fill="NOTHING")
    solidify(lining, rl - rb, offset=-1)
    # 承口（接口加粗的环，内孔贯通）+ 橡胶圈
    bell = cyl(0.405, 0.36, (-0.35, 0, cz), M["iron"], rot=RX, verts=96, fill="NOTHING")
    solidify(bell, 0.405 - ro, offset=-1)
    gasket = torus(0.336, 0.018, (-0.17, 0, cz), M["rubber"], rot=RX, seg=(96, 12))
    water = cyl(rb - 0.004, L - 0.02, (0, 0, cz), M["water"], rot=RX, verts=96)
    # 左段剖切：去掉前上四分之一（y<0、z>轴心），露出铁壁、砂浆内衬和水；剖到承口前为止，接口保持完整
    cutter = box((1.08, 1.0, 1.0), (-1.62 + 0.54, -0.5, cz + 0.5), None)
    for piece in (outer, lining, bell, water):
        cut(piece, cutter)
    bpy.data.objects.remove(cutter, do_unlink=True)
    # 右段顶部爆管裂口：纵向撕裂的不规则开口，略朝前上方（镜头一侧）偏转，看得见开口
    lean = math.radians(16)
    normal = Vector((0, -math.sin(lean), math.cos(lean)))
    axis = Vector((0.85, 0, cz))
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=1.0, location=axis + normal * ro,
                                         rotation=(lean, 0, 0))
    crack = bpy.context.active_object
    crack.scale = (0.4, 0.05, 0.22)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    tex = bpy.data.textures.new("CrackNoise", "CLOUDS")
    tex.noise_scale = 0.04
    disp = crack.modifiers.new("Jag", "DISPLACE")
    disp.texture = tex
    disp.strength = 0.05
    apply_all(crack)
    for piece in (outer, lining):
        cut(piece, crack)
    bpy.data.objects.remove(crack, do_unlink=True)
    # 两个混凝土支墩
    supports = []
    for x in (-1.05, 1.15):
        s = box((0.35, 0.8, cz), (x, 0, cz / 2), M["concrete"], bevel=0.015)
        hole = cyl(ro + 0.005, 0.5, (x, 0, cz), None, rot=RX, verts=96)
        cut(s, hole, keep_cutter=False)
        supports.append(s)
    body = join([outer, lining, bell, gasket] + supports, "PipeSection_Body")
    water.name = "PipeSection_Water"
    # 爆管喷水（示意）：裂口斜向喷出一股水柱，过了最高点分成三股、断成水珠
    spray = build_burst(axis, normal, ro)
    return group("PipeSection", [body, water, spray])


def build_burst(axis, normal, ro):
    """爆管喷水（示意）：裂口斜向喷出一股水柱，划一道抛物线，过了最高点分成三股、缩颈断成水珠落下；
    裂口里水往外涌，沿裂口还有几小股短促的溅水。全部用元球（metaball）建模：相邻的球自动融成连续光滑的水流，
    分开处自然缩颈成水滴，整股水是一张无缝曲面，没有穿插接缝。"""
    g = Vector((0, 0, -9.81))
    data = bpy.data.metaballs.new("PipeSection_Spray")
    data.resolution = data.render_resolution = 0.0055
    data.threshold = 0.6
    meta = bpy.data.objects.new("PipeSection_Spray", data)
    scene.collection.objects.link(meta)

    def ball(p, r):  # r 为单个球看得见的半径（硬度 2、阈值 0.6 时约为影响半径的 0.575 倍）
        el = data.elements.new()
        el.co = p
        el.radius = r / 0.575
        el.stiffness = 2.0

    def jitter(a):
        return Vector((random.gauss(0, a), random.gauss(0, a), random.gauss(0, a)))

    def path(p0, v, t_end, spacing, place):
        """沿抛物线按弧长等距放球：place(t, 进度 0~1, 位置)。"""
        t, last, dist = 0.0, None, spacing
        while t <= t_end:
            p = p0 + v * t + g * (0.5 * t * t)
            if last is not None:
                dist += (p - last).length
            last = p
            if dist >= spacing:
                place(t, t / t_end, p)
                dist = 0.0
            t += 0.0004

    root = axis + normal * (ro - 0.015)
    heading = Vector((1.0, -0.45, 0.0)).normalized()  # 朝右（管端方向）并朝镜头一侧
    side = Vector((0, 0, 1)).cross(heading).normalized()

    def launch(speed, elev_deg):
        e = math.radians(elev_deg)
        return (heading * math.cos(e) + Vector((0, 0, 1)) * math.sin(e)) * speed

    # 1. 主水柱：几股从同一处以不同角度、不同粗细喷出——根部融成一股粗水柱，越往上越分开成扇形的水弧；
    #    过了顶点变细、缩颈，断成一串水珠。参数：喷速 m/s、仰角°、朝镜头的偏移速度、持续时间 s、粗细系数
    for speed, elev, depth, t_end, thick in ((4.2, 84, -0.06, 0.64, 0.72), (4.7, 74, 0.0, 0.66, 1.0),
                                             (4.2, 63, 0.07, 0.5, 0.82), (3.9, 69, 0.13, 0.46, 0.5)):
        v0 = launch(speed, elev) + side * depth
        wobble = random.uniform(0, 2 * math.pi)
        t_split = t_end * random.uniform(0.62, 0.7)

        def place(t, f, p, t_end=t_end, wobble=wobble, t_split=t_split, thick=thick):
            if t < t_split:
                r = thick * (0.038 - 0.016 * t / t_split) * (1 + 0.08 * math.sin(36 * t + wobble))
                ball(p + jitter(0.0012), r)
            else:
                k = (t - t_split) / (t_end - t_split)
                pulse = 0.5 + 0.5 * math.cos(2 * math.pi * (2.5 + 2.5 * k) * k + wobble)  # 鼓包与缩颈交替
                r = (0.0075 + 0.011 * thick) * (1 - 0.3 * k) * (0.3 + 0.7 * pulse)
                if r > 0.0042:
                    ball(p + jitter(0.0018), r)

        path(root, v0, t_end, 0.008, place)
        # 末端甩出的几颗水滴，越往后越稀
        t = t_end
        for _ in range(random.randint(2, 4)):
            t += random.uniform(0.025, 0.05)
            ball(root + v0 * t + g * (0.5 * t * t) + jitter(0.01), (0.005 + 0.009 * thick) * random.uniform(0.8, 1.2))

    # 2. 裂口里往外涌的水：沿裂口一道鼓起的水面，和主水柱根部连成一体
    for i in range(57):
        x = -0.28 + 0.56 * i / 56
        bulge = math.sqrt(max(0.0, 1 - (x / 0.3) ** 2))
        ball(root + Vector((x, 0, 0)) + normal * (0.008 + 0.006 * bulge), 0.006 + 0.008 * bulge)

    # 3. 零星的细小水滴：水柱断裂处和裂口附近
    v_mid = launch(4.7, 74)
    for _ in range(10):
        t = random.uniform(0.45, 0.8)
        ball(root + v_mid * t + g * (0.5 * t * t) + side * random.uniform(-0.15, 0.15) + jitter(0.04),
             random.uniform(0.0025, 0.005))
    for _ in range(5):
        ball(root + normal * random.uniform(0.06, 0.16) + Vector((random.uniform(-0.25, 0.25), random.uniform(-0.05, 0.05), 0)),
             random.uniform(0.0025, 0.0045))

    bpy.ops.object.select_all(action="DESELECT")
    meta.select_set(True)
    bpy.context.view_layer.objects.active = meta
    bpy.ops.object.convert(target="MESH")
    spray = bpy.context.active_object
    for o in [o for o in bpy.data.objects if o.type == "META"]:
        bpy.data.objects.remove(o, do_unlink=True)
    spray.name = spray.data.name = "PipeSection_Spray"
    smooth_all(spray, M["spray"])
    spray.visible_shadow = False  # 水是透明的，不投硬阴影
    return spray


pump = build_pump()
valve = build_valve()
tank = build_tank()
reservoir = build_reservoir()
pipe = build_pipe_section()

# 每个设备的根节点留在原点导出，前端自行定位
OUT_GLB.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.export_scene.gltf(filepath=str(OUT_GLB), export_format="GLB", export_apply=True, export_texcoords=False)
print("GLB:", OUT_GLB, OUT_GLB.stat().st_size, "bytes")

# 棚拍布局（render_pearl.py 使用）：设备沿一条弧线摆开
tank.location = (-4.6, 1.1, 0)
pump.location = (-1.25, -0.2, 0)
valve.location = (1.35, -0.1, 0)
reservoir.location = (4.55, 1.2, 0)
reservoir.rotation_euler = (0, 0, math.radians(-8))
pipe.location = (0, -5.0, 0)  # 管段单独取景

bpy.ops.mesh.primitive_plane_add(size=400, location=(0, 0, 0))
floor = bpy.context.active_object
floor.name = "Preview_Floor"
floor.data.materials.append(material("WG_Floor", (0.9, 0.9, 0.93), rough=0.6))

cam_data = bpy.data.cameras.new("Cam")
cam = bpy.data.objects.new("Cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam

OUT_BLEND.parent.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.save_version = 0  # 覆盖保存时不留 .blend1 备份
bpy.ops.wm.save_as_mainfile(filepath=str(OUT_BLEND))
print("BLEND:", OUT_BLEND)
