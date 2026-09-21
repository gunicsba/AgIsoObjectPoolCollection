"""Regenerate ddop/manifest.csv and iop/manifest.csv from the files on disk.

Derived columns are recomputed from the pool bytes. The hand-written columns (source, note) are
kept from the existing manifest, keyed by file, so this is safe to re-run. New files get empty
source and note: fill them in.

    python scripts/build_manifest.py            rewrite the manifests
    python scripts/build_manifest.py --check    exit 1 if a manifest is out of date, change nothing
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import re
import sys
from pathlib import Path

import poollib

ROOT = Path(__file__).resolve().parent.parent

POOL_TYPES = ("sprayer", "spreader", "seeder", "combined", "tractor", "unsorted")
DDOP_COLUMNS = ["file", "type", "sections", "functions", "geometry", "section_control",
                "connector_types", "bytes", "language", "strings", "source", "note"]
IOP_COLUMNS = ["file", "type", "bytes", "sha256", "parts", "note"]

# A pool that came in several files: iop/<type>/<stem>/<stem>_partNN.iop hold the parts, and
# iop/<type>/<stem>.iop is their concatenation in part order.
PART_NAME = re.compile(r"^(?P<stem>.+)_part(?P<n>\d{2,})\.iop$")


def pool_files(directory: Path, suffix: str) -> list[str]:
    """Paths relative to directory, always with forward slashes, sorted."""
    return sorted(p.relative_to(directory).as_posix() for p in directory.rglob("*")
                  if p.is_file() and p.suffix.lower() == suffix)


def read_manifest(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def render_manifest(columns: list[str], rows: list[dict]) -> str:
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(sorted(rows, key=lambda r: r["file"]))
    return out.getvalue()


def ddop_row(rel: str, data: bytes, kept: dict, files: list[str] | None = None) -> dict:
    s = poollib.summarize(data)
    return {
        "file": rel,
        "type": rel.split("/", 1)[0],
        "sections": s["sections"],
        "functions": s["functions"],
        "geometry": s["geometry"],
        "section_control": s["section_control"],
        "connector_types": " ".join(map(str, s["connector_types"])) or "-",
        "bytes": s["bytes"],
        "language": s["language"],
        "strings": s["strings"],
        "source": kept.get("source", ""),
        "note": kept.get("note", ""),
    }


def iop_parts(rel: str, files: list[str]) -> str:
    """'part' for a part file, the number of parts for a merged pool, '-' for a single file."""
    if rel.count("/") == 2:
        return "part"
    n = sum(1 for f in files if f.startswith(rel[:-4] + "/"))
    return str(n) if n else "-"


def iop_row(rel: str, data: bytes, kept: dict, files: list[str] | None = None) -> dict:
    return {
        "file": rel,
        "type": rel.split("/", 1)[0],
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "parts": iop_parts(rel, files or []),
        "note": kept.get("note", ""),
    }


def build(root: Path) -> dict[Path, str]:
    """Return {manifest path: expected text}. Raises poollib.PoolError for an unparseable pool."""
    result = {}
    for sub, suffix, columns, make_row in (
            ("ddop", ".ddop", DDOP_COLUMNS, ddop_row), ("iop", ".iop", IOP_COLUMNS, iop_row)):
        directory = root / sub
        manifest = directory / "manifest.csv"
        kept = {r["file"]: r for r in read_manifest(manifest)}
        rows = []
        rels = pool_files(directory, suffix)
        for rel in rels:
            try:
                rows.append(make_row(rel, (directory / rel).read_bytes(), kept.get(rel, {}), rels))
            except poollib.PoolError as e:
                raise poollib.PoolError("%s/%s: %s" % (sub, rel, e)) from e
        result[manifest] = render_manifest(columns, rows)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this repository)")
    ap.add_argument("--check", action="store_true", help="do not write; exit 1 if out of date")
    args = ap.parse_args(argv)
    try:
        expected = build(args.root)
    except poollib.PoolError as e:
        print("error:", e, file=sys.stderr)
        return 1
    stale = 0
    for path, text in expected.items():
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        name = path.relative_to(args.root).as_posix()
        if current == text:
            print("up to date:", name)
        elif args.check:
            print("OUT OF DATE:", name, "(run scripts/build_manifest.py)")
            stale += 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            print("wrote:", name)
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
