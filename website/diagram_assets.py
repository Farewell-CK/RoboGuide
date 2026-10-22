"""Publish source-verified Mermaid SVGs as directly visible, script-independent figures."""

from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path
from typing import TypedDict, cast

ROOT = Path(__file__).resolve().parent.parent
FENCE = re.compile(r"^```mermaid\n([\s\S]*?)\n```", re.MULTILINE)


class Diagram(TypedDict):
    """Retain one rendered asset's original source, byte identity and display dimensions."""

    name: str
    locale: str
    title: str
    source: str
    index: int
    source_sha256: str
    svg: str
    svg_sha256: str
    mmd: str
    width: int
    height: int


def digest(value: bytes) -> str:
    """Return the exact digest shared with the maintained Mermaid renderer."""
    return hashlib.sha256(value).hexdigest()


def diagram_registry() -> dict[str, Diagram]:
    """Refuse stale source/rendered bytes before publishing any registered architecture figure."""
    manifest = json.loads((ROOT / "website/diagram-assets.json").read_text(encoding="utf-8"))
    if digest((ROOT / manifest["renderer"]).read_bytes()) != manifest["renderer_sha256"]:
        raise SystemExit("Mermaid renderer changed: regenerate architecture SVG assets")
    registry: dict[str, Diagram] = {}
    for item in cast(list[Diagram], manifest["diagrams"]):
        codes = FENCE.findall((ROOT / item["source"]).read_text(encoding="utf-8"))
        code = codes[item["index"]].strip() if item["index"] < len(codes) else ""
        if digest(code.encode()) != item["source_sha256"]:
            raise SystemExit(f"stale architecture SVG source: {item['source']}")
        svg = (ROOT / item["svg"]).read_bytes()
        if digest(svg) != item["svg_sha256"]:
            raise SystemExit(f"architecture SVG bytes differ: {item['svg']}")
        if (
            digest((ROOT / item["mmd"]).read_text(encoding="utf-8").strip().encode())
            != item["source_sha256"]
        ):
            raise SystemExit(f"architecture Mermaid download differs: {item['mmd']}")
        if item["width"] <= 0 or item["height"] <= 0:
            raise SystemExit(f"architecture SVG dimensions are unavailable: {item['svg']}")
        registry[item["source_sha256"]] = item
    return registry


def render_figures(text: str, registry: dict[str, Diagram]) -> str:
    """Replace registered Mermaid fences with visible SVGs and expandable original source."""

    def replace(match: re.Match[str]) -> str:
        """Publish a matching immutable source without changing other Mermaid diagrams."""
        code = match.group(1).strip()
        item = registry.get(digest(code.encode()))
        if item is None:
            return match.group(0)
        # Both locales share root assets; raw HTML is not rewritten by MkDocs i18n.
        svg = html.escape("/" + item["svg"], quote=True)
        mmd = html.escape("/" + item["mmd"], quote=True)
        title = html.escape(item["title"], quote=True)
        enlarge, download, source = (
            ("Open full diagram", "Download Mermaid", "View Mermaid source")
            if item["locale"] == "en"
            else ("放大查看", "下载 Mermaid", "查看 Mermaid 源码")
        )
        return (
            f'<figure class="rg-architecture-figure" aria-label="{title}">\n'
            f'<a href="{svg}" target="_blank" rel="noopener" aria-label="{enlarge}">'
            f'<img src="{svg}" alt="{title}" width="{item["width"]}" '
            f'height="{item["height"]}" decoding="async"></a>\n'
            f'<figcaption><a href="{svg}" target="_blank" rel="noopener">{enlarge}</a>'
            f' · <a href="{mmd}" download>{download}</a></figcaption>\n</figure>\n\n'
            f'<details class="rg-diagram-source"><summary>{source}</summary>\n\n'
            f"```text\n{code}\n```\n\n</details>"
        )

    return FENCE.sub(replace, text)
