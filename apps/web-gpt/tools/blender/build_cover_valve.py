"""封面用闸阀拆解动画（Blender 5.2，后台运行）。

DN300 明杆楔式闸阀，按常见规格比例建模（米），10 个零件各自独立，便于拆解：
  阀体（端法兰带螺栓孔）、闸板、阀杆、阀盖垫片、阀盖、阀盖螺栓、填料压盖、支架、阀杆螺母、手轮。

输出到 public/cover/valve/：
  f00.webp … f71.webp  边转边拆的序列帧（竖幅、透明底，Cycles）：前 10 帧为装配态，之后各零件错开起步、沿阀杆方向拆开；
                       网页再按滚动把整只阀转向对角线方向
  lines.webp           拆开后的工程线稿（Freestyle，透明底，只有线）
  anchors.json         每一帧各零件标注点在画面上的位置（0~1，左上为原点），网页据此画引线

用法（仓库根目录）：
  blender -b --python apps/web-gpt/tools/blender/build_cover_valve.py -- [preview] [lines]
  传 preview 时只出 12 帧低清图，检查构图；传 lines 时只重渲线稿（不动序列帧和 anchors.json）。
"""
import json
import math
import sys
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve()
WEB = HERE.parents[2]
OUT = WEB / "public" / "cover" / "valve"
ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PREVIEW = "preview" in ARGS
LINES_ONLY = "lines" in ARGS
FRAMES = 12 if PREVIEW else 72
W, H = (380, 700) if PREVIEW else (760, 1400)

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
RX = (0, math.pi / 2, 0)  # 圆柱轴沿 X
OUT.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------ 材质
def material(name, color, metallic=0.0, rough=0.5, coat=0.0, bump=0.0, bump_scale=80.0):
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


M = {
    "epoxy": material("WG_EpoxyBlue", (0.02, 0.23, 0.6), rough=0.3, coat=0.7, bump=0.06, bump_scale=160),
    "wheel": material("WG_WheelRed", (0.72, 0.05, 0.04), rough=0.3, coat=0.6),
    "zinc": material("WG_Galvanized", (0.58, 0.6, 0.62), metallic=1.0, rough=0.38, bump=0.06, bump_scale=300),
    "steel": material("WG_Stainless", (0.66, 0.68, 0.7), metallic=1.0, rough=0.22),
    "bronze": material("WG_Bronze", (0.62, 0.4, 0.17), metallic=1.0, rough=0.3),
    "rubber": material("WG_EPDM", (0.03, 0.03, 0.035), rough=0.7),
    "wedge": material("WG_WedgeIron", (0.13, 0.17, 0.23), metallic=0.3, rough=0.4, coat=0.3),
}


# ------------------------------------------------------------------ 几何工具
def _finish(o, mat, smooth=True, angle=32):
    if mat is not None:
        o.data.materials.append(mat)
    if smooth:
        bpy.ops.object.select_all(action="DESELECT")
        o.select_set(True)
        bpy.context.view_layer.objects.active = o
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(angle))
        o.select_set(False)
    return o


def cyl(r, depth, loc, mat, rot=(0, 0, 0), verts=64, fill="NGON", smooth=True):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=r, depth=depth, location=loc, rotation=rot,
                                        end_fill_type=fill)
    return _finish(bpy.context.active_object, mat, smooth)


def box(size, loc, mat, rot=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc, rotation=rot)
    o = bpy.context.active_object
    o.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    mod = o.modifiers.new("Bevel", "BEVEL")
    mod.width = min(size) * 0.18
    mod.segments = 3
    return _finish(o, mat, smooth=True, angle=40)


def torus(major, minor, loc, mat, rot=(0, 0, 0), seg=(96, 16)):
    bpy.ops.mesh.primitive_torus_add(major_radius=major, minor_radius=minor, major_segments=seg[0],
                                     minor_segments=seg[1], location=loc, rotation=rot)
    return _finish(bpy.context.active_object, mat)


