// Static block browser for the hosted Minecraft Texture Viewer.
//
// Reads a studio-catalog/ bundle written by export_studio_catalog.py — no
// backend. Builds model-accurate block meshes from the exported blockstate +
// resolved models, using minecraft-model.mjs math and the shared LabPBR
// shader patch. Faces whose textures have published Metallum art render with
// it (plus baked _n/_s companions when PBR shading is on); every other face
// gets a placeholder so missing coverage is visible, not silently vanilla.
import { resourceId, selectedModels, faceData, animationGrid } from "./minecraft-model.mjs";

export const displayName = id =>
  id.replace(/^minecraft:(block\/)?/, "").replaceAll("_", " ");

export async function loadCatalog(base) {
  const response = await fetch(`${base}/catalog.json`);
  if (!response.ok) throw new Error(`catalog.json unavailable (HTTP ${response.status})`);
  return response.json();
}

export async function loadBlockDetail(base, id) {
  const slug = id.replace(/[:/]/g, "_");
  const response = await fetch(`${base}/blocks/${slug}.json`);
  if (!response.ok) throw new Error(`block detail unavailable (HTTP ${response.status})`);
  return response.json();
}

let cachedPlaceholderURL = null;
export function placeholderURL() {
  if (cachedPlaceholderURL) return cachedPlaceholderURL;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 32;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#232a33"; ctx.fillRect(0, 0, 32, 32);
  ctx.fillStyle = "#2c3540";
  ctx.fillRect(0, 0, 16, 16); ctx.fillRect(16, 16, 16, 16);
  ctx.fillStyle = "#8a94a0"; ctx.font = "16px sans-serif";
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText("?", 16, 17);
  cachedPlaceholderURL = canvas.toDataURL("image/png");
  return cachedPlaceholderURL;
}

function loadAlbedo(env, url) {
  const { THREE, textureCache, maxAnisotropy } = env;
  if (!textureCache.has(url)) {
    textureCache.set(url, new THREE.TextureLoader().loadAsync(url).then(map => {
      map.colorSpace = THREE.SRGBColorSpace;
      // Published art is shown the way it looks in game: nearest, no mipmaps.
      map.generateMipmaps = false;
      map.magFilter = THREE.NearestFilter;
      map.minFilter = THREE.NearestFilter;
      map.anisotropy = Math.min(8, maxAnisotropy);
      map.needsUpdate = true;
      return map;
    }).catch(error => { textureCache.delete(url); throw error; }));
  }
  return textureCache.get(url);
}

function loadDataMap(env, url) {
  const { THREE, textureCache } = env;
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

// env: { THREE, patchLabPBR, textureCache: Map, maxAnisotropy, catalogBase }
// artMap: { textureId: { albedo, n, s } } — relative URLs against catalogBase
export async function buildBlockGroup(env, detail, artMap, { state = {}, alternative = 0, usePBR = true } = {}) {
  const { THREE, patchLabPBR, catalogBase } = env;
  const definitions = selectedModels(detail.blockstate, state, alternative);
  const maps = new Map(), animations = [];
  let metallumFaces = 0, placeholderFaces = 0;

  await Promise.all(detail.textures.map(async id => {
    const art = artMap[id];
    const entry = { art: !!art, count: 1 };
    try {
      const map = art?.albedo
        ? await loadAlbedo(env, catalogBase + "/" + art.albedo)
        : await loadAlbedo(env, placeholderURL());
      const { count, columns, rows } = animationGrid(map.image.width, map.image.height, null);
      if (count > 1) {
        map.repeat.set(1 / columns, 1 / rows);
        animations.push({ map, count, columns, rows });
      }
      entry.map = map; entry.count = count;
      if (count === 1 && usePBR && art) {
        const [normal, specular] = await Promise.all([
          art.n ? loadDataMap(env, catalogBase + "/" + art.n).catch(() => null) : null,
          art.s ? loadDataMap(env, catalogBase + "/" + art.s).catch(() => null) : null,
        ]);
        entry.normal = normal; entry.specular = specular;
      }
    } catch {
      entry.failed = true;
    }
    maps.set(id, entry);
  }));

  const group = new THREE.Group(), issues = [...(detail.issues || [])];
  for (const definition of definitions) {
    const model = detail.models[resourceId(definition.model)];
    if (!model) { issues.push(`Unresolved model: ${definition.model}`); continue; }
    issues.push(...(model.issues || []));
    for (const element of model.elements || []) {
      for (const [direction, face] of Object.entries(element.faces || {})) {
        const binding = maps.get(face.texture);
        if (!binding || binding.failed) continue;
        const data = faceData(element, direction, face, definition);
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", new THREE.Float32BufferAttribute(data.positions, 3));
        geometry.setAttribute("uv", new THREE.Float32BufferAttribute(data.uvs, 2));
        geometry.setIndex([0, 1, 2, 0, 2, 3]);
        geometry.computeVertexNormals();
        const material = new THREE.MeshStandardMaterial({
          map: binding.map || null,
          alphaTest: 0.1, polygonOffset: true, polygonOffsetFactor: -0.1, polygonOffsetUnits: -0.1,
          transparent: face.translucent === true, depthWrite: face.translucent !== true,
        });
        if (usePBR && (binding.normal || binding.specular)) {
          material.roughness = 1.0;
          material.metalness = 0.0;
          if (binding.normal) { material.normalMap = binding.normal; material.aoMap = binding.normal; }
          if (binding.specular) material.roughnessMap = binding.specular;
          material.onBeforeCompile = shader => patchLabPBR(shader, THREE.ShaderChunk,
            { labDepth: { value: 0.06 }, labInspect: { value: 0 } });
          material.customProgramCacheKey = () => "labpbr-blockbrowser-v1";
        }
        const mesh = new THREE.Mesh(geometry, material);
        mesh.userData.textureId = face.texture;
        mesh.renderOrder = face.translucent ? 1 : 0;
        group.add(mesh);
        if (binding.art) metallumFaces++; else placeholderFaces++;
      }
    }
  }

  const uniqueIssues = [...new Set(issues)];
  let notes;
  if (!group.children.length) {
    notes = `No standard model geometry for this state. ${uniqueIssues.join("; ")}`.trim();
  } else {
    notes = `${metallumFaces} Metallum faces · ${placeholderFaces} placeholder faces in this state. ` +
      "PBR preview on published art (normals, roughness, metalness; no displacement). " +
      "Tint is approximate; special renderers and fluid geometry are not emulated. " +
      uniqueIssues.join("; ");
  }
  return { group, animations, notes: notes.trim(), metallumFaces, placeholderFaces };
}
