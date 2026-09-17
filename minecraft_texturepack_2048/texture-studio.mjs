import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { resourceId, properties, selectedModels, faceData, animationFrame, animationGrid } from "./minecraft-model.mjs";
import { patchLabPBR } from "./labpbr-viewer-shaders.mjs";

const $ = id => document.getElementById(id);
let catalog, block, textureId, blockEpoch = 0, meshEpoch = 0, busy = false, meshReady = false;
let state = {}, alternative = 0;
const selections = new Map(), textureCache = new Map();
const name = id => id.replace(/^minecraft:(block\/)?/, "").replaceAll("_", " ");
function message(text, kind = "") { $("message").textContent = text; $("message").className = kind; }
function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  if (className) result.className = className;
  return result;
}
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-Studio-Token": catalog.token },
    body: JSON.stringify(data),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}
function safe(action) {
  return (...args) => Promise.resolve().then(() => action(...args)).catch(error => message(error.message, "error"));
}
const candidateFor = id => {
  const texture = catalog.textures[id];
  return texture?.candidates.find(c => c.id === selections.get(id))
    || texture?.candidates.find(c => c.id === texture.keeper)
    || texture?.candidates.at(-1);
};

function renderCoverage() {
  const s = catalog.summary;
  $("version").textContent = `Minecraft ${catalog.version} · Local candidate library`;
  $("version").title = catalog.source;
  const percent = s.trackable ? s.complete / s.trackable * 100 : 0;
  $("coverageText").textContent = `${percent.toFixed(2)}% complete · ${s.complete} / ${s.trackable} model-backed blocks`;
  $("coverageBar").max = s.trackable || 1;
  $("coverageBar").value = s.complete;
  $("coverageDetail").textContent = `${s.partial} partial · ${s.missing} missing · ${s.excluded} excluded (special renderers, no faces or unresolved assets). Albedo coverage only.`;
}
function renderList() {
  const search = $("search").value.trim().toLowerCase().replaceAll(" ", "_"), filter = $("statusFilter").value;
  const visible = catalog.blocks.filter(item => item.id.toLowerCase().includes(search) && (!filter || item.status === filter));
  $("listCount").textContent = `${visible.length} of ${catalog.blocks.length} blocks`;
  const fragment = document.createDocumentFragment();
  for (const item of visible) {
    const button = node("button", undefined, "block-row");
    button.type = "button";
    button.setAttribute("aria-current", String(item.id === block?.id));
    button.append(node("span", name(item.id)), node("span", item.status, `status ${item.status}`));
    button.addEventListener("click", safe(() => selectBlock(item.id)));
    fragment.append(button);
  }
  if (!visible.length) fragment.append(node("p", "No blocks match this filter.", "filters muted"));
  $("blockList").replaceChildren(fragment);
}
function renderTextures() {
  const item = catalog.blocks.find(item => item.id === block.id);
  const keepers = block.textures.filter(id => catalog.textures[id]?.status === "complete").length;
  $("blockCoverage").textContent = `${item.status} · ${keepers} / ${block.textures.length} textures have keepers`;
  const list = document.createDocumentFragment();
  for (const id of block.textures) {
    const texture = catalog.textures[id];
    const row = node("div", undefined, `texture-row${textureId === id ? " selected" : ""}`);
    const button = node("button", name(id), "texture-title");
    button.type = "button"; button.addEventListener("click", () => selectTexture(id));
    const faces = new Set();
    for (const model of Object.values(block.models)) {
      for (const element of model.elements || []) {
        for (const [direction, face] of Object.entries(element.faces || {})) {
          if (face.texture === id) faces.add(direction);
        }
      }
    }
    row.append(button, node("small", `${texture?.status || "missing"} · ${texture?.candidates.length || 0} candidates`),
      node("small", `Faces: ${[...faces].join(", ") || "unresolved"}`));
    list.append(row);
  }
  $("textureList").replaceChildren(list);
}
function selectTexture(id) {
  const changed = textureId !== id;
  textureId = id;
  const texture = catalog.textures[id];
  $("textureEditor").hidden = !texture;
  renderTextures();
  if (!texture) return;
  if (changed) {
    const faces = Object.values(block.models).flatMap(model => (model.elements || []).flatMap(element => Object.values(element.faces || {})))
      .filter(face => face.texture === id);
    $("grayscale").checked = faces.length > 0 && faces.every(face => face.tintindex >= 0);
    $("prompt").value = "";
  }
  $("textureId").textContent = id;
  $("sourceImage").src = texture.sourceUrl;
  const candidate = candidateFor(id);
  $("candidateSelect").replaceChildren();
  if (!texture.candidates.length) $("candidateSelect").add(new Option("No candidates yet", ""));
  for (const entry of texture.candidates) {
    $("candidateSelect").add(new Option(`${entry.id === texture.keeper ? "Keeper · " : ""}${entry.name}`, entry.id));
  }
  if (candidate) $("candidateSelect").value = candidate.id;
  $("candidateImage").hidden = !candidate;
  $("noCandidate").hidden = !!candidate;
  if (candidate) $("candidateImage").src = candidate.url;
  else $("candidateImage").removeAttribute("src");
  $("candidateCaption").textContent = candidate?.id === texture.keeper ? "Your keeper" : "Unconfirmed candidate";
  $("candidateInfo").textContent = candidate
    ? (candidate.eligible ? "Ready for your review. Choosing a keeper updates all blocks that share this texture."
      : candidate.reason || "Preview only; incomplete animation cannot count as a keeper.")
    : "Generate or import a PNG for this Minecraft texture ID.";
  $("markKeeper").disabled = busy || !candidate?.eligible || candidate.id === texture.keeper;
  $("clearKeeper").disabled = busy || !texture.keeper;
  $("animationField").hidden = !texture.animated;
}
function renderStateControls() {
  const controls = document.createDocumentFragment();
  for (const [key, values] of Object.entries(properties(block.blockstate))) {
    const label = node("label", key), select = document.createElement("select");
    values.forEach(value => select.add(new Option(value, value)));
    if (!values.includes(state[key])) {
      state[key] = key === "axis" && values.includes("y") ? "y" : values.includes("false") ? "false" : values[0];
    }
    select.value = state[key];
    select.addEventListener("change", safe(() => { state[key] = select.value; return buildModel(); }));
    label.append(select); controls.append(label);
  }
  const choices = [
    ...Object.values(block.blockstate.variants || {}),
    ...(block.blockstate.multipart || []).map(part => part.apply),
  ];
  const maxAlternatives = Math.max(1, ...choices.map(choice => Array.isArray(choice) ? choice.length : 1));
  if (maxAlternatives > 1) {
    const label = node("label", "Model alternative"), select = document.createElement("select");
    for (let i = 0; i < maxAlternatives; i++) select.add(new Option(String(i + 1), String(i)));
    select.addEventListener("change", safe(() => { alternative = Number(select.value); return buildModel(); }));
    label.append(select); controls.append(label);
  }
  $("stateControls").replaceChildren(controls);
}
async function selectBlock(id) {
  const epoch = ++blockEpoch;
  ++meshEpoch; meshReady = false; modelGroup.visible = false;
  const detail = await api(`/api/block?id=${encodeURIComponent(id)}`);
  if (epoch !== blockEpoch) return;
  block = detail; state = {}; alternative = 0;
  $("blockTitle").textContent = name(id);
  $("blockTitle").title = id;
  renderList(); renderStateControls();
  selectTexture(block.textures.includes(textureId) ? textureId : block.textures[0]);
  const url = new URL(location.href); url.searchParams.set("block", id); history.replaceState(null, "", url);
  resetCamera();
  await buildModel();
}
async function refresh() {
  catalog = await api("/api/catalog");
  renderCoverage(); renderList();
  if (block) { selectTexture(textureId); await buildModel(); }
}

