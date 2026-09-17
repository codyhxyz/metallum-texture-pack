#!/usr/bin/env python3
"""Build the four-material scan showcase; source images and ZIPs stay outside Git."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import ssl
import tempfile
import urllib.request
import zipfile

import numpy as np
from PIL import Image

PACK = Path(__file__).resolve().parents[1]
# The pack is now its own repository, so the pack root is the repo root.
ROOT = PACK
BUILD = ROOT / "build/companion-2k"
MATERIALS = {
    "stone": ("rock_boulder_dry", "minecraft:block/stone", False, 255),
    "oak_planks": ("plank_flooring", "minecraft:block/oak_planks", False, 255),
    "iron_block": ("corrugated_iron", "minecraft:block/iron_block", True, 255),
    # Authored emission, not a claim that the photographed rock emits light.
    "glowstone": ("rock_boulder_dry", "minecraft:block/glowstone", False, 192),
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_file(entry, cache, download):
    url = entry["url"]
    if not url.startswith("https://dl.polyhaven.org/file/ph-assets/Textures/png/2k/"):
        raise ValueError("Only pinned Poly Haven 2K PNG source URLs are accepted")
    size = entry["size"]
    if not 0 < size <= 32 * 1024 * 1024:
        raise ValueError("Source file exceeds the 32 MiB limit")
    path = cache / url.rsplit("/", 1)[1]
    if not path.exists():
        if not download:
            raise FileNotFoundError(f"Missing {path.name}; run with --download")
        context = ssl.create_default_context()
        # Python.org's macOS distribution can lack the system trust-store link.
        if Path("/etc/ssl/cert.pem").is_file():
            context.load_verify_locations("/etc/ssl/cert.pem")
        request = urllib.request.Request(url, headers={"User-Agent": "Metallum-Companion-Builder/1"})
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as temp:
            temporary = Path(temp.name)
            try:
                with urllib.request.urlopen(request, context=context, timeout=60) as response:
                    remaining = size
                    while chunk := response.read(min(1024 * 1024, remaining + 1)):
                        remaining -= len(chunk)
                        if remaining < 0:
                            raise ValueError(f"Oversized download: {path.name}")
                        temp.write(chunk)
                temp.flush()
                if remaining or digest(temporary) != entry["sha256"]:
                    raise ValueError(f"Source size/hash mismatch: {path.name}")
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    if path.stat().st_size != size or digest(path) != entry["sha256"]:
        raise ValueError(f"Cached source size/hash mismatch: {path.name}")
    return path


def pixels(path):
    with Image.open(path) as image:
        if image.format != "PNG" or image.size != (2048, 2048):
            raise ValueError(f"Expected a 2048-square PNG: {path}")
        if image.mode in ("I;16", "I;16B", "I;16L", "I"):
            values = np.asarray(image, dtype=np.int64)
            if values.min() < 0 or values.max() > 65535:
                raise ValueError(f"Invalid 16-bit data map: {path}")
            return ((values + 128) // 257).astype(np.uint8)
        return np.array(image.convert("RGB"), dtype=np.uint8)


def scalar(image):
    return image if image.ndim == 2 else image[:, :, 0]


def encode_maps(files, metal, emission):
    color = pixels(files["Diffuse"])
    normal = pixels(files["nor_dx"])
    ao = scalar(pixels(files["AO"]))
    height = scalar(pixels(files["Displacement"]))
    roughness = scalar(pixels(files["Rough"]))
    code = np.full(height.shape, 10, dtype=np.uint8)
    if metal:
        code[scalar(pixels(files["Metal"])) >= 128] = 230
    return {
        "albedo": np.dstack((color, np.full(height.shape, 255, dtype=np.uint8))),
        "normal": np.dstack((normal[:, :, :2], ao, height)),
        "specular": np.dstack((255 - roughness, code, np.zeros_like(code),
                               np.full(height.shape, emission, dtype=np.uint8))),
    }


def downsample(image, kind):
    """Match MaterialImages.average data rules; albedo averages in linear light."""
    h, w, channels = image.shape
    if image.dtype != np.uint8 or channels != 4 or min(h, w) < 2 or h % 2 or w % 2:
        raise ValueError("Expected even RGBA8 mip dimensions of at least two")
    p = np.stack((image[0::2, 0::2], image[0::2, 1::2],
                  image[1::2, 0::2], image[1::2, 1::2]), axis=2)
    if kind == "specular":
        p[:, :, :, 3] = np.where(p[:, :, :, 3] == 255, 0, p[:, :, :, 3])
    out = ((p.sum(axis=2, dtype=np.uint16) + 2) // 4).astype(np.uint8)
    if kind == "albedo":
        rgb = p[:, :, :, :3].astype(np.float64) / 255
        linear = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4).mean(axis=2)
        srgb = np.where(linear <= .0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - .055)
        out[:, :, :3] = np.clip(np.floor(srgb * 255 + .5), 0, 255).astype(np.uint8)
    elif kind == "normal":
        xy = p[:, :, :, :2].astype(np.float64) / 127.5 - 1
        z = np.sqrt(np.maximum(0, 1 - (xy * xy).sum(axis=3)))
        vectors = np.concatenate((xy, z[:, :, :, None]), axis=3)
        vectors /= np.linalg.norm(vectors, axis=3, keepdims=True)
        total = vectors.sum(axis=2)
        length = np.linalg.norm(total, axis=2, keepdims=True)
        unit = np.divide(total, length, out=np.zeros_like(total), where=length > 1e-9)
        out[:, :, :2] = np.clip(np.floor((unit[:, :, :2] * .5 + .5) * 255 + .5), 0, 255).astype(np.uint8)
    elif kind == "specular":
        codes = p[:, :, :, 1]
        counts = (codes[:, :, :, None] == codes[:, :, None, :]).sum(axis=3)
        modal = np.take_along_axis(codes, counts.argmax(axis=2)[:, :, None], axis=2)[:, :, 0]
        out[:, :, 1] = np.where((codes >= 230).any(axis=2), modal, out[:, :, 1])
        # PNG representation uses 255=no emission; never average that sentinel.
        out[:, :, 3] = np.where(out[:, :, 3] == 0, 255, out[:, :, 3])
    else:
        raise ValueError(f"Unknown map type: {kind}")
    return out


def json_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def write_png(path, image):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(image).save(path, compress_level=6)


def make_pack(folder, sources, files):
    entries = {}
    for name, (source, sprite, metal, emission) in MATERIALS.items():
        maps = encode_maps(files[source], metal, emission)
        levels = []
        for mip in range(12):
            paths = {}
            for kind, image in maps.items():
                relative = f"materials/{name}/{mip}/{kind}.png"
                write_png(folder / "assets/companion_2k" / relative, image)
                paths[kind] = "companion_2k:" + relative
                if mip == 4:
                    suffix = {"albedo": "", "normal": "_n", "specular": "_s"}[kind]
                    write_png(folder / f"assets/minecraft/textures/block/{name}{suffix}.png", image)
            levels.append(paths)
            if mip < 11:
                maps = {kind: downsample(image, kind) for kind, image in maps.items()}
        entries[sprite] = {**levels[0], "mips": levels}
    json_file(folder / "assets/metallum_streaming/materials.json", {"version": 1, "materials": entries})
    json_file(folder / "pack.mcmeta", {"pack": {"min_format": [88, 0], "max_format": [88, 0],
        "description": "Metallum scan showcase: 2K streamed materials / 128 fallback"}})
    json_file(folder / "sources.json", sources)
    (folder / "ATTRIBUTION.txt").write_text("Poly Haven assets: CC0-1.0\nhttps://polyhaven.com/license\n"
        "https://creativecommons.org/publicdomain/zero/1.0/legalcode\n\n" + "\n\n".join(
        key + "\n" + value["url"] + "\n" + ", ".join(f"{a}: {r}" for a, r in value["authors"].items())
        for key, value in sources["sources"].items()) + "\n\nGlowstone emission is authored, not captured.\n")
    bytes_per_material = 3 * 4 * sum((2048 >> level) ** 2 for level in range(12))
    json_file(folder / "material-budget.json", {
        "rgba8FullMipBytesPerMaterial": bytes_per_material,
        "rgba8FullSetMipBytes": bytes_per_material * len(MATERIALS),
        "worstCase3x3SectionNeighborhoodBytes": bytes_per_material * len(MATERIALS),
        "excludes": "GPU gutters/alignment, fallback atlas, metadata, decode/staging and overlapping generations",
        "fallbackSize": 128, "sourceSize": 2048,
        "transform": "DirectX XY + AO + displacement; perceptual smoothness=255-roughness; metal mask threshold128; iron230/dielectric10; emission255=none or authored192",
        "mips": "2x2 box; linear-light albedo, renormalized normals, modal metal codes, emission sentinel removed before averaging",
    })


def archive(folder, target):
    temporary = target.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as output:
            for path in sorted(p for p in folder.rglob("*") if p.is_file()):
                info = zipfile.ZipInfo(path.relative_to(folder).as_posix(), (1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                output.writestr(info, path.read_bytes())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="fetch missing pinned public CC0 PNG sources")
    parser.add_argument("--build-dir", type=Path, default=BUILD, help="ignored local build/cache directory")
    args = parser.parse_args()
    output = args.build_dir.resolve()
    # Do not write generated images into repository source or a live launcher profile.
    if output != BUILD.resolve() and BUILD.resolve() not in output.parents:
        parser.error(f"--build-dir must be {BUILD} or a child of it")
    cache = output / "sources"
    cache.mkdir(parents=True, exist_ok=True)
    sources = json.loads((PACK / "sources.json").read_text())
    files = {name: {role: source_file(entry, cache, args.download) for role, entry in value["files"].items()}
             for name, value in sources["sources"].items()}
    target = output / "companion-2k-showcase.zip"
    with tempfile.TemporaryDirectory(dir=output, prefix="pack-") as temp:
        make_pack(Path(temp), sources, files)
        archive(Path(temp), target)
    print(f"Built {target}\nSHA256 {digest(target)}")


if __name__ == "__main__":
    main()
