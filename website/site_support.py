"""Build provenance, bilingual publication rules and source-derived reference pages."""

from __future__ import annotations

import html
import json
import re
import subprocess
import tomllib
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "website" / "content-baseline.json"


@lru_cache(maxsize=1)
def source_revision() -> str:
    """Return the actual checkout commit rather than an untrusted deployment environment value."""
    value = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise SystemExit("documentation requires a complete Git commit identity")
    return value


def tracked_paths() -> set[str]:
    """Return only tracked publication inputs, excluding local meetings and experiments."""
    return set(subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT, text=True).split("\0"))


def read_constant(path: str, name: str) -> str:
    """Read an exact Rust string constant and refuse silent fallback on source drift."""
    text = (ROOT / path).read_text(encoding="utf-8")
    match = re.search(rf'\bpub const {re.escape(name)}:\s*&str\s*=\s*"([^"]+)"', text)
    if match is None:
        raise SystemExit(f"cannot extract authoritative version: {path}:{name}")
    return match.group(1)


def contract_versions() -> dict[str, str]:
    """Extract maintained wire, negotiated semantic, config and checkpoint identities."""
    grpc = "core/integration/src/grpc.rs"
    protocol = re.findall(
        r'pub const PROTOCOL_VERSION:\s*&str\s*=\s*"([^"]+)"', (ROOT / grpc).read_text()
    )
    if not protocol:
        raise SystemExit("Node Protocol version declaration is missing")
    config = re.findall(
        r'pub const CONFIG_SCHEMA_V0_\d+:\s*&str\s*=\s*"([^"]+)"',
        (ROOT / "core/node-service/src/local_engine/mod.rs").read_text(),
    )
    wire_catalogs = re.findall(
        r"contracts/node/(v\d+\.\d+)/roboguide-node\.proto",
        (ROOT / "core/integration/build.rs").read_text(),
    )
    if not wire_catalogs:
        raise SystemExit("compiled Node wire catalog declaration is missing")
    mission = tomllib.loads((ROOT / "config/mission.toml").read_text())
    default_plan = mission["mission"].get("contract_version")
    if not isinstance(default_plan, str):
        raise SystemExit("configured MissionPlan default is missing")
    journal = (ROOT / "core/node-service/src/journal/storage.rs").read_text()
    journal_version = re.search(r'pragma_update\(None, "user_version", (\d+)_i64\)', journal)
    if journal_version is None:
        raise SystemExit("Node journal storage fence declaration is missing")
    return {
        "Resource topology (explicit Control deployment)": json.loads(
            (ROOT / "contracts/control/resource-topology-v0.1/registry.schema.json").read_text()
        )["$id"],
        "MissionPlan (configured default)": default_plan,
        "MissionPlan (explicit Memory)": read_constant(
            "core/domain/src/lib.rs", "MISSION_PLAN_SCHEMA_V0_9"
        ),
        "MissionPlan (explicit quantitative resources)": read_constant(
            "core/domain/src/lib.rs", "MISSION_PLAN_SCHEMA_V0_10"
        ),
        "MissionPlan (explicit scoped typed Memory)": read_constant(
            "core/domain/src/lib.rs", "MISSION_PLAN_SCHEMA_V0_11"
        ),
        "Node journal (storage fence)": "v" + journal_version.group(1),
        "Node wire catalog": max(
            wire_catalogs, key=lambda value: tuple(map(int, value[1:].split(".")))
        ),
        "Node Protocol": max(
            protocol, key=lambda value: tuple(map(int, value.rsplit("v", 1)[1].split(".")))
        ),
        "Node Contract (explicit safety/Memory profile)": read_constant(
            grpc, "SAFETY_NODE_CONTRACT_VERSION"
        ),
        "Node Contract (explicit quantitative profile)": read_constant(
            grpc, "CAPACITY_NODE_CONTRACT_VERSION"
        ),
        "Node Contract (explicit scoped typed Memory profile)": read_constant(
            grpc, "SCOPED_MAP_NODE_CONTRACT_VERSION"
        ),
        "Node config (latest explicit profile)": max(
            config, key=lambda value: int(value.rsplit(".", 1)[1])
        ),
        "Controller checkpoint": read_constant(
            "core/state/src/lib.rs", "CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA"
        ),
        "Capability Catalog": str(
            json.loads((ROOT / "contracts/capability/v0.3/catalog.json").read_text())[
                "schema_version"
            ]
        ),
    }


