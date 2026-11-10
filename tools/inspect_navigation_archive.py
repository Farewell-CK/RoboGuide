#!/usr/bin/env python3
"""Verify and inspect a navigation archive directory or exported ZIP using only Python stdlib.

Examples:
  python3 inspect_navigation_archive.py /path/to/nav-session
  python3 inspect_navigation_archive.py exported.zip --json
  python3 inspect_navigation_archive.py exported.zip --reconstruct-maps ./restored-maps
  python3 inspect_navigation_archive.py exported.zip --depth-info artifacts/inference_raw_depth/00000001.gz --export-depth-pgm depth.pgm

Map replay restores serialized .navmap bundles (or legacy .ot bytes), not a new geometric map. It does not invent
missing observations or merge VINS generations. Depth PGM stores original uint16
values; use the reported depth_unit_m to convert to meters.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
import gzip
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import sys
import zipfile
import zlib

MAX_MANIFEST = 16 * 1024 * 1024
MAX_FILE = 32 * 1024**3
MAX_DEPTH = 256 * 1024 * 1024
MAX_EVENT_LINE = 16 * 1024 * 1024
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
MAP_TYPES = {"octomap_full_checkpoint", "octomap_full_delta", "octomap_full_final_snapshot"}
INFERENCE_TYPES = {"inference_rgb", "inference_raw_depth", "inference_aligned_depth", "semantic_labels", "semantic_confidence"}


class ArchiveError(Exception):
    pass


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or any(ord(c) < 32 for c in value):
        raise ArchiveError("Invalid archive path")
    parts = value.split("/")
    if any(p in {"", ".", ".."} or ":" in p for p in parts) or PurePosixPath(value).is_absolute():
        raise ArchiveError(f"Unsafe archive path: {value!r}")
    return value


def strict_json(data: bytes):
    def reject_constant(value):
        raise ValueError(f"Non-finite JSON number: {value}")
    try:
        return json.loads(data, parse_constant=reject_constant)
    except (ValueError, UnicodeError) as error:
        raise ArchiveError(f"Invalid JSON: {error}") from error


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


class Archive:
    """Never extracts ZIP members; all access is through validated manifest paths."""
    def __init__(self, path: str | Path):
        self.path = Path(path).absolute()
        self.zip = None
        self.members = {}
        if self.path.is_dir():
            if self.path.is_symlink():
                raise ArchiveError("Archive root must not be a symbolic link")
            self.root = self.path.resolve()
        else:
            self.root = None
            try:
                self.zip = zipfile.ZipFile(self.path)
                for info in self.zip.infolist():
                    name = info.filename[:-1] if info.is_dir() else info.filename
                    safe_relative(name)
                    if stat.S_ISLNK((info.external_attr >> 16) & 0xffff):
                        raise ArchiveError(f"ZIP symbolic link is not allowed: {name}")
                    if name in self.members:
                        raise ArchiveError(f"Duplicate ZIP member: {name}")
                    self.members[name] = info
            except (OSError, zipfile.BadZipFile, ArchiveError) as error:
                if self.zip:
                    self.zip.close()
                raise ArchiveError(f"Cannot open archive: {error}") from error
        try:
            with self.open("manifest.json") as stream:
                encoded = stream.read(MAX_MANIFEST + 1)
            if len(encoded) > MAX_MANIFEST:
                raise ArchiveError("Manifest exceeds 16 MiB")
            self.manifest = strict_json(encoded)
            self._validate_manifest()
        except Exception:
            self.close()
            raise

    def _validate_manifest(self):
        m = self.manifest
        if not isinstance(m, dict) or m.get("schema_version") != 1:
            raise ArchiveError("Unsupported manifest schema")
        if not isinstance(m.get("session_id"), str) or not SESSION.fullmatch(m["session_id"]):
            raise ArchiveError("Invalid manifest session_id")
        entries = m.get("files")
        if not isinstance(entries, list) or len(entries) > 20000:
            raise ArchiveError("Invalid files manifest")
        self.files = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ArchiveError("Invalid file descriptor")
            path = safe_relative(entry.get("path"))
            if path in self.files or path == "manifest.json":
                raise ArchiveError(f"Duplicate or reserved file descriptor: {path}")
            if type(entry.get("size")) is not int or not 0 <= entry["size"] <= MAX_FILE:
                raise ArchiveError(f"Invalid file size: {path}")
            if not isinstance(entry.get("sha256"), str) or not SHA256.fullmatch(entry["sha256"]):
                raise ArchiveError(f"Invalid SHA-256: {path}")
            if not isinstance(entry.get("type", "unknown"), str):
                raise ArchiveError(f"Invalid artifact type: {path}")
            metadata = entry.get("metadata", {})
            if not isinstance(metadata, dict):
                raise ArchiveError(f"Invalid metadata: {path}")
            if metadata.get("session_id", m["session_id"]) != m["session_id"]:
                raise ArchiveError(f"Artifact crosses session boundary: {path}")
            self.files[path] = entry
        for path in self.files:
            parts = path.split("/")
            if any("/".join(parts[:i]) in self.files for i in range(1, len(parts))):
                raise ArchiveError(f"File/directory path conflict: {path}")

    def _local(self, name):
        candidate = self.root.joinpath(*safe_relative(name).split("/"))
        for part in [candidate, *candidate.parents]:
            if part == self.root:
                break
            if part.is_symlink():
                raise ArchiveError(f"Symbolic link is not allowed: {name}")
        if not candidate.resolve().is_relative_to(self.root):
            raise ArchiveError(f"Path leaves archive root: {name}")
        return candidate

    @contextmanager
    def open(self, name):
        safe_relative(name)
        try:
            if self.zip:
                info = self.members.get(name)
                if info is None or info.is_dir():
                    raise ArchiveError(f"Missing archive file: {name}")
                with self.zip.open(info) as stream:
                    yield stream
            else:
                with self._local(name).open("rb") as stream:
                    yield stream
        except (OSError, zipfile.BadZipFile, RuntimeError) as error:
            raise ArchiveError(f"Cannot read {name}: {error}") from error

    def verify(self) -> dict:
        total = 0
        for name, entry in self.files.items():
            if self.zip and self.members.get(name) and self.members[name].file_size != entry["size"]:
                raise ArchiveError(f"Size mismatch: {name}")
            digest = hashlib.sha256()
            actual = 0
            with self.open(name) as stream:
                while True:
                    block = stream.read(min(65536, entry["size"] - actual + 1))
                    if not block:
                        break
                    actual += len(block)
                    if actual > entry["size"]:
                        raise ArchiveError(f"Size mismatch: {name}")
                    digest.update(block)
            if actual != entry["size"]:
                raise ArchiveError(f"Size mismatch: {name}")
            if digest.hexdigest() != entry["sha256"]:
                raise ArchiveError(f"SHA-256 mismatch: {name}")
            total += actual
        return {"verified_files": len(self.files), "verified_bytes": total}

    def events(self):
        for name in sorted(path for path in self.files if path.endswith(".jsonl")):
            with self.open(name) as stream:
                line_number = 0
                while True:
                    line = stream.readline(MAX_EVENT_LINE + 1)
                    if not line:
                        break
                    line_number += 1
                    if len(line) > MAX_EVENT_LINE:
                        raise ArchiveError(f"Event line too large: {name}:{line_number}")
                    if not line.endswith(b"\n"):
                        raise ArchiveError(f"Unsealed event tail: {name}:{line_number}")
                    value = strict_json(line)
                    if not isinstance(value, dict):
                        raise ArchiveError(f"Invalid event: {name}:{line_number}")
                    if not isinstance(value.get("type", "unknown"), str) or value.get("data") is not None and not isinstance(value["data"], dict):
                        raise ArchiveError(f"Invalid event fields: {name}:{line_number}")
                    if value.get("session_id", self.manifest["session_id"]) != self.manifest["session_id"]:
                        raise ArchiveError(f"Event crosses session boundary: {name}:{line_number}")
                    yield value

    def summary(self) -> dict:
        verification = self.verify()
        kinds = Counter(entry.get("type", "unknown") for entry in self.files.values())
        event_types, terminal, interruptions = Counter(), [], []
        failed_ids, pending_ids = set(), set()
        for event in self.events():
            kind = event.get("type", "unknown")
            event_types[kind] += 1
            data = event.get("data") or {}
            if not isinstance(data, dict):
                continue
            if kind == "processing_closed_before_result":
                processing = data.get("processing") or {}
                if not isinstance(processing, dict) or not isinstance(processing.get("id"), str):
                    raise ArchiveError("Invalid processing terminal identity")
                pending_ids.add(processing.get("id"))
                terminal.append({"type": kind, "processing_id": processing.get("id"),
                                 "processing_type": processing.get("type"), "status": data.get("status"),
                                 "elapsed_time_ns": event.get("elapsed_time_ns")})
            elif kind in {"inference_result_use", "inference_failed"} or kind.startswith("cloud.") and kind.endswith((".result", ".failure", ".cancelled")):
                identifier = data.get("inference_id") or data.get("request_id")
                if identifier is not None and not isinstance(identifier, str):
                    raise ArchiveError("Invalid inference/request identity")
                terminal.append({"type": kind, "processing_id": identifier,
                                 "status": data.get("status", data.get("reason")),
                                 "elapsed_time_ns": event.get("elapsed_time_ns")})
                if kind == "inference_failed":
                    failed_ids.add(identifier)
            if any(word in kind for word in ("dropped", "_gap", "_failed", "_error", "interrupted", "capture_reject")):
                interruptions.append({"type": kind, "elapsed_time_ns": event.get("elapsed_time_ns"),
                                      "inference_id": data.get("inference_id"), "reason": data.get("reason")})
        inferences = defaultdict(set)
        for entry in self.files.values():
            kind = entry.get("type")
            identifier = entry.get("metadata", {}).get("inference_id")
            if identifier is not None and not isinstance(identifier, str):
                raise ArchiveError("Invalid artifact inference_id")
            if identifier and kind in INFERENCE_TYPES:
                inferences[identifier].add(kind)
        missing = [{"inference_id": identifier, "missing": sorted(INFERENCE_TYPES - found),
                    "status": "pending_at_session_end" if identifier in pending_ids else "inference_failed" if identifier in failed_ids else "unexplained"}
                   for identifier, found in sorted(inferences.items()) if INFERENCE_TYPES - found]
        started, ended = self.manifest.get("started_at_ms"), self.manifest.get("ended_at_ms")
        duration = (ended - started) / 1000 if isinstance(started, (float, int)) and isinstance(ended, (float, int)) else None
        return {"session_id": self.manifest["session_id"], "state": self.manifest.get("state"),
                "capture_complete": self.manifest.get("capture_complete"), "duration_seconds": duration,
                **verification, "file_types": dict(sorted(kinds.items())), "event_types": dict(sorted(event_types.items())),
                "dropped_records": self.manifest.get("dropped_records", 0), "write_errors": self.manifest.get("write_errors", 0),
                "missing_inference_artifacts": missing, "processing_terminal_events": terminal,
                "interruptions": interruptions}

    def resolve_entry(self, selected):
        entry = self.files.get(selected)
        if entry:
            return entry
        candidates = [entry for name, entry in self.files.items() if PurePosixPath(name).name == selected]
        if len(candidates) != 1:
            raise ArchiveError("Select an exact manifest path (or a unique basename)")
        return candidates[0]

    def close(self):
        if self.zip:
            self.zip.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def read_exact(stream, count):
    data = stream.read(count)
    if len(data) != count:
        raise ArchiveError("Truncated map patch")
    return data


def apply_patch(base: Path, stream, destination: Path):
    """Apply the exact gzip/DataOutput NDT1 format emitted by SnapshotPatch.java."""
    with gzip.GzipFile(fileobj=stream, mode="rb") as patch:
        if read_exact(patch, 4) != b"NDT1":
            raise ArchiveError("Invalid map patch magic")
        length, = struct.unpack(">q", read_exact(patch, 8))
        if not 0 <= length <= MAX_FILE:
            raise ArchiveError("Invalid map patch target length")
        with base.open("rb") as before, destination.open("xb") as after:
            shutil.copyfileobj(before, after, length=65536)
        try:
            with destination.open("r+b") as output:
                output.truncate(length)
                previous_end = 0
                while True:
                    offset, = struct.unpack(">q", read_exact(patch, 8))
                    if offset == -1:
                        break
                    count, = struct.unpack(">i", read_exact(patch, 4))
                    if offset < previous_end or count <= 0 or count > 65536 or offset + count > length:
                        raise ArchiveError("Invalid, overlapping, or out-of-range map patch span")
                    output.seek(offset)
                    output.write(read_exact(patch, count))
                    previous_end = offset + count
                if patch.read(1):
                    raise ArchiveError("Trailing data after map patch terminator")
        except Exception:
            destination.unlink(missing_ok=True)
            raise



def inspect_map_bundle(source) -> dict:
    """Validate NSEM0001 authoritative semantic state without extracting nested ZIP files.

    Header words and records are little endian. Occupancy is log-odds float32 bits;
    confidence is a float32 native score and may exceed 1 after ground/fusion updates.
    Paths append each child index as three low bits: child_path=(parent_path<<3)|index.
    """
    try:
        with zipfile.ZipFile(source) as bundle:
            members = {}
            for entry in bundle.infolist():
                safe_relative(entry.filename)
                if entry.filename in members or entry.is_dir() or stat.S_ISLNK((entry.external_attr >> 16) & 0xffff):
                    raise ArchiveError("Duplicate, directory, or symbolic-link map bundle member")
                if entry.file_size > MAX_FILE:
                    raise ArchiveError("Map bundle member exceeds format limit")
                members[entry.filename] = entry
            if set(members) != {"occupancy.ot", "semantics.bin"}:
                raise ArchiveError("Map bundle must contain exactly occupancy.ot and semantics.bin")
            with bundle.open(members["semantics.bin"]) as stream:
                header = read_exact(stream, 96)
                magic, version, tree_depth, resolution, nodes, leaves, record_size, native_sha, native_build_id = struct.unpack("<8sIIdQQI32s20s", header)
                if magic != b"NSEM0001" or version != 1 or record_size != 25:
                    raise ArchiveError("Unsupported semantic sidecar header/version/record size")
                if not 0 <= tree_depth <= 16 or not math.isfinite(resolution) or resolution <= 0:
                    raise ArchiveError("Invalid semantic tree depth/resolution")
                if leaves > nodes or members["semantics.bin"].file_size != 96 + nodes * record_size:
                    raise ArchiveError("Semantic sidecar node count/length mismatch")
                expected = [(0, 0)] if nodes else []
                actual_leaves = 0
                confidence_min = confidence_max = occupancy_min = occupancy_max = None
                depth_counts, flag_counts = Counter(), Counter()
                for _ in range(nodes):
                    depth, path, child_mask, occupancy_bits, raw_rgb, semantic_rgb, confidence_bits, display_flag = struct.unpack("<BQBI3s3sIB", read_exact(stream, record_size))
                    if depth > tree_depth or path >> (3 * depth):
                        raise ArchiveError("Invalid semantic node depth/path levels")
                    if not expected or expected.pop() != (depth, path):
                        raise ArchiveError("Semantic nodes do not follow the declared preorder topology")
                    if depth == tree_depth and child_mask:
                        raise ArchiveError("Semantic node has children below maximum tree depth")
                    occupancy = struct.unpack("<f", struct.pack("<I", occupancy_bits))[0]
                    confidence = struct.unpack("<f", struct.pack("<I", confidence_bits))[0]
                    if not math.isfinite(occupancy) or not math.isfinite(confidence):
                        raise ArchiveError("Semantic node contains non-finite occupancy/confidence")
                    occupancy_min = occupancy if occupancy_min is None else min(occupancy_min, occupancy)
                    occupancy_max = occupancy if occupancy_max is None else max(occupancy_max, occupancy)
                    confidence_min = confidence if confidence_min is None else min(confidence_min, confidence)
                    confidence_max = confidence if confidence_max is None else max(confidence_max, confidence)
                    depth_counts[depth] += 1
                    flag_counts[display_flag] += 1
                    if child_mask == 0:
                        actual_leaves += 1
                    for child in range(7, -1, -1):
                        if child_mask & (1 << child):
                            expected.append((depth + 1, (path << 3) | child))
                if expected or actual_leaves != leaves or stream.read(1):
                    raise ArchiveError("Semantic sidecar child topology/leaf count mismatch")
            # Read occupancy.ot too, so its ZIP CRC is checked. Sidecar is authoritative for confidence.
            digest = hashlib.sha256()
            with bundle.open(members["occupancy.ot"]) as stream:
                for block in iter(lambda: stream.read(65536), b""):
                    digest.update(block)
            return {"format": "navmap:NSEM0001", "semantic_state_authoritative": True,
                    "version": version, "tree_depth": tree_depth, "resolution_m": resolution,
                    "nodes": nodes, "leaves": leaves, "record_size": record_size,
                    "native_library_sha256": native_sha.hex(), "native_library_build_id": native_build_id.hex(),
                    "occupancy_log_odds_range": [occupancy_min, occupancy_max] if nodes else None,
                    "semantic_confidence_range": [confidence_min, confidence_max] if nodes else None,
                    "depth_counts": dict(sorted(depth_counts.items())), "display_flag_counts": dict(sorted(flag_counts.items())),
                    "occupancy_ot_bytes": members["occupancy.ot"].file_size, "occupancy_ot_sha256": digest.hexdigest()}
    except (OSError, zipfile.BadZipFile, EOFError, struct.error, zlib.error) as error:
        raise ArchiveError(f"Invalid map bundle: {error}") from error


def map_info(archive: Archive, selected: str) -> dict:
    archive.verify()
    entry = archive.resolve_entry(selected)
    if entry.get("type") not in {"octomap_full_checkpoint", "octomap_full_final_snapshot"} or not str(entry.get("metadata", {}).get("format", "")).startswith("zip:"):
        raise ArchiveError("Select a full .navmap checkpoint/final snapshot; reconstruct byte deltas first")
    with archive.open(entry["path"]) as stream:
        result = inspect_map_bundle(stream)
    return {"path": entry["path"], "generation": entry.get("metadata", {}).get("generation"),
            "sequence": entry.get("metadata", {}).get("sequence"), **result}


def reconstruct_maps(archive: Archive, directory: str | Path) -> list:
    archive.verify()
    output = Path(directory).absolute()
    if output.is_symlink() or output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ArchiveError("Map output must be a new or empty directory; existing files will not be overwritten")
    output.mkdir(parents=True, exist_ok=True)
    entries = []
    for entry in archive.files.values():
        if entry.get("type") not in MAP_TYPES:
            continue
        meta = entry.get("metadata", {})
        if type(meta.get("generation")) is not int or meta["generation"] < 0 or type(meta.get("sequence")) is not int or meta["sequence"] < 1:
            raise ArchiveError(f"Map has no valid generation/sequence: {entry['path']}")
        if meta.get("scope") != "complete_current_native_tree" or not SHA256.fullmatch(str(meta.get("sha256", ""))):
            raise ArchiveError(f"Map lacks complete-tree scope/target checksum: {entry['path']}")
        entries.append(entry)
    entries.sort(key=lambda e: (e["metadata"]["generation"], e["metadata"]["sequence"], e["type"] == "octomap_full_final_snapshot"))
    states, result = {}, []
    for entry in entries:
        meta, kind = entry["metadata"], entry["type"]
        generation, sequence = meta["generation"], meta["sequence"]
        before = states.get(generation)
        if before and sequence <= before["sequence"] and not (kind == "octomap_full_final_snapshot" and sequence == before["sequence"] and meta["sha256"] == before["sha256"]):
            raise ArchiveError(f"Conflicting map sequence within generation {generation}: {sequence}")
        generation_dir = output / archive.manifest["session_id"] / f"generation-{generation}"
        generation_dir.mkdir(parents=True, exist_ok=True)
        suffix = "-final" if kind == "octomap_full_final_snapshot" else ""
        extension = ".navmap" if str(meta.get("format", "")).startswith("zip:") else ".ot"
        target = generation_dir / f"sequence-{sequence:08d}{suffix}{extension}"
        partial = target.with_suffix(extension + ".partial")
        try:
            with archive.open(entry["path"]) as source:
                if kind == "octomap_full_delta":
                    if before is None or sequence != before["sequence"] + 1 or meta.get("base_sha256") != before["sha256"]:
                        raise ArchiveError(f"Missing/mismatched map base in generation {generation}, sequence {sequence}")
                    if digest_file(before["path"]) != meta["base_sha256"]:
                        raise ArchiveError("Reconstructed base changed before applying next patch")
                    apply_patch(before["path"], source, partial)
                else:
                    with partial.open("xb") as destination:
                        shutil.copyfileobj(source, destination, length=65536)
            if digest_file(partial) != meta["sha256"]:
                raise ArchiveError(f"Reconstructed target SHA-256 mismatch: generation {generation}, sequence {sequence}")
            bundle_info = inspect_map_bundle(partial) if extension == ".navmap" else None
            if target.exists():
                raise ArchiveError(f"Refusing to overwrite {target}")
            partial.rename(target)
        except (OSError, EOFError, gzip.BadGzipFile, zlib.error, ArchiveError) as error:
            partial.unlink(missing_ok=True)
            raise ArchiveError(f"Cannot restore {entry['path']}: {error}") from error
        states[generation] = {"sequence": sequence, "sha256": meta["sha256"], "path": target}
        result.append({"generation": generation, "sequence": sequence, "type": kind,
                       "sha256": meta["sha256"], "path": str(target),
                       **({"map_info": bundle_info} if bundle_info is not None else {})})
    return result


def depth_info(archive: Archive, selected: str, export: str | Path | None = None) -> dict:
    archive.verify()
    entry = archive.resolve_entry(selected)
    if entry.get("type") not in {"inference_raw_depth", "inference_aligned_depth"}:
        raise ArchiveError("Selected file is not a recorded raw/aligned inference depth")
    meta = entry.get("metadata", {})
    width, height, stride, unit = (meta.get(k) for k in ("width", "height", "stride_bytes", "depth_unit_m"))
    if any(type(x) is not int or x < 1 for x in (width, height, stride)) or width > 8192 or height > 8192 or stride < width * 2 or stride * height > MAX_DEPTH:
        raise ArchiveError("Invalid depth dimensions/stride")
    if type(unit) not in (int, float) or not math.isfinite(unit) or unit <= 0:
        raise ArchiveError("Invalid depth unit")
    with archive.open(entry["path"]) as compressed, gzip.GzipFile(fileobj=compressed) as stream:
        raw = stream.read(stride * height + 1)
    if len(raw) != stride * height:
        raise ArchiveError("Decoded depth size differs from dimensions/stride")
    valid, minimum, maximum = 0, None, None
    for y in range(height):
        for x in range(width):
            value, = struct.unpack_from("<H", raw, y * stride + x * 2)
            if value:
                valid += 1
                minimum = value if minimum is None else min(minimum, value)
                maximum = value if maximum is None else max(maximum, value)
    if export:
        with Path(export).open("xb") as destination:
            destination.write(f"P5\n{width} {height}\n65535\n".encode("ascii"))
            for y in range(height):
                for x in range(width):
                    value, = struct.unpack_from("<H", raw, y * stride + x * 2)
                    destination.write(struct.pack(">H", value))
    return {"path": entry["path"], "inference_id": meta.get("inference_id"), "generation": meta.get("generation"),
            "width": width, "height": height, "stride_bytes": stride, "depth_unit_m": unit,
            "valid_pixels": valid, "invalid_zero_pixels": width * height - valid,
            "raw_nonzero_min": minimum, "raw_nonzero_max": maximum,
            "nonzero_range_m": [minimum * unit, maximum * unit] if valid else None,
            "exported_pgm": str(export) if export else None}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("archive", nargs="?", type=Path, help="Navigation session directory or exported ZIP")
    parser.add_argument("--json", action="store_true", help="Print structured summary JSON")
    parser.add_argument("--reconstruct-maps", metavar="OUT", type=Path, help="Restore full .navmap/legacy .ot versions into a new/empty directory")
    parser.add_argument("--map-info", metavar="PATH", help="Inspect a manifest .navmap checkpoint, or a standalone reconstructed bundle when archive is omitted")
    parser.add_argument("--depth-info", metavar="PATH", help="Inspect a manifest depth path or unique basename")
    parser.add_argument("--export-depth-pgm", metavar="OUT", type=Path, help="With --depth-info, export exact uint16 values to PGM")
    args = parser.parse_args(argv)
    if args.archive is None and (not args.map_info or args.depth_info or args.reconstruct_maps):
        parser.error("Provide an archive, or use --map-info PATH for a standalone .navmap bundle")
    if args.export_depth_pgm and not args.depth_info:
        parser.error("--export-depth-pgm requires --depth-info")
    try:
        if args.archive is None:
            report = {"map_info": inspect_map_bundle(Path(args.map_info))}
        else:
            with Archive(args.archive) as archive:
                report = archive.summary()
                if args.reconstruct_maps:
                    report["reconstructed_maps"] = reconstruct_maps(archive, args.reconstruct_maps)
                if args.depth_info:
                    report["depth"] = depth_info(archive, args.depth_info, args.export_depth_pgm)
                if args.map_info:
                    report["map_info"] = map_info(archive, args.map_info)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif "session_id" not in report:
            print("map_info: " + json.dumps(report["map_info"], ensure_ascii=False))
        else:
            print(f"Session: {report['session_id']} | state: {report['state']} | capture_complete: {report['capture_complete']}")
            print(f"Verified: {report['verified_files']} files, {report['verified_bytes']} bytes | duration: {report['duration_seconds']} s")
            print("File types: " + json.dumps(report["file_types"], ensure_ascii=False))
            print(f"Missing inference groups: {len(report['missing_inference_artifacts'])}; interruption/error events: {len(report['interruptions'])}")
            print("Processing terminal events: " + json.dumps(report["processing_terminal_events"], ensure_ascii=False))
            for key in ("missing_inference_artifacts", "reconstructed_maps", "depth", "map_info"):
                if report.get(key):
                    print(key + ": " + json.dumps(report[key], ensure_ascii=False))
        return 0
    except (ArchiveError, OSError, EOFError, gzip.BadGzipFile, zipfile.BadZipFile, zlib.error) as error:
        print(f"Archive verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
