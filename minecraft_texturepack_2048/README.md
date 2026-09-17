# AI Albedo &rarr; LabPBR 1.3 Material Pipeline

Source albedos and baked `_n`/`_s` PNG files stay uncommitted. This README and `generate_pbr.py` are the authoritative pipeline for the 2048x resource pack.

## 1. Automated CLI Generation (`generate_vanilla_albedo.py`)

Generate Minecraft-conditioned 1024×1024 albedos and their LabPBR maps in one call. This script does not upscale them to 2K.

```bash
# Generate my_cobble.png, my_cobble_n.png and my_cobble_s.png:
python3 generate_vanilla_albedo.py cobblestone my_cobble.png --seed 42

# View in 3D:
open http://127.0.0.1:8765/labpbr-viewer.html
```

### Other materials

Run these commands from this directory:

```bash
python3 generate_vanilla_albedo.py oak_planks oak_planks.png --material wood
python3 generate_vanilla_albedo.py bricks bricks.png --material brick
python3 generate_vanilla_albedo.py iron_ore iron_ore.png --material ore
python3 generate_vanilla_albedo.py oak_leaves oak_leaves.png --material leaves --grayscale
python3 generate_vanilla_albedo.py fire_0 fire_preview.png --material fire --frame 0

# Generate all fire frames and fire_0.png.mcmeta (one inference per source frame):
python3 generate_vanilla_albedo.py fire_0 fire_0.png --material fire --animate

# An unlisted material needs no code changes:
python3 generate_vanilla_albedo.py sand sand.png --material generic \
  --prompt "top-down fine pale sand, seamless game texture, shadowless albedo"

# Only for an existing albedo made outside this pipeline:
python3 generate_pbr.py oak_planks.png oak_planks --profile wood
```

Presets select the prompt, negative prompt, and guide method:

| Material | Guide | Purpose |
|---|---|---|
| `stone` (default) | `organic` | Original cobblestone recipe, with rounded dark mortar contours |
| `wood` | `edges` | Wood grain and board joints |
| `brick` | `edges` | Straight masonry joints |
| `ore` | `edges` | Mineral deposits in host rock |
| `metal` | `edges` | Metal surfaces |
| `leaves` | `edges` | Leaf clusters |
| `fire` | `edges` | Flame sprites |
| `generic` | `edges` | Other materials with a custom prompt |

`--prompt`, `--negative-prompt`, and `--guide` override the preset independently.
`--guide none` disables ControlNet. `--canny-low` and `--canny-high` adjust edge detection.
The edge guide does not assume dark mortar or rounded stones.
Use `--material generic` for unlisted blocks; the script does not infer their material from the filename.

Source alpha is preserved with nearest-neighbor scaling, including partial transparency.
This preserves Minecraft silhouettes; it does not generate higher-resolution cutout boundaries.
For biome-tinted textures such as oak leaves, use `--grayscale` to avoid an extra baked color tint.
Do not use that option for untinted colored leaves, such as cherry leaves.

Animated sources require `--animate` or a zero-based `--frame` preview.
The script supports square frames in vertical strips and preserves `.png.mcmeta` frame order, timing, and interpolation.
It scales explicit frame dimensions to 1024 pixels. Other animation layouts are rejected before inference.
All frames use the same seed, but independently generated frames can still flicker.
Review animation, alpha boundaries, color, and tile seams before pack installation.
Seamless output is not guaranteed.

PBR baking now runs automatically for every generated frame, in both the CLI and Python API.
The complete albedo, `_n`, and `_s` strips share frame positions and matching `.png.mcmeta` files.
No separate bake command or opt-in flag is required.

| Generation material | Automatic PBR recipe |
|---|---|
| `stone`, `brick`, `ore`, `generic` | Stone-derived dielectric maps |
| `wood` | Wood profile |
| `metal` | Metal profile (iron code `230`) |
| `leaves` | Provisional stone-derived dielectric maps, with neutral data in transparent texels |
| `fire` | Flat normals, no recess depth, full emission on visible texels |

