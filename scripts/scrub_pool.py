"""Scrub a DDOP before you share it: serial number string set to '0', NAME identity number zeroed.

    python scripts/scrub_pool.py machine.ddop            writes machine.scrubbed.ddop next to it
    python scripts/scrub_pool.py machine.ddop -o out.ddop

Length and layout are unchanged. The original is never modified. A file that is not a parseable
DDOP is refused rather than copied.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import poollib


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pool", type=Path)
    ap.add_argument("-o", "--output", type=Path, help="where to write (default: <name>.scrubbed.ddop)")
    args = ap.parse_args(argv)
    out = args.output or args.pool.with_name(args.pool.stem + ".scrubbed.ddop")
    if out.resolve() == args.pool.resolve():
        print("error: refusing to overwrite the original", file=sys.stderr)
        return 1
    try:
        poollib.write_scrubbed(out, args.pool.read_bytes())
    except poollib.PoolError as e:
        print("error: %s: %s" % (args.pool, e), file=sys.stderr)
        return 1
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
