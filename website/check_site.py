"""Check rendered local targets, maintained-page anchors, bilingual output and provenance."""

from __future__ import annotations

import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from diagram_assets import diagram_registry
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
    for prefix in ("", "en/"):
        routes.add(prefix + "reference/index.html")
        routes.add(prefix + "docs/architecture/v2/architecture-handbook/index.html")
        routes.add(prefix + "docs/architecture/v2/current-architecture/index.html")
        routes.add(prefix + "docs/architecture/v2/index.html")
    return routes


def architecture_diagram_errors() -> list[str]:
    """Reject overview drift between downloadable Mermaid and maintained entry pages."""
    errors: list[str] = []
    for suffix in ("", ".en"):
        asset_suffix = "-english" if suffix else ""
        source = (
            ROOT / f"docs/images/roboguide-current-architecture{asset_suffix}.mmd"
        ).read_text()
        expected = "```mermaid\n" + source.rstrip() + "\n```"
        paths = [
            f"docs/architecture/v2/current-architecture{suffix}.md",
            f"website/pages/architecture-tour{suffix}.md",
        ]
        if not suffix:
            paths.extend(("README.md", "docs/architecture/v2/README.md"))
        else:
            paths.append("docs/architecture/v2/README.en.md")
        for name in paths:
            text = (ROOT / name).read_text()
            start = "<!-- architecture-overview:start -->"
            end = "<!-- architecture-overview:end -->"
            if text.count(start) != 1 or text.count(end) != 1:
                errors.append(f"{name}: missing or duplicate architecture overview markers")
            elif text.split(start)[1].split(end)[0].strip() != expected:
                errors.append(
                    f"{name}: overview differs from canonical {suffix or 'Chinese'} Mermaid"
                )
    return errors


def inspect(site: Path) -> list[str]:
    """Return publication errors; archived anchor imperfections remain separately counted."""
    validate_pairs(ROOT / "website/pages")
    diagram_registry()
    errors = architecture_diagram_errors()
    documents = {
        path.resolve(): Document(path.read_text(encoding="utf-8")) for path in site.rglob("*.html")
    }
    maintained = maintained_routes()
    for route in sorted(maintained):
        if not (site / route).is_file():
            errors.append(f"missing maintained bilingual output: {route}")
    historical_anchors: set[str] = set()
    for path, document in documents.items():
        relative = path.relative_to(site.resolve()).as_posix()
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
                site / unquote(parsed.path).lstrip("/")
                if parsed.path.startswith("/")
                else path.parent / unquote(parsed.path)
            ).resolve()
            if target.is_dir():
                target /= "index.html"
            if not target.is_file():
                errors.append(f"{relative}: missing local target {link}")
            elif parsed.fragment and target in documents:
                anchor = unquote(parsed.fragment)
                if anchor not in documents[target].identifiers:
                    message = f"{relative}: missing anchor {link}"
                    if relative in maintained:
                        errors.append(message)
                    else:
                        historical_anchors.add(message)
    metadata = json.loads((site / "build-info.json").read_text())
    if metadata["source_commit"] != source_revision():
        errors.append("build-info source does not match checkout")
    if metadata["versions"] != contract_versions():
        errors.append("rendered contract versions differ from authority declarations")
    print(f"Checked {len(documents)} HTML pages; {len(maintained)} maintained routes.")
    print(f"Archived unresolved anchors: {len(historical_anchors)} (not current guide claims).")
    return sorted(set(errors))


def main() -> int:
    """Inspect a built static site and refuse broken publication targets."""
    site = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "website/site"
    if not site.is_dir():
        raise SystemExit("build the site before running check_site.py")
    errors = inspect(site)
    for error in errors:
        print(f"error: {error}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