const renderer = new THREE.WebGLRenderer({ canvas: $("canvas"), antialias: true, alpha: false });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
const scene = new THREE.Scene(); scene.background = new THREE.Color("#171c23");
scene.add(new THREE.HemisphereLight(0xffffff, 0x687689, 2));
const sun = new THREE.DirectionalLight(0xffffff, 2.5); sun.position.set(3, 5, 4); scene.add(sun);
const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 100);
const orbit = new OrbitControls(camera, $("canvas")); orbit.enableDamping = true;
let modelGroup = new THREE.Group(); scene.add(modelGroup);
let animatedMaps = [], clickStart;
function resetCamera() { camera.position.set(2.4, 1.8, 2.7); orbit.target.set(0, 0, 0); orbit.update(); }
resetCamera();
const observer = new ResizeObserver(() => {
  const { width, height } = $("stage").getBoundingClientRect();
  renderer.setSize(width, height, false); camera.aspect = width / height; camera.updateProjectionMatrix();
});
observer.observe($("stage"));
function loadMap(descriptor) {
  const url = descriptor.url || descriptor.sourceUrl;
  if (!textureCache.has(url)) {
    textureCache.set(url, new THREE.TextureLoader().loadAsync(url).then(map => {
      map.colorSpace = THREE.SRGBColorSpace;
      const isPixelArt = !descriptor.url || (map.image && Math.min(map.image.width, map.image.height) <= 64);
      if (isPixelArt) {
        map.generateMipmaps = false;
        map.magFilter = THREE.NearestFilter;
        map.minFilter = THREE.NearestFilter;
      } else {
        map.generateMipmaps = true;
        map.magFilter = THREE.LinearFilter;
        map.minFilter = THREE.LinearMipmapLinearFilter;
        map.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy?.() || 1);
      }
      map.needsUpdate = true;
      return map;
    }).catch(error => { textureCache.delete(url); throw error; }));
  }
  return textureCache.get(url);
}
function loadDataMap(url) {
  if (!textureCache.has(url)) {
    textureCache.set(url, new THREE.TextureLoader().loadAsync(url).then(map => {
      map.colorSpace = THREE.NoColorSpace;
      map.channel = 0;
      map.magFilter = THREE.NearestFilter;
      map.minFilter = THREE.LinearMipmapLinearFilter;
      return map;
    }).catch(error => { textureCache.delete(url); throw error; }));
  }
  return textureCache.get(url);
}
function disposeGroup(group) {
  group.traverse(child => { child.geometry?.dispose(); child.material?.dispose(); });
}
async function buildModel() {
  if (!block) return;
  meshReady = false; modelGroup.visible = false;
  const epoch = ++meshEpoch, definitions = selectedModels(block.blockstate, state, alternative);
  const maps = new Map(), animation = [];
  let candidateFaces = 0, vanillaFaces = 0;
  await Promise.all(block.textures.map(async id => {
    const source = catalog.textures[id];
    if (!source) return;
    const mode = $("previewMode").value;
    const candidate = mode === "vanilla" ? null : mode === "keepers"
      ? source.candidates.find(c => c.id === source.keeper) : candidateFor(id);
    const descriptor = candidate || source, map = await loadMap(descriptor);
    const { count, columns, rows } = animationGrid(map.image.width, map.image.height, descriptor.animation);
    if (count > 1) {
      map.repeat.set(1 / columns, 1 / rows);
      animation.push({ map, count, columns, rows, metadata: descriptor.animation });
    }
    // Baked LabPBR companions ride the albedo UVs; animated frames stay albedo-only.
    const entry = { map, candidate: !!candidate, count };
    if (count === 1 && candidate) {
      const [normal, specular] = await Promise.all([
        descriptor.nUrl ? loadDataMap(descriptor.nUrl).catch(() => null) : null,
        descriptor.sUrl ? loadDataMap(descriptor.sUrl).catch(() => null) : null,
      ]);
      entry.normal = normal;
      entry.specular = specular;
    }
    maps.set(id, entry);
  }));
  if (epoch !== meshEpoch) return;
  const group = new THREE.Group(), issues = [...(block.issues || [])];
  for (const definition of definitions) {
    const model = block.models[resourceId(definition.model)];
    if (!model) { issues.push(`Unresolved model: ${definition.model}`); continue; }
    issues.push(...(model.issues || []));
    for (const element of model.elements || []) {
      for (const [direction, face] of Object.entries(element.faces || {})) {
        const binding = maps.get(face.texture);
        const data = faceData(element, direction, face, definition);
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", new THREE.Float32BufferAttribute(data.positions, 3));
        geometry.setAttribute("uv", new THREE.Float32BufferAttribute(data.uvs, 2));
        geometry.setIndex([0, 1, 2, 0, 2, 3]); geometry.computeVertexNormals();
        const Material = $("unlit").checked ? THREE.MeshBasicMaterial : THREE.MeshStandardMaterial;
        const material = new Material({
          map: binding?.map || null, color: !binding ? 0xff00ff : face.tintindex >= 0 ? $("tint").value : 0xffffff,
          alphaTest: 0.1, polygonOffset: true, polygonOffsetFactor: -0.1, polygonOffsetUnits: -0.1,
          transparent: face.translucent === true, depthWrite: face.translucent !== true,
        });
        if (Material === THREE.MeshStandardMaterial && (binding?.normal || binding?.specular)) {
          material.roughness = 1.0;
          material.metalness = 0.0;
          if (binding.normal) {
            material.normalMap = binding.normal;
            material.aoMap = binding.normal;
          }
          if (binding.specular) material.roughnessMap = binding.specular;
          material.onBeforeCompile = shader => patchLabPBR(shader, THREE.ShaderChunk,
            { labDepth: { value: 0.06 }, labInspect: { value: 0 } });
          material.customProgramCacheKey = () => "labpbr-studio-v1";
        }
        const mesh = new THREE.Mesh(geometry, material);
        mesh.userData.textureId = face.texture;
        mesh.renderOrder = face.translucent ? 1 : 0;
        group.add(mesh);
        if (binding?.candidate) candidateFaces++; else vanillaFaces++;
      }
    }
  }
  scene.remove(modelGroup); disposeGroup(modelGroup);
  modelGroup = group; scene.add(group); animatedMaps = animation; meshReady = true;
  const limitations = "PBR preview on baked candidates (normals, roughness, metalness; no displacement), albedo only elsewhere. Tint is approximate. Special renderers and fluid geometry are not emulated.";
  const extra = animation.some(entry => entry.metadata?.interpolate) ? " Animation interpolation is not emulated." : "";
  $("modelNotes").textContent = `${candidateFaces} generated faces · ${vanillaFaces} vanilla faces in this state. ${limitations}${extra} ${[...new Set(issues)].join("; ")}`;
  if (!group.children.length) $("modelNotes").textContent = `No standard model geometry for this state. ${[...new Set(issues)].join("; ")} Coverage stays excluded where assets cannot resolve.`;
}
const raycaster = new THREE.Raycaster();
$("canvas").addEventListener("pointerdown", event => { clickStart = [event.clientX, event.clientY]; });
$("canvas").addEventListener("pointerup", event => {
  if (!meshReady || !clickStart || Math.hypot(event.clientX - clickStart[0], event.clientY - clickStart[1]) > 4) return;
  const rect = $("canvas").getBoundingClientRect();
  raycaster.setFromCamera(new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1), camera);
  const hit = raycaster.intersectObjects(modelGroup.children)[0];
  if (hit && block.textures.includes(hit.object.userData.textureId)) selectTexture(hit.object.userData.textureId);
});
renderer.setAnimationLoop(milliseconds => {
  for (const { map, count, columns, rows, metadata } of animatedMaps) {
    const frame = animationFrame(metadata, count, milliseconds / 1000);
    map.offset.set(frame % columns / columns, 1 - (Math.floor(frame / columns) + 1) / rows);
  }
  orbit.update(); renderer.render(scene, camera);
});

