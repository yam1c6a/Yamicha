"""Run the complete stage-0 quality gate without third-party tools."""

from __future__ import annotations

import compileall
import sys
import unittest
from pathlib import Path

from check_architecture import find_violations
from check_utf8 import find_invalid_files


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    source_root = repository_root / "src"
    sys.path.insert(0, str(source_root))

    print("build: compiling sources")
    if not compileall.compile_dir(source_root, quiet=1):
        return 1

    print("static: checking dependency direction")
    violations = find_violations(source_root)
    if violations:
        for violation in violations:
            print(violation)
        return 1

    print("encoding: checking UTF-8")
    invalid = find_invalid_files(repository_root)
    if invalid:
        for path in invalid:
            print(f"invalid UTF-8: {path}")
        return 1

    print("test: running unit tests")
    suite = unittest.defaultTestLoader.discover(str(repository_root / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
