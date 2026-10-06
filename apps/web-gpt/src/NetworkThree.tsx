import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import type { NetworkScene } from "@waterguard/contracts";
import type { Entity, LinkState } from "./Network";

export type NodeAppearance = {
  level: "ok" | "low" | "bad" | "na";
  violating: boolean;
  label: string;
};
export type LinkAppearance = {
  state: LinkState;
  flow: number | null;
  faultFraction: number | null;
  label: string;
};
export type ThreeView = {
  fit: () => void;
  top: () => void;
  zoom: (factor: number) => void;
  focus: (entity: Entity) => void;
};
type Props = {
  scene: NetworkScene;
  nodes: Map<string, NodeAppearance>;
  links: Map<string, LinkAppearance>;
  entity: Entity;
  labels: boolean;
  flowLayer: boolean;
  onSelect: (entity: Entity) => void;
  onUnavailable: () => void;
};
const tones = { ok: "#13ac9b", low: "#e49a28", bad: "#ed534f", na: "#9aa7ba" };
const stateTones = { open: "#a2b8ce", closed: "#c39564", isolated: "#8d9eac", leaking: "#ef9e38", ruptured: "#e76364" };
const key = (e: Entity) => `${e.kind}:${e.id}`;

/** Procedural geometry follows the hydraulic topology. All sizes and vertical positions are illustrative. */
const NetworkThree = forwardRef<ThreeView, Props>(function NetworkThree(props, ref) {
  const host = useRef<HTMLDivElement>(null);
  const annotation = useRef<HTMLDivElement>(null);
  const latest = useRef(props);
  latest.current = props;
  const api = useRef<ThreeView | null>(null);
  const refresh = useRef<(() => void) | null>(null);
  const [hover, setHover] = useState<Entity | null>(null);
  const hoverRef = useRef<Entity | null>(null);
  const [ready, setReady] = useState(false);
  useImperativeHandle(ref, () => ({
    fit: () => api.current?.fit(),
    top: () => api.current?.top(),
    zoom: (f) => api.current?.zoom(f),
    focus: (e) => api.current?.focus(e),
  }), []);

  useEffect(() => {
    const root = host.current;
    if (!root) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      latest.current.onUnavailable();
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.95;
    renderer.domElement.tabIndex = 0;
    renderer.domElement.setAttribute("aria-label", "三维供水管网。拖动旋转，右键平移，滚轮缩放；点击选择节点或管段。加减号缩放，0 适应全图，T 俯视，方向键旋转。");
    root.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 600);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false;
    controls.minDistance = 5;
    controls.maxDistance = 240;
    controls.maxPolarAngle = Math.PI / 2 - 0.05;
    controls.zoomSpeed = 0.75;
    controls.screenSpacePanning = true;
    const pmrem = new THREE.PMREMGenerator(renderer);
    const room = new RoomEnvironment();
    const environment = pmrem.fromScene(room, 0.04);
    scene.environment = environment.texture;
    scene.environmentIntensity = 0.75;
    room.dispose();
    pmrem.dispose();
    scene.add(new THREE.HemisphereLight(0xf5f7ff, 0x858fac, 1.4));
    const sun = new THREE.DirectionalLight(0xfff8ec, 3.2);
    sun.position.set(-25, 55, 20);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    Object.assign(sun.shadow.camera, { left: -46, right: 46, top: 46, bottom: -46, near: 1, far: 130 });
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = 0.04;
    sun.shadow.radius = 3;
    scene.add(sun);
    const rim = new THREE.DirectionalLight(0xc5d5ff, 2);
    rim.position.set(35, 18, -40);
    scene.add(rim);
    const geometries: THREE.BufferGeometry[] = [];
    const materials: THREE.Material[] = [];
    const geo = <T extends THREE.BufferGeometry>(g: T): T => { geometries.push(g); return g; };
    const mat = <T extends THREE.Material>(m: T): T => { materials.push(m); return m; };
    const metal = mat(new THREE.MeshStandardMaterial({ color: 0xb7c8d9, metalness: 0.82, roughness: 0.3 }));
    const darkMetal = mat(new THREE.MeshStandardMaterial({ color: 0x536784, metalness: 0.75, roughness: 0.35 }));
    const cylinder = geo(new THREE.CylinderGeometry(1, 1, 1, 12));
    const sphere = geo(new THREE.SphereGeometry(1, 16, 12));
    const ringGeometry = geo(new THREE.TorusGeometry(1, 0.045, 8, 36));
    const mesh = (g: THREE.BufferGeometry, m: THREE.Material, parent: THREE.Object3D = scene) => {
      const object = new THREE.Mesh(g, m);
      object.castShadow = object.receiveShadow = true;
      parent.add(object);
      return object;
    };
    const sourceNodes = props.scene.nodes;
    const xs = sourceNodes.map(n => n.x_m), ys = sourceNodes.map(n => n.y_m);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const span = Math.max(maxX - minX, maxY - minY, 1);
    const scale = 46 / span;
    const positions = new Map(sourceNodes.map(n => [n.id, new THREE.Vector3(
      (n.x_m - (minX + maxX) / 2) * scale, 0.55, -(n.y_m - (minY + maxY) / 2) * scale,
    )]));
    const width = Math.max(14, (maxX - minX) * scale + 8);
    const depth = Math.max(14, (maxY - minY) * scale + 8);
    const base = mesh(geo(new THREE.BoxGeometry(width, 0.38, depth)), mat(new THREE.MeshStandardMaterial({ color: 0xe1e6f2, roughness: 0.85, metalness: 0.08 })));
    base.position.y = -0.3;
    const gridPoints: number[] = [];
    for (let x = -width / 2; x <= width / 2; x += 2) gridPoints.push(x, -0.1, -depth / 2, x, -0.1, depth / 2);
    for (let z = -depth / 2; z <= depth / 2; z += 2) gridPoints.push(-width / 2, -0.1, z, width / 2, -0.1, z);
    const grid = new THREE.LineSegments(geo(new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(gridPoints, 3))), mat(new THREE.LineBasicMaterial({ color: 0xf8faff, transparent: true, opacity: 0.55 })));
    scene.add(grid);
    const anchors = new Map<string, THREE.Vector3>();
    const picks: THREE.Object3D[] = [];
    const nodeParts = new Map<string, { lamp: THREE.Mesh; halo: THREE.Mesh; material: THREE.MeshStandardMaterial }>();
    const linkParts = new Map<string, { pipe: THREE.Mesh; material: THREE.MeshStandardMaterial; arrow: THREE.Mesh; fault: THREE.Mesh; from: THREE.Vector3; to: THREE.Vector3 }>();
    const up = new THREE.Vector3(0, 1, 0);
    for (const link of props.scene.links) {
      const from = positions.get(link.from_node), to = positions.get(link.to_node);
      if (!from || !to) continue;
      const entity: Entity = { kind: "link", id: link.id };
      const group = new THREE.Group();
      group.userData.entity = entity;
      scene.add(group);
      const delta = to.clone().sub(from);
      const length = delta.length();
      const direction = delta.clone().normalize();
      const material = mat(metal.clone());
      const pipe = mesh(cylinder, material, group);
      pipe.position.copy(from).lerp(to, 0.5);
      pipe.quaternion.setFromUnitVectors(up, length > 1e-6 ? direction : up);
      pipe.scale.set(0.18, Math.max(length, 0.001), 0.18);
      pipe.userData.entity = entity;
      picks.push(pipe);
      for (const fraction of [0.12, 0.88]) {
        const collar = mesh(cylinder, darkMetal, group);
        collar.position.copy(from).lerp(to, fraction);
        collar.quaternion.copy(pipe.quaternion);
        collar.scale.set(0.235, Math.min(0.18, length * 0.15), 0.235);
        collar.userData.entity = entity;
        picks.push(collar);
      }
      // A fixed arrow encodes the simulated sign; it does not suggest a measured velocity.
      const arrow = mesh(geo(new THREE.ConeGeometry(0.18, 0.45, 12)), mat(new THREE.MeshStandardMaterial({ color: 0x35b6cc, metalness: 0.25, roughness: 0.3 })), group);
      arrow.position.copy(pipe.position).add(new THREE.Vector3(0, 0.17, 0));
      arrow.visible = false;
      const fault = mesh(ringGeometry, mat(new THREE.MeshBasicMaterial({ color: 0xf05c54 })), group);
      fault.rotation.x = -Math.PI / 2;
      fault.scale.setScalar(0.53);
      fault.visible = false;
      anchors.set(key(entity), pipe.position.clone());
      linkParts.set(link.id, { pipe, material, arrow, fault, from, to });
      if (link.kind !== "pipe") {
        const device = mesh(geo(new THREE.BoxGeometry(0.5, 0.42, 0.45)), darkMetal, group);
        device.position.copy(pipe.position).add(new THREE.Vector3(0, 0.12, 0));
        device.userData.entity = entity;
        picks.push(device);
      }
    }
    for (const node of sourceNodes) {
      const entity: Entity = { kind: "node", id: node.id };
      const position = positions.get(node.id)!;
      const group = new THREE.Group();
      group.position.copy(position);
      scene.add(group);
      const fitting = mesh(sphere, metal, group);
      fitting.scale.setScalar(0.32);
      fitting.userData.entity = entity;
      picks.push(fitting);
      const material = mat(new THREE.MeshStandardMaterial({ color: 0x13ac9b, emissive: 0x08786d, emissiveIntensity: 0.25, metalness: 0.25, roughness: 0.25 }));
      const lamp = mesh(sphere, material, group);
      lamp.position.y = 0.23;
      lamp.scale.set(0.22, 0.17, 0.22);
      lamp.userData.entity = entity;
      picks.push(lamp);
      const halo = mesh(ringGeometry, mat(new THREE.MeshBasicMaterial({ color: 0xf05b55 })), group);
      halo.rotation.x = -Math.PI / 2;
      halo.position.y = 0.28;
      halo.scale.setScalar(0.44);
      halo.visible = false;
      if (node.kind !== "junction") {
        const body = mesh(cylinder, mat(new THREE.MeshStandardMaterial({ color: 0x657de0, metalness: 0.65, roughness: 0.3 })), group);
        body.position.y = 0.75;
        body.scale.set(0.57, 1.5, 0.57);
        body.userData.entity = entity;
        picks.push(body);
        for (const y of [0.04, 1.5]) {
          const rim = mesh(cylinder, metal, group);
          rim.position.y = y;
          rim.scale.set(0.61, 0.08, 0.61);
          rim.userData.entity = entity;
          picks.push(rim);
        }
        const water = mesh(cylinder, mat(new THREE.MeshStandardMaterial({ color: 0x4edbd2, metalness: 0.2, roughness: 0.15 })), group);
        water.position.y = 1.52;
        water.scale.set(0.52, 0.025, 0.52);
      }
      anchors.set(key(entity), position.clone().add(new THREE.Vector3(0, node.kind === "junction" ? 0.3 : 1.65, 0)));
      nodeParts.set(node.id, { lamp, halo, material });
    }
    const selection = mesh(ringGeometry, mat(new THREE.MeshBasicMaterial({ color: 0x6478ef, transparent: true, opacity: 0.9 })));
    selection.rotation.x = -Math.PI / 2;
    selection.scale.setScalar(0.65);
    const labelNodes = new Map<string, HTMLSpanElement>();
    const sourceIds = new Set(sourceNodes.filter(n => n.kind !== "junction").map(n => n.id));
    for (const node of sourceNodes) {
      const label = document.createElement("span");
      label.className = `three-id${node.kind === "junction" ? "" : " three-source"}`;
      label.textContent = node.kind === "reservoir" ? `水源 ${node.id}` : node.kind === "tank" ? `水箱 ${node.id}` : `N${node.id}`;
      root.appendChild(label);
      labelNodes.set(node.id, label);
    }
    const labelLinks = new Map<string, HTMLSpanElement>();
    for (const link of props.scene.links) {
      const label = document.createElement("span");
      label.className = "three-id three-link-id";
      label.textContent = `P${link.id}`;
      label.hidden = true;
      root.appendChild(label);
      labelLinks.set(link.id, label);
    }
    const project = (point: THREE.Vector3, element: HTMLElement) => {
      const projected = point.clone().project(camera);
      const shown = projected.z > -1 && projected.z < 1 && Math.abs(projected.x) < 0.94 && Math.abs(projected.y) < 0.88;
      element.hidden = !shown;
      if (shown) {
        element.style.transform = `translate(${(projected.x + 1) * root.clientWidth / 2}px, ${(1 - projected.y) * root.clientHeight / 2}px)`;
        if (element === annotation.current && element.firstElementChild instanceof HTMLElement) {
          const label = element.firstElementChild;
          label.style.transform = `translate(${projected.x > 0.45 ? -label.offsetWidth - 10 : 10}px, -32px)`;
        }
      }
    };
    let disposed = false, raf = 0;
    const draw = () => {
      if (disposed) return;
      const p = latest.current;
      const flowMax = Math.max(1e-9, ...[...p.links.values()].map(l => Math.abs(l.flow ?? 0)));
      nodeParts.forEach(({ lamp, halo, material }, id) => {
        const appearance = p.nodes.get(id);
        const level = appearance?.level ?? "na";
        material.color.set(tones[level]);
        material.emissive.set(tones[level]);
        lamp.scale.setScalar(level === "bad" ? 0.28 : level === "low" ? 0.25 : 0.22);
        lamp.scale.y *= 0.78;
        halo.visible = !!appearance?.violating;
      });
      linkParts.forEach(({ pipe, material, arrow, fault, from, to }, id) => {
        const appearance = p.links.get(id);
        const state = appearance?.state ?? "open";
        const selected = p.entity.kind === "link" && p.entity.id === id;
        material.color.set(selected ? "#788cef" : state !== "open" ? stateTones[state] : p.flowLayer ? "#669fbe" : stateTones.open);
        material.emissive.set(selected ? "#4557ab" : "#000000");
        material.emissiveIntensity = selected ? 0.22 : 0;
        const radius = p.flowLayer && appearance?.flow != null ? 0.12 + 0.13 * Math.sqrt(Math.abs(appearance.flow) / flowMax) : 0.18;
        pipe.scale.x = pipe.scale.z = radius;
        arrow.visible = p.flowLayer && state === "open" && Math.abs(appearance?.flow ?? 0) > 1e-9;
        const direction = to.clone().sub(from).normalize().multiplyScalar((appearance?.flow ?? 0) < 0 ? -1 : 1);
        arrow.quaternion.setFromUnitVectors(up, direction.lengthSq() > 0 ? direction : up);
        fault.visible = appearance?.faultFraction != null;
        if (fault.visible) fault.position.copy(from).lerp(to, appearance!.faultFraction!).add(new THREE.Vector3(0, 0.22, 0));
      });
      const selectedPoint = anchors.get(key(p.entity));
      selection.visible = !!selectedPoint;
      if (selectedPoint) { selection.position.copy(selectedPoint); selection.position.y = 0.08; }
      renderer.render(scene, camera);
      const shown = hoverRef.current ?? p.entity;
      const anchor = anchors.get(key(shown));
      if (annotation.current && anchor) project(anchor, annotation.current);
      labelNodes.forEach((label, id) => {
        if (p.labels || sourceIds.has(id)) project(anchors.get(`node:${id}`)!, label);
        else label.hidden = true;
      });
      labelLinks.forEach((label, id) => {
        const anchor = anchors.get(`link:${id}`);
        if (p.labels && anchor) project(anchor, label);
        else label.hidden = true;
      });
    };
    const invalidate = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(draw); };
    refresh.current = invalidate;
    let fitView = true, topView = false;
    const fit = (top = false) => {
      fitView = true;
      topView = top;
      controls.target.set(0, 0.4, 0);
      const vFov = THREE.MathUtils.degToRad(camera.fov);
      const distance = Math.max(depth / (2 * Math.tan(vFov / 2)), width / (2 * Math.tan(vFov / 2) * camera.aspect)) * 1.08;
      camera.position.copy(new THREE.Vector3(top ? 0 : 0.22, top ? 1 : 0.95, top ? 0.001 : 1).normalize().multiplyScalar(distance));
      controls.update();
      invalidate();
    };
    api.current = {
      fit: () => fit(), top: () => fit(true),
      zoom: factor => {
        fitView = false;
        const offset = camera.position.clone().sub(controls.target);
        offset.setLength(THREE.MathUtils.clamp(offset.length() / factor, controls.minDistance, controls.maxDistance));
        camera.position.copy(controls.target).add(offset);
        controls.update(); invalidate();
      },
      focus: entity => {
        fitView = false;
        const part = entity.kind === "link" ? linkParts.get(entity.id) : null;
        const fraction = entity.kind === "link" ? latest.current.links.get(entity.id)?.faultFraction : null;
        const point = part && fraction != null ? part.from.clone().lerp(part.to, fraction) : anchors.get(key(entity));
        if (!point) return;
        const offset = camera.position.clone().sub(controls.target).setLength(Math.min(22, camera.position.distanceTo(controls.target)));
        controls.target.copy(point);
        camera.position.copy(point).add(offset);
        controls.update(); invalidate();
      },
    };
    let sized = false;
    const resize = new ResizeObserver(() => {
      if (!root.clientWidth || !root.clientHeight) return;
      camera.aspect = root.clientWidth / root.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(root.clientWidth, root.clientHeight);
      if (!sized || fitView) { fit(topView); sized = true; }
      else invalidate();
    });
    resize.observe(root);
    controls.addEventListener("change", invalidate);
    controls.addEventListener("start", () => { fitView = false; });
    const raycaster = new THREE.Raycaster();
    const pick = (event: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      raycaster.setFromCamera(new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1), camera);
      return raycaster.intersectObjects(picks, false)[0]?.object.userData.entity as Entity | undefined;
    };
    let down: { x: number; y: number; button: number } | null = null;
    const pointerDown = (e: PointerEvent) => { down = e.isPrimary ? { x: e.clientX, y: e.clientY, button: e.button } : null; };
    const pointerUp = (e: PointerEvent) => {
      if (down && down.button === 0 && Math.hypot(e.clientX - down.x, e.clientY - down.y) < 5) {
        const entity = pick(e);
        if (entity) latest.current.onSelect(entity);
      }
      down = null;
    };
    const pointerMove = (e: PointerEvent) => {
      if (e.buttons) return;
      const entity = pick(e) ?? null;
      if (entity?.id === hoverRef.current?.id && entity?.kind === hoverRef.current?.kind) return;
      hoverRef.current = entity;
      setHover(entity);
      renderer.domElement.style.cursor = entity ? "pointer" : "grab";
      invalidate();
    };
    const pointerLeave = () => { hoverRef.current = null; setHover(null); invalidate(); };
    const pointerCancel = () => { down = null; pointerLeave(); };
    const keyboard = (e: KeyboardEvent) => {
      const view = api.current!;
      if (["+", "="].includes(e.key)) view.zoom(1.3);
      else if (e.key === "-") view.zoom(1 / 1.3);
      else if (e.key === "0") view.fit();
      else if (e.key.toLowerCase() === "t") view.top();
      else if (e.key.startsWith("Arrow")) {
        fitView = false;
        const offset = camera.position.clone().sub(controls.target);
        const spherical = new THREE.Spherical().setFromVector3(offset);
        if (e.key === "ArrowLeft") spherical.theta -= 0.12;
        if (e.key === "ArrowRight") spherical.theta += 0.12;
        if (e.key === "ArrowUp") spherical.phi -= 0.12;
        if (e.key === "ArrowDown") spherical.phi += 0.12;
        spherical.phi = THREE.MathUtils.clamp(spherical.phi, 0.02, controls.maxPolarAngle);
        camera.position.copy(controls.target).add(new THREE.Vector3().setFromSpherical(spherical));
        controls.update(); invalidate();
      } else return;
      e.preventDefault();
    };
    const unavailable = (e: Event) => { e.preventDefault(); latest.current.onUnavailable(); };
    const canvas = renderer.domElement;
    canvas.addEventListener("pointerdown", pointerDown);
    canvas.addEventListener("pointerup", pointerUp);
    canvas.addEventListener("pointermove", pointerMove);
    canvas.addEventListener("pointerleave", pointerLeave);
    canvas.addEventListener("pointercancel", pointerCancel);
    canvas.addEventListener("keydown", keyboard);
    canvas.addEventListener("webglcontextlost", unavailable);
    setReady(true);
    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      resize.disconnect();
      controls.dispose();
      canvas.removeEventListener("pointerdown", pointerDown);
      canvas.removeEventListener("pointerup", pointerUp);
      canvas.removeEventListener("pointermove", pointerMove);
      canvas.removeEventListener("pointerleave", pointerLeave);
      canvas.removeEventListener("pointercancel", pointerCancel);
      canvas.removeEventListener("keydown", keyboard);
      canvas.removeEventListener("webglcontextlost", unavailable);
      geometries.forEach(g => g.dispose());
      materials.forEach(m => m.dispose());
      environment.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      canvas.remove();
      labelNodes.forEach(label => label.remove());
      labelLinks.forEach(label => label.remove());
      api.current = null;
      refresh.current = null;
    };
  }, [props.scene]);
  useEffect(() => { refresh.current?.(); }, [props.nodes, props.links, props.entity, props.labels, props.flowLayer, hover]);
  const shown = hover ?? props.entity;
  const text = shown.kind === "node" ? props.nodes.get(shown.id)?.label : props.links.get(shown.id)?.label;
  return <div className="network-three" ref={host} data-ready={ready}>
    <div className={`three-annotation${hover ? " is-hover" : ""}`} ref={annotation} aria-live="polite">
      <span>{text ?? `${shown.kind === "node" ? "节点" : "管段"} ${shown.id}`}</span>
    </div>
    {!ready && <div className="three-loading">正在构建三维管网…</div>}
  </div>;
});
export default NetworkThree;
