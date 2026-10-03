"""首页开场主视觉（Blender 5.2，后台运行）。

  S 当前开场（用户 2026-10-03 定）：闸阀拆开、转半圈、再装回去的竖幅序列帧，滚动播放；第 0 帧就是开场画面
  以下三张是挑方向时的备选，首页已不引用：
  A 管道之河：一束镀铬与白瓷管道沿同一条中心线平行排布、缓缓扭转，穿过深蓝空间；其中一根是透光水管
  B 城市之下：白色素模城市立在磨砂玻璃地台上，地台下是发光的供水管网（走向取自 EPA Net3 真实拓扑）
  C 从图纸到实物：DN300 闸阀产品特写，另出一张同机位线稿，网页里由线稿“显影”成实物

用法（仓库根目录）：
  blender -b --python apps/web-gpt/tools/blender/build_hero.py -- S [preview]
  S 输出到 apps/web-gpt/public/cover/hero-valve/（f00.webp…、lines.webp、anchors.json）；preview 只出 12 帧低清，
    加 anchors 只重算 anchors.json、不重渲
  blender -b --python apps/web-gpt/tools/blender/build_hero.py -- A [preview]
输出：apps/web-gpt/public/cover/hero-a.webp（b、c 同理；C 另有 hero-c-lines.webp）；
      preview 时文件名加 -preview（1280×720、低采样）
      B、C 的标注点在画面上的位置写进 apps/web-gpt/src/cover-hero-anchors.ts
"""
import json
import math
import random
import re
import sys
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector

HERE = Path(__file__).resolve()
WEB = HERE.parents[2]
REPO = HERE.parents[4]
OUT = WEB / "public" / "cover"
ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
CONCEPT = next((a.upper() for a in ARGS if a.upper() in ("A", "B", "C", "S")), "A")
PREVIEW = "preview" in ARGS
W, H = (1280, 720) if PREVIEW else (2560, 1440)
random.seed(11)

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


# ------------------------------------------------------------------ 通用
def material(name, color, metallic=0.0, rough=0.4, coat=0.0, transmission=0.0, ior=1.45, emission=None,
             strength=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1.0)
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = rough
    if coat:
        b.inputs["Coat Weight"].default_value = coat
        b.inputs["Coat Roughness"].default_value = 0.04
    if transmission:
        b.inputs["Transmission Weight"].default_value = transmission
        b.inputs["IOR"].default_value = ior
    if emission is not None:
        b.inputs["Emission Color"].default_value = (*emission, 1.0)
        b.inputs["Emission Strength"].default_value = strength
    return m


def flowing(name, color, strength, waves=14.0, floor=0.35):
    """发光水芯：沿管长方向明暗起伏（mesh_tube 的 UV 横向即管长 0~1），看起来水在流。"""
    m = material(name, (0.02, 0.2, 0.6), emission=color, strength=strength)
    nt = m.node_tree
    coord = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(coord.outputs["UV"], sep.inputs["Vector"])
    mul = nt.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    nt.links.new(sep.outputs["X"], mul.inputs[0])
    mul.inputs[1].default_value = waves * 2 * math.pi
    sine = nt.nodes.new("ShaderNodeMath")
    sine.operation = "SINE"
    nt.links.new(mul.outputs["Value"], sine.inputs[0])
    span = nt.nodes.new("ShaderNodeMath")
    span.operation = "MULTIPLY_ADD"
    nt.links.new(sine.outputs["Value"], span.inputs[0])
    span.inputs[1].default_value = (1 - floor) / 2 * strength
    span.inputs[2].default_value = (1 + floor) / 2 * strength
    nt.links.new(span.outputs["Value"], nt.nodes["Principled BSDF"].inputs["Emission Strength"])
    return m


def tube(points, radius, mat, name, caps=True, resolution=6):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = radius
    cu.bevel_resolution = 10
    cu.use_fill_caps = caps
    sp = cu.splines.new("NURBS")
    sp.points.add(len(points) - 1)
    for p, co in zip(sp.points, points):
        p.co = (co[0], co[1], co[2], 1.0)
    sp.order_u = 4
    sp.use_endpoint_u = True
    sp.resolution_u = resolution
    sp.use_smooth = True
    o = bpy.data.objects.new(name, cu)
    scene.collection.objects.link(o)
    o.data.materials.append(mat)
    return o


