# Companion 2K material packs

**Status: four-material local builder with CPU checks. Gameplay, appearance, and headset validation remain pending.**

AI albedo bake (Juggernaut + luma/Scharr LabPBR) lives in [`minecraft_texturepack_2048/README.md`](minecraft_texturepack_2048/README.md), not in `MODS_TO_CREATE.md`.

Photogrammetry-sourced 2048×2048 LabPBR terrain. The point is not a bigger PNG in the vanilla atlas. The point is real surfaces, close enough to touch, at a cost the hardware can actually pay.

## Build the small showcase

From the repository root, with Python 3.12+, Pillow, and NumPy:

```sh
python3 tools/build.py --download
python3 tools/test_build.py --pack build/companion-2k/companion-2k-showcase.zip
```

`--download` fetches missing, hash-pinned CC0 PNGs from Poly Haven over verified HTTPS. Later builds work offline without the flag.
Downloads and the finished ZIP remain under ignored `build/companion-2k/`. The builder does not install anything or edit a launcher profile.
Source hashes, authors, license URLs, and conversion details are in [`sources.json`](sources.json) and travel inside the pack.

| Block | Scan source | Authored material properties |
|---|---|---|
| Stone | [Rock Boulder Dry](https://polyhaven.com/a/rock_boulder_dry) | Dielectric F0 `10/255`, source roughness |
| Oak planks | [Plank Flooring](https://polyhaven.com/a/plank_flooring) | Dielectric F0 `10/255`, source roughness |
| Iron block | [Corrugated Iron](https://polyhaven.com/a/corrugated_iron) | Source metalness mask selects exact iron code `230` or dielectric `10` |
| Glowstone | Rock Boulder Dry | Authored emission `192`, not a claim of captured natural emission |

Each material has twelve levels of albedo, DirectX normal/AO/height, and LabPBR specular PNGs, from 2048×2048 through 1×1.
`assets/metallum_streaming/materials.json` supplies the version-1 bindings and complete precomputed mip chains.
High-resolution assets live outside `textures/` and do not enter the vanilla block atlas.
Ordinary consumers get matching 128-square albedo, `_n`, and `_s` fallback textures. They do **not** get streamed 2K without the streaming module.
This slice emits uncompressed RGBA8 data in lossless PNG files. ASTC remains a separate follow-up.

Mips average albedo in linear light and renormalize normal vectors. They preserve categorical metal codes and decode emission sentinel `255` before averaging.
The builder never treats numeric alpha as opacity. Scalar 16-bit sources use rounded 8-bit quantization; Pillow decodes RGB sources to 8-bit.
The upstream 2K export filter is unknown, so no filter-quality claim is made.

All four materials with complete RGBA8 mip chains total **268,435,440 bytes** before gutters and other allocations.
A 3×3-section neighborhood using this set has the same worst-case material bytes because sections share materials.
This excludes the fallback atlas, GPU alignment, metadata, CPU decoding, staging, and overlapping generations.

The CPU checks cover source provenance, channel encodings, every exported mip, fallback equivalence, and deterministic ZIP metadata.
The streaming CPU fixture also loads the actual ZIP through its production source loader.
These checks do not establish GPU sampling, visual quality, physical height calibration, or performance.
The full set below remains planned. This first build deliberately includes only four materials.

## Philosophy

Photogrammetry is the trick of photographing a real thing from many angles until albedo and shape fall out of the photographs. Quixel built an industry on it. Games that want stone to be stone, not a painted square, start there.

Minecraft started at 16×16. That is a typeface for a world. For twenty years the HD-pack tradition has argued the opposite: a block face can still be a *place*. Faithful, Sphax, Painterly, then 128 and 256, then LabPBR at 512. Roundista, Pixlli, and rotrBLOCKS live there because a complete 512 PBR set already fights the stitcher and VRAM. Pixlli’s own 512 notes that it runs fully on NVIDIA and that AMD needs lower-res swaps. That is the practical ceiling of “put every map in one atlas.”

Above 512 is not theoretical. It exists:

| Pack | What it actually is |
|---|---|
| Unbelievable Definition | Photoreal 128 / 512 / 2048 with handmade heightmaps for POM. Parsa Farvadian ships the lower sizes *because RAM fails*. That is brute-force 2K. |
| Faithful PBR 2048x | Faithful’s LabPBR line taken to 2048. |
| Fantastik 2048x, Verum HD | Same resolution class, photoreal marketing. |
| RuidSkin Ultimate | 256 through 2048+, with some textures claimed at 4096. Closest thing to a “highest texel” crown, and it is Patreon-gated and partial. |
| “8K” PMC packs | Usually upscales. Marketing resolution is not authored resolution. |

**Ours would not be the highest.** 4096 already appears. 8K appears as a word. Chasing the crown is how you get a 16k RGBA atlas and a hitch.

The pursuit those packs represent is *being there*. VR makes it honest: you lean in, and 16-pixel cobble becomes a font. 512 LabPBR is the last resolution a complete pack can shove through Minecraft’s atlas and still pretend it is a texture pack. Photogrammetry wants more texels than that because a real surface has pores, chips, and grit at the scale of a headset.

We embody that pursuit by refusing the atlas as the engine.

Apple Silicon is a watt budget. Unified memory, hardware ASTC decode, tile-based rendering, hardware ray tracing, MetalFX. Fidelity per watt is the thesis. Scan a surface once, keep numeric LabPBR honest, compress albedo for the GPU, stream only what the camera neighborhood needs. That is more material in front of your face per joule than an uncompressed 2048 sheet the stitcher cannot fit.

So 2048 is the **capture and authoring size**, not a promise that every block in the Overworld is resident. The texture-streaming module owns residency. VoxelSmooth’s triplanar mapping is how those scans sit on slopes without stretching. Native Metal shaders are how anti-repetition and POM read the maps without a CPU round trip.

Purchased packs stay out of Git. They are evidence that the hunger is real. They are not this content.

## Purpose

Supply a first-party photogrammetry set that streaming can resident-load, that Materials can identify, and that VoxelSmooth can sample on slopes.

## First material set

Start with a small, complete set. A scan of cobble that you can walk up to in VR beats a fake full-vanilla 2K atlas.

- stone, dirt, grass, sand, oak planks, oak log
- cobblestone, bricks, stone bricks
- iron block, gold block, glowstone
- water is out of scope until fluid shading exists

Each material includes:

| Map | LabPBR role |
|---|---|
| albedo | base color from the scan, downsampled to 2048 |
| `_n` RG | DirectX normal, green downward |
| `_n` B | ambient occlusion |
| `_n` A | height, 255 means no depth |
| `_s` R | perceptual smoothness |
| `_s` G | dielectric F0 or metal code |
| `_s` A | emission, 255 means none |

Named metals 230–237 and albedo-based generic metals follow the Materials decoder. Emission sentinel 255 is removed before filtering.

## Provenance

Keep a manifest per source file:

- origin URL or scan library (Poly Haven, ambientCG, or original capture)
- license and attribution text
- original resolution and downsampling filter
- whether generation filled a gap, and which maps it produced

Allowed sources are original photogrammetry, CC0/CC-BY libraries that permit this use, or assets with written permission. Record CC-BY attribution in the pack.

Photogrammetry is the default for albedo, height, and derived normals. Do not invent metal codes from a photograph. Smoothness, metal, and emission stay numeric LabPBR.

## Authoring

1. Ingest a scan at ≥2048. Downsample with a high-quality filter. Kaiser/Lanczos is the default unless measurement prefers another.
2. Produce height from the scan, then DirectX normals and AO.
3. Author smoothness, metal, and emission as numeric LabPBR.
4. Emit the texture-streaming GPU format: ASTC (or equivalent) for albedo, lossless or carefully quantized numeric maps. Offline compression is the watt-saving step. Hardware decode should carry the runtime.
5. Optional stochastic or noise maps for anti-repetition. Those maps are sampled by the native shader interface, not by CPU atlas code.

## Memory

2048² RGBA8 with mips is tens of megabytes per map. Three maps per material times the first set already needs a budget. The streaming module owns residency. This pack must list worst-case bytes for the full set and for a 3×3-section neighborhood.

That neighborhood budget is the product statement: close-up 2K where you look, not 2K everywhere.

## Acceptance

- The first set loads through Materials identifiers and the streaming format.
- A headset-distance view of cobble/stone shows scan detail, not an upscaled 16x tile.
- Metal codes survive mip generation.
- No purchased third-party PNG appears in Git.
- Attribution is complete for every non-original file.
- Runtime cost is argued in residency and compression, not in atlas dimensions.

## Dependencies

Texture-streaming format, Metallum Materials semantics, later VoxelSmooth triplanar sampling and native shader-pack bindings.

## Sharing built textures

Source PNGs stay uncommitted (see `.gitignore`). Built packs travel as
[release assets](https://github.com/codyhxyz/metallum-texture-pack/releases):
attach the finished ZIP to a release to share it between machines, and fetch
it with `gh release download <tag>`.
