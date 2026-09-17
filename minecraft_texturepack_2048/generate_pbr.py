#!/usr/bin/env python3
"""
Heuristic image-to-height generator with calibrated LabPBR 1.3 normals.
Uses a unified Dyadic Laplacian Octave Decomposition.
Coarse height retains the octave weights and bilateral filter.
Fine source detail bypasses that filter, with a bounded height contribution.
Normals derive from that height at LabPBR's quarter-tile depth.
Generates _n.png (DirectX Normal + AO + Height) and _s.png (Smoothness + Metal + Porosity).
"""

import os
import sys
from pathlib import Path
import cv2
import numpy as np
from PIL import Image

MATERIAL_PROFILES = {
    "stone": {
        # Artistic weights for B2..B7; B0/B1 use the separate detail control.
        "coarse_octave_weights": [0.18, 0.35, 0.60, 0.85, 1.00, 1.00],
        "base_smoothness": 55.0,
        "is_metal": False,
        "porosity": 40
    },
    "ore": {
        # Host rock uses stone relief; bright desaturated flecks read as raw metal.
        "coarse_octave_weights": [0.18, 0.35, 0.60, 0.85, 1.00, 1.00],
        "base_smoothness": 55.0,
        "is_metal": False,
        "porosity": 40,
        "metal_luminance": 0.55,
        "metal_saturation": 0.15,
        # Mottled photo albedo: kill mid-frequency color blotches, keep slabs.
        "relief_blur": 8.0,
    },
    "metal": {
        "coarse_octave_weights": [0.25, 0.20, 0.10, 0.05, 0.02, 0.02],
        "base_smoothness": 180.0,
        "is_metal": True,
        "porosity": 0
    },
    "wood": {
        "coarse_octave_weights": [0.35, 0.50, 0.40, 0.25, 0.15, 0.10],
        "base_smoothness": 80.0,
        "is_metal": False,
        "porosity": 60
    }
}

# LabPBR height 0..1 spans a quarter of a unit UV tile.
LABPBR_HEIGHT_RANGE = 0.25
DEFAULT_DETAIL_STRENGTH = 0.15


def add_surface_detail(height, detail, strength):
    """Add source contrast as fine relief, with a resolution-aware cap.

    No per-image normalization: weak source detail stays weak.
    The cap bounds single-pixel steps to a ~20-degree slope so photo grain
    cannot become cliffs at high resolutions; coherent multi-pixel slopes pass.
    """
    if not np.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError("Detail strength must be finite and between 0 and 1")
    cap = 1.5 / max(np.shape(height)[:2])
    return np.clip(height + np.clip(detail * strength, -cap, cap), 0.0, 1.0)


def height_to_normal(height):
    """Return DirectX XYZ normals. Image rows increase downward, so both signs are negative."""
    height = np.asarray(height, dtype=np.float32)
    if height.ndim != 2 or min(height.shape) < 2 or not np.isfinite(height).all():
        raise ValueError("Height must be a finite 2D array with at least two rows and columns")
    h, w = height.shape
    gx = (np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1)) * (w * 0.5)
    gy = (np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0)) * (h * 0.5)
    normal = np.dstack((-gx * LABPBR_HEIGHT_RANGE, -gy * LABPBR_HEIGHT_RANGE, np.ones_like(height)))
    return normal / np.linalg.norm(normal, axis=2, keepdims=True)