def mesh_tube(points, radius, mat, name, ring=20):
    """沿点列扫出的网格管，UV 横向 = 管长 0~1（给流动的水芯用）。"""
    pts = [Vector(p) for p in points]
    n = len(pts)
    tangents = [(pts[min(k + 1, n - 1)] - pts[max(k - 1, 0)]).normalized() for k in range(n)]
    normal = Vector((0, 1, 0)).cross(tangents[0]).normalized()
    verts, faces = [], []
    for k, t in enumerate(tangents):
        normal = (normal - t * normal.dot(t)).normalized()
        side = t.cross(normal)
        for j in range(ring):
            a = 2 * math.pi * j / ring
            verts.append(pts[k] + (normal * math.cos(a) + side * math.sin(a)) * radius)
    for k in range(n - 1):
        for j in range(ring):
            j2 = (j + 1) % ring
            faces.append((k * ring + j, k * ring + j2, (k + 1) * ring + j2, (k + 1) * ring + j))
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], faces)
    uv = me.uv_layers.new(name="UVMap")
    for poly in me.polygons:
        for li in poly.loop_indices:
            vi = me.loops[li].vertex_index
            uv.data[li].uv = ((vi // ring) / (n - 1), (vi % ring) / ring)
    me.shade_smooth()
    o = bpy.data.objects.new(name, me)
    scene.collection.objects.link(o)
    o.data.materials.append(mat)
    return o


def catmull(points, samples):
    """过控制点的 Catmull-Rom 曲线，均匀取 samples 个点。"""
    pts = [Vector(p) for p in points]
    pts = [pts[0] * 2 - pts[1]] + pts + [pts[-1] * 2 - pts[-2]]
    segs = len(pts) - 3
    out = []
    for k in range(samples):
        u = k / (samples - 1) * segs
        i = min(int(u), segs - 1)
        t = u - i
        p0, p1, p2, p3 = pts[i:i + 4]
        out.append(0.5 * (2 * p1 + (p2 - p0) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                          + (3 * p1 - p0 - 3 * p2 + p3) * t * t * t))
    return out


def ribbon(center, radii, gap, twist, seed_dir=(0, 1, 0)):
    """沿中心线平行排开的一束管，返回每根管的点列；twist(s) 是绕切线的转角（弧度，s 为 0~1）。"""
    n = len(center)
    tangents = [(center[min(k + 1, n - 1)] - center[max(k - 1, 0)]).normalized() for k in range(n)]
    normal = Vector(seed_dir).cross(tangents[0]).normalized()
    across = []
    for k, t in enumerate(tangents):
        normal = (normal - t * normal.dot(t)).normalized()  # 平行移动，避免翻转
        across.append(Quaternion(t, twist(k / (n - 1))) @ normal)
    total = sum(2 * r for r in radii) + gap * (len(radii) - 1)
    offsets, acc = [], -total / 2
    for r in radii:
        offsets.append(acc + r)
        acc += 2 * r + gap
    return [[c + a * o for c, a in zip(center, across)] for o in offsets]


def area_light(name, loc, target, energy, color, size, size_y=None):
    data = bpy.data.lights.new(name, "AREA")
    data.energy, data.color = energy, color
    if size_y:
        data.shape = "RECTANGLE"
        data.size, data.size_y = size, size_y
    else:
        data.size = size
    o = bpy.data.objects.new(name, data)
    scene.collection.objects.link(o)
    o.location = loc
    o.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    o.visible_camera = False
    return o


def camera(loc, target, lens, focus=None, fstop=2.8, shift_x=0.0, shift_y=0.0):
    data = bpy.data.cameras.new("Cam")
    data.lens = lens
    data.shift_x, data.shift_y = shift_x, shift_y
    if focus is not None:
        data.dof.use_dof = True
        data.dof.focus_distance = focus
        data.dof.aperture_fstop = fstop
    o = bpy.data.objects.new("Cam", data)
    scene.collection.objects.link(o)
    o.location = loc
    o.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    scene.camera = o
    return o


def ramp(nodes, stops, interpolation="EASE"):
    r = nodes.new("ShaderNodeValToRGB")
    r.color_ramp.interpolation = interpolation
    els = r.color_ramp.elements
    els[0].position, els[0].color = stops[0][0], (*stops[0][1], 1)
    els[1].position, els[1].color = stops[-1][0], (*stops[-1][1], 1)
    for pos, col in stops[1:-1]:
        e = els.new(pos)
        e.color = (*col, 1)
    return r


def world(backdrop, env, bands=(), glow=None):
    """镜头直接看到的背景：屏幕坐标上下渐变 + 一团柔光（glow=(x, y, 半径, 颜色)）。
    反射与照明看到的环境：按高度角渐变，可加几圈亮灯带（(高度角下沿, 上沿, 强度)），让镀铬有干净的高光线。"""
    wd = bpy.data.worlds.new("Hero")
    wd.use_nodes = True
    nt = wd.node_tree
    nodes, links = nt.nodes, nt.links
    for n in list(nodes):
        if n.type != "OUTPUT_WORLD":
            nodes.remove(n)
    out = next(n for n in nodes if n.type == "OUTPUT_WORLD")
    coord = nodes.new("ShaderNodeTexCoord")

    # 镜头看到的
    win = nodes.new("ShaderNodeSeparateXYZ")
    links.new(coord.outputs["Window"], win.inputs["Vector"])
    vert = ramp(nodes, backdrop)
    links.new(win.outputs["Y"], vert.inputs["Fac"])
    color = vert.outputs["Color"]
    if glow:
        gx, gy, radius, gcol = glow
        dist = nodes.new("ShaderNodeVectorMath")
        dist.operation = "DISTANCE"
        links.new(coord.outputs["Window"], dist.inputs[0])
        dist.inputs[1].default_value = (gx, gy, 0)
        scale = nodes.new("ShaderNodeMath")
        scale.operation = "DIVIDE"
        links.new(dist.outputs["Value"], scale.inputs[0])
        scale.inputs[1].default_value = radius
        g = ramp(nodes, [(0.0, gcol), (1.0, (0, 0, 0))])
        links.new(scale.outputs["Value"], g.inputs["Fac"])
        add = nodes.new("ShaderNodeVectorMath")
        add.operation = "ADD"
        links.new(color, add.inputs[0])
        links.new(g.outputs["Color"], add.inputs[1])
        color = add.outputs["Vector"]
    bg_cam = nodes.new("ShaderNodeBackground")
    links.new(color, bg_cam.inputs["Color"])

    # 反射与照明看到的
    gen = nodes.new("ShaderNodeSeparateXYZ")
    links.new(coord.outputs["Generated"], gen.inputs["Vector"])
    fac = nodes.new("ShaderNodeMath")
    fac.operation = "MULTIPLY_ADD"
    links.new(gen.outputs["Z"], fac.inputs[0])
    fac.inputs[1].default_value = 0.5
    fac.inputs[2].default_value = 0.5
    sky = ramp(nodes, env)
    links.new(fac.outputs["Value"], sky.inputs["Fac"])
    bg_env = nodes.new("ShaderNodeBackground")
    links.new(sky.outputs["Color"], bg_env.inputs["Color"])
    env_shader = bg_env.outputs["Background"]
    for lo, hi, power in bands:
        f_lo, f_hi = (math.sin(math.radians(lo)) + 1) / 2, (math.sin(math.radians(hi)) + 1) / 2
        pad = (f_hi - f_lo) * 0.25
        b = ramp(nodes, [(f_lo - pad, (0, 0, 0)), (f_lo, (1, 1, 1)), (f_hi, (1, 1, 1)), (f_hi + pad, (0, 0, 0))])
        links.new(fac.outputs["Value"], b.inputs["Fac"])
        bg_band = nodes.new("ShaderNodeBackground")
        bg_band.inputs["Strength"].default_value = power
        links.new(b.outputs["Color"], bg_band.inputs["Color"])
        add_s = nodes.new("ShaderNodeAddShader")
        links.new(env_shader, add_s.inputs[0])
        links.new(bg_band.outputs["Background"], add_s.inputs[1])
        env_shader = add_s.outputs["Shader"]

    path = nodes.new("ShaderNodeLightPath")
    mix = nodes.new("ShaderNodeMixShader")
    links.new(path.outputs["Is Camera Ray"], mix.inputs["Fac"])
    links.new(env_shader, mix.inputs[1])
    links.new(bg_cam.outputs["Background"], mix.inputs[2])
    links.new(mix.outputs["Shader"], out.inputs["Surface"])
    scene.world = wd


def setup_render(samples, transparent=False, exposure=0.0):
    scene.render.engine = "CYCLES"
    scene.cycles.samples = max(32, samples // 5) if PREVIEW else samples
    scene.cycles.use_denoising = True
    scene.render.film_transparent = transparent
    scene.render.resolution_x, scene.render.resolution_y = W, H
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.exposure = exposure
    for look in ("AgX - Medium High Contrast", "Medium High Contrast"):
        try:
            scene.view_settings.look = look
            break
        except TypeError:
            continue
    img = scene.render.image_settings
    img.file_format = "WEBP"
    img.color_mode = "RGBA" if transparent else "RGB"
    img.quality = 86
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for backend in ("OPTIX", "CUDA"):
            prefs.compute_device_type = backend
            prefs.get_devices()
            if any(d.type == backend for d in prefs.devices):
                for d in prefs.devices:
                    d.use = d.type == backend
                scene.cycles.device = "GPU"
                break
    except Exception as exc:  # noqa: BLE001
        print("GPU setup skipped:", exc)


def save_anchor(key, co):
    """记下一个三维点在画面上的位置（0~1，左上为原点），写进 src/cover-hero-anchors.ts，网页据此画标注。"""
    from bpy_extras.object_utils import world_to_camera_view
    bpy.context.view_layer.update()
    x, y, _ = world_to_camera_view(scene, scene.camera, Vector(co))
    path = WEB / "src" / "cover-hero-anchors.ts"
    data = {}
    if path.exists():  # 读回已有的点（文件按 prettier 的格式写，键名可能不带引号）
        for k, u, v in re.findall(r'"?([\w:]+)"?\s*:\s*\[\s*([\d.]+)\s*,\s*([\d.]+)\s*\]',
                                  path.read_text(encoding="utf-8")):
            data[k] = [float(u), float(v)]
    data[key] = [round(x, 4), round(1 - y, 4)]
    rows = "".join(f"  {k if re.fullmatch(r'[A-Za-z_]\w*', k) else json.dumps(k)}: [{u}, {v}],\n"
                   for k, (u, v) in sorted(data.items()))
    path.write_text(
        "// 由 tools/blender/build_hero.py 生成：开场渲染图上标注点的位置（0~1，左上为原点），请勿手改\n"
        "const anchors: Record<string, [number, number]> = {\n" + rows + "};\nexport default anchors;\n",
        encoding="utf-8", newline="\n")


def render_to(stem):
    OUT.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(OUT / f"{stem}{'-preview' if PREVIEW else ''}.webp")
    bpy.ops.render.render(write_still=True)
    print("rendered", scene.render.filepath, flush=True)


# ------------------------------------------------------------------ A 管道之河
def concept_a():
    chrome = material("Chrome", (0.92, 0.94, 0.97), metallic=1.0, rough=0.07)
    satin = material("Satin", (0.8, 0.83, 0.88), metallic=1.0, rough=0.24)
    ceramic = material("Ceramic", (0.82, 0.85, 0.9), rough=0.32, coat=0.8)
    cobalt = material("Cobalt", (0.08, 0.26, 0.85), metallic=1.0, rough=0.16)
    glass = material("Glass", (0.92, 0.97, 1.0), rough=0.0, transmission=1.0, ior=1.5)
    water = flowing("Water", (0.12, 0.62, 1.0), 2.6)

    # 相机在 (0,-10,0) 朝 +y：x≈画面横向、z≈画面纵向、y≈纵深。管束从右下前景进画，向左弯到画面中右，再回卷向右上远处
    center = catmull([(5.2, -2.6, -3.4), (2.7, -0.6, -1.5), (0.95, 1.2, -0.25), (1.25, 3.0, 1.05),
                      (3.4, 4.2, 1.85), (6.6, 4.6, 2.5)], 140)
    radii = [0.045, 0.05, 0.075, 0.05, 0.045, 0.11, 0.045, 0.05, 0.075, 0.045, 0.05, 0.06, 0.045]
    mats = [chrome, satin, chrome, ceramic, chrome, None, chrome, cobalt, satin, chrome, ceramic, chrome, satin]
    lanes = ribbon(center, radii, 0.016, lambda s: math.radians(-35 + 330 * s * s * (3 - 2 * s)))
    for i, (pts, r, mat) in enumerate(zip(lanes, radii, mats)):
        if mat is None:  # 透光水管：玻璃外壁 + 发光水芯
            tube(pts, r, glass, f"Glass{i}", caps=False)
            mesh_tube(pts, r * 0.58, water, f"Water{i}")
        else:
            tube(pts, r, mat, f"Pipe{i}")

    world(backdrop=[(0.0, (0.004, 0.012, 0.045)), (0.55, (0.006, 0.02, 0.07)), (1.0, (0.002, 0.006, 0.022))],
          env=[(0.0, (0.0, 0.0, 0.002)), (0.47, (0.003, 0.008, 0.03)), (0.52, (0.05, 0.12, 0.34)),
               (0.6, (0.01, 0.025, 0.08)), (1.0, (0.004, 0.008, 0.03))],
          bands=[(16, 19, 7.0), (46, 49, 4.0)],
          glow=(0.66, 0.42, 0.62, (0.025, 0.07, 0.2)))
    area_light("Key", (-5, -4, 6), (1.5, 1.5, 0), 500, (1.0, 0.98, 0.95), 7, 1.2)
    area_light("Rim", (6, 9, 2), (1.5, 1.5, 0), 2200, (0.4, 0.62, 1.0), 5, 1.5)
    area_light("Under", (1, -3, -6), (1.5, 1.5, 0), 400, (0.3, 0.55, 1.0), 6)
    camera((0, -10, 0), (0, 0, 0), 50, focus=11.2, fstop=1.6)
    setup_render(320)
    render_to("hero-a")


# ------------------------------------------------------------------ B 城市之下
FAULT_LINK = "123"  # 首页演练的故障管段（Net3 管段 123，30 英寸干管），渲染成暖色


def read_inp(path):
    """读 EPANET .inp：节点坐标、管段（id, 起点, 终点, 管径 in）、水泵、水塔与水源。"""
    sec, coords, pipes, pumps, tanks, sources = None, {}, [], [], [], []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.split(";")[0].strip()
        if not line:
            continue
        if line.startswith("["):
            sec = line.upper()
            continue
        f = line.split()
        if sec == "[COORDINATES]":
            coords[f[0]] = (float(f[1]), float(f[2]))
        elif sec == "[PIPES]":
            pipes.append((f[0], f[1], f[2], float(f[4])))
        elif sec == "[PUMPS]":
            pumps.append((f[0], f[1], f[2], 16.0))
        elif sec == "[TANKS]":
            tanks.append(f[0])
        elif sec == "[RESERVOIRS]":
            sources.append(f[0])
    return coords, pipes, pumps, tanks, sources


def boxes_mesh(name, boxes, mat):
    """一组长方体合成一个网格（城市体块），boxes 为 (中心x, 中心y, 底z, 宽, 深, 高)。"""
    verts, faces = [], []
    for cx, cy, z0, sx, sy, h in boxes:
        b = len(verts)
        for z in (z0, z0 + h):
            for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                verts.append((cx + dx * sx / 2, cy + dy * sy / 2, z))
        faces += [(b, b + 3, b + 2, b + 1), (b + 4, b + 5, b + 6, b + 7), (b, b + 1, b + 5, b + 4),
                  (b + 1, b + 2, b + 6, b + 5), (b + 2, b + 3, b + 7, b + 6), (b + 3, b, b + 4, b + 7)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    o = bpy.data.objects.new(name, me)
    scene.collection.objects.link(o)
    o.data.materials.append(mat)
    return o


def pipe_net(name, segments, mat, z):
    """一批直管段合成一条曲线物体；每段两端点的 radius 决定粗细。"""
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = 1.0
    cu.bevel_resolution = 4
    cu.use_fill_caps = True
    for (x1, y1), (x2, y2), r in segments:
        sp = cu.splines.new("POLY")
        sp.points.add(1)
        sp.points[0].co, sp.points[1].co = (x1, y1, z, 1), (x2, y2, z, 1)
        sp.points[0].radius = sp.points[1].radius = r
    o = bpy.data.objects.new(name, cu)
    scene.collection.objects.link(o)
    o.data.materials.append(mat)
    return o


def seg_dist(p, a, b):
    ab = b - a
    t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_squared, 1e-9)))
    return (p - (a + ab * t)).length


def concept_b():
    coords, pipes, pumps, tanks, sources = read_inp(REPO / "data" / "networks" / "wntr-1.5.0" / "Net3.inp")
    xs = [c[0] for c in coords.values()]
    ys = [c[1] for c in coords.values()]
    cx, cy, k = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, 0.42

    def at(nid):
        x, y = coords[nid]
        return Vector(((x - cx) * k, (y - cy) * k))

    def radius(d):
        return 0.016 + 0.0013 * min(16.0 if d > 90 else d, 36.0)

    normal, fault, streets = [], [], []
    for pid, a, b, d in pipes + pumps:
        if a not in coords or b not in coords:
            continue
        seg = (tuple(at(a)), tuple(at(b)), radius(d))
        (fault if pid == FAULT_LINK else normal).append(seg)
        streets.append((at(a), at(b), radius(d)))

    glow = material("Glow", (0.02, 0.3, 0.8), emission=(0.1, 0.62, 1.0), strength=7.0)
    ember = material("Ember", (0.8, 0.3, 0.05), emission=(1.0, 0.42, 0.1), strength=34.0)
    clay = material("Clay", (0.82, 0.84, 0.88), rough=0.62)
    frost = material("Frost", (0.86, 0.93, 1.0), rough=0.22, transmission=1.0, ior=1.45)
    base = material("Base", (0.01, 0.018, 0.04), rough=0.4)
    pool = material("Pool", (0.02, 0.12, 0.35), rough=0.05, coat=1.0)

    lo_x, hi_x = min(xs) - cx, max(xs) - cx
    lo_y, hi_y = min(ys) - cy, max(ys) - cy
    sx, sy = (hi_x - lo_x) * k + 1.6, (hi_y - lo_y) * k + 1.6
    slab_h = 0.42
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, -slab_h / 2))
    slab = bpy.context.object
    slab.scale = (sx, sy, slab_h)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    slab.data.materials.append(frost)
    bevel = slab.modifiers.new("Edge", "BEVEL")  # 倒角给地台边一道细高光
    bevel.width, bevel.segments = 0.03, 3
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, -slab_h - 0.03))
    plinth = bpy.context.object
    plinth.scale = (sx + 0.02, sy + 0.02, 0.06)
    plinth.data.materials.append(base)
    pipe_net("Pipes", normal, glow, -slab_h * 0.55)
    pipe_net("Fault", fault, ember, -slab_h * 0.55)

    # 城市体块：落在管段之外的格子里（管段就是街道）。市中心成片、往外渐稀，边缘不撒零星小块
    rnd = random.Random(7)
    cell, blocks = 0.34, []
    town = Vector((0.3, 0.2))
    nx, ny = int(sx / cell) - 2, int(sy / cell) - 2
    for i in range(nx):
        for j in range(ny):
            p = Vector((-sx / 2 + cell * (i + 1.5), -sy / 2 + cell * (j + 1.5)))
            dc = (p - town).length
            edge = max(0.0, min(1.0, (5.0 - dc) / 2.2))
            if rnd.random() > edge * edge * (3 - 2 * edge) * 0.92:
                continue
            if any(seg_dist(p, a, b) < r + 0.17 for a, b, r in streets):
                continue
            core = math.exp(-(dc / 2.4) ** 2)
            h = 0.05 + (0.12 + 1.5 * core) * rnd.random() ** 1.5
            if rnd.random() < 0.06 * core:
                h += 0.7 + 0.6 * rnd.random()
            h = round(h / 0.045) * 0.045 + 0.045  # 按层高取整
            w = cell * rnd.uniform(0.7, 0.84)
            d = cell * rnd.uniform(0.7, 0.84)
            blocks.append((p.x, p.y, 0.0, w, d, h))
    boxes_mesh("City", blocks, clay)
    for t in tanks:  # 水塔
        p = at(t)
        bpy.ops.mesh.primitive_cylinder_add(radius=0.11, depth=0.42, location=(p.x, p.y, 0.21), vertices=48)
        bpy.context.object.data.materials.append(clay)
        bpy.ops.mesh.primitive_torus_add(major_radius=0.13, minor_radius=0.012, location=(p.x, p.y, 0.43))
        bpy.context.object.data.materials.append(glow)
    for s in sources:  # 水源：江、湖
        p = at(s)
        bpy.ops.mesh.primitive_cylinder_add(radius=0.42, depth=0.02, location=(p.x, p.y, 0.005), vertices=96)
        bpy.context.object.data.materials.append(pool)

    a, b, _ = fault[0]
    mid = (Vector(a) + Vector(b)) / 2
    lamp = bpy.data.lights.new("FaultGlow", "POINT")
    lamp.energy, lamp.color, lamp.shadow_soft_size = 240, (1.0, 0.45, 0.15), 0.1
    lo = bpy.data.objects.new("FaultGlow", lamp)
    scene.collection.objects.link(lo)
    lo.location = (mid.x, mid.y, 0.12)

    world(backdrop=[(0.0, (0.004, 0.012, 0.045)), (0.55, (0.006, 0.02, 0.07)), (1.0, (0.002, 0.006, 0.022))],
          env=[(0.0, (0.0, 0.0, 0.004)), (0.5, (0.02, 0.05, 0.13)), (0.7, (0.05, 0.09, 0.2)),
               (1.0, (0.03, 0.05, 0.12))],
          glow=(0.66, 0.5, 0.6, (0.02, 0.06, 0.17)))
    area_light("Key", (-14, -10, 22), (0, 0, 0), 9000, (0.92, 0.95, 1.0), 14)
    area_light("Rim", (16, 18, 10), (0, 0, 0), 7000, (0.45, 0.65, 1.0), 10)
    target = Vector((0, 0, 0))
    az, el, dist = math.radians(24), math.radians(36), 55
    loc = target + Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))) * dist
    camera(tuple(loc), tuple(target), 70, focus=dist, fstop=0.22, shift_x=-0.21)  # 城市整体偏右，左半屏留给标题
    setup_render(384)
    save_anchor(f"b_fault:{FAULT_LINK}", (mid.x, mid.y, 0.04))  # 键名带管段号，网页只在演练故障是这根管时标注
    render_to("hero-b")


