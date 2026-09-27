# AgIso object pool collection

Real ISOBUS object pools, kept as test and reference data for anything that has to read them.

- **DDOP** (`ddop/`): device descriptor object pools, the machine description an implement sends to a
  task controller. They come from machines that were connected to a task controller and were shared
  in issues.
- **VT pools** (`pools/`): virtual terminal object pools (`.iop`), the user interface an implement
  shows on the terminal, for testing VT implementations. Each one sits in its own folder with a
  `meta.yaml` that records where it came from.

Any project can use the files. Known consumers: the
[AOG-TaskController](https://github.com/AgOpenGPS-Official/AOG-TaskController) tests, and the AgIsoDDOPGenerator.

## Layout

```
ddop/
  manifest.csv            index of every pool, one row per file
  <type>/<BRAND>_<MODEL>[_variant].ddop
pools/
  <manufacturer>/<slug>/
    pool.iop              the whole pool
    meta.yaml             metadata, see below
    parts/partNN.iop      only for a pool that came in several files
    reference/            optional photos and screenshots from commercial VTs
schema/meta.schema.json   JSON Schema for meta.yaml
tools/validate.py         checks pools/ (what the Validate VT pools workflow runs)
incoming/                 exists only on import pull requests: scrubbed candidates + REPORT.md
scripts/                  DDOP tooling, Python 3.12, standard library only
```

The `ddop/<type>` folders:

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

## VT pools

### Folders

`pools/<manufacturer>/<slug>/`. `manufacturer` is the name from the
[AgIsoVirtualTerminal manufacturer map](https://github.com/Open-Agriculture/AgIsoVirtualTerminal/blob/main/include/ManufacturerMap.hpp)
for the manufacturer code in the working-set NAME, in lower case with hyphens (`ptx-trimble`,
`vaderstad`), or `unknown`. The code identifies who made the ECU, which is not always the machine
brand. `slug` is free, lower case letters, digits and hyphens; pools imported without a known machine
are `vt-<id>`. Rename or move a folder whenever you learn more: the `id` in `meta.yaml` stays.

An implement can upload one pool as several numbered files (`object_pool_0.iop`, `object_pool_1.iop`, ...).
Then `parts/` keeps them as `part00.iop`, `part01.iop`, ... and `pool.iop` is their concatenation in
part order. Consumers read `pool.iop`. Concatenation is not validated as a VT pool.

`reference/` holds photos or screenshots of the pool on commercial VTs, named
`<object-id>_<terminal>.jpg` or `.png`: the decimal id of the object shown (usually a data mask) and the
terminal in lower case with hyphens, for example `1000_john-deere-g5.jpg`.

### meta.yaml

Checked against [`schema/meta.schema.json`](schema/meta.schema.json). Unknown values are the string
`TODO`, not left out.

| Field | Meaning |
|---|---|
| `id` | Stable id: the first 8 hex digits of the sha256 of `pool.iop` when the pool was added. Never changes afterwards, not on rename, move, or a fix to `pool.iop`. Quote it (`id: "0cd84f4a"`), or YAML may read it as a number |
| `name` | Machine or pool name |
| `manufacturer` | Manufacturer name, as in the manufacturer map |
| `manufacturer_code` | Optional. ISOBUS manufacturer code from the working-set NAME |
| `vt_version` | VT version the pool targets, 2 to 6, or `TODO` |
| `source` | Free text: how and where it was captured (issue, attachment, path inside the zip, VT used) |
| `public` | `true` only once someone has confirmed the pool may be shown in a public gallery. Default `false` |
| `render` | `data_mask_size`, `softkey_count`, `softkey_width`, `softkey_height`: the terminal settings to render with, in pixels |
| `known_issues` | URLs of issues about this pool |

### Validation

`pip install -r tools/requirements.txt`, then `python tools/validate.py`. It fails when a pool folder
lacks `pool.iop` or `meta.yaml`, `meta.yaml` does not match the schema, two pools share an `id`, a
`reference/` file is misnamed, `pool.iop` is not its `parts/` joined, or `meta.yaml` holds a working-set
NAME with a non-zero identity number. It does not parse the pools. The AOG-TaskController repository
has an `iop_validator` (C++, built on the AgIsoStack++ parser) that does; real machine pools often
fail its strict checks, so it would be a report, not a gate.

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

**VT pool.**

1. Compute the id: the first 8 hex digits of `sha256sum pool.iop`.
2. Create `pools/<manufacturer>/<slug>/` and copy the pool in as `pool.iop`, unchanged. A pool that
   came in several files: the numbered files go to `parts/part00.iop`, `part01.iop`, ... in upload
   order, and `pool.iop` is them joined (`cat parts/part*.iop > pool.iop`).
3. Write `meta.yaml`; copy one from another pool and change every field. Leave `public: false`.
   Use `TODO` for what you do not know.
4. Optionally add screenshots to `reference/`.
5. `python tools/validate.py`.

A working-set NAME (16 hex digits, often a folder name in AgIsoVirtualTerminal's `iso_data`) holds the
machine's identity number: zero its last 21 bits before writing it in `meta.yaml` (`a00c80000c412345`
becomes `a00c80000c400000`). Object pools can hold text typed in on the terminal (owner names, phone
numbers), so read a pool before adding it.

The *Import IOPs* and *Import IOPs from issues* workflows and their scripts still write the old
`iop/<type>/` layout and are disabled until they are updated.

## Scripts

The DDOP scripts in `scripts/`: Python 3.12, standard library only. On Windows set `PYTHONUTF8=1` (the default code page cannot read
issue JSON).

| Script | Purpose |
|---|---|
| `poollib.py` | Shared code: DDOP parser, scrub, summary, content hash |
| `build_manifest.py` | Rebuild the manifests (`--check` only reports) |
| `check_pools.py` | What the Check pools workflow runs: manifests match the files, every DDOP parses and is scrubbed, names are well formed |
| `scrub_pool.py` | Scrub one pool before sharing it |
| `import_ddops.py` | Stage new DDOPs from issue attachments in `incoming/` |
| `import_iops.py` | Copy `.iop` files from a source repository (old `iop/` layout, workflow disabled) |
| `import_iops_from_issues.py` | Add `.iop` files found in issue attachments (old `iop/` layout, workflow disabled) |
| `attachments.py` | Shared by the issue importers: issues, downloads, zips, NAME masking |
| `open_pr.sh` | Used by the workflows: commit to a new branch and open a pull request |

Tests: `python -m unittest discover -s scripts/tests`. `scripts/tests/sample_data.py OUTDIR` writes
saved issues and attachments for trying `import_ddops.py --fixtures OUTDIR` without network access.

The workflows are pinned by commit SHA. Set the repository option *Allow GitHub Actions to create and
approve pull requests*; an optional `PR_TOKEN` secret makes the import pull requests start the normal
pull request checks.

## Licence

This repository (the scripts, schema, workflows, manifests and documentation) is under the
[WTFPL](LICENSE). That licence does not cover the pools or the reference images.

A VT object pool is the manufacturer's work: its layout, text and bitmaps belong to whoever made the
implement software, and none of the pools here came with a licence. They are kept to test VT
implementations. Do not redistribute them beyond that, and do not show them publicly unless `public`
is `true`, which needs someone to have confirmed it. The same goes for photos and screenshots in
`reference/`, which may also show a commercial terminal's own interface. `source` in `meta.yaml` says
where each pool came from.

The DDOPs were contributed by machine owners and their status still needs to be decided.
