"""Bounded no-follow input snapshots and immutable fsynced spatial job evidence."""

from __future__ import annotations

import json
import os
import stat
import struct
from pathlib import Path
from typing import Any

from .contracts import Job, canonical, digest


def sync_directory(path: Path) -> None:
    """Persist directory entries before exposing a durable file result."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def immutable(path: Path, content: bytes) -> None:
    """Create one sealed immutable object, refusing any prior different bytes or symlink."""
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != content:
            raise ValueError("immutable spatial object identity conflicts") from None
        return
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o400)
        sync_directory(path.parent)
    except BaseException:
        # A partial exclusive name is evidence of interruption; it is never overwritten on retry.
        raise


def read_source(root: Path, relative: str, maximum: int) -> bytes:
    """Read a bounded regular file through no-follow source-root traversal."""
    parts = relative.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts) or relative.startswith("/"):
        raise ValueError("source path leaves its configured root")
    current = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = child
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
                raise ValueError("spatial source is not a bounded regular file")
            content = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
            if (
                len(content) > maximum
                or len(content) != before.st_size
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            ):
                raise ValueError("spatial source changed or exceeded its immutable bound")
            return content
    finally:
        os.close(current)


def point_count(content: bytes, format_name: str, maximum: int) -> int:
    """Bound native PLY/PCD allocation, including compressed PCD declared decoded bytes."""
    if format_name == "ply":
        end = content.find(b"end_header\n", 0, 65536)
        delimiter = len(b"end_header\n")
        if end < 0:
            end = content.find(b"end_header\r\n", 0, 65536)
            delimiter = len(b"end_header\r\n")
        if end < 0 or not content.startswith((b"ply\n", b"ply\r\n")):
            raise ValueError("PLY header is missing or too large")
        lines = content[: end + delimiter].decode("ascii").splitlines()
        vertices = []
        properties = 0
        for line in lines:
            words = line.split()
            if words[:2] == ["element", "vertex"] and len(words) == 3:
                vertices.append(int(words[2]))
            elif words[:1] == ["element"] and (len(words) != 3 or int(words[2]) != 0):
                raise ValueError("point-cloud PLY cannot contain unbounded extra elements")
            elif words[:1] == ["property"]:
                properties += 1
                if len(words) != 3 or words[1] == "list":
                    raise ValueError("point-cloud PLY properties must be bounded scalar fields")
        if len(vertices) != 1 or not 1 <= vertices[0] <= maximum or not 3 <= properties <= 32:
            raise ValueError("PLY point/property declaration exceeds its bound")
        return vertices[0]
    if format_name != "pcd":
        raise ValueError("native point-cloud format is not admitted")
    header: dict[str, list[str]] = {}
    position = 0
    while position < min(len(content), 65536):
        end = content.find(b"\n", position, 65536)
        if end < 0:
            break
        words = content[position:end].decode("ascii").split()
        position = end + 1
        if not words or words[0].startswith("#"):
            continue
        key = words[0].upper()
        if key in header:
            raise ValueError("duplicate PCD header field")
        header[key] = words[1:]
        if key == "DATA":
            break
    try:
        count = int(header["POINTS"][0])
        width = int(header["WIDTH"][0])
        height = int(header["HEIGHT"][0])
        if width <= 0 or height <= 0 or width * height != count:
            raise ValueError("PCD width/height differ from its bounded point count")
        sizes = [int(value) for value in header["SIZE"]]
        counts = [int(value) for value in header.get("COUNT", ["1"] * len(sizes))]
        names = header["FIELDS"]
        types = header["TYPE"]
        encoding = header["DATA"][0]
        if not 3 <= count <= maximum or not 3 <= len(names) <= 32 or len(set(names)) != len(names):
            raise ValueError("PCD point/field count is invalid")
        if len(sizes) != len(counts) or len(sizes) != len(names) or len(types) != len(names):
            raise ValueError("PCD fields have inconsistent shape")
        if not {"x", "y", "z"} <= set(names) or any(v not in {1, 2, 4, 8} for v in sizes):
            raise ValueError("PCD coordinates or scalar byte widths are invalid")
        if any(not 1 <= v <= 4 for v in counts) or any(v not in {"F", "I", "U"} for v in types):
            raise ValueError("PCD field type/count exceeds its bound")
        decoded = count * sum(size * width for size, width in zip(sizes, counts, strict=True))
        if decoded > maximum * 256:
            raise ValueError("PCD decoded allocation exceeds its bound")
        if encoding == "binary" and len(content) - position != decoded:
            raise ValueError("PCD binary payload size differs from header")
        if encoding == "binary_compressed":
            if len(content) - position < 8:
                raise ValueError("compressed PCD has no exact byte-size envelope")
            compressed, expanded = struct.unpack_from("<II", content, position)
            if expanded != decoded or compressed != len(content) - position - 8:
                raise ValueError("compressed PCD byte-size envelope differs")
        elif encoding not in {"ascii", "binary"}:
            raise ValueError("PCD data encoding is unsupported")
        return count
    except (KeyError, IndexError) as error:
        raise ValueError("PCD header is incomplete") from error


def snapshots(job: Job, source_root: Path, directory: Path) -> list[dict[str, Any]]:
    """Freeze actual source bytes under the aggregate point budget."""
    result = []
    total = 0
    for index, source in enumerate(job.inputs):
        content = read_source(source_root, source.path, job.policy.max_input_bytes)
        if digest(content) != source.content_digest:
            raise ValueError("input content digest differs from the admitted source")
        count = point_count(content, source.format, job.policy.max_points)
        if count < 3:
            raise ValueError("input geometry has insufficient original points")
        total += count
        if total > job.policy.max_total_points:
            raise ValueError("aggregate input points exceed the declared computation budget")
        name = f"input-{index}.{source.format}"
        immutable(directory / name, content)
        result.append(
            {
                "source_id": source.source_id,
                "frame_id": source.frame_id,
                "content_digest": source.content_digest,
                "units": source.units,
                "format": source.format,
                "point_count": count,
                "byte_size": len(content),
                "snapshot": name,
            }
        )
    return result


def read_json(path: Path, maximum: int = 1024 * 1024) -> dict[str, Any]:
    """Read one bounded local JSON object without following a final symlink."""
    value = json.loads(read_source(path.parent, path.name, maximum))
    if not isinstance(value, dict):
        raise ValueError("spatial metadata must be a JSON object")
    return value


def save_json(path: Path, value: object) -> None:
    """Persist a complete immutable metadata object before returning its identity."""
    immutable(path, canonical(value))
