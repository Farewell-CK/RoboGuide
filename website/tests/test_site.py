"""Regression checks for documentation publication boundaries and source identity."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import diagram_assets
import site_support
import sync_docs
from check_site import Document


class PublicationTests(unittest.TestCase):
    """Reject missing translations, version drift and accidental local publication."""

    def test_stale_architecture_asset_refuses_publication(self) -> None:
        """Changed source, SVG or source download must not publish an old architecture image."""
        original = diagram_assets.ROOT
        manifest = json.loads((original / "website/diagram-assets.json").read_text())
        manifest["diagrams"] = manifest["diagrams"][:1]
        item = manifest["diagrams"][0]
        inputs = [manifest["renderer"], item["source"], item["svg"], item["mmd"]]
        for changed in (item["source"], item["svg"], item["mmd"]):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "website").mkdir()
                (root / "website/diagram-assets.json").write_text(json.dumps(manifest))
                for name in inputs:
                    destination = root / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes((original / name).read_bytes())
                with patch.object(diagram_assets, "ROOT", root):
                    self.assertEqual(len(diagram_assets.diagram_registry()), 1)
                    path = root / changed
                    path.write_text(
                        path.read_text().replace("flowchart TB", "flowchart LR") + "tampered"
                    )
                    with self.assertRaises(SystemExit):
                        diagram_assets.diagram_registry()

    def test_translation_pair_is_required(self) -> None:
        """A new maintained guide without English must refuse synchronization."""
        with tempfile.TemporaryDirectory() as directory:
            pages = Path(directory)
            (pages / "guide.md").write_text("# 指南\n")
            with self.assertRaisesRegex(SystemExit, "bilingual counterpart"):
                site_support.validate_pairs(pages)
            (pages / "guide.en.md").write_text("# Guide\n")
            site_support.validate_pairs(pages)

    def test_constant_removal_refuses_fallback(self) -> None:
        """A renamed authority constant cannot silently publish a stale version."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.rs").write_text('pub const NEW_NAME: &str = "v2";')
            with patch.object(site_support, "ROOT", root):
                with self.assertRaisesRegex(SystemExit, "authoritative version"):
                    site_support.read_constant("source.rs", "OLD_NAME")

    def test_untracked_and_meeting_files_are_excluded(self) -> None:
        """Recursive mirroring excludes private local files even under docs."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs/meetings").mkdir(parents=True)
            for name in ("public.md", "local.md", "meetings/private.md"):
                (root / "docs" / name).write_text("# document\n")
            with (
                patch.object(sync_docs, "REPO_ROOT", root),
                patch.object(sync_docs, "COPY_PLAN", (("docs", "docs"),)),
                patch.object(
                    sync_docs,
                    "tracked_paths",
                    return_value={
                        "docs/public.md",
                        "docs/meetings/private.md",
                    },
                ),
            ):
                self.assertEqual(
                    sync_docs.build_file_mapping(),
                    {
                        "docs/public.md": "docs/public.md",
                    },
                )

    def test_source_links_use_actual_commit(self) -> None:
        """Both generated and authored source links bind the build's original checkout."""
        revision = "a" * 40
        with (
            patch.object(sync_docs, "source_revision", return_value=revision),
            patch.object(site_support, "source_revision", return_value=revision),
        ):
            link = sync_docs.github_url("core/a b.rs", False)
            self.assertIn(f"/blob/{revision}/core/a%20b.rs", link)
            self.assertEqual(
                site_support.pin_links(
                    "https://github.com/Farewell-CK/RoboGuide/blob/main/core/a.rs"
                ),
                f"https://github.com/Farewell-CK/RoboGuide/blob/{revision}/core/a.rs",
            )

    def test_legacy_local_line_link_becomes_web_source(self) -> None:
        """Archived absolute workspace line links remain usable on the public site."""
        source = "/data/workspace/code/RoboGuide/core/state/src/lib.rs:8"
        target = sync_docs.rewrite_target(source, "", "", {}, {}, Path("archive.md"))
        self.assertIsNotNone(target)
        assert target is not None
        self.assertTrue(target.endswith("/core/state/src/lib.rs#L8"))
        self.assertNotIn("/data/workspace/", target)

    def test_html_parser_preserves_unicode_fragments(self) -> None:
        """Rendered IDs, rather than a guessed Markdown slugger, drive anchor checking."""
        page = Document('<h2 id="证据">Evidence</h2><a href="#%E8%AF%81%E6%8D%AE">go</a>')
        self.assertIn("证据", page.identifiers)
        self.assertEqual(page.links, ["#%E8%AF%81%E6%8D%AE"])

    def test_duplicate_anchor_is_recorded(self) -> None:
        """Compatibility aliases cannot silently shadow a new heading identity."""
        page = Document('<span id="old"></span><h2 id="old">new heading</h2>')
        self.assertEqual(page.duplicates, {"old"})

    def test_version_drift_blocks_reference_publication(self) -> None:
        """Changed source versions require an explicit content review before publication."""
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(site_support, "contract_versions", return_value={"new": "v100"}):
                with self.assertRaisesRegex(SystemExit, "review content"):
                    site_support.write_reference(Path(directory))


if __name__ == "__main__":
    unittest.main()
