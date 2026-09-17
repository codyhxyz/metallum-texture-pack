#!/usr/bin/env python3
"""CPU checks for map semantics, pinned provenance and deterministic pack contents."""
import argparse
import io
import json
from pathlib import Path
import tempfile
import zipfile

import numpy as np
from PIL import Image

import build


def check():
    # Numeric alpha must not act as opacity; a transparent specular map retains R/G.
    spec = np.array([[[100, 230, 17, 255], [120, 231, 21, 254]],
                     [[140, 231, 25, 255], [160, 230, 29, 255]]], dtype=np.uint8)
    original = spec.copy()
    assert build.downsample(spec, "specular").tolist() == [[[130, 230, 23, 64]]]
    assert np.array_equal(spec, original)
    spec[:, :, 3] = 255
    assert build.downsample(spec, "specular")[0, 0, 3] == 255
    spec[:, :, 1] = [[10, 20], [30, 40]]
    assert build.downsample(spec, "specular")[0, 0, 1] == 25
    normal = np.array([[[128, 128, 0, 0], [128, 128, 100, 100]],
                       [[128, 128, 200, 200], [128, 128, 252, 252]]], dtype=np.uint8)
    assert build.downsample(normal, "normal").tolist() == [[[128, 128, 138, 138]]]
    normal[:, :, :2] = [[[255, 128], [128, 128]], [[255, 128], [128, 128]]]
    assert build.downsample(normal, "normal")[0, 0, :2].tolist() == [218, 128]
    color = np.array([[[0, 0, 0, 255], [255, 255, 255, 255]],
                      [[0, 0, 0, 255], [255, 255, 255, 255]]], dtype=np.uint8)
    assert build.downsample(color, "albedo").tolist() == [[[188, 188, 188, 255]]]
    for bad in (np.zeros((1, 1, 4), dtype=np.uint8), np.zeros((3, 3, 4), dtype=np.uint8)):
        try:
            build.downsample(bad, "normal")
            raise AssertionError("Invalid mip dimensions accepted")
        except ValueError:
            pass
    manifest = json.loads((build.PACK / "sources.json").read_text())
    assert manifest["license"] == "CC0-1.0"
    assert set(manifest["sources"]) == {material[0] for material in build.MATERIALS.values()}
    for asset in manifest["sources"].values():
        assert any("Photography" in role for role in asset["authors"].values())
        assert asset["downloadResolution"] == [2048, 2048]
        for entry in asset["files"].values():
            assert len(entry["sha256"]) == 64 and int(entry["sha256"], 16) > 0
            assert entry["url"].startswith("https://dl.polyhaven.org/")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # The 16-bit source branch must scale rather than clamp almost everything to white.
        values = np.full((2048, 2048), 32768, dtype=np.uint16)
        values[0, 0] = 65535
        path = root / "height.png"
        Image.fromarray(values).save(path)
        converted = build.pixels(path)
        assert converted[0, 0] == 255 and converted[1, 1] == 128
        folder = root / "pack"
        folder.mkdir()
        (folder / "test.txt").write_text("fixed contents\n")
        build.archive(folder, root / "one.zip")
        build.archive(folder, root / "two.zip")
        assert build.digest(root / "one.zip") == build.digest(root / "two.zip")
        corrupt = root / "bad.png"
        corrupt.write_bytes(b"bad cache")
        try:
            build.source_file({"url": "https://dl.polyhaven.org/file/ph-assets/Textures/png/2k/bad.png",
                "size": 9, "sha256": "0" * 64}, root, False)
            raise AssertionError("Corrupt cache accepted")
        except ValueError:
            pass
    print("PASS: provenance, numeric mips, normal normalization, linear albedo, 16-bit conversion, corrupt cache, deterministic ZIP")


def check_pack(path):
    with zipfile.ZipFile(path) as pack:
        names = pack.namelist()
        assert len(names) == len(set(names))
        assert all(not n.startswith("/") and ".." not in Path(n).parts for n in names)
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in pack.infolist())
        description = json.loads(pack.read("assets/metallum_streaming/materials.json"))
        assert description["version"] == 1
        assert set(description["materials"]) == {item[1] for item in build.MATERIALS.values()}
        assert json.loads(pack.read("sources.json")) == json.loads((build.PACK / "sources.json").read_text())
        meta = json.loads(pack.read("pack.mcmeta"))
        assert meta["pack"]["min_format"] == [88, 0]
        assert meta["pack"]["max_format"] == [88, 0]
        for name, (_, sprite, metal, emission) in build.MATERIALS.items():
            entry = description["materials"][sprite]
            assert len(entry["mips"]) == 12
            assert all(entry[kind] == entry["mips"][0][kind] for kind in ("albedo", "normal", "specular"))
            previous = {}
            for level, maps in enumerate(entry["mips"]):
                for kind, identifier in maps.items():
                    namespace, resource = identifier.split(":", 1)
                    assert not resource.startswith("textures/")
                    with Image.open(io.BytesIO(pack.read(f"assets/{namespace}/{resource}"))) as image:
                        assert image.mode == "RGBA" and image.size == (2048 >> level,) * 2
                        pixels = np.array(image)
                    if level:
                        assert np.array_equal(pixels, build.downsample(previous[kind], kind)), (name, kind, level)
                    previous[kind] = pixels
                    if kind == "specular":
                        assert set(np.unique(pixels[:, :, 1])) <= ({10, 230} if metal else {10})
                        assert set(np.unique(pixels[:, :, 3])) == {emission}
                    if level == 4:
                        suffix = {"albedo": "", "normal": "_n", "specular": "_s"}[kind]
                        fallback = f"assets/minecraft/textures/block/{name}{suffix}.png"
                        with Image.open(io.BytesIO(pack.read(fallback))) as image:
                            assert np.array_equal(np.array(image), pixels)
            assert any(n.startswith(f"assets/companion_2k/materials/{name}/") for n in names)
        budget = json.loads(pack.read("material-budget.json"))
        assert budget["rgba8FullSetMipBytes"] == 268435440
        assert budget["worstCase3x3SectionNeighborhoodBytes"] == budget["rgba8FullSetMipBytes"]
        print(f"PASS: all 144 mip images and 12 fallbacks, material bindings, channel codes, emission, sizes, budgets: {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, help="also validate a built pack and every material mip")
    args = parser.parse_args()
    check()
    if args.pack:
        check_pack(args.pack)
