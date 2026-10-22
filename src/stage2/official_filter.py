from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence

from .fragment_policy import is_documented_invalid_example


SUPPORTED_PROFILES = {"none", "neo4j_current", "portable_cypher"}


@dataclass(frozen=True)
class OfficialFilterDecision:
    keep: bool
    reasons: List[str]


COMMON_NOISE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("deprecated_or_removed", re.compile(r"\bdeprecated\b|\bremoved\b|\bno longer supported\b", re.IGNORECASE)),
    ("admin_or_server_command", re.compile(r"\bCREATE\s+(DATABASE|USER|ROLE)\b|\bDROP\s+(DATABASE|USER|ROLE)\b", re.IGNORECASE)),
    ("planner_or_tooling", re.compile(r"\bPROFILE\b|\bEXPLAIN\b", re.IGNORECASE)),
    ("external_extension", re.compile(r"\bapoc\.|\bgds\.|\bgenai\.|\bLOAD\s+CSV\b|file://", re.IGNORECASE)),
    ("procedure_catalog", re.compile(r"\bCALL\s+db\.|\bSHOW\s+(PROCEDURES|FUNCTIONS|SETTINGS|TRANSACTIONS)\b", re.IGNORECASE)),
]

PORTABLE_CYPHER_FILTER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("cypher_25_only", re.compile(r"\bNEXT\b|\bLET\b|\bFILTER\b|\bWHEN\b\s+.+\bTHEN\b", re.IGNORECASE)),
    ("neo4j_type_predicate", re.compile(r"\bIS\s*::", re.IGNORECASE)),
    ("neo4j_vector", re.compile(r"\bvector(?:_|\s*\()|\bVECTOR\b", re.IGNORECASE)),
    ("neo4j_scoped_call", re.compile(r"\bCALL\s*\([^)]*\)\s*\{", re.IGNORECASE)),
    ("neo4j_composite_or_graph", re.compile(r"\bUSE\s+GRAPH\b|\bgraph\.byElementId\b|\bcomposite database\b", re.IGNORECASE)),
    ("schema_catalog", re.compile(r"\bCREATE\s+(INDEX|CONSTRAINT)\b|\bDROP\s+(INDEX|CONSTRAINT)\b", re.IGNORECASE)),
    ("internal_id_or_nondeterminism", re.compile(r"\b(id|elementId|rand|randomUUID|timestamp)\s*\(", re.IGNORECASE)),
]


def normalize_profile(profile: str | None) -> str:
    value = str(profile or "none").strip().lower()
    if value not in SUPPORTED_PROFILES:
        raise ValueError(f"Unsupported official filter profile: {profile}")
    return value


def _chunk_source(chunk: Dict[str, Any]) -> str:
    return str(chunk.get("source") or chunk.get("source_id") or "").strip()


def _chunk_text(chunk: Dict[str, Any]) -> str:
    parts = [
        chunk.get("text"),
        chunk.get("embedding_text"),
        chunk.get("page_title"),
        chunk.get("section_title"),
        chunk.get("chunk_type"),
        chunk.get("url"),
    ]
    metadata = chunk.get("metadata")
    if isinstance(metadata, dict):
        parts.extend(
            [
                metadata.get("source"),
                metadata.get("page_title"),
                metadata.get("section_title"),
                metadata.get("chunk_type"),
                metadata.get("url"),
            ]
        )
    tags = chunk.get("tags") or []
    if isinstance(tags, list):
        parts.extend(tags)
    return "\n".join(str(part) for part in parts if part)


def should_keep_official_chunk(
    chunk: Dict[str, Any],
    *,
    profile: str = "none",
    allowed_sources: Sequence[str] | None = None,
) -> OfficialFilterDecision:
    active_profile = normalize_profile(profile)
    # This is an example-safety policy, independent of dialect filtering.
    if is_documented_invalid_example(chunk, assume_official=True):
        return OfficialFilterDecision(False, ["documented_invalid_example"])
    if active_profile == "none":
        return OfficialFilterDecision(True, [])

    reasons: List[str] = []
    source = _chunk_source(chunk)
    if allowed_sources:
        allowed = {str(item).strip() for item in allowed_sources if str(item).strip()}
        if source not in allowed:
            reasons.append(f"disallowed_source:{source or 'unknown'}")

    text = _chunk_text(chunk)
    for reason, pattern in COMMON_NOISE_PATTERNS:
        if pattern.search(text):
            reasons.append(reason)

    if active_profile == "portable_cypher":
        for reason, pattern in PORTABLE_CYPHER_FILTER_PATTERNS:
            if pattern.search(text):
                reasons.append(reason)

    return OfficialFilterDecision(not reasons, reasons)


def filter_official_chunks(
    chunks: Iterable[Dict[str, Any]],
    *,
    profile: str = "none",
    allowed_sources: Sequence[str] | None = None,
    limit: int | None = None,
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    kept: List[Dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    for chunk in chunks:
        decision = should_keep_official_chunk(chunk, profile=profile, allowed_sources=allowed_sources)
        if decision.keep:
            kept.append(chunk)
            if limit is not None and len(kept) >= limit:
                break
        else:
            reason_counts.update(decision.reasons)
    return kept, dict(reason_counts)
