"""Shared code for the pool collection scripts: DDOP parser, scrub, summary and content hash.

Standard library only. Layout handled is the DDOP binary serialisation used by ISO 11783-10 version 3:

    DVC  object id (2), designator (len+str), software version (len+str), NAME (8, LE),
         serial number (len+str), structure label (7), localization label (7)
    DET  object id (2), element type (1), designator (len+str), element number (2),
         parent id (2), child count (2), child ids (2 each)
    DPD  object id (2), DDI (2), properties (1), trigger methods (1), designator (len+str),
         presentation id (2)
    DPT  object id (2), DDI (2), value (4, signed), designator (len+str), presentation id (2)
    DVP  object id (2), offset (4), scale (4), decimals (1), designator (len+str)

Version 4 pools carry an extended structure label after the localization label. That is handled
on a best-effort basis (see Dvc.extended_structure_label) and has NOT been tested on a real pool.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

MAGIC = b"DVC"
TAGS = (b"DVC", b"DET", b"DPD", b"DPT", b"DVP")
MAX_FILE_BYTES = 40 * 1024 * 1024

ELEMENT_TYPES = {
    1: "Device", 2: "Function", 3: "Bin", 4: "Section",
    5: "Unit", 6: "Connector", 7: "NavigationReference",
}

# DDIs used to classify section geometry and control (see the README).
DDI_OFFSET_X = 134
DDI_OFFSET_Y = 135
DDI_WIDTHS = (67, 68, 70)
GEOMETRY_DDIS = (DDI_OFFSET_X, DDI_OFFSET_Y) + DDI_WIDTHS
DDI_CONNECTOR_TYPE = 157
# Section control method, in the priority order the TC uses.
SECTION_CONTROL_DDIS = ((290, "DDI290"), (161, "DDI161"), (141, "DDI141"))

IDENTITY_NUMBER_MASK = 0x1FFFFF  # NAME bits 0..20
SCRUB_CHAR = 0x30  # '0'


class PoolError(ValueError):
    """The bytes are not a DDOP this module can parse."""


def looks_like_ddop(data: bytes) -> bool:
    return data[:3] == MAGIC


@dataclass
class Dvc:
    designator: bytes
    software_version: bytes
    name: int
    serial: bytes
    structure_label: bytes
    localization_label: bytes
    extended_structure_label: bytes | None
    # Byte offsets into the pool, used by scrub and content_hash.
    name_offset: int
    serial_offset: int
    localization_offset: int


@dataclass
class Element:
    id: int
    type: int
    designator: bytes
    number: int
    parent: int
    children: tuple[int, ...]

    @property
    def type_name(self) -> str:
        return ELEMENT_TYPES.get(self.type, str(self.type))


@dataclass
class ProcessData:
    id: int
    ddi: int
    properties: int
    triggers: int
    designator: bytes


@dataclass
class Property:
    id: int
    ddi: int
    value: int
    designator: bytes


@dataclass
class Pool:
    dvc: Dvc
    elements: list[Element] = field(default_factory=list)
    process_data: list[ProcessData] = field(default_factory=list)
    properties: list[Property] = field(default_factory=list)
    presentation_designators: list[bytes] = field(default_factory=list)


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def at_end(self) -> bool:
        return self.pos >= len(self.data)

    def peek(self, n: int) -> bytes:
        return self.data[self.pos:self.pos + n]

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise PoolError("truncated at offset %d (need %d bytes)" % (self.pos, n))
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def unpack(self, fmt: str):
        st = struct.Struct("<" + fmt)
        return st.unpack(self.take(st.size))

    def u8(self) -> int:
        return self.take(1)[0]

    def pstr(self) -> bytes:
        return self.take(self.u8())


def parse(data: bytes) -> Pool:
    """Parse a DDOP. Raises PoolError on anything it does not understand."""
    if not looks_like_ddop(data):
        raise PoolError("does not start with the DVC tag")
    r = _Reader(data)
    pool: Pool | None = None
    while not r.at_end():
        tag = r.take(3)
        if tag == b"DVC":
            if pool is not None:
                raise PoolError("more than one DVC object")
            r.take(2)  # object id
            designator = r.pstr()
            version = r.pstr()
            name_offset = r.pos
            name = int.from_bytes(r.take(8), "little")
            n = r.u8()
            serial_offset = r.pos
            serial = r.take(n)
            structure = r.take(7)
            localization_offset = r.pos
            localization = r.take(7)
            extended = None
            if not r.at_end() and r.peek(3) not in TAGS:
                # Version 4: length-prefixed extended structure label. Untested on real pools.
                extended = r.pstr()
                if not r.at_end() and r.peek(3) not in TAGS:
                    raise PoolError("unexpected bytes after the DVC header at offset %d" % r.pos)
            pool = Pool(Dvc(designator, version, name, serial, structure, localization, extended,
                            name_offset, serial_offset, localization_offset))
        elif pool is None:
            raise PoolError("DVC must come first")
        elif tag == b"DET":
            oid, etype = r.unpack("HB")
            designator = r.pstr()
            number, parent, count = r.unpack("HHH")
            children = r.unpack("%dH" % count) if count else ()
            pool.elements.append(Element(oid, etype, designator, number, parent, tuple(children)))
        elif tag == b"DPD":
            oid, ddi, props, trig = r.unpack("HHBB")
            designator = r.pstr()
            r.take(2)  # presentation object id
            pool.process_data.append(ProcessData(oid, ddi, props, trig, designator))
        elif tag == b"DPT":
            oid, ddi, value = r.unpack("HHi")
            designator = r.pstr()
            r.take(2)  # presentation object id
            pool.properties.append(Property(oid, ddi, value, designator))
        elif tag == b"DVP":
            r.take(2 + 4 + 4 + 1)  # id, offset, scale, decimals
            pool.presentation_designators.append(r.pstr())
        else:
            raise PoolError("unknown tag %r at offset %d" % (tag, r.pos - 3))
    if pool is None:
        raise PoolError("no DVC object")
    return pool


# --- scrub -----------------------------------------------------------------------------------

def scrub_problems(data: bytes) -> list[str]:
    """Return the reasons a pool is NOT scrubbed; an empty list means it is."""
    dvc = parse(data).dvc
    problems = []
    if any(c not in (0, SCRUB_CHAR) for c in dvc.serial):
        problems.append("serial number string is not only '0' or NUL")
    if dvc.name & IDENTITY_NUMBER_MASK:
        problems.append("NAME identity number (bits 0..20) is not zero")
    return problems


def is_scrubbed(data: bytes) -> bool:
    return not scrub_problems(data)


def scrub(data: bytes) -> bytes:
    """Replace every non-NUL serial byte with '0' and zero the NAME identity number.

    Length and layout are unchanged. Raises PoolError when the pool cannot be parsed, so an
    unparseable file is never passed on unscrubbed.
    """
    dvc = parse(data).dvc
    out = bytearray(data)
    name = dvc.name & ~IDENTITY_NUMBER_MASK
    out[dvc.name_offset:dvc.name_offset + 8] = name.to_bytes(8, "little")
    for i in range(dvc.serial_offset, dvc.serial_offset + len(dvc.serial)):
        if out[i] != 0:
            out[i] = SCRUB_CHAR
    result = bytes(out)
    if len(result) != len(data) or scrub_problems(result):
        raise PoolError("scrub failed its own verification")
    return result


def write_scrubbed(path, data: bytes) -> bytes:
    """The only way the import scripts write a pool: scrub, verify, then write."""
    clean = scrub(data)
    if not is_scrubbed(clean):
        raise PoolError("refusing to write an unscrubbed pool")
    with open(path, "wb") as f:
        f.write(clean)
    return clean


def content_hash(data: bytes) -> str:
    """SHA-256 for duplicate detection: what the pool says about the machine, nothing else.

    The localization label is zeroed, the NAME identity number is zeroed and the serial number
    string is left out altogether (its length included: a VIN and a short serial from the same
    machine are still the same machine). Scrubbed and unscrubbed copies hash the same.
    """
    dvc = parse(data).dvc
    canon = bytearray(data)
    canon[dvc.localization_offset:dvc.localization_offset + 7] = bytes(7)
    name = dvc.name & ~IDENTITY_NUMBER_MASK
    canon[dvc.name_offset:dvc.name_offset + 8] = name.to_bytes(8, "little")
    del canon[dvc.serial_offset - 1:dvc.serial_offset + len(dvc.serial)]  # length byte + string
    return hashlib.sha256(bytes(canon)).hexdigest()


# --- summary ---------------------------------------------------------------------------------

def language(label: bytes) -> str:
    """Language code from the first two bytes of the localization label, or '-'.

    Some pools carry control characters there (AOG-TaskController issue #61); those give '-'.
    """
    code = label[:2]
    if len(code) == 2 and all(65 <= c <= 90 or 97 <= c <= 122 for c in code):
        return code.decode("ascii").lower()
    return "-"


def string_kind(pool: Pool) -> str:
    """ascii | non-ascii | none, over every designator in the pool (the serial is not one)."""
    strings = [pool.dvc.designator, pool.dvc.software_version]
    strings += [e.designator for e in pool.elements]
    strings += [p.designator for p in pool.process_data]
    strings += [p.designator for p in pool.properties]
    strings += pool.presentation_designators
    strings = [s for s in strings if s]
    if not strings:
        return "none"
    return "non-ascii" if any(c >= 0x80 for s in strings for c in s) else "ascii"


def geometry_kind(pool: Pool) -> str:
    """none | static | process-data | mixed | missing (see the README)."""
    sections = [e for e in pool.elements if e.type_name == "Section"]
    if not sections:
        return "none"
    static_ids = {p.id for p in pool.properties if p.ddi in GEOMETRY_DDIS}
    dynamic_ids = {p.id for p in pool.process_data if p.ddi in GEOMETRY_DDIS}
    static = sum(1 for s in sections for c in s.children if c in static_ids)
    dynamic = sum(1 for s in sections for c in s.children if c in dynamic_ids)
    if static and not dynamic:
        return "static"
    if dynamic and not static:
        return "process-data"
    if static and dynamic:
        return "mixed"
    return "missing"


def section_control(pool: Pool) -> str:
    ddis = {p.ddi for p in pool.process_data}
    for ddi, label in SECTION_CONTROL_DDIS:
        if ddi in ddis:
            return label
    return "none"


def connector_types(pool: Pool) -> list[int]:
    by_id = {p.id: p for p in pool.properties}
    found = {by_id[c].value for e in pool.elements if e.type_name == "Connector"
             for c in e.children if c in by_id and by_id[c].ddi == DDI_CONNECTOR_TYPE}
    return sorted(found)


def summarize(data: bytes) -> dict:
    """Facts about a pool. Never includes the serial number or the NAME identity number."""
    pool = parse(data)
    dvc = pool.dvc
    return {
        "designator": dvc.designator.decode("utf-8", "replace"),
        "software_version": dvc.software_version.decode("utf-8", "replace"),
        "manufacturer_code": (dvc.name >> 21) & 0x7FF,
        "sections": sum(1 for e in pool.elements if e.type_name == "Section"),
        "functions": sum(1 for e in pool.elements if e.type_name == "Function"),
        "geometry": geometry_kind(pool),
        "section_control": section_control(pool),
        "connector_types": connector_types(pool),
        "bytes": len(data),
        "language": language(dvc.localization_label),
        "strings": string_kind(pool),
        "extended_structure_label": dvc.extended_structure_label is not None,
    }
