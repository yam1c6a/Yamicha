"""Run Yamicha's stage-5 interactive console."""

from __future__ import annotations

import sys

from yamicha.bootstrap import run_interactive_console


def main() -> int:
    return run_interactive_console(
        input_stream=sys.stdin,
        output_stream=sys.stdout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
