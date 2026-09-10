#!/usr/bin/env python3
"""Verify prebuilt ARM64 libraries and the native contents of a built NPU APK."""

import argparse
import hashlib
from pathlib import Path
import struct
import zipfile


APP_LIBRARIES = (
    "libelabrador_native.so",
    "libvins_feature_tracker.so",
    "libvins_estimator.so",
)
APK_LIBRARIES = APP_LIBRARIES + (
    "librealsense2.so",
    "libonnxruntime.so",
    "libonnxruntime4j_jni.so",
    "libtensorflowlite_gpu_jni.so",
    "libtensorflowlite_jni.so",
)


def check_arm64(header: bytes, name: str) -> None:
    """Reject missing ELF headers, LFS pointers, and libraries for another ABI."""
    if len(header) < 20 or header[:6] != b"\x7fELF\x02\x01":
        raise ValueError(f"{name}: not a 64-bit little-endian ELF; run git lfs pull")
    if struct.unpack_from("<H", header, 18)[0] != 183:
        raise ValueError(f"{name}: not an AArch64 (arm64-v8a) library")


def main() -> None:
    """Check source prebuilts and optionally an APK, exiting nonzero on any failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", nargs="?", type=Path, help="built app-npu-debug.apk")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    prebuilts = [root / "app/src/main/jniLibs/arm64-v8a" / lib for lib in APP_LIBRARIES]
    prebuilts.append(root / "librealsense/src/main/jniLibs/arm64-v8a/librealsense2.so")
    for path in prebuilts:
        with path.open("rb") as stream:
            check_arm64(stream.read(20), str(path.relative_to(root)))
        print(f"OK prebuilt {path.name}: {path.stat().st_size} bytes")
    if args.apk is not None:
        with zipfile.ZipFile(args.apk) as archive:
            names = archive.namelist()
            for lib in APK_LIBRARIES:
                name = f"lib/arm64-v8a/{lib}"
                if names.count(name) != 1:
                    raise ValueError(f"APK must contain exactly one {name}")
                with archive.open(name) as stream:
                    check_arm64(stream.read(20), name)
                print(f"OK APK {name}")
        with args.apk.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        print(f"APK SHA256 {digest}")
    print("Native library verification passed.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, zipfile.BadZipFile, KeyError) as error:
        raise SystemExit(f"Native library verification FAILED: {error}") from error
