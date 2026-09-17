import assert from "node:assert/strict";
import { matches, properties, selectedModels, faceData, animationFrame, animationGrid } from "./minecraft-model.mjs";

assert(matches({ OR: [{ axis: "x|z" }, { AND: [{ lit: "true" }, { facing: "north" }] }] }, { axis: "z" }));
assert(!matches({ AND: [{ lit: "true" }, { facing: "north" }] }, { lit: "false", facing: "north" }));
const state = { multipart: [
  { apply: { model: "post" } },
  { when: { north: "true" }, apply: [{ model: "side1" }, { model: "side2" }] },
] };
assert.deepEqual(properties(state), { north: ["false", "true"] });
assert.deepEqual(selectedModels(state, { north: "false" }), [{ model: "post" }]);
assert.deepEqual(selectedModels(state, { north: "true" }, 1), [{ model: "post" }, { model: "side2" }]);
assert.deepEqual(selectedModels({ variants: { "axis=x": { model: "horizontal", x: 90 }, "axis=y": { model: "vertical" } } },
  { axis: "y" }), [{ model: "vertical" }]);
const cube = { from: [0, 0, 0], to: [16, 16, 16] };
const top = faceData(cube, "up", {});
assert.deepEqual(top.uvs, [0, 1, 0, 0, 1, 0, 1, 1]);
assert.deepEqual(faceData(cube, "up", { rotation: 90 }).uvs, [0, 0, 1, 0, 1, 1, 0, 1]);
assert.notDeepEqual(faceData(cube, "up", {}, { y: 90, uvlock: true }).uvs, top.uvs);
assert.deepEqual(faceData(cube, "up", {}, { y: 90 }).uvs, top.uvs);
for (const direction of ["north", "south", "east", "west", "up", "down"]) {
  const { positions, uvs } = faceData(cube, direction, {});
  assert.equal(positions.length, 12); assert.equal(uvs.length, 8);
  assert(positions.every(v => Math.abs(v) <= 0.5));
}
assert.equal(animationFrame({ frames: [1, { index: 0, time: 3 }] }, 2, 0), 1);
assert.equal(animationFrame({ frames: [1, { index: 0, time: 3 }] }, 2, 0.05), 0);
assert.equal(animationFrame({ frames: [1, { index: 0, time: 3 }] }, 2, 0.2), 1);
assert.deepEqual(animationGrid(1024, 32768, {}), { columns: 1, rows: 32, count: 32 });
assert.deepEqual(animationGrid(32, 32, { width: 16, height: 16 }), { columns: 2, rows: 2, count: 4 });
assert.deepEqual(animationGrid(1024, 1024, null), { columns: 1, rows: 1, count: 1 });
console.log("PASS: variants, multipart predicates, face UVs, rotations, UV lock and animation timing");
