#!/usr/bin/env python3
"""Export a static, publishable snapshot of the texture studio catalog.

Run on the machine that hosts the studio (it needs the Minecraft client JAR
and the local candidate library)::

    cd minecraft_texturepack_2048
    python3 export_studio_catalog.py --out studio-catalog --shipped textures/

Then commit studio-catalog/ and push. The hosted "Minecraft Texture Viewer"
reads the bundle with no backend: coverage summary, searchable block list,
per-block texture statuses, and model-accurate 3D previews.

Published art: keeper albedos (plus their baked _n/_s companions) are copied
out of the local library. The --shipped directory covers textures that were
released without going through the keeper flow (matched by file name, e.g.
textures/stone.png -> minecraft:block/stone). Textures with no published art
render as placeholders in the viewer; nothing is generated or approved here.
"""

import argparse
import datetime
import json
import shutil
from pathlib import Path


def slug(identifier):
    return identifier.replace(":", "_").replace("/", "_")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-jar", type=Path, default=None,
                        help="Minecraft client JAR (defaults to the studio's)")
    parser.add_argument("--library", type=Path, default=None,
                        help="Studio library dir (defaults to the studio's)")
    parser.add_argument("--out", type=Path, required=True,
                        help="Output directory for the static bundle")
    parser.add_argument("--shipped", type=Path, default=None,
                        help="Directory of released PNGs treated as published art")
    args = parser.parse_args(argv)

    # Build the studio the same way texture_studio.main does, minus the server.
    import texture_studio as studio_module
    client_jar = args.client_jar.expanduser() if args.client_jar else studio_module.CLIENT_JAR
    library = args.library.expanduser() if args.library else studio_module.LIBRARY
    studio = studio_module.Studio(client_jar, library)

    out = args.out
    tex_dir = out / "textures"
    blocks_dir = out / "blocks"
    tex_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)

    catalog = studio.catalog()
    art = {}  # texture id -> {"albedo": rel, "n": rel|None, "s": rel|None}

    def publish(texture_id, albedo_bytes, n_bytes=None, s_bytes=None):
        base = slug(texture_id)
        paths = {}
        for suffix, data in (("", albedo_bytes), ("_n", n_bytes), ("_s", s_bytes)):
            if data is None:
                paths[suffix or "albedo"] = None
                continue
            name = f"{base}{suffix}.png"
            (tex_dir / name).write_bytes(data)
            paths[suffix or "albedo"] = f"textures/{name}"
        art[texture_id] = {"albedo": paths["albedo"], "n": paths["_n"], "s": paths["_s"]}

    # 1. Keeper art from the local library.
    candidates_dir = studio.library / "candidates"
    for texture_id, info in catalog["textures"].items():
        keeper = info.get("keeper")
        if not keeper:
            continue
        record = studio.records["candidates"].get(keeper)
        if not isinstance(record, dict):
            continue
        albedo_path = candidates_dir / f"{keeper}.png"
        if not albedo_path.is_file():
            continue
        maps = record.get("maps") or {}
        n_path = candidates_dir / f"{keeper}_n.png"
        s_path = candidates_dir / f"{keeper}_s.png"
        publish(
            texture_id,
            albedo_path.read_bytes(),
            n_path.read_bytes() if "n" in maps and n_path.is_file() else None,
            s_path.read_bytes() if "s" in maps and s_path.is_file() else None,
        )

    # 2. Released PNGs (the --shipped dir) fill gaps the keeper flow missed.
    if args.shipped and args.shipped.is_dir():
        by_name = {}
        for texture_id in catalog["textures"]:
            by_name.setdefault(texture_id.split("/")[-1] + ".png", texture_id)
        for png in sorted(args.shipped.glob("*.png")):
            if png.name.endswith(("_n.png", "_s.png")):
                continue
            texture_id = by_name.get(png.name)
            if texture_id is None or texture_id in art:
                continue
            n_path = png.with_name(png.stem + "_n.png")
            s_path = png.with_name(png.stem + "_s.png")
            publish(
                texture_id,
                png.read_bytes(),
                n_path.read_bytes() if n_path.is_file() else None,
                s_path.read_bytes() if s_path.is_file() else None,
            )

    # 3. catalog.json — summary, block list, texture statuses + published art.
    static_textures = {}
    for texture_id, info in catalog["textures"].items():
        static_textures[texture_id] = {
            "status": info["status"],
            "width": info["width"],
            "height": info["height"],
            "animated": info["animated"],
            "art": art.get(texture_id),
        }
    static_blocks = [
        {k: block[k] for k in ("id", "textures", "issues", "status")}
        for block in catalog["blocks"]
    ]
    (out / "catalog.json").write_text(json.dumps({
        "version": catalog["version"],
        "source": catalog["source"],
        "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "summary": catalog["summary"],
        "blocks": static_blocks,
        "textures": static_textures,
    }, indent=1, sort_keys=True) + "\n")

    # 4. Per-block detail: blockstate + resolved models, same shape as /api/block.
    for block_id, detail in studio.details.items():
        payload = {k: detail[k] for k in ("id", "textures", "issues", "blockstate", "models")}
        payload["status"] = next(b["status"] for b in catalog["blocks"] if b["id"] == block_id)
        (blocks_dir / f"{slug(block_id)}.json").write_text(
            json.dumps(payload, sort_keys=True) + "\n")

    published = sum(1 for a in art.values() if a["albedo"])
    print(f"Exported {len(static_blocks)} blocks, {published} published textures -> {out}")
    print(f"Summary: {catalog['summary']['complete']}/{catalog['summary']['trackable']} "
          f"complete ({catalog['summary']['percent']}%)")


if __name__ == "__main__":
    main()
