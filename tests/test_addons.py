"""追加の部品（Beatrice の zip、pylibrb の wheel）を入れる処理。"""
from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from voicechanger import addons


def write_zip(path: Path, members: dict[str, bytes]) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BeatriceZipTest(unittest.TestCase):
    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.work, True)
        self.app_data = self.work / "app"

    def manifest(self, vst_sha: str, model_sha: str) -> dict:
        return {"items": {
            "beatrice_vst": {"name": "VST", "version": "1", "file_pattern": "vst*.zip", "sha256": vst_sha,
                             "extract_to": "runtime/beatrice/vst"},
            "beatrice_model_1": {"name": "モデル", "version": "1", "file_pattern": "model*.zip", "sha256": model_sha,
                                 "extract_to": "runtime/beatrice/models"}}}

    def test_extracts_matching_zip_and_replaces_old(self) -> None:
        zip_path = self.work / "model.zip"
        digest = write_zip(zip_path, {"model_1/model.toml": b"x", "model_1/a.bin": b"y"})
        old = self.app_data / "runtime" / "beatrice" / "models" / "stale.txt"
        old.parent.mkdir(parents=True)
        old.write_text("old")
        name = addons.install_beatrice_zip(zip_path, self.app_data, self.manifest("0" * 64, digest))
        self.assertIn("モデル", name)
        destination = self.app_data / "runtime" / "beatrice" / "models"
        self.assertEqual((destination / "model_1" / "model.toml").read_bytes(), b"x")
        self.assertFalse(old.exists())

    def test_rejects_unknown_zip(self) -> None:
        zip_path = self.work / "other.zip"
        write_zip(zip_path, {"a.txt": b"a"})
        with self.assertRaises(addons.AddonError):
            addons.install_beatrice_zip(zip_path, self.app_data, self.manifest("0" * 64, "1" * 64))
        self.assertFalse((self.app_data / "runtime").exists() and any((self.app_data / "runtime").rglob("a.txt")))

    def test_rejects_path_traversal(self) -> None:
        zip_path = self.work / "vst.zip"
        digest = write_zip(zip_path, {"../evil.txt": b"bad"})
        with self.assertRaises(addons.AddonError):
            addons.install_beatrice_zip(zip_path, self.app_data, self.manifest(digest, "1" * 64))
        self.assertFalse((self.app_data / "runtime" / "beatrice" / "evil.txt").exists())

    def test_manifest_knows_both_official_zips(self) -> None:
        manifest = addons.load_manifest()
        for key in addons.BEATRICE_ITEMS:
            self.assertRegex(manifest["items"][key]["sha256"], r"^[0-9a-f]{64}$")


class PylibrbTest(unittest.TestCase):
    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.work, True)
        self.lib_dir = self.work / "runtime" / "lib"

    def test_extracts_only_package_from_wheel(self) -> None:
        wheel = self.work / "pylibrb.whl"
        digest = write_zip(wheel, {"pylibrb/__init__.py": b"# ok", "pylibrb-0.1.2.dist-info/LICENSE": b"GPL",
                                   "other/x.py": b"no"})
        with mock.patch.dict(addons.PYLIBRB_WHEEL, {"sha256": digest}):
            addons.install_pylibrb(self.lib_dir, wheel_path=wheel)
        self.assertTrue((self.lib_dir / "pylibrb" / "__init__.py").is_file())
        self.assertTrue((self.lib_dir / "pylibrb-0.1.2.dist-info" / "LICENSE").is_file())
        self.assertFalse((self.lib_dir / "other").exists())
        self.assertTrue(addons.pylibrb_installed(self.lib_dir))

    def test_rejects_wrong_hash(self) -> None:
        def fake_download(_url: str, target: Path) -> None:
            write_zip(target, {"pylibrb/__init__.py": b"tampered"})

        with self.assertRaises(addons.AddonError):
            addons.install_pylibrb(self.lib_dir, downloader=fake_download)
        self.assertFalse((self.lib_dir / "pylibrb").exists())

    def test_download_error_is_reported(self) -> None:
        def failing_download(_url: str, _target: Path) -> None:
            raise OSError("offline")

        with self.assertRaisesRegex(addons.AddonError, "offline"):
            addons.install_pylibrb(self.lib_dir, downloader=failing_download)


if __name__ == "__main__":
    unittest.main()
