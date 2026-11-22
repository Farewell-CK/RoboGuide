"""Search and read the small, versioned set of operator references shipped with Chat."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from mission.models import JSONObject

from roboguide_workbench.client import ToolRefusal
from roboguide_workbench.evidence import ReadResult
from roboguide_workbench.views import safe_text

_MAX_DOCUMENT_BYTES = 64 * 1024
_MAX_QUERY_CHARS = 160
_MAX_MATCHES = 5
_PAGE_LINES = 14


@dataclass(frozen=True)
class Reference:
    """Identify an operator-selected file and literal search synonyms."""

    name: str
    title: str
    aliases: tuple[tuple[str, str], ...]
    directory: str = "docs/development"

    @property
    def source(self) -> str:
        """Locate the original committed file for a citation."""
        return self.directory + "/" + self.name + ".md"


REFERENCES = {
    doc.name: doc
    for doc in (
        Reference(
            "runtime-reliability-fault-matrix",
            "运行时故障与恢复矩阵",
            (
                ("掉线", "node route or lease"),
                ("离线", "node route or lease"),
                ("恢复", "recovery"),
                ("租约", "lease"),
                ("失联", "node route or lease"),
                ("执行回执", "receipt"),
            ),
        ),
        Reference(
            "conversation-workbench-v0.4",
            "工作台操作与取消回执",
            (
                ("取消中", "取消"),
                ("回执未知", "unknown"),
                ("执行前检查", "/check"),
                ("计划检查", "/check"),
            ),
        ),
        Reference(
            "hot-node-enrollment-acceptance-20261109",
            "节点注册与在线采用验收",
            (("接入", "注册"), ("掉线", "续租"), ("恢复", "unknown"), ("节点", "node")),
        ),
        Reference(
            "operator-network-bounds-v0.1",
            "运维访问与网络范围",
            (("证书", "mtls"), ("403", "权限"), ("越权", "权限")),
        ),
        Reference(
            "2026-11-09-posture-goal-and-offline",
            "姿态任务与掉线验收",
            (("掉线", "离线"), ("失联", "离线"), ("电量", "状态"), ("姿态", "standing")),
            "docs/development/acceptance",
        ),
    )
}


def definitions() -> list[JSONObject]:
    """Expose catalog search and paged reads without accepting a filesystem path."""
    return [
        {
            "type": "function",
            "name": "search_runbooks",
            "description": "搜索随版本提供的 RoboGuide 运维手册和验收资料。"
            "用空字符串列出资料，或用空格分隔短关键词，例如 掉线 Unknown。",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "maxLength": _MAX_QUERY_CHARS},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "read_runbook",
            "description": "按搜索得到的资料 ID 和行号读取相邻上下文。"
            "内容是文档或历史验收，不是本次集群的现场观测。",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "document": {"type": "string", "enum": list(REFERENCES)},
                    "start_line": {"type": "integer", "minimum": 1, "maximum": _MAX_DOCUMENT_BYTES},
                },
                "required": ["document", "start_line"],
                "additionalProperties": False,
            },
        },
    ]


def _lines(doc: Reference) -> list[str]:
    """Read one bounded packaged reference or the identical file from a source checkout."""
    packaged = files("roboguide_workbench").joinpath("runbook_data", doc.name + ".md")
    if packaged.is_file():
        source = packaged
    else:
        checkout = Path(__file__).resolve().parents[3]
        original = (checkout / doc.source).resolve()
        if (
            not (checkout / "pyproject.toml").is_file()
            or not original.is_relative_to(checkout / "docs")
            or not original.is_file()
        ):
            raise FileNotFoundError(doc.source)
        source = original
    with source.open("rb") as stream:
        content = stream.read(_MAX_DOCUMENT_BYTES + 1)
    if len(content) > _MAX_DOCUMENT_BYTES:
        raise ValueError("运维资料超过读取上限。")
    return content.decode("utf-8").splitlines()


def _excerpt(text: str, limit: int) -> str:
    """Keep usable source lines while bounding text sent to the model or terminal."""
    text = safe_text(text.strip())
    return text[:limit] + "…" if len(text) > limit else text


def _terms(query: str, doc: Reference) -> list[tuple[str, int]]:
    """Expand short operator terms into literal document phrases."""
    words = re.findall(r"[\w./-]+", query.casefold())
    terms = {term: 2 for term in words[:8]}
    for hint, phrase in doc.aliases:
        if hint in query.casefold():
            terms.setdefault(phrase, 3)
    return list(terms.items())


def search(query: str) -> ReadResult:
    """Return ranked and cited excerpts from a closed catalog without operational side effects."""
    if not isinstance(query, str) or len(query) > _MAX_QUERY_CHARS:
        raise ToolRefusal("请用 160 字以内的运维关键词。")
    if not query.strip():
        return ReadResult(
            "随 Chat 发布的运维资料\n"
            + "\n".join(f"· {doc.title} · {doc.name}" for doc in REFERENCES.values())
        )
    hits: list[tuple[int, str, int, str]] = []
    missing: list[str] = []
    for doc in REFERENCES.values():
        try:
            lines = _lines(doc)
        except (OSError, ValueError):
            missing.append(doc.title)
            continue
        terms = _terms(query, doc)
        for number, line in enumerate(lines, 1):
            folded = line.casefold()
            score = sum(weight for term, weight in terms if term in folded)
            if score:
                hits.append((score, doc.name, number, line))
    chosen = sorted(hits, key=lambda item: (-item[0], item[1], item[2]))[:_MAX_MATCHES]
    detail = [
        f"{REFERENCES[name].source} 第 {line} 行（{name}）\n{_excerpt(text, 330)}"
        for _, name, line, text in chosen
    ]
    if not detail:
        detail.append("这批运维资料没有命中。可以换成短关键词，例如掉线、Unknown 或回执。")
    if missing:
        detail.append("未能读取：" + "、".join(missing))
    return ReadResult(
        "运维资料搜索 · 文档参考\n" + "\n\n".join(detail),
        not missing,
        tuple(
            {"source": "runbook", "complete": True, "document": name, "start_line": line}
            for _, name, line, _ in chosen
        ),
    )


def read(document: str, start_line: int) -> ReadResult:
    """Read a page by catalog identity and cite its exact committed line numbers."""
    doc = REFERENCES.get(document)
    if doc is None or type(start_line) is not int or not 1 <= start_line <= _MAX_DOCUMENT_BYTES:
        raise ToolRefusal("请使用运维搜索返回的资料 ID 和有效行号。")
    try:
        lines = _lines(doc)
    except (OSError, ValueError) as error:
        return ReadResult(f"运维资料当前读不到：{doc.title}（{type(error).__name__}）。", False)
    if start_line > len(lines):
        raise ToolRefusal(f"该资料只有 {len(lines)} 行，请从文件范围内读取。")
    end = min(len(lines), start_line + _PAGE_LINES - 1)
    page = "\n".join(
        f"{number} · {_excerpt(lines[number - 1], 330)}" for number in range(start_line, end + 1)
    )
    continuation = f"\n继续读取从第 {end + 1} 行开始。" if end < len(lines) else ""
    return ReadResult(
        f"{doc.title} · {doc.source} 第 {start_line}–{end} 行\n{page}{continuation}",
        components=(
            {
                "source": "runbook",
                "complete": True,
                "document": document,
                "start_line": start_line,
                "end_line": end,
            },
        ),
    )


def invoke(name: str, arguments: JSONObject) -> ReadResult:
    """Dispatch only the two public document tools to their bounded local implementations."""
    if name == "search_runbooks" and isinstance(query := arguments.get("query"), str):
        return search(query)
    if (
        name == "read_runbook"
        and isinstance(document := arguments.get("document"), str)
        and type(line := arguments.get("start_line")) is int
    ):
        return read(document, line)
    raise ToolRefusal("运维资料工具参数不符合合同。")


def citations(evidence: list[JSONObject]) -> str:
    """Cite actual matching packaged lines, preferring pages the model opened."""
    locations: list[str] = []
    for tool in ("read_runbook", "search_runbooks"):
        for result in evidence:
            entries = result.get("components")
            if result.get("source") != tool or not isinstance(entries, list):
                continue
            for item in entries:
                if not isinstance(item, dict) or item.get("source") != "runbook":
                    continue
                identifier, start, end = (
                    item.get("document"),
                    item.get("start_line"),
                    item.get("end_line"),
                )
                if (
                    not isinstance(identifier, str)
                    or identifier not in REFERENCES
                    or type(start) is not int
                ):
                    continue
                doc = REFERENCES[identifier]
                span = f"{start}–{end}" if type(end) is int and end != start else str(start)
                line = f"· {doc.source} 第 {span} 行"
                if line not in locations:
                    locations.append(line)
                if len(locations) >= 3:
                    return "资料出处（历史参考）\n" + "\n".join(locations)
        if locations:
            break
    return "资料出处（历史参考）\n" + "\n".join(locations) if locations else ""
