"""Run Yamicha's latest interactive console."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from yamicha.bootstrap import run_interactive_console


def main() -> int:
    parser = argparse.ArgumentParser(description="Yamicha 対話コンソール（段階10）")
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(".yamicha/yamicha.sqlite3"),
        help="永続化DBのパス（既定: .yamicha/yamicha.sqlite3）",
    )
    arguments = parser.parse_args()
    return run_interactive_console(
        input_stream=sys.stdin,
        output_stream=sys.stdout,
        persistence_path=arguments.db,
    )


if __name__ == "__main__":
    raise SystemExit(main())
