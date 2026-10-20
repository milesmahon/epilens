import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

// Glass-brain scene (shader, lighting and views from lens/viewer.js)
export const bytes = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const typed = (s, T) => new T(bytes(s).buffer);

const glassVert = `
  varying vec3 vN; varying vec3 vV;
  void main() {
    vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
    vN = normalize(normalMatrix * normal); vV = normalize(-mvPosition.xyz);
    gl_Position = projectionMatrix * mvPosition;
  }`;
// Deep structures: glass that blends to opaque with colors for different structures
const glassFrag = `
  uniform vec3 color; uniform float base; uniform float rim; uniform float opacity; uniform float solid;
  varying vec3 vN; varying vec3 vV;
  void main() {
    float facing = abs(dot(normalize(vN), normalize(vV))), f = pow(1.0 - facing, 2.5);
    vec4 glassC = vec4(color * (0.55 + 0.7 * f), (base + rim * f) * opacity);
    vec4 solidC = vec4(color * (0.3 + 0.75 * facing) + 0.1 * pow(facing, 20.0), 1.0);
    gl_FragColor = mix(glassC, solidC, solid);
  }`;
const glass = (color, base, rim) =>
  new THREE.ShaderMaterial({
    vertexShader: glassVert,
    fragmentShader: glassFrag,
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    uniforms: {
      color: { value: new THREE.Color(color) },
      base: { value: base },
      rim: { value: rim },
      opacity: { value: 1 },
      solid: { value: 0 },
    },
  });

// Entry spots are painted onto the surface itself
export const MAX_ENTRIES = 64;
const cortexVert = `
  attribute float sulc;
  varying vec3 vN; varying vec3 vV; varying float vS; varying vec3 vW;
  void main() {
    vW = position;   // the mesh isn't transformed: model = world (MNI mm)
    vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
    vN = normalize(normalMatrix * normal); vV = normalize(-mvPosition.xyz); vS = clamp(sulc / 10.0, 0.0, 1.0);
    gl_Position = projectionMatrix * mvPosition;
  }`;
const cortexFrag = `
  uniform vec3 color; uniform float base; uniform float rim; uniform float opacity; uniform float solid;
  uniform vec3 entryAt[${MAX_ENTRIES}]; uniform vec3 entryDir[${MAX_ENTRIES}]; uniform int nEntries;
  uniform float entryR; uniform vec3 entryColor;
  varying vec3 vN; varying vec3 vV; varying float vS; varying vec3 vW;
  float entrySpot() {
    float s = 0.0;
    for (int i = 0; i < ${MAX_ENTRIES}; i++) {
      if (i >= nEntries) break;
      vec3 d = vW - entryAt[i];
      float t = dot(d, entryDir[i]), r = length(d - t * entryDir[i]);
      if (abs(t) < 3.0) s = max(s, 1.0 - smoothstep(entryR - 0.35, entryR + 0.35, r));
    }
    return s;
  }
  void main() {
    vec3 n = normalize(vN), v = normalize(vV);
    float facing = abs(dot(n, v)), f = pow(1.0 - facing, 2.5);
    vec3 glassC = color * (0.55 + 0.7 * f) * (1.0 - 0.35 * vS);
    float glassA = (base + rim * f) * opacity * (1.0 - 0.4 * vS);
    vec3 albedo = mix(vec3(0.86, 0.84, 0.82), vec3(0.42, 0.40, 0.40), vS);
    vec3 solidC = albedo * (0.28 + 0.72 * facing) + 0.08 * pow(facing, 24.0);
    vec4 c = vec4(mix(glassC, solidC, solid), mix(glassA, 1.0, solid));
    float spot = entrySpot();
    vec4 red = vec4(entryColor * mix(1.0, 0.45 + 0.55 * facing, solid), mix(0.9, 1.0, solid));
    gl_FragColor = mix(c, red, spot);
  }`;
const cortexMaterial = (color) =>
  new THREE.ShaderMaterial({
    vertexShader: cortexVert,
    fragmentShader: cortexFrag,
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    uniforms: {
      color: { value: new THREE.Color(color) },
      base: { value: 0.015 },
      rim: { value: 0.33 },
      opacity: { value: 1 },
      solid: { value: 0 },
      entryAt: {
        value: Array.from({ length: MAX_ENTRIES }, () => new THREE.Vector3()),
      },
      entryDir: {
        value: Array.from(
          { length: MAX_ENTRIES },
          () => new THREE.Vector3(1, 0, 0),
        ),
      },
      nEntries: { value: 0 },
      entryR: { value: 2.2 },
      entryColor: { value: new THREE.Color(0xe5383b) },
    },
  });

export const TARGET = new THREE.Vector3(0, -18, 8);
const VIEWS = {
  L: [-1, 0, 0],
  R: [1, 0, 0],
  A: [0, 1, 0],
  P: [0, -1, 0],
  S: [0, 0, 1],
  I: [0, 0, -1],
};