These recipes do not infer leaf translucency or per-mineral metal masks.
The low-level `generate_pbr.py` CLI still expects a single surface. Do not pass an animation strip to it.
The automatic pipeline uses `generate_material_maps()` separately for each frame.

The same pipeline is callable from Python:

```python
from generate_vanilla_albedo import generate_albedo

output = generate_albedo("cobblestone", "my_cobble.png", seed=42)
output = generate_albedo("oak_planks", "oak_planks.png", material="wood")
# Or use a local square image:
output = generate_albedo("source.png", "custom.png", material="generic",
                         prompt="top-down weathered rock, shadowless")
```

`source` accepts a vanilla block name or an image path, with an optional adjacent `.png.mcmeta` file.
`generate_albedo()` returns the albedo `Path` and always writes matching `_n.png` and `_s.png` files beside it.
It raises exceptions on failure and removes temporary files.
Generation or PBR validation failures leave the existing material unchanged.
Publication errors trigger rollback of all three PNGs and their metadata; publication is not crash-atomic.
Static output removes stale animation metadata from all three destinations.

Keyword options are `client_jar`, `material`, `guide`, `prompt`, `negative_prompt`, `canny_low`, `canny_high`,
`animate`, `frame`, `grayscale`, `seed`, `steps`, `cfg`, `weight`, `guidance_end`, `model`, and `control_model`.
The CLI exposes the same options with hyphenated names, such as `--client-jar` and `--guidance-end`.
The default JAR location uses the current user's Modrinth directory and version `21.10.64`. Other installations require an explicit JAR path.
Generation requires `draw-things-cli` and the selected local models.
Python dependencies are Pillow, NumPy, and OpenCV.

Run offline checks without Draw Things:

```bash
python3 -m unittest test_generate_vanilla_albedo -v
```

---

## 2. Albedo Generation Settings & Details (Draw Things)

- **Model:** `Juggernaut XL v9 (8-bit)`
- **Sampler:** `DPM++ 2M Karras`
- **Control:** `Canny Edge Map (SDXL, Diffusers 1.0 Mid)` from organic stone contours
- **Control Settings:** Weight `0.42`, Mode `Balanced`, Start `0.0` / End `0.35`
- **Strength:** 100% (Text to Image), CFG `6.0`, Size `1024×1024`, Steps: `22`
- **Disabled:** Tiled Diffusion, Hires Fix, Refiner, LoRAs

### Prompts

**Positive:**
```text
top-down orthographic cobblestone, deep fissures, seamless tile, delit albedo, shadowless, uniform overcast
```

**Negative:**
```text
shadow, sunlight, rim light, studio lighting, vignette, AO, baked lighting, 3d render
```

The guide uses repeated source tiles at its boundaries. This does not guarantee seamless AI output.

---

## 2. LabPBR 1.3 Bake Pipeline (`generate_pbr.py`)

Bake standard LabPBR 1.3 maps from any albedo using unified **Dyadic Laplacian Octave Decomposition**:

```bash
# Stone (default)
python3 generate_pbr.py albedo.png [output_prefix] --profile stone

# Wood / Planks
python3 generate_pbr.py albedo.png [output_prefix] --profile wood

# Metal
python3 generate_pbr.py albedo.png [output_prefix] --profile metal
```

Height estimation remains an artistic brightness-based approximation, not measured geometry.
Coarse bands retain the weights, bilateral filter, and height normalization from `1311558`.
Fine bands B0/B1 bypass that filter and are added after macro-height normalization.
`--detail-strength` controls their contribution (default `0.15`; `0` disables it).
The detail is not contrast-normalized. Its maximum positive or negative height contribution is 1% of the total relief range.
At the viewer's `0.06` depth, that cap is `0.0006` scene units.
AO and smoothness use the coarse surface and do not change with the detail control.
Edge-height matching does not guarantee matching slopes.

```bash
python3 generate_pbr.py vanilla_cobble_v2.png vanilla_cobble_v2_detail --detail-strength 0.15
```

