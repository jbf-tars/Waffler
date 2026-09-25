#!/usr/bin/env python3
"""Check that Waffler.app declares a minimum macOS it can actually run on.

Reads the minimum macOS version every Mach-O binary in the bundle was built
for (LC_BUILD_VERSION minos, or the older LC_VERSION_MIN_MACOSX), including
each slice of a universal binary, and compares the highest one with
LSMinimumSystemVersion in the bundle's Info.plist.

Why this exists: until 3.14.100 the app declared 10.13.0, but the macOS 14
build runner installs NumPy's macosx_14_0_arm64 wheel, whose extension
modules are built for macOS 14.0 and call Accelerate functions
($NEWLAPACK$ILP64) that older releases do not have. NumPy is imported at
start-up (src/audio.py), so on an older Mac the app failed to open instead
of macOS refusing it cleanly. A dependency upgrade can raise the real
minimum again at any time, so the release build runs this check.

Usage:
    python scripts/check_macos_minos.py dist/Waffler.app

Exit status is 1 when any binary needs a newer macOS than the bundle
declares, 0 otherwise. Standard library only, so it also runs on Windows
against an extracted .dmg.
"""
from __future__ import annotations

import os
import plistlib
import struct
import sys

LC_VERSION_MIN_MACOSX = 0x24
LC_BUILD_VERSION = 0x32
PLATFORM_MACOS = 1

_CPU = {0x0100000C: "arm64", 0x01000007: "x86_64"}


def _ver(packed: int) -> tuple[int, int, int]:
    """Decode the xxxx.yy.zz nibble encoding Mach-O uses for versions."""
    return (packed >> 16, (packed >> 8) & 0xFF, packed & 0xFF)


def parse_version(text: str) -> tuple[int, int, int]:
    parts = [int(p) for p in str(text).strip().split(".") if p != ""]
    parts = (parts + [0, 0, 0])[:3]
    return (parts[0], parts[1], parts[2])


def fmt(v: tuple[int, int, int]) -> str:
    return ".".join(str(p) for p in v)


def _parse_slice(data: bytes, off: int) -> dict | None:
    if off + 28 > len(data):
        return None
    magic = struct.unpack_from("<I", data, off)[0]
    if magic == 0xFEEDFACF:
        header = 32
    elif magic == 0xFEEDFACE:
        header = 28
    else:
        return None
    cputype, _sub, _ftype, ncmds, _size, _flags = struct.unpack_from("<iiIIII", data, off + 4)
    p, minos = off + header, None
    for _ in range(ncmds):
        if p + 8 > len(data):
            break
        cmd, cmdsize = struct.unpack_from("<II", data, p)
        if cmd == LC_BUILD_VERSION and p + 16 <= len(data):
            platform, packed = struct.unpack_from("<II", data, p + 8)
            if platform == PLATFORM_MACOS:
                minos = _ver(packed)
        elif cmd == LC_VERSION_MIN_MACOSX and minos is None and p + 12 <= len(data):
            minos = _ver(struct.unpack_from("<I", data, p + 8)[0])
        if cmdsize == 0:
            break
        p += cmdsize
    return {"arch": _CPU.get(cputype & 0xFFFFFFFF, hex(cputype & 0xFFFFFFFF)), "minos": minos}


def macho_slices(data: bytes) -> list[dict]:
    """Return one {"arch", "minos"} per Mach-O slice in data (empty if not Mach-O)."""
    if len(data) < 8:
        return []
    fat_magic = struct.unpack_from(">I", data, 0)[0]
    if fat_magic in (0xCAFEBABE, 0xCAFEBABF):
        count = struct.unpack_from(">I", data, 4)[0]
        # Java class files share 0xCAFEBABE; a real fat header has a handful of arches.
        if count == 0 or count > 20:
            return []
        entry = 20 if fat_magic == 0xCAFEBABE else 32
        out = []
        for i in range(count):
            base = 8 + i * entry
            if fat_magic == 0xCAFEBABE:
                offset = struct.unpack_from(">I", data, base + 8)[0]
            else:
                offset = struct.unpack_from(">Q", data, base + 8)[0]
            s = _parse_slice(data, offset)
            if s:
                out.append(s)
        return out
    s = _parse_slice(data, 0)
    return [s] if s else []


def scan_app(app: str) -> list[dict]:
    """Every Mach-O slice in the bundle, with its path relative to the bundle."""
    rows = []
    for root, _dirs, files in os.walk(app):
        for name in files:
            path = os.path.join(root, name)
            if os.path.islink(path):
                continue  # PyInstaller aliases point at a real file we visit anyway
            try:
                with open(path, "rb") as fh:
                    head = fh.read(8)
                    if len(head) < 8:
                        continue
                    magic_le = struct.unpack_from("<I", head, 0)[0]
                    magic_be = struct.unpack_from(">I", head, 0)[0]
                    if magic_le not in (0xFEEDFACF, 0xFEEDFACE) and magic_be not in (0xCAFEBABE, 0xCAFEBABF):
                        continue
                    data = head + fh.read()
            except OSError:
                continue
            rel = os.path.relpath(path, app).replace(os.sep, "/")
            for s in macho_slices(data):
                rows.append({"path": rel, **s})
    return rows


def declared_minimum(app: str) -> tuple[int, int, int] | None:
    plist = os.path.join(app, "Contents", "Info.plist")
    if not os.path.isfile(plist):
        return None
    with open(plist, "rb") as fh:
        value = plistlib.load(fh).get("LSMinimumSystemVersion")
    return parse_version(value) if value else None


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not os.path.isdir(argv[1]):
        print("usage: check_macos_minos.py path/to/Waffler.app", file=sys.stderr)
        return 2
    app = argv[1]
    rows = scan_app(app)
    declared = declared_minimum(app)
    versioned = [r for r in rows if r["minos"]]
    if not versioned:
        print(f"check_macos_minos: no Mach-O binaries with a version record found in {app}", file=sys.stderr)
        return 2
    highest = max(r["minos"] for r in versioned)
    needing_highest = sorted({r["path"] for r in versioned if r["minos"] == highest})

    print(f"check_macos_minos: {len(rows)} Mach-O slices in {len({r['path'] for r in rows})} files")
    print(f"  highest minimum any binary needs: macOS {fmt(highest)} ({len(needing_highest)} binaries)")
    for path in needing_highest[:8]:
        print(f"    {path}")
    if len(needing_highest) > 8:
        print(f"    ... and {len(needing_highest) - 8} more")
    print(f"  LSMinimumSystemVersion declared:  {fmt(declared) if declared else 'MISSING'}")

    if declared is None:
        print("::error::Info.plist has no LSMinimumSystemVersion; set it in Waffler_mac.spec.")
        return 1
    if highest > declared:
        print(
            f"::error::Waffler.app declares macOS {fmt(declared)} but bundles binaries built for "
            f"macOS {fmt(highest)}. On anything older it fails to open instead of macOS refusing it "
            f"cleanly. Raise LSMinimumSystemVersion in Waffler_mac.spec to {fmt(highest)}, or install "
            f"builds of those packages that target an older macOS."
        )
        return 1
    if declared > highest:
        print(f"  note: every binary would run on macOS {fmt(highest)}; the declared minimum could be lowered.")
    print("check_macos_minos: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
