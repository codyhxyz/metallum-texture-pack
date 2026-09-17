#!/usr/bin/env python3
"""Run directly: python3 test_texture_studio.py (no inference or live profile access)."""
import base64
import copy
from http.client import HTTPConnection
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import zipfile

from PIL import Image

from texture_studio import MAX_BODY, Studio, animation_layout, make_server


def png(size=(4, 4), color="red"):
    output = io.BytesIO()
    Image.new("RGBA", size, color).save(output, format="PNG")
    return output.getvalue()


def rejects(call):
    try:
        call()
    except (ValueError, OSError, KeyError, TypeError):
        return
    raise AssertionError("Invalid input was accepted")


def synthetic_jar(path):
    element = {"from": [0, 0, 0], "to": [16, 16, 16],
               "rotation": {"origin": [8, 8, 8], "axis": "y", "angle": 22.5},
               "faces": {"up": {"texture": "#end", "uv": [1, 2, 15, 14], "rotation": 90, "tintindex": 0},
                         "north": {"texture": "#side"}}}
    models = {
        "log_base": {"textures": {"end": "#top", "top": "minecraft:block/old_end", "side": "minecraft:block/old_side",
                                   "particle": "minecraft:block/particle_only"}, "elements": [element]},
        "oak": {"parent": "minecraft:block/log_base", "textures": {"top": "minecraft:block/oak_end", "side": "minecraft:block/oak_side"}},
        "shared": {"parent": "minecraft:block/oak",
                   "textures": {"side": {"sprite": "minecraft:block/oak_side", "force_translucent": True}},
                   "elements": [dict(element, faces={"up": {"texture": "end"}, "north": {"texture": "side"}})]},
        "one": {"textures": {"all": "minecraft:block/extra"}, "elements": [{"faces": {"up": {"texture": "#all"}}}]},
        "fire": {"textures": {"all": "minecraft:block/fire"}, "elements": [{"faces": {"north": {"texture": "#all"}}}]},
        "missing_parent": {"parent": "minecraft:block/absent"},
        "cycle_a": {"parent": "minecraft:block/cycle_b"}, "cycle_b": {"parent": "minecraft:block/cycle_a"},
        "alias_cycle": {"textures": {"a": "#b", "b": "#a"}, "elements": [{"faces": {"up": {"texture": "#a"}}}]},
        "builtin": {"parent": "minecraft:builtin/entity"},
        "empty": {"textures": {"particle": "minecraft:block/extra"}},
        "missing_source": {"elements": [{"faces": {"up": {"texture": "minecraft:block/absent_png"}}}]},
        "bad_source": {"elements": [{"faces": {"up": {"texture": "minecraft:block/bad_png"}}}]},
    }
    states = {name: {"variants": {"": {"model": "minecraft:block/" + name}}} for name in
              ("oak", "shared", "fire", "missing_parent", "cycle_a", "alias_cycle", "builtin", "empty", "missing_source", "bad_source")}
    states["union"] = {"variants": {"axis=y": [{"model": "minecraft:block/oak", "weight": 2},
                                               {"model": "minecraft:block/one", "x": 90, "y": 180, "uvlock": True}]},
                       "multipart": [{"when": {"powered": "true"}, "apply": [{"model": "minecraft:block/fire"}]}]}
    metadata = {"animation": {"frametime": 2, "interpolate": True, "frames": [0, {"index": 2, "time": 4}, 1]}}
    with zipfile.ZipFile(path, "w") as jar:
        jar.writestr("version.json", json.dumps({"id": "synthetic-1"}))
        for name, model in models.items():
            jar.writestr(f"assets/minecraft/models/block/{name}.json", json.dumps(model))
        for name, state in states.items():
            jar.writestr(f"assets/minecraft/blockstates/{name}.json", json.dumps(state))
        for name in ("oak_end", "oak_side", "old_end", "old_side", "extra"):
            jar.writestr(f"assets/minecraft/textures/block/{name}.png", png())
        jar.writestr("assets/minecraft/textures/block/fire.png", png((4, 12)))
        jar.writestr("assets/minecraft/textures/block/fire.png.mcmeta", json.dumps(metadata))
        jar.writestr("assets/minecraft/textures/block/bad_png.png", b"not a PNG")
        jar.writestr("assets/custom/blockstates/example.json", json.dumps({"variants": {"": {"model": "minecraft:block/one"}}}))
    return metadata


