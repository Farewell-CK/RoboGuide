"""Publish reviewed API-generated architecture images and retain exact provenance."""

from __future__ import annotations

import hashlib
import html
import json
import re
import struct
from pathlib import Path
from typing import TypedDict, cast

ROOT = Path(__file__).resolve().parent.parent
FENCE = re.compile(r"^\x60\x60\x60mermaid\n([\s\S]*?)\n\x60\x60\x60", re.MULTILINE)
FIGURE = re.compile(r"<!-- diagram:([a-z-]+):(zh|en) -->\s*\n!\[[^\]]*\]\([^\n)]+\)")


class Diagram(TypedDict):
    """Bind one localized image to its reviewed prompt and code evidence."""

    name: str
    locale: str
    title: str
    prompt: str
    prompt_sha256: str
    image: str
    image_sha256: str | None
    requested_model: str
    status: str
    width: int
    height: int
    evidence: list[str]
    evidence_sha256: dict[str, str]


def digest(value: bytes) -> str:
    """Hash exact bytes without assuming generation is deterministic."""
    return hashlib.sha256(value).hexdigest()


def asset_path(name: str) -> Path:
    """Refuse manifest paths outside the checkout, including escaping symlinks."""
    path = (ROOT / name).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise SystemExit(f"architecture asset escapes repository: {name}")
    return path


def diagram_registry(*, allow_pending: bool = False) -> dict[str, Diagram]:
    """Refuse missing or changed assets; incomplete figures require explicit local preview."""
    manifest = json.loads((ROOT / "website/diagram-assets.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "roboguide.generated-diagrams/v1":
        raise SystemExit("unsupported generated architecture manifest")
    baseline = json.loads((ROOT / "website/content-baseline.json").read_text(encoding="utf-8"))
    if manifest["code_commit"] != baseline["code_commit"]:
        raise SystemExit("architecture figure baseline differs from reviewed content")
    registry: dict[str, Diagram] = {}
    for item in cast(list[Diagram], manifest["diagrams"]):
        key = f"{item['name']}:{item['locale']}"
        if item["locale"] not in {"zh", "en"} or key in registry:
            raise SystemExit(f"duplicate or invalid architecture locale: {key}")
        prompt = asset_path(item["prompt"])
        if not prompt.is_file() or digest(prompt.read_bytes()) != item["prompt_sha256"]:
            raise SystemExit(f"architecture prompt changed: {item['prompt']}")
        if not item["evidence"] or any(not asset_path(p).is_file() for p in item["evidence"]):
            raise SystemExit(f"architecture code evidence is missing: {key}")
        for source in item["evidence"]:
            content = asset_path(source).read_text(encoding="utf-8").encode("utf-8")
            if digest(content) != item["evidence_sha256"].get(source):
                raise SystemExit(f"architecture code evidence changed: {source}")
        if not item["requested_model"].startswith("gpt-image-"):
            raise SystemExit(f"architecture image model is missing: {key}")
        if item["status"] != "reviewed":
            if not allow_pending:
                raise SystemExit(f"architecture image not generated and reviewed: {key}")
        else:
            path = asset_path(item["image"])
            if not path.is_file() or digest(path.read_bytes()) != item["image_sha256"]:
                raise SystemExit(f"architecture image bytes differ: {item['image']}")
            raw = path.read_bytes()
            if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n":
                raise SystemExit(f"architecture asset is not a PNG: {item['image']}")
            if struct.unpack(">II", raw[16:24]) != (item["width"], item["height"]):
                raise SystemExit(f"architecture dimensions differ: {item['image']}")
        registry[key] = item
    for key, item in registry.items():
        other = "en" if item["locale"] == "zh" else "zh"
        if f"{item['name']}:{other}" not in registry:
            raise SystemExit(f"missing bilingual architecture image: {key}")
    return registry


def render_figures(text: str, registry: dict[str, Diagram]) -> str:
    """Publish script-independent images; retain historical diagram sources as literal text."""

    def replace(match: re.Match[str]) -> str:
        """Resolve exactly the registered topic and language."""
        key = f"{match[1]}:{match[2]}"
        if key not in registry:
            raise SystemExit(f"unregistered architecture figure: {key}")
        item = registry[key]
        title = html.escape(item["title"], quote=True)
        prompt = html.escape(published_asset(item["prompt"], item["locale"]), quote=True)
        enlarge, source = (
            ("Open full image", "Generation prompt")
            if item["locale"] == "en"
            else ("查看原图", "生成提示词")
        )
        if item["status"] != "reviewed":
            message = (
                "Text preview only: image generation is blocked by API permissions."
                if item["locale"] == "en"
                else "仅文字预览：生图接口权限尚未开通，当前没有生成图片。"
            )
            return (
                f'<figure class="rg-architecture-figure rg-pending-image" data-diagram="{key}">'
                f"<p>{message}</p><figcaption>{title} · "
                f'<a href="{prompt}">{source}</a></figcaption></figure>'
            )
        image = html.escape(published_asset(item["image"], item["locale"]), quote=True)
        return (
            f'<figure class="rg-architecture-figure" data-diagram="{key}" aria-label="{title}">\n'
            f'<a href="{image}" target="_blank" rel="noopener" aria-label="{enlarge}">'
            f'<img src="{image}" alt="{title}" width="{item["width"]}" '
            f'height="{item["height"]}" decoding="async"></a>\n'
            f'<figcaption>{title} · <a href="{image}" target="_blank" rel="noopener">'
            f'{enlarge}</a> · <a href="{prompt}">{source}</a></figcaption>\n</figure>'
        )

    rendered = FIGURE.sub(replace, text)
    return FENCE.sub(lambda m: "\x60\x60\x60text\n" + m[1] + "\n\x60\x60\x60", rendered)


def published_asset(name: str, locale: str) -> str:
    """Match suffix-localized static assets to MkDocs' actual language-specific output."""
    path = Path(name)
    stem = path.stem.removesuffix("." + locale)
    localized = path.with_name(stem + path.suffix).as_posix()
    return ("/en/" if locale == "en" else "/") + localized
