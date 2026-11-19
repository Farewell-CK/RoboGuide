"""Check generated NAVI pages, local links, anchors and asset references offline."""

import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

SITE = Path(__file__).resolve().parent / "site"


class References(HTMLParser):
    """Collect HTML IDs and only attributes that reference linked resources."""

    def __init__(self) -> None:
        """Initialize per-document link and anchor collections."""
        super().__init__()
        self.links: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Read element references without executing scripts or making requests."""
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(str(values["id"]))
        key = "href" if tag in {"a", "link"} else "src"
        if tag in {"a", "link", "script", "img"} and values.get(key):
            self.links.append(str(values[key]))


def main() -> None:
    """Reject missing pages, broken links, escaping paths and wrong build identity."""
    metadata = json.loads((SITE / "build-info.json").read_text("utf-8"))
    if metadata["intended_worker"] != "roboguide-navi-docs":
        raise SystemExit("Unexpected publication target in build-info.json")
    expected = ["index.html", "404.html"] + [
        f"{Path(name).stem}/index.html"
        for name in metadata["pages"]
        if name != "index.md"
    ]
    missing = [name for name in expected if not (SITE / name).is_file()]
    if missing:
        raise SystemExit(f"Missing generated pages: {missing}")
    documents: dict[Path, References] = {}
    for page in SITE.rglob("*.html"):
        parser = References()
        parser.feed(page.read_text("utf-8"))
        documents[page.resolve()] = parser
    failures = []
    checked = 0
    for page, references in documents.items():
        for link in references.links:
            parts = urlsplit(link)
            if parts.scheme or parts.netloc:
                continue
            path = unquote(parts.path)
            target = (
                (SITE / path.lstrip("/"))
                if path.startswith("/")
                else (page.parent / path if path else page)
            ).resolve()
            checked += 1
            if not target.is_relative_to(SITE.resolve()):
                failures.append(f"{page.name}: path escapes site: {link}")
                continue
            if target.is_dir():
                target = target / "index.html"
            if not target.is_file():
                failures.append(f"{page.name}: missing resource: {link}")
            elif parts.fragment and target in documents:
                if unquote(parts.fragment) not in documents[target].ids:
                    failures.append(f"{page.name}: missing anchor: {link}")
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"Passed: {len(documents)} HTML pages, {checked} local references; build identity present.")


if __name__ == "__main__":
    main()
