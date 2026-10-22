from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.common.io_utils import read_text
from src.common.text_utils import normalize_ws
from src.llm.model_client import BaseModelClient

STRING_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
LABEL_RE = re.compile(r":\s*([A-Za-z_][A-Za-z0-9_]*)")
RELTYPE_RE = re.compile(r"\[:\s*([A-Za-z_][A-Za-z0-9_]*)")
PROP_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)")
PARAM_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


@dataclass
class SkeletonResult:
    skeleton_text: str
    holes: List[Dict[str, Any]]
    method: str
    notes: str = ""


def _replace_with_holes(text: str, pattern: re.Pattern[str], hole_type: str, hole_prefix: str,
                        holes: List[Dict[str, Any]]) -> str:
    counter = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal counter
        original = match.group(0)
        name = f"{hole_prefix}_{counter}"
        counter += 1
        holes.append({"name": name, "type": hole_type, "original": original})
        if hole_type == "label":
            return f":{name}"
        if hole_type == "rel_type":
            return f"[:{name}"
        if hole_type == "property":
            return f".{name}"
        if hole_type == "parameter":
            return f"${name}"
        return name

    return pattern.sub(repl, text)


def heuristic_extract_skeleton(query_text: str) -> SkeletonResult:
    skeleton = query_text.strip()
    holes: List[Dict[str, Any]] = []

    skeleton = _replace_with_holes(skeleton, STRING_RE, "string_literal", "HOLE_STR", holes)
    skeleton = _replace_with_holes(skeleton, NUMBER_RE, "number_literal", "HOLE_NUM", holes)
    skeleton = _replace_with_holes(skeleton, RELTYPE_RE, "rel_type", "HOLE_RELTYPE", holes)
    skeleton = _replace_with_holes(skeleton, LABEL_RE, "label", "HOLE_LABEL", holes)
    skeleton = _replace_with_holes(skeleton, PROP_RE, "property", "HOLE_PROP", holes)
    skeleton = _replace_with_holes(skeleton, PARAM_RE, "parameter", "HOLE_PARAM", holes)

    skeleton = normalize_ws(skeleton)
    return SkeletonResult(skeleton_text=skeleton, holes=holes, method="heuristic")


def llm_extract_skeleton(query_text: str, client: BaseModelClient, system_prompt: str) -> SkeletonResult:
    user_prompt = (
        "Extract a reusable Cypher skeleton from the following concrete query. "
        "Return JSON only.\n\n"
        f"QUERY:\n{query_text}\n"
    )
    data = client.chat_json(system_prompt=system_prompt, user_prompt=user_prompt)
    skeleton_text = normalize_ws(str(data.get("skeleton_text", "")).strip())
    holes = data.get("holes", [])
    notes = str(data.get("notes", ""))
    if not skeleton_text:
        raise ValueError("LLM returned empty skeleton_text")
    if not isinstance(holes, list):
        holes = []
    normalized_holes = []
    for item in holes:
        if isinstance(item, dict):
            normalized_holes.append(
                {
                    "name": str(item.get("name", "")).strip(),
                    "type": str(item.get("type", "unknown")).strip(),
                    "original": str(item.get("original", "")).strip(),
                }
            )
    return SkeletonResult(skeleton_text=skeleton_text, holes=normalized_holes, method="llm", notes=notes)


def stable_skeleton_id(text: str) -> str:
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    return f"sk_{h}"


def load_system_prompt(path: str | Path) -> str:
    return read_text(path)