def sphere(r, loc, mat, scale=(1, 1, 1)):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=r, location=loc)
    o = bpy.context.active_object
    o.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return _finish(o, mat)


def tube(r_out, r_in, depth, loc, mat, rot=(0, 0, 0), verts=96):
    o = cyl(r_out, depth, loc, mat, rot=rot, verts=verts, fill="NOTHING")
    mod = o.modifiers.new("Wall", "SOLIDIFY")
    mod.thickness = r_out - r_in
    mod.offset = -1
    mod.use_even_offset = True
    return o


def apply_all(o):
    bpy.ops.object.select_all(action="DESELECT")
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.convert(target="MESH")
    o.select_set(False)
    return o


def cut(target, cutter):
    mod = target.modifiers.new("Cut", "BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cutter
    mod.solver = "EXACT"
    mod.use_self = True  # 目标由几块互相重叠的形状拼成，必须按自相交处理，否则重叠部分会被算丢
    apply_all(target)
    bpy.data.objects.remove(cutter, do_unlink=True)
    return target


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
    o.name = o.data.name = name
    bpy.ops.object.select_all(action="DESELECT")
    return o


def hex_nut(r, h, loc, mat, rot=(0, 0, 0)):
    return cyl(r, h, loc, mat, rot=rot, verts=6, smooth=False)


def ring_of(count, radius, make, start=0.0):
    """沿一圈放 count 个零件：make(角度, 位置偏移) 返回物体列表。"""
    out = []
    for i in range(count):
        a = start + i * 2 * math.pi / count
        out += make(a)
    return out


# ------------------------------------------------------------------ 零件（装配态位置；阀的竖直轴为 Z，管道轴为 X）
FLANGE_R, BOLT_R = 0.23, 0.2


def part_body():
    # 腔体：比通孔宽得多的椭球，削平顶部接阀盖法兰；两端短颈接端法兰
    chamber = sphere(1.0, (0, 0, -0.02), M["epoxy"], scale=(0.21, 0.225, 0.34))
    chamber = cut(chamber, box((1.0, 1.0, 0.5), (0, 0, 0.55), None))
    p = [chamber,
         cyl(0.19, 0.5, (0, 0, 0), M["epoxy"], rot=RX, verts=96),
         cyl(0.22, 0.04, (0, 0, 0.3), M["epoxy"], verts=96)]
    for x in (-0.27, 0.27):
        p.append(cyl(FLANGE_R, 0.04, (x, 0, 0), M["epoxy"], rot=RX, verts=96))
    for side in (-1, 1):  # 阀体两侧的铸造加强筋
        p.append(box((0.035, 0.05, 0.46), (0, side * 0.215, -0.02), M["epoxy"]))
    body = join(p, "Valve_Body")
    body = cut(body, cyl(0.15, 0.8, (0, 0, 0), None, rot=RX, verts=96))
    holes = join([cyl(0.013, 0.1, (x, BOLT_R * math.cos(a), BOLT_R * math.sin(a)), None, rot=RX, verts=16)
                  for x in (-0.27, 0.27) for a in [math.pi / 8 + k * math.pi / 4 for k in range(8)]], "Holes")
    body = cut(body, holes)
    top_holes = join([cyl(0.011, 0.08, (0.18 * math.cos(a), 0.18 * math.sin(a), 0.3), None, verts=16)
                      for a in [math.pi / 8 + k * math.pi / 4 for k in range(8)]], "TopHoles")
    return cut(body, top_holes)


def part_wedge():
    disc = cyl(0.165, 0.075, (0, 0, -0.01), M["wedge"], rot=RX, verts=96)
    neck = box((0.08, 0.1, 0.09), (0, 0, 0.19), M["wedge"])
    slot = box((0.085, 0.032, 0.05), (0, 0, 0.205), None)
    neck = cut(neck, slot)
    rings = [torus(0.128, 0.009, (x, 0, -0.01), M["bronze"], rot=RX, seg=(96, 12)) for x in (-0.039, 0.039)]
    return join([disc, neck] + rings, "Valve_Wedge")


def part_stem():
    p = [cyl(0.022, 0.96, (0, 0, 0.66), M["steel"], verts=32),
         box((0.07, 0.03, 0.025), (0, 0, 0.2), M["steel"])]  # 底部 T 形头，卡进闸板槽
    for k in range(38):  # 上段梯形螺纹
        p.append(torus(0.0235, 0.0045, (0, 0, 0.68 + k * 0.0115), M["steel"], seg=(32, 8)))
    return join(p, "Valve_Stem")


def part_gasket():
    return join([tube(0.205, 0.155, 0.007, (0, 0, 0.3215), M["rubber"])], "Valve_Gasket")


def part_bonnet():
    p = [cyl(0.22, 0.035, (0, 0, 0.3375), M["epoxy"], verts=96),
         cyl(0.155, 0.17, (0, 0, 0.44), M["epoxy"], verts=96),
         sphere(1.0, (0, 0, 0.525), M["epoxy"], scale=(0.155, 0.155, 0.075)),
         cyl(0.078, 0.12, (0, 0, 0.64), M["epoxy"], verts=64)]
    return join(p, "Valve_Bonnet")


def part_bonnet_bolts():
    def one(a):
        x, y = 0.18 * math.cos(a), 0.18 * math.sin(a)
        return [cyl(0.0105, 0.13, (x, y, 0.325), M["zinc"], verts=16),
                hex_nut(0.02, 0.018, (x, y, 0.369), M["zinc"]),
                hex_nut(0.02, 0.018, (x, y, 0.274), M["zinc"])]
    return join(ring_of(8, 0.18, one, start=math.pi / 8), "Valve_BonnetBolts")


def part_gland():
    plate = cyl(1.0, 0.022, (0, 0, 0.722), M["zinc"], verts=48)
    plate.scale = (0.12, 0.055, 1)
    bpy.ops.object.select_all(action="DESELECT")
    plate.select_set(True)
    bpy.context.view_layer.objects.active = plate
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    p = [plate, cyl(0.05, 0.05, (0, 0, 0.7), M["zinc"], verts=48)]
    for x in (-0.085, 0.085):
        p += [cyl(0.008, 0.07, (x, 0, 0.735), M["zinc"], verts=12),
              hex_nut(0.015, 0.014, (x, 0, 0.74), M["zinc"])]
    return join(p, "Valve_Gland")


def part_yoke():
    p = [cyl(0.105, 0.03, (0, 0, 0.765), M["epoxy"], verts=64)]
    for x in (-0.105, 0.105):
        p.append(box((0.04, 0.07, 0.36), (x, 0, 0.96), M["epoxy"]))
    p.append(box((0.27, 0.09, 0.06), (0, 0, 1.15), M["epoxy"]))
    p.append(cyl(0.055, 0.07, (0, 0, 1.17), M["epoxy"], verts=48))
    return join(p, "Valve_Yoke")


def part_stem_nut():
    return join([hex_nut(0.048, 0.05, (0, 0, 1.23), M["bronze"]),
                 cyl(0.032, 0.03, (0, 0, 1.268), M["bronze"], verts=32)], "Valve_StemNut")


def part_handwheel():
    z = 1.32
    p = [torus(0.24, 0.02, (0, 0, z), M["wheel"], seg=(128, 16)),
         cyl(0.055, 0.075, (0, 0, z), M["wheel"], verts=48),
         hex_nut(0.03, 0.025, (0, 0, z + 0.05), M["zinc"])]
    for k in range(5):
        a = k * 2 * math.pi / 5
        mid = Vector((0.145 * math.cos(a), 0.145 * math.sin(a), z - 0.012))
        bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=0.013, depth=0.2, location=mid,
                                            rotation=(0, math.pi / 2 - 0.12, a))
        p.append(_finish(bpy.context.active_object, M["wheel"]))
    return join(p, "Valve_Handwheel")


