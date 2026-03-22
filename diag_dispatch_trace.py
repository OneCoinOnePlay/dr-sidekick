#!/usr/bin/env python3
"""Inspect SP-303 RDAC MT1 block metadata using the current firmware-grounded scaffold."""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dr_sidekick.engine.core import sp303_inspect_sp0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sp0_path", help="Path to an SP0 file")
    parser.add_argument("--blocks", type=int, default=8, help="Number of blocks to inspect")
    args = parser.parse_args()

    traces = sp303_inspect_sp0(args.sp0_path, max_blocks=args.blocks)
    print(json.dumps(traces, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
