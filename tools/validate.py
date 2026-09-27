"""Validate pools/. Exit 0 when everything passes, 1 otherwise.

Checks
  * every pool folder is pools/<manufacturer>/<slug>/ and holds pool.iop and meta.yaml
  * meta.yaml is valid against schema/meta.schema.json
  * ids are unique
  * reference/ files are named <object-id>_<terminal>.jpg|png
  * a pool with parts/: pool.iop is byte for byte parts/part00.iop, part01.iop, ... joined
  * meta.yaml never holds a working-set NAME with a non-zero identity number

    pip install -r tools/requirements.txt
    python tools/validate.py [--root DIR]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parent.parent

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REFERENCE_NAME = re.compile(r"^(?P<object>\d{1,5})_(?P<terminal>[a-z0-9]+(?:-[a-z0-9]+)*)\.(?:jpg|png)$")
PART_NAME = re.compile(r"^part(?P<n>\d{2,})\.iop$")
POOL_ENTRIES = {"pool.iop", "meta.yaml", "reference", "parts"}

# A 16-hex-digit token is a working-set NAME; bits 0..20 are the identity number and must be zero.
NAME_TOKEN = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{16}(?![0-9A-Fa-f])")
IDENTITY_NUMBER_MASK = 0x1FFFFF


def check_reference(folder: Path, shown: str, errors: list[str]):
    ref = folder / "reference"
    if not ref.is_dir():
        errors.append("%s/reference: must be a folder" % shown)
        return
    for p in sorted(ref.iterdir()):
        m = REFERENCE_NAME.match(p.name)
        if not p.is_file() or not m or int(m["object"]) > 65535:
            errors.append("%s/reference/%s: must be named <object-id>_<terminal>.jpg|png "
                          "(object id 0..65535, terminal lower case letters, digits and hyphens)" % (shown, p.name))


def check_parts(folder: Path, shown: str, errors: list[str]):
    parts = folder / "parts"
    names = sorted(p.name for p in parts.iterdir()) if parts.is_dir() else []
    bad = [n for n in names if not PART_NAME.match(n) or not (parts / n).is_file()]
    if bad or len(names) < 2:
        errors.append("%s/parts: must hold at least two files part00.iop, part01.iop, ... (found %s)"
                      % (shown, ", ".join(names) or "nothing"))
        return
    numbers = sorted(int(PART_NAME.match(n)["n"]) for n in names)
    if numbers != list(range(len(numbers))):
        errors.append("%s/parts: part numbers must start at 0 and have no gaps" % shown)
        return
    joined = b"".join((parts / n).read_bytes() for n in sorted(names, key=lambda n: int(PART_NAME.match(n)["n"])))
    if (folder / "pool.iop").is_file() and joined != (folder / "pool.iop").read_bytes():
        errors.append("%s/pool.iop is not the concatenation of parts/ in part order" % shown)


def check_pool(folder: Path, shown: str, validator, errors: list[str]) -> str | None:
    """Check one pool folder; return its id when meta.yaml has a valid one."""
    entries = {p.name for p in folder.iterdir()}
    for name in sorted(entries - POOL_ENTRIES):
        errors.append("%s/%s: unexpected (a pool folder holds pool.iop, meta.yaml, reference/, parts/)" % (shown, name))
    if not (folder / "pool.iop").is_file():
        errors.append("%s: pool.iop is missing" % shown)
    if "reference" in entries:
        check_reference(folder, shown, errors)
    if "parts" in entries:
        check_parts(folder, shown, errors)
    meta_file = folder / "meta.yaml"
    if not meta_file.is_file():
        errors.append("%s: meta.yaml is missing" % shown)
        return None
    text = meta_file.read_text(encoding="utf-8")
    for token in sorted(set(NAME_TOKEN.findall(text))):
        if int(token, 16) & IDENTITY_NUMBER_MASK:
            errors.append("%s/meta.yaml: working-set NAME %s has a non-zero identity number (zero bits 0..20)"
                          % (shown, token))
    try:
        meta = yaml.safe_load(text)
    except yaml.YAMLError as e:
        errors.append("%s/meta.yaml: not valid YAML: %s" % (shown, e))
        return None
    problems = sorted(validator.iter_errors(meta), key=lambda e: list(e.absolute_path))
    for e in problems:
        where = "/".join(str(p) for p in e.absolute_path) or "(top level)"
        errors.append("%s/meta.yaml: %s: %s" % (shown, where, e.message))
    return None if problems else meta["id"]


def run(root: Path) -> tuple[list[str], int]:
    errors: list[str] = []
    schema = json.loads((root / "schema" / "meta.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    pools = root / "pools"
    if not pools.is_dir():
        return ["pools/ is missing"], 0
    ids: dict[str, str] = {}
    count = 0
    for manufacturer in sorted(pools.iterdir()):
        if not manufacturer.is_dir() or not SLUG.match(manufacturer.name):
            errors.append("pools/%s: must be a folder named in lower case letters, digits and hyphens" % manufacturer.name)
            continue
        for folder in sorted(manufacturer.iterdir()):
            shown = "pools/%s/%s" % (manufacturer.name, folder.name)
            if not folder.is_dir() or not SLUG.match(folder.name):
                errors.append("%s: must be a pool folder named in lower case letters, digits and hyphens" % shown)
                continue
            count += 1
            pool_id = check_pool(folder, shown, validator, errors)
            if pool_id is None:
                continue
            if pool_id in ids:
                errors.append("%s: id %s is already used by %s" % (shown, pool_id, ids[pool_id]))
            else:
                ids[pool_id] = shown
    return errors, count


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this repository)")
    args = ap.parse_args(argv)
    errors, count = run(args.root)
    for e in errors:
        print("error:", e)
    if errors:
        print("FAILED: %d error(s) in %d pool(s)" % (len(errors), count))
        return 1
    print("OK: %d pool(s) checked" % count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
