// Three.js r160 adapter. Packed data stays on the GPU: canvas alpha compositing
// would destroy normal/AO values wherever LabPBR height (alpha) is zero.
export const HEIGHT_RANGE = 0.25;

export const NORMAL_DECODE = `
vec3 labNormal(vec2 encoded) {
    vec2 xy = encoded * 2.0 - 1.0;
    return vec3(xy, sqrt(max(0.0, 1.0 - dot(xy, xy))));
}
`;

// vLabPosition is the UNDISPLACED surface. Measuring the displaced surface
// here would feed displacement back into normal strength.
export const NORMAL_SAMPLE = `
vec3 mapN = labNormal(texture2D(normalMap, vNormalMapUv).rg);
vec3 px = dFdx(vLabPosition), py = dFdy(vLabPosition);
vec2 ux = dFdx(vNormalMapUv), uy = dFdy(vNormalMapUv);
float det = ux.x * uy.y - ux.y * uy.x;
vec2 tileSize = vec2(1.0);
if (abs(det) > 1e-12) {
    tileSize = vec2(length((px * uy.y - py * ux.y) / det),
                    length((py * ux.x - px * uy.x) / det));
}
mapN.xy *= labDepth / (${HEIGHT_RANGE} * max(tileSize, vec2(1e-6)));
`;

function replace(source, before, after) {
    if (!source.includes(before)) throw new Error(`Three.js shader changed: ${before}`);
    return source.replace(before, after);
}

export function patchLabPBR(shader, chunks, uniforms) {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = `varying vec3 vLabPosition;\n` + shader.vertexShader;
    shader.vertexShader = replace(shader.vertexShader, "#include <displacementmap_vertex>",
        "vLabPosition = (modelViewMatrix * vec4(transformed, 1.0)).xyz;\n" +
        replace(chunks.displacementmap_vertex,
            "texture2D( displacementMap, vDisplacementMapUv ).x",
            "texture2D( displacementMap, vDisplacementMapUv ).a"));

    shader.fragmentShader = `
uniform float labDepth;
uniform int labInspect;
varying vec3 vLabPosition;
${NORMAL_DECODE}
` + shader.fragmentShader;
    shader.fragmentShader = replace(shader.fragmentShader, "#include <normal_fragment_maps>",
        replace(chunks.normal_fragment_maps,
            "vec3 mapN = texture2D( normalMap, vNormalMapUv ).xyz * 2.0 - 1.0;", NORMAL_SAMPLE));
    shader.fragmentShader = replace(shader.fragmentShader, "#include <aomap_fragment>",
        replace(chunks.aomap_fragment, "texture2D( aoMap, vAoMapUv ).r",
            "texture2D( aoMap, vAoMapUv ).b"));
    // Three's BRDF already squares perceptual roughness. Do not square it twice.
    shader.fragmentShader = replace(shader.fragmentShader, "#include <roughnessmap_fragment>",
        replace(chunks.roughnessmap_fragment, "texelRoughness.g", "(1.0 - texelRoughness.r)"));
    // LabPBR packs the metal flag in specular green (10 dielectric, 230 metal).
    // Three reads metalness from blue, so derive it from the same _s texel instead.
    shader.fragmentShader = replace(shader.fragmentShader, "#include <metalnessmap_fragment>",
        `${chunks.metalnessmap_fragment}
#ifdef USE_ROUGHNESSMAP
\tmetalnessFactor = step( 0.5, texelRoughness.g );
#endif`);
    shader.fragmentShader = replace(shader.fragmentShader, "#include <map_fragment>",
        replace(chunks.map_fragment, "diffuseColor *= sampledDiffuseColor;", `
if (labInspect == 2) sampledDiffuseColor.rgb = labNormal(sampledDiffuseColor.rg) * 0.5 + 0.5;
if (labInspect == 3) sampledDiffuseColor.rgb = vec3(sampledDiffuseColor.a);
if (labInspect == 4) sampledDiffuseColor.rgb = vec3(1.0 - sampledDiffuseColor.r);
if (labInspect == 5) sampledDiffuseColor.rgb = vec3(sampledDiffuseColor.b);
if (labInspect > 1) sampledDiffuseColor.a = 1.0;
diffuseColor *= sampledDiffuseColor;
`));
    shader.fragmentShader = replace(shader.fragmentShader, "#include <opaque_fragment>",
        "if (labInspect != 0) outgoingLight = diffuseColor.rgb;\n#include <opaque_fragment>");
    shader.fragmentShader = replace(shader.fragmentShader, "#include <colorspace_fragment>",
        "#include <colorspace_fragment>\nif (labInspect > 1) gl_FragColor.rgb = diffuseColor.rgb;");
}
