"""Offline checks: python3 -m unittest test_generate_vanilla_albedo -v"""

import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

import generate_vanilla_albedo as albedo


class AlbedoTest(unittest.TestCase):
    def test_material_presets_edges_alpha_and_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / "oak_planks.png", root / "output.png"
            # Both colors exceed the old mortar threshold: wood must still have edges.
            image = Image.new("RGBA", (16, 16), (220, 190, 150, 255))
            image.paste((150, 130, 110, 255), (0, 6, 16, 9))
            image.save(source)
            edge, organic = root / "edges.png", root / "organic.png"
            albedo.create_edge_guide(image, str(edge))
            albedo.create_organic_canny_guide(image, str(organic))
            with Image.open(edge) as guide:
                self.assertEqual(guide.getextrema(), (0, 255))
            with Image.open(organic) as guide:
                self.assertEqual(guide.getextrema(), (0, 0))

            image.putpixel((0, 0), (255, 0, 0, 0))
            image.putpixel((1, 0), (255, 0, 0, 128))
            image.save(source)

            def generate(cmd, **kwargs):
                options = dict(zip(cmd[2::2], cmd[3::2]))
                Image.new("RGB", (1024, 1024), (100, 160, 200)).save(options["--output"])

            with patch.object(albedo.subprocess, "run", side_effect=generate) as run:
                for material, (prompt, negative, mode) in albedo.MATERIAL_PRESETS.items():
                    with self.subTest(material=material):
                        albedo.generate_albedo(source, output, material=material)
                        options = dict(zip(run.call_args.args[0][2::2], run.call_args.args[0][3::2]))
                        self.assertEqual(options["--prompt"], prompt.format(block="oak planks"))
                        self.assertEqual(options["--negative-prompt"], negative)
                        with Image.open(output) as result:
                            self.assertEqual(result.mode, "RGBA")
                            self.assertEqual(result.getpixel((0, 0))[3], 0)
                            self.assertEqual(result.getpixel((64, 0))[3], 128)
                            self.assertEqual(result.getpixel((128, 0))[3], 255)
                        for suffix in ("_n.png", "_s.png"):
                            with Image.open(output.with_name(output.stem + suffix)) as packed:
                                self.assertEqual(packed.size, (1024, 1024))
                                self.assertEqual(packed.mode, "RGBA")
                        with Image.open(output.with_name(output.stem + "_s.png")) as packed:
                            self.assertEqual(packed.getpixel((128, 0))[1], 230 if material == "metal" else 10)
                            self.assertEqual(packed.getpixel((128, 0))[3], 254 if material == "fire" else 255)
                albedo.generate_albedo(source, output, material="leaves", guide="none",
                                       prompt="custom", negative_prompt="", grayscale=True)
                options = dict(zip(run.call_args.args[0][2::2], run.call_args.args[0][3::2]))
                self.assertEqual(options["--prompt"], "custom")
                self.assertEqual(options["--negative-prompt"], "")
                self.assertNotIn("--image", options)
                self.assertEqual(json.loads(options["--config-json"])["controls"], [])
                with Image.open(output) as result:
                    r, g, b, a = result.getpixel((64, 0))
                    self.assertEqual((r, g, a), (b, b, 128))
                # Explicit guide and thresholds must override a non-stone preset.
                with patch.object(albedo, "create_organic_canny_guide", wraps=albedo.create_organic_canny_guide) as guide:
                    albedo.generate_albedo(source, output, material="wood", guide="organic")
                    guide.assert_called_once()
                with patch.object(albedo, "create_edge_guide", wraps=albedo.create_edge_guide) as guide:
                    albedo.generate_albedo(source, output, material="wood", canny_low=10, canny_high=30)
                    self.assertEqual(guide.call_args.args[2:], (10, 30, 1024))
                image.resize((32, 32)).save(source)
                albedo.generate_albedo(source, output, material="generic")
                with Image.open(output) as result:
                    self.assertEqual(result.size, (1024, 1024))

    def test_animation_file_and_jar_metadata_preview_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / "fire_0.png", root / "output.png"
            image = Image.new("RGBA", (16, 32), (200, 160, 30, 255))
            image.paste((200, 160, 30, 100), (0, 16, 16, 32))
            image.save(source)
            metadata = {"animation": {"width": 16, "height": 16, "frametime": 3,
                                     "interpolate": True, "frames": [1, {"index": 0, "time": 7}]}}
            sidecar = Path(str(source) + ".mcmeta")
            sidecar.write_text(json.dumps(metadata))
            output_meta = Path(str(output) + ".mcmeta")
            jar = root / "client.jar"
            with zipfile.ZipFile(jar, "w") as archive:
                archive.write(source, "assets/minecraft/textures/block/fire_0.png")
                archive.write(sidecar, "assets/minecraft/textures/block/fire_0.png.mcmeta")

            def generate(cmd, **kwargs):
                options = dict(zip(cmd[2::2], cmd[3::2]))
                Image.new("RGB", (1024, 1024), "orange").save(options["--output"])

            with patch.object(albedo.subprocess, "run", side_effect=generate) as run:
                for input_source in (source, "fire_0"):
                    albedo.generate_albedo(input_source, output, client_jar=jar, material="fire", animate=True)
                    with Image.open(output) as result:
                        self.assertEqual(result.size, (1024, 2048))
                        self.assertEqual(result.getpixel((0, 0))[3], 255)
                        self.assertEqual(result.getpixel((0, 1024))[3], 100)
                    expected = json.loads(json.dumps(metadata))
                    expected["animation"].update(width=1024, height=1024)
                    self.assertEqual(json.loads(output_meta.read_text()), expected)
                    for suffix in ("_n.png", "_s.png"):
                        packed_path = output.with_name(output.stem + suffix)
                        with Image.open(packed_path) as packed:
                            self.assertEqual(packed.size, (1024, 2048))
                            self.assertEqual(packed.mode, "RGBA")
                        self.assertEqual(json.loads(Path(str(packed_path) + ".mcmeta").read_text()), expected)
                self.assertEqual(run.call_count, 4)

                # A later frame failure must leave BOTH previous files unchanged.
                old_png, old_meta = output.read_bytes(), output_meta.read_bytes()
                old_material = {path: path.read_bytes() for path in root.glob("output*")}
                calls = 0
                def fail_second(cmd, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise subprocess.CalledProcessError(2, cmd)
                    generate(cmd, **kwargs)
                run.side_effect = fail_second
                with self.assertRaises(subprocess.CalledProcessError):
                    albedo.generate_albedo(source, output, material="fire", animate=True)
                self.assertEqual((output.read_bytes(), output_meta.read_bytes()), (old_png, old_meta))
                self.assertEqual({path: path.read_bytes() for path in old_material}, old_material)
                run.side_effect = generate

                # A metadata publication failure must roll the PNG back too.
                replace = Path.replace
                def fail_metadata(path, target):
                    if path.name == "albedo.png.mcmeta":
                        raise OSError("metadata write failed")
                    return replace(path, target)
                with patch.object(Path, "replace", fail_metadata):
                    with self.assertRaisesRegex(OSError, "metadata write failed"):
                        albedo.generate_albedo(source, output, material="fire", animate=True)
                self.assertEqual((output.read_bytes(), output_meta.read_bytes()), (old_png, old_meta))
                self.assertEqual({path: path.read_bytes() for path in old_material}, old_material)

                # PBR failure must not publish even an otherwise valid new albedo.
                with patch.object(albedo, "generate_material_maps", side_effect=ValueError("PBR failed")):
                    with self.assertRaisesRegex(ValueError, "PBR failed"):
                        albedo.generate_albedo(source, output, material="fire", animate=True)
                self.assertEqual({path: path.read_bytes() for path in old_material}, old_material)

                # Fail late, after albedo and normal publication; roll all six files back.
                def fail_specular(path, target):
                    if path.name == "albedo_s.png":
                        raise OSError("specular write failed")
                    return replace(path, target)
                with patch.object(Path, "replace", fail_specular):
                    with self.assertRaisesRegex(OSError, "specular write failed"):
                        albedo.generate_albedo(source, output, material="fire", animate=True)
                self.assertEqual({path: path.read_bytes() for path in old_material}, old_material)

                albedo.generate_albedo(source, output, material="fire", frame=1)
                with Image.open(output) as result:
                    self.assertEqual(result.size, (1024, 1024))
                    self.assertEqual(result.getpixel((0, 0))[3], 100)
                self.assertFalse(output_meta.exists())  # No stale animation on a static preview.
                for suffix in ("_n.png", "_s.png"):
                    packed_path = output.with_name(output.stem + suffix)
                    self.assertFalse(Path(str(packed_path) + ".mcmeta").exists())
                    with Image.open(packed_path) as packed:
                        self.assertEqual(packed.size, (1024, 1024))
                self.assertFalse(list(root.glob(".albedo-*")))
                self.assertEqual(json.loads(sidecar.read_text()), metadata)

    def test_invalid_material_and_animation_options_fail_before_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "source.png", Path(tmp) / "out.png"
            Image.new("RGBA", (16, 32)).save(source)
            with patch.object(albedo.subprocess, "run") as run:
                for options in ({}, {"animate": True}, {"frame": -1}, {"frame": 2},
                                {"frame": 0, "animate": True}, {"material": "typo"},
                                {"guide": "typo"}, {"canny_low": 80, "canny_high": 20},
                                {"canny_low": float("nan")}):
                    with self.subTest(options=options), self.assertRaises(ValueError):
                        albedo.generate_albedo(source, output, **options)
                sidecar = Path(str(source) + ".mcmeta")
                for metadata in ([], {"animation": None}, {"animation": {"width": 8}},
                                 {"animation": {"frames": [2]}}, {"animation": {"frames": []}},
                                 {"animation": {"frametime": 0}}, {"animation": {"interpolate": "yes"}}):
                    sidecar.write_text(json.dumps(metadata))
                    with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                        albedo.generate_albedo(source, output, animate=True)
                sidecar.unlink()
                for destination in (source, Path(tmp) / "not-a-png.txt"):
                    with self.assertRaises(ValueError):
                        albedo.generate_albedo(source, destination, frame=0)
                # A companion map must not silently overwrite the input either.
                collision_source = Path(tmp) / "out_n.png"
                Image.new("RGB", (16, 16)).save(collision_source)
                with self.assertRaises(ValueError):
                    albedo.generate_albedo(collision_source, output)
                run.assert_not_called()
            self.assertFalse(output.exists())

    def test_file_and_jar_sources_and_generation_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "stone.png"
            image = Image.new("RGB", (16, 16), "gray")
            image.paste("black", (4, 4, 12, 12))
            image.save(source)
            jar = root / "client.jar"
            with zipfile.ZipFile(jar, "w") as archive:
                archive.write(source, "assets/minecraft/textures/block/stone.png")

            def generate(cmd, **kwargs):
                self.assertEqual(kwargs, dict(capture_output=True, text=True, check=True))
                self.assertEqual(cmd[:2], ["draw-things-cli", "generate"])
                options = dict(zip(cmd[2::2], cmd[3::2]))
                with Image.open(options["--image"]) as guide:
                    self.assertEqual(guide.size, (1024, 1024))
                    self.assertEqual(guide.getextrema(), (0, 255))
                Image.new("RGB", (1024, 1024), "gray").save(options["--output"])

            with patch.object(albedo.subprocess, "run", side_effect=generate) as run:
                output = root / "nested" / "albedo.png"
                self.assertEqual(albedo.generate_albedo(source, output), output)
                default_cmd = run.call_args.args[0]
                default_options = dict(zip(default_cmd[2::2], default_cmd[3::2]))
                self.assertEqual(default_options["--prompt"], albedo.DEFAULT_PROMPT)
                self.assertEqual(default_options["--negative-prompt"], albedo.DEFAULT_NEGATIVE_PROMPT)
                self.assertEqual(default_options["--model"], albedo.DEFAULT_MODEL)
                for key, value in {"--seed": "42", "--steps": "22", "--cfg": "6.0",
                                   "--strength": "1.0", "--width": "1024", "--height": "1024"}.items():
                    self.assertEqual(default_options[key], value)
                control = json.loads(default_options["--config-json"])["controls"][0]
                self.assertEqual(control["weight"], 0.42)
                self.assertEqual(control["guidanceEnd"], 0.35)
                self.assertEqual(control["file"], albedo.DEFAULT_CONTROL_MODEL)

                albedo.generate_albedo(
                    "stone", output, client_jar=jar, prompt="rock", negative_prompt="ink",
                    seed=7, steps=3, cfg=4.0, weight=0.2, guidance_end=0.5,
                    model="model.ckpt", control_model="control.ckpt",
                )
                cmd = run.call_args.args[0]
                options = dict(zip(cmd[2::2], cmd[3::2]))
                for key, value in {"--prompt": "rock", "--negative-prompt": "ink",
                                   "--seed": "7", "--steps": "3", "--cfg": "4.0",
                                   "--model": "model.ckpt"}.items():
                    self.assertEqual(options[key], value)
                control = json.loads(options["--config-json"])["controls"][0]
                self.assertEqual((control["weight"], control["guidanceEnd"], control["file"]),
                                 (0.2, 0.5, "control.ckpt"))
                self.assertFalse(Path(options["--image"]).exists())
                self.assertEqual(set(output.parent.iterdir()),
                                 {output, output.with_name("albedo_n.png"), output.with_name("albedo_s.png")})
                with Image.open(output) as result:
                    self.assertEqual(result.size, (1024, 1024))
                    result.verify()

    def test_failures_preserve_output_and_clean_temporary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / "source.png", root / "output.png"
            Image.new("RGB", (16, 16)).save(source)
            output.write_bytes(b"existing albedo")

            for failure in ("process", "missing", "corrupt", "wrong_size"):
                with self.subTest(failure=failure):
                    def generate(cmd, **kwargs):
                        generated = Path(cmd[cmd.index("--output") + 1])
                        if failure == "process":
                            generated.write_bytes(b"partial output")
                            raise subprocess.CalledProcessError(2, cmd, stderr="model missing")
                        if failure == "corrupt":
                            generated.write_bytes(b"not an image")
                        if failure == "wrong_size":
                            Image.new("RGB", (16, 16)).save(generated)

                    with patch.object(albedo.subprocess, "run", side_effect=generate):
                        with self.assertRaises((subprocess.CalledProcessError, OSError, ValueError)):
                            albedo.generate_albedo(source, output)
                    self.assertEqual(output.read_bytes(), b"existing albedo")
                    self.assertEqual(set(root.iterdir()), {source, output})

    def test_invalid_sources_do_not_start_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / "source.png", root / "output.png"
            Image.new("RGB", (32, 16)).save(source)
            with patch.object(albedo.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "16x16"):
                    albedo.generate_albedo(source, output)
                for missing in (root / "missing.png", str(root / "missing.png")):
                    with self.assertRaises(FileNotFoundError):
                        albedo.generate_albedo(missing, output)
                jar = root / "empty.jar"
                with zipfile.ZipFile(jar, "w"):
                    pass
                with self.assertRaisesRegex(FileNotFoundError, "not in client jar"):
                    albedo.generate_albedo("stone", output, client_jar=jar)
                run.assert_not_called()
            self.assertFalse(output.exists())
            self.assertFalse(list(root.glob(".albedo-*")))

    def test_cli_delegates_and_reports_process_errors(self):
        with patch.object(albedo, "generate_albedo", return_value=Path("out.png")) as generate:
            with patch("sys.stdout", new_callable=io.StringIO):
                albedo.main(["oak_leaves", "out.png", "--seed", "9", "--prompt", "rock",
                             "--material", "leaves", "--guide", "edges", "--grayscale", "--frame", "0"])
            self.assertEqual(generate.call_args.kwargs["source"], "oak_leaves")
            self.assertEqual(generate.call_args.kwargs["seed"], 9)
            self.assertEqual(generate.call_args.kwargs["prompt"], "rock")
            self.assertEqual(generate.call_args.kwargs["material"], "leaves")
            self.assertEqual(generate.call_args.kwargs["guide"], "edges")
            self.assertTrue(generate.call_args.kwargs["grayscale"])
            self.assertEqual(generate.call_args.kwargs["frame"], 0)
            generate.side_effect = subprocess.CalledProcessError(2, ["draw-things-cli"], stderr="model missing")
            with patch("sys.stderr", new_callable=io.StringIO) as stderr:
                with self.assertRaises(SystemExit) as error:
                    albedo.main(["stone", "out.png"])
                self.assertEqual(error.exception.code, 1)
                self.assertIn("model missing", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
