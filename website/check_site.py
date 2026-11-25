"""Check rendered local targets, maintained-page anchors, bilingual output and provenance."""

from __future__ import annotations

import argparse
import json
import posixpath
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from diagram_assets import diagram_registry, digest, published_asset
from site_support import ROOT, contract_versions, source_revision, validate_pairs


class Document(HTMLParser):
    """Collect rendered identifiers, asset references and links without executing JavaScript."""

    def __init__(self, text: str) -> None:
        """Parse one complete HTML document."""
        super().__init__()
        self.identifiers: set[str] = set()
        self.duplicates: set[str] = set()
        self.links: list[str] = []
        self.feed(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Retain semantic anchor IDs and references to local documents/assets."""
        values = dict(attrs)
        identifier = values.get("id")
        if identifier:
            if identifier in self.identifiers:
                self.duplicates.add(identifier)
            self.identifiers.add(identifier)
        for key in ("href", "src"):
            target = values.get(key)
            if target:
                self.links.append(target)


def maintained_routes() -> set[str]:
    """Map every authored bilingual page to its actual MkDocs output route."""
    routes: set[str] = set()
    for source in (ROOT / "website/pages").rglob("*.md"):
        relative = source.relative_to(ROOT / "website/pages").as_posix()
        en = relative.endswith(".en.md")
        stem = relative.removesuffix(".en.md") if en else relative.removesuffix(".md")
        route = ("" if stem == "index" else stem + "/") + "index.html"
        routes.add(("en/" if en else "") + route)
    for source in (ROOT / "docs").rglob("*.md"):
        relative = source.relative_to(ROOT).as_posix()
        if not (
            relative.startswith(("docs/getting-started/", "docs/guides/"))
            or (
                relative.startswith("docs/architecture/v2/")
                and source.name
                in {
                    name + suffix + ".md"
                    for name in (
                        "README",
                        "architecture-handbook",
                        "current-architecture",
                        "system-dataflows",
                        "architecture-tour",
                        "execution-lifecycle",
                        "state-memory",
                        "concepts",
                    )
                    for suffix in ("", ".en")
                }
            )
            or relative
            in {
                "docs/README.md",
                "docs/README.en.md",
                "docs/operations/README.md",
                "docs/operations/README.en.md",
                "docs/reference/contracts-and-build.md",
                "docs/reference/contracts-and-build.en.md",
                "docs/reference/implementation-status.md",
                "docs/reference/implementation-status.en.md",
            }
        ) or ("/reference/" in relative and not relative.startswith("docs/reference/")):
            continue
        en = relative.endswith(".en.md")
        stem = relative.removesuffix(".en.md") if en else relative.removesuffix(".md")
        stem = stem.removesuffix("README") if stem.endswith("README") else stem + "/"
        routes.add(("en/" if en else "") + stem + "index.html")
    for prefix in ("", "en/"):
        routes.add(prefix + "reference/index.html")
    return routes


def architecture_diagram_errors() -> list[str]:
    """Require localized current figures and refuse Mermaid in maintained guide sources."""
    errors: list[str] = []
    sources = json.loads((ROOT / "website/source-pages.json").read_text(encoding="utf-8"))["pages"]
    for name in set(sources.values()) | {"README.md"}:
        if "```mermaid" in (ROOT / name).read_text(encoding="utf-8"):
            errors.append(f"{name}: maintained guide still contains Mermaid")
    topics = (
        "overview",
        "mission-planning",
        "dispatch",
        "completion",
        "state-memory",
        "recovery",
        "cooperation",
        "deployment",
    )
    for locale, suffix in (("zh", ""), ("en", ".en")):
        text = (ROOT / f"docs/architecture/v2/current-architecture{suffix}.md").read_text(
            encoding="utf-8"
        )
        for topic in topics:
            if text.count(f"<!-- diagram:{topic}:{locale} -->") != 1:
                errors.append(f"architecture atlas requires one {topic}:{locale} figure")
    return errors


def inspect(site: Path, *, text_preview: bool = False) -> list[str]:
    """Return publication errors; archived anchor imperfections remain separately counted."""
    validate_pairs(ROOT / "website/pages")
    registry = diagram_registry(allow_pending=text_preview)
    errors = architecture_diagram_errors()
    site = site.resolve()
    files = {path.relative_to(site).as_posix(): path for path in site.rglob("*") if path.is_file()}
    for item in registry.values():
        fields = ("prompt", "image") if item["status"] == "reviewed" else ("prompt",)
        for field in fields:
            name = item["prompt"] if field == "prompt" else item["image"]
            expected = item["prompt_sha256"] if field == "prompt" else item["image_sha256"]
            destination = published_asset(name, item["locale"]).lstrip("/")
            if destination not in files or digest(files[destination].read_bytes()) != expected:
                errors.append(
                    f"published architecture {field} differs from reviewed bytes: {destination}"
                )
    documents = {
        name: Document(path.read_text(encoding="utf-8"))
        for name, path in files.items()
        if name.endswith(".html")
    }
    maintained = maintained_routes()
    for route in sorted(maintained):
        if route not in files:
            errors.append(f"missing maintained bilingual output: {route}")
    historical_anchors: set[str] = set()
    for relative, document in documents.items():
        if relative in maintained:
            for identifier in document.duplicates:
                errors.append(f"{relative}: duplicate anchor {identifier}")
        for link in document.links:
            parsed = urlsplit(link)
            if parsed.scheme or parsed.netloc:
                continue
            if parsed.path.startswith("/data/") or parsed.path.startswith("/home/"):
                errors.append(f"{relative}: local filesystem link leaked: {link}")
                continue
            target = (
                relative
                if not parsed.path
                else posixpath.normpath(
                    unquote(parsed.path).lstrip("/")
                    if parsed.path.startswith("/")
                    else posixpath.join(posixpath.dirname(relative), unquote(parsed.path))
                )
            )
            index = target.rstrip("/") + "/index.html" if target != "." else "index.html"
            if target not in files and index in files:
                target = index
            if target not in files:
                errors.append(f"{relative}: missing local target {link}")
            elif parsed.fragment and target in documents:
                anchor = unquote(parsed.fragment)
                if anchor not in documents[target].identifiers:
                    message = f"{relative}: missing anchor {link}"
                    if relative in maintained:
                        errors.append(message)
                    else:
                        historical_anchors.add(message)
    metadata = json.loads((site / "build-info.json").read_text(encoding="utf-8"))
    if not metadata.get("publication_ready", False) and not text_preview:
        errors.append("text-only preview cannot be published as a completed update")
    if metadata["source_commit"] != source_revision():
        errors.append("build-info source does not match checkout")
    if metadata["versions"] != contract_versions():
        errors.append("rendered contract versions differ from authority declarations")
    print(f"Checked {len(documents)} HTML pages; {len(maintained)} maintained routes.")
    print(f"Archived unresolved anchors: {len(historical_anchors)} (not current guide claims).")
    return sorted(set(errors))


def main() -> int:
    """Inspect a built static site and refuse broken publication targets."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", nargs="?", type=Path, default=ROOT / "website/site")
    parser.add_argument(
        "--text-preview", action="store_true", help="Inspect a local text-only preview"
    )
    args = parser.parse_args()
    site = args.site
    if not site.is_dir():
        raise SystemExit("build the site before running check_site.py")
    errors = inspect(site, text_preview=args.text_preview)
    for error in errors:
        print(f"error: {error}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
