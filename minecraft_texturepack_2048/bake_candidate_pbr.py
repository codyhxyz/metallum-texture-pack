#!/usr/bin/env python3
"""Bake the five current static candidates without changing their albedos.

Leaves use a provisional stone-derived dielectric recipe, not a foliage/SSS model.
Fire is a flat emissive sprite, not displaced solid geometry.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from PIL import Image

from generate_pbr import generate_material_maps


CANDIDATES = {
    "oak_planks": "wood",
    "bricks": "stone",
    "iron_ore": "ore",  # Raw nuggets read metallic; host rock stays dielectric stone.
    "oak_leaves": "stone",  # Provisional dielectric maps; no SSS claim.
    "fire_0": "emissive_sprite",
}


def bake_candidate(source, profile):
    source = Path(source)
    outputs = [source.with_name(source.stem + suffix) for suffix in ("_n.png", "_s.png")]
    if any(path.exists() for path in outputs):
        raise FileExistsError(f"Existing maps for {source.name}; preserve them before rebaking")
    if source.with_suffix(".png.mcmeta").exists():
        raise ValueError("This candidate baker accepts static previews only")
    with Image.open(source) as img:
        if img.width != img.height:
            raise ValueError("Expected a square static candidate, not an animation strip")
    with tempfile.TemporaryDirectory(prefix=".candidate-pbr-", dir=source.parent) as tmp:
        prefix = Path(tmp) / source.stem
        normal_path, specular_path = generate_material_maps(source, prefix, profile)
        normal_path.replace(outputs[0])
        try:
            specular_path.replace(outputs[1])
        except OSError:
            outputs[0].unlink()  # Neither destination existed before this call.
            raise
    return outputs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True, help="Evidence JSON path outside the texture sources")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    receipt = []
    for name, profile in CANDIDATES.items():
        source = root / f"candidate-{name}.png"
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        outputs = bake_candidate(source, profile)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
        receipt.append({
            "source": str(source), "source_sha256": source_hash, "profile": profile,
            "maps": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in outputs},
        })
        print(f"Baked {name}: {profile}")
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Receipt: {args.receipt}")


if __name__ == "__main__":
    main()
