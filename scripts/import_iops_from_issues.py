"""Collect VT object pools (.iop) attached to a repository's issues and add them to iop/.

Reads the issues and comments of a repository, downloads their github.com/user-attachments/ links
(no token needed), looks inside zips (also zips inside zips) and takes every file ending in .iop.
IOP files have no magic bytes, so the extension is what identifies them. Logs, traces, images and
installers are skipped, as are files over 40 MB.

The AgIsoVirtualTerminal app writes what an implement uploads as
    iso_data/<working set NAME>/object_pool_<N>.iop   (and object_pool_with_label_<N>.iopx: ignored)
One upload of a large pool arrives as several numbered files. Within each such folder the files are
put in numeric order and a new pool starts at every file whose first object is a Working Set
(object type 0); the files after it, up to the next Working Set, are its parts. A pool of several
parts is stored as
    iop/<type>/<BRAND>_<MODEL>.iop                       the parts concatenated, in order
    iop/<type>/<BRAND>_<MODEL>/<BRAND>_<MODEL>_partNN.iop the parts
and a pool of one file is just iop/<type>/<BRAND>_<MODEL>.iop. Concatenation is not validated
as a VT pool (there is no object parser here): the grouping relies on the Working Set rule, and a
series with more than 32 parts, or with parts before any Working Set, is reported and not stored.

Names: with a working-set NAME the brand is MFG<manufacturer code> and the model VT_<hash>; a lone
.iop uses the repository's first word as brand and its own name (sanitised) as model. Rename later
if the machine is known; check_pools.py checks the result.

Privacy: the NAME folder in the source path carries the machine's identity number. It is zeroed
(bits 0..20) in everything written to iop/SOURCES.md and the report. Object pools can hold text
typed in on the terminal; read the ones you keep.

    python scripts/import_iops_from_issues.py [--repo OWNER/NAME] [--issues N,N] [--since DATE]
    python scripts/import_iops_from_issues.py --fixtures DIR     (saved issues.json + attachments.json)
"""
from __future__ import annotations

import argparse
import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import build_manifest as bm
import poollib
import sources_md
from attachments import (IGNORED_SUFFIXES, REPO_NAME, DownloadError, FixtureSource, Found, GhSource,
                         Reference, cell, find_references, mask_names, parse_numbers, parse_since,
                         scan_bytes, url_suffix)
from import_iops import default_brand, licence_file, model_name  # noqa: F401

