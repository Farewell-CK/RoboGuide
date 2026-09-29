"""Synchronize curated repository documents into the MkDocs site source tree.

Both the Cloudflare Pages build and the local preview run this script before
``mkdocs build --strict``. Responsibilities and invariants:

- Mirror an explicit, curated list of repository documents into
  ``website/docs/`` so the site always builds from the current working tree;
  source documents are never edited.
- Rename mirrored ``README.md`` files to ``index.md`` for clean URLs and
  rewrite Markdown links so internal targets stay valid under the mirrored
  layout.
- Rewrite links that target repository paths which are intentionally not
  mirrored (source code, contracts, configuration, scenarios) to GitHub URLs
  so they keep working on the deployed site.
- Generate the ADR overview page and the literate-nav ``SUMMARY.md``.
- Fail with a nonzero exit code when a generated link target is missing so a
  broken site can never ship.

The script is standard-library only so the deployment image can run it with a
bare ``python`` interpreter. Failure behavior: exits nonzero when a sync-plan
source is missing or a generated link cannot be resolved; targets that were
already broken in the repository are reported as warnings and left untouched.
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

WEBSITE_DIR = Path(__file__).resolve().parent
REPO_ROOT = WEBSITE_DIR.parent
SITE_DOCS_DIR = WEBSITE_DIR / "docs"
SUMMARY_PATH = SITE_DOCS_DIR / "SUMMARY.md"
GITHUB_BASE = "https://github.com/Farewell-CK/RoboGuide"

#: File suffixes that are copied into the site docs tree; everything else is ignored.
COPY_SUFFIXES: frozenset[str] = frozenset({".md", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".docx"})

#: Curated sync plan as ``(repository path, site path)`` pairs. Directories are
#: mirrored recursively; single files are copied individually.
COPY_PLAN: tuple[tuple[str, str], ...] = (
    ("README.md", "index.md"),
    ("docs", "docs"),
    ("mission/README.md", "mission/index.md"),
    ("evaluation/README.md", "evaluation/index.md"),
    ("evaluation/docs", "evaluation/docs"),
    ("integrations/habitat-local-eaios/README.md", "integrations/habitat-local-eaios/index.md"),
    ("integrations/robonix-map-service/README.md", "integrations/robonix-map-service/index.md"),
)

#: Title of the automatically generated ADR navigation group; the group's
#: children are produced from the mirrored ``docs/decisions/`` pages at sync time.
ADR_NAV_TITLE = "架构决策记录（ADR）"

#: Curated hierarchical navigation as ``(title, parent site path, children)``
#: triples. A group with empty children renders as a plain navigation entry;
#: children are emitted indented by four spaces because Python-Markdown (and
#: therefore literate-nav) only nests list items at that indent width.
NAV_GROUPS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    ("首页", "index.md", ()),
    ("文档索引", "docs/index.md", ()),
    (
        "项目范围",
        "docs/project-goals-and-mvp.md",
        (
            ("MVP 定义", "docs/mvp-definition.md"),
            ("待定决策清单", "docs/implementation-backlog.md"),
        ),
    ),
    (
        "架构",
        "docs/architecture/index.md",
        (
            ("V2 架构基线", "docs/architecture/v2/index.md"),
            ("V1.1 历史架构", "docs/architecture/v1.1/index.md"),
            ("架构图索引", "docs/images/index.md"),
        ),
    ),
    (ADR_NAV_TITLE, "docs/decisions/index.md", ()),
    (
        "开发",
        "docs/development/index.md",
        (
            ("编码规范", "docs/development/coding-standards.md"),
            ("运行时可靠性故障矩阵", "docs/development/runtime-reliability-fault-matrix.md"),
            ("C1 前生产链准入审计", "docs/development/pre-c1-readiness-audit-2026-09-15.md"),
            ("MI 协调指导验证", "docs/development/mi-coordination-guidance-validation.md"),
            ("设备扩展规范 v0.1", "docs/extensions/device-extension-conformance-v0.1.md"),
            ("设备扩展规范 v0.2", "docs/extensions/device-extension-conformance-v0.2.md"),
        ),
    ),
    ("Mission Intelligence", "mission/index.md", ()),
    (
        "Eval Harness",
        "evaluation/index.md",
        (
            ("E1 公平性集成计划", "evaluation/docs/E1_FAIRNESS_INTEGRATION_PLAN.md"),
            ("E1 测试框架就绪审计", "evaluation/docs/E1_HARNESS_READINESS_AUDIT.md"),
            ("E1 公平性基础", "evaluation/docs/e1-fairness-foundation.md"),
            ("E1 公平性评审挑战", "evaluation/docs/e1-fairness-reviewer-challenges.md"),
            ("评估证据有效性规范", "evaluation/docs/evaluation-evidence-validity.md"),
            ("F01-F07 集成回归", "evaluation/docs/f01-f07-integration-regression.md"),
        ),
    ),
    ("Robonix 地图适配器", "integrations/robonix-map-service/index.md", ()),
    ("Habitat Local EAIOS 桥", "integrations/habitat-local-eaios/index.md", ()),
)

LINK_PATTERN = re.compile(r"\]\((?P<target>[^()\s]+)(?P<title>\s+\"[^\"]*\")?\)")
SKIP_TARGET_PREFIXES = ("#", "http://", "https://", "mailto:", "data:")
HEADING_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
STATUS_PATTERN = re.compile(r"^\s*[-*]\s*(?:Status|状态)\s*[:：]\s*(.+?)\s*$", re.MULTILINE)
DATE_PATTERN = re.compile(r"^\s*[-*]\s*(?:Date|日期)\s*[:：]\s*(.+?)\s*$", re.MULTILINE)
ADR_NUMBER_PATTERN = re.compile(r"^(\d{4})-")
ADR_TITLE_PREFIX_PATTERN = re.compile(r"^ADR-\d+\s*[:：]?\s*")


@dataclass(frozen=True)
class AdrSummary:
    """Display metadata for one architecture decision record."""

    number: str
    title: str
    status: str
    date: str
    site_path: str


def build_file_mapping() -> dict[str, str]:
    """Map repository-relative POSIX paths to site-relative POSIX paths.

    Returns:
        Mapping with one entry per file that will exist in the site docs tree.
        Mirrored ``README.md`` files are renamed to ``index.md``.

    Raises:
        SystemExit: If a sync-plan source does not exist in the repository.
    """
    mapping: dict[str, str] = {}
    for source, destination in COPY_PLAN:
        source_path = REPO_ROOT / source
        if source_path.is_dir():
            for file_path in sorted(source_path.rglob("*")):
                relative = file_path.relative_to(source_path)
                if not file_path.is_file() or any(part.startswith(".") for part in relative.parts):
                    continue
                if file_path.suffix.lower() not in COPY_SUFFIXES:
                    continue
                site_relative = Path(destination) / relative
                if site_relative.name == "README.md":
                    site_relative = site_relative.with_name("index.md")
                mapping[file_path.relative_to(REPO_ROOT).as_posix()] = site_relative.as_posix()
        elif source_path.is_file():
            site_relative = Path(destination)
            if site_relative.name == "README.md":
                site_relative = site_relative.with_name("index.md")
            mapping[source] = site_relative.as_posix()
        else:
            raise SystemExit(f"sync plan source is missing: {source}")
    return mapping


def copy_sources(mapping: dict[str, str]) -> None:
    """Recreate the site docs tree from the sync-plan mapping.

    Args:
        mapping: Repository-to-site relative POSIX path mapping.
    """
    if SITE_DOCS_DIR.exists():
        shutil.rmtree(SITE_DOCS_DIR)
    for repo_posix, site_posix in mapping.items():
        destination = SITE_DOCS_DIR / site_posix
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / repo_posix, destination)


def github_url(repo_posix: str, is_directory: bool) -> str:
    """Return the GitHub URL for a repository path.

    Args:
        repo_posix: Repository-relative POSIX path.
        is_directory: Whether the path is a directory (``tree`` instead of ``blob``).

    Returns:
        Absolute GitHub URL with percent-encoded path segments.
    """
    quoted = "/".join(urllib.parse.quote(part, safe="") for part in repo_posix.split("/"))
    kind = "tree" if is_directory else "blob"
    return f"{GITHUB_BASE}/{kind}/main/{quoted}"


def resolve_site_relative(
    repo_target: str, site_source_dir: str, mapping: dict[str, str]
) -> str | None:
    """Resolve a repository path to a site-relative link target.

    Args:
        repo_target: Repository-relative POSIX path of the link target.
        site_source_dir: Site-relative directory of the linking file.
        mapping: Repository-to-site relative POSIX path mapping.

    Returns:
        Site-relative link target, or ``None`` when the path is not mirrored.
    """
    for candidate in (repo_target, f"{repo_target}/index.md"):
        site_target = mapping.get(candidate)
        if site_target is None:
            continue
        new_relative = os.path.relpath(
            str(SITE_DOCS_DIR / site_target), str(SITE_DOCS_DIR / site_source_dir)
        ).replace(os.sep, "/")
        return new_relative
    return None


def rewrite_target(
    target: str,
    repo_source_dir: str,
    site_source_dir: str,
    mapping: dict[str, str],
    broken_by_file: dict[Path, set[str]],
    site_file: Path,
) -> str | None:
    """Rewrite one link target for the site layout.

    Args:
        target: Raw Markdown link target, possibly with an ``#anchor``.
        repo_source_dir: Repository-relative directory of the linking file.
        site_source_dir: Site-relative directory of the linking file.
        mapping: Repository-to-site relative POSIX path mapping.
        broken_by_file: Accumulator for targets that resolve nowhere and are
            kept unchanged because they were already broken in the repository.
        site_file: Site path of the linking file, used to key broken targets.

    Returns:
        The rewritten target, or ``None`` when the target must stay untouched
        (external URL, anchor-only, or outside the repository).
    """
    if not target or target.startswith(SKIP_TARGET_PREFIXES):
        return None
    path_part, anchor_separator, anchor = target.partition("#")
    if not path_part:
        return None
    if path_part.startswith("/"):
        base = ""
        relative = path_part.lstrip("/")
    else:
        base = repo_source_dir
        relative = path_part
    repo_target = posixpath.normpath(posixpath.join(base, relative))
    if repo_target in ("", ".") or repo_target.startswith(".."):
        return None
    suffix = f"#{anchor}" if anchor_separator else ""
    decoded_target = posixpath.normpath(posixpath.join(base, urllib.parse.unquote(relative)))
    for candidate in dict.fromkeys((repo_target, decoded_target)):
        new_relative = resolve_site_relative(candidate, site_source_dir, mapping)
        if new_relative is not None:
            return f"{new_relative}{suffix}"
        absolute = REPO_ROOT / candidate
        if absolute.is_dir():
            return f"{github_url(candidate, is_directory=True)}{suffix}"
        if absolute.is_file():
            return f"{github_url(candidate, is_directory=False)}{suffix}"
    broken_by_file.setdefault(site_file, set()).add(target)
    return target


def rewrite_markdown_file(
    site_file: Path,
    repo_posix: str,
    site_posix: str,
    mapping: dict[str, str],
    broken_by_file: dict[Path, set[str]],
) -> None:
    """Rewrite links inside one mirrored Markdown file in place.

    Args:
        site_file: Absolute path of the mirrored Markdown file.
        repo_posix: Repository-relative POSIX path of the source file.
        site_posix: Site-relative POSIX path of the mirrored file.
        mapping: Repository-to-site relative POSIX path mapping.
        broken_by_file: Accumulator for unresolvable link targets.
    """
    text = site_file.read_text(encoding="utf-8-sig")
    repo_source_dir = posixpath.dirname(repo_posix)
    site_source_dir = posixpath.dirname(site_posix)

    def replace(match: re.Match[str]) -> str:
        """Rewrite a single Markdown inline link match.

        Args:
            match: Regular-expression match for one inline link.

        Returns:
            The original match text, or the link with a rewritten target.
        """
        original = match.group(0)
        target = match.group("target")
        title = match.group("title") or ""
        rewritten = rewrite_target(
            target, repo_source_dir, site_source_dir, mapping, broken_by_file, site_file
        )
        if rewritten is None or rewritten == target:
            return original
        return f"]({rewritten}{title})"

    rewritten_text = LINK_PATTERN.sub(replace, text)
    if rewritten_text != text:
        site_file.write_text(rewritten_text, encoding="utf-8", newline="\n")


def parse_adr_summaries(mapping: dict[str, str]) -> list[AdrSummary]:
    """Collect display metadata for every mirrored ADR page.

    Args:
        mapping: Repository-to-site relative POSIX path mapping.

    Returns:
        ADR summaries sorted by their zero-padded number.
    """
    summaries: list[AdrSummary] = []
    for repo_posix, site_posix in mapping.items():
        if not repo_posix.startswith("docs/decisions/") or not repo_posix.endswith(".md"):
            continue
        number_match = ADR_NUMBER_PATTERN.match(Path(site_posix).name)
        if number_match is None:
            continue
        text = (SITE_DOCS_DIR / site_posix).read_text(encoding="utf-8-sig")
        heading = HEADING_PATTERN.search(text)
        raw_title = heading.group(1) if heading else Path(site_posix).stem
        stripped_title = ADR_TITLE_PREFIX_PATTERN.sub("", raw_title)
        status_match = STATUS_PATTERN.search(text)
        date_match = DATE_PATTERN.search(text)
        summaries.append(
            AdrSummary(
                number=number_match.group(1),
                title=stripped_title or raw_title,
                status=(status_match.group(1) if status_match else "—").replace("|", "\\|"),
                date=(date_match.group(1) if date_match else "—").replace("|", "\\|"),
                site_path=site_posix,
            )
        )
    return sorted(summaries, key=lambda item: item.number)


def write_adr_overview(summaries: list[AdrSummary]) -> Path:
    """Generate the ADR overview page inside the mirrored decisions directory.

    Args:
        summaries: ADR summaries sorted by number.

    Returns:
        Path of the generated overview page.
    """
    lines = [
        "# 架构决策记录（ADR）总览",
        "",
        "架构决策记录按编号正序排列。本页由 `website/sync_docs.py` 自动生成，",
        "请勿手工编辑；原始文件位于仓库 `docs/decisions/` 目录。",
        "",
        "| 编号 | 标题 | 状态 | 日期 |",
        "| --- | --- | --- | --- |",
    ]
    for adr in summaries:
        filename = Path(adr.site_path).name
        lines.append(
            f"| [ADR-{adr.number}]({filename}) | {adr.title} | {adr.status} | {adr.date} |"
        )
    lines.append("")
    destination = SITE_DOCS_DIR / "docs" / "decisions" / "index.md"
    destination.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return destination


def write_summary(summaries: list[AdrSummary]) -> Path:
    """Generate the literate-nav root navigation file.

    Args:
        summaries: ADR summaries sorted by number.

    Returns:
        Path of the generated ``SUMMARY.md``.

    Raises:
        SystemExit: If a curated navigation entry does not exist after syncing.
    """
    adr_children = tuple((f"ADR-{adr.number} {adr.title}", adr.site_path) for adr in summaries)
    groups: list[tuple[str, str, tuple[tuple[str, str], ...]]] = []
    for title, site_path, children in NAV_GROUPS:
        if title == ADR_NAV_TITLE:
            groups.append((title, site_path, adr_children))
        else:
            groups.append((title, site_path, children))
    for _, site_path, children in groups:
        if not (SITE_DOCS_DIR / site_path).is_file():
            raise SystemExit(f"navigation entry target is missing after sync: {site_path}")
        for _, child_path in children:
            if not (SITE_DOCS_DIR / child_path).is_file():
                raise SystemExit(f"navigation child target is missing after sync: {child_path}")
    lines: list[str] = []
    for title, site_path, children in groups:
        lines.append(f"- [{title}]({site_path})")
        for child_title, child_path in children:
            lines.append(f"    - [{child_title}]({child_path})")
    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return SUMMARY_PATH


def validate_site_links(broken_by_file: dict[Path, set[str]]) -> list[str]:
    """Return error lines for every generated internal link whose target is missing.

    Args:
        broken_by_file: Targets that were already broken in the repository and
            are therefore allowed to remain unchanged.

    Returns:
        One error line per unresolved internal link target.
    """
    errors: list[str] = []
    for md_file in sorted(SITE_DOCS_DIR.rglob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        allowed = broken_by_file.get(md_file, set())
        for match in LINK_PATTERN.finditer(text):
            target = match.group("target")
            if target in allowed or target.startswith(SKIP_TARGET_PREFIXES):
                continue
            path_part = target.partition("#")[0]
            if not path_part:
                continue
            resolved = (md_file.parent / path_part).resolve()
            if resolved.exists():
                continue
            errors.append(f"{md_file.relative_to(SITE_DOCS_DIR)}: broken link target {target!r}")
    return errors


def main() -> int:
    """Run the full synchronization and validation pass.

    Returns:
        Process exit code: 0 on success, 1 when generated link targets are
        missing after rewriting.
    """
    mapping = build_file_mapping()
    copy_sources(mapping)
    broken_by_file: dict[Path, set[str]] = {}
    rewritten_pages = 0
    for repo_posix, site_posix in sorted(mapping.items()):
        if not site_posix.endswith(".md"):
            continue
        rewrite_markdown_file(
            SITE_DOCS_DIR / site_posix, repo_posix, site_posix, mapping, broken_by_file
        )
        rewritten_pages += 1
    summaries = parse_adr_summaries(mapping)
    write_adr_overview(summaries)
    write_summary(summaries)
    for site_file, targets in sorted(broken_by_file.items()):
        for target in sorted(targets):
            relative = site_file.relative_to(SITE_DOCS_DIR).as_posix()
            print(f"warning: {relative}: link target {target!r} does not exist in the repository")
    errors = validate_site_links(broken_by_file)
    if errors:
        for error in errors:
            print(f"error: {error}")
        return 1
    print(
        f"synced {len(mapping)} files ({rewritten_pages} markdown pages rewritten), "
        f"{len(summaries)} ADRs indexed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
