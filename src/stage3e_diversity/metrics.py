from __future__ import annotations

import itertools
import re
from typing import Iterable, Iterator, NamedTuple


class _Token(NamedTuple):
    kind: str
    text: str


_TOKEN_RE = re.compile(
    r"(?P<space>\s+)"
    r"|(?P<single>'(?:''|\\.|[^'\\])*')"
    r'|(?P<double>"(?:""|\\.|[^"\\])*")'
    r"|(?P<backtick>`(?:``|[^`])*`)"
    r"|(?P<line_comment>//[^\r\n]*)"
    r"|(?P<block_comment>/\*[\s\S]*?\*/)"
    r"|(?P<parameter>\$[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<number>(?:\d+\.\d+|\d+)(?:[eE][+-]?\d+)?)"
    r"|(?P<word>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<operator><>|<=|>=|=~|->|<-|\.\.|\+=|-=|\*=|/=|%=|::|\|\|)"
    r"|(?P<punct>.)"
)


_KEYWORDS = {
    "all",
    "and",
    "any",
    "as",
    "asc",
    "ascending",
    "by",
    "call",
    "case",
    "constraint",
    "contains",
    "create",
    "delete",
    "desc",
    "descending",
    "detach",
    "distinct",
    "drop",
    "else",
    "end",
    "ends",
    "exists",
    "false",
    "filter",
    "foreach",
    "in",
    "index",
    "is",
    "let",
    "limit",
    "match",
    "merge",
    "next",
    "none",
    "not",
    "null",
    "on",
    "optional",
    "or",
    "order",
    "remove",
    "return",
    "set",
    "show",
    "single",
    "skip",
    "starts",
    "then",
    "transactions",
    "true",
    "union",
    "unique",
    "unwind",
    "when",
    "where",
    "with",
    "xor",
    "yield",
}


SEMANTIC_FEATURE_VOCABULARY = (
    "aggregation",
    "all_predicate",
    "any_predicate",
    "avg",
    "call_subquery",
    "case",
    "collect",
    "collect_subquery",
    "count",
    "count_subquery",
    "create",
    "delete",
    "distinct",
    "dynamic_label_or_type",
    "exists",
    "exists_subquery",
    "filter_clause",
    "foreach",
    "limit",
    "list_comprehension",
    "map_projection",
    "match",
    "max",
    "merge",
    "min",
    "none_predicate",
    "null_semantics",
    "optional_match",
    "order_by",
    "path_pattern",
    "procedure_call",
    "property_access",
    "quantified_path",
    "read",
    "read_then_write",
    "reduce",
    "relationship_pattern",
    "remove",
    "return",
    "set",
    "shortest_path",
    "single_predicate",
    "skip",
    "subquery_in_transactions",
    "sum",
    "union",
    "union_all",
    "unwind",
    "variable_length_path",
    "where",
    "with",
    "write",
    "write_then_read",
)

# These features describe ubiquitous query wrappers or derived umbrella
# categories. Excluding them provides a conservative view of nontrivial
# mechanism interactions without discarding specific constructs such as
# OPTIONAL MATCH, UNION, subqueries, or individual write clauses.
GENERIC_STRUCTURAL_FEATURES = frozenset(
    {
        "aggregation",
        "match",
        "path_pattern",
        "property_access",
        "read",
        "relationship_pattern",
        "return",
        "write",
    }
)


def _tokens(query: str) -> list[_Token]:
    return [
        _Token(match.lastgroup or "punct", match.group(0))
        for match in _TOKEN_RE.finditer(query or "")
        if match.lastgroup not in {"space", "line_comment", "block_comment"}
    ]


def _without_cypher_version(tokens: list[_Token]) -> list[_Token]:
    if len(tokens) >= 2 and tokens[0].kind == "word" and tokens[0].text.lower() == "cypher":
        if tokens[1].kind == "number":
            return tokens[2:]
    return tokens


def _next_token(tokens: list[_Token], index: int) -> _Token | None:
    if index + 1 < len(tokens):
        return tokens[index + 1]
    return None


def normalize_cypher_shape(query: str) -> str:
    """Return a conservative lexical shape for semantic duplicate analysis."""

    tokens = _without_cypher_version(_tokens(query))
    normalized: list[str] = []
    for index, token in enumerate(tokens):
        if token.kind in {"single", "double"}:
            normalized.append("str")
        elif token.kind == "number":
            normalized.append("num")
        elif token.kind == "parameter":
            normalized.append("param")
        elif token.kind == "backtick":
            normalized.append("id")
        elif token.kind == "word":
            lowered = token.text.lower()
            if lowered in _KEYWORDS:
                normalized.append(lowered)
            elif (_next_token(tokens, index) or _Token("", "")).text == "(":
                normalized.append(f"fn:{lowered}")
            else:
                normalized.append("id")
        else:
            normalized.append(token.text.lower())
    return " ".join(normalized).strip().rstrip(" ;")


def _semantic_text(query: str) -> str:
    kept: list[str] = []
    for token in _tokens(query):
        if token.kind in {"single", "double", "backtick"}:
            kept.append(" value ")
        elif token.kind == "parameter":
            kept.append(" param ")
        else:
            kept.append(token.text)
    return " ".join(kept)


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL) is not None