DEFAULT_REPO = "Open-Agriculture/AgIsoVirtualTerminal"
MAX_PARTS = 32
SERIES_FILE = re.compile(r"^(?P<dir>(?:.*/)?)object_pool_(?P<n>\d+)\.iop$")
NAME_DIR = re.compile(r"^[0-9a-fA-F]{16}$")
UNSAFE_PATH_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f|`\\�]")
WORKING_SET = 0  # VT object type of the first object of a pool


def iop_wanted(name: str, head: bytes) -> bool:
    return name.lower().endswith(".iop")


def starts_pool(data: bytes) -> bool:
    """First object is a Working Set: object id (2 bytes), then the object type (0)."""
    return len(data) >= 3 and data[2] == WORKING_SET


def mask_name(component: str) -> str:
    """Zero the identity number (bits 0..20) of a working-set NAME written as 16 hex digits."""
    if not NAME_DIR.match(component):
        return component
    return "%016x" % (int(component, 16) & ~poollib.IDENTITY_NUMBER_MASK)


def masked_path(label: str) -> str:
    """Attachment URL plus the path inside each zip, safe to record: NAMEs masked, odd characters replaced."""
    # Every 16-hex-digit token, not only folder names: users name their zips after the NAME too.
    return UNSAFE_PATH_CHARS.sub("_", mask_names(label))


@dataclass
class Part:
    data: bytes
    found: Found
    ref: Reference


@dataclass
class Pool:
    parts: list[Part]
    name: str | None      # working-set NAME as found (only used for the manufacturer code)
    stem: str             # source file stem, for a lone file

    @property
    def data(self) -> bytes:
        return b"".join(p.data for p in self.parts)


@dataclass
class Outcome:
    added: list[tuple[str, Pool]] = field(default_factory=list)          # dest file, pool
    duplicates: list[tuple[Pool, str]] = field(default_factory=list)     # pool, existing file
    rejected: list[tuple[str, str, Reference]] = field(default_factory=list)  # what, why, ref
    failed: list[tuple[str, str, Reference]] = field(default_factory=list)
    stats: Counter = field(default_factory=Counter)
    n_refs: int = 0


def group_pools(found: list[tuple[Found, Reference]], out: Outcome) -> list[Pool]:
    """Series of object_pool_N.iop become pools of parts; other .iop files are pools of one file."""
    series: dict[tuple[str, str], list[tuple[int, Part]]] = defaultdict(list)
    pools: list[Pool] = []
    for f, ref in found:
        if not f.data:
            out.stats["empty file"] += 1
            continue
        m = SERIES_FILE.match(f.member)
        part = Part(f.data, f, ref)
        if m:
            container = f.label[:len(f.label) - len(f.member)]
            series[(container, m["dir"])].append((int(m["n"]), part))
        elif starts_pool(f.data):
            pools.append(Pool([part], None, Path(f.member).stem))
        else:
            out.rejected.append((f.label, "does not start with a Working Set object", ref))
    for (container, folder), files in series.items():
        files.sort(key=lambda t: t[0])
        last = folder.rstrip("/").rsplit("/", 1)[-1]
        name = last if NAME_DIR.match(last) else None
        groups: list[list[Part]] = []
        for _, part in files:
            if starts_pool(part.data):
                groups.append([part])
            elif groups:
                groups[-1].append(part)
            else:
                out.stats["file before any Working Set (not a pool start)"] += 1
        for g in groups:
            if len(g) > MAX_PARTS:
                out.rejected.append((g[0].found.label, "%d parts in one series (limit %d): probably not one pool"
                                     % (len(g), MAX_PARTS), g[0].ref))
            else:
                pools.append(Pool(g, name, "object_pool"))
    return pools


def existing_pools(root: Path) -> dict[str, str]:
    """sha256 of every whole pool already in iop/ (parts are not pools by themselves) -> its path."""
    known = {}
    directory = root / "iop"
    if directory.is_dir():
        for rel in bm.pool_files(directory, ".iop"):
            if rel.count("/") == 1:
                known.setdefault(hashlib.sha256((directory / rel).read_bytes()).hexdigest(), "iop/" + rel)
    return known


def destination(pool: Pool, digest: str, pool_type: str, brand: str, taken: dict[str, str]) -> str:
    """iop/-relative path of the whole pool. Never built from source names except a lone file's stem."""
    if pool.name:
        code = (int(pool.name, 16) >> 21) & 0x7FF
        b, model = "MFG%d" % code, "VT_" + digest[:8]
    else:
        b, model = brand, model_name(pool.stem, []) or ("VT_" + digest[:8])
    dest = "%s/%s_%s.iop" % (pool_type, b, model)
    if taken.get(dest, digest) != digest:
        dest = "%s/%s_%s_%s.iop" % (pool_type, b, model, digest[:8])
    return dest


def part_path(dest: str, i: int, count: int) -> str:
    stem = dest[:-4]
    return "%s/%s_part%0*d.iop" % (stem, stem.rsplit("/", 1)[1], max(2, len(str(count - 1))), i)


def render_report(o: Outcome, repo: str, n_refs: int) -> str:
    out = ["# Imported IOP pools\n",
           "Source: `%s`, %d attachment link(s) examined. All are in `iop/<type>/` with rows in `iop/SOURCES.md`.\n" % (repo, n_refs),
           "## Added (%d)\n" % len(o.added),
           "| File | Parts | Bytes | Working set NAME (identity zeroed) | Source issue | Attachment |",
           "|---|---|---|---|---|---|"]
    for dest, pool in o.added:
        first = pool.parts[0]
        name = mask_name(pool.name) if pool.name else "-"
        out.append("| `%s` | %d | %d | `%s` | [#%d](%s) | %s |" % (
            dest, len(pool.parts), len(pool.data), name, first.ref.issue, first.ref.issue_url,
            cell(masked_path(first.found.label), 200)))
    out.append("")
    if o.duplicates:
        out += ["## Already in the collection (%d)\n" % len(o.duplicates), "| Source | Same as |", "|---|---|"]
        out += ["| %s | `%s` |" % (cell(masked_path(p.parts[0].found.label), 200), d) for p, d in o.duplicates]
        out.append("")
    if o.rejected or o.failed:
        out += ["## Not added\n", "| What | Why | Source issue |", "|---|---|---|"]
        out += ["| %s | %s | [#%d](%s) |" % (cell(masked_path(what), 200), cell(why, 120), r.issue, r.issue_url)
                for what, why, r in o.rejected + o.failed]
        out.append("")
    if o.stats:
        out += ["## Skipped\n"] + ["- %d x %s" % (n, why) for why, n in sorted(o.stats.items())] + [""]
    return "\n".join(out)


def run(source, root: Path, numbers: list[int], since: datetime | None, repo: str, pool_type: str,
        brand: str, max_bytes: int) -> Outcome:
    issues = source.issues(numbers, since)
    refs = find_references(issues, since)
    print("%d issue(s), %d attachment link(s)" % (len(issues), len(refs)))
    out = Outcome(n_refs=len(refs))
    found: list[tuple[Found, Reference]] = []
    for ref in refs:
        if url_suffix(ref.url) in IGNORED_SUFFIXES:
            out.stats["log, trace, installer or media file"] += 1
            continue
        try:
            data = source.download(ref.url)
        except DownloadError as e:
            out.failed.append((ref.url, str(e), ref))
            print("skip %s: %r" % (ref.url, str(e)))
            continue
        for f in scan_bytes(data, ref.url, ref.url.rsplit("/", 1)[-1], max_bytes, out.stats, iop_wanted):
            found.append((f, ref))

    known = existing_pools(root)
    taken = {v[len("iop/"):]: k for k, v in known.items()}
    sources_file = root / "iop" / "SOURCES.md"
    rows = {r["file"]: r for r in sources_md.parse(sources_file.read_text(encoding="utf-8"))} \
        if sources_file.is_file() else {}
    for pool in group_pools(found, out):
        whole = pool.data
        digest = hashlib.sha256(whole).hexdigest()
        if len(whole) > max_bytes:
            out.rejected.append((pool.parts[0].found.label, "merged pool over the size limit", pool.parts[0].ref))
            continue
        if digest in known:
            out.duplicates.append((pool, known[digest]))
            continue
        dest = destination(pool, digest, pool_type, brand, taken)
        taken[dest] = digest
        known[digest] = "iop/" + dest
        out.added.append((dest, pool))
        target = root / "iop" / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(whole)
        first = pool.parts[0]
        many = len(pool.parts) > 1
        rows[dest] = {"file": dest, "repo": repo, "ref": "#%d" % first.ref.issue, "licence": "unspecified",
                      "path": ("concatenation of the files in %s/, in part order" % dest[:-4].rsplit("/", 1)[1])
                      if many else masked_path(first.found.label)}
        if many:
            for i, part in enumerate(pool.parts):
                rel = part_path(dest, i, len(pool.parts))
                (root / "iop" / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / "iop" / rel).write_bytes(part.data)
                rows[rel] = {"file": rel, "repo": repo, "ref": "#%d" % part.ref.issue, "licence": "unspecified",
                             "path": masked_path(part.found.label)}
        print("added %s: %d part(s), %d bytes" % (dest, len(pool.parts), len(whole)))
    if out.added:
        sources_file.parent.mkdir(parents=True, exist_ok=True)
        sources_file.write_text(sources_md.render(list(rows.values())), encoding="utf-8", newline="\n")
        manifest = root / "iop" / "manifest.csv"
        manifest.write_text(bm.build(root)[manifest], encoding="utf-8", newline="\n")
    print("%d added, %d duplicate(s), %d not added, %d download(s) failed" % (
        len(out.added), len(out.duplicates), len(out.rejected), len(out.failed)))
    for why, n in sorted(out.stats.items()):
        print("  skipped %d x %s" % (n, why))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=DEFAULT_REPO, help="repository whose issues to read (default: %(default)s)")
    ap.add_argument("--issues", default="", help="only these issue numbers (comma or space separated)")
    ap.add_argument("--since", default="", help="only issues, comments and updates on or after this date (YYYY-MM-DD)")
    ap.add_argument("--type", default="unsorted", choices=bm.POOL_TYPES, dest="pool_type", help="folder under iop/ (default: %(default)s)")
    ap.add_argument("--brand", help="BRAND for lone .iop files, A-Z and 0-9 (default: first word of the repository name)")
    ap.add_argument("--root", type=Path, default=bm.ROOT, help="repository root (default: this repository)")
    ap.add_argument("--fixtures", type=Path, help="read saved issues and attachments from this directory instead of GitHub")
    ap.add_argument("--max-bytes", type=int, default=poollib.MAX_FILE_BYTES, help="skip files larger than this (default: 40 MB)")
    ap.add_argument("--report", type=Path, help="also write a markdown report here (put it outside the repository)")
    args = ap.parse_args(argv)
    if not REPO_NAME.match(args.repo):
        raise SystemExit("--repo must look like owner/name, got %r" % args.repo)
    brand = args.brand or default_brand(args.repo)
    if not re.match(r"^[A-Z0-9]+$", brand):
        raise SystemExit("--brand must be upper case letters and digits, got %r" % brand)
    source = FixtureSource(args.fixtures, args.max_bytes) if args.fixtures else GhSource(args.repo, args.max_bytes)
    numbers = parse_numbers(args.issues)
    out = run(source, args.root, numbers, parse_since(args.since), args.repo, args.pool_type, brand, args.max_bytes)
    if args.report:
        args.report.write_text(render_report(out, args.repo, out.n_refs), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
