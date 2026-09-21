# AgIso object pool collection

Real ISOBUS object pools, kept as test and reference data for anything that has to read them.

- **DDOP** (`ddop/`): device descriptor object pools, the machine description an implement sends to a
  task controller. They come from machines that were connected to a task controller and were shared
  in issues.
- **IOP** (`iop/`): virtual terminal (VT) object pools, the user interface an implement shows on the
  terminal. They are copied from repositories and each one is traced to its source.

Any project can use the files. Known consumers: the
[AOG-TaskController](https://github.com/AgOpenGPS-Official/AOG-TaskController) tests, and the AgIsoDDOPGenerator.

## Layout

```
ddop/
  manifest.csv            index of every pool, one row per file
  <type>/<BRAND>_<MODEL>[_variant].ddop
iop/
  manifest.csv            index of every file
  SOURCES.md              where each file came from (commit SHA or issue), plus the source's licence
  LICENSES/               licence text of each source repository that has one
  <type>/<BRAND>_<MODEL>[_variant].iop
  <type>/<BRAND>_<MODEL>/<BRAND>_<MODEL>_partNN.iop     only for a pool that came in several files
incoming/                 exists only on import pull requests: scrubbed candidates + REPORT.md
scripts/                  Python 3.12, standard library only
```

`ddop/<type>` and `iop/<type>` use the same folders:

| Folder | Contents |
|---|---|
| `sprayer` | Sprayers |
| `spreader` | Fertilizer spreaders |
| `seeder` | Seed drills and single-product planters |
| `combined` | Machines with several products (for example planter + fertilizer + microgranulate) |
| `tractor` | Tractor pools. They have no sections and must never be taken for an implement |
| `unsorted` | Real pools whose machine could not be identified. Move them once known |

## File names

`<BRAND>_<MODEL>[_variant].ddop`

- `BRAND` is upper case letters and digits. The rest uses letters, digits and underscores.
- No spaces, no control characters, no non-ASCII. Names are always taken from the manifest or written
  by hand, never copied from a source file name: localization labels and file names in the wild can
  contain control characters.
- The variant says what tells two pools of the same machine apart: section count (`_12sec`), boom
  count (`_2booms`), section control method (`_DDI290`), language (`_hu`), `_minimal`.
- `.iop` files follow the same scheme. The importer builds `MODEL` from the file's path in the source
  repository (characters outside `A-Z a-z 0-9` become `_`) and `BRAND` from the repository name, or from
  `--brand`. The original path is kept in `iop/SOURCES.md`; there are no per-repository folders.

## manifest.csv

`ddop/manifest.csv`, one row per pool. Columns up to `strings` are computed from the file by
`scripts/build_manifest.py`; `source` and `note` are written by hand and kept across rebuilds.

| Column | Meaning |
|---|---|
| `file` | Path below `ddop/`, forward slashes |
| `type` | The folder |
| `sections` | Number of Section elements |
| `functions` | Number of Function elements (booms, products) |
| `geometry` | Where the section offsets and width come from, see below |
| `section_control` | Method the pool supports, in the priority a task controller uses: `DDI290` (condensed setpoint), then `DDI161` (settable condensed actual), then `DDI141` (per-element work state). `none` if the pool has none of them |
| `connector_types` | DDI 157 values found on the connector elements, `-` if none |
| `bytes` | File size |
| `language` | First two bytes of the localization label (`en`, `hu`, ...), `-` if they are not letters |
| `strings` | `ascii`, `non-ascii` or `none`: what the designators in the pool contain |
| `source` | Where the pool came from. `#N` is an issue of AgOpenGPS-Official/AOG-TaskController |
| `note` | Free text |

### The geometry column

- `static`: the section offsets and width are DeviceProperty objects, so the values are inside the pool.
- `process-data`: they are DeviceProcessData objects without a value. The task controller has to
  request them from the implement over the bus after the pool is activated.
- `mixed`: some sections one way, some the other.
- `missing`: there are sections but no geometry object at all.
- `none`: no sections (tractors).

The DDIs looked at are 134 and 135 (section offset X and Y) and 67, 68 and 70 (working width).

`iop/manifest.csv` has `file`, `type`, `bytes`, `sha256`, `parts` and `note`. `parts` is the number of
parts for a merged pool, `part` for a file inside a parts folder, and `-` for a single file.

### Pools that come in several files

An implement can upload one pool as several numbered files (`object_pool_0.iop`, `object_pool_1.iop`, ...).
Such a pool is stored twice: the parts in `iop/<type>/<stem>/<stem>_partNN.iop`, and the whole pool as
`iop/<type>/<stem>.iop` in the main folder, which is the parts concatenated in part order. Consumers
just read the main-folder files. `scripts/check_pools.py` fails if a merged file is not exactly its parts
joined, if part numbers have gaps, or if the merged file is missing. Concatenation is not validated as
a VT pool by these scripts (see *IOP validation* below).

## IOP validation

Nothing here parses VT object pools: the scripts only check names, manifests, provenance, and that a
merged pool is its parts joined. The AOG-TaskController repository has an `iop_validator` (C++, built on
the AgIsoStack++ parser) that does parse them. It is not part of this repository yet; real machine pools
often fail its strict checks, so it would be a report, not a gate, apart from "does not parse".

## Privacy: pools are scrubbed

A DDOP holds the machine's serial number (some are VIN-style) and, in its NAME, an identity number.
Every pool in this repository has both removed, with length and layout unchanged:

- every non-NUL byte of the serial number string is `0` (NUL bytes stay);
- NAME bits 0..20 (the identity number) are zero.

`scripts/check_pools.py` fails on any pool where that is not the case. Never commit an unscrubbed pool,
and never add field data, task data or log files. Scrubbing covers those two fields only: designator
strings are copied as they are, so read them before publishing a pool from a machine you do not own.

Two pools that differ only in localization label, serial number or identity number are the same machine
and are treated as duplicates.

## Adding a pool

**DDOP, through an issue.** Open a *Submit a DDOP* issue and attach the file (a zip is fine). Scrub it first with
`scripts/scrub_pool.py`: what you attach there is public. The *Import DDOPs* workflow
(Actions tab, run manually) reads the issues of a repository, finds pools by their content (the first
bytes are `DVC`, whatever the file is called), scrubs them, skips those already present and opens a pull
request with the new ones in `incoming/` and a `REPORT.md`. It never commits to the default branch.

**DDOP, by hand.**

1. Scrub the pool: `python scripts/scrub_pool.py machine.ddop` writes `machine.scrubbed.ddop`.
2. Copy it to `ddop/<type>/<BRAND>_<MODEL>[_variant].ddop`.
3. `python scripts/build_manifest.py`, then fill in `source` and `note` for the new row.
4. `python scripts/check_pools.py`.

For an import pull request, do steps 2 to 4 with the files from `incoming/`, then delete `incoming/`.

**IOP from issue attachments.** The *Import IOPs from issues* workflow (default source
`Open-Agriculture/AgIsoVirtualTerminal`) reads issues and comments, downloads the attachments, looks
inside zips for `.iop` files, groups the numbered files of a multi-file pool (a new pool starts at every
file whose first object is a Working Set), and opens a pull request adding them under
`iop/<type>/`. Names are provisional: `MFG<manufacturer code>_VT_<hash>`. The working-set NAME in
source paths contains the machine's identity number, so it is zeroed in everything recorded, and
`check_pools.py` fails if an unmasked NAME appears in `iop/SOURCES.md`. Object pools can hold text typed
in on the terminal, so read a pool before keeping it.

**IOP from a repository.** Open a *Submit an IOP* issue naming the source repository, or run the *Import IOPs* workflow
(source repository, ref, path glob, optional type and brand; default `Open-Agriculture/AgIsoStack-plus-plus`,
`examples/**/*.iop`, type `unsorted`). It copies the files into `iop/<type>/`, updates `iop/SOURCES.md`
and `iop/manifest.csv` and opens a pull request. The source repository's licence is written down
(`iop/SOURCES.md`, and its text in `iop/LICENSES/`) but never checked or enforced: pools rarely have a
licence of their own.

## Scripts

Python 3.12, standard library only. On Windows set `PYTHONUTF8=1` (the default code page cannot read
issue JSON).

| Script | Purpose |
|---|---|
| `poollib.py` | Shared code: DDOP parser, scrub, summary, content hash |
| `build_manifest.py` | Rebuild the manifests (`--check` only reports) |
| `check_pools.py` | What CI runs: manifests match the files, every DDOP parses and is scrubbed, names are well formed |
| `scrub_pool.py` | Scrub one pool before sharing it |
| `import_ddops.py` | Stage new DDOPs from issue attachments in `incoming/` |
| `import_iops.py` | Copy `.iop` files from a source repository |
| `import_iops_from_issues.py` | Add `.iop` files found in issue attachments, merging multi-file pools |
| `attachments.py` | Shared by the issue importers: issues, downloads, zips, NAME masking |
| `open_pr.sh` | Used by the workflows: commit to a new branch and open a pull request |

Tests: `python -m unittest discover -s scripts/tests`. `scripts/tests/sample_data.py OUTDIR` writes
saved issues and attachments for trying `import_ddops.py --fixtures OUTDIR` without network access.

The workflows are pinned by commit SHA. Set the repository option *Allow GitHub Actions to create and
approve pull requests*; an optional `PR_TOKEN` secret makes the import pull requests start the normal
pull request checks.

## Licence

This repository (the scripts, workflows, manifests and documentation) is under the
[WTFPL](LICENSE). The licence of this repository is unrelated to that of the pools in it. For `iop/`,
`iop/SOURCES.md` says what each source repository declares; the DDOPs were contributed by machine owners
and their status still needs to be decided.
