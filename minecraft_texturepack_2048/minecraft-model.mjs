// Minecraft model math; no renderer dependency. Coordinates and UVs use 0..16.
export const resourceId = id => id.includes(":") ? id : `minecraft:${id}`;

export function matches(when, state) {
  if (!when) return true;
  return Object.entries(when).every(([key, value]) => {
    if (key === "OR") return value.some(part => matches(part, state));
    if (key === "AND") return value.every(part => matches(part, state));
    return String(value).split("|").includes(String(state[key]));
  });
}

export function properties(blockstate) {
  const values = {};
  function add(key, value) {
    values[key] ??= new Set();
    String(value).split("|").forEach(v => values[key].add(v));
  }
  function visit(when) {
    for (const [key, value] of Object.entries(when || {})) {
      if (key === "OR" || key === "AND") value.forEach(visit);
      else add(key, value);
    }
  }
  for (const variant of Object.keys(blockstate.variants || {})) {
    for (const pair of variant.split(",").filter(Boolean)) {
      const [key, value] = pair.split("=");
      add(key, value);
    }
  }
  (blockstate.multipart || []).forEach(part => visit(part.when));
  // Boolean multipart conditions often only mention true, but false is a valid preview choice.
  for (const choices of Object.values(values)) {
    if ([...choices].every(v => v === "true" || v === "false")) {
      choices.add("false"); choices.add("true");
    }
  }
  return Object.fromEntries(Object.entries(values).map(([key, choices]) => [key, [...choices].sort()]));
}

export function selectedModels(blockstate, state, alternative = 0) {
  const result = [];
  const add = definition => {
    const list = Array.isArray(definition) ? definition : [definition];
    if (list.length) result.push(list[alternative % list.length]);
  };
  for (const [variant, definition] of Object.entries(blockstate.variants || {})) {
    const when = Object.fromEntries(variant.split(",").filter(Boolean).map(v => v.split("=")));
    if (matches(when, state)) add(definition);
  }
  for (const part of blockstate.multipart || []) {
    if (matches(part.when, state)) add(part.apply);
  }
  return result;
}

function rotate(vector, axis, degrees) {
  const result = [...vector], angle = degrees * Math.PI / 180;
  const [a, b] = { x: [1, 2], y: [2, 0], z: [0, 1] }[axis];
  result[a] = vector[a] * Math.cos(angle) - vector[b] * Math.sin(angle);
  result[b] = vector[a] * Math.sin(angle) + vector[b] * Math.cos(angle);
  return result;
}
const stateRotate = (v, state) => rotate(rotate(v, "x", -(state.x || 0)), "y", -(state.y || 0));
const dot = (a, b) => a.reduce((sum, v, i) => sum + v * b[i], 0);
const subtract = (a, b) => a.map((v, i) => v - b[i]);
const axes = {
  north: [[0, 0, -1], [-1, 0, 0], [0, -1, 0]],
  south: [[0, 0, 1], [1, 0, 0], [0, -1, 0]],
  west: [[-1, 0, 0], [0, 0, 1], [0, -1, 0]],
  east: [[1, 0, 0], [0, 0, -1], [0, -1, 0]],
  up: [[0, 1, 0], [1, 0, 0], [0, 0, 1]],
  down: [[0, -1, 0], [1, 0, 0], [0, 0, -1]],
};

export function faceData(element, direction, face, state = {}) {
  const [x0, y0, z0] = element.from, [x1, y1, z1] = element.to;
  const vertices = {
    north: [[x1, y1, z0], [x1, y0, z0], [x0, y0, z0], [x0, y1, z0]],
    south: [[x0, y1, z1], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1]],
    west: [[x0, y1, z0], [x0, y0, z0], [x0, y0, z1], [x0, y1, z1]],
    east: [[x1, y1, z1], [x1, y0, z1], [x1, y0, z0], [x1, y1, z0]],
    up: [[x0, y1, z0], [x0, y1, z1], [x1, y1, z1], [x1, y1, z0]],
    down: [[x0, y0, z1], [x0, y0, z0], [x1, y0, z0], [x1, y0, z1]],
  }[direction];
  const bounds = face.uv || {
    down: [x0, 16 - z1, x1, 16 - z0], up: [x0, z0, x1, z1],
    north: [16 - x1, 16 - y1, 16 - x0, 16 - y0],
    south: [x0, 16 - y1, x1, 16 - y0],
    west: [z0, 16 - y1, z1, 16 - y0],
    east: [16 - z1, 16 - y1, 16 - z0, 16 - y0],
  }[direction];
  const [u0, v0, u1, v1] = bounds;
  const uvCorners = [[u0, v0], [u0, v1], [u1, v1], [u1, v0]];
  const uvs = vertices.map((_, index) => {
    let [u, v] = uvCorners[(index + (face.rotation || 0) / 90) % 4];
    if (state.uvlock) {
      const [normal, uAxis, vAxis] = axes[direction].map(axis => stateRotate(axis, state));
      const target = Object.values(axes).find(([n]) => dot(n, normal) > 0.99);
      if (target) {
        [u, v] = [
          8 + (u - 8) * dot(uAxis, target[1]) + (v - 8) * dot(vAxis, target[1]),
          8 + (u - 8) * dot(uAxis, target[2]) + (v - 8) * dot(vAxis, target[2]),
        ];
      }
    }
    return [u / 16, 1 - v / 16];
  });
  const positions = vertices.map(vertex => {
    let point = vertex;
    if (element.rotation) {
      const { origin, axis, angle, rescale } = element.rotation;
      point = subtract(point, origin);
      if (rescale) {
        const scale = 1 / Math.cos(angle * Math.PI / 180);
        point = point.map((v, i) => "xyz"[i] === axis ? v : v * scale);
      }
      point = rotate(point, axis, angle).map((v, i) => v + origin[i]);
    }
    return stateRotate(subtract(point, [8, 8, 8]), state).map(v => v / 16);
  });
  return { positions: positions.flat(), uvs: uvs.flat() };
}

export function animationFrame(animation, count, seconds) {
  if (count <= 1) return 0;
  const entries = (animation?.frames?.length ? animation.frames : Array.from({ length: count }, (_, i) => i))
    .map(entry => typeof entry === "number"
      ? { index: entry, time: animation?.frametime || 1 }
      : { index: entry.index, time: entry.time || animation?.frametime || 1 });
  const total = entries.reduce((sum, frame) => sum + frame.time, 0);
  let tick = Math.floor(seconds * 20) % total;
  for (const frame of entries) {
    if (tick < frame.time) return Math.min(count - 1, Math.max(0, frame.index));
    tick -= frame.time;
  }
  return 0;
}

export function animationGrid(width, height, animation) {
  if (!animation) return { columns: 1, rows: 1, count: 1 };
  const fw = animation.width ?? ("height" in animation ? width : Math.min(width, height));
  const fh = animation.height ?? ("width" in animation ? height : Math.min(width, height));
  const columns = width / fw, rows = height / fh;
  if (!Number.isInteger(columns) || !Number.isInteger(rows) || columns < 1 || rows < 1) {
    return { columns: 1, rows: 1, count: 1 };
  }
  return { columns, rows, count: columns * rows };
}
