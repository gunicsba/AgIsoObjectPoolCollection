"""Collect DDOPs attached to a repository's issues and stage them, scrubbed, in incoming/.

For every issue body and comment it finds github.com/user-attachments/ links, downloads them
(no token needed), looks inside zips (also zips inside zips), and treats any file that starts with
the 'DVC' magic as a DDOP, whatever its name; some real pools have no extension at all.

Nothing is written unscrubbed: a pool that cannot be parsed cannot be scrubbed, so it is reported
and dropped. Output file names come from the content hash, never from source names (localization
labels can hold control characters, see AOG-TaskController issue #61).

Writes, under --root:
    incoming/<hash>.ddop     one file per new, scrubbed pool
    incoming/REPORT.md       what was found, where it came from, what was skipped

Pools already in ddop/ or incoming/ are skipped by content hash (serial and localization label
ignored). If nothing new is found, nothing is written.

Live:    python scripts/import_ddops.py --repo AgOpenGPS-Official/AOG-TaskController
Offline: python scripts/import_ddops.py --fixtures DIR      (DIR/issues.json + DIR/attachments.json)
Needs the gh CLI for live runs. On Windows set PYTHONUTF8=1.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import build_manifest as bm
import poollib
from attachments import (ATTACHMENT_URL, IGNORED_SUFFIXES, REPO_NAME, DownloadError, FixtureSource,  # noqa: F401
                         GhSource, Reference, cell, find_references, mask_names, parse_numbers, parse_since,
                         scan_bytes, url_suffix)

DEFAULT_REPO = "AgOpenGPS-Official/AOG-TaskController"


def ddop_wanted(name: str, head: bytes) -> bool:
    """A DDOP is recognised by its first bytes ('DVC'), whatever the file is called."""
    return head[:3] == poollib.MAGIC


@dataclass
class Candidate:
    name: str
    summary: dict
    ref: Reference
    label: str


@dataclass
class Outcome:
    candidates: list[Candidate] = field(default_factory=list)
    duplicates: list[tuple[str, str, Reference]] = field(default_factory=list)  # label, duplicate of, ref
    rejected: list[tuple[str, str, Reference]] = field(default_factory=list)    # label, reason, ref
    failed: list[tuple[str, str, Reference]] = field(default_factory=list)      # url, reason, ref
    stats: Counter = field(default_factory=Counter)


def render_report(o: Outcome, repo: str, numbers: list[int], since: datetime | None, n_refs: int) -> str:
    scope = ["all issues" if not numbers else "issues " + ", ".join("#%d" % n for n in numbers)]
    if since:
        scope.append("since " + since.date().isoformat())
    out = ["# Incoming DDOP candidates\n",
           "Source: `%s` (%s). %d attachment link(s) examined.\n" % (repo, ", ".join(scope), n_refs),
           "Every pool below was scrubbed before it was written (serial number string set to `0`, "
           "NAME identity number zeroed). Review each one, then move it to `ddop/<type>/<BRAND>_<MODEL>.ddop`, "
           "run `python scripts/build_manifest.py`, and fill in `source` and `note`.\n",
           "## Candidates (%d)\n" % len(o.candidates),
           "| File | Designator | Software version | Sections | Geometry | Section control | Language | Strings | Source issue | Attachment |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for c in o.candidates:
        s = c.summary
        out.append("| `%s` | %s | %s | %d | %s | %s | %s | %s | [#%d](%s) | %s |" % (
            c.name, cell(s["designator"]), cell(s["software_version"]), s["sections"], s["geometry"],
            s["section_control"], s["language"], s["strings"], c.ref.issue, c.ref.issue_url,
            "%s %s" % (mask_names(c.ref.url), cell(mask_names(c.label.split("!", 1)[1]))) if "!" in c.label else mask_names(c.ref.url)))
    out.append("")
    if any(c.summary["extended_structure_label"] for c in o.candidates):
        out.append("Note: at least one pool has bytes after the localization label, read as a version 4 "
                   "extended structure label. That parsing is untested; check those pools by hand.\n")
    if o.duplicates:
        out += ["## Already in the collection (%d)\n" % len(o.duplicates), "| Found as | Same as | Source issue |", "|---|---|---|"]
        out += ["| %s | `%s` | [#%d](%s) |" % (cell(mask_names(lbl.split("!", 1)[-1])), dup, r.issue, r.issue_url)
                for lbl, dup, r in o.duplicates]
        out.append("")
    if o.rejected or o.failed:
        out += ["## Not staged\n", "| What | Why | Source issue |", "|---|---|---|"]
        out += ["| %s | %s | [#%d](%s) |" % (cell(mask_names(lbl.split("!", 1)[-1])), cell(why), r.issue, r.issue_url)
                for lbl, why, r in o.rejected]
        out += ["| %s | %s | [#%d](%s) |" % (mask_names(url), cell(why), r.issue, r.issue_url) for url, why, r in o.failed]
        out.append("")
    if o.stats:
        out += ["## Skipped\n"] + ["- %d x %s" % (n, why) for why, n in sorted(o.stats.items())] + [""]
    return "\n".join(out)


# --- the import ------------------------------------------------------------------------------

def known_hashes(root: Path) -> dict[str, str]:
    """content hash -> path relative to root, for everything already in ddop/ and incoming/."""
    known = {}
    for base in ("ddop", "incoming"):
        directory = root / base
        if not directory.is_dir():
            continue
        for rel in bm.pool_files(directory, ".ddop"):
            try:
                known.setdefault(poollib.content_hash((directory / rel).read_bytes()), "%s/%s" % (base, rel))
            except poollib.PoolError as e:
                print("warning: %s/%s does not parse (%s); not used for duplicate detection" % (base, rel, e))
    return known


def run(source, root: Path, numbers: list[int], since: datetime | None, repo: str, max_bytes: int) -> Outcome:
    issues = source.issues(numbers, since)
    refs = find_references(issues, since)
    print("%d issue(s), %d attachment link(s)" % (len(issues), len(refs)))
    known = known_hashes(root)
    outcome = Outcome()
    staged: list[tuple[str, bytes]] = []
    for ref in refs:
        if url_suffix(ref.url) in IGNORED_SUFFIXES:
            outcome.stats["log, trace, installer or media file"] += 1
            continue
        try:
            data = source.download(ref.url)
        except DownloadError as e:
            outcome.failed.append((ref.url, str(e), ref))
            print("skip %s: %r" % (ref.url, str(e)))
            continue
        for found in scan_bytes(data, ref.url, ref.url.rsplit("/", 1)[-1], max_bytes, outcome.stats, ddop_wanted):
            try:
                digest = poollib.content_hash(found.data)
                clean = poollib.scrub(found.data)
                summary = poollib.summarize(clean)
            except poollib.PoolError as e:
                outcome.rejected.append((found.label, "not a parseable DDOP, so it cannot be scrubbed: %s" % e, ref))
                continue
            name = "%s.ddop" % digest[:16]
            if digest in known:
                outcome.duplicates.append((found.label, known[digest], ref))
                continue
            known[digest] = "incoming/" + name
            outcome.candidates.append(Candidate(name, summary, ref, found.label))
            staged.append((name, clean))
            # Pool strings are untrusted: keep them out of the log as raw text (workflow commands start with '::').
            print("candidate %s: designator %r, %d sections, %s" % (name, summary["designator"][:60], summary["sections"], summary["geometry"]))
    if staged:
        incoming = root / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        for name, clean in staged:
            path = incoming / name
            poollib.write_scrubbed(path, clean)
            problems = poollib.scrub_problems(path.read_bytes())
            if problems:  # cannot happen; never leave the file behind if it does
                path.unlink()
                raise SystemExit("%s failed the scrub check after writing: %s" % (name, problems))
        report = render_report(outcome, repo, numbers, since, len(refs))
        (incoming / "REPORT.md").write_text(report, encoding="utf-8", newline="\n")
    print("%d new candidate(s), %d duplicate(s), %d rejected, %d download(s) failed" % (
        len(outcome.candidates), len(outcome.duplicates), len(outcome.rejected), len(outcome.failed)))
    for why, n in sorted(outcome.stats.items()):
        print("  skipped %d x %s" % (n, why))
    return outcome


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=DEFAULT_REPO, help="source repository, owner/name (default: %(default)s)")
    ap.add_argument("--issues", default="", help="only these issue numbers (comma or space separated)")
    ap.add_argument("--since", default="", help="only issues, comments and updates on or after this date (YYYY-MM-DD)")
    ap.add_argument("--root", type=Path, default=bm.ROOT, help="repository root (default: this repository)")
    ap.add_argument("--fixtures", type=Path, help="read saved issues and attachments from this directory instead of GitHub")
    ap.add_argument("--max-bytes", type=int, default=poollib.MAX_FILE_BYTES, help="skip files larger than this (default: 40 MB)")
    args = ap.parse_args(argv)
    if not REPO_NAME.match(args.repo):
        raise SystemExit("--repo must look like owner/name, got %r" % args.repo)
    numbers = parse_numbers(args.issues)
    since = parse_since(args.since)
    source = FixtureSource(args.fixtures, args.max_bytes) if args.fixtures else GhSource(args.repo, args.max_bytes)
    run(source, args.root, numbers, since, args.repo, args.max_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