# ------------------------------------------------------------------ C 从图纸到实物
VIEW = Vector((0.9, -1.6, 0.26)).normalized()  # 开场看闸阀的方向（右前方略高）


def valve_scene():
    """建 10 个零件（装配态，取自 build_cover_valve.py），换成开场用的白瓷 + 镀铬，配深蓝环境和灯光。
    返回该脚本的命名空间（PARTS、root、REST 等）。拆解章节里的真实涂装不受影响。"""
    src_path = HERE.parent / "build_cover_valve.py"
    src = src_path.read_text(encoding="utf-8")
    head = src.split("# ------------------------------------------------------------------ 相机与灯光")[0]
    head = head.replace("bpy.ops.wm.read_factory_settings(use_empty=True)", "")
    ns = {"__file__": str(src_path), "__name__": "valve_parts"}
    exec(compile(head, str(src_path), "exec"), ns)  # 只建零件，不渲它的序列帧

    def restyle(mat, color, metallic, rough, coat=0.0):
        b = mat.node_tree.nodes["Principled BSDF"]
        b.inputs["Base Color"].default_value = (*color, 1.0)
        b.inputs["Metallic"].default_value = metallic
        b.inputs["Roughness"].default_value = rough
        b.inputs["Coat Weight"].default_value = coat
    M = ns["M"]
    restyle(M["epoxy"], (0.8, 0.82, 0.86), 0.0, 0.22, coat=1.0)
    restyle(M["wheel"], (0.86, 0.88, 0.92), 1.0, 0.12)
    restyle(M["zinc"], (0.84, 0.86, 0.9), 1.0, 0.16)
    restyle(M["steel"], (0.9, 0.92, 0.95), 1.0, 0.08)
    restyle(M["bronze"], (0.88, 0.9, 0.93), 1.0, 0.1)
    restyle(M["wedge"], (0.05, 0.07, 0.1), 0.6, 0.3)

    world(backdrop=[(0.0, (0.004, 0.012, 0.045)), (1.0, (0.002, 0.006, 0.022))],
          env=[(0.0, (0.0, 0.0, 0.002)), (0.47, (0.004, 0.01, 0.035)), (0.52, (0.08, 0.17, 0.42)),
               (0.62, (0.015, 0.035, 0.1)), (1.0, (0.006, 0.012, 0.04))],
          bands=[(18, 22, 5.0), (50, 54, 2.5)])
    target = (0, 0, 0.55)
    area_light("Key", (-4.5, -5.5, 5.5), target, 1300, (1.0, 0.98, 0.95), 4.5, 1.2)
    area_light("RimL", (-4, 4.5, 2.2), target, 2400, (0.5, 0.75, 1.0), 0.6, 4)
    area_light("RimR", (4.8, 3.2, 1.6), target, 2600, (0.55, 0.8, 1.0), 0.6, 4)
    area_light("Under", (0.5, -3, -3), target, 260, (0.3, 0.55, 1.0), 4)
    return ns


