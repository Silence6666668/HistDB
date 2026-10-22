from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Sequence

WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
CODE_FENCE_RE = re.compile(r"^```[\w-]*\s*|```$", re.MULTILINE)
WS_RE = re.compile(r"\s+")

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it",
    "of", "on", "or", "that", "the", "to", "with", "into", "this", "these", "those", "using",
    "use", "used", "over", "under", "after", "before", "query", "cypher", "returns", "return",
    "match", "where", "with", "call", "all", "any", "none", "single", "create", "merge", "set",
    "delete", "remove", "optional", "order", "skip", "limit", "union", "case", "when", "then",
}


def normalize_ws(text: str) -> str:
    return WS_RE.sub(" ", text or "").strip()


def clean_text(text: str) -> str:
    text = CODE_FENCE_RE.sub("", text or "")
    return normalize_ws(text)


def tokenize(text: str) -> List[str]:
    raw = [m.group(0).lower() for m in WORD_RE.finditer(text or "")]
    out: List[str] = []
    for tok in raw:
        parts = [p for p in tok.replace("__", "_").split("_") if p]
        if not parts:
            parts = [tok]
        for p in parts:
            if p not in STOPWORDS and len(p) > 1:
                out.append(p)
    return out


def iter_jsonl(path: str | Path) -> Iterator[Dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return iter(())
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def write_jsonl(records: Iterable[Dict[str, Any]], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def dump_json(obj: Any, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def stable_id(prefix: str, *parts: Any, length: int = 12) -> str:
    import hashlib

    basis = "||".join(str(p) for p in parts)
    h = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{h}"


def build_idf(corpus_tokens: Sequence[Sequence[str]]) -> Dict[str, float]:
    df: Dict[str, int] = defaultdict(int)
    n_docs = len(corpus_tokens)
    for tokens in corpus_tokens:
        for tok in set(tokens):
            df[tok] += 1
    return {tok: math.log((n_docs + 1) / (freq + 1)) + 1.0 for tok, freq in df.items()}


def first_nonempty(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None
