#!/usr/bin/env python3
"""
Callable pipeline and CLI to generate Minecraft-conditioned 1024x1024 frames:
1. Extracts or loads a square texture or an animated vertical frame strip.
2. Extracts material-appropriate contours.
3. Drives draw-things-cli with Juggernaut XL + Canny ControlNet automatically.
4. Bakes frame-aligned LabPBR normal/height/AO and specular maps before publication.
"""

import argparse
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile
import cv2
import numpy as np
from PIL import Image
from generate_pbr import generate_material_maps

CLIENT_JAR = Path.home() / "Library/Application Support/ModrinthApp/meta/libraries/net/neoforged/minecraft-client-patched/21.10.64/minecraft-client-patched-21.10.64.jar"
DEFAULT_PROMPT = (
    "macro photography top-down orthographic cobblestone, rough granite rock boulders, "
    "deep mortar recesses, weathered stone texture, delit albedo, shadowless, uniform overcast"
)
DEFAULT_NEGATIVE_PROMPT = (
    "black lines, outlines, line art, cartoon, drawing, ink, illustration, "
    "smooth concrete, tiles, bathroom grid"
)
DEFAULT_MODEL = "juggernaut_xl_v9_q6p_q8p.ckpt"
DEFAULT_CONTROL_MODEL = "controlnet_canny_sdxl_v1.0_mid_f16.ckpt"
OUTPUT_SIZE = 1024
SURFACE_PROMPT = "macro photography, top-down orthographic, seamless texture, delit albedo, shadowless, uniform overcast"
SURFACE_NEGATIVE = "black outlines, cartoon, drawing, text, perspective, cast shadows, baked lighting"
# Keep the original stone recipe; other materials must not inherit its mortar mask.
MATERIAL_PRESETS = {
    "stone": (DEFAULT_PROMPT, DEFAULT_NEGATIVE_PROMPT, "organic"),
    "wood": (
        "{block}, natural wood surface, visible wood grain and fibers, " + SURFACE_PROMPT,
        SURFACE_NEGATIVE + ", stone, cobblestone, mortar",
        "edges",
    ),
    "metal": (
        "{block}, metal surface, fine metallic detail, " + SURFACE_PROMPT,
        SURFACE_NEGATIVE + ", stone, cobblestone, wood grain",
        "edges",
    ),
    "brick": (
        "{block}, masonry, straight brick courses and recessed mortar joints, " + SURFACE_PROMPT,
        SURFACE_NEGATIVE + ", rounded boulders, wood grain",
        "edges",
    ),
    "ore": (
        "{block}, mineral deposits embedded in rough host rock, " + SURFACE_PROMPT,
        SURFACE_NEGATIVE + ", brickwork, wood grain",
        "edges",
    ),
    "leaves": (
        "{block}, dense botanical leaf cluster, detailed leaf veins, " + SURFACE_PROMPT,
        SURFACE_NEGATIVE + ", stone, wood planks",
        "edges",
    ),
    "fire": (
        "{block}, isolated flame tongues, orange and yellow fire, black background, "
        "front view, emissive game sprite, no surroundings",
        "text, frame, fireplace, logs, scenery, smoke cloud",
        "edges",
    ),
    "generic": ("{block}, detailed material surface, " + SURFACE_PROMPT, SURFACE_NEGATIVE, "edges"),
}


def extract_vanilla_texture(
    block_name: str, client_jar: str | Path = CLIENT_JAR
) -> Image.Image:
    if not Path(client_jar).is_file():
        raise FileNotFoundError(f"Minecraft client jar not found: {client_jar}")
    target = f"assets/minecraft/textures/block/{block_name}.png"
    with zipfile.ZipFile(client_jar, "r") as zf:
        if target not in zf.namelist():
            raise FileNotFoundError(f"Texture {target} not in client jar")
        with Image.open(io.BytesIO(zf.read(target))) as img:
            return img.convert("RGBA")


def load_texture(source: str | Path, client_jar: str | Path):
    """Load pixels and optional Minecraft metadata without dropping alpha."""
    path = Path(source)
    if path.is_file():
        with Image.open(path) as img:
            image = img.convert("RGBA")
        sidecar = Path(str(path) + ".mcmeta")
        metadata = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    elif isinstance(source, Path) or path.suffix or path.parent != Path("."):
        raise FileNotFoundError(f"Source texture not found: {source}")
    else:
        image = extract_vanilla_texture(source, client_jar)
        with zipfile.ZipFile(client_jar) as archive:
            sidecar = f"assets/minecraft/textures/block/{source}.png.mcmeta"
            metadata = json.loads(archive.read(sidecar)) if sidecar in archive.namelist() else {}
    if not isinstance(metadata, dict):
        raise ValueError("Texture .mcmeta must contain a JSON object")
    return image, metadata


