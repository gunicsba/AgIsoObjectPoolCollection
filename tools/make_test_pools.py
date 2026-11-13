"""Writes the hand-made VT test pools in pools/open-agriculture/test-*/pool.iop.

Each pool has one data mask that shows one kind of object in many variants, so a
drawing change shows up in a vt-render before/after comparison. Run it from the
repository root: python tools/make_test_pools.py
"""

import pathlib
import struct

ROOT = pathlib.Path(__file__).resolve().parents[1] / "pools" / "open-agriculture"


def u8(value):
    return struct.pack("<B", value)


def u16(value):
    return struct.pack("<H", value)


def i16(value):
    return struct.pack("<h", value)


def u32(value):
    return struct.pack("<I", value)


def i32(value):
    return struct.pack("<i", value)


def f32(value):
    return struct.pack("<f", value)


def pool(children, objects):
    """A working set with one data mask (1000) holding the children."""
    working_set = u16(0) + u8(0) + u8(1) + u8(1) + u16(1000) + u8(0) + u8(0) + u8(0)
    data_mask = u16(1000) + u8(1) + u8(1) + u16(0xFFFF) + u8(len(children)) + u8(0) + b"".join(children)
    return working_set + data_mask + b"".join(objects)


def rectangle_line_suppression():
    """Every line suppression value, with a solid line and with line art."""
    objects = [
        u16(100) + u8(24) + u8(0) + u8(6) + u16(0xFFFF) + u8(0),  # Line attributes: solid, width 6
        u16(101) + u8(24) + u8(0) + u8(6) + u16(0xAAAA) + u8(0),  # Line attributes: line art 1010..., width 6
        u16(200) + u8(25) + u8(2) + u8(8) + u16(0xFFFF) + u8(0),  # Fill attributes: grey
    ]
    children = []
    object_id = 2000
    for row, line in enumerate((100, 101)):
        for suppression in range(16):
            x = 8 + (suppression % 8) * 58
            y = 8 + (suppression // 8) * 58 + row * 130
            objects.append(u16(object_id) + u8(14) + u16(line) + u16(50) + u16(40) + u8(suppression) + u16(200) + u8(0))
            children.append(u16(object_id) + i16(x) + i16(y))
            object_id += 1
    return pool(children, objects)


def output_meter():
    """Meters at the minimum, middle and maximum, with and without border."""
    objects = []
    children = []
    for index, (options, value) in enumerate([(0x07, 50), (0x07, 0), (0x07, 100), (0x05, 30)]):
        object_id = 3000 + index
        objects.append(u16(object_id) + u8(17) + u16(100) + u8(0) + u8(0) + u8(12) + u8(options) + u8(11) + u8(0) + u8(90) +
                       u16(0) + u16(100) + u16(0xFFFF) + u16(value) + u8(0))
        children.append(u16(object_id) + i16(10 + index * 115) + i16(10))
    return pool(children, objects)


def output_number_rounding():
    """Values that float scales get wrong, rounded in the left column and truncated in the right one."""
    cases = [  # value, scale, decimals, options
        (12345678, 0.00001, 5, 0),
        (12345678, 0.00001, 4, 0),
        (10000, 0.01, 2, 0),
        (2675, 0.001, 2, 0),
        (999, 0.001, 2, 0),
        (123456, 1.0, 0, 0),
        (4, 0.001, 2, 4),  # Zero as blank: shown as 0.00, so blank
        (0, 0.01, 2, 4),
    ]
    objects = [u16(100) + u8(23) + u8(0) + u8(4) + u8(0) + u8(0) + u8(0)]  # Font attributes: black 12x16
    children = []
    object_id = 4000
    for row, (value, scale, decimals, options) in enumerate(cases):
        for column, truncate in enumerate((0, 8)):
            objects.append(u16(object_id) + u8(12) + u16(150) + u16(24) + u8(1) + u16(100) + u8(options | truncate) + u16(0xFFFF) +
                           u32(value) + i32(0) + f32(scale) + u8(decimals) + u8(0) + u8(0) + u8(0))
            children.append(u16(object_id) + i16(10 + column * 170) + i16(10 + row * 30))
            object_id += 1
    return pool(children, objects)


def output_string(object_id, width, height, text, font=100):
    """A transparent Output String with the text."""
    return (u16(object_id) + u8(11) + u16(width) + u16(height) + u8(1) + u16(font) + u8(1) + u16(0xFFFF) + u8(0) +
            u16(len(text)) + text.encode("latin-1") + u8(0))


def focus_frame():
    """Input fields that look like plain text, as old pools draw them, a Button and a soft key.

    Render it with vt-render --focus <id> to see the focus frame on one of them:
    4001 Input Number, 4002 Input String, 4003 Input List, 4004 Input Boolean, 4005 Button, 5001 Key.
    """
    objects = [
        u16(100) + u8(23) + u8(0) + u8(4) + u8(0) + u8(0) + u8(0),  # Font attributes: black 12x16
        u16(4001) + u8(9) + u16(180) + u16(24) + u8(1) + u16(100) + u8(1) + u16(0xFFFF) + u32(1250) + u32(0) +
        u32(100000) + i32(0) + f32(0.01) + u8(2) + u8(0) + u8(0) + u8(1) + u8(0),
        u16(4002) + u8(8) + u16(180) + u16(24) + u8(1) + u16(100) + u16(0xFFFF) + u8(1) + u16(0xFFFF) + u8(0) +
        u8(10) + b"Field 12  " + u8(1) + u8(0),
        u16(4003) + u8(10) + u16(180) + u16(24) + u16(0xFFFF) + u8(0) + u8(2) + u8(1) + u8(0) + u16(4101) + u16(4102),
        output_string(4101, 180, 24, "Select one"),
        output_string(4102, 180, 24, "Seeder"),
        u16(4004) + u8(7) + u8(1) + u16(24) + u16(100) + u16(0xFFFF) + u8(1) + u8(1) + u8(0),
        u16(4005) + u8(6) + u16(120) + u16(40) + u8(7) + u8(0) + u8(1) + u8(0) + u8(1) + u8(0) + u16(4105) + i16(10) + i16(8),
        output_string(4105, 100, 24, "Start"),
        u16(2000) + u8(4) + u8(7) + u8(1) + u8(0) + u16(5001),  # Soft Key Mask with one Key
        u16(5001) + u8(5) + u8(7) + u8(2) + u8(1) + u8(0) + u16(5101) + i16(4) + i16(18),
        output_string(5101, 52, 24, "Next"),
    ]
    labels = ["Number", "String", "List", "Boolean"]
    children = []
    for row, label in enumerate(labels):
        objects.append(output_string(4201 + row, 120, 24, label + ":"))
        children.append(u16(4201 + row) + i16(20) + i16(30 + row * 50))
        children.append(u16(4001 + row) + i16(160) + i16(30 + row * 50))
    children.append(u16(4005) + i16(160) + i16(240))
    working_set = u16(0) + u8(0) + u8(1) + u8(1) + u16(1000) + u8(0) + u8(0) + u8(0)
    data_mask = u16(1000) + u8(1) + u8(1) + u16(2000) + u8(len(children)) + u8(0) + b"".join(children)
    return working_set + data_mask + b"".join(objects)


def main():
    pools = {
        "test-rectangle-line-suppression": rectangle_line_suppression(),
        "test-output-meter": output_meter(),
        "test-output-number-rounding": output_number_rounding(),
        "test-focus-frame": focus_frame(),
    }
    for slug, data in pools.items():
        folder = ROOT / slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "pool.iop").write_bytes(data)
        print(f"Wrote {folder / 'pool.iop'}")


if __name__ == "__main__":
    main()
