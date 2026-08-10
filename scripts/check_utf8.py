"""Verify that repository text files are valid UTF-8."""

from __future__ import annotations

import sys
from pathlib import Path


TEXT_SUFFIXES = {".md", ".py", ".toml", ".yml", ".yaml"}
EXCLUDED_PARTS = {".git", ".venv", "__pycache__"}


def find_invalid_files(repository_root: Path) -> list[Path]:
    invalid: list[Path] = []
    for path in sorted(repository_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            invalid.append(path)
    return invalid


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    invalid = find_invalid_files(repository_root)
    if invalid:
        for path in invalid:
            print(f"invalid UTF-8: {path}")
        return 1
    print("utf-8: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
