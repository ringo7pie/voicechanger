"""ファイルの置き場所（ソース／インストーラー版）と、エンジンを呼ぶコマンドの組み立て。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from voicechanger import paths
from voicechanger.gui.engine_link import engine_command, engine_prefix


class AppDataRootTest(unittest.TestCase):
    def test_source_uses_repository(self) -> None:
        self.assertEqual(paths.app_data_root(frozen=False, environ={}), paths.REPO_ROOT)

    def test_frozen_uses_local_appdata(self) -> None:
        root = paths.app_data_root(frozen=True, environ={"LOCALAPPDATA": r"C:\Users\someone\AppData\Local"})
        self.assertEqual(root, Path(r"C:\Users\someone\AppData\Local") / "VoiceChanger")

    def test_environment_override_wins(self) -> None:
        environ = {paths.DATA_DIR_ENV: r"D:\test-data", "LOCALAPPDATA": r"C:\x"}
        self.assertEqual(paths.app_data_root(frozen=True, environ=environ), Path(r"D:\test-data"))
        self.assertEqual(paths.app_data_root(frozen=False, environ=environ), Path(r"D:\test-data"))

    def test_source_resources_are_in_repository(self) -> None:
        self.assertEqual(paths.resource_root(frozen=False), paths.REPO_ROOT)
        self.assertTrue((paths.resource_root(frozen=False) / "config" / "voicechanger.json").is_file())


class EngineCommandTest(unittest.TestCase):
    def test_source_runs_module(self) -> None:
        self.assertEqual(engine_prefix(Path("python.exe"), frozen=False), ["python.exe", "-m", "voicechanger"])

    def test_frozen_calls_itself_with_engine(self) -> None:
        command = engine_command(Path("ignored.exe"), 42, ["--no-obs"], frozen=True)
        self.assertEqual(command, [sys.executable, "engine", "run", "--parent-pid", "42", "--no-obs"])


if __name__ == "__main__":
    unittest.main()
