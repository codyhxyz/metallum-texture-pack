#!/usr/bin/env python3
"""Local, explicit-choice texture catalogue. Nothing generates until POST /api/generate."""
import argparse
import base64
import copy
import hashlib
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets
import tempfile
import threading
from urllib.parse import parse_qs, quote, urlsplit
import uuid
import zipfile

from PIL import Image

CLIENT_JAR = Path.home() / "Library/Application Support/ModrinthApp/meta/versions/26.2-0.19.5/26.2-0.19.5.jar"
LIBRARY = Path.home() / ".pi/agent/workspaces/metallum-texture-studio/library"
MAX_BODY = 64 * 1024 * 1024
MAX_PIXELS = 64 * 1024 * 1024
STATIC = {"texture-studio.html", "texture-studio.mjs", "minecraft-model.mjs", "labpbr-viewer-shaders.mjs"}
RESOURCE = re.compile(r"[a-z0-9_.-]+:[a-z0-9_./-]+\Z")


def resource(value):
    if not isinstance(value, str):
        raise ValueError("Invalid resource ID")
    value = value if ":" in value else "minecraft:" + value
    if not RESOURCE.fullmatch(value) or any(p in ("", ".", "..") for p in value.split(":", 1)[1].split("/")):
        raise ValueError("Invalid resource ID")
    return value


def asset(identifier, kind, suffix):
    namespace, name = resource(identifier).split(":", 1)
    return f"assets/{namespace}/{kind}/{name}{suffix}"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def png_size(data):
    if not data or len(data) > MAX_BODY:
        raise ValueError("Empty or oversized PNG")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or max(image.size) > 65536 or image.width * image.height > MAX_PIXELS:
                raise ValueError("Expected a bounded PNG (64M pixels, 65536 per dimension)")
            size = image.size
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
        return size
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid PNG") from exc


def animation_layout(size, metadata):
    """Normalize Minecraft frame geometry and playback, including grid animations."""
    animation = metadata.get("animation")
    if not isinstance(animation, dict):
        raise ValueError("Animation metadata must be an object")
    width, height = size
    fw = animation.get("width", width if "height" in animation else min(size))
    fh = animation.get("height", height if "width" in animation else min(size))
    if any(type(n) is not int or n <= 0 for n in (fw, fh)) or width % fw or height % fh:
        raise ValueError("Invalid animation frame dimensions")
    columns, rows = width // fw, height // fh
    timing = animation.get("frametime", 1)
    if type(timing) is not int or timing <= 0:
        raise ValueError("Invalid frame time")
    frames = animation.get("frames", list(range(columns * rows)))
    if not isinstance(frames, list):
        raise ValueError("Invalid frame list")
    if not frames:
        frames = list(range(columns * rows))
    playback = []
    for frame in frames:
        index, duration = (frame.get("index"), frame.get("time", timing)) if isinstance(frame, dict) else (frame, timing)
        if type(index) is not int or not 0 <= index < columns * rows or type(duration) is not int or duration <= 0:
            raise ValueError("Invalid animation frame")
        playback.append((index, duration))
    interpolate = animation.get("interpolate", False)
    if type(interpolate) is not bool:
        raise ValueError("Invalid interpolation flag")
    return columns, rows, fw, fh, playback, interpolate