def validate_pairs(pages: Path) -> None:
    """Require both languages for every maintained page before copying any site content."""
    for path in pages.rglob("*.md"):
        counterpart = (
            path.with_name(path.name.removesuffix(".en.md") + ".md")
            if path.name.endswith(".en.md")
            else path.with_name(path.stem + ".en.md")
        )
        if not counterpart.is_file():
            raise SystemExit(f"missing bilingual counterpart: {counterpart.relative_to(pages)}")


def pin_links(text: str) -> str:
    """Pin repository source links to the actual build commit in both languages."""
    prefix = "https://github.com/Farewell-CK/RoboGuide/"
    return text.replace(prefix + "blob/main/", prefix + "blob/" + source_revision() + "/").replace(
        prefix + "tree/main/", prefix + "tree/" + source_revision() + "/"
    )


def write_reference(destination: Path) -> None:
    """Generate bilingual contract/provenance pages and machine-readable build metadata."""
    baseline = json.loads(BASELINE.read_text())
    versions = contract_versions()
    if versions != baseline["versions"]:
        raise SystemExit(
            "contract versions changed: review content and update website/content-baseline.json"
        )
    dirty = bool(
        subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=normal"], cwd=ROOT, text=True
        ).strip()
    )
    metadata = {
        "schema": "roboguide.docs-build/v1",
        "source_commit": source_revision(),
        "content_review_commit": baseline["code_commit"],
        "reviewed_at": baseline["reviewed_at"],
        "built_at": datetime.now(UTC).isoformat(),
        "working_tree_dirty": dirty,
        "versions": versions,
    }
    (destination / "build-info.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"
    )
    for en in (False, True):
        heading = "Versions and build provenance" if en else "版本与构建来源"
        intro = (
            "Generated from authoritative code declarations. "
            "Build identity and content review baseline are separate."
            if en
            else "版本从权威代码声明生成。站点构建身份与正文核对基线分别记录。"
        )
        notes = (
            "MissionPlan defaults to v0.8; explicit v0.9 retains Memory prerequisites and v0.10 "
            "declares quantitative resources; v0.11 retains scoped typed-map source semantics. "
            "Older compatibility inputs retain their meaning. "
            "A later Node profile requires explicit negotiation; the basic example config remains "
            "v0.7. Node journal " + versions["Node journal (storage fence)"] + " is an independent "
            "storage fence. Node wire catalog " + versions["Node wire catalog"] + " is distinct "
            "from Capability Catalog. Logical allocation does not prove OS/GPU/device enforcement."
            if en
            else "MissionPlan 默认 v0.8，显式 v0.9 保留 Memory 前提，v0.10 声明量化资源，"
            "v0.11 保留 scoped typed source；"
            "旧兼容输入保留原语义。新的 Node profile 必须显式协商，基础示例 config 仍为 v0.7。"
            "Node journal " + versions["Node journal (storage fence)"] + " 是独立存储 fence。"
            "Node wire catalog " + versions["Node wire catalog"] + " 与 Capability Catalog 不同；"
            "逻辑分配不建立 OS/GPU/设备强制事实。"
        )
        lines = [
            f"# {heading}",
            "",
            intro,
            "",
            "| Contract | Source value |",
            "| --- | --- |",
            *[f"| {key} | `{value}` |" for key, value in versions.items()],
            "",
            notes,
            "",
            "## Build / 构建",
            "",
            f"- Source commit: [`{source_revision()[:12]}`]"
            f"(https://github.com/Farewell-CK/RoboGuide/commit/{source_revision()})",
            f"- Content review: `{baseline['code_commit']}` ({baseline['reviewed_at']})",
            f"- Built at (UTC): `{metadata['built_at']}`",
            f"- Working tree dirty: `{str(dirty).lower()}`",
            "",
            "[Machine-readable build information](build-info.json).",
            "",
            (
                "Dirty builds include local edits not represented by the source commit. "
                "Published builds must be clean. Source links resolve to the build commit; "
                "content review remains explicitly dated."
                if en
                else "dirty 构建包含提交 SHA 未覆盖的本地改动，仅用于预览；"
                "发布构建必须来自干净提交。源码链接固定到构建提交，正文核对日期仍单独保留。"
            ),
            "",
            "[Implementation status](status.md) · [ADR topics](decisions.md) "
            "· [Archives](archives.md)",
            "",
        ]
        (destination / ("reference.en.md" if en else "reference.md")).write_text("\n".join(lines))


def legacy_anchors(page: str) -> str:
    """Keep old public heading URLs resolvable after the approved content restructure."""
    aliases = json.loads((ROOT / "website/legacy-anchors.json").read_text())
    return "\n".join(
        f'<span id="{html.escape(anchor, quote=True)}"></span>' for anchor in aliases.get(page, [])
    )
