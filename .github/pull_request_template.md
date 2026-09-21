## What this adds or changes

<!-- Pools added or moved, scripts changed, and where the pools came from (issue number, repository). -->

## Checklist

**Any change**
- [ ] `python scripts/check_pools.py` passes (needs Python 3.12; on Windows set `PYTHONUTF8=1`)
- [ ] `python scripts/build_manifest.py` was run, and `source` / `note` are filled in for new rows

**DDOPs (`ddop/`)**
- [ ] Scrubbed: serial number string only `0` or NUL, NAME identity number zero (`scripts/scrub_pool.py`)
- [ ] Named `<BRAND>_<MODEL>[_variant].ddop` in the right `<type>/` folder, no spaces or control characters
- [ ] No field data, task data or logs, and I read the designator strings for anything personal

**IOPs (`iop/`)**
- [ ] Source recorded in `iop/SOURCES.md` (repo and commit, or issue and attachment)
- [ ] A pool in several files: parts in `<stem>/<stem>_partNN.iop`, the merged pool as `<stem>.iop`
- [ ] No working-set NAME with an identity number anywhere in what is recorded (`check_pools.py` checks `SOURCES.md`)
- [ ] I looked at text inside the pool (owner names, serial numbers, phone numbers) before keeping it

**Import pull requests** (opened by the *Import ...* workflows)
- [ ] I read the report in the description, including *Not added* and *Already in the collection*
- [ ] Provisional names (`MFG<code>_VT_<hash>`, `incoming/<hash>.ddop`) were renamed where the machine is known

## Notes for the reviewer

<!-- Anything unusual: a pool that fails validation, a duplicate you kept on purpose, a size concern. -->
