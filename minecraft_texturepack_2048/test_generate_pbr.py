#!/usr/bin/env python3
"""Offline calibration checks. Run: python3 -m unittest test_generate_pbr -v"""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from generate_pbr import generate_pbr, height_to_normal, add_surface_detail, LABPBR_HEIGHT_RANGE
from bake_candidate_pbr import bake_candidate


class PBRTest(unittest.TestCase):
    def test_candidate_cutout_and_flat_emission(self):
        with tempfile.TemporaryDirectory() as tmp:
            for profile in ("stone", "emissive_sprite"):
                source = Path(tmp) / f"{profile}.png"
                image = Image.new("RGBA", (64, 64), (100, 110, 120, 255))
                image.paste((200, 0, 0, 0), (0, 0, 16, 16))
                image.save(source)
                original = source.read_bytes()
                with contextlib.redirect_stdout(io.StringIO()):
                    n_path, s_path = bake_candidate(source, profile)
                with Image.open(n_path) as img:
                    n = np.asarray(img)
                with Image.open(s_path) as img:
                    s = np.asarray(img)
                np.testing.assert_array_equal(n[0, 0], [128, 128, 255, 255])
                if profile == "emissive_sprite":
                    np.testing.assert_array_equal(n, np.broadcast_to([128, 128, 255, 255], n.shape))
                    self.assertEqual(s[32, 32, 3], 254)
                    self.assertEqual(s[0, 0, 3], 255)
                else:
                    self.assertTrue(np.all(s[..., 3] == 255))
                self.assertEqual(source.read_bytes(), original)
                with self.assertRaises(FileExistsError):
                    bake_candidate(source, profile)

    def test_detail_is_bounded_not_normalized_and_can_be_disabled(self):
        base = np.full((64, 64), 0.5, dtype=np.float32)
        detail = np.tile(np.sin(np.arange(64) * np.pi / 2), (64, 1)).astype(np.float32) * 0.02
        np.testing.assert_array_equal(add_surface_detail(base, detail, 0), base)
        combined = add_surface_detail(base, detail, 0.15)
        self.assertAlmostEqual(float(np.max(np.abs(combined - base))), 0.003, places=6)
        strong = add_surface_detail(base, detail * 100, 1)
        self.assertLessEqual(float(np.max(np.abs(strong - base))), 1.5 / 64 + 1e-6)
        self.assertGreater(float(np.std(height_to_normal(combined)[..., 0])), 0.01)
        for strength in (-1, 2, np.nan, np.inf):
            with self.assertRaises(ValueError):
                add_surface_detail(base, detail, strength)

    def test_flat_surface_has_vertical_normal(self):
        normal = height_to_normal(np.full((32, 64), 0.5))
        np.testing.assert_array_equal(normal[..., :2], 0)
        np.testing.assert_array_equal(normal[..., 2], 1)

    def test_rectangular_xy_ramp_matches_physical_surface(self):
        v, u = np.mgrid[:64, :128].astype(float)
        height = 0.2 + 0.1 * u / 128 + 0.2 * v / 64
        normal = height_to_normal(height)[1:-1, 1:-1]
        expected = np.array([-0.1 * LABPBR_HEIGHT_RANGE, -0.2 * LABPBR_HEIGHT_RANGE, 1])
        expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(normal, np.broadcast_to(expected, normal.shape), atol=2e-6)

        # Three's UV v increases UP: green must flip exactly once.
        tangent_u = [1, 0, 0.1 * LABPBR_HEIGHT_RANGE]
        tangent_v = [0, 1, -0.2 * LABPBR_HEIGHT_RANGE]
        geometric = np.cross(tangent_u, tangent_v)
        geometric /= np.linalg.norm(geometric)
        np.testing.assert_allclose(normal[0, 0] * [1, -1, 1], geometric, atol=2e-6)

    def test_periodic_surface_is_resolution_independent(self):
        normals = []
        for size in (128, 256, 512):
            v, u = np.mgrid[:size, :size] / size
            height = 0.5 + 0.1 * np.sin(2 * np.pi * u) + 0.05 * np.cos(2 * np.pi * v)
            normals.append(height_to_normal(height)[::size // 128, ::size // 128])
        for normal in normals[1:]:
            np.testing.assert_allclose(normals[0], normal, atol=7e-5)

    def test_decoded_normal_scales_with_depth_and_tile_size(self):
        height = np.broadcast_to(np.arange(128) / 128, (64, 128))
        n = height_to_normal(height)[32, 64]
        # At 0.06 scene units depth, 2 units wide, tiled three times.
        scaled = n * [0.06 / (LABPBR_HEIGHT_RANGE * (2 / 3)), 1, 1]
        self.assertAlmostEqual(scaled[0] / scaled[2], -0.06 / (2 / 3), places=6)
        # AO is not an input to reconstructed Z.
        z = np.sqrt(max(0, 1 - np.dot(n[:2], n[:2])))
        self.assertAlmostEqual(float(z), float(n[2]), places=6)

    def test_invalid_height(self):
        for height in (np.zeros((1, 4)), np.zeros(4), np.full((4, 4), np.nan)):
            with self.assertRaises(ValueError):
                height_to_normal(height)

    def test_ore_marks_bright_desaturated_flecks_as_metal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "ore.png"
            image = Image.new("RGB", (64, 64), (150, 40, 20))  # Saturated rust.
            nugget = Image.new("RGB", (16, 16), (205, 205, 205))  # Raw metal.
            image.paste(nugget, (24, 24))
            image.save(source)
            with contextlib.redirect_stdout(io.StringIO()):
                n_path, s_path = generate_pbr(str(source), str(root / "material"), material_type="ore")
            with Image.open(s_path) as img:
                s = np.asarray(img)
            self.assertEqual(s[32, 32, 1], 230)  # Nugget interior is metal.
            self.assertEqual(s[32, 32, 0], 200)  # ... and polished smooth.
            self.assertEqual(s[4, 4, 1], 10)  # Rust stays dielectric.
            self.assertLess(s[4, 4, 0], 100)

    def test_bake_uses_disposable_synthetic_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            Image.fromarray(np.random.default_rng(42).integers(0, 256, (64, 64, 3), dtype=np.uint8)).save(source)
            with contextlib.redirect_stdout(io.StringIO()):
                n_path, s_path = generate_pbr(str(source), str(root / "material"))
            with Image.open(n_path) as img:
                self.assertEqual(img.mode, "RGBA")
                n = np.asarray(img)
            with Image.open(s_path) as img:
                self.assertEqual(img.mode, "RGBA")
                s = np.asarray(img)
            self.assertEqual(n.shape, (64, 64, 4))
            self.assertEqual(s.shape, n.shape)
            self.assertTrue(np.all(s[..., 1] == 10))
            self.assertTrue(np.all(s[..., 3] == 255))
            with contextlib.redirect_stdout(io.StringIO()):
                flat_n, flat_s = generate_pbr(str(source), str(root / "no_detail"), detail_strength=0)
            with Image.open(flat_n) as img:
                base = np.asarray(img)
            with Image.open(flat_s) as img:
                np.testing.assert_array_equal(s, np.asarray(img))
            np.testing.assert_array_equal(n[..., 2], base[..., 2])  # Same AO.
            self.assertFalse(np.array_equal(n[..., :2], base[..., :2]))
            delta = np.abs(n[..., 3].astype(int) - base[..., 3].astype(int))
            self.assertLessEqual(delta.max(), 7)  # 1.5/64 cap plus 8-bit rounding.


if __name__ == "__main__":
    unittest.main()
