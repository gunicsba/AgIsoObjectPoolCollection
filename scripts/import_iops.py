"""Copy .iop (VT object pool) files from a source repository into iop/<type>/<BRAND>_<MODEL>.iop.

Fetches one commit of the source repository with git and copies every tracked file matching --glob
byte for byte. Files are named like the DDOPs: BRAND defaults to the first word of the repository
name (AgIsoStack-plus-plus -> AGISOSTACK), MODEL is the source path below the glob's fixed prefix
(examples/vt_a/object_pool.iop with 'examples/**/*.iop' -> vt_a_object_pool), and the folder is
--type (default unsorted). There is no per-repository directory; iop/SOURCES.md records repo, path,
commit SHA and licence for every file, the repository's licence text is kept as
iop/LICENSES/<owner>_<name>.txt, and iop/manifest.csv is refreshed.

The source repository's licence is recorded, never enforced: iop/SOURCES.md gets what its licence file
says (MIT, GPL-3.0, ...), 'unrecognised' or 'unspecified' (no licence file), and a note is printed for
anything that is not permissive. Object pools rarely have a licence of their own, and that of the
repository they are collected in is unrelated. --licence names an unrecognised licence text.

    python scripts/import_iops.py [--repo OWNER/NAME] [--ref REF] [--glob PATTERN]

Glob (case-insensitive): '*' and '?' stay within one path component, '**/' matches any number of
directories.
In names, anything outside A-Z a-z 0-9 becomes '_'. Two source files that map to the same name stop
the import; so does a name already used by a file from another repository.
Files removed upstream are left alone. Needs git on PATH. On Windows set PYTHONUTF8=1.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path

import build_manifest as bm
import poollib
import sources_md

DEFAULT_REPO = "Open-Agriculture/AgIsoStack-plus-plus"
DEFAULT_GLOB = "examples/**/*.iop"
REPO_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REF_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./@{}^~+-]*$")
BRAND_NAME = re.compile(r"^[A-Z0-9]+$")
# Printable, and safe inside a markdown code span in a table cell.
SAFE_SOURCE_PATH = re.compile(r"^[^\x00-\x1f\x7f-\x9f|`\\\ufffd]+$")
LICENCE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "COPYING")
PERMISSIVE = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Unlicense", "CC0-1.0"}


def glob_regex(pattern: str) -> re.Pattern:
    if pattern.startswith("/") or ".." in pattern.split("/") or "\\" in pattern:
        raise SystemExit("--glob must be a relative, forward-slash pattern without '..', got %r" % pattern)
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


def detect_licence(text: str) -> str | None:
    """SPDX id for the common licences, from their standard text; None when unrecognised."""
    t = " ".join(text.split()).lower()
    if "gnu affero general public license" in t:
        return "AGPL-3.0" if "version 3" in t else "AGPL"
    if "gnu lesser general public license" in t:
        return "LGPL-3.0" if "version 3" in t else "LGPL"
    if "gnu general public license" in t:
        return "GPL-3.0" if "version 3" in t else "GPL-2.0" if "version 2" in t else "GPL"
    if "apache license" in t and "version 2.0" in t:
        return "Apache-2.0"
    if "permission is hereby granted, free of charge" in t and "without restriction" in t:
        return "MIT"
    if "redistribution and use in source and binary forms" in t:
        return "BSD-3-Clause" if "neither the name of" in t else "BSD-2-Clause"
    if "permission to use, copy, modify, and/or distribute" in t:
        return "ISC"
    if "this is free and unencumbered software released into the public domain" in t:
        return "Unlicense"
    if "cc0" in t and "public domain dedication" in t:
        return "CC0-1.0"
    return None


def default_brand(repo: str) -> str:
    """First word of the repository name, upper case letters and digits only."""
    return re.sub(r"[^A-Z0-9]", "", repo.split("/", 1)[1].split("-", 1)[0].upper())


def fixed_prefix(pattern: str) -> list[str]:
    """Leading directories of a glob that contain no wildcard."""
    prefix = []
    for part in pattern.split("/")[:-1]:
        if any(c in part for c in "*?"):
            break
        prefix.append(part)
    return prefix


def model_name(path: str, prefix: list[str]) -> str:
    """Source path below the glob's fixed prefix, without extension, as MODEL_PARTS."""
    parts = path.split("/")
    if parts[:len(prefix)] == prefix:
        parts = parts[len(prefix):]
    parts[-1] = parts[-1].rsplit(".", 1)[0]
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]", "_", "_".join(parts))).strip("_")


def licence_file(repo: str) -> str:
    return "LICENSES/%s.txt" % repo.replace("/", "_")


def git(cwd: Path, *args: str, check: bool = True) -> bytes:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, env=env)
    if check and proc.returncode != 0:
        raise SystemExit("git %s failed: %s" % (args[0], proc.stderr.decode("utf-8", "replace").strip()))
    return proc.stdout


def list_tree(work: Path) -> list[tuple[str, str, int]]:
    """(path, blob sha, size) for every regular file in the fetched commit."""
    entries = []
    for rec in git(work, "ls-tree", "-r", "-l", "-z", "FETCH_HEAD").split(b"\0"):
        if not rec:
            continue
        meta, _, path = rec.partition(b"\t")
        mode, kind, sha, size = meta.decode("ascii").split()
        if kind == "blob" and mode in ("100644", "100755"):
            entries.append((path.decode("utf-8", "replace"), sha, int(size)))
    return entries