def generate_pbr(
    image_path: str,
    output_prefix: str = None,
    material_type: str = "stone",
    is_emissive: bool = False,
    detail_strength: float = DEFAULT_DETAIL_STRENGTH,
):
    if not np.isfinite(detail_strength) or not 0 <= detail_strength <= 1:
        raise ValueError("Detail strength must be finite and between 0 and 1")
    if not os.path.exists(image_path):
        print(f"Error: {image_path} not found.")
        sys.exit(1)

    if output_prefix is None:
        base, _ = os.path.splitext(image_path)
        output_prefix = base

    profile = MATERIAL_PROFILES.get(material_type, MATERIAL_PROFILES["stone"])

    # 1. Existing sRGB brightness heuristic; this is not linear-light luminance.
    albedo = Image.open(image_path).convert("RGB")
    w, h = albedo.size
    rgb = np.array(albedo, dtype=np.float32) / 255.0
    gray = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    # Single-pixel photo grain is albedo noise, not surface relief. Low-pass the
    # decomposition input so grain cannot become cliffs; masks and micro-finish
    # below still use the sharp luminance.
    relief = cv2.GaussianBlur(gray, (0, 0), sigmaX=profile.get("relief_blur", 1.5))

    # 2. Dyadic Laplacian Octave Decomposition (1px to 64px)
    sigmas = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0]
    gaussians = [cv2.GaussianBlur(relief, (0, 0), sigmaX=s, sigmaY=s) for s in sigmas]
    dc_background = cv2.GaussianBlur(relief, (0, 0), sigmaX=128.0, sigmaY=128.0)

    laplacians = []
    prev = relief
    for g in gaussians:
        laplacians.append(prev - g)
        prev = g
    laplacians.append(prev - dc_background)

    # 3. Synthesize Continuous Height Field
    # Only coarse bands go through the mortar filter. B0/B1 bypass it below.
    weights = profile["coarse_octave_weights"]
    height_field = np.zeros_like(gray)
    for band, weight in zip(laplacians[2:], weights):
        height_field += band * weight

    # Existing artistic smoothing of crevice boundaries.
    height_field = cv2.bilateralFilter(height_field.astype(np.float32), d=11, sigmaColor=0.10, sigmaSpace=6.0)

    # Full 0..1 dynamic range normalization
    h_min, h_max = np.percentile(height_field, 0.5), np.percentile(height_field, 99.5)
    height = np.clip((height_field - h_min) / (h_max - h_min + 1e-6), 0.0, 1.0)

    # 4. Existing edge-height blend. This does not guarantee matching slopes.
    fade = 16
    seam_y = 0.5 * (height[0, :] + height[-1, :])
    t_y = np.linspace(0.0, 1.0, fade)[:, None]
    height[:fade, :] = t_y * height[:fade, :] + (1.0 - t_y) * seam_y
    height[-fade:, :] = (1.0 - t_y[::-1]) * height[-fade:, :] + t_y[::-1] * seam_y
    height[0, :] = seam_y
    height[-1, :] = seam_y

    seam_x = 0.5 * (height[:, 0] + height[:, -1])
    t_x = np.linspace(0.0, 1.0, fade)[None, :]
    height[:, :fade] = t_x * height[:, :fade] + (1.0 - t_x) * seam_x[:, None]
    height[:, -fade:] = (1.0 - t_x[:, ::-1]) * height[:, -fade:] + t_x[:, ::-1] * seam_x[:, None]
    height[:, 0] = seam_x
    height[:, -1] = seam_x

    # Keep AO and smoothness independent of the detail knob.
    macro_height = height
    fine_detail = laplacians[0] + laplacians[1]
    # Preserve the existing edge-height constraint; taper only the fine residual
    # at the boundary, rather than blurring grain across the whole texture.
    edge_x = np.minimum(np.arange(w), np.arange(w)[::-1])
    edge_y = np.minimum(np.arange(h), np.arange(h)[::-1])
    edge_fade = np.minimum(np.minimum(edge_y[:, None], edge_x[None, :]) / 4.0, 1.0)
    height = add_surface_detail(macro_height, fine_detail * edge_fade, detail_strength)

    # 5. Matching normals: physical slopes per UV tile, not per pixel.
    normal = height_to_normal(height)
    r_normal = np.rint((normal[..., 0] * 0.5 + 0.5) * 255.0).astype(np.uint8)
    g_normal = np.rint((normal[..., 1] * 0.5 + 0.5) * 255.0).astype(np.uint8)

    # 6. Ambient Occlusion from Crevice Depth
    ao_blur = cv2.GaussianBlur(macro_height, (0, 0), sigmaX=8.0)
    ao = np.clip(macro_height - ao_blur + 0.85, 0.1, 1.0)
    b_ao = (ao * 255.0).astype(np.uint8)
    a_height = (height * 255.0).astype(np.uint8)

    normal_img = np.dstack((r_normal, g_normal, b_ao, a_height))
    normal_path = f"{output_prefix}_n.png"
    Image.fromarray(normal_img, mode="RGBA").save(normal_path)
    print(f"✓ Generated LabPBR Normal & Height Map: {normal_path}")

    # 7. Specular / Smoothness Map
    micro_fine = gray - cv2.GaussianBlur(gray, (0, 0), sigmaX=1.5)
    smoothness = np.clip(profile["base_smoothness"] + (macro_height * 30.0) - (np.abs(micro_fine) * 140.0), 10.0, 240.0)
    r_smooth = smoothness.astype(np.uint8)
    g_metal = np.full((h, w), 230 if profile["is_metal"] else 10, dtype=np.uint8)
    b_porosity = np.full((h, w), profile["porosity"], dtype=np.uint8)
    if "metal_luminance" in profile:
        mx, mn = rgb.max(axis=2), rgb.min(axis=2)
        fleck = ((gray > profile["metal_luminance"]
                  ) & ((mx - mn) / (mx + 1e-6) < profile["metal_saturation"])).astype(np.uint8)
        # Isolated bright pixels are glints, not nuggets; keep coherent flecks only.
        fleck = cv2.morphologyEx(fleck, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)).astype(bool)
        r_smooth = np.where(fleck, 200, r_smooth)
        g_metal = np.where(fleck, 230, g_metal)
        b_porosity = np.where(fleck, 0, b_porosity)
    a_emission = np.full((h, w), 192 if is_emissive else 255, dtype=np.uint8)

    specular_img = np.dstack((r_smooth, g_metal, b_porosity, a_emission))
    specular_path = f"{output_prefix}_s.png"
    Image.fromarray(specular_img, mode="RGBA").save(specular_path)
    print(f"✓ Generated LabPBR Specular & Smoothness Map: {specular_path}")

    return normal_path, specular_path