export function initScene(stage, anatomy) {
  const renderer = new THREE.WebGLRenderer({
    antialias: true,
    preserveDrawingBuffer: true,
  });
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setClearColor(0x0b0e14);
  stage.prepend(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32, 1, 1, 3000);
  camera.up.set(0, 0, 1);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.copy(TARGET);
  controls.enableDamping = true;
  scene.add(new THREE.AmbientLight(0xffffff, 0.7));
  const headLight = new THREE.DirectionalLight(0xffffff, 1.5);
  camera.add(headLight);
  scene.add(camera);

  function setView(k) {
    const d =
      k === "oblique"
        ? new THREE.Vector3(0.75, 0.45, 0.5).normalize()
        : new THREE.Vector3(...VIEWS[k]);
    camera.up.set(0, 0, 1);
    if (k === "S" || k === "I") camera.up.set(0, 1, 0);
    camera.position.copy(TARGET).addScaledVector(d, 430);
    controls.target.copy(TARGET);
    controls.update();
  }
  setView("oblique");

  const hull = [],
    deep = [];
  for (const m of anatomy.meshes) {
    const g = new THREE.BufferGeometry();
    g.setAttribute(
      "position",
      new THREE.BufferAttribute(typed(m.v, Float32Array), 3),
    );
    g.setIndex(
      new THREE.BufferAttribute(
        m.f32 ? typed(m.f, Uint32Array) : typed(m.f, Uint16Array),
        1,
      ),
    );
    g.computeVertexNormals();
    const isHull = m.kind === "hull";
    if (isHull)
      g.setAttribute(
        "sulc",
        new THREE.BufferAttribute(
          m.s
            ? Float32Array.from(bytes(m.s))
            : new Float32Array(g.attributes.position.count),
          1,
        ),
      );
    const mesh = new THREE.Mesh(
      g,
      isHull ? cortexMaterial(m.color) : glass(m.color, 0.1, 0.55),
    );
    mesh.renderOrder = isHull ? 3 : 2;
    mesh.userData = { name: m.name };
    (isHull ? hull : deep).push(mesh);
    scene.add(mesh);
  }
  const setGlass = (meshes, v, { cull }) =>
    meshes.forEach((m) => {
      const u = m.material.uniforms,
        solid = Math.max(0, Math.min(1, (v - 0.5) / 0.5));
      u.opacity.value = Math.min(v, 0.5) * 2;
      u.solid.value = solid;
      m.material.depthWrite = solid > 0;
      if (cull)
        m.material.side = solid > 0 ? THREE.FrontSide : THREE.DoubleSide;
      m.visible = v > 0;
    });
  const setHull = (v) => setGlass(hull, v, { cull: true });
  const setDeep = (v) => setGlass(deep, v, { cull: false });
  // spots: [{ at: [x, y, z], dir: unit vector along the track }], painted by cortexFrag
  const setEntries = (spots) =>
    hull.forEach((m) => {
      const u = m.material.uniforms,
        n = Math.min(spots.length, MAX_ENTRIES);
      for (let i = 0; i < n; i++) {
        u.entryAt.value[i].set(...spots[i].at);
        u.entryDir.value[i].set(...spots[i].dir);
      }
      u.nEntries.value = n;
    });
  setHull(0.5);

  const views = stage.querySelector("#views");
  for (const k of Object.keys(VIEWS)) {
    const b = document.createElement("button");
    b.textContent = k;
    b.dataset.view = k;
    b.title = {
      L: "left",
      R: "right",
      A: "front",
      P: "back",
      S: "top",
      I: "bottom",
    }[k];
    b.addEventListener("click", () => setView(k));
    views.append(b);
  }

  const hullIn = stage.querySelector("#hull"),
    deepIn = stage.querySelector("#deep");
  hullIn.addEventListener("input", () => setHull(+hullIn.value));
  deepIn.addEventListener("input", () => setDeep(+deepIn.value));

  // Orientation letters at the view's edges (as on scans)
  const LETTER = [
    ["R", "L"],
    ["A", "P"],
    ["S", "I"],
  ];
  const edges = Object.fromEntries(
    ["left", "right", "top", "bottom"].map((k) => {
      const el = document.createElement("div");
      el.className = `orient ${k}`;
      stage.append(el);
      return [k, el];
    }),
  );
  const letter = (v) => {
    // the letter of the axis v mostly points along
    const a = [0, 1, 2].reduce(
      (m, i) =>
        Math.abs(v.getComponent(i)) > Math.abs(v.getComponent(m)) ? i : m,
      0,
    );
    return LETTER[a][v.getComponent(a) > 0 ? 0 : 1];
  };
  function orient() {
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
    const up = new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion);
    const set = (k, v) => {
      const t = letter(v);
      if (edges[k].textContent !== t) edges[k].textContent = t;
    };
    set("right", right);
    set("left", right.clone().negate());
    set("top", up);
    set("bottom", up.clone().negate());
  }

  const callbacks = [orient];
  function resize() {
    const w = stage.clientWidth,
      h = stage.clientHeight;
    renderer.setSize(w, h);
    camera.aspect = w / Math.max(1, h);
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(stage);
  resize();
  function frame() {
    controls.update();
    for (const cb of callbacks) cb();
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  // world point -> CSS pixels in the stage; null when behind the camera
  const toScreen = (p) => {
    const v = new THREE.Vector3(...p).project(camera);
    if (v.z > 1) return null;
    return [
      ((v.x + 1) / 2) * stage.clientWidth,
      ((1 - v.y) / 2) * stage.clientHeight,
    ];
  };
  const ray = new THREE.Raycaster(),
    ndc = new THREE.Vector2();
  ray.params.Line.threshold = 1.5; // mm, so the thin track lines can be picked
  const rayAt = (ev) => {
    const r = renderer.domElement.getBoundingClientRect();
    ndc.set(
      ((ev.clientX - r.left) / r.width) * 2 - 1,
      (-(ev.clientY - r.top) / r.height) * 2 + 1,
    );
    ray.setFromCamera(ndc, camera);
    return ray;
  };
  return {
    THREE,
    scene,
    camera,
    renderer,
    controls,
    hull,
    setView,
    setHull,
    setDeep,
    setEntries,
    onFrame: (cb) => callbacks.push(cb),
    toScreen,
    rayAt,
  };
}
