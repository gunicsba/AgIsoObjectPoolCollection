"""Tests for import_iops_from_issues.py, the merged-pool layout and the NAME masking.

    python -m unittest discover -s scripts/tests -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import attachments  # noqa: E402
import build_manifest  # noqa: E402
import check_pools  # noqa: E402
import import_ddops  # noqa: E402
import import_iops_from_issues as imp  # noqa: E402
import sample_data  # noqa: E402
import sources_md  # noqa: E402
from test_pools import TempDirCase, copy_tree, quiet  # noqa: E402

BASE = "https://github.com/user-attachments/files"
NAME = "a00c80000c421b07"            # identity number bits 0..20 = 0x021b07, must never be recorded
NAME2 = "a00c80000c400000"           # already masked by whoever made the capture


def obj(object_type: int, fill: int, size: int = 40) -> bytes:
    """A stand-in for a run of VT objects: id (2 bytes), type, then filler."""
    return bytes([fill, 0, object_type]) + bytes([fill]) * size


WS, DATA_MASK, OUT_STRING = 0, 1, 11
SESSION = [obj(WS, 1), obj(28, 2), obj(4, 3)]      # one pool of three parts


class IopIssueTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.fixtures = self.tmp / "fixtures"
        self.fixtures.mkdir()
        self.root = copy_tree(self.tmp / "repo")
        self.session2 = [obj(WS, 9), obj(OUT_STRING, 8)]
        series = {"iso_data/%s/object_pool_%d.iop" % (NAME, i): d for i, d in enumerate(SESSION + self.session2)}
        series["iso_data/%s/object_pool_with_label_0.iopx" % NAME] = SESSION[0] + bytes(7)   # not a .iop
        series["iso_data/%s/object_pool_0.iop" % NAME2] = obj(OUT_STRING, 5)                 # before any Working Set
        series["iso_data/%s/object_pool_1.iop" % NAME2] = obj(WS, 6)                         # lone pool
        many = {"iso_data/%s/object_pool_%d.iop" % ("a0088000afe00000", i): obj(WS if i == 0 else OUT_STRING, i)
                for i in range(40)}
        files = {
            "%s/1/%s.zip" % (BASE, NAME): sample_data.zipped(series),
            "%s/2/%s.zip" % (BASE, NAME): sample_data.zipped({"iso_data/%s/object_pool_%d.iop" % (NAME, i): d
                                                               for i, d in enumerate(SESSION)}),   # same pool again
            "%s/3/great_plains.iop.zip" % BASE: sample_data.zipped({"great_plains.iop": obj(WS, 7, 100)}),
            "%s/4/debug.zip" % BASE: sample_data.zipped({"debug.iop": obj(OUT_STRING, 4)}),
            "%s/5/lonely.iop" % BASE: obj(WS, 3),
            "%s/6/many.zip" % BASE: sample_data.zipped(many),
            "%s/7/logs.zip" % BASE: sample_data.zipped({"CANLog.asc": b"x" * 100}),
        }
        (self.fixtures / "attachments").mkdir()
        mapping = {}
        for i, (url, data) in enumerate(files.items()):
            (self.fixtures / "attachments" / ("%02d.bin" % i)).write_bytes(data)
            mapping[url] = "attachments/%02d.bin" % i
        (self.fixtures / "attachments.json").write_text(json.dumps(mapping), encoding="utf-8")
        issues = [{"number": n, "title": "t", "url": "https://github.com/example/vt/issues/%d" % n,
                   "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z", "comments": [],
                   "body": "[f](%s)" % url}
                  for n, url in ((int(u.split("/")[-2]), u) for u in files)]
        (self.fixtures / "issues.json").write_text(json.dumps(issues), encoding="utf-8")

    def run_import(self, **kw):
        source = attachments.FixtureSource(self.fixtures, 1_000_000)
        return quiet(imp.run, source, self.root, [], None, "example/AgIsoVT", "unsorted", "EXAMPLE", 1_000_000)

    def test_layout_merge_and_check(self):
        out = self.run_import()
        iop = self.root / "iop" / "unsorted"
        code = (int(NAME, 16) >> 21) & 0x7FF
        merged1 = b"".join(SESSION)
        digest1 = imp.hashlib.sha256(merged1).hexdigest()[:8]
        # three parts: merged file in the main folder, parts in a subfolder named like it
        m1 = iop / ("MFG%d_VT_%s.iop" % (code, digest1))
        self.assertEqual(m1.read_bytes(), merged1)
        for i, part in enumerate(SESSION):
            self.assertEqual((iop / m1.stem / ("%s_part%02d.iop" % (m1.stem, i))).read_bytes(), part)
        # the second session in the same folder is a separate pool
        merged2 = b"".join(self.session2)
        self.assertTrue((iop / ("MFG%d_VT_%s.iop" % (code, imp.hashlib.sha256(merged2).hexdigest()[:8]))).is_file())
        # a lone Working Set file is just one file, no subfolder
        lone = [p for p in iop.glob("MFG*_VT_*.iop") if p.read_bytes() == obj(WS, 6)]
        self.assertEqual(len(lone), 1)
        self.assertFalse((iop / lone[0].stem).exists())
        self.assertTrue((iop / "EXAMPLE_great_plains.iop").is_file())
        self.assertTrue((iop / "EXAMPLE_lonely.iop").is_file())
        # not added: no Working Set first, and a series that is far too long
        why = {w for _, w, _ in out.rejected}
        self.assertTrue(any("Working Set" in w for w in why), why)
        self.assertTrue(any("40 parts" in w for w in why), why)
        self.assertEqual(out.stats["file before any Working Set (not a pool start)"], 1)
        self.assertEqual(len(out.duplicates), 1)          # attachment 2 repeats session 1
        self.assertEqual(check_pools.run(self.root).errors, [])
        manifest = {r["file"]: r for r in build_manifest.read_manifest(self.root / "iop" / "manifest.csv")}
        self.assertEqual(manifest["unsorted/" + m1.name]["parts"], "3")
        self.assertEqual(manifest["unsorted/%s/%s_part00.iop" % (m1.stem, m1.stem)]["parts"], "part")
        self.assertEqual(manifest["unsorted/EXAMPLE_great_plains.iop"]["parts"], "-")

    def test_no_working_set_identity_is_recorded_anywhere(self):
        out = self.run_import()
        report = imp.render_report(out, "example/AgIsoVT", out.n_refs)
        texts = [report] + [p.read_text(encoding="utf-8") for p in (self.root / "iop").rglob("*")
                            if p.is_file() and p.suffix in (".md", ".csv")]
        for text in texts:
            self.assertNotIn(NAME, text)
            self.assertEqual(attachments.unmasked_names(text), [])
        self.assertIn(attachments.mask_names(NAME), "\n".join(texts))       # the masked form is there
        rows = {r["file"]: r for r in sources_md.parse((self.root / "iop" / "SOURCES.md").read_text(encoding="utf-8"))}
        self.assertTrue(rows and all(r["licence"] == "unspecified" and r["ref"].startswith("#") for r in rows.values()))

    def test_rerun_adds_nothing(self):
        self.run_import()
        before = (self.root / "iop" / "SOURCES.md").read_text(encoding="utf-8")
        out = self.run_import()
        self.assertEqual(out.added, [])
        self.assertEqual((self.root / "iop" / "SOURCES.md").read_text(encoding="utf-8"), before)

    def test_check_flags_broken_merged_pools_and_unmasked_names(self):
        self.run_import()
        iop = self.root / "iop" / "unsorted"
        merged = next(p for p in iop.glob("MFG*_VT_*.iop") if (iop / p.stem).is_dir())
        original = merged.read_bytes()
        merged.write_bytes(original + b"x")
        self.assertIn("is not the concatenation", "\n".join(check_pools.run(self.root).errors))
        merged.write_bytes(original)
        (iop / merged.stem / ("%s_part01.iop" % merged.stem)).rename(iop / merged.stem / ("%s_part05.iop" % merged.stem))
        self.assertIn("without gaps", "\n".join(check_pools.run(self.root).errors))
        (iop / merged.stem / ("%s_part05.iop" % merged.stem)).rename(iop / merged.stem / ("%s_part01.iop" % merged.stem))
        merged.unlink()
        self.assertIn("(the merged pool) does not exist", "\n".join(check_pools.run(self.root).errors))
        merged.write_bytes(original)
        sources = self.root / "iop" / "SOURCES.md"
        sources.write_text(sources.read_text(encoding="utf-8") + "\n" + NAME + "\n", encoding="utf-8")
        self.assertIn("non-zero identity number", "\n".join(check_pools.run(self.root).errors))

    def test_helpers(self):
        self.assertEqual(attachments.mask_names("x/%s.zip!iso_data/%s/a" % (NAME, NAME.upper())),
                         "x/a00c80000c400000.zip!iso_data/a00c80000c400000/a")
        self.assertEqual(attachments.unmasked_names("a00c80000c400000 %s" % NAME), [NAME])
        self.assertEqual(attachments.mask_names("assets/6b3f5d0e-1111-4c1a-9a55-3c1f2e5b7a10"),
                         "assets/6b3f5d0e-1111-4c1a-9a55-3c1f2e5b7a10")
        self.assertTrue(imp.starts_pool(obj(WS, 1)) and not imp.starts_pool(obj(OUT_STRING, 1)) and not imp.starts_pool(b"\0"))
        self.assertEqual(imp.part_path("unsorted/AB_x.iop", 3, 5), "unsorted/AB_x/AB_x_part03.iop")
        self.assertEqual(imp.part_path("unsorted/AB_x.iop", 3, 150), "unsorted/AB_x/AB_x_part003.iop")
        self.assertNotIn("|", imp.masked_path("u|v`w\x07"))

    def test_ddop_report_masks_working_set_names_in_urls(self):
        ref = attachments.Reference("%s/9/%s.zip" % (BASE, NAME), 9, "https://github.com/example/tc/issues/9")
        out = import_ddops.Outcome()
        out.failed.append((ref.url, "download failed", ref))
        out.duplicates.append(("%s!x/%s/y.ddop" % (ref.url, NAME), "ddop/a/B_C.ddop", ref))
        self.assertEqual(attachments.unmasked_names(import_ddops.render_report(out, "example/tc", [], None, 1)), [])
