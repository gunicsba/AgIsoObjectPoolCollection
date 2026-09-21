"""Read and write iop/SOURCES.md, the per-file provenance table for the iop/ tree."""
from __future__ import annotations

import re

COLUMNS = ("file", "repo", "path", "ref", "licence")

HEADER = """# IOP sources

Every `.iop` under `iop/` is copied byte for byte from where the table says. `Ref` is the commit
SHA for files taken from a repository, or `#N` (the issue) for files taken from an issue attachment;
for those `Path` is the attachment URL, then `!` and the path inside the zip, with the identity number
in any working-set NAME zeroed. A merged pool (`<stem>.iop`) is the concatenation of the files in
`<stem>/`; its `Path` says so. The licence column is what the source repository's licence file says
(`unspecified`: it has none); it is recorded for information and its text, when there is one, is kept
in `iop/LICENSES/`. This table is maintained by `scripts/import_iops.py` and
`scripts/import_iops_from_issues.py`; do not edit it by hand.

| File | Repo | Path | Ref | Licence |
|---|---|---|---|---|
"""

_ROW = re.compile(r"^\|\s*`([^`|]+)`\s*\|\s*([^|]+?)\s*\|\s*`([^`|]+)`\s*\|\s*`([^`|]+)`\s*\|\s*([^|]+?)\s*\|\s*$")


def parse(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        m = _ROW.match(line)
        if m:
            rows.append(dict(zip(COLUMNS, m.groups())))
    return rows


def render(rows: list[dict]) -> str:
    out = [HEADER]
    for r in sorted(rows, key=lambda r: r["file"]):
        out.append("| `%(file)s` | %(repo)s | `%(path)s` | `%(ref)s` | %(licence)s |\n" % r)
    return "".join(out)