# 零件表：id、物体、拆开后的位移（阀的自身坐标，米）、起步帧偏移（错开起步）、标注点（自身坐标）
PARTS = [
    ("handwheel", part_handwheel(), (0, 0, 0.62), 0, (0.24, 0, 1.32)),
    ("stem_nut", part_stem_nut(), (0, 0, 0.5), 2, (0.048, 0, 1.23)),
    ("yoke", part_yoke(), (0, 0, 0.42), 4, (0.125, 0, 0.98)),
    ("gland", part_gland(), (0, 0, 0.3), 6, (0.12, 0, 0.722)),
    ("bonnet_bolts", part_bonnet_bolts(), (0, 0, 0.46), 7, (0.18, 0, 0.37)),
    ("bonnet", part_bonnet(), (0, 0, 0.1), 8, (0.15, 0, 0.5)),
    ("gasket", part_gasket(), (0, 0, 0.02), 10, (0.205, 0, 0.3215)),
    ("stem", part_stem(), (0, 0, 0.0), 0, (0.024, 0, 0.9)),
    ("wedge", part_wedge(), (0, 0, -0.02), 12, (0, -0.04, -0.01)),
    ("body", part_body(), (0, 0, -0.62), 12, (0.27, -0.16, 0.12)),
]

root = bpy.data.objects.new("Valve", None)
scene.collection.objects.link(root)
for _, obj, *_ in PARTS:
    obj.parent = root
