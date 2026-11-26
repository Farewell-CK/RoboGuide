"""Regression checks for documentation publication boundaries and source identity."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import diagram_assets
import doc_inventory
import site_support
import sync_docs
from check_site import Document


class PublicationTests(unittest.TestCase):
    """Reject missing translations, version drift and accidental local publication."""

    def _image_fixture(self, root: Path) -> dict[str, object]:
        """Create tiny localized test PNGs, distinct from generated deliverables."""
        import base64

        (root / "website").mkdir()
        (root / "prompt.txt").write_text("exact prompt", encoding="utf-8")
        (root / "source.rs").write_text("original code", encoding="utf-8")
        image = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZQmcAAAAASUVORK5CYII="
        )
        (root / "image.png").write_bytes(image)
        (root / "website/content-baseline.json").write_text(json.dumps({"code_commit": "a" * 40}))
        entries = []
        for locale in ("zh", "en"):
            entries.append(
                {
                    "name": "overview",
                    "locale": locale,
                    "title": "Example",
                    "prompt": "prompt.txt",
                    "prompt_sha256": diagram_assets.digest(b"exact prompt"),
                    "image": "image.png",
                    "image_sha256": diagram_assets.digest(image),
                    "requested_model": "gpt-image-2.6-sunburst",
                    "status": "reviewed",
                    "width": 1,
                    "height": 1,
                    "evidence": ["source.rs"],
                    "evidence_sha256": {"source.rs": diagram_assets.digest(b"original code")},
                }
            )
        manifest: dict[str, object] = {
            "schema": "roboguide.generated-diagrams/v1",
            "code_commit": "a" * 40,
            "generation_records": "generation-records.json",
            "diagrams": entries,
        }
        (root / "generation-records.json").write_text(
            json.dumps(
                {
                    "schema": "roboguide.image-generation-records/v1",
                    "code_commit": "a" * 40,
                    "completed": [
                        {
                            "figure": f"overview:{locale}",
                            "image": "image.png",
                            "image_sha256": diagram_assets.digest(image),
                            "requested_model": "gpt-image-2.6-sunburst",
                            "steps": [
                                {
                                    "endpoint": "/v1/images/generations",
                                    "prompt": "prompt.txt",
                                    "prompt_sha256": diagram_assets.digest(b"exact prompt"),
                                    "inputs": [],
                                    "output_sha256": diagram_assets.digest(image),
                                }
                            ],
                        }
                        for locale in ("zh", "en")
                    ],
                }
            ),
            encoding="utf-8",
        )
        (root / "website/diagram-assets.json").write_text(json.dumps(manifest))
        return manifest

    def test_stale_architecture_asset_refuses_publication(self) -> None:
        """Prompt, image and actual code changes independently invalidate an existing figure."""
        for changed in ("prompt.txt", "image.png", "source.rs"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._image_fixture(root)
                with patch.object(diagram_assets, "ROOT", root):
                    self.assertEqual(len(diagram_assets.diagram_registry()), 2)
                    (root / changed).write_bytes(b"tampered")
                    with self.assertRaises(SystemExit):
                        diagram_assets.diagram_registry()

    def test_pending_images_require_explicit_local_preview(self) -> None:
        """A model listing or a rejected generation request cannot pass the publication gate."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._image_fixture(root)
            entries = manifest["diagrams"]
            assert isinstance(entries, list)
            entries[0]["status"] = "blocked-api"
            (root / "website/diagram-assets.json").write_text(json.dumps(manifest))
            path = root / "generation-records.json"
            records = json.loads(path.read_text(encoding="utf-8"))
            records["completed"] = records["completed"][1:]
            path.write_text(json.dumps(records), encoding="utf-8")
            with patch.object(diagram_assets, "ROOT", root):
                with self.assertRaisesRegex(SystemExit, "not generated and reviewed"):
                    diagram_assets.diagram_registry()
                registry = diagram_assets.diagram_registry(allow_pending=True)
                rendered = diagram_assets.render_figures(
                    "<!-- diagram:overview:zh -->\n![Example](image.png)", registry
                )
                self.assertIn("rg-pending-image", rendered)
                self.assertNotIn("<img", rendered)

    def test_changed_reference_image_refuses_publication(self) -> None:
        """Reference-based edits bind the actual source bytes and selected edit history."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._image_fixture(root)
            reference = root / "reference.png"
            reference.write_bytes(b"reference bytes")
            path = root / "generation-records.json"
            records = json.loads(path.read_text(encoding="utf-8"))
            first_step = records["completed"][0]["steps"][0]
            first_step["endpoint"] = "/v1/images/edits"
            first_step["inputs"] = [
                {"path": "reference.png", "sha256": diagram_assets.digest(b"reference bytes")}
            ]
            path.write_text(json.dumps(records), encoding="utf-8")
            with patch.object(diagram_assets, "ROOT", root):
                self.assertEqual(len(diagram_assets.diagram_registry()), 2)
                reference.write_bytes(b"changed")
                with self.assertRaisesRegex(SystemExit, "reference image changed"):
                    diagram_assets.diagram_registry()

    def test_missing_image_translation_refuses_publication(self) -> None:
        """English/Chinese figure pairs cannot silently drift to one available language."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._image_fixture(root)
            entries = manifest["diagrams"]
            assert isinstance(entries, list)
            manifest["diagrams"] = entries[:1]
            (root / "website/diagram-assets.json").write_text(json.dumps(manifest))
            with patch.object(diagram_assets, "ROOT", root):
                with self.assertRaisesRegex(SystemExit, "bilingual"):
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

    def test_static_assets_follow_actual_i18n_output(self) -> None:
        """Localized PNG and prompt links follow suffix removal and the English prefix."""
        self.assertEqual(
            diagram_assets.published_asset("docs/images/overview.zh.png", "zh"),
            "/docs/images/overview.png",
        )
        self.assertEqual(
            diagram_assets.published_asset("docs/images/prompts/overview.en.txt", "en"),
            "/en/docs/images/prompts/overview.txt",
        )
        self.assertEqual(
            diagram_assets.published_asset(
                "docs/images/prompts/framework.en.reference-v2.txt", "en"
            ),
            "/docs/images/prompts/framework.en.reference-v2.txt",
        )

    def test_archived_site_guide_keeps_generated_reference_link(self) -> None:
        """Original site-relative links are rebased without modifying the preserved Git blob."""
        self.assertEqual(
            sync_docs.rewrite_target(
                "../reference.md", "website/pages/modules", "archive", {}, {}, Path("archive.md")
            ),
            "../reference.md",
        )

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


