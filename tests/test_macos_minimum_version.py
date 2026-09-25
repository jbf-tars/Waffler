"""The Mac app must declare a minimum macOS it can actually run on.

v3.14.99 declared LSMinimumSystemVersion 10.13.0, but a scan of the shipped
bundle found 13 NumPy extension modules built for macOS 14.0 (NumPy's
macosx_14_0_arm64 wheel, which calls Accelerate's $NEWLAPACK$ILP64 functions).
NumPy loads at start-up, so older Macs got an app that failed to open.
scripts/check_macos_minos.py now runs on every release build; these tests
cover its Mach-O parsing with synthetic binaries, so they run on any OS.
"""
import os
import plistlib
import re
import struct
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import check_macos_minos as cm  # noqa: E402

ARM64, X86_64 = 0x0100000C, 0x01000007


def _packed(major, minor=0, patch=0):
    return (major << 16) | (minor << 8) | patch


def _thin(minos=None, cpu=ARM64, legacy=False, extra_cmds=0):
    """A minimal 64-bit Mach-O: header plus optional padding commands and one version command."""
    cmds = b""
    for _ in range(extra_cmds):  # unrelated load commands the parser must skip
        cmds += struct.pack("<II", 0x19, 16) + b"\0" * 8
    if minos is not None:
        if legacy:
            cmds += struct.pack("<IIII", cm.LC_VERSION_MIN_MACOSX, 16, _packed(*minos), 0)
        else:
            cmds += struct.pack("<IIIIII", cm.LC_BUILD_VERSION, 24, cm.PLATFORM_MACOS, _packed(*minos), 0, 0)
    ncmds = extra_cmds + (1 if minos is not None else 0)
    header = struct.pack("<IiiIIIII", 0xFEEDFACF, cpu, 0, 6, ncmds, len(cmds), 0, 0)
    return header + cmds


def _fat(slices):
    """A universal binary: fat header, arch table, then each slice at an aligned offset."""
    out = struct.pack(">II", 0xCAFEBABE, len(slices))
    offset = 4096
    table, body = b"", b""
    for cpu, data in slices:
        table += struct.pack(">iiIII", cpu, 0, offset + len(body), len(data), 12)
        body += data + b"\0" * (4096 - len(data) % 4096)
    return (out + table).ljust(offset, b"\0") + body


# ── parsing ──────────────────────────────────────────────────────────────────

def test_reads_build_version_minos():
    assert cm.macho_slices(_thin((14, 0))) == [{"arch": "arm64", "minos": (14, 0, 0)}]


def test_reads_legacy_version_min_macosx():
    assert cm.macho_slices(_thin((10, 13), legacy=True))[0]["minos"] == (10, 13, 0)


def test_skips_unrelated_load_commands():
    assert cm.macho_slices(_thin((11, 0), extra_cmds=3))[0]["minos"] == (11, 0, 0)


def test_reads_every_slice_of_a_universal_binary():
    data = _fat([(ARM64, _thin((11, 0), cpu=ARM64)), (X86_64, _thin((10, 15), cpu=X86_64))])
    got = {s["arch"]: s["minos"] for s in cm.macho_slices(data)}
    assert got == {"arm64": (11, 0, 0), "x86_64": (10, 15, 0)}


def test_java_class_files_are_not_mistaken_for_universal_binaries():
    java = struct.pack(">II", 0xCAFEBABE, 0x00000034) + b"\0" * 64  # major version 52
    assert cm.macho_slices(java) == []


def test_non_macho_files_are_ignored():
    assert cm.macho_slices(b"#!/bin/sh\necho hi\n") == []
    assert cm.macho_slices(b"") == []


def test_version_parsing():
    assert cm.parse_version("14.0.0") == (14, 0, 0)
    assert cm.parse_version("14") == (14, 0, 0)
    assert cm.parse_version("10.13") == (10, 13, 0)


# ── the release check, end to end on a fake bundle ───────────────────────────

def _bundle(tmp_path, declared, binaries):
    app = tmp_path / "Waffler.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Frameworks" / "numpy").mkdir(parents=True)
    if declared is not None:
        with open(app / "Contents" / "Info.plist", "wb") as fh:
            plistlib.dump({"LSMinimumSystemVersion": declared}, fh)
    for rel, data in binaries.items():
        (app / rel).write_bytes(data)
    return str(app)


def test_release_check_fails_when_a_binary_needs_a_newer_macos(tmp_path, capsys):
    """The exact shape of the v3.14.99 bug: declares 10.13, NumPy needs 14.0."""
    app = _bundle(tmp_path, "10.13.0", {
        "Contents/MacOS/Waffler": _thin((11, 0)),
        "Contents/Frameworks/numpy/_multiarray_umath.so": _thin((14, 0)),
    })
    assert cm.main(["x", app]) == 1
    out = capsys.readouterr().out
    assert "14.0.0" in out and "numpy/_multiarray_umath.so" in out


def test_release_check_passes_when_the_declared_minimum_is_true(tmp_path):
    app = _bundle(tmp_path, "14.0.0", {
        "Contents/MacOS/Waffler": _thin((11, 0)),
        "Contents/Frameworks/numpy/_multiarray_umath.so": _thin((14, 0)),
    })
    assert cm.main(["x", app]) == 0


def test_release_check_fails_without_a_declared_minimum(tmp_path):
    app = _bundle(tmp_path, None, {"Contents/MacOS/Waffler": _thin((11, 0))})
    assert cm.main(["x", app]) == 1


# ── wiring ───────────────────────────────────────────────────────────────────

def _spec_minimum():
    with open(os.path.join(ROOT, "Waffler_mac.spec"), "r", encoding="utf-8") as fh:
        m = re.search(r"'LSMinimumSystemVersion':\s*'([0-9.]+)'", fh.read())
    assert m, "Waffler_mac.spec must set LSMinimumSystemVersion"
    return cm.parse_version(m.group(1))


def test_spec_declares_at_least_the_first_apple_silicon_macos():
    """The build is Apple Silicon only, and macOS 11 was the first to run on it."""
    assert _spec_minimum() >= (11, 0, 0)


def test_release_workflow_runs_the_check_on_the_built_app():
    with open(os.path.join(ROOT, ".github", "workflows", "macos-release.yml"), "r", encoding="utf-8") as fh:
        wf = fh.read()
    build = wf.index("python -m PyInstaller")
    check = wf.find("scripts/check_macos_minos.py dist/Waffler.app")
    assert check > build, "the minimum-version check must run on the built app, after PyInstaller"
    # The first "codesign" in the file is the keychain setup (-T /usr/bin/codesign);
    # what matters is the first actual signing command after the build.
    first_sign = wf.index("codesign --sign", build)
    assert check < first_sign, "run the check before signing, so a bad build fails fast"