def _positions(text: str, pattern: str) -> Iterator[int]:
    for match in re.finditer(pattern, text, flags=re.IGNORECASE | re.DOTALL):
        yield match.start()


def detect_semantic_features(query: str) -> frozenset[str]:
    """Detect only semantic mechanisms observable directly in Cypher text."""

    text = _semantic_text(query)
    features: set[str] = set()
    clause_patterns = {
        "optional_match": r"\bOPTIONAL\s+MATCH\b",
        "match": r"\bMATCH\b",
        "unwind": r"\bUNWIND\b",
        "with": r"\bWITH\b",
        "where": r"\bWHERE\b",
        "filter_clause": r"\bFILTER\b",
        "create": r"\bCREATE\b",
        "merge": r"\bMERGE\b",
        "set": r"\bSET\b",
        "delete": r"\b(?:DETACH\s+)?DELETE\b",
        "remove": r"\bREMOVE\b",
        "return": r"\bRETURN\b",
        "order_by": r"\bORDER\s+BY\b",
        "skip": r"\bSKIP\b",
        "limit": r"\bLIMIT\b",
        "foreach": r"\bFOREACH\b",
        "union": r"\bUNION\b",
    }
    for name, pattern in clause_patterns.items():
        if _has(text, pattern):
            features.add(name)

    aggregate_names = ("count", "collect", "sum", "avg", "min", "max")
    for name in aggregate_names:
        if _has(text, rf"\b{name}\s*\("):
            features.update({"aggregation", name})

    function_features = {
        "exists": r"\bexists\s*(?:\(|\{)",
        "reduce": r"\breduce\s*\(",
        "all_predicate": r"\ball\s*\(",
        "any_predicate": r"\bany\s*\(",
        "none_predicate": r"\bnone\s*\(",
        "single_predicate": r"\bsingle\s*\(",
        "shortest_path": r"\b(?:allShortestPaths|shortestPath)\s*\(",
    }
    for name, pattern in function_features.items():
        if _has(text, pattern):
            features.add(name)

    if _has(text, r"\bCALL\s*\{"):
        features.add("call_subquery")
    if _has(text, r"\bCALL\s+(?!\{)[A-Za-z_]"):
        features.add("procedure_call")
    if _has(text, r"\bCALL\s*\{[\s\S]*?\}\s+IN\s+TRANSACTIONS\b"):
        features.add("subquery_in_transactions")
    if _has(text, r"\bEXISTS\s*\{"):
        features.add("exists_subquery")
    if _has(text, r"\bCOUNT\s*\{"):
        features.add("count_subquery")
    if _has(text, r"\bCOLLECT\s*\{"):
        features.add("collect_subquery")
    if _has(text, r"\bUNION\s+ALL\b"):
        features.add("union_all")
    if _has(text, r"\bDISTINCT\b"):
        features.add("distinct")
    if _has(text, r"\bCASE\b"):
        features.add("case")
    if _has(text, r"\bIS\s+(?:NOT\s+)?NULL\b|\bcoalesce\s*\("):
        features.add("null_semantics")
    if _has(text, r"\[[^\]]*\bIN\b[^\]]*\|"):
        features.add("list_comprehension")
    if _has(text, r"\{\s*\.[A-Za-z_]|\b[A-Za-z_][A-Za-z0-9_]*\s*\{\s*\."):
        features.add("map_projection")
    if _has(text, r"\.[A-Za-z_][A-Za-z0-9_]*"):
        features.add("property_access")
    if _has(text, r"\)\s*(?:<-\s*)?-\s*\[") or _has(text, r"\]\s*-\s*(?:->)?\s*\("):
        features.update({"path_pattern", "relationship_pattern"})
    if _has(text, r"\[[^\]]*\*\s*(?:\d+)?\s*(?:\.\.\s*\d*)?[^\]]*\]"):
        features.update({"path_pattern", "variable_length_path"})
    if _has(text, r"\]\s*(?:->|-)?\s*\{\s*\d+\s*(?:,\s*\d*)?\s*\}"):
        features.update({"path_pattern", "quantified_path"})
    if _has(text, r"\$\s*\("):
        features.add("dynamic_label_or_type")

    write_pattern = r"\b(?:CREATE|MERGE|SET|DELETE|REMOVE|FOREACH)\b"
    read_pattern = r"\b(?:OPTIONAL\s+MATCH|MATCH|UNWIND|WITH|WHERE|FILTER|CALL|RETURN)\b"
    write_positions = list(_positions(text, write_pattern))
    read_positions = list(_positions(text, read_pattern))
    if write_positions:
        features.add("write")
    if read_positions:
        features.add("read")
    if write_positions and read_positions:
        if min(read_positions) < max(write_positions):
            features.add("read_then_write")
        if min(write_positions) < max(read_positions):
            features.add("write_then_read")

    return frozenset(features)


def feature_pairs(features: Iterable[str]) -> frozenset[tuple[str, str]]:
    ordered = sorted(set(features))
    return frozenset(itertools.combinations(ordered, 2))


def semantic_mechanism_features(features: Iterable[str]) -> frozenset[str]:
    """Remove generic wrappers for a conservative mechanism interaction view."""

    return frozenset(set(features) - GENERIC_STRUCTURAL_FEATURES)