def lines_pass(parts, path_stem, thickness):
    """同机位线稿：表面挖空，只留 Freestyle 线（浅青，压在深底上）。"""
    holdout = bpy.data.materials.new("Holdout")
    holdout.use_nodes = True
    hn = holdout.node_tree
    hn.nodes.remove(hn.nodes["Principled BSDF"])
    hold = hn.nodes.new("ShaderNodeHoldout")
    hn.links.new(hold.outputs["Holdout"], hn.nodes["Material Output"].inputs["Surface"])
    for obj in parts:
        for slot in obj.material_slots:
            slot.material = holdout
    scene.world = None
    scene.render.use_freestyle = True
    scene.render.line_thickness_mode = "ABSOLUTE"
    scene.render.line_thickness = thickness
    fs = scene.view_layers[0].freestyle_settings
    fs.crease_angle = math.radians(140)
    lineset = fs.linesets[0] if len(fs.linesets) else fs.linesets.new("Lines")
    lineset.select_silhouette = lineset.select_border = lineset.select_crease = True
    if lineset.linestyle is None:
        lineset.linestyle = bpy.data.linestyles.new("Line")
    lineset.linestyle.color = (0.55, 0.85, 1.0)
    scene.cycles.samples = 1
    render_to(path_stem)


def concept_c():
    ns = valve_scene()
    parts = [obj for _, obj, *_ in ns["PARTS"]]
    target = Vector((0, 0, 0.55))
    camera(tuple(target + VIEW * 10.4), tuple(target), 85, shift_x=-0.16)
    setup_render(256, transparent=True, exposure=-0.4)
    save_anchor("c_wheel", (0.2, 0, 1.33))
    save_anchor("c_body", (0, 0, -0.32))
    render_to("hero-c")
    lines_pass(parts, "hero-c-lines", 1.1 if PREVIEW else 2.0)