**Normal calibration:** `height_to_normal()` differentiates height in UV units, including image width and height.
LabPBR height spans a quarter of a unit tile. Both normal components use negative derivatives in image coordinates.
The viewer flips green once for Three.js. There is no extra normal-only grain blend.
This makes normal slopes resolution-independent for the same sampled height surface.
The image-to-height filters still use pixel-sized kernels, so regenerating from resized albedos can change the estimated surface.

| Texture Map | Channel | Content / Role |
|---|---|---|
| `_n.png` | **R** | DirectX Normal X |
| `_n.png` | **G** | DirectX Normal Y (+Y is downward) |
| `_n.png` | **B** | Ambient Occlusion (derived from crevices) |
| `_n.png` | **A** | Height / Displacement (0..255 for POM) |
| `_s.png` | **R** | Perceptual Smoothness (flatter ridges smoother, deep crevices rougher) |
| `_s.png` | **G** | Dielectric F0 `10` (or `230` for iron) |
| `_s.png` | **B** | Porosity `40` (stone wetness porosity) |
| `_s.png` | **A** | Emission sentinel `255` (no emission) |

---

## 3. LabPBR Viewer (`labpbr-viewer.html`)

Open **LabPBR Viewer** in your browser (or visit local server `http://127.0.0.1:8765/labpbr-viewer.html`):

```bash
open labpbr-viewer.html
```

- **Inspect in 3D:** Sharp Minecraft block, smooth beveled block, 2×2 repeat plane, or sphere.
- **Inspect Channels:** View Full PBR, Albedo only, Normal map, Height/Displacement, or Roughness.
- **Interactive Light:** Orbit the sun or tweak altitude and azimuth to see specular glints and crevice shadows.
- **Drag & Drop:** Drop any `*.png`, `*_n.png`, or `*_s.png` files onto the window to test custom bakes instantly.

The viewer reconstructs normal Z from RG. It reads AO from blue, height from alpha, and perceptual roughness from inverted specular red.
Packed maps stay on the GPU without canvas alpha compositing. Channel isolation is unlit.

Depth Scale controls displacement and normal strength together, with local UV scale and tiling included.
Keep **Normal Override** at `1.0` and **DirectX Flip** enabled for calibrated maps.
The `0.06` default is a preview choice, not a measured rock depth. AO starts disabled to expose the surface shape.
Vertex displacement is only a preview, not Minecraft's POM implementation. Steep walls and sharp cube corners can still stretch or separate.

Presets load `*_detail_n.png` and `*_detail_s.png` files.
**Cobblestone · Before Detail** loads the previous calibrated bake. Previous maps remain intact.
Texture filtering uses up to 8× anisotropy, subject to browser support.
For example, rebuild the default preset with:

```bash
python3 generate_pbr.py vanilla_cobble_v2.png vanilla_cobble_v2_detail
```

Old and imported normals can have different strength conventions. Their encoding can decode correctly without matching the new displacement calibration.
The viewer does not yet implement all LabPBR specular features, including metal IDs and emission.

Offline checks:

```bash
python3 -m unittest test_generate_pbr test_generate_vanilla_albedo -v
node test_labpbr_viewer.mjs
```

The JavaScript check also compiles the normal GLSL when `glslangValidator` is installed.

The five `candidate-*` previews now have matching PBR maps and viewer presets.
Leaves remain a provisional dielectric recipe. Fire remains a static preview, with flat emissive maps.
The viewer uses an explicit full-emission setting for that fire preset, not a general LabPBR emission decoder.
`bake_candidate_pbr.py` records source/map hashes and refuses to overwrite existing candidate maps.

After approval for a graphical test, render a matched comparison with a disposable Chrome profile:

```bash
node compare_surface_detail.mjs vanilla_cobble_v2_calibrated vanilla_cobble_v2_detail /path/to/evidence
```

The script requires the viewer server on port 8765 and Google Chrome in `/Applications`.
It captures the real viewer with identical source, camera, lighting, roughness, and depth on both sides.
It produces `comparison.html`, `comparison.png`, individual captures, and `validation.json`.
