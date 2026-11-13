"""Write a Windows shell link (.lnk) without Windows (MS-SHLLINK).

Only needed once, to put the desktop shortcut back from outside the computer;
the app itself creates shortcuts through WScript.Shell.

The link carries the target twice, like links Windows writes for %VAR% paths:
  * a LinkTargetIDList (My Computer > H:\\ > folder > file), with the long
    Unicode names in BEEF0004 extension blocks and ANSI (GBK) short names;
  * an EnvironmentVariableDataBlock with the full Unicode target path.
"""
import struct
import sys
import uuid
from datetime import datetime

LINK_CLSID = uuid.UUID("00021401-0000-0000-C000-000000000046").bytes_le
MY_COMPUTER = uuid.UUID("20D04FE0-3AEA-1069-A2D8-08002B30309D").bytes_le

HasLinkTargetIDList = 0x1
HasName = 0x4
HasWorkingDir = 0x10
HasIconLocation = 0x40
IsUnicode = 0x80
ForceNoLinkInfo = 0x100
HasExpString = 0x200


def dos_datetime(dt):
    date = ((dt.year - 1980) << 9) | (dt.month << 5) | dt.day
    time = (dt.hour << 11) | (dt.minute << 5) | (dt.second // 2)
    return struct.pack("<HH", date, time)


def filetime(dt):
    return struct.pack("<Q", int((dt.timestamp() + 11644473600) * 10_000_000))


def item(body):
    return struct.pack("<H", len(body) + 2) + body


def file_entry(name, is_dir, size, now, ansi="gbk"):
    short = name.encode(ansi, errors="replace") + b"\x00"
    if len(short) % 2:
        short += b"\x00"
    attrs = 0x10 if is_dir else 0x20
    body = bytes([0x31 if is_dir else 0x32, 0x00]) + struct.pack("<I", size) + dos_datetime(now)
    body += struct.pack("<H", attrs) + short
    long_name = name.encode("utf-16-le") + b"\x00\x00"
    ext = struct.pack("<HI", 0x03, 0xBEEF0004) + dos_datetime(now) + dos_datetime(now)
    ext += struct.pack("<HH", 0x14, 0) + long_name  # identifier, long-string size (0: no localized name)
    ext_with_size = struct.pack("<H", len(ext) + 2 + 2) + ext
    body += ext_with_size + struct.pack("<H", len(body) + 2)  # offset of the extension block
    return item(body)


def string_data(text):
    return struct.pack("<H", len(text)) + text.encode("utf-16-le")


def make(lnk_path, target, work_dir, icon, description, file_size=0):
    now = datetime.now()
    parts = target.split("\\")
    drive, names = parts[0] + "\\", parts[1:]
    items = item(bytes([0x1F, 0x50]) + MY_COMPUTER)
    items += item(bytes([0x2F]) + drive.encode("ascii").ljust(22, b"\x00"))
    for i, name in enumerate(names):
        last = i == len(names) - 1
        items += file_entry(name, not last, file_size if last else 0, now)
    idlist = struct.pack("<H", len(items) + 2) + items + b"\x00\x00"

    flags = HasLinkTargetIDList | HasName | HasWorkingDir | HasIconLocation | IsUnicode | ForceNoLinkInfo | HasExpString
    header = struct.pack("<I", 0x4C) + LINK_CLSID + struct.pack("<II", flags, 0x20)
    header += filetime(now) * 3 + struct.pack("<IiIHHII", file_size, 0, 1, 0, 0, 0, 0)
    assert len(header) == 0x4C

    data = string_data(description) + string_data(work_dir) + string_data(icon)

    ansi_target = target.encode("gbk", errors="replace")[:259].ljust(260, b"\x00")
    uni_target = target.encode("utf-16-le")[:518].ljust(520, b"\x00")
    env_block = struct.pack("<II", 0x314, 0xA0000001) + ansi_target + uni_target
    assert len(env_block) == 0x314
    extra = env_block + struct.pack("<I", 0)

    with open(lnk_path, "wb") as handle:
        handle.write(header + idlist + data + extra)


if __name__ == "__main__":
    make(*sys.argv[1:6], file_size=int(sys.argv[6]) if len(sys.argv) > 6 else 0)
