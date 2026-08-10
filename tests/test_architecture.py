from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

from check_architecture import find_violations  # noqa: E402


class ArchitectureCheckTest(unittest.TestCase):
    def test_current_source_obeys_dependency_direction(self) -> None:
        self.assertEqual(find_violations(REPOSITORY_ROOT / "src"), [])

    def test_body_importing_life_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_root = Path(directory) / "src"
            module = source_root / "yamicha" / "body" / "invalid.py"
            module.parent.mkdir(parents=True)
            module.write_text("from yamicha.life import core\n", encoding="utf-8")

            violations = find_violations(source_root)

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].source_area, "body")
        self.assertEqual(violations[0].target_area, "life")

    def test_relative_import_cannot_bypass_dependency_direction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_root = Path(directory) / "src"
            module = source_root / "yamicha" / "body" / "runtime" / "invalid.py"
            module.parent.mkdir(parents=True)
            module.write_text("from ...life import core\n", encoding="utf-8")

            violations = find_violations(source_root)

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].source_area, "body")
        self.assertEqual(violations[0].target_area, "life")

    def test_root_reexport_import_cannot_bypass_dependency_direction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_root = Path(directory) / "src"
            module = source_root / "yamicha" / "body" / "invalid.py"
            module.parent.mkdir(parents=True)
            module.write_text("from yamicha import life\n", encoding="utf-8")

            violations = find_violations(source_root)

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].source_area, "body")
        self.assertEqual(violations[0].target_area, "life")


if __name__ == "__main__":
    unittest.main()
