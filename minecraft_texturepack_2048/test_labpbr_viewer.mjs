// Offline: node test_labpbr_viewer.mjs
// Optional: pass a downloaded three@0.160.0/build/three.module.js as .mjs
// to verify the adapter against the complete upstream shader sources.
import assert from "node:assert/strict";
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";
import { patchLabPBR, NORMAL_DECODE, NORMAL_SAMPLE } from "./labpbr-viewer-shaders.mjs";

// r160 replacement anchors; a version mismatch must fail, not silently render wrong.
const chunks = {
    displacementmap_vertex: "texture2D( displacementMap, vDisplacementMapUv ).x",
    normal_fragment_maps: "vec3 mapN = texture2D( normalMap, vNormalMapUv ).xyz * 2.0 - 1.0;\nmapN.xy *= normalScale;",
    aomap_fragment: "texture2D( aoMap, vAoMapUv ).r",
    roughnessmap_fragment: "texelRoughness.g",
    metalnessmap_fragment: "float metalnessFactor = metalness;",
    map_fragment: "diffuseColor *= sampledDiffuseColor;",
};
const shader = {
    uniforms: {},
    vertexShader: "#include <displacementmap_vertex>",
    fragmentShader: ["normal_fragment_maps", "aomap_fragment", "roughnessmap_fragment", "metalnessmap_fragment", "map_fragment", "opaque_fragment", "colorspace_fragment"]
        .map(name => `#include <${name}>`).join("\n"),
};
const uniforms = { labDepth: { value: 0.06 }, labInspect: { value: 0 } };
patchLabPBR(shader, chunks, uniforms);
assert.equal(shader.uniforms.labDepth, uniforms.labDepth);
assert.match(shader.vertexShader, /vDisplacementMapUv \)\.a/);
assert.match(shader.fragmentShader, /vAoMapUv \)\.b/);
assert.match(shader.fragmentShader, /metalnessFactor = step\( 0\.5, texelRoughness\.g \)/);
assert.match(shader.fragmentShader, /labNormal\(texture2D\(normalMap, vNormalMapUv\)\.rg\)/);
assert.match(shader.fragmentShader, /mapN\.xy \*= normalScale/); // Green flip still applies once.
assert.match(shader.fragmentShader, /outgoingLight = diffuseColor\.rgb/); // Unlit ISO.
assert.throws(() => patchLabPBR({ uniforms: {}, vertexShader: "", fragmentShader: "" }, chunks, uniforms),
    /Three.js shader changed/);

if (process.argv[2]) {
    const three = await import(pathToFileURL(resolve(process.argv[2])));
    assert.equal(three.REVISION, "160");
    patchLabPBR({ ...three.ShaderLib.standard, uniforms: {} }, three.ShaderChunk, uniforms);
}

const html = readFileSync(new URL("./labpbr-viewer.html", import.meta.url), "utf8");
assert.ok(!html.includes("getImageData")); // Never alpha-composite packed data through canvas.
assert.match(html, /premultiplyAlpha: "none"/);
assert.match(html, /labUniforms\.labDepth\.value = emissiveSprite \? 0 : dispVal/);
assert.match(shader.fragmentShader, /if \(labInspect > 1\) sampledDiffuseColor\.a = 1\.0/);
assert.match(html, /material\.emissiveMap = mapAlbedo/);
assert.match(html, /mapHeight = nrm/);
assert.match(html, /mapHeight = tex/);
// Every baked preset pins a preview depth; shallow masonry must not inherit rock depth.
for (const preset of ["candidate_oak_planks", "candidate_bricks", "candidate_iron_ore",
    "candidate_oak_leaves", "candidate_fire_0", "vanilla_cobble_mat", "vanilla_cobble_before",
    "granite_mat", "granite_legacy_mat", "cobble_mat"]) {
  assert.match(html, new RegExp(`${preset}[\\s\\S]*?depth: 0(\\.0\\d+)?`));
}
assert.match(html, /if \(p\.depth !== undefined\)/);
assert.match(html, /getElementById\("dispScale"\)\.value = p\.depth/);

// Compile the actual GLSL functions without launching a browser or rendering.
const tmp = mkdtempSync(join(tmpdir(), "labpbr-shader-"));
try {
    const file = join(tmp, "normal.frag");
    writeFileSync(file, `#version 300 es
#define texture2D texture
precision highp float;
uniform sampler2D normalMap;
uniform float labDepth;
in vec3 vLabPosition;
in vec2 vNormalMapUv;
out vec4 color;
${NORMAL_DECODE}
void main() {
${NORMAL_SAMPLE}
color = vec4(normalize(mapN), 1.0);
}
`);
    const compiled = spawnSync("glslangValidator", ["-S", "frag", file], { encoding: "utf8" });
    if (compiled.error?.code === "ENOENT") {
        console.log("SKIP GLSL compilation: glslangValidator is not installed");
    } else {
        assert.equal(compiled.status, 0, compiled.stdout + compiled.stderr);
    }
} finally {
    rmSync(tmp, { recursive: true, force: true });
}
console.log("PASS: LabPBR channels, scale wiring, shader adapter, and GLSL");