async function mutate(action, pending, done) {
  if (busy) return;
  busy = true; message(pending, "busy");
  for (const id of ["generateButton", "importButton", "markKeeper", "clearKeeper"]) $(id).disabled = true;
  try { await action(); await refresh(); message(done); }
  finally {
    busy = false; $("generateButton").disabled = false; $("importButton").disabled = false;
    if (textureId) selectTexture(textureId);
  }
}
$("search").addEventListener("input", renderList);
$("statusFilter").addEventListener("change", renderList);
for (const id of ["previewMode", "unlit", "tint"]) $(id).addEventListener("change", safe(buildModel));
$("resetCamera").addEventListener("click", resetCamera);
$("candidateSelect").addEventListener("change", safe(async () => {
  selections.set(textureId, $("candidateSelect").value); selectTexture(textureId); await buildModel();
}));
$("markKeeper").addEventListener("click", safe(() => {
  const id = textureId, candidate = candidateFor(id);
  return mutate(() => api("/api/keeper", { texture: id, candidate: candidate.id }), "Saving your keeper…", "Keeper saved. Shared-block coverage updated.");
}));
$("clearKeeper").addEventListener("click", safe(() => {
  const id = textureId;
  return mutate(() => api("/api/keeper", { texture: id, candidate: null }), "Clearing keeper…", "Keeper cleared. Candidate files remain.");
}));
$("generateButton").addEventListener("click", safe(() => {
  const id = textureId;
  const options = { texture: id, material: $("material").value, seed: Number($("seed").value), grayscale: $("grayscale").checked };
  if ($("prompt").value.trim()) options.prompt = $("prompt").value.trim();
  if (catalog.textures[id].animated && $("generationFrames").value === "preview") options.frame = 0;
  return mutate(async () => {
    const candidate = await api("/api/generate", options);
    selections.set(id, candidate.id);
  }, `Generating ${name(id)}. ${options.frame === 0 ? "First frame only." : "Animated sources can take several minutes."}`,
  "Candidate generated and associated. Review it before marking a keeper.");
}));
$("importButton").addEventListener("click", safe(async () => {
  const id = textureId, file = $("importPng").files[0], metadata = $("importMeta").files[0];
  if (!file) throw new Error("Choose a PNG to import.");
  if (file.size > 45 * 1024 * 1024) throw new Error("PNG is too large for browser import; use the CLI --import option.");
  const png = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = () => reject(new Error("Cannot read that PNG."));
    reader.readAsDataURL(file);
  });
  const mcmeta = metadata ? JSON.parse(await metadata.text()) : null;
  return mutate(async () => {
    const candidate = await api("/api/import", { texture: id, name: file.name, png, mcmeta });
    selections.set(id, candidate.id);
  }, "Importing candidate…", "Candidate imported. It is not a keeper until you approve it.");
}));

safe(async () => {
  await refresh();
  const requested = new URLSearchParams(location.search).get("block");
  const first = catalog.blocks.find(item => item.id === requested)
    || catalog.blocks.find(item => item.id === "minecraft:oak_log") || catalog.blocks[0];
  if (first) await selectBlock(first.id);
  message("Choose a block, inspect its face textures, then mark your keepers. No automatic approvals.");
})();