class Studio:
    def __init__(self, client_jar, library):
        self.client_jar = Path(client_jar)
        self.library = Path(library)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.generation_lock = threading.Lock()
        self.details = {}
        self.textures = {}
        self.source_data = {}
        self.source_meta = {}
        self._index()
        self.library.mkdir(parents=True, exist_ok=True)
        self.records = {"candidates": {}, "keepers": {}}
        state = self.library / "library.json"
        if state.exists():
            self.records = json.loads(state.read_text())
            if not isinstance(self.records, dict) or not all(isinstance(self.records.get(k), dict) for k in ("candidates", "keepers")):
                raise ValueError("Invalid library.json; restore library.json.bak before continuing")

    def _index(self):
        with zipfile.ZipFile(self.client_jar) as jar:
            names = set(jar.namelist())
            self.version = json.loads(jar.read("version.json")).get("id", "unknown") if "version.json" in names else "unknown"
            cache = {}

            def resolve(identifier, trail=()):
                if identifier in trail:
                    return {"textures": {}, "elements": [], "issues": [f"Model cycle: {identifier}"]}
                if identifier in cache:
                    return copy.deepcopy(cache[identifier])
                result = {"textures": {}, "elements": [], "issues": []}
                try:
                    raw = json.loads(jar.read(asset(identifier, "models", ".json")))
                    if "parent" in raw:
                        parent = resource(raw["parent"])
                        result = resolve(parent, trail + (identifier,))
                    result["textures"].update(raw.get("textures", {}))
                    if "elements" in raw:
                        result["elements"] = raw["elements"]
                    if not isinstance(result["elements"], list):
                        raise ValueError("Invalid elements")
                except (KeyError, ValueError, TypeError, AttributeError):
                    result["issues"].append(f"Missing, builtin, or invalid model: {identifier}")
                cache[identifier] = copy.deepcopy(result)
                return result

            def model_view(identifier):
                resolved = resolve(identifier)
                issues = list(resolved["issues"])
                elements = copy.deepcopy(resolved["elements"])
                used = set()
                try:
                    for element in elements:
                        for face in element.get("faces", {}).values():
                            binding = face["texture"]
                            # Modern model faces can reference a slot without the # prefix.
                            if isinstance(binding, str) and not binding.startswith("#") and binding in resolved["textures"]:
                                binding = "#" + binding
                            seen = set()
                            while isinstance(binding, str) and binding.startswith("#"):
                                if binding in seen:
                                    raise ValueError("Texture alias cycle")
                                seen.add(binding)
                                binding = resolved["textures"][binding[1:]]
                            # 26.2 material objects carry the sprite plus rendering flags.
                            if isinstance(binding, dict):
                                face["translucent"] = binding.get("force_translucent", False) is True
                                binding = binding["sprite"]
                            face["texture"] = resource(binding)
                            used.add(face["texture"])
                except (KeyError, ValueError, TypeError, AttributeError):
                    issues.append(f"Missing or cyclic face texture: {identifier}")
                if not used:
                    issues.append(f"No face geometry: {identifier}")
                return {"elements": elements, "issues": sorted(set(issues))}, used

            for path in sorted(names):
                match = re.fullmatch(r"assets/([^/]+)/blockstates/(.+)\.json", path)
                if not match:
                    continue
                identifier = resource(f"{match[1]}:{match[2]}")
                issues, models, textures = [], {}, set()
                raw = {}
                roots = set()
                try:
                    raw = json.loads(jar.read(path))
                    entries = list(raw.get("variants", {}).values())
                    entries.extend(part["apply"] for part in raw.get("multipart", []))
                    for entry in entries:
                        for variant in entry if isinstance(entry, list) else [entry]:
                            roots.add(resource(variant["model"]))
                    if not roots:
                        issues.append("No blockstate models")
                except (ValueError, KeyError, TypeError, AttributeError):
                    issues.append("Invalid blockstate")
                for root in sorted(roots):
                    view, used = model_view(root)
                    models[root] = view
                    textures.update(used)
                    issues.extend(view["issues"])
                # Include parent detail for inspection, but never count its pre-override bindings.
                pending = list(roots)
                while pending:
                    current = pending.pop()
                    try:
                        model = json.loads(jar.read(asset(current, "models", ".json")))
                        if "parent" in model:
                            parent = resource(model["parent"])
                            if parent not in models:
                                models[parent] = model_view(parent)[0]
                                pending.append(parent)
                    except (ValueError, KeyError, TypeError, AttributeError):
                        pass
                for texture in sorted(textures):
                    if texture not in self.textures:
                        info = {"id": texture, "sourceUrl": "/api/source?texture=" + quote(texture, safe=""),
                                "width": None, "height": None, "animated": False}
                        try:
                            source_path = asset(texture, "textures", ".png")
                            data = jar.read(source_path)
                            info["width"], info["height"] = png_size(data)
                            metadata = json.loads(jar.read(source_path + ".mcmeta")) if source_path + ".mcmeta" in names else {}
                            if not isinstance(metadata, dict):
                                raise ValueError("Invalid metadata")
                            info["animated"] = "animation" in metadata
                            info["animation"] = metadata.get("animation")
                            if info["animated"]:
                                animation_layout((info["width"], info["height"]), metadata)
                            self.source_data[texture] = data
                            self.source_meta[texture] = metadata
                        except (KeyError, ValueError, OSError, Image.DecompressionBombError):
                            pass
                        self.textures[texture] = info
                    if texture not in self.source_data:
                        issues.append(f"Missing or invalid source texture: {texture}")
                self.details[identifier] = {"id": identifier, "textures": sorted(textures),
                                            "issues": sorted(set(issues)), "blockstate": raw, "models": models}

    def _save(self):
        destination = self.library / "library.json"
        data = (json.dumps(self.records, indent=2) + "\n").encode()
        # Same-directory renames keep each publication atomic; retain the preceding state.
        for path, payload in ((self.library / "library.json.bak", destination.read_bytes() if destination.exists() else None),
                              (destination, data)):
            if payload is None:
                continue
            with tempfile.NamedTemporaryFile(dir=self.library, delete=False) as stream:
                temp = Path(stream.name)
                try:
                    stream.write(payload)
                    stream.flush()
                    import os
                    os.fsync(stream.fileno())
                except BaseException:
                    temp.unlink(missing_ok=True)
                    raise
            try:
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)

    def _candidate_bytes(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ValueError("Unknown candidate")
        record = self.records["candidates"].get(identifier)
        if not isinstance(record, dict):
            raise ValueError("Unknown candidate")
        path = self.library / "candidates" / (identifier + ".png")
        if path.stat().st_size > MAX_BODY:
            raise ValueError("Oversized candidate")
        data = path.read_bytes()
        if digest(data) != record["sha256"]:
            raise ValueError("Candidate changed")
        meta_path = path.with_suffix(".png.mcmeta")
        meta_bytes = meta_path.read_bytes() if meta_path.exists() and meta_path.stat().st_size <= MAX_BODY else None
        if meta_path.exists() and meta_bytes is None:
            raise ValueError("Oversized metadata")
        if (digest(meta_bytes) if meta_bytes is not None else None) != record.get("metadata_sha256"):
            raise ValueError("Candidate metadata changed")
        metadata = json.loads(meta_bytes) if meta_bytes is not None else {}
        if not isinstance(metadata, dict):
            raise ValueError("Invalid candidate metadata")
        return record, data, metadata

    def _eligibility(self, texture, size, metadata):
        source = self.textures[texture]
        if texture not in self.source_data:
            return False, "Source unavailable"
        if source["animated"]:
            try:
                expected = animation_layout((source["width"], source["height"]), self.source_meta[texture])
                actual = animation_layout(size, metadata)
                if (expected[:2] != actual[:2] or expected[2] * actual[3] != actual[2] * expected[3]
                        or expected[4:] != actual[4:]):
                    return False, "Animation layout or playback differs from source"
            except (ValueError, TypeError):
                return False, "Full frame layout and matching animation metadata required"
        elif "animation" in metadata:
            return False, "Source is not animated"
        elif size[0] * source["height"] != size[1] * source["width"]:
            return False, "Image aspect ratio differs from source"
        return True, None

    def candidate(self, identifier):
        record, data, metadata = self._candidate_bytes(identifier)
        size = png_size(data)
        if "animation" in metadata:
            animation_layout(size, metadata)
        eligible, reason = self._eligibility(record["texture"], size, metadata)
        if record.get("source_sha256") not in (None, digest(self.source_data.get(record["texture"], b""))):
            eligible, reason = False, "Minecraft source changed; import again to review against this version"
        result = {"id": identifier, "name": record["name"], "url": "/api/candidate?id=" + identifier,
                  "eligible": eligible, "reason": reason, "width": size[0], "height": size[1],
                  "animation": metadata.get("animation")}
        for channel, expected_hash in record.get("maps", {}).items():
            path = self.library / "candidates" / f"{identifier}_{channel}.png"
            if path.is_file() and path.stat().st_size <= MAX_BODY and digest(path.read_bytes()) == expected_hash:
                result[f"{channel}Url"] = f"/api/candidate?id={identifier}&map={channel}"
        if "recipe" in record:
            result["recipe"] = record["recipe"]
        return result

    def catalog(self):
        with self.lock:
            textures = copy.deepcopy(self.textures)
            for info in textures.values():
                info.update(candidates=[], keeper=None, status="missing")
            for identifier, record in self.records["candidates"].items():
                try:
                    candidate = self.candidate(identifier)
                    info = textures[record["texture"]]
                    info["candidates"].append(candidate)
                    if candidate["eligible"] and self.records["keepers"].get(record["texture"]) == identifier:
                        info["keeper"] = identifier
                except (ValueError, OSError, KeyError, TypeError, Image.DecompressionBombError):
                    continue
            for info in textures.values():
                info["status"] = "complete" if info["keeper"] else "partial" if info["candidates"] else "missing"
            blocks = []
            summary = dict(total=len(self.details), trackable=0, complete=0, partial=0, missing=0, excluded=0, percent=0)
            for detail in self.details.values():
                states = [textures[t]["status"] for t in detail["textures"]]
                status = ("excluded" if detail["issues"] or not states else "complete" if all(s == "complete" for s in states)
                          else "partial" if any(s != "missing" for s in states) else "missing")
                blocks.append({k: detail[k] for k in ("id", "textures", "issues")} | {"status": status})
                summary[status] += 1
            summary["trackable"] = summary["total"] - summary["excluded"]
            if summary["trackable"]:
                summary["percent"] = round(100 * summary["complete"] / summary["trackable"], 2)
            return {"version": self.version, "source": self.client_jar.name, "token": self.token,
                    "summary": summary, "blocks": blocks, "textures": textures}

    def import_png(self, texture, name, data, metadata=None, recipe=None, maps=None):
        if texture not in self.textures:
            raise ValueError("Unknown texture")
        if not isinstance(name, str) or not name.strip() or len(name) > 256 or "/" in name or "\\" in name:
            raise ValueError("Expected a short display name, not a path")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("mcmeta must be an object or null")
        size = png_size(data)
        if metadata is not None and "animation" in metadata:
            animation_layout(size, metadata)
        for channel, pixels in (maps or {}).items():
            if channel not in ("n", "s") or png_size(pixels) != size:
                raise ValueError("Companion maps must match the albedo dimensions")
        meta_bytes = (json.dumps(metadata, allow_nan=False, sort_keys=True) + "\n").encode() if metadata is not None else None
        if meta_bytes is not None and len(meta_bytes) > MAX_BODY:
            raise ValueError("Oversized metadata")
        identifier = uuid.uuid4().hex
        with self.lock:
            directory = self.library / "candidates"
            directory.mkdir(exist_ok=True)
            path = directory / (identifier + ".png")
            path.write_bytes(data)
            if meta_bytes is not None:
                path.with_suffix(".png.mcmeta").write_bytes(meta_bytes)
            record = {"texture": texture, "name": name, "sha256": digest(data),
                      "metadata_sha256": digest(meta_bytes) if meta_bytes is not None else None,
                      "source_version": self.version, "source_sha256": digest(self.source_data.get(texture, b""))}
            for channel, pixels in (maps or {}).items():
                companion = directory / f"{identifier}_{channel}.png"
                companion.write_bytes(pixels)
                if meta_bytes is not None:
                    companion.with_suffix(".png.mcmeta").write_bytes(meta_bytes)
                record.setdefault("maps", {})[channel] = digest(pixels)
            if recipe is not None:
                record["recipe"] = recipe
            self.records["candidates"][identifier] = record
            try:
                self._save()
            except BaseException:
                del self.records["candidates"][identifier]
                path.unlink(missing_ok=True)
                path.with_suffix(".png.mcmeta").unlink(missing_ok=True)
                for channel in maps or {}:
                    companion = directory / f"{identifier}_{channel}.png"
                    companion.unlink(missing_ok=True)
                    companion.with_suffix(".png.mcmeta").unlink(missing_ok=True)
                raise
            return self.candidate(identifier)

    def set_keeper(self, texture, identifier):
        with self.lock:
            if texture not in self.textures:
                raise ValueError("Unknown texture")
            if identifier is not None:
                candidate = self.candidate(identifier)
                if self.records["candidates"][identifier]["texture"] != texture or not candidate["eligible"]:
                    raise ValueError("Candidate does not belong to texture or is ineligible")
            previous = self.records["keepers"].copy()
            self.records["keepers"][texture] = identifier
            try:
                self._save()
            except BaseException:
                self.records["keepers"] = previous
                raise
            return {"texture": texture, "keeper": identifier}

    def generate(self, request):
        texture = request.get("texture")
        if not isinstance(texture, str) or texture not in self.source_data:
            raise ValueError("Unknown or unavailable texture")
        if request.get("prompt") is not None and not isinstance(request["prompt"], str):
            raise ValueError("prompt must be text")
        if request.get("seed") is not None and type(request["seed"]) is not int:
            raise ValueError("seed must be an integer")
        if "grayscale" in request and type(request["grayscale"]) is not bool:
            raise ValueError("grayscale must be true or false")
        if request.get("frame") is not None and (type(request["frame"]) is not int or request["frame"] < 0):
            raise ValueError("frame must be a nonnegative integer")
        if not self.generation_lock.acquire(blocking=False):
            raise BlockingIOError("Generation already in progress")
        try:
            from generate_vanilla_albedo import MATERIAL_PRESETS, generate_albedo
            material = request.get("material")
            if not isinstance(material, str) or material not in MATERIAL_PRESETS:
                raise ValueError("Unknown material")
            recipe = {key: request[key] for key in ("material", "prompt", "seed", "frame", "grayscale") if request.get(key) is not None}
            recipe["animate"] = self.textures[texture]["animated"] and request.get("frame") is None
            recipe.setdefault("seed", 42)
            with tempfile.TemporaryDirectory(prefix="texture-studio-") as temp:
                source = Path(temp) / (texture.split("/")[-1].split(":")[-1] + ".png")
                source.write_bytes(self.source_data[texture])
                if self.source_meta[texture]:
                    source.with_suffix(".png.mcmeta").write_text(json.dumps(self.source_meta[texture]))
                output = Path(temp) / (uuid.uuid4().hex + ".png")
                generate_albedo(source, output, client_jar=self.client_jar, **recipe)
                meta_path = output.with_suffix(".png.mcmeta")
                metadata = json.loads(meta_path.read_text()) if meta_path.exists() else None
                maps = {channel: path.read_bytes() for channel in ("n", "s")
                        if (path := output.with_name(output.stem + f"_{channel}.png")).is_file()}
                return self.import_png(texture, texture.split("/")[-1] + " · " + material, output.read_bytes(), metadata, recipe, maps)
        finally:
            self.generation_lock.release()


def make_server(studio, port=8766, static_dir=None):
    static_dir = Path(static_dir) if static_dir is not None else Path(__file__).parent

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, content_type="application/json"):
            if not isinstance(body, bytes):
                body = json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def allowed(self, write=False):
            port = self.server.server_port
            hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
            if len(self.headers.get_all("Host", [])) != 1 or self.headers.get("Host") not in hosts:
                self.send(403, {"error": "Invalid Host"})
                return False
            if write:
                origins = self.headers.get_all("Origin", [])
                tokens = self.headers.get_all("X-Studio-Token", [])
                if (len(origins) > 1 or (origins and origins[0] != "http://" + self.headers["Host"])
                        or self.headers.get("Sec-Fetch-Site") == "cross-site"
                        or len(tokens) != 1 or not tokens[0].isascii() or not secrets.compare_digest(tokens[0], studio.token)):
                    self.send(403, {"error": "Invalid origin or token"})
                    return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            try:
                if parsed.path == "/api/catalog":
                    return self.send(200, studio.catalog())
                if parsed.path == "/api/block":
                    return self.send(200, studio.details[query["id"][0]])
                if parsed.path == "/api/source":
                    return self.send(200, studio.source_data[query["texture"][0]], "image/png")
                if parsed.path == "/api/candidate":
                    with studio.lock:
                        identifier = query["id"][0]
                        candidate = studio.candidate(identifier)
                        channel = query.get("map", [None])[0]
                        if channel is not None:
                            if channel not in ("n", "s") or f"{channel}Url" not in candidate:
                                raise ValueError("Missing or modified companion map")
                            data = (studio.library / "candidates" / f"{identifier}_{channel}.png").read_bytes()
                        else:
                            data = studio._candidate_bytes(identifier)[1]
                    return self.send(200, data, "image/png")
                name = "texture-studio.html" if parsed.path == "/" else parsed.path.removeprefix("/")
                if name in STATIC:
                    return self.send(200, (static_dir / name).read_bytes(), "text/html; charset=utf-8" if name.endswith(".html") else "text/javascript; charset=utf-8")
            except (KeyError, ValueError, OSError, TypeError, Image.DecompressionBombError):
                pass
            self.send(404, {"error": "Not found or no longer intact"})

        def do_POST(self):
            if not self.allowed(write=True):
                return
            if self.path not in ("/api/import", "/api/keeper", "/api/generate"):
                return self.send(404, {"error": "Not found"})
            try:
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
                    return self.send(400, {"error": "Content-Length required; chunked bodies unsupported"})
                length = int(lengths[0])
                if not 0 < length <= MAX_BODY:
                    return self.send(413, {"error": "Request exceeds 64 MiB or is empty"})
                if self.headers.get_content_type() != "application/json":
                    return self.send(415, {"error": "JSON required"})
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise ValueError("Expected an object")
                if self.path == "/api/import":
                    result = studio.import_png(request["texture"], request["name"], base64.b64decode(request["png"], validate=True), request.get("mcmeta"))
                elif self.path == "/api/keeper":
                    result = studio.set_keeper(request["texture"], request["candidate"])
                else:
                    result = studio.generate(request)
                self.send(200, result)
            except BlockingIOError:
                self.send(409, {"error": "Generation already in progress"})
            except (ValueError, KeyError, TypeError, Image.DecompressionBombError):
                self.send(400, {"error": "Invalid request, PNG, metadata, or candidate"})
            except Exception:
                # Do not leak local paths, generator stderr, or library internals to the API.
                self.send(500, {"error": "Operation failed; check local dependencies and library permissions"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-jar", type=Path, default=CLIENT_JAR)
    parser.add_argument("--library", type=Path, default=LIBRARY)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--import", dest="imports", action="append", default=[], metavar="TEXTURE_ID=PNG_PATH")
    parser.add_argument("--index-only", action="store_true")
    args = parser.parse_args(argv)
    studio = Studio(args.client_jar.expanduser(), args.library.expanduser())
    for item in args.imports:
        texture, filename = item.split("=", 1)
        path = Path(filename).expanduser()
        if path.stat().st_size > MAX_BODY:
            raise ValueError("Oversized PNG")
        sidecar = path.with_suffix(path.suffix + ".mcmeta")
        metadata = json.loads(sidecar.read_text()) if sidecar.exists() else None
        maps = {channel: companion.read_bytes() for channel in ("n", "s")
                if (companion := path.with_name(path.stem + f"_{channel}.png")).is_file()}
        studio.import_png(resource(texture), path.name, path.read_bytes(), metadata, maps=maps)
    if args.index_only:
        print(json.dumps({"version": studio.version, "source": studio.client_jar.name, "summary": studio.catalog()["summary"]}, indent=2))
        return
    with make_server(studio, args.port) as server:
        print(f"Texture Studio: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
