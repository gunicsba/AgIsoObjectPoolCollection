"""Generate saved sample data for the import scripts, from the pools already in ddop/.

    python scripts/tests/sample_data.py OUTDIR

writes OUTDIR/issues.json, OUTDIR/attachments.json and OUTDIR/attachments/*, the input format of
`import_ddops.py --fixtures OUTDIR`. The pools are the committed (scrubbed) ones with a fake VIN
serial and identity number put back, so the importer has something to scrub; those unscrubbed
variants only ever exist in the output directory, never in the repository.
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import poollib  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FAKE_SERIAL = b"WVWZZZ1JZXW000001"  # obviously fake, VIN shaped
FAKE_IDENTITY = 0x1ABCDE
BASE = "https://github.com/user-attachments"


def pool(rel: str) -> bytes:
    return (ROOT / "ddop" / rel).read_bytes()


def with_identity(data: bytes, serial: bytes = FAKE_SERIAL, identity: int = FAKE_IDENTITY,
                  name_xor: int = 0, label: bytes | None = None) -> bytes:
    """Put a serial and identity number back; optionally change the NAME (=> a different machine)."""
    d = poollib.parse(data).dvc
    b = bytearray(data)
    name = ((d.name & ~poollib.IDENTITY_NUMBER_MASK) | identity) ^ name_xor
    b[d.name_offset:d.name_offset + 8] = name.to_bytes(8, "little")
    if label is not None:
        b[d.localization_offset:d.localization_offset + 7] = label
    head, tail = bytes(b[:d.serial_offset - 1]), bytes(b[d.serial_offset + len(d.serial):])
    return head + bytes([len(serial)]) + serial + tail


def zipped(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def build(out: Path) -> dict:
    """Write the sample data and return what a correct import should find."""
    new_sprayer = with_identity(pool("sprayer/JD_740_SPRAYER_9sec.ddop"), name_xor=1 << 33)
    new_seeder = with_identity(pool("seeder/KUHN_ESPRO_2sec_DDI141_hu.ddop"), name_xor=1 << 34)
    new_combined = with_identity(pool("combined/AMAZONE_PRECEA_4500_6sec_2booms.ddop"), name_xor=1 << 35)
    # Already in ddop/: same machine, other serial, other localization label.
    known = with_identity(pool("spreader/SULKY_ISOLINK_SPREADER_6sec.ddop"),
                          serial=b"SN-99887766", identity=0x00042, label=b"en" + bytes(5))
    nested = with_identity(pool("tractor/JD_6R175_6000RW_TRACTOR.ddop"), name_xor=1 << 36)
    broken = b"DVC\x01\x00\x05ABC"  # starts like a DDOP, is not one

    attachments = {
        "%s/files/1001/pools.zip" % BASE: zipped({
            "machines/sprayer.ddop": new_sprayer,
            "machines/same_as_known.ddop": known,
            "logs/canbus.log": b"log line\n" * 50,
            "trace.trc": b"\x00\x01" * 50,
            "screenshot.png": b"\x89PNG\r\n\x1a\n" + bytes(40),
            "README.txt": b"my machine\n",
        }),
        # No extension at all, like the real files named just "hu" or "en".
        "%s/files/1002/hu" % BASE: new_seeder,
        "%s/files/1003/canbus.log" % BASE: b"log line\n" * 50,
        "%s/files/1004/installer.exe" % BASE: b"MZ" + bytes(200),
        "%s/assets/6b3f5d0e-1111-4c1a-9a55-3c1f2e5b7a10" % BASE: new_combined,
        "%s/files/1005/big.bin" % BASE: b"X" * 9000,
        "%s/files/1006/broken.zip" % BASE: zipped({"truncated.ddop": broken}),
        "%s/files/1007/outer.zip" % BASE: zipped({"inner.zip": zipped({"nested/old.ddop": nested})}),
    }
    files = {}
    (out / "attachments").mkdir(parents=True, exist_ok=True)
    for i, (url, data) in enumerate(attachments.items()):
        name = "attachments/%02d.bin" % i
        (out / name).write_bytes(data)
        files[url] = name
    (out / "attachments.json").write_text(json.dumps(files, indent=1), encoding="utf-8")

    def link(n, name):
        return "[%s](%s)" % (name, [u for u in attachments if "/%d/" % n in u][0])

    issues = [
        {"number": 10, "title": "Sprayer", "url": "https://github.com/example/tc/issues/10",
         "createdAt": "2026-08-01T10:00:00Z", "updatedAt": "2026-09-02T10:00:00Z",
         "body": "My sprayer, pools attached: %s and broken: %s" % (link(1001, "pools.zip"), link(1006, "broken.zip")),
         "comments": [
             {"createdAt": "2026-08-02T10:00:00Z", "url": "https://github.com/example/tc/issues/10#c1",
              "body": "Seeder pool (extensionless): %s\nlog: %s" % (link(1002, "hu"), link(1003, "canbus.log"))},
             {"createdAt": "2026-09-02T10:00:00Z", "url": "https://github.com/example/tc/issues/10#c2",
              "body": "Same zip again %s. Installer %s" % (link(1001, "pools.zip"), link(1004, "installer.exe"))},
         ]},
        {"number": 20, "title": "Planter", "url": "https://github.com/example/tc/issues/20",
         "createdAt": "2026-09-05T10:00:00Z", "updatedAt": "2026-09-05T10:00:00Z",
         "body": "![img](%s/assets/6b3f5d0e-1111-4c1a-9a55-3c1f2e5b7a10) and a huge one %s." % (BASE, link(1005, "big.bin")),
         "comments": []},
        {"number": 30, "title": "Old tractor", "url": "https://github.com/example/tc/issues/30",
         "createdAt": "2025-02-01T10:00:00Z", "updatedAt": "2025-02-03T10:00:00Z",
         "body": "Nested zip %s" % link(1007, "outer.zip"), "comments": []},
    ]
    (out / "issues.json").write_text(json.dumps(issues, indent=1), encoding="utf-8")
    return {
        "new": [new_sprayer, new_seeder, new_combined, nested],
        "known": known, "fake_serial": FAKE_SERIAL,
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    build(Path(sys.argv[1]))
    print("sample data written to", sys.argv[1])