def generate_material_maps(image_path, output_prefix, material="stone"):
    """Bake one square albedo frame, including cutout and fire handling.

    Leaves/generic use provisional dielectric maps, not inferred SSS or metal masks.
    Callers stage these files before publishing a complete material.
    """
    profile = {"brick": "stone", "leaves": "stone", "generic": "stone",
               "fire": "emissive_sprite"}.get(material, material)
    if profile not in (*MATERIAL_PROFILES, "emissive_sprite"):
        raise ValueError(f"Unknown PBR material: {material}")
    with Image.open(image_path) as img:
        if img.width != img.height:
            raise ValueError("Bake PBR one square frame at a time, not an animation strip")
        alpha = img.convert("RGBA").getchannel("A")
        size = img.size
    normal_path, specular_path = Path(f"{output_prefix}_n.png"), Path(f"{output_prefix}_s.png")
    if profile == "emissive_sprite":
        Image.new("RGBA", size, (128, 128, 255, 255)).save(normal_path)
        specular = Image.new("RGBA", size, (0, 10, 0, 254))
        specular.putalpha(alpha.point(lambda a: 254 if a else 255))
        specular.save(specular_path)
    else:
        generate_pbr(str(image_path), str(output_prefix), material_type=profile)
        with Image.open(normal_path) as img:
            normal = img.copy()
        # Invisible texels are not pits. Alpha remains height, not cutout opacity.
        normal.paste((128, 128, 255, 255), mask=alpha.point(lambda a: 255 if a == 0 else 0))
        normal.save(normal_path)
    for path in (normal_path, specular_path):
        with Image.open(path) as img:
            if img.size != size or img.mode != "RGBA":
                raise ValueError(f"Invalid packed map: {path}")
            img.verify()
    return normal_path, specular_path


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("image", help="Albedo image path")
    p.add_argument("prefix", nargs="?", help="Output prefix")
    p.add_argument("--profile", choices=["stone", "wood", "metal", "ore"], default="stone")
    p.add_argument("--detail-strength", type=float, default=DEFAULT_DETAIL_STRENGTH,
                   help="Fine source relief, 0..1 (default: %(default)s); 0 disables it")
    args = p.parse_args()
    generate_pbr(args.image, args.prefix, material_type=args.profile, detail_strength=args.detail_strength)
