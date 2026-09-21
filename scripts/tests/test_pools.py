"""Tests for the pool scripts. Standard library only:

    python -m unittest discover -s scripts/tests -v
"""
from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_manifest  # noqa: E402
import check_pools  # noqa: E402
import import_ddops  # noqa: E402
import import_iops  # noqa: E402
import poollib  # noqa: E402
import sample_data  # noqa: E402
import sources_md  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
QUIET = contextlib.redirect_stdout(io.StringIO())


def quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def copy_tree(dest: Path) -> Path:
    """A scratch repository root: a copy of the real ddop/ tree and an empty iop/ tree.

    iop/ starts empty so the tests do not depend on which real pools happen to be imported.
    """
    shutil.copytree(ROOT / "ddop", dest / "ddop")
    (dest / "iop").mkdir()
    (dest / "iop" / "SOURCES.md").write_text(sources_md.render([]), encoding="utf-8", newline="\n")
    (dest / "iop" / "manifest.csv").write_text(",".join(build_manifest.IOP_COLUMNS) + "\n",
                                               encoding="utf-8", newline="\n")
    return dest


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


class PoolLibTests(unittest.TestCase):
    def test_every_committed_pool_parses_and_is_scrubbed(self):
        files = sorted((ROOT / "ddop").rglob("*.ddop"))
        self.assertGreaterEqual(len(files), 24)
        for f in files:
            data = f.read_bytes()
            poollib.parse(data)
            self.assertTrue(poollib.is_scrubbed(data), f.name)

    def test_scrub_keeps_length_and_touches_only_serial_and_name(self):
        raw = sample_data.with_identity(sample_data.pool("sprayer/JD_740_SPRAYER_9sec.ddop"))
        self.assertFalse(poollib.is_scrubbed(raw))
        clean = poollib.scrub(raw)
        self.assertEqual(len(clean), len(raw))
        d = poollib.parse(raw).dvc
        changed = {i for i in range(len(raw)) if raw[i] != clean[i]}
        allowed = set(range(d.name_offset, d.name_offset + 3)) | set(
            range(d.serial_offset, d.serial_offset + len(d.serial)))
        self.assertTrue(changed <= allowed)
        cd = poollib.parse(clean).dvc
        self.assertEqual(cd.serial, b"0" * len(sample_data.FAKE_SERIAL))
        self.assertEqual(cd.name & poollib.IDENTITY_NUMBER_MASK, 0)
        self.assertEqual(poollib.scrub(clean), clean)  # idempotent

    def test_scrub_keeps_nul_bytes_in_the_serial(self):
        raw = sample_data.with_identity(sample_data.pool("sprayer/JD_740_SPRAYER_9sec.ddop"), serial=b"AB\0CD\0\0")
        self.assertEqual(poollib.parse(poollib.scrub(raw)).dvc.serial, b"00\0" + b"00\0\0")

    def test_content_hash_ignores_serial_identity_and_localization_label(self):
        base = sample_data.pool("spreader/SULKY_ISOLINK_SPREADER_6sec.ddop")
        other = sample_data.with_identity(base, serial=b"XYZ-1", identity=7, label=b"de" + bytes(5))
        self.assertEqual(poollib.content_hash(base), poollib.content_hash(other))
        different = sample_data.with_identity(base, name_xor=1 << 33)
        self.assertNotEqual(poollib.content_hash(base), poollib.content_hash(different))

    def test_no_two_committed_pools_are_the_same_machine(self):
        hashes = [poollib.content_hash(f.read_bytes()) for f in (ROOT / "ddop").rglob("*.ddop")]
        self.assertEqual(len(hashes), len(set(hashes)))

    def test_parse_rejects_bad_input_without_indexerror(self):
        good = sample_data.pool("sprayer/JD_740_SPRAYER_9sec.ddop")
        for bad in (b"", b"DVC", b"DVC\x01\x00\xff", good[:len(good) // 2], b"XYZ" + good[3:], good + b"ZZZ"):
            with self.assertRaises(poollib.PoolError):
                poollib.parse(bad)
        with self.assertRaises(poollib.PoolError):
            poollib.scrub(b"DVC\x01\x00\x05ABC")  # cannot scrub what cannot be parsed

    def test_extended_structure_label_is_read_and_flagged(self):
        good = sample_data.pool("sprayer/JD_740_SPRAYER_9sec.ddop")
        d = poollib.parse(good).dvc
        end = d.localization_offset + 7
        v4 = good[:end] + bytes([3]) + b"XYZ" + good[end:]
        parsed = poollib.parse(v4)
        self.assertEqual(parsed.dvc.extended_structure_label, b"XYZ")
        self.assertEqual(len(parsed.elements), len(poollib.parse(good).elements))

    def test_language_and_strings(self):
        self.assertEqual(poollib.language(b"EN\0\0\0\0\xff"), "en")
        self.assertEqual(poollib.language(b"\x01h\0\0\0\0\xff"), "-")
        self.assertEqual(poollib.summarize(sample_data.pool("seeder/KUHN_ESPRO_2sec_DDI141_hu.ddop"))["strings"], "non-ascii")
        self.assertEqual(poollib.summarize(sample_data.pool("seeder/KUHN_ESPRO_2sec_DDI141_en.ddop"))["strings"], "ascii")

    def test_summary_never_contains_serial_or_identity(self):
        raw = sample_data.with_identity(sample_data.pool("sprayer/JD_740_SPRAYER_9sec.ddop"))
        self.assertNotIn("WVWZZZ", repr(poollib.summarize(raw)))


class ScrubCliTests(TempDirCase):
    def test_writes_scrubbed_copy_and_leaves_original(self):
        import scrub_pool
        src = self.tmp / "machine.ddop"
        raw = sample_data.with_identity(sample_data.pool("sprayer/JD_740_SPRAYER_9sec.ddop"))
        src.write_bytes(raw)
        self.assertEqual(quiet(scrub_pool.main, [str(src)]), 0)
        self.assertEqual(src.read_bytes(), raw)
        self.assertTrue(poollib.is_scrubbed((self.tmp / "machine.scrubbed.ddop").read_bytes()))
        self.assertEqual(quiet(scrub_pool.main, [str(src), "-o", str(src)]), 1)
        (self.tmp / "junk.ddop").write_bytes(b"DVC")
        self.assertEqual(quiet(scrub_pool.main, [str(self.tmp / "junk.ddop")]), 1)
        self.assertFalse((self.tmp / "junk.scrubbed.ddop").exists())


class ManifestAndCheckTests(TempDirCase):
    def test_committed_tree_passes(self):
        rep = check_pools.run(ROOT)
        self.assertEqual(rep.errors, [])

    def test_committed_manifests_are_up_to_date(self):
        for path, text in build_manifest.build(ROOT).items():
            self.assertEqual(path.read_text(encoding="utf-8"), text, path.name)

    def test_check_catches_each_problem(self):
        root = copy_tree(self.tmp / "repo")
        ddop = root / "ddop"
        good = sample_data.pool("sprayer/JD_740_SPRAYER_9sec.ddop")
        (ddop / "sprayer" / "JD_740_SPRAYER_9sec.ddop").unlink()               # listed, missing
        (ddop / "sprayer" / "NEW_POOL.ddop").write_bytes(good)                   # exists, not listed
        (ddop / "sprayer" / "bad name.ddop").write_bytes(good)                   # space in name
        (ddop / "sprayer" / "BAD_CTL\x07.ddop").write_bytes(good) if sys.platform != "win32" else None
        (ddop / "seeder" / "UNSCRUBBED_ONE.ddop").write_bytes(sample_data.with_identity(good))
        (ddop / "seeder" / "BROKEN_ONE.ddop").write_bytes(b"DVC\x01")
        (ddop / "combined" / "lower_case_brand.ddop").write_bytes(good)
        (root / "iop" / "loose.iop").write_bytes(b"\x01")
        errors = "\n".join(check_pools.run(root).errors)
        self.assertIn("lists sprayer/JD_740_SPRAYER_9sec.ddop, which does not exist", errors)
        self.assertIn("NEW_POOL.ddop exists but is not in ddop/manifest.csv", errors)
        self.assertIn("bad\\x20name.ddop: name must match", errors)
        self.assertIn("UNSCRUBBED_ONE.ddop: NOT SCRUBBED: serial", errors)
        self.assertIn("UNSCRUBBED_ONE.ddop: NOT SCRUBBED: NAME identity", errors)
        self.assertIn("BROKEN_ONE.ddop: does not parse", errors)
        self.assertIn("lower_case_brand.ddop: name must match", errors)
        self.assertIn("iop/loose.iop", errors)

    def test_check_catches_stale_manifest_column(self):
        root = copy_tree(self.tmp / "repo")
        manifest = root / "ddop" / "manifest.csv"
        text = manifest.read_text(encoding="utf-8").replace(",process-data,DDI290,-,2705,", ",static,DDI290,-,2705,")
        manifest.write_text(text, encoding="utf-8", newline="\n")
        self.assertIn("column geometry is 'static'", "\n".join(check_pools.run(root).errors))

    def test_check_flags_unscrubbed_pool_in_incoming(self):
        root = copy_tree(self.tmp / "repo")
        (root / "incoming").mkdir()
        (root / "incoming" / "abc.ddop").write_bytes(sample_data.with_identity(sample_data.pool("sprayer/JD_740_SPRAYER_9sec.ddop")))
        self.assertIn("incoming/abc.ddop: NOT SCRUBBED", "\n".join(check_pools.run(root).errors))

    def test_build_manifest_keeps_hand_written_columns_and_adds_new_files(self):
        root = copy_tree(self.tmp / "repo")
        (root / "ddop" / "sprayer" / "NEW_POOL.ddop").write_bytes(sample_data.pool("sprayer/JD_740_SPRAYER_9sec.ddop"))
        self.assertEqual(build_manifest.main(["--root", str(root), "--check"]), 1)
        quiet(build_manifest.main, ["--root", str(root)])
        rows = {r["file"]: r for r in build_manifest.read_manifest(root / "ddop" / "manifest.csv")}
        self.assertEqual(rows["sprayer/NEW_POOL.ddop"]["source"], "")
        self.assertEqual(rows["sprayer/JD_740_SPRAYER_9sec.ddop"]["source"], "#34")
        self.assertEqual(build_manifest.main(["--root", str(root), "--check"]), 0)


class ImportDdopsTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.fixtures = self.tmp / "fixtures"
        self.fixtures.mkdir()
        self.expected = sample_data.build(self.fixtures)
        self.root = copy_tree(self.tmp / "repo")

    def run_import(self, numbers=(), since=None, max_bytes=8000):
        source = import_ddops.FixtureSource(self.fixtures, max_bytes)
        return quiet(import_ddops.run, source, self.root, list(numbers), since, "example/tc", max_bytes)

    def incoming(self):
        return sorted(p.name for p in (self.root / "incoming").iterdir()) if (self.root / "incoming").is_dir() else []

    def test_full_run(self):
        out = self.run_import()
        self.assertEqual(len(out.candidates), 4)
        want = {poollib.content_hash(p)[:16] + ".ddop" for p in self.expected["new"]}
        self.assertEqual(set(self.incoming()), want | {"REPORT.md"})
        for c in out.candidates:
            data = (self.root / "incoming" / c.name).read_bytes()
            self.assertTrue(poollib.is_scrubbed(data))
            self.assertNotIn(sample_data.FAKE_SERIAL, data)
        self.assertEqual(len(out.duplicates), 1)
        self.assertEqual(out.duplicates[0][1], "ddop/spreader/SULKY_ISOLINK_SPREADER_6sec.ddop")
        self.assertEqual(len(out.rejected), 1)                       # the truncated pool is not written
        self.assertEqual([why for _, why, _ in out.failed], ["larger than the size limit"])  # big.bin, 40 MB rule
        self.assertEqual(out.stats["log, trace, installer or media file"], 5)
        # Nothing anywhere in the tree carries the fake serial.
        for p in self.root.rglob("*"):
            if p.is_file():
                self.assertNotIn(sample_data.FAKE_SERIAL, p.read_bytes(), p.name)
                self.assertNotIn(b"WVWZZZ", p.read_bytes(), p.name)

    def test_report_content(self):
        self.run_import()
        report = (self.root / "incoming" / "REPORT.md").read_text(encoding="utf-8")
        for needle in ("JD_ISOBUS_SPRAYER", "process-data", "DDI161", "[#10](https://github.com/example/tc/issues/10)",
                       "[#20]", "[#30]", "https://github.com/user-attachments/files/1001/pools.zip",
                       "non-ascii", "SULKY_ISOLINK_SPREADER_6sec.ddop", "## Skipped"):
            self.assertIn(needle, report)
        self.assertNotIn("WVWZZZ", report)

    def test_second_run_finds_nothing_new(self):
        self.run_import()
        before = {p.name: p.read_bytes() for p in (self.root / "incoming").iterdir()}
        out = self.run_import()
        self.assertEqual(out.candidates, [])
        self.assertEqual(len(out.duplicates), 5)
        self.assertEqual({p.name: p.read_bytes() for p in (self.root / "incoming").iterdir()}, before)

    def test_nothing_written_when_nothing_new(self):
        out = self.run_import(numbers=[999])
        self.assertEqual(out.candidates, [])
        self.assertFalse((self.root / "incoming").exists())

    def test_issue_and_since_filters(self):
        self.assertEqual(len(self.run_import(numbers=[30]).candidates), 1)  # nested zip only
        shutil.rmtree(self.root / "incoming")
        out = self.run_import(since=datetime(2026, 9, 1, tzinfo=timezone.utc))
        # #10 body (updated 09-02) and its 09-02 comment, #20; not the old #30, not the 08-02 comment.
        self.assertEqual(sorted(c.summary["designator"] for c in out.candidates).__len__(), 2)
        self.assertEqual({c.ref.issue for c in out.candidates}, {10, 20})

    def test_incoming_output_passes_check(self):
        self.run_import()
        self.assertEqual(check_pools.run(self.root).errors, [])

    def test_extensionless_and_nested_are_found(self):
        out = self.run_import()
        labels = {c.label for c in out.candidates}
        self.assertTrue(any(l.endswith("/files/1002/hu") for l in labels))
        self.assertTrue(any(l.endswith("outer.zip!inner.zip!nested/old.ddop") for l in labels))

    def test_report_cell_neutralises_markup_and_control_characters(self):
        self.assertEqual(import_ddops.cell("a|b`c\x07\nd"), "`a\\|b'c\\x07\\x0ad`")

    def test_parse_helpers(self):
        self.assertEqual(import_ddops.parse_numbers("1, 2 3"), [1, 2, 3])
        with self.assertRaises(SystemExit):
            import_ddops.parse_numbers("1; rm -rf")
        urls = import_ddops.ATTACHMENT_URL.findall(
            "see (https://github.com/user-attachments/files/1/a%20b.zip) and https://github.com/user-attachments/assets/ab-12.")
        self.assertEqual(urls, ["https://github.com/user-attachments/files/1/a%20b.zip",
                                "https://github.com/user-attachments/assets/ab-12."])


MIT_TEXT = ("MIT License\n\nCopyright (c) 2026 Example Authors\n\nPermission is hereby granted, free of charge, "
            "to any person obtaining a copy of this software and associated documentation files (the \"Software\"), "
            "to deal in the Software without restriction, including without limitation the rights to use.\n")
GPL_TEXT = "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n"


def make_source_repo(path: Path, licence: str | None, files: dict[str, bytes]) -> str:
    path.mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a],
                                    cwd=path, check=True, capture_output=True)
    run("init", "--quiet")
    if licence is not None:
        files = {"LICENSE": licence.encode(), **files}
    for name, data in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    run("add", "-A")
    run("commit", "--quiet", "-m", "init")
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, check=True).stdout.decode().strip()


