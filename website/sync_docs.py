"""Prepare only the public NAVI pages and record their build provenance."""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAGES = (
    "index.md",
    "getting-started.md",
    "navigation.md",
    "deployment.md",
    "testing.md",
)


def git_output(*args: str) -> str:
    """Read source identity from Git; never fabricate provenance on failure."""
    return subprocess.check_output(
        ["git", "-C", str(ROOT.parent), *args], encoding="utf-8"
    ).strip()


def main() -> None:
    """Validate the CI destination, then generate allowlisted pages and metadata."""
    if os.environ.get("WORKERS_CI") == "1":
        destination = os.environ.get("WRANGLER_CI_OVERRIDE_NAME", "")
        if destination != "roboguide-navi-docs":
            raise SystemExit(
                "Refusing to build NAVI docs for Worker "
                f"{destination or '<unknown>'!r}. Connect a dedicated "
                "roboguide-navi-docs Worker; keep roboguide-docs on main."
            )

    # Resolve every publication input before writing generated files.
    contents = {name: (ROOT / "pages" / name).read_text("utf-8") for name in PAGES}
    metadata = {
        "schema": "roboguide.navi-docs-build/v1",
        "source_commit": git_output("rev-parse", "HEAD"),
        "source_branch": git_output("branch", "--show-current"),
        "working_tree_dirty": bool(git_output("status", "--porcelain")),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "intended_worker": "roboguide-navi-docs",
        "pages": list(PAGES),
    }
    output = ROOT / "docs"
    output.mkdir(exist_ok=True)
    for name, content in contents.items():
        (output / name).write_text(content, encoding="utf-8")
    (output / "build-info.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Prepared {len(contents)} NAVI pages; source {metadata['source_commit']}.")


if __name__ == "__main__":
    main()