def texture_frames(image: Image.Image, metadata: dict, animate: bool, frame: int | None):
    """Support square tiles and vertical strips; reject other layouts explicitly."""
    size = image.width
    if image.height % size or image.height < size:
        raise ValueError("Expected square frames (e.g. 16x16) in a vertical strip")
    count = image.height // size
    animation = metadata.get("animation")
    if "animation" in metadata:
        if not isinstance(animation, dict):
            raise ValueError("The animation metadata must be an object")
        if any(animation.get(key, size) != size for key in ("width", "height")):
            raise ValueError("Only square frames in vertical animation strips are supported")
        duration = animation.get("frametime", 1)
        if type(duration) is not int or duration <= 0:
            raise ValueError("Animation frametime must be a positive integer")
        if type(animation.get("interpolate", False)) is not bool:
            raise ValueError("Animation interpolate must be true or false")
        frames = animation.get("frames", list(range(count)))
        if not isinstance(frames, list) or not frames:
            raise ValueError("Animation frames must be a nonempty list")
        for entry in frames:
            index = entry.get("index") if isinstance(entry, dict) else entry
            time = entry.get("time", duration) if isinstance(entry, dict) else duration
            if type(index) is not int or not 0 <= index < count or type(time) is not int or time <= 0:
                raise ValueError("Invalid animation frame index or duration")
    if animate and frame is not None:
        raise ValueError("Choose --animate or --frame, not both")
    if frame is not None:
        if type(frame) is not int or not 0 <= frame < count:
            raise ValueError(f"Frame must be between 0 and {count - 1}")
        metadata.pop("animation", None)
        return [frame]
    if count > 1 and not animate:
        raise ValueError(f"Source has {count} frames; use --frame 0 for a preview or --animate for all frames")
    if animate and animation is None:
        raise ValueError("--animate requires an animation section in the source .png.mcmeta")
    if animation is not None:
        animation.update(width=OUTPUT_SIZE, height=OUTPUT_SIZE)
    return range(count)


def create_organic_canny_guide(img: Image.Image, output_path: str, size: int = OUTPUT_SIZE):
    if img.width != img.height:
        raise ValueError(f"Expected a square source texture, got {img.width}x{img.height}")
    gray = np.array(img.convert("L"))
    # Dark crevice threshold (for cobblestone / stone textures)
    mortar_mask = (gray <= 100).astype(np.uint8) * 255

    # Tile 3x3 for periodic continuity
    tiled = np.tile(mortar_mask, (3, 3))
    up = cv2.resize(tiled, (size * 3, size * 3), interpolation=cv2.INTER_CUBIC)
    center = up[size:size * 2, size:size * 2]

    blurred = cv2.GaussianBlur(center, (31, 31), 10.0)
    _, rounded_blobs = cv2.threshold(blurred, 120, 255, cv2.THRESH_BINARY)
    stone_contours = cv2.Canny(rounded_blobs, 50, 150)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    stone_contours_clean = cv2.dilate(stone_contours, kernel, iterations=1)

    Image.fromarray(stone_contours_clean).save(output_path)
    return output_path