class ImportIopsTests(TempDirCase):
    FILES = {
        "examples/vt/object_pool.iop": bytes(range(64)),
        "examples/deep/er/Second Pool!.IOP": b"\x01\x02\x03",
        "examples/readme.md": b"not a pool",
        "docs/other.iop": b"outside the glob",
    }
    A = "unsorted/SOURCEREPO_vt_object_pool.iop"
    B = "unsorted/SOURCEREPO_deep_er_Second_Pool.iop"

    def setUp(self):
        super().setUp()
        self.root = copy_tree(self.tmp / "repo")

    def do_import(self, remote: Path, **kw):
        args = dict(repo="example/SourceRepo-x", ref="HEAD", pattern="examples/**/*.iop", pool_type="unsorted",
                    brand="SOURCEREPO", licence_override=None, remote=remote.as_uri(), max_bytes=1000)
        args.update(kw)
        return quiet(import_iops.run, self.root, **args)

    def sources(self):
        return {r["file"]: r for r in sources_md.parse((self.root / "iop" / "SOURCES.md").read_text(encoding="utf-8"))}

    def test_import_names_like_ddops_records_and_passes_check(self):
        remote = self.tmp / "src"
        sha = make_source_repo(remote, MIT_TEXT, self.FILES)
        self.do_import(remote)
        iop = self.root / "iop"
        self.assertEqual((iop / self.A).read_bytes(), self.FILES["examples/vt/object_pool.iop"])
        self.assertTrue((iop / self.B).is_file())                     # sanitised, same scheme as ddop/
        self.assertEqual(sorted(p.name for p in iop.iterdir()), ["LICENSES", "SOURCES.md", "manifest.csv", "unsorted"])
        self.assertEqual((iop / "LICENSES" / "example_SourceRepo-x.txt").read_text(encoding="utf-8"), MIT_TEXT)
        rows = self.sources()
        self.assertEqual(set(rows), {self.A, self.B})
        row = rows[self.B]
        self.assertEqual((row["repo"], row["path"], row["ref"], row["licence"]),
                         ("example/SourceRepo-x", "examples/deep/er/Second Pool!.IOP", sha, "MIT"))
        manifest = {r["file"]: r for r in build_manifest.read_manifest(iop / "manifest.csv")}
        self.assertEqual({r["type"] for r in manifest.values()}, {"unsorted"})
        self.assertEqual(check_pools.run(self.root).errors, [])

    def test_type_and_brand_options(self):
        remote = self.tmp / "src"
        make_source_repo(remote, MIT_TEXT, self.FILES)
        self.do_import(remote, pool_type="sprayer", brand="ACME")
        self.assertTrue((self.root / "iop/sprayer/ACME_vt_object_pool.iop").is_file())
        self.assertEqual(check_pools.run(self.root).errors, [])
        self.assertEqual(import_iops.default_brand("Open-Agriculture/AgIsoStack-plus-plus"), "AGISOSTACK")
        self.assertEqual(import_iops.model_name("examples/vt/a b/object_pool.iop", ["examples"]), "vt_a_b_object_pool")

    def test_rerun_is_idempotent_and_update_records_new_commit(self):
        remote = self.tmp / "src"
        make_source_repo(remote, MIT_TEXT, self.FILES)
        self.do_import(remote)
        first = (self.root / "iop" / "SOURCES.md").read_text(encoding="utf-8")
        self.do_import(remote)
        self.assertEqual((self.root / "iop" / "SOURCES.md").read_text(encoding="utf-8"), first)
        (remote / "examples/vt/object_pool.iop").write_bytes(b"changed")
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e.invalid", "commit", "-qam", "edit"],
                       cwd=remote, check=True, capture_output=True)
        self.do_import(remote)
        self.assertEqual((self.root / "iop" / self.A).read_bytes(), b"changed")
        self.assertNotEqual((self.root / "iop" / "SOURCES.md").read_text(encoding="utf-8"), first)
        self.assertEqual(check_pools.run(self.root).errors, [])

    def test_licence_is_recorded_never_enforced(self):
        cases = (("gpl", GPL_TEXT, "GPL-3.0", True), ("odd", "Do what you like.\n", "unrecognised", True),
                 ("none", None, "unspecified", False))
        for name, licence, recorded, has_text in cases:
            root = copy_tree(self.tmp / ("repo-" + name))
            remote = self.tmp / name
            make_source_repo(remote, licence, self.FILES)
            args = dict(repo="example/SourceRepo-x", ref="HEAD", pattern="examples/**/*.iop", pool_type="unsorted",
                        brand="SOURCEREPO", licence_override=None, remote=remote.as_uri(), max_bytes=1000)
            quiet(import_iops.run, root, **args)
            rows = {r["file"]: r for r in sources_md.parse((root / "iop" / "SOURCES.md").read_text(encoding="utf-8"))}
            self.assertEqual({r["licence"] for r in rows.values()}, {recorded}, name)
            self.assertEqual((root / "iop" / "LICENSES" / "example_SourceRepo-x.txt").is_file(), has_text, name)
            self.assertEqual(check_pools.run(root).errors, [], name)
        # --licence names an unrecognised text
        root = copy_tree(self.tmp / "repo-override")
        args["licence_override"] = "custom terms"
        quiet(import_iops.run, root, **args)
        self.assertIn("custom terms", (root / "iop" / "SOURCES.md").read_text(encoding="utf-8"))

    def test_name_clash_with_another_repository_stops(self):
        remote = self.tmp / "src"
        make_source_repo(remote, MIT_TEXT, self.FILES)
        self.do_import(remote)
        with self.assertRaises(SystemExit):
            self.do_import(remote, repo="someone/Else")

    def test_two_sources_with_the_same_name_stop(self):
        remote = self.tmp / "src"
        make_source_repo(remote, MIT_TEXT, {"examples/a b.iop": b"1", "examples/a_b.iop": b"2"})
        with self.assertRaises(SystemExit):
            self.do_import(remote)

    def test_size_limit_skips_large_files(self):
        remote = self.tmp / "src"
        make_source_repo(remote, MIT_TEXT, {**self.FILES, "examples/big.iop": bytes(5000)})
        self.do_import(remote)
        self.assertFalse((self.root / "iop/unsorted/SOURCEREPO_big.iop").exists())

    def test_check_flags_bad_iop_names_and_folders(self):
        (self.root / "iop" / "unsorted").mkdir()
        (self.root / "iop" / "unsorted" / "bad name.iop").write_bytes(b"\x01")
        (self.root / "iop" / "loose.iop").write_bytes(b"\x01")
        errors = "\n".join(check_pools.run(self.root).errors)
        self.assertIn("bad\\x20name.iop: name must match", errors)
        self.assertIn("iop/loose.iop: must be iop/<", errors)

    def test_glob(self):
        m = import_iops.glob_regex("examples/**/*.iop")
        for yes in ("examples/a.iop", "examples/x/y/a.iop"):
            self.assertTrue(m.match(yes), yes)
        for no in ("docs/a.iop", "examples/a.iop.txt", "xexamples/a.iop"):
            self.assertFalse(m.match(no), no)
        self.assertFalse(import_iops.glob_regex("a/*.iop").match("a/b/c.iop"))
        with self.assertRaises(SystemExit):
            import_iops.glob_regex("../x/*.iop")

    def test_licence_detection(self):
        self.assertEqual(import_iops.detect_licence(MIT_TEXT), "MIT")
        self.assertEqual(import_iops.detect_licence(GPL_TEXT), "GPL-3.0")
        self.assertIsNone(import_iops.detect_licence("hello"))


if __name__ == "__main__":
    unittest.main()