class InventoryTests(unittest.TestCase):
    """Keep original documents recoverable while current reading routes move."""

    def _inventory_fixture(self, root: Path) -> dict[str, str]:
        """Bind an original Git blob to two canonical language guides and a stable route."""
        for folder in ("docs/reference", "docs/guide", "website/pages"):
            (root / folder).mkdir(parents=True)
        content = b"# Original history\n"
        (root / "docs/history.md").write_bytes(content)
        for suffix in ("", ".en"):
            (root / f"docs/guide/current{suffix}.md").write_text("# Current\n", encoding="utf-8")
        (root / "website/pages/guide.md").write_text(
            "<!-- docs-source: docs/guide/current.md -->\n", encoding="utf-8"
        )
        (root / "website/source-pages.json").write_text(
            json.dumps({"pages": {"guide.md": "docs/guide/current.md"}}), encoding="utf-8"
        )
        (root / "website/content-baseline.json").write_text(
            json.dumps({"code_commit": "a" * 40}), encoding="utf-8"
        )
        (root / "docs/reference/documentation-inventory.json").write_text(
            json.dumps(
                {
                    "schema": "roboguide.documentation-inventory/v1",
                    "code_commit": "a" * 40,
                    "documents": [
                        {
                            "original": "docs/history.md",
                            "original_sha256": hashlib.sha256(content).hexdigest(),
                            "preserved_path": "docs/history.md",
                            "current_path": "docs/guide/current.md",
                            "role": "historical",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        blob = hashlib.sha1(
            b"blob " + str(len(content)).encode() + b"\0" + content, usedforsecurity=False
        )
        return {"docs/history.md": blob.hexdigest()}

    def test_inventory_rejects_missing_or_modified_history(self) -> None:
        """Changing, deleting or omitting an original cannot pass a docs-only migration."""
        for change in ("modify", "delete", "coverage"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                objects = self._inventory_fixture(root)
                with (
                    patch.object(doc_inventory, "ROOT", root),
                    patch.object(doc_inventory, "baseline_objects", return_value=objects),
                ):
                    self.assertEqual(doc_inventory.validate_inventory(), {})
                    if change == "modify":
                        (root / "docs/history.md").write_bytes(b"replacement history")
                    elif change == "delete":
                        (root / "docs/history.md").unlink()
                    else:
                        objects["docs/dropped.md"] = "b" * 40
                    with self.assertRaises(SystemExit):
                        doc_inventory.validate_inventory()

    def test_inventory_rejects_translation_or_route_drift(self) -> None:
        """Route wrappers cannot point elsewhere or lose a current guide's translation."""
        for change in ("translation", "route"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                objects = self._inventory_fixture(root)
                if change == "translation":
                    (root / "docs/guide/current.en.md").unlink()
                else:
                    (root / "website/pages/guide.md").write_text("# duplicate guide\n")
                with (
                    patch.object(doc_inventory, "ROOT", root),
                    patch.object(doc_inventory, "baseline_objects", return_value=objects),
                    self.assertRaises(SystemExit),
                ):
                    doc_inventory.validate_inventory()


if __name__ == "__main__":
    unittest.main()
