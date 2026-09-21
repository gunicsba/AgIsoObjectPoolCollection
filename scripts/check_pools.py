"""Validate the pool collection. Exit 0 when everything passes, 1 otherwise.

Checks
  * every file in ddop/manifest.csv and iop/manifest.csv exists, and every pool file is listed
  * every .ddop (also in incoming/) parses
  * every .ddop is scrubbed: serial string only '0' or NUL, NAME identity number (bits 0..20) zero
  * .ddop names look like <BRAND>_<MODEL>[_variant].ddop: no spaces, no control characters
  * the manifest columns derived from the pool bytes are current
  * every .iop has a row in iop/SOURCES.md (ref and licence), and, when the source has a licence file,
    its text is in iop/LICENSES/; SOURCES.md never holds an unmasked working-set NAME
  * a merged pool iop/<type>/<stem>.iop is byte for byte its parts iop/<type>/<stem>/<stem>_partNN.iop
  * no stray files, no names that differ only by case (they collide on Windows and macOS)

Duplicate pools (same content hash) are reported as warnings only.

    python scripts/check_pools.py [--root DIR]
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

import build_manifest as bm
import poollib
import sources_md
from attachments import unmasked_names

# <BRAND>_<MODEL>[_variant]: upper-case brand, then letters, digits and underscores.
DDOP_NAME = re.compile(r"^[A-Z0-9]+_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*\.ddop$")
IOP_NAME = re.compile(r"^[A-Z0-9]+_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*\.iop$")

DERIVED_DDOP = ("type", "sections", "functions", "geometry", "section_control",
                "connector_types", "bytes", "language", "strings")
DERIVED_IOP = ("type", "bytes", "sha256", "parts")


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str):
        self.errors.append(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)


def printable(name: str) -> str:
    """Show control characters and spaces visibly in messages."""
    return name.encode("unicode_escape").decode("ascii").replace(" ", "\\x20")


def check_case_collisions(names: list[str], rep: Report, where: str):
    seen = defaultdict(list)
    for n in names:
        seen[n.lower()].append(n)
    for group in seen.values():
        if len(group) > 1:
            rep.error("%s: names differ only by case: %s" % (where, ", ".join(map(printable, group))))


def check_manifest_rows(root: Path, sub: str, suffix: str, rows: list[dict], columns: list[str],
                        derived: tuple, make_row, rep: Report) -> set[str]:
    directory = root / sub
    manifest = directory / "manifest.csv"
    label = "%s/manifest.csv" % sub
    if not manifest.is_file():
        rep.error("%s is missing" % label)
        return set()
    first = manifest.read_text(encoding="utf-8").split("\n", 1)[0].rstrip("\r")
    if first != ",".join(columns):
        rep.error("%s: header is %r, expected %r" % (label, printable(first), ",".join(columns)))
    listed: dict[str, dict] = {}
    for r in rows:
        f = r.get("file") or ""
        if f in listed:
            rep.error("%s: %s is listed twice" % (label, printable(f)))
        listed[f] = r
    on_disk = set(bm.pool_files(directory, suffix))
    for f in sorted(listed):
        if f not in on_disk:
            rep.error("%s lists %s, which does not exist" % (label, printable(f)))
    for f in sorted(on_disk - set(listed)):
        rep.error("%s/%s exists but is not in %s" % (sub, printable(f), label))
    for f in sorted(on_disk & set(listed)):
        try:
            data = (directory / f).read_bytes()
            expected = make_row(f, data, listed[f], sorted(on_disk))
        except poollib.PoolError:
            continue  # reported by the parse check
        for col in derived:
            if str(listed[f].get(col, "")) != str(expected[col]):
                rep.error("%s: %s: column %s is %r but the file gives %r (run scripts/build_manifest.py)"
                          % (label, printable(f), col, listed[f].get(col), str(expected[col])))
    return on_disk


def check_ddops(root: Path, rep: Report) -> int:
    ddop_dir = root / "ddop"
    files = [("ddop", f) for f in bm.pool_files(ddop_dir, ".ddop")]
    incoming = root / "incoming"
    if incoming.is_dir():
        files += [("incoming", f) for f in bm.pool_files(incoming, ".ddop")]
    hashes = defaultdict(list)
    for base, rel in files:
        shown = "%s/%s" % (base, printable(rel))
        parts = rel.split("/")
        if base == "ddop":
            if len(parts) != 2 or parts[0] not in bm.POOL_TYPES:
                rep.error("%s: must be ddop/<%s>/<file>.ddop" % (shown, "|".join(bm.POOL_TYPES)))
            if not DDOP_NAME.match(parts[-1]):
                rep.error("%s: name must match <BRAND>_<MODEL>[_variant].ddop "
                          "(A-Z0-9 brand, then letters, digits, underscores; no spaces or control characters)" % shown)
        data = (root / base / rel).read_bytes()
        try:
            problems = poollib.scrub_problems(data)
        except poollib.PoolError as e:
            rep.error("%s: does not parse: %s" % (shown, e))
            continue
        for p in problems:
            rep.error("%s: NOT SCRUBBED: %s" % (shown, p))
        if not problems:
            hashes[poollib.content_hash(data)].append(shown)
    for group in hashes.values():
        if len(group) > 1:
            rep.warn("same pool (ignoring serial and localization label): %s" % ", ".join(group))
    check_case_collisions([f for b, f in files if b == "ddop"], rep, "ddop")
    return len(files)


def check_stray(root: Path, rep: Report):
    def stray(directory: Path, allowed):
        if not directory.is_dir():
            return
        for p in sorted(directory.rglob("*")):
            if p.is_file() and not allowed(p.relative_to(directory).as_posix()):
                rep.error("%s/%s: unexpected file" % (directory.name, printable(p.relative_to(directory).as_posix())))
    stray(root / "ddop", lambda r: r == "manifest.csv" or r.lower().endswith(".ddop"))
    stray(root / "iop", lambda r: r in ("manifest.csv", "SOURCES.md") or r.lower().endswith(".iop")
          or (r.startswith("LICENSES/") and r.count("/") == 1 and r.endswith(".txt")))
    stray(root / "incoming", lambda r: r == "REPORT.md" or r.lower().endswith(".ddop"))


def sources_md_licence(repo: str) -> str:
    """Where the licence text of a source repository is kept, below iop/."""
    return "LICENSES/%s.txt" % repo.replace("/", "_")


def check_merged_pools(iop_dir: Path, files: list[str], rep: Report):
    """Every <type>/<stem>/ folder of parts needs <type>/<stem>.iop, byte for byte their concatenation."""
    folders = defaultdict(list)
    for f in files:
        p = f.split("/")
        if len(p) == 3:
            folders[(p[0], p[1])].append(p[2])
    for (ptype, stem), names in sorted(folders.items()):
        shown = "iop/%s/%s" % (printable(ptype), printable(stem))
        numbers = sorted(int(bm.PART_NAME.match(n)["n"]) for n in names if bm.PART_NAME.match(n))
        if numbers != list(range(len(numbers))) or len(numbers) < 2:
            rep.error("%s/: parts must be numbered part00, part01, ... without gaps, and there must be at least two" % shown)
            continue
        merged = iop_dir / ptype / (stem + ".iop")
        if not merged.is_file():
            rep.error("%s/: has parts but %s.iop (the merged pool) does not exist" % (shown, shown))
            continue
        width = len(bm.PART_NAME.match(sorted(names)[0])["n"])
        joined = b"".join((iop_dir / ptype / stem / ("%s_part%0*d.iop" % (stem, width, n))).read_bytes() for n in numbers)
        if joined != merged.read_bytes():
            rep.error("%s.iop is not the concatenation of the files in %s/ (in part order)" % (shown, shown))


def check_iops(root: Path, rep: Report) -> int:
    iop_dir = root / "iop"
    files = bm.pool_files(iop_dir, ".iop") if iop_dir.is_dir() else []
    for f in files:
        parts = f.split("/")
        shown = "iop/%s" % printable(f)
        if len(parts) not in (2, 3) or parts[0] not in bm.POOL_TYPES:
            rep.error("%s: must be iop/<%s>/<file>.iop, or iop/<type>/<stem>/<stem>_partNN.iop for the parts of a merged pool"
                      % (shown, "|".join(bm.POOL_TYPES)))
        elif len(parts) == 3:
            m = bm.PART_NAME.match(parts[2])
            if not m or m["stem"] != parts[1]:
                rep.error("%s: files in iop/<type>/<stem>/ must be named <stem>_partNN.iop" % shown)
        if not IOP_NAME.match(parts[-1]):
            rep.error("%s: name must match <BRAND>_<MODEL>[_variant].iop "
                      "(A-Z0-9 brand, then letters, digits, underscores; no spaces or control characters)" % shown)
    check_merged_pools(iop_dir, files, rep)
    sources_file = iop_dir / "SOURCES.md"
    if files and not sources_file.is_file():
        rep.error("iop/SOURCES.md is missing")
        return len(files)
    sources_text = sources_file.read_text(encoding="utf-8") if sources_file.is_file() else ""
    for name in unmasked_names(sources_text):
        rep.error("iop/SOURCES.md contains the working-set NAME %s with a non-zero identity number "
                  "(zero NAME bits 0..20 in anything recorded here)" % name)
    rows = sources_md.parse(sources_text)
    listed = {r["file"] for r in rows}
    for f in files:
        if f not in listed:
            rep.error("iop/%s has no row in iop/SOURCES.md" % printable(f))
    for f in sorted(listed - set(files)):
        rep.error("iop/SOURCES.md lists %s, which does not exist" % printable(f))
    for repo in sorted({r["repo"] for r in rows if r["licence"] != "unspecified"}):
        if not (iop_dir / sources_md_licence(repo)).is_file():
            rep.error("iop/%s is missing (keep the source repository's licence text; %s is listed in SOURCES.md)"
                      % (sources_md_licence(repo), repo))
    check_case_collisions(files, rep, "iop")
    return len(files)


def run(root: Path) -> Report:
    rep = Report()
    ddop_rows = bm.read_manifest(root / "ddop" / "manifest.csv")
    iop_rows = bm.read_manifest(root / "iop" / "manifest.csv")
    check_manifest_rows(root, "ddop", ".ddop", ddop_rows, bm.DDOP_COLUMNS, DERIVED_DDOP, bm.ddop_row, rep)
    for r in ddop_rows:
        if r.get("type") not in bm.POOL_TYPES:
            rep.error("ddop/manifest.csv: %s: unknown type %r" % (printable(r.get("file") or ""), r.get("type")))
    for r in iop_rows:
        if r.get("type") not in bm.POOL_TYPES:
            rep.error("iop/manifest.csv: %s: unknown type %r" % (printable(r.get("file") or ""), r.get("type")))
    check_manifest_rows(root, "iop", ".iop", iop_rows, bm.IOP_COLUMNS, DERIVED_IOP, bm.iop_row, rep)
    n_ddop = check_ddops(root, rep)
    n_iop = check_iops(root, rep)
    check_stray(root, rep)
    rep.counts = (n_ddop, n_iop)
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=bm.ROOT, help="repository root (default: this repository)")
    args = ap.parse_args(argv)
    rep = run(args.root)
    for w in rep.warnings:
        print("warning:", w)
    for e in rep.errors:
        print("error:", e)
    n_ddop, n_iop = rep.counts
    if rep.errors:
        print("FAILED: %d error(s) in %d ddop and %d iop file(s)" % (len(rep.errors), n_ddop, n_iop))
        return 1
    print("OK: %d ddop and %d iop file(s) checked, %d warning(s)" % (n_ddop, n_iop, len(rep.warnings)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