# ------------------------------------------------------------------ S 开场：闸阀拆开、转半圈、再装回去（滚动播放的序列帧）
def smoother(t):
    t = max(0.0, min(1.0, t))
    return t * t * t * (t * (6 * t - 15) + 10)


def concept_s():
    """竖幅透明底序列帧 public/cover/hero-valve/f00…：第 0 帧是装配好的闸阀（即开场画面），
    之后各零件错开起步沿阀杆方向拆开、整只阀转半圈、停一下，再按装配顺序（先阀体、最后手轮）装回去；
    镜头随拆开程度拉远再推近，让拆开后的整串零件也在画面里。
    另出第 0 帧的线稿 lines.webp（开场“显影”用），以及 anchors.json（每帧各零件标注点的位置）。"""
    frames = 12 if PREVIEW else 72
    w, h = (420, 630) if PREVIEW else (840, 1260)
    seq = OUT / "hero-valve"
    seq.mkdir(parents=True, exist_ok=True)
    ns = valve_scene()
    PARTS, root, REST = ns["PARTS"], ns["root"], ns["REST"]
    target = Vector((0, 0, 0.53))
    cam = camera(tuple(target + VIEW * 5.1), tuple(target), 85)
    setup_render(96, transparent=True, exposure=-0.4)
    scene.render.resolution_x, scene.render.resolution_y = w, h

    def pose(t):
        root.rotation_euler = (0, 0, math.radians(180 * smoother(t)))
        for pid, obj, delta, lag, _ in PARTS:
            l = lag / 12  # 原脚本的错开量（0~12 帧），归一化
            out = smoother((t - (0.06 + 0.1 * l)) / 0.28)
            back = smoother((t - (0.56 + 0.1 * (1 - l))) / 0.28)
            obj.location = REST[pid] + Vector(delta) * (out * (1 - back))
        spread = smoother((t - 0.04) / 0.36) * (1 - smoother((t - 0.6) / 0.36))
        cam.location = target + VIEW * 5.1 * (1 + 0.62 * spread)

    from bpy_extras.object_utils import world_to_camera_view
    only_anchors = "anchors" in ARGS  # 只重算 anchors.json，不重渲
    anchors = {"frames": frames, "width": w, "height": h, "parts": [p[0] for p in PARTS], "points": []}
    for f in range(frames):
        pose(f / (frames - 1))
        bpy.context.view_layer.update()
        row = []
        for pid, obj, _, _, anchor in PARTS:
            # 标注点取零件在阀杆中轴上的高度：转半圈时不会甩到另一侧，引线不横穿阀身
            co = root.matrix_world @ (Vector((0, 0, anchor[2])) + (obj.location - REST[pid]))
            x, y, _ = world_to_camera_view(scene, cam, co)
            row.append([round(x, 4), round(1 - y, 4)])
        anchors["points"].append(row)
        if f == 0:  # 装配态的上下沿：网页“显影”从这里扫到这里
            top = world_to_camera_view(scene, cam, Vector((0, 0, 1.4)))
            bottom = world_to_camera_view(scene, cam, Vector((0, 0, -0.33)))
            anchors["bounds0"] = [round(1 - top.y, 4), round(1 - bottom.y, 4)]
        if only_anchors:
            continue
        scene.render.filepath = str(seq / f"f{f:02d}.webp")
        bpy.ops.render.render(write_still=True)
        print(f"frame {f + 1}/{frames}", flush=True)
    (seq / "anchors.json").write_text(json.dumps(anchors, separators=(",", ":")), encoding="utf-8")
    if only_anchors:
        return

    pose(0)
    bpy.context.view_layer.update()
    lines_pass([obj for _, obj, *_ in PARTS], "hero-valve/lines", 0.9 if PREVIEW else 1.8)


CONCEPTS = {"A": concept_a, "B": concept_b, "C": concept_c, "S": concept_s}
CONCEPTS[CONCEPT]()
