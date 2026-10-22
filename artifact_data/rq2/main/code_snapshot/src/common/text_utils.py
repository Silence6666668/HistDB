from __future__ import annotations

import re


CODE_FENCE_RE = re.compile(r"^```(?:json|cypher|sql)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
WS_RE = re.compile(r"\s+")


def strip_code_fences(text: str) -> str:
    return CODE_FENCE_RE.sub("", text).strip()


def normalize_ws(text: str) -> str:
    return WS_RE.sub(" ", text).strip()


def first_nonempty(*values: str | None) -> str | None:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v
    return None