REST = {pid: obj.location.copy() for pid, obj, *_ in PARTS}

# ------------------------------------------------------------------ 相机与灯光
cam_data = bpy.data.cameras.new("Cam")
cam_data.type = "ORTHO"
cam_data.ortho_scale = 3.15
cam = bpy.data.objects.new("Cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
target = Vector((0, 0, 0.42))
cam.location = target + Vector((1.0, -1.45, 0.62)).normalized() * 12
cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()

world = bpy.data.worlds.new("WG_Studio")
world.use_nodes = True
nt = world.node_tree
env = nt.nodes.new("ShaderNodeTexEnvironment")
env.image = bpy.data.images.load(str(Path(bpy.utils.resource_path("LOCAL")) / "datafiles" / "studiolights" / "world" / "studio.exr"))
mapping = nt.nodes.new("ShaderNodeMapping")
coord = nt.nodes.new("ShaderNodeTexCoord")
mapping.inputs["Rotation"].default_value = (0, 0, math.radians(120))
nt.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
nt.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
nt.links.new(env.outputs["Color"], nt.nodes["Background"].inputs["Color"])
scene.world = world
try:
    world.cycles_visibility.diffuse = False
except AttributeError:
    world.visibility.diffuse = False


def area_light(name, loc, energy, color, size):
    data = bpy.data.lights.new(name, "AREA")
    data.energy, data.color, data.size = energy, color, size
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    obj.location = loc
    obj.rotation_euler = (target - Vector(loc)).to_track_quat("-Z", "Y").to_euler()


area_light("Key", (-5, -7, 8), 2600, (1.0, 0.96, 0.9), 6)
area_light("Top", (0, -1, 10), 1500, (0.97, 0.98, 1.0), 10)
area_light("Rim", (6, 6, 4), 2000, (0.66, 0.72, 1.0), 5)
area_light("Fill", (3, -10, 2), 700, (0.9, 0.93, 1.0), 8)

scene.render.engine = "CYCLES"
scene.cycles.samples = 24 if PREVIEW else 96
scene.cycles.use_denoising = True
scene.render.film_transparent = True
scene.render.resolution_x, scene.render.resolution_y = W, H
scene.render.resolution_percentage = 100
scene.view_settings.view_transform = "AgX"
scene.view_settings.exposure = -0.6
for look in ("AgX - Medium High Contrast", "Medium High Contrast"):
    try:
        scene.view_settings.look = look
        break
    except TypeError:
        continue
img = scene.render.image_settings
img.file_format = "WEBP"
img.color_mode = "RGBA"
img.quality = 84
try:
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for backend in ("OPTIX", "CUDA"):
        prefs.compute_device_type = backend
        prefs.get_devices()
        if any(d.type == backend for d in prefs.devices):
            for d in prefs.devices:
                d.use = d.type == backend
            scene.cycles.device = "GPU"
            print("render device:", backend)
            break
except Exception as exc:  # noqa: BLE001
    print("GPU setup skipped:", exc)


# ------------------------------------------------------------------ 动画：装配态停留 → 各零件错开起步拆开，同时整体缓慢转动
def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * t * (t * (6 * t - 15) + 10)  # smootherstep


def pose(f):
    """第 f 帧（0..FRAMES-1）：设置转角和各零件位置，返回拆开程度 0~1（以手轮为准）。"""
    u = f / (FRAMES - 1)
    root.rotation_euler = (0, 0, math.radians(-28 + 46 * u))
    scale = FRAMES / 72
    for pid, obj, delta, lag, _ in PARTS:
        start = (10 + lag) * scale
        k = ease((f - start) / (40 * scale))
        obj.location = REST[pid] + Vector(delta) * k
    return ease((f - 10 * scale) / (40 * scale))


anchors = {"frames": FRAMES, "width": W, "height": H, "parts": [p[0] for p in PARTS], "points": []}
digits = 2
for f in range(0 if LINES_ONLY else FRAMES):
    pose(f)
    bpy.context.view_layer.update()
    row = []
    for pid, obj, _, _, anchor in PARTS:
        world_co = root.matrix_world @ (Vector(anchor) + (obj.location - REST[pid]))
        x, y, _ = world_to_camera_view(scene, cam, world_co)
        row.append([round(x, 4), round(1 - y, 4)])
    anchors["points"].append(row)
    scene.render.filepath = str(OUT / f"f{f:0{digits}d}.webp")
    bpy.ops.render.render(write_still=True)
    print(f"frame {f + 1}/{FRAMES}", flush=True)

if not LINES_ONLY:
    (OUT / "anchors.json").write_text(json.dumps(anchors, separators=(",", ":")), encoding="utf-8")

# ------------------------------------------------------------------ 拆开后的工程线稿：表面全部挖空（透明），只留 Freestyle 线
pose(FRAMES - 1)
holdout = bpy.data.materials.new("WG_Holdout")
holdout.use_nodes = True
hn = holdout.node_tree
hn.nodes.remove(hn.nodes["Principled BSDF"])
hold = hn.nodes.new("ShaderNodeHoldout")
hn.links.new(hold.outputs["Holdout"], hn.nodes["Material Output"].inputs["Surface"])
for _, obj, *_ in PARTS:
    for slot in obj.material_slots:
        slot.material = holdout
scene.world = None
scene.render.use_freestyle = True
scene.render.line_thickness_mode = "ABSOLUTE"
scene.render.line_thickness = 1.4 if PREVIEW else 2.6
fs = scene.view_layers[0].freestyle_settings
fs.crease_angle = math.radians(140)
lineset = fs.linesets[0] if len(fs.linesets) else fs.linesets.new("Lines")
lineset.select_silhouette = True
lineset.select_border = True
lineset.select_crease = True
if lineset.linestyle is None:
    lineset.linestyle = bpy.data.linestyles.new("WG_Line")
lineset.linestyle.color = (0.03, 0.05, 0.16)  # 品牌深蓝（显示约 #2b3a67）
scene.cycles.samples = 1
scene.render.filepath = str(OUT / "lines.webp")
bpy.ops.render.render(write_still=True)
print("DONE", OUT)