def run():
    assert "generate_vanilla_albedo" not in sys.modules, "Generator must remain lazy"
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp)
        jar, library = directory / "synthetic.jar", directory / "library"
        metadata = synthetic_jar(jar)
        original_jar = jar.read_bytes()
        studio = Studio(jar, library)
        catalog = studio.catalog()
        assert catalog["version"] == "synthetic-1"
        assert catalog["source"] == "synthetic.jar"
        assert catalog["summary"] == dict(total=12, trackable=5, complete=0, partial=0, missing=5, excluded=7, percent=0)
        block = studio.details["minecraft:oak"]
        assert block["textures"] == ["minecraft:block/oak_end", "minecraft:block/oak_side"]
        assert "minecraft:block/old_end" not in studio.textures
        assert "minecraft:block/particle_only" not in studio.textures
        shared_face = studio.details["minecraft:shared"]["models"]["minecraft:block/shared"]["elements"][0]["faces"]["north"]
        assert shared_face["texture"] == "minecraft:block/oak_side" and shared_face["translucent"]
        assert {"minecraft:block/log_base", "minecraft:block/oak"} <= block["models"].keys()
        element = block["models"]["minecraft:block/oak"]["elements"][0]
        assert element["rotation"]["angle"] == 22.5
        assert element["faces"]["up"] == {"texture": "minecraft:block/oak_end", "uv": [1, 2, 15, 14], "rotation": 90, "tintindex": 0}
        union = studio.details["minecraft:union"]
        assert set(union["textures"]) == {"minecraft:block/oak_end", "minecraft:block/oak_side", "minecraft:block/extra", "minecraft:block/fire"}
        assert union["blockstate"]["variants"]["axis=y"][1]["uvlock"] is True
        assert all(b["issues"] for b in catalog["blocks"] if b["status"] == "excluded")

        def states():
            return {b["id"]: b["status"] for b in studio.catalog()["blocks"]}

        end, side, fire = "minecraft:block/oak_end", "minecraft:block/oak_side", "minecraft:block/fire"
        rejects(lambda: studio.import_png("minecraft:block/not_known", "bad", png()))
        rejects(lambda: studio.import_png(end, "bad", b""))
        rejects(lambda: studio.import_png(end, "bad", b"not PNG"))
        rejects(lambda: studio.import_png(end, "bad", png()[:-10]))
        rejects(lambda: studio.import_png(end, "too wide", png((65537, 1))))
        rejects(lambda: studio.import_png(end, "../bad", png()))
        rejects(lambda: studio.import_png(end, "bad", png(), []))
        for invalid_animation in (1, {"frames": "bad"}, {"frames": [100]}, {"frametime": 0}):
            rejects(lambda: studio.import_png(end, "bad metadata", png(), {"animation": invalid_animation}))
        assert not studio._eligibility(end, (8, 4), {})[0]
        candidate_end = studio.import_png(end, "end.png", png(), maps={"n": png(color="blue")})
        assert candidate_end["width"] == 4 and candidate_end["animation"] is None
        assert candidate_end["eligible"] and studio.catalog()["textures"][end]["keeper"] is None
        assert states()["minecraft:oak"] == states()["minecraft:shared"] == "partial"
        assert studio.catalog()["summary"]["percent"] == 0
        rejects(lambda: studio.set_keeper(side, candidate_end["id"]))
        studio.set_keeper(end, candidate_end["id"])
        candidate_side = studio.import_png(side, "side.png", png(color="blue"), {"texture": {"blur": False}})
        studio.set_keeper(side, candidate_side["id"])
        assert states()["minecraft:oak"] == states()["minecraft:shared"] == "complete"
        assert studio.catalog()["summary"]["percent"] == 40
        source_bytes = studio.source_data[end]
        studio.source_data[end] = png(color="green")
        assert not studio.candidate(candidate_end["id"])["eligible"]
        assert states()["minecraft:oak"] == "partial"
        studio.source_data[end] = source_bytes
        assert (library / "library.json.bak").is_file()
        studio = Studio(jar, library)
        assert states()["minecraft:oak"] == "complete"
        studio.set_keeper(end, None)
        assert states()["minecraft:oak"] == states()["minecraft:shared"] == "partial"
        studio = Studio(jar, library)
        assert studio.catalog()["textures"][end]["keeper"] is None
        studio.set_keeper(end, candidate_end["id"])
        candidate_path = library / "candidates" / (candidate_end["id"] + ".png")
        valid_bytes = candidate_path.read_bytes()
        for changed in (b"", b"broken", png(color="green")):
            candidate_path.write_bytes(changed)
            assert studio.catalog()["textures"][end]["status"] == "missing"
            rejects(lambda: studio.set_keeper(end, candidate_end["id"]))
        candidate_path.unlink()
        assert studio.catalog()["textures"][end]["keeper"] is None
        candidate_path.write_bytes(valid_bytes)
        assert studio.catalog()["textures"][end]["status"] == "complete"
        # An unexpected, changed, or deleted sidecar invalidates the identity pair.
        meta_path = candidate_path.with_suffix(".png.mcmeta")
        meta_path.write_text("{}")
        assert studio.catalog()["textures"][end]["status"] == "missing"
        meta_path.unlink()
        side_meta = library / "candidates" / (candidate_side["id"] + ".png.mcmeta")
        side_meta_bytes = side_meta.read_bytes()
        side_meta.write_text('{"texture":{"blur":true}}')
        assert studio.catalog()["textures"][side]["status"] == "missing"
        side_meta.unlink()
        assert studio.catalog()["textures"][side]["status"] == "missing"
        side_meta.write_bytes(side_meta_bytes)
        assert states()["minecraft:oak"] == "complete"

        preview = studio.import_png(fire, "preview", png((8, 8)))
        assert not preview["eligible"] and states()["minecraft:fire"] == "partial"
        rejects(lambda: studio.set_keeper(fire, preview["id"]))
        assert not studio.import_png(fire, "no metadata", png((8, 24)))["eligible"]
        wrong = copy.deepcopy(metadata)
        wrong["animation"]["frametime"] = 3
        assert not studio.import_png(fire, "wrong playback", png((8, 24)), wrong)["eligible"]
        rejects(lambda: studio.import_png(fire, "wrong count", png((8, 16)), metadata))
        scaled_meta = copy.deepcopy(metadata)
        scaled_meta["animation"].update(width=8, height=8)
        full = studio.import_png(fire, "full", png((8, 24)), scaled_meta)
        assert full["eligible"]
        studio.set_keeper(fire, full["id"])
        assert states()["minecraft:fire"] == "complete"
        assert animation_layout((8, 8), {"animation": {"width": 4, "height": 4}})[:2] == (2, 2)
        assert states()["minecraft:missing_source"] == "excluded"

        # Exercise HTTP boundaries and explicit generation with a fake callable, never inference.
        server = make_server(studio, 0, directory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port

        def http(method, path, payload=None, headers=None, raw=None):
            connection = HTTPConnection("127.0.0.1", port, timeout=10)
            body = raw if raw is not None else json.dumps(payload) if payload is not None else None
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            data, status = response.read(), response.status
            assert response.getheader("Access-Control-Allow-Origin") is None
            assert response.getheader("X-Frame-Options") == "DENY"
            assert response.getheader("Content-Security-Policy") == "frame-ancestors 'none'"
            connection.close()
            return status, data

        authorized = {"Content-Type": "application/json", "X-Studio-Token": studio.token, "Origin": f"http://127.0.0.1:{port}"}
        try:
            status, data = http("GET", "/api/catalog")
            assert status == 200 and json.loads(data)["token"] == studio.token
            assert str(directory).encode() not in data
            assert http("GET", "/api/catalog", headers={"Host": "evil.example"})[0] == 403
            assert http("GET", "/api/catalog", headers={"Host": "127.0.0.1:1"})[0] == 403
            assert http("GET", "/api/block?id=minecraft%3Aoak")[0] == 200
            assert http("GET", "/api/source?texture=minecraft%3Ablock%2Foak_end") == (200, png())
            assert http("GET", "/api/source?texture=../../etc/passwd")[0] == 404
            assert http("GET", "/../texture_studio.py")[0] == 404
            assert http("GET", "/%2e%2e/texture_studio.py")[0] == 404
            assert http("GET", "/texture_studio.py")[0] == 404
            (directory / "texture-studio.html").write_text("<!doctype html><title>Test</title>")
            assert http("GET", "/")[0] == 200
            assert http("GET", candidate_end["url"]) == (200, valid_bytes)
            assert http("GET", candidate_end["nUrl"]) == (200, png(color="blue"))
            assert http("GET", candidate_end["url"] + "&map=../../secret")[0] == 404
            candidate_path.write_bytes(b"broken")
            assert http("GET", candidate_end["url"])[0] == 404
            candidate_path.write_bytes(valid_bytes)
            payload = {"texture": end, "candidate": None}
            assert http("POST", "/api/keeper", payload)[0] == 403
            assert http("POST", "/api/keeper", payload, authorized | {"Origin": "https://evil.example"})[0] == 403
            assert http("POST", "/api/keeper", payload, authorized | {"Origin": "null"})[0] == 403
            assert http("POST", "/api/keeper", payload, authorized | {"X-Studio-Token": "wrong"})[0] == 403
            assert http("POST", "/api/keeper", payload, authorized | {"X-Studio-Token": "é"})[0] == 403
            assert http("POST", "/api/keeper", payload, authorized | {"Sec-Fetch-Site": "cross-site"})[0] == 403
            assert http("POST", "/api/keeper", payload, authorized)[0] == 200
            assert http("POST", "/api/import", headers=authorized, raw="{")[0] == 400
            assert http("POST", "/api/import", headers=authorized | {"Content-Length": str(MAX_BODY + 1)}, raw="")[0] == 413
            assert http("POST", "/api/import", {"texture": end, "name": "test", "png": "not base64!"}, authorized)[0] == 400
            assert http("POST", "/api/import", {"texture": end, "name": "test", "png": base64.b64encode(b"not a PNG").decode()}, authorized)[0] == 400
            status, data = http("POST", "/api/import", {"texture": end, "name": "http", "png": base64.b64encode(png()).decode(), "mcmeta": None}, authorized)
            assert status == 200 and json.loads(data)["eligible"]
            assert studio.catalog()["textures"][end]["keeper"] is None
            calls = []
            entered, release = threading.Event(), threading.Event()

            def fake_generate(source, output, **kwargs):
                calls.append(kwargs)
                assert Path(source).read_bytes() == studio.source_data[fire]
                assert json.loads(Path(str(source) + ".mcmeta").read_text()) == metadata
                assert not Path(output).exists()
                entered.set()
                assert release.wait(5)
                Path(output).write_bytes(png((8, 24)))
                Path(str(output) + ".mcmeta").write_text(json.dumps(scaled_meta))
                Path(output).with_name(Path(output).stem + "_n.png").write_bytes(png((8, 24), color="blue"))

            sys.modules["generate_vanilla_albedo"] = types.SimpleNamespace(MATERIAL_PRESETS={"fire": None}, generate_albedo=fake_generate)
            assert http("POST", "/api/generate", {"texture": fire, "material": "invalid"}, authorized)[0] == 400
            assert not calls
            result = []
            generation = threading.Thread(target=lambda: result.append(http("POST", "/api/generate", {"texture": fire, "material": "fire", "seed": 99, "grayscale": True}, authorized)))
            generation.start()
            assert entered.wait(5)
            assert http("POST", "/api/generate", {"texture": fire, "material": "fire"}, authorized)[0] == 409
            assert http("GET", "/api/catalog")[0] == 200
            release.set()
            generation.join(10)
            assert result[0][0] == 200
            generated = json.loads(result[0][1])
            assert generated["eligible"] and generated["recipe"]["animate"] and calls[0]["seed"] == 99
            assert generated["animation"] == scaled_meta["animation"] and calls[0]["grayscale"] is True
            assert http("GET", generated["nUrl"]) == (200, png((8, 24), color="blue"))
            assert calls[0]["client_jar"] == jar
            assert studio.catalog()["textures"][fire]["keeper"] == full["id"], "Generation must not choose a keeper"

            def fake_preview(source, output, **kwargs):
                assert not kwargs["animate"] and kwargs["frame"] == 1
                Path(output).write_bytes(png())

            sys.modules["generate_vanilla_albedo"].generate_albedo = fake_preview
            status, data = http("POST", "/api/generate", {"texture": fire, "material": "fire", "frame": 1}, authorized)
            assert status == 200 and not json.loads(data)["eligible"]

            def failed_generate(*args, **kwargs):
                raise RuntimeError("Private local path must not leak: " + str(directory))

            sys.modules["generate_vanilla_albedo"].generate_albedo = failed_generate
            status, data = http("POST", "/api/generate", {"texture": fire, "material": "fire"}, authorized)
            assert status == 500 and str(directory).encode() not in data
            assert not studio.generation_lock.locked()
        finally:
            sys.modules.pop("generate_vanilla_albedo", None)
            server.shutdown()
            server.server_close()
            thread.join()

        local = directory / "explicit.png"
        local.write_bytes(png())
        Path(str(local) + ".mcmeta").write_text('{"texture":{"blur":false}}')
        output = subprocess.check_output([sys.executable, str(Path(__file__).with_name("texture_studio.py")),
                                          "--client-jar", str(jar), "--library", str(directory / "cli-library"),
                                          "--import", end + "=" + str(local), "--index-only"], text=True)
        assert json.loads(output)["summary"]["complete"] == 0
        imported = Studio(jar, directory / "cli-library").catalog()["textures"][end]
        assert len(imported["candidates"]) == 1 and imported["keeper"] is None
        assert jar.read_bytes() == original_jar, "Client JAR must be read-only"
    print("PASS: catalogue resolution, coverage/persistence/integrity, animation, HTTP security, fake generation, CLI")


if __name__ == "__main__":
    run()