def blob(work: Path, sha: str) -> bytes:
    return git(work, "cat-file", "blob", sha)


def run(root: Path, repo: str, ref: str, pattern: str, pool_type: str, brand: str,
        licence_override: str | None, remote: str | None, max_bytes: int) -> int:
    matcher = glob_regex(pattern)
    prefix = fixed_prefix(pattern)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        git(work, "init", "--quiet")
        git(work, "fetch", "--quiet", "--depth", "1", remote or "https://github.com/%s.git" % repo, ref)
        commit = git(work, "rev-parse", "FETCH_HEAD^{commit}").decode("ascii").strip()
        tree = list_tree(work)

        # The source repository's licence is recorded for information; it never stops an import.
        # Object pools rarely carry a licence of their own, and it is not the licence of this repository.
        licence_path = next((p for want in LICENCE_FILES for p, _, _ in tree if p == want), None)
        licence_text = None
        if licence_path is None:
            licence = licence_override or "unspecified"
        else:
            licence_text = blob(work, next(s for p, s, _ in tree if p == licence_path))
            licence = detect_licence(licence_text.decode("utf-8", "replace")) or licence_override or "unrecognised"
        if licence not in PERMISSIVE:
            print("note: licence of %s is %s; recorded in iop/SOURCES.md, not enforced" % (repo, licence))

        sources_file = root / "iop" / "SOURCES.md"
        rows = {r["file"]: r for r in sources_md.parse(sources_file.read_text(encoding="utf-8"))} \
            if sources_file.is_file() else {}

        plan: dict[str, tuple[str, str, bytes]] = {}  # dest -> (source path, source sha, data)
        skipped = 0
        for path, sha, size in tree:
            if not path.lower().endswith(".iop") or not matcher.match(path):
                continue
            if not SAFE_SOURCE_PATH.match(path) or size > max_bytes or size == 0:
                print("skip %r: unusual name, empty or over the size limit" % path)
                skipped += 1
                continue
            model = model_name(path, prefix)
            if not model:
                print("skip %r: no usable name" % path)
                skipped += 1
                continue
            dest = "%s/%s_%s.iop" % (pool_type, brand, model)
            if dest in plan:
                raise SystemExit("%r and %r would both be written to iop/%s; narrow --glob or use --brand"
                                 % (plan[dest][0], path, dest))
            if dest in rows and rows[dest]["repo"] != repo:
                raise SystemExit("iop/%s already holds a file from %s; use --brand or --type to name these differently"
                                 % (dest, rows[dest]["repo"]))
            plan[dest] = (path, sha, blob(work, sha))

    if not plan:
        print("no files match %r in %s at %s" % (pattern, repo, commit))
        return 0

    added = updated = unchanged = 0
    for dest, (path, _, data) in sorted(plan.items()):
        target = root / "iop" / dest
        existing = target.read_bytes() if target.is_file() else None
        if existing is None:
            added += 1
        elif existing != data:
            updated += 1
        else:
            unchanged += 1
        if existing != data:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        rows[dest] = {"file": dest, "repo": repo, "path": path, "ref": commit, "licence": licence}
    if licence_text is not None:
        licence_target = root / "iop" / licence_file(repo)
        licence_target.parent.mkdir(parents=True, exist_ok=True)
        licence_target.write_bytes(licence_text)
    sources_file.parent.mkdir(parents=True, exist_ok=True)
    sources_file.write_text(sources_md.render(list(rows.values())), encoding="utf-8", newline="\n")
    manifest = root / "iop" / "manifest.csv"
    manifest.write_text(bm.build(root)[manifest], encoding="utf-8", newline="\n")
    print("%s at %s (%s): %d added, %d updated, %d unchanged, %d skipped" % (
        repo, commit, licence, added, updated, unchanged, skipped))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=DEFAULT_REPO, help="source repository, owner/name (default: %(default)s)")
    ap.add_argument("--ref", default="HEAD", help="branch, tag or commit to copy from (default: the default branch)")
    ap.add_argument("--glob", default=DEFAULT_GLOB, help="which files to copy (default: %(default)s)")
    ap.add_argument("--type", default="unsorted", choices=bm.POOL_TYPES, dest="pool_type",
                    help="folder under iop/ (default: %(default)s)")
    ap.add_argument("--brand", help="BRAND in the file names, A-Z and 0-9 (default: first word of the repository name)")
    ap.add_argument("--licence", help="licence to record when the licence text is not recognised, or there is none")
    ap.add_argument("--remote", help="fetch from this URL or path instead of GitHub (for tests)")
    ap.add_argument("--root", type=Path, default=bm.ROOT, help="repository root (default: this repository)")
    ap.add_argument("--max-bytes", type=int, default=poollib.MAX_FILE_BYTES, help="skip files larger than this (default: 40 MB)")
    args = ap.parse_args(argv)
    if not REPO_NAME.match(args.repo):
        raise SystemExit("--repo must look like owner/name, got %r" % args.repo)
    if not REF_NAME.match(args.ref):
        raise SystemExit("--ref %r is not a plain branch, tag or commit name" % args.ref)
    brand = args.brand or default_brand(args.repo)
    if not BRAND_NAME.match(brand):
        raise SystemExit("--brand must be upper case letters and digits, got %r" % brand)
    return run(args.root, args.repo, args.ref, args.glob, args.pool_type, brand, args.licence,
               args.remote, args.max_bytes)


if __name__ == "__main__":
    raise SystemExit(main())