def create_edge_guide(img: Image.Image, output_path: str, low: float = 20, high: float = 60, size: int = OUTPUT_SIZE):
    """Preserve grain, board joints and other edges without assuming dark mortar."""
    if img.width != img.height:
        raise ValueError(f"Expected a square source texture, got {img.width}x{img.height}")
    if not 0 <= low < high <= 255:
        raise ValueError("Canny thresholds must satisfy 0 <= low < high <= 255")
    # Composite invisible pixels onto black so their hidden RGB cannot create edges.
    rgba = img.convert("RGBA")
    visible = Image.new("RGB", img.size)
    visible.paste(rgba, mask=rgba.getchannel("A"))
    gray = np.array(visible.convert("L"))
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    # Detect on a periodic (size/4)px tile, then enlarge the thin edges to full size.
    tile = max(size // 4, 16)
    tiled = cv2.resize(np.tile(gray, (3, 3)), (tile * 3, tile * 3), interpolation=cv2.INTER_CUBIC)
    edges = cv2.Canny(cv2.GaussianBlur(tiled, (5, 5), 1.0), low, high)
    guide = cv2.resize(edges[tile:tile * 2, tile:tile * 2], (size, size), interpolation=cv2.INTER_NEAREST)
    Image.fromarray(guide).save(output_path)
    return output_path


def run_draw_things_generation(
    guide_path: str | None,
    output_path: str,
    prompt: str,
    negative_prompt: str,
    steps: int = 22,
    cfg: float = 6.0,
    seed: int = 42,
    weight: float = 0.42,
    guidance_end: float = 0.35,
    size: int = OUTPUT_SIZE,
    model: str = DEFAULT_MODEL,
    control_model: str = DEFAULT_CONTROL_MODEL
):
    cfg_json = json.dumps({
        "controls": [
            {
                "weight": weight,
                "globalAveragePooling": False,
                "inputOverride": "canny",
                "file": control_model,
                "guidanceStart": 0.0,
                "noPrompt": False,
                "targetBlocks": [],
                "guidanceEnd": guidance_end,
                "controlImportance": "balanced",
                "downSamplingRate": 1.0
            }
        ] if guide_path is not None else []
    })

    cmd = [
        "draw-things-cli", "generate",
        "--model", model,
        "--prompt", prompt,
        "--negative-prompt", negative_prompt,
        "--strength", "1.0",
        "--steps", str(steps),
        "--cfg", str(cfg),
        "--seed", str(seed),
        "--width", str(size),
        "--height", str(size),
        "--config-json", cfg_json,
        "--output", output_path
    ]
    if guide_path is not None:
        cmd.extend(["--image", guide_path])

    subprocess.run(cmd, capture_output=True, text=True, check=True)


def generate_albedo(
    source: str | Path,
    output: str | Path,
    *,
    client_jar: str | Path = CLIENT_JAR,
    prompt: str | None = None,
    negative_prompt: str | None = None,
    material: str = "stone",
    guide: str | None = None,
    canny_low: float = 20,
    canny_high: float = 60,
    animate: bool = False,
    frame: int | None = None,
    grayscale: bool = False,
    steps: int = 22,
    cfg: float = 6.0,
    seed: int = 42,
    weight: float = 0.42,
    guidance_end: float = 0.35,
    model: str = DEFAULT_MODEL,
    control_model: str = DEFAULT_CONTROL_MODEL,
    size: int = OUTPUT_SIZE,
) -> Path:
    """Generate size x size albedo frames and their _n/_s maps as one material.

    Defaults preserve the cobblestone recipe. Use material="wood" for wood.
    Explicit prompts and guide modes override the material preset.
    Return the output path. Raise exceptions without exiting the caller.
    Existing outputs are replaced only after every frame and map passes validation.
    """
    if material not in MATERIAL_PRESETS:
        raise ValueError(f"Unknown material: {material}; choose from {', '.join(MATERIAL_PRESETS)}")
    preset_prompt, preset_negative, preset_guide = MATERIAL_PRESETS[material]
    guide = preset_guide if guide is None else guide
    if guide not in ("organic", "edges", "none"):
        raise ValueError(f"Unknown guide: {guide}; choose organic, edges or none")
    if not 0 <= canny_low < canny_high <= 255:
        raise ValueError("Canny thresholds must satisfy 0 <= low < high <= 255")
    if size < 64 or size % 64:
        raise ValueError("Size must be a multiple of 64 and at least 64")
    source_path = Path(source)
    if prompt is None:
        prompt = preset_prompt.format(block=source_path.stem.replace("_", " "))
    if negative_prompt is None:
        negative_prompt = preset_negative
    v_img, metadata = load_texture(source, client_jar)
    frames = texture_frames(v_img, metadata, animate, frame)

    output = Path(output)
    if output.suffix.lower() != ".png":
        raise ValueError("Output must have a .png extension")
    material_outputs = (output, output.with_name(output.stem + "_n.png"),
                        output.with_name(output.stem + "_s.png"))
    destinations = tuple(path for png in material_outputs for path in (png, Path(str(png) + ".mcmeta")))
    protected = {source_path.resolve(), Path(str(source_path) + ".mcmeta").resolve(), Path(client_jar).resolve()}
    if any(destination.resolve() in protected for destination in destinations):
        raise ValueError("Output must not overwrite the source texture or client JAR")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Stage beside the destination so a failed run cannot damage an existing albedo.
    with tempfile.TemporaryDirectory(prefix=".albedo-", dir=output.parent) as tmp:
        guide_file = str(Path(tmp) / "guide.png")
        generated = Path(tmp) / "albedo.png"
        result = Image.new("RGBA", (size, size * len(frames)))
        normal_strip = Image.new("RGBA", result.size)
        specular_strip = Image.new("RGBA", result.size)
        for slot, index in enumerate(frames):
            tile = v_img.crop((0, index * v_img.width, v_img.width, (index + 1) * v_img.width))
            if guide == "organic":
                create_organic_canny_guide(tile, guide_file, size)
            elif guide == "edges":
                create_edge_guide(tile, guide_file, canny_low, canny_high, size)
            generated.unlink(missing_ok=True)
            # Same seed reduces random changes; it does not guarantee temporal coherence.
            run_draw_things_generation(
                None if guide == "none" else guide_file, str(generated), prompt, negative_prompt,
                steps=steps, cfg=cfg, seed=seed, weight=weight,
                guidance_end=guidance_end, size=size, model=model, control_model=control_model,
            )
            with Image.open(generated) as img:
                if img.format != "PNG" or img.size != (size, size):
                    raise ValueError(f"Draw Things must produce a {size}x{size} PNG")
                img.verify()
            with Image.open(generated) as img:
                rgba = img.convert("RGBA")
            if grayscale:
                rgba = rgba.convert("L").convert("RGBA")
            # Preserve vanilla cutout silhouettes, not invented AI opacity.
            rgba.putalpha(tile.getchannel("A").resize((size, size), Image.Resampling.NEAREST))
            result.paste(rgba, (0, slot * size))
            rgba.save(generated)
            normal_path, specular_path = generate_material_maps(generated, Path(tmp) / "frame", material)
            for path, strip in ((normal_path, normal_strip), (specular_path, specular_strip)):
                with Image.open(path) as img:
                    strip.paste(img, (0, slot * size))
        result.save(generated)
        generated_normal, generated_specular = Path(tmp) / "albedo_n.png", Path(tmp) / "albedo_s.png"
        normal_strip.save(generated_normal)
        specular_strip.save(generated_specular)
        publications = []
        for staged, destination in zip((generated, generated_normal, generated_specular), material_outputs):
            publications.append((staged, destination))
            staged_meta = Path(str(staged) + ".mcmeta")
            if metadata:
                staged_meta.write_text(json.dumps(metadata, indent=2) + "\n")
            publications.append((staged_meta if metadata else None, Path(str(destination) + ".mcmeta")))
        # Publish the complete set. A map/metadata failure rolls every file back.
        backups = {}
        for index, destination in enumerate(destinations):
            if destination.exists():
                backup = Path(tmp) / f"backup-{index}"
                shutil.copy2(destination, backup)
                backups[destination] = backup
        try:
            for staged, destination in publications:
                if staged is not None:
                    staged.replace(destination)
                else:
                    destination.unlink(missing_ok=True)
        except OSError:
            for destination in destinations:
                if destination in backups:
                    backups[destination].replace(destination)
                else:
                    destination.unlink(missing_ok=True)
            raise
    return output


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("source", help="Vanilla block name or path to a square texture/vertical frame strip")
    p.add_argument("output", help="Albedo output path; matching _n/_s maps are always generated beside it")
    p.add_argument("--client-jar", type=Path, default=CLIENT_JAR)
    p.add_argument("--material", choices=MATERIAL_PRESETS, default="stone",
                   help="Prompt, guide and PBR recipe (default: stone/cobblestone)")
    p.add_argument("--guide", choices=("organic", "edges", "none"), help="Override or disable the preset guide")
    p.add_argument("--canny-low", type=float, default=20, help="Edge guide lower threshold")
    p.add_argument("--canny-high", type=float, default=60, help="Edge guide upper threshold")
    animation = p.add_mutually_exclusive_group()
    animation.add_argument("--animate", action="store_true", help="Generate every frame and preserve animation metadata")
    animation.add_argument("--frame", type=int, help="Generate one source frame as a static preview")
    p.add_argument("--grayscale", action="store_true", help="Produce neutral albedo for biome-tinted textures")
    p.add_argument("--prompt", help="Override the material prompt")
    p.add_argument("--negative-prompt", help="Override the material negative prompt")
    p.add_argument("--size", type=int, default=1024, help="Output frame size in pixels (default 1024)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--steps", type=int, default=22)
    p.add_argument("--cfg", type=float, default=6.0)
    p.add_argument("--weight", type=float, default=0.42)
    p.add_argument("--guidance-end", type=float, default=0.35)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--control-model", default=DEFAULT_CONTROL_MODEL)
    args = p.parse_args(argv)
    try:
        output = generate_albedo(**vars(args))
    except subprocess.CalledProcessError as exc:
        p.exit(1, f"Draw Things failed: {exc.stderr or exc}\n")
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        p.exit(1, f"{exc}\n")
    print(f"Generated material: {output}, {output.stem}_n.png, {output.stem}_s.png")


if __name__ == "__main__":
    main()
