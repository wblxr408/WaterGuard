/* 进入工作台时的「波光粼粼」转场：从点击处荡开一片发光的水面盖住整屏，页面在水面下切换，
   再从同一处退开，露出新页面。全屏 WebGL 画布直接挂在 body 上，不随首页卸载而消失。
   「减少动画」或浏览器没有 WebGL 时直接切换。 */

const VERT = `attribute vec2 p;
void main() { gl_Position = vec4(p, 0.0, 1.0); }`;

const FRAG = `precision highp float;
uniform vec2 uRes;      // 画布像素尺寸
uniform vec2 uO;        // 波源（像素，左下为原点）
uniform float uT;       // 时间（秒）
uniform float uCover;   // 盖上来的水面半径（以对角线长为 1）
uniform float uReveal;  // 退开的半径

// 水底焦散：迭代的正弦扭曲（思路来自 Shadertoy「Tileable Water Caustic」）
float caustic(vec2 uv, float t) {
  vec2 p = mod(uv * 6.2831853, 6.2831853) - 250.0;
  vec2 i = p;
  float c = 1.0;
  float inten = 0.005;
  for (int n = 0; n < 5; n++) {
    float tt = t * (1.0 - (3.5 / float(n + 1)));
    i = p + vec2(cos(tt - i.x) + sin(tt + i.y), sin(tt - i.y) + cos(tt + i.x));
    c += 1.0 / length(vec2(p.x / (sin(i.x + tt) / inten), p.y / (cos(i.y + tt) / inten)));
  }
  c /= 5.0;
  c = 1.17 - pow(c, 1.4);
  return pow(abs(c), 8.0);
}

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }

void main() {
  vec2 frag = gl_FragCoord.xy;
  float d = distance(frag, uO) / length(uRes);
  float cover = 1.0 - smoothstep(uCover - 0.12, uCover, d);
  float hole = smoothstep(uReveal - 0.14, uReveal, d);
  float a = cover * hole;
  // 前沿的亮环
  float ring = exp(-pow((d - uCover) * 30.0, 2.0)) * step(d, 1.0)
             + exp(-pow((d - uReveal) * 24.0, 2.0)) * step(0.001, uReveal);
  // 同心水波扭曲焦散的采样位置
  vec2 dir = normalize(frag - uO + 0.0001);
  float wave = sin(d * 80.0 - uT * 9.0) * 0.014 * cover;
  vec2 uv = frag / uRes.y;
  float c = caustic(uv * 0.9 + dir * wave + vec2(0.0, uT * 0.05), uT * 0.55 + 23.0);
  vec3 deep = mix(vec3(0.015, 0.06, 0.18), vec3(0.03, 0.22, 0.5), frag.y / uRes.y);
  vec3 col = deep + vec3(0.45, 0.85, 1.0) * c * 1.15;
  // 碎光：稀疏的亮点随时间闪
  float h = hash(floor(frag / 5.0));
  float glint = pow(max(0.0, sin(uT * (6.0 + h * 10.0) + h * 40.0)), 24.0) * step(0.982, h) * (0.6 + c);
  col += vec3(0.8, 0.95, 1.0) * glint;
  col += vec3(0.65, 0.92, 1.0) * ring * 0.9;
  float alpha = clamp(a + ring * 0.5 * (1.0 - a), 0.0, 1.0);
  gl_FragColor = vec4(col * alpha, alpha);
}`;

const COVER_S = 0.6,
  SWAP_S = 0.62,
  REVEAL_FROM = 0.72,
  REVEAL_S = 0.95;
const easeInOut = (t: number) => {
  const x = Math.max(0, Math.min(1, t));
  return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
};

let running = false;

/** 从屏幕坐标 (x, y) 荡开水面；水面盖满时调用 swap() 切换页面。 */
export function playWaterTransition(x: number, y: number, swap: () => void) {
  if (running) return;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const canvas = document.createElement("canvas");
  const gl = reduce
    ? null
    : canvas.getContext("webgl", {
        premultipliedAlpha: true,
        antialias: false,
      });
  if (!gl) {
    swap();
    return;
  }
  const program = gl.createProgram()!;
  for (const [type, src] of [
    [gl.VERTEX_SHADER, VERT],
    [gl.FRAGMENT_SHADER, FRAG],
  ] as const) {
    const s = gl.createShader(type)!;
    gl.shaderSource(s, src);
    gl.compileShader(s);
    gl.attachShader(program, s);
  }
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    swap();
    return;
  }
  running = true;
  Object.assign(canvas.style, {
    position: "fixed",
    inset: "0",
    width: "100vw",
    height: "100vh",
    zIndex: "1000",
    pointerEvents: "auto",
  });
  canvas.setAttribute("aria-hidden", "true");
  const w = (canvas.width = innerWidth),
    h = (canvas.height = innerHeight);
  document.body.appendChild(canvas);

  gl.useProgram(program);
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(
    gl.ARRAY_BUFFER,
    new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]),
    gl.STATIC_DRAW,
  );
  const loc = gl.getAttribLocation(program, "p");
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  const u = (name: string) => gl.getUniformLocation(program, name);
  gl.viewport(0, 0, w, h);
  gl.uniform2f(u("uRes"), w, h);
  gl.uniform2f(u("uO"), x, h - y);

  const start = performance.now();
  let swapped = false;
  const frame = (now: number) => {
    const t = (now - start) / 1000;
    if (!swapped && t >= SWAP_S) {
      swapped = true;
      swap();
    }
    gl.uniform1f(u("uT"), t);
    gl.uniform1f(u("uCover"), easeInOut(t / COVER_S) * 1.2);
    gl.uniform1f(u("uReveal"), easeInOut((t - REVEAL_FROM) / REVEAL_S) * 1.25);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    if (t < REVEAL_FROM + REVEAL_S + 0.05) requestAnimationFrame(frame);
    else {
      canvas.remove();
      gl.getExtension("WEBGL_lose_context")?.loseContext();
      running = false;
    }
  };
  requestAnimationFrame(frame);
}

/** 从点击事件取波源：鼠标点哪儿从哪儿荡开；键盘触发（没有坐标）时从按钮中心。 */
export function rippleFrom(e: {
  clientX: number;
  clientY: number;
  currentTarget: Element;
}) {
  if (e.clientX || e.clientY) return [e.clientX, e.clientY] as const;
  const r = e.currentTarget.getBoundingClientRect();
  return [r.left + r.width / 2, r.top + r.height / 2] as const;
}
