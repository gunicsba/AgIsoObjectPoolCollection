"""Shared by the issue importers: list a repository's issues, find and download their attachments,
and look inside zips (also zips inside zips) for the files an importer wants.

Attachment links are github.com/user-attachments/... and download without a token. Live runs use
the gh CLI to list issues with their comments; FixtureSource reads the same data from a directory.
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

ISSUE_FIELDS = "number,title,body,comments,createdAt,updatedAt,url"
ATTACHMENT_URL = re.compile(r"https://github\.com/user-attachments/(?:files|assets)/[A-Za-z0-9._~%/+-]+")
REPO_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# Logs, traces, installers and media are never pools; skip them without opening.
IGNORED_SUFFIXES = {
    ".log", ".trc", ".trace", ".asc", ".blf", ".pcap", ".pcapng", ".candump",
    ".exe", ".msi", ".msix", ".appx", ".dmg", ".pkg", ".deb", ".rpm", ".apk", ".appimage",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".mp4", ".mov", ".webm",
}
MAX_ZIP_ENTRIES = 5000
MAX_ZIP_DEPTH = 2

# want(name, first_bytes) -> True when the file is one the importer is after.
Want = Callable[[str, bytes], bool]


class DownloadError(Exception):
    pass


# --- sources of issues and attachments -------------------------------------------------------

class GhSource:
    """Live: the gh CLI lists issues with their comments; attachments are plain HTTPS downloads."""

    def __init__(self, repo: str, max_bytes: int):
        self.repo = repo
        self.max_bytes = max_bytes

    def _gh(self, *args) -> str:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0:
            raise SystemExit("gh %s failed: %s" % (" ".join(args[:3]), proc.stderr.strip()))
        return proc.stdout

    def issues(self, numbers: list[int], since: datetime | None) -> list[dict]:
        if numbers:
            return [json.loads(self._gh("issue", "view", str(n), "-R", self.repo, "--json", ISSUE_FIELDS))
                    for n in numbers]
        args = ["issue", "list", "-R", self.repo, "--state", "all", "--limit", "5000", "--json", ISSUE_FIELDS]
        if since:
            args += ["--search", "updated:>=%s" % since.date().isoformat()]
        return json.loads(self._gh(*args))

    def download(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "agiso-object-pool-collection"})
        last = None
        for _ in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    length = resp.headers.get("Content-Length")
                    if length and int(length) > self.max_bytes:
                        raise DownloadError("larger than the size limit (%s bytes)" % length)
                    data = resp.read(self.max_bytes + 1)
                if len(data) > self.max_bytes:
                    raise DownloadError("larger than the size limit")
                return data
            except DownloadError:
                raise
            except urllib.error.HTTPError as e:
                if 400 <= e.code < 500:
                    raise DownloadError("download failed: HTTP %d" % e.code)
                last = e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = e
        raise DownloadError("download failed: %s" % last)


class FixtureSource:
    """Saved data: DIR/issues.json (the gh output shape) and DIR/attachments.json (url -> file)."""

    def __init__(self, directory: Path, max_bytes: int):
        self.dir = directory
        self.max_bytes = max_bytes
        self.files = json.loads((directory / "attachments.json").read_text(encoding="utf-8"))

    def issues(self, numbers: list[int], since: datetime | None) -> list[dict]:
        data = json.loads((self.dir / "issues.json").read_text(encoding="utf-8"))
        if numbers:
            data = [i for i in data if i["number"] in numbers]
        return data

    def download(self, url: str) -> bytes:
        if url not in self.files:
            raise DownloadError("download failed: not in the fixtures")
        path = self.dir / self.files[url]
        if path.stat().st_size > self.max_bytes:
            raise DownloadError("larger than the size limit")
        return path.read_bytes()


# --- finding attachments ---------------------------------------------------------------------

@dataclass
class Reference:
    url: str
    issue: int
    issue_url: str


def parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def find_references(issues: list[dict], since: datetime | None) -> list[Reference]:
    """Attachment links in issue bodies and comments, first reference wins, oldest issue first."""
    refs: dict[str, Reference] = {}

    def scan(text: str | None, issue: dict):
        for url in ATTACHMENT_URL.findall(text or ""):
            url = url.rstrip(".,;:")
            refs.setdefault(url, Reference(url, issue["number"], issue.get("url", "")))

    for issue in sorted(issues, key=lambda i: i["number"]):
        stamps = [parse_time(issue[k]) for k in ("createdAt", "updatedAt") if issue.get(k)]
        if not since or not stamps or max(stamps) >= since:
            scan(issue.get("body"), issue)
        for c in issue.get("comments") or []:
            if not since or parse_time(c["createdAt"]) >= since:
                scan(c.get("body"), issue)
    return list(refs.values())


def url_suffix(url: str) -> str:
    return "." + url.rsplit(".", 1)[-1].lower() if "." in url.rsplit("/", 1)[-1] else ""


def parse_numbers(text: str) -> list[int]:
    parts = [p for p in re.split(r"[,\s]+", text.strip()) if p]
    if not all(p.isdigit() for p in parts):
        raise SystemExit("--issues takes issue numbers separated by commas or spaces, got %r" % text)
    return [int(p) for p in parts]


def parse_since(text: str) -> datetime | None:
    from datetime import timezone
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    except ValueError:
        raise SystemExit("--since must be a date like 2026-01-31, got %r" % text)


# --- looking inside files --------------------------------------------------------------------

@dataclass
class Found:
    label: str    # attachment URL, then "!member" for each zip level: safe to show after escaping
    member: str   # path inside the innermost zip, or the attachment name; never used for output names
    data: bytes


def scan_bytes(data: bytes, label: str, name: str, max_bytes: int, stats: Counter, want: Want, depth: int = 0):
    """Yield every wanted file inside data: the file itself, or members of a zip (nested up to a limit)."""
    if want(name, data[:4]):
        yield Found(label, name, data)
        return
    if data[:4] != b"PK\x03\x04" or not zipfile.is_zipfile(io.BytesIO(data)):
        stats["not a pool or a zip"] += 1
        return
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, OSError, NotImplementedError):
        stats["unreadable zip"] += 1
        return
    if len(infos) > MAX_ZIP_ENTRIES:
        stats["zip with too many entries"] += 1
        return
    for info in infos:
        member = info.filename
        if info.is_dir():
            continue
        shown = "%s!%s" % (label, member)
        if url_suffix(member) in IGNORED_SUFFIXES:
            stats["log, trace, installer or media file"] += 1
        elif info.flag_bits & 0x1:
            stats["encrypted zip member"] += 1
        elif info.file_size > max_bytes:
            stats["over the size limit"] += 1
        else:
            try:
                with zf.open(info) as f:
                    head = f.read(4)
                    if want(member, head):
                        body = head + f.read(max_bytes + 1 - len(head))
                        if len(body) > max_bytes:
                            stats["over the size limit"] += 1
                        else:
                            yield Found(shown, member, body)
                    elif head == b"PK\x03\x04" and depth < MAX_ZIP_DEPTH:
                        yield from scan_bytes(head + f.read(max_bytes), shown, member, max_bytes, stats, want, depth + 1)
                    else:
                        stats["not a pool or a zip"] += 1
            except (zipfile.BadZipFile, OSError, NotImplementedError, RuntimeError):
                stats["unreadable zip member"] += 1


# --- reports ---------------------------------------------------------------------------------

def cell(text: object, limit: int = 80) -> str:
    """Untrusted text (pool strings, member names) as a safe markdown table cell."""
    s = "".join(ch if 0x20 <= ord(ch) < 0x7F or ord(ch) > 0xA0 else "\\x%02x" % ord(ch) for ch in str(text))
    s = s.replace("`", "'").replace("|", "\\|")
    if len(s) > limit:
        s = s[:limit - 1] + "…"
    return "`%s`" % s if s else ""


# --- working-set NAMEs -----------------------------------------------------------------------
# The VT app names its dump folders, and users name their zips, after the implement's 64-bit NAME
# in hex, which includes its identity number. Nothing recorded in this repository may carry it.

_NAME_TOKEN = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{16}(?![0-9A-Fa-f])")


def mask_names(text: str) -> str:
    """Zero the identity number (NAME bits 0..20) of every 16-hex-digit token in text."""
    from poollib import IDENTITY_NUMBER_MASK
    return _NAME_TOKEN.sub(lambda m: "%016x" % (int(m[0], 16) & ~IDENTITY_NUMBER_MASK), text)


def unmasked_names(text: str) -> list[str]:
    """16-hex-digit tokens in text whose identity number is not zero."""
    from poollib import IDENTITY_NUMBER_MASK
    return sorted({t for t in _NAME_TOKEN.findall(text) if int(t, 16) & IDENTITY_NUMBER_MASK})
