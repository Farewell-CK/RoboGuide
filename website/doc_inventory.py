"""Check complete documentation migration coverage against the original Git tree."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import TypedDict, cast

ROOT = Path(__file__).resolve().parent.parent


class DocumentEntry(TypedDict):
    """Retain an original file, its preserved bytes and its current reading destination."""

    original: str
    original_sha256: str
    preserved_path: str
    current_path: str
    role: str


def baseline_objects(commit: str) -> dict[str, str]:
    """Read exact original blob identities, including Unicode paths, without checking out files."""
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise SystemExit("documentation inventory needs a complete original commit")
    if subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{tree}}"], cwd=ROOT, stderr=subprocess.DEVNULL
    ).returncode:
        # Workers Builds may check out only HEAD and omit the origin remote.
        subprocess.run(
            [
                "git",
                "fetch",
                "--depth=1",
                "--no-tags",
                "--filter=blob:none",
                "https://github.com/Farewell-CK/RoboGuide.git",
                commit,
            ],
            cwd=ROOT,
            check=True,
        )
    raw = subprocess.check_output(
        ["git", "ls-tree", "-r", "-z", commit, "--", "docs", "README.md", "website/pages"], cwd=ROOT
    )
    objects: dict[str, str] = {}
    for line in raw.split(b"\0"):
        if line:
            metadata, name = line.split(b"\t", 1)
            objects[name.decode("utf-8")] = metadata.split()[2].decode("ascii")
    return objects


def checked_path(name: str) -> Path:
    """Keep migration evidence and reading targets inside this checkout."""
    path = (ROOT / name).resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
        raise SystemExit(f"documentation migration target is missing or escapes repository: {name}")
    return path


def validate_inventory() -> dict[str, str]:
    """Refuse dropped originals, changed historical bytes, missing guides and language drift."""
    manifest = json.loads(
        (ROOT / "docs/reference/documentation-inventory.json").read_text(encoding="utf-8")
    )
    baseline = json.loads((ROOT / "website/content-baseline.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "roboguide.documentation-inventory/v1":
        raise SystemExit("unsupported documentation inventory schema")
    if manifest["code_commit"] != baseline["code_commit"]:
        raise SystemExit("documentation inventory differs from reviewed content baseline")
    original = baseline_objects(manifest["code_commit"])
    entries = cast(list[DocumentEntry], manifest["documents"])
    if len(entries) != len(original) or {row["original"] for row in entries} != set(original):
        raise SystemExit("documentation migration does not cover the complete original Git tree")
    origins: dict[str, str] = {}
    for row in entries:
        preserved = checked_path(row["preserved_path"])
        checked_path(row["current_path"])
        raw = preserved.read_bytes()
        candidates = (raw, raw.replace(b"\r\n", b"\n"))
        exact = next(
            (
                content
                for content in candidates
                if hashlib.sha1(
                    b"blob " + str(len(content)).encode("ascii") + b"\0" + content,
                    usedforsecurity=False,
                ).hexdigest()
                == original[row["original"]]
            ),
            None,
        )
        if exact is None:
            raise SystemExit(
                f"documentation archive differs from original Git blob: {row['original']}"
            )
        if hashlib.sha256(exact).hexdigest() != row["original_sha256"]:
            raise SystemExit(f"preserved Git content digest changed: {row['preserved_path']}")
        if row["role"] == "historical" and row["preserved_path"] != row["original"]:
            raise SystemExit(
                f"historical original must remain at its original path: {row['original']}"
            )
        if row["preserved_path"] != row["original"]:
            origins[row["preserved_path"]] = row["original"]
    routes = json.loads((ROOT / "website/source-pages.json").read_text(encoding="utf-8"))["pages"]
    for route, source in routes.items():
        path = checked_path(source)
        counterpart = (
            path.with_name(path.name.removesuffix(".en.md") + ".md")
            if path.name.endswith(".en.md")
            else path.with_name(path.stem + ".en.md")
        )
        if not counterpart.is_file():
            raise SystemExit(f"canonical guide lacks translation: {source}")
        wrapper = checked_path("website/pages/" + route).read_text(encoding="utf-8").strip()
        if wrapper != f"<!-- docs-source: {source} -->":
            raise SystemExit(f"site route differs from canonical source mapping: {route}")
    return origins
