"""Synthetic format/protocol fixtures only; these do not validate a camera, codec, or native OctoMap."""
import gzip
import hashlib
import importlib.util
import io
from contextlib import redirect_stdout
import json
from pathlib import Path
import stat
import struct
import tempfile
import unittest
import warnings
import zipfile

MODULE = Path(__file__).resolve().parents[1] / "inspect_navigation_archive.py"
spec = importlib.util.spec_from_file_location("inspect_navigation_archive", MODULE)
inspect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspect)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fixture(root, files, events=None):
    root.mkdir(parents=True, exist_ok=True)
    entries = []
    if events is not None:
        files = [*files, ("events-00000.jsonl", "events", b"".join(json.dumps(event).encode()+b"\n" for event in events), {})]
    for name, kind, data, meta in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        entries.append({"path": name, "type": kind, "size": len(data), "sha256": sha(data), "metadata": meta})
    manifest = {"schema_version": 1, "session_id": "nav-synthetic", "state": "completed", "capture_complete": True,
                "started_at_ms": 1000, "ended_at_ms": 6500, "files": entries}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def exported(root, destination):
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in root.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())
    return destination


def patch(before, after):
    out = bytearray(b"NDT1" + struct.pack(">q", len(after)))
    for offset in range(0, len(after), 65536):
        block = after[offset:offset+65536]
        if block != before[offset:offset+len(block)]:
            out += struct.pack(">qi", offset, len(block)) + block
    return gzip.compress(bytes(out) + struct.pack(">q", -1))


def map_file(name, kind, data, generation, sequence, target, base=None):
    metadata = {"generation": generation, "sequence": sequence, "scope": "complete_current_native_tree", "sha256": sha(target)}
    if base is not None:
        metadata["base_sha256"] = sha(base)
    return name, kind, data, metadata


def semantic_records(root_confidence=0.25):
    return [(0, 0, 0x88, -0.5, b"\x10\x20\x30", b"\x80\x40\x80", root_confidence, 1),
            (1, 3, 0x04, 1.2, b"\x20\x30\x40", b"\xf4\x23\xe8", 1.0, 0),
            (2, (3 << 3) | 2, 0, 2.0, b"\x30\x40\x50", b"\xf4\x23\xe8", 5.0, 1),
            (1, 7, 0, -1.0, b"\x40\x50\x60", b"\x80\x40\x80", 100.0, 0)]


def sidecar(records, tree_depth=16, leaves=None, nodes=None):
    nodes = len(records) if nodes is None else nodes
    leaves = sum(record[2] == 0 for record in records) if leaves is None else leaves
    header = struct.pack("<8sIIdQQI32s20s", b"NSEM0001", 1, tree_depth, 0.2, nodes, leaves, 25, bytes(range(32)), bytes(range(20)))
    rows = bytearray()
    for depth, path, mask, log_odds, rgb, semantic_rgb, confidence, flag in records:
        occupancy_bits, = struct.unpack("<I", struct.pack("<f", log_odds))
        confidence_bits, = struct.unpack("<I", struct.pack("<f", confidence))
        rows += struct.pack("<BQBI3s3sIB", depth, path, mask, occupancy_bits, rgb, semantic_rgb, confidence_bits, flag)
    return header + rows


def bundle(semantic):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in (("occupancy.ot", b"synthetic compatibility occupancy tree"), ("semantics.bin", semantic)):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return output.getvalue()


class InspectorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_directory_and_zip_verify_same_counts_and_processing_outcomes(self):
        events = [
            {"session_id": "nav-synthetic", "type": "processing_closed_before_result", "elapsed_time_ns": 1234,
             "data": {"processing": {"id": "inference-1", "type": "semantic_inference"}, "status": "not_finished_before_session_end"}},
            {"session_id": "nav-synthetic", "type": "video_frame_dropped", "data": {"reason": "encoder_queue_full"}},
        ]
        data = [("artifacts/inference_rgb/0001.jpg", "inference_rgb", b"synthetic RGB fixture", {"inference_id": "inference-1"})]
        fixture(self.root / "session", data, events)
        archive_zip = exported(self.root / "session", self.root / "session.zip")
        for source in (self.root / "session", archive_zip):
            with self.subTest(source=source), inspect.Archive(source) as archive:
                result = archive.summary()
                self.assertEqual(2, result["verified_files"])
                self.assertEqual(5.5, result["duration_seconds"])
                self.assertEqual(1, result["file_types"]["inference_rgb"])
                self.assertEqual("pending_at_session_end", result["missing_inference_artifacts"][0]["status"])
                self.assertIn("inference_raw_depth", result["missing_inference_artifacts"][0]["missing"])
                self.assertEqual("inference-1", result["processing_terminal_events"][0]["processing_id"])
                self.assertEqual("video_frame_dropped", result["interruptions"][0]["type"])

    def test_tamper_missing_and_cross_session_events_are_rejected(self):
        root = self.root / "session"
        fixture(root, [("data.bin", "synthetic", b"abc", {})])
        (root / "data.bin").write_bytes(b"xyz")
        with inspect.Archive(root) as archive, self.assertRaisesRegex(inspect.ArchiveError, "SHA-256"):
            archive.verify()
        (root / "data.bin").unlink()
        with inspect.Archive(root) as archive, self.assertRaisesRegex(inspect.ArchiveError, "Cannot read"):
            archive.verify()
        fixture(root, [], [{"session_id": "nav-other", "type": "pose", "data": {}}])
        with inspect.Archive(root) as archive, self.assertRaisesRegex(inspect.ArchiveError, "crosses session"):
            archive.summary()

    def test_zip_traversal_symlink_and_duplicate_members_are_rejected(self):
        for mode in ("traversal", "symlink", "duplicate"):
            path = self.root / (mode + ".zip")
            with zipfile.ZipFile(path, "w") as archive:
                if mode == "traversal":
                    archive.writestr("../escaped.txt", b"bad")
                elif mode == "symlink":
                    info = zipfile.ZipInfo("link")
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(info, "outside")
                else:
                    archive.writestr("manifest.json", b"{}")
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", UserWarning)
                        archive.writestr("manifest.json", b"{}")
            with self.subTest(mode=mode), self.assertRaises(inspect.ArchiveError):
                inspect.Archive(path)
        self.assertFalse((self.root.parent / "escaped.txt").exists())

    def test_directory_symlink_is_rejected_without_reading_target(self):
        root = self.root / "session"
        fixture(root, [("data.bin", "synthetic", b"abc", {})])
        target = self.root / "outside.bin"
        target.write_bytes(b"abc")
        (root / "data.bin").unlink()
        (root / "data.bin").symlink_to(target)
        with inspect.Archive(root) as archive, self.assertRaisesRegex(inspect.ArchiveError, "Symbolic link"):
            archive.verify()

    def test_map_checkpoints_growth_truncation_final_and_generation_boundary(self):
        first = b"a" * 70000
        second = first[:66000] + b"b" * 14000
        third = second[:32001]
        other = b"synthetic independent generation"
        files = [
            map_file("maps/g0-1.ot", "octomap_full_checkpoint", first, 0, 1, first),
            map_file("maps/g0-2.gz", "octomap_full_delta", patch(first, second), 0, 2, second, first),
            map_file("maps/g0-3.gz", "octomap_full_delta", patch(second, third), 0, 3, third, second),
            map_file("maps/g0-final.ot", "octomap_full_final_snapshot", third, 0, 3, third),
            map_file("maps/g1-1.ot", "octomap_full_checkpoint", other, 1, 1, other),
        ]
        fixture(self.root / "session", files)
        source = exported(self.root / "session", self.root / "session.zip")
        with inspect.Archive(source) as archive:
            restored = inspect.reconstruct_maps(archive, self.root / "maps")
        self.assertEqual(5, len(restored))
        for entry, expected in zip(restored, (first, second, third, third, other)):
            self.assertEqual(expected, Path(entry["path"]).read_bytes())
            self.assertEqual(sha(expected), entry["sha256"])
        with inspect.Archive(source) as archive, self.assertRaisesRegex(inspect.ArchiveError, "new or empty"):
            inspect.reconstruct_maps(archive, self.root / "maps")

    def test_map_wrong_base_missing_sequence_and_cross_generation_are_rejected(self):
        first, second = b"old", b"newer"
        for mode in ("wrong_hash", "missing_sequence", "other_generation"):
            files = [map_file("maps/1.ot", "octomap_full_checkpoint", first, 0, 1, first),
                     map_file("maps/2.gz", "octomap_full_delta", patch(first, second), 1 if mode == "other_generation" else 0,
                              3 if mode == "missing_sequence" else 2, second, b"wrong" if mode == "wrong_hash" else first)]
            fixture(self.root / mode, files)
            with self.subTest(mode=mode), inspect.Archive(self.root / mode) as archive, self.assertRaisesRegex(inspect.ArchiveError, "map base"):
                inspect.reconstruct_maps(archive, self.root / (mode + "-out"))

    def test_map_target_checksum_and_invalid_patch_spans_are_rejected(self):
        first, second = b"old", b"newer"
        malformed = gzip.compress(b"NDT1" + struct.pack(">q", 5) + struct.pack(">qi", 4, 2) + b"xx" + struct.pack(">q", -1))
        for mode in ("target_hash", "bad_span"):
            files = [map_file("maps/1.ot", "octomap_full_checkpoint", first, 0, 1, first),
                     map_file("maps/2.gz", "octomap_full_delta", malformed if mode == "bad_span" else patch(first, second),
                              0, 2, b"wrong target" if mode == "target_hash" else second, first)]
            fixture(self.root / mode, files)
            with self.subTest(mode=mode), inspect.Archive(self.root / mode) as archive, self.assertRaises(inspect.ArchiveError):
                inspect.reconstruct_maps(archive, self.root / (mode + "-out"))
            self.assertFalse(any((self.root / (mode + "-out")).rglob("*.partial")))

    def test_depth_exact_uint16_stride_unit_and_pgm_preservation(self):
        # Row padding must not be interpreted as pixels. Values deliberately exceed 8-bit range.
        raw = struct.pack("<HHH", 0, 1, 99) + struct.pack("<HHH", 256, 65535, 99)
        name = "artifacts/inference_raw_depth/0001.gz"
        metadata = {"inference_id": "inference-7", "generation": 3, "width": 2, "height": 2,
                    "stride_bytes": 6, "depth_unit_m": 0.001}
        fixture(self.root / "session", [(name, "inference_raw_depth", gzip.compress(raw), metadata)])
        path = exported(self.root / "session", self.root / "session.zip")
        with inspect.Archive(path) as archive:
            result = inspect.depth_info(archive, name, self.root / "depth.pgm")
        self.assertEqual(3, result["valid_pixels"])
        self.assertEqual([0.001, 65.535], result["nonzero_range_m"])
        self.assertEqual("inference-7", result["inference_id"])
        self.assertEqual(b"P5\n2 2\n65535\n" + struct.pack(">HHHH", 0, 1, 256, 65535), (self.root / "depth.pgm").read_bytes())
        with inspect.Archive(path) as archive, self.assertRaises(FileExistsError):
            inspect.depth_info(archive, name, self.root / "depth.pgm")

    def test_navmap_preorder_confidence_and_native_identity(self):
        data = bundle(sidecar(semantic_records()))
        result = inspect.inspect_map_bundle(io.BytesIO(data))
        self.assertEqual(4, result["nodes"])
        self.assertEqual(2, result["leaves"])
        self.assertTrue(result["semantic_state_authoritative"])
        self.assertEqual([-1.0, 2.0], result["occupancy_log_odds_range"])
        self.assertEqual([0.25, 100.0], result["semantic_confidence_range"])
        self.assertEqual(bytes(range(32)).hex(), result["native_library_sha256"])
        self.assertEqual(bytes(range(20)).hex(), result["native_library_build_id"])
        standalone = self.root / "standalone.navmap"
        standalone.write_bytes(data)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, inspect.main(["--map-info", str(standalone), "--json"]))
        self.assertEqual(100, json.loads(output.getvalue())["map_info"]["semantic_confidence_range"][1])

    def test_navmap_reconstruction_keeps_authoritative_sidecar_and_extension(self):
        first = bundle(sidecar(semantic_records()))
        second = bundle(sidecar(semantic_records(10.0)))
        files = [map_file("maps/1.navmap", "octomap_full_checkpoint", first, 0, 1, first),
                 map_file("maps/2.gz", "octomap_full_delta", patch(first, second), 0, 2, second, first),
                 map_file("maps/final.navmap", "octomap_full_final_snapshot", second, 0, 2, second)]
        for entry in files:
            entry[3]["format"] = "zip:occupancy.ot+semantics.bin(NSEM0001)"
        fixture(self.root / "session", files)
        source = exported(self.root / "session", self.root / "session.zip")
        with inspect.Archive(source) as archive:
            inspected = inspect.map_info(archive, "maps/1.navmap")
            self.assertEqual([0.25, 100.0], inspected["semantic_confidence_range"])
            restored = inspect.reconstruct_maps(archive, self.root / "restored")
        for entry, expected in zip(restored, (first, second, second)):
            self.assertTrue(entry["path"].endswith(".navmap"))
            self.assertEqual(expected, Path(entry["path"]).read_bytes())
            self.assertEqual(4, entry["map_info"]["nodes"])
        self.assertEqual([1.0, 100.0], restored[1]["map_info"]["semantic_confidence_range"])

    def test_navmap_rejects_bad_count_topology_levels_and_nonfinite_values(self):
        for mode in ("count", "leaves", "path_levels", "wrong_parent", "depth", "confidence", "occupancy"):
            records = semantic_records()
            if mode == "path_levels":
                row = list(records[2]); row[1] = 129; records[2] = tuple(row)
            if mode == "wrong_parent":
                row = list(records[2]); row[1] = 19; records[2] = tuple(row)
            if mode == "confidence":
                row = list(records[2]); row[6] = float("nan"); records[2] = tuple(row)
            if mode == "occupancy":
                row = list(records[2]); row[3] = float("inf"); records[2] = tuple(row)
            raw = sidecar(records, nodes=5 if mode == "count" else None,
                          leaves=3 if mode == "leaves" else None, tree_depth=17 if mode == "depth" else 16)
            with self.subTest(mode=mode), self.assertRaises(inspect.ArchiveError):
                inspect.inspect_map_bundle(io.BytesIO(bundle(raw)))

    def test_navmap_empty_tree_and_missing_semantic_state(self):
        result = inspect.inspect_map_bundle(io.BytesIO(bundle(sidecar([]))))
        self.assertEqual(0, result["nodes"])
        self.assertIsNone(result["semantic_confidence_range"])
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("occupancy.ot", b"synthetic")
        with self.assertRaisesRegex(inspect.ArchiveError, "exactly occupancy.ot"):
            inspect.inspect_map_bundle(io.BytesIO(output.getvalue()))

    def test_depth_declared_geometry_must_match_decoded_bytes(self):
        name = "depth.gz"
        fixture(self.root / "session", [(name, "inference_aligned_depth", gzip.compress(b"\x00\x00"),
                {"width": 2, "height": 2, "stride_bytes": 4, "depth_unit_m": 0.001})])
        with inspect.Archive(self.root / "session") as archive, self.assertRaisesRegex(inspect.ArchiveError, "Decoded depth size"):
            inspect.depth_info(archive, name)


if __name__ == "__main__":
    unittest.main()
