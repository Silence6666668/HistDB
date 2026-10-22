from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Tuple


RewriteFn = callable


def stable_pair_id(case_id: str, rewrite_type: str, rewritten_query: str) -> str:
    raw = f"{case_id}||{rewrite_type}||{rewritten_query}"
    return "pair_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _split_language_prefix(query: str) -> Tuple[str, str]:
    """Separate Neo4j language-version prefix from the executable body.

    Most rewrite rules were written against plain Cypher bodies starting at
    MATCH/WITH/RETURN. Cypher 25 testcases intentionally start with
    `CYPHER 25`, so self-diff generation should rewrite the body and then put
    the language prefix back.
    """
    match = re.match(r"(?is)^\s*(CYPHER\s+(?:5|25))\b\s*(?P<body>[\s\S]+)$", query.strip())
    if not match:
        return "", query.strip()
    return match.group(1).strip(), match.group("body").strip()


def _join_language_prefix(prefix: str, body: str) -> str:
    body = body.strip()
    if not prefix or re.match(r"(?is)^CYPHER\b", body):
        return body
    return f"{prefix} {body}"


def _find_outer_braced_subquery(query: str, keyword: str) -> Tuple[int, int, str] | None:
    pattern = re.compile(rf"(?is)\b{re.escape(keyword)}\s*\{{")
    match = pattern.search(query)
    if not match:
        return None
    open_idx = query.find("{", match.start())
    if open_idx < 0:
        return None

    depth = 0
    quote: str | None = None
    i = open_idx
    while i < len(query):
        ch = query[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        else:
            if ch in ("'", '"'):
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return match.start(), i, query[open_idx + 1 : i].strip()
        i += 1
    return None


def _find_outer_exists_function_pattern(query: str) -> Tuple[int, int, str] | None:
    pattern = re.compile(r"(?is)\bexists\s*\(")
    match = pattern.search(query)
    if not match:
        return None

    open_idx = query.find("(", match.start())
    if open_idx < 0:
        return None

    depth = 0
    quote: str | None = None
    i = open_idx
    while i < len(query):
        ch = query[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        else:
            if ch in ("'", '"'):
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    body = query[open_idx + 1 : i].strip()
                    if body.startswith("(") and body.endswith(")"):
                        return match.start(), i, body
                    return None
        i += 1
    return None


def _inject_relationship_variable(pattern_text: str, base_name: str) -> Tuple[str | None, str | None]:
    match = re.match(
        r"(?is)^(?P<left>\([^)]+\))\s*(?P<before><-|-)\[(?P<rel>[^\]]*)\](?P<after>->|-)\s*(?P<right>\([^)]+\))$",
        pattern_text.strip(),
    )
    if not match:
        return None, None

    left = match.group("left").strip()
    before = match.group("before")
    rel = match.group("rel").strip()
    after = match.group("after")
    right = match.group("right").strip()

    if "*" in rel:
        return None, None

    var_match = re.match(r"(?is)^([A-Za-z_]\w*)(?=[:{\s]|$)", rel)
    if var_match:
        rel_var = var_match.group(1)
        rewritten_rel = rel
    else:
        rel_var = base_name
        if not rel:
            rewritten_rel = rel_var
        elif rel.startswith(":"):
            rewritten_rel = f"{rel_var}{rel}"
        elif rel.startswith("{"):
            rewritten_rel = f"{rel_var} {rel}"
        else:
            return None, None

    rewritten_pattern = f"{left}{before}[{rewritten_rel}]{after}{right}"
    return rewritten_pattern, rel_var


def _split_top_level_csv(text: str) -> List[str]:
    parts: List[str] = []
    buf: List[str] = []
    paren = bracket = brace = 0
    quote: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == "\\":
                i += 1
                if i < len(text):
                    buf.append(text[i])
            elif ch == quote:
                quote = None
        else:
            if ch in ("'", '"'):
                quote = ch
                buf.append(ch)
            elif ch == "(":
                paren += 1
                buf.append(ch)
            elif ch == ")":
                paren = max(paren - 1, 0)
                buf.append(ch)
            elif ch == "[":
                bracket += 1
                buf.append(ch)
            elif ch == "]":
                bracket = max(bracket - 1, 0)
                buf.append(ch)
            elif ch == "{":
                brace += 1
                buf.append(ch)
            elif ch == "}":
                brace = max(brace - 1, 0)
                buf.append(ch)
            elif ch == "," and paren == 0 and bracket == 0 and brace == 0:
                part = "".join(buf).strip()
                if part:
                    parts.append(part)
                buf = []
            else:
                buf.append(ch)
        i += 1
    part = "".join(buf).strip()
    if part:
        parts.append(part)
    return parts


def _is_simple_neo4j_metamorphic_outer_query(query: str) -> bool:
    lowered = f" {query.lower()} "
    if len(re.findall(r"\breturn\b", lowered)) != 1:
        return False
    blocked = [
        " call ",
        " union ",
        " create ",
        " merge ",
        " delete ",
        " set ",
        " remove ",
        " load csv ",
        " foreach ",
        " unwind ",
    ]
    return not any(token in lowered for token in blocked)


def _has_nondeterministic_selfdiff_functions(query: str) -> bool:
    lowered = query.lower()
    if re.search(r"\brand\s*\(", lowered):
        return True
    if re.search(r"\brandomuuid\s*\(", lowered):
        return True
    if re.search(r"\btimestamp\s*\(", lowered):
        return True
    if re.search(r"\b(?:datetime|localdatetime|date|time|localtime)\s*\(\s*\)", lowered):
        return True
    if re.search(r"\b(?:datetime|localdatetime|date|time|localtime)\.(?:realtime|transaction|statement)\s*\(", lowered):
        return True
    # datetime({timezone: ...}) and similar clock-derived map constructors depend
    # on the current instant even though they are not empty-argument calls.
    if re.search(r"\b(?:datetime|time)\s*\(\s*\{[^}]*\btimezone\b", lowered, flags=re.S):
        return True
    return False


def _has_write_clause(query: str) -> bool:
    return bool(
        re.search(
            r"\b(?:CREATE|MERGE|DELETE|DETACH\s+DELETE|SET|REMOVE|FOREACH|LOAD\s+CSV)\b",
            query,
            flags=re.I,
        )
    )


def _is_dinkel_safe_read_query(query: str) -> bool:
    lowered = f" {query.lower()} "
    if _has_nondeterministic_selfdiff_functions(query):
        return False
    if _has_cypher_comment(query):
        return False
    blocked = [
        " create ",
        " merge ",
        " delete ",
        " detach delete ",
        " set ",
        " remove ",
        " load csv ",
        " foreach ",
        " call db.",
        " call apoc.",
        " call gds.",
        " show ",
        " union ",
        " cypher ",
        " profile ",
        " explain ",
        " limit ",
        " skip ",
        " offset ",
    ]
    if any(token in lowered for token in blocked):
        return False
    if re.search(r"\b(?:id|elementid)\s*\(", lowered):
        return False
    if _has_unstable_collection_order_hazard(query):
        return False
    return bool(re.search(r"\breturn\b", lowered))


def _final_return_match(query: str) -> re.Match[str] | None:
    return re.search(r"(?is)\bRETURN\s+(?P<body>.+?)(?P<tail>\s+ORDER\s+BY\s+.+|\s+LIMIT\s+\d+\s*|\s*)$", query.strip())


def _is_likely_numeric_expr(expr: str) -> bool:
    expr_lower = expr.lower().strip()
    if re.search(r"\bcase\b|\[|\]|\bcollect\s*\(", expr_lower):
        return False
    if re.fullmatch(r"-?\d+(?:\.\d+)?", expr_lower):
        return True
    if re.search(r"\b(?:count|sum|min|max|avg|size|length|abs|round|floor|ceil)\s*\(", expr_lower):
        return True
    return bool(re.search(r"\.(?:age|year|score|value|weight|count|cnt|size|len|length|price|amount|total|rank)\b", expr_lower))


def _is_safe_dinkel_expression_text(expr: str) -> bool:
    expr_lower = expr.lower()
    return not bool(
        re.search(r"\b(?:rand|randomuuid|timestamp)\s*\(", expr_lower)
        or re.search(r"\b(?:datetime|localdatetime|date|time|localtime)\s*\(\s*\)", expr_lower)
        or re.search(r"\b(?:id|elementid)\s*\(", expr_lower)
    )


def _split_return_alias(item: str) -> Tuple[str | None, str | None]:
    m = re.match(r"(?is)^(?P<expr>.+?)\s+AS\s+(?P<alias>[A-Za-z_]\w*)$", item.strip())
    if not m:
        return None, None
    return m.group("expr").strip(), m.group("alias").strip()


def _split_top_level_boolean_operator(cond: str, operator: str) -> Tuple[str | None, str | None]:
    op = operator.upper()
    paren = bracket = brace = 0
    quote: str | None = None
    i = 0
    while i < len(cond):
        ch = cond[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        else:
            if ch in ("'", '"'):
                quote = ch
            elif ch == "(":
                paren += 1
            elif ch == ")":
                paren = max(paren - 1, 0)
            elif ch == "[":
                bracket += 1
            elif ch == "]":
                bracket = max(bracket - 1, 0)
            elif ch == "{":
                brace += 1
            elif ch == "}":
                brace = max(brace - 1, 0)
            elif paren == 0 and bracket == 0 and brace == 0:
                if cond[i : i + len(op)].upper() == op:
                    before_ok = i == 0 or not (cond[i - 1].isalnum() or cond[i - 1] == "_")
                    after_i = i + len(op)
                    after_ok = after_i >= len(cond) or not (cond[after_i].isalnum() or cond[after_i] == "_")
                    if before_ok and after_ok:
                        left = cond[:i].strip()
                        right = cond[after_i:].strip()
                        if left and right:
                            return left, right
        i += 1
    return None, None


def _normalize_projection_or_order_term(term: str) -> str:
    normalized = re.sub(r"(?is)\s+(ASC|DESC)\b", "", term.strip())
    normalized = re.sub(r"(?is)\s+NULLS\s+(FIRST|LAST)\b", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip().lower()


def _return_projection_terms(body: str) -> List[str]:
    terms: List[str] = []
    body = re.sub(r"(?is)^\s*DISTINCT\s+", "", body.strip())
    for item in _split_top_level_csv(body):
        expr, alias = _split_return_alias(item)
        terms.append(_normalize_projection_or_order_term(alias or item))
    return [term for term in terms if term and term != "*"]


def _final_order_terms(tail: str) -> List[str]:
    m = re.search(r"(?is)\bORDER\s+BY\s+(?P<body>.*?)(?:\s+(?:SKIP|OFFSET|LIMIT)\b|$)", tail or "")
    if not m:
        return []
    return [_normalize_projection_or_order_term(part) for part in _split_top_level_csv(m.group("body"))]


def _has_cypher_comment(query: str) -> bool:
    return bool(re.search(r"//|/\*|\*/", query))


def _has_unstable_collection_order_hazard(query: str) -> bool:
    """Skip metamorphic oracles based on unspecified collection/path order."""
    pattern_comprehension = re.search(
        r"\[[\s\S]{0,500}(?:--|->|<-)[\s\S]{0,500}\|",
        query,
        flags=re.I,
    )
    return bool(
        pattern_comprehension
        or re.search(r"\b[A-Za-z_]\w*\s*\[\s*\d+\s*\]", query)
        or re.search(r"\b(?:collect|nodes|relationships)\s*\(", query, flags=re.I)
        or re.search(r"\[[^\]]*\*", query)
    )


def _has_potential_fetch_limit_order_hazard(query: str) -> bool:
    """Avoid false differences from driver fetch limits and tied result ordering."""
    lowered = f" {query.lower()} "
    if " match " not in lowered:
        return False
    m = _final_return_match(query)
    if not m:
        return True
    projections = _return_projection_terms(m.group("body"))
    order_terms = _final_order_terms(m.group("tail") or "")
    if not order_terms:
        return True
    # If the order does not cover every projected value, tied rows can be
    # fetched as different first-N windows even when two queries are equivalent.
    return any(term not in order_terms for term in projections)


def _is_dinkel_predicate_rewrite_safe(query: str) -> bool:
    return _is_dinkel_safe_read_query(query) and not _has_potential_fetch_limit_order_hazard(query)


def _has_call_subquery_or_union(query: str) -> bool:
    lowered = query.lower()
    return bool(re.search(r"\bcall\s*(?:\([^)]*\)\s*)?\{", lowered) or re.search(r"\bunion\b", lowered))


def _has_dinkel_clause_rewrite_hazard(query: str) -> bool:
    """Avoid regex clause rewrites where Cypher has nested predicate syntax."""
    return bool(
        re.search(r"\[[^\]]*\bWHERE\b[^\]]*\]", query, flags=re.I | re.S)
        or re.search(r"\([^)]*\bWHERE\b[^)]*\)", query, flags=re.I | re.S)
        or re.search(r"\b(?:EXISTS|COUNT|COLLECT)\s*\{", query, flags=re.I)
        or re.search(r"(?:->|<-)\+", query)
    )


_DINKEL_WHERE_STOP = (
    r"WITH|RETURN|MATCH|OPTIONAL\s+MATCH|CALL|UNWIND|ORDER\s+BY|SKIP|OFFSET|LIMIT|"
    r"CREATE|MERGE|SET|DELETE|DETACH\s+DELETE|REMOVE|FOREACH|LOAD\s+CSV|NEXT|FILTER|LET"
)


def _extract_match_path_variables(query: str) -> List[str]:
    return sorted(
        {
            match.group(1)
            for match in re.finditer(r"(?is)\b(?:MATCH|OPTIONAL\s+MATCH)\s+([A-Za-z_]\w*)\s*=", query)
        }
    )


def _rewrite_where_pattern_predicate_to_exists(query: str) -> Tuple[str | None, str | None]:
    if not _is_simple_neo4j_metamorphic_outer_query(query):
        return None, None
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*?\bMATCH\b.*?\s)WHERE\s+(?P<body>\([^)]+\)\s*(?:<-|-)\[[^\]]*\](?:->|-)\s*\([^)]+\))\s+(?P<suffix>RETURN\b[\s\S]*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    lowered = f" {query.lower()} "
    if any(tok in lowered for tok in [" exists ", " and ", " or ", " xor ", " not "]):
        return None, None
    rewritten = f"{m.group('prefix')}WHERE EXISTS {{ MATCH {m.group('body').strip()} }} {m.group('suffix').strip()}"
    return "where_pattern_predicate_to_exists", rewritten.strip()


def _rewrite_where_exists_to_pattern_predicate(query: str) -> Tuple[str | None, str | None]:
    if not _is_simple_neo4j_metamorphic_outer_query(query):
        return None, None
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*?\bMATCH\b.*?\s)WHERE\s+EXISTS\s*\{\s*MATCH\s+(?P<body>\([^)]+\)\s*(?:<-|-)\[[^\]]*\](?:->|-)\s*\([^)]+\))\s*\}\s+(?P<suffix>RETURN\b[\s\S]*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    lowered = f" {query.lower()} "
    if any(tok in lowered for tok in [" and ", " or ", " xor ", " not "]):
        return None, None
    rewritten = f"{m.group('prefix')}WHERE {m.group('body').strip()} {m.group('suffix').strip()}"
    return "where_exists_to_pattern_predicate", rewritten.strip()


def _rewrite_where_exists_with_import_to_plain_exists(query: str) -> Tuple[str | None, str | None]:
    lowered_query = f" {query.lower()} "
    if any(
        token in lowered_query
        for token in [" call ", " union ", " create ", " merge ", " delete ", " set ", " remove ", " load csv ", " foreach ", " unwind ", " optional match ", " with * "]
    ):
        return None, None
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*?\bMATCH\b.*?\s)WHERE\s+EXISTS\s*\{\s*WITH\s+(?P<imports>[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s+MATCH\s+(?P<body>\([^)]+\)\s*(?:<-|-)\[[^\]]*\](?:->|-)\s*\([^)]+\))(?P<where>\s+WHERE\s+[\s\S]+?)?\s+RETURN\s+[\s\S]+?\}\s+(?P<suffix>RETURN\b[\s\S]*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None

    lowered = f" {query.lower()} "
    if any(tok in lowered for tok in [" and ", " or ", " xor ", " not exists {", " count {", " collect {"]):
        return None, None

    where_part = (m.group("where") or "").strip()
    exists_body = f"MATCH {m.group('body').strip()}"
    if where_part:
        exists_body += f" {where_part}"
    rewritten = f"{m.group('prefix')}WHERE EXISTS {{ {exists_body} }} {m.group('suffix').strip()}"
    return "where_exists_with_import_to_plain_exists", rewritten.strip()


def _rewrite_where_exists_to_count_gt_zero(query: str) -> Tuple[str | None, str | None]:
    lowered_query = f" {query.lower()} "
    if any(
        token in lowered_query
        for token in [" call ", " union ", " create ", " merge ", " delete ", " set ", " remove ", " load csv ", " foreach ", " unwind ", " with ", " optional match "]
    ):
        return None, None
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*?\bMATCH\b.*?\s)WHERE\s+EXISTS\s*\{\s*(?P<body>MATCH[\s\S]+?RETURN[\s\S]+?)\s*\}\s+(?P<suffix>RETURN\b[\s\S]*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None

    lowered = f" {query.lower()} "
    if any(tok in lowered for tok in [" and ", " or ", " xor ", " not exists "]):
        return None, None
    if any(tok in m.group("body").lower() for tok in [" with ", " union ", " call ", " optional match "]):
        return None, None

    rewritten = f"{m.group('prefix')}WHERE COUNT {{ {m.group('body').strip()} }} > 0 {m.group('suffix').strip()}"
    return "where_exists_to_count_gt_zero", rewritten.strip()


def _rewrite_where_count_gt_zero_to_exists(query: str) -> Tuple[str | None, str | None]:
    lowered_query = f" {query.lower()} "
    if any(
        token in lowered_query
        for token in [" call ", " union ", " create ", " merge ", " delete ", " set ", " remove ", " load csv ", " foreach ", " unwind ", " with "]
    ):
        return None, None
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*?\bMATCH\b.*?\s)WHERE\s+COUNT\s*\{\s*(?P<body>[\s\S]+?)\s*\}\s*(?P<cmp>>\s*0|>=\s*1)\s+(?P<suffix>RETURN\b[\s\S]*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None

    lowered = f" {query.lower()} "
    if any(tok in lowered for tok in [" and ", " or ", " xor ", " not "]):
        return None, None

    body = m.group("body").strip()
    body_match = re.match(
        r"(?is)^(?:MATCH\s+)?(?P<pattern>\([^)]+\)\s*(?:<-|-)\[[^\]]*\](?:->|-)\s*\([^)]+\))(?P<where>\s+WHERE\s+[\s\S]+?)?(?:\s+RETURN\s+[\s\S]+)?$",
        body,
    )
    if not body_match:
        return None, None

    pattern_text = body_match.group("pattern").strip()
    where_part = (body_match.group("where") or "").strip()
    if any(tok in where_part.lower() for tok in [" count {", " collect {", " exists {", " union ", " call ", " optional match "]):
        return None, None

    exists_body = f"MATCH {pattern_text}"
    if where_part:
        exists_body += f" {where_part}"

    rewritten = f"{m.group('prefix')}WHERE EXISTS {{ {exists_body} }} {m.group('suffix').strip()}"
    return "where_count_gt_zero_to_exists", rewritten.strip()


def _rewrite_where_exists_function_to_exists_subquery(query: str) -> Tuple[str | None, str | None]:
    lowered_query = f" {query.lower()} "
    if any(
        token in lowered_query
        for token in [" call ", " union ", " create ", " merge ", " delete ", " set ", " remove ", " load csv ", " foreach ", " unwind "]
    ):
        return None, None
    located = _find_outer_exists_function_pattern(query)
    if not located:
        return None, None
    exists_start, exists_end, body = located
    prefix = query[:exists_start]
    suffix = query[exists_end + 1 :]
    prefix_match = re.match(r"(?is)^(?P<head>.*?\bMATCH\b[\s\S]*?\bWHERE\s*)$", prefix.strip())
    if not prefix_match:
        return None, None
    if not re.match(r"(?is)^\s*(?:OPTIONAL\s+MATCH|RETURN)\b", suffix):
        return None, None
    lowered = f" {query.lower()} "
    if any(tok in lowered for tok in [" and ", " or ", " xor ", " not "]):
        return None, None
    rewritten = f"{prefix_match.group('head')} EXISTS {{ MATCH {body.strip()} }} {suffix.strip()}"
    return "where_exists_function_to_exists_subquery", rewritten.strip()


def _rewrite_where_exists_function_to_count_gt_zero(query: str) -> Tuple[str | None, str | None]:
    lowered_query = f" {query.lower()} "
    if any(
        token in lowered_query
        for token in [" call ", " union ", " create ", " merge ", " delete ", " set ", " remove ", " load csv ", " foreach ", " unwind "]
    ):
        return None, None
    located = _find_outer_exists_function_pattern(query)
    if not located:
        return None, None
    exists_start, exists_end, body = located
    prefix = query[:exists_start]
    suffix = query[exists_end + 1 :]
    prefix_match = re.match(r"(?is)^(?P<head>.*?\bMATCH\b[\s\S]*?\bWHERE\s*)$", prefix.strip())
    if not prefix_match:
        return None, None
    if not re.match(r"(?is)^\s*(?:OPTIONAL\s+MATCH|RETURN)\b", suffix):
        return None, None
    lowered = f" {query.lower()} "
    if any(tok in lowered for tok in [" and ", " or ", " xor ", " not "]):
        return None, None
    rewritten = f"{prefix_match.group('head')} COUNT {{ {body.strip()} }} > 0 {suffix.strip()}"
    return "where_exists_function_to_count_gt_zero", rewritten.strip()


def _rewrite_where_exists_function_to_pattern_predicate(query: str) -> Tuple[str | None, str | None]:
    lowered_query = f" {query.lower()} "
    if any(
        token in lowered_query
        for token in [" call ", " union ", " create ", " merge ", " delete ", " set ", " remove ", " load csv ", " foreach ", " unwind "]
    ):
        return None, None
    located = _find_outer_exists_function_pattern(query)
    if not located:
        return None, None
    exists_start, exists_end, body = located
    prefix = query[:exists_start]
    suffix = query[exists_end + 1 :]
    prefix_match = re.match(r"(?is)^(?P<head>.*?\bMATCH\b[\s\S]*?\bWHERE\s*)$", prefix.strip())
    if not prefix_match:
        return None, None
    if not re.match(r"(?is)^\s*(?:OPTIONAL\s+MATCH|RETURN)\b", suffix):
        return None, None
    lowered = f" {query.lower()} "
    if any(tok in lowered for tok in [" and ", " or ", " xor ", " not "]):
        return None, None
    rewritten = f"{prefix_match.group('head')}{body.strip()} {suffix.strip()}"
    return "where_exists_function_to_pattern_predicate", rewritten.strip()


def _rewrite_count_pattern_subquery_to_optional_match(query: str) -> Tuple[str | None, str | None]:
    lowered_query = f" {query.lower()} "
    if any(token in lowered_query for token in [" call ", " union ", " create ", " merge ", " delete ", " set ", " remove ", " load csv ", " foreach ", " unwind "]):
        return None, None

    located = _find_outer_braced_subquery(query, "COUNT")
    if not located:
        return None, None
    count_start, count_end, body = located
    if any(token in body.lower() for token in [" match ", " return ", " with ", " union ", " call ", " when "]):
        return None, None

    before = query[:count_start]
    after = query[count_end + 1 :]
    before_lowered = before.lower()
    if len(re.findall(r"\breturn\b", before_lowered)) != 1:
        return None, None
    if any(token in before_lowered for token in [" optional match ", " with ", " collect {"]):
        return None, None
    before_match = re.match(
        r"(?is)^(?P<prefix>.*)\bRETURN\s+(?P<left_expr>[^,]+?)\s+AS\s+(?P<left_alias>[A-Za-z_]\w+)\s*,\s*$",
        before.strip(),
    )
    after_match = re.match(r"(?is)^\s*AS\s+(?P<count_alias>[A-Za-z_]\w+)(?P<tail>[\s\S]*)$", after)
    if not before_match or not after_match:
        return None, None

    body_match = re.match(
        r"(?is)^(?P<pattern>\([^)]+\)\s*(?:<-|-)\[[^\]]*\](?:->|-)\s*\([^)]+\))(?P<where>\s+WHERE\s+[\s\S]+)?$",
        body,
    )
    if not body_match:
        return None, None

    rewritten_pattern, rel_var = _inject_relationship_variable(
        body_match.group("pattern").strip(),
        f"__count_rel_{after_match.group('count_alias')}",
    )
    if not rewritten_pattern or not rel_var:
        return None, None

    prefix = before_match.group("prefix").rstrip()
    left_expr = before_match.group("left_expr").strip()
    left_alias = before_match.group("left_alias")
    count_alias = after_match.group("count_alias")
    tail = after_match.group("tail") or ""
    left_base_var_match = re.match(r"(?is)^([A-Za-z_]\w*)(?:\.[A-Za-z_]\w+)?$", left_expr)
    left_base_var = left_base_var_match.group(1) if left_base_var_match else None
    if re.match(r"(?is)^\s*,", tail) and not left_base_var:
        return None, None
    where_part = body_match.group("where") or ""

    if left_base_var:
        rewritten = (
            f"{prefix} OPTIONAL MATCH {rewritten_pattern}{where_part} "
            f"WITH {left_base_var}, {left_expr} AS {left_alias}, COUNT({rel_var}) AS {count_alias} "
            f"RETURN {left_alias}, {count_alias}{tail}"
        )
    else:
        rewritten = (
            f"{prefix} OPTIONAL MATCH {rewritten_pattern}{where_part} "
            f"WITH {left_expr} AS {left_alias}, COUNT({rel_var}) AS {count_alias} "
            f"RETURN {left_alias}, {count_alias}{tail}"
        )
    return "count_pattern_subquery_to_optional_match", rewritten.strip()


def _rewrite_collect_subquery_to_optional_match(query: str) -> Tuple[str | None, str | None]:
    lowered_query = f" {query.lower()} "
    if any(token in lowered_query for token in [" call ", " union ", " create ", " merge ", " delete ", " set ", " remove ", " load csv ", " foreach ", " unwind "]):
        return None, None

    located = _find_outer_braced_subquery(query, "COLLECT")
    if not located:
        return None, None
    collect_start, collect_end, body = located
    if any(token in body.lower() for token in [" with ", " union ", " call ", " optional match "]):
        return None, None

    before = query[:collect_start]
    after = query[collect_end + 1 :]
    before_match = re.match(
        r"(?is)^(?P<prefix>.*)\bRETURN\s+(?P<left_expr>[^,]+?)\s+AS\s+(?P<left_alias>[A-Za-z_]\w+)\s*,\s*$",
        before.strip(),
    )
    after_match = re.match(r"(?is)^\s*AS\s+(?P<collect_alias>[A-Za-z_]\w+)(?P<tail>[\s\S]*)$", after)
    if not before_match or not after_match:
        return None, None

    body_match = re.match(
        r"(?is)^MATCH\s+(?P<pattern>\([^)]+\)\s*(?:<-|-)\[[^\]]*\](?:->|-)\s*\([^)]+\))(?P<where>\s+WHERE\s+[\s\S]+?)?\s+RETURN\s+(?P<expr>[^,]+?)\s*$",
        body,
    )
    if not body_match:
        return None, None

    collect_expr = body_match.group("expr").strip()
    if any(token in collect_expr.lower() for token in [" count(", " collect(", " exists(", " case ", "{", "}"]):
        return None, None

    prefix = before_match.group("prefix").rstrip()
    left_expr = before_match.group("left_expr").strip()
    left_alias = before_match.group("left_alias")
    collect_alias = after_match.group("collect_alias")
    tail = after_match.group("tail") or ""
    if re.match(r"(?is)^\s*,", tail):
        return None, None
    where_part = body_match.group("where") or ""
    pattern_text = body_match.group("pattern").strip()

    rewritten = (
        f"{prefix} OPTIONAL MATCH {pattern_text}{where_part} "
        f"WITH {left_expr} AS {left_alias}, COLLECT({collect_expr}) AS {collect_alias} "
        f"RETURN {left_alias}, {collect_alias}{tail}"
    )
    return "collect_subquery_to_optional_match", rewritten.strip()


def _rewrite_return_exists_to_optional_match_bool(query: str) -> Tuple[str | None, str | None]:
    lowered_query = f" {query.lower()} "
    if any(
        token in lowered_query
        for token in [" call ", " union ", " create ", " merge ", " delete ", " set ", " remove ", " load csv ", " foreach ", " unwind ", " with ", " optional match "]
    ):
        return None, None

    located = _find_outer_braced_subquery(query, "EXISTS")
    if not located:
        return None, None
    exists_start, exists_end, body = located
    if any(token in body.lower() for token in [" optional match ", " with ", " union ", " call "]):
        return None, None

    before = query[:exists_start]
    after = query[exists_end + 1 :]
    before_match = re.match(
        r"(?is)^(?P<prefix>.*)\bRETURN\s+(?P<left_expr>[^,]+?)\s+AS\s+(?P<left_alias>[A-Za-z_]\w+)\s*,\s*$",
        before.strip(),
    )
    after_match = re.match(r"(?is)^\s*AS\s+(?P<exists_alias>[A-Za-z_]\w+)(?P<tail>[\s\S]*)$", after)
    if not before_match or not after_match:
        return None, None

    body_match = re.match(
        r"(?is)^MATCH\s+(?P<pattern>\([^)]+\)\s*(?:<-|-)\[[^\]]*\](?:->|-)\s*\([^)]+\))(?P<where>\s+WHERE\s+[\s\S]+?)?(?:\s+RETURN\s+[\s\S]+)?$",
        body.strip(),
    )
    if not body_match:
        return None, None

    rewritten_pattern, rel_var = _inject_relationship_variable(
        body_match.group("pattern").strip(),
        f"__exists_rel_{after_match.group('exists_alias')}",
    )
    if not rewritten_pattern or not rel_var:
        return None, None

    prefix = before_match.group("prefix").rstrip()
    left_expr = before_match.group("left_expr").strip()
    left_alias = before_match.group("left_alias")
    exists_alias = after_match.group("exists_alias")
    tail = after_match.group("tail") or ""
    if re.match(r"(?is)^\s*,", tail):
        return None, None
    where_part = body_match.group("where") or ""

    rewritten = (
        f"{prefix} OPTIONAL MATCH {rewritten_pattern}{where_part} "
        f"WITH {left_expr} AS {left_alias}, COUNT({rel_var}) > 0 AS {exists_alias} "
        f"RETURN {left_alias}, {exists_alias}{tail}"
    )
    return "return_exists_to_optional_match_bool", rewritten.strip()


def _rewrite_return_exists_to_count_gt_zero_bool(query: str) -> Tuple[str | None, str | None]:
    lowered_query = f" {query.lower()} "
    if any(
        token in lowered_query
        for token in [" call ", " union ", " create ", " merge ", " delete ", " set ", " remove ", " load csv ", " foreach ", " unwind ", " with ", " optional match "]
    ):
        return None, None

    located = _find_outer_braced_subquery(query, "EXISTS")
    if not located:
        return None, None
    exists_start, exists_end, body = located
    if any(token in body.lower() for token in [" with ", " union ", " call ", " optional match "]):
        return None, None

    before = query[:exists_start]
    after = query[exists_end + 1 :]
    before_match = re.match(
        r"(?is)^(?P<prefix>.*)\bRETURN\s+(?P<left_expr>[^,]+?)\s+AS\s+(?P<left_alias>[A-Za-z_]\w+)\s*,\s*$",
        before.strip(),
    )
    after_match = re.match(r"(?is)^\s*AS\s+(?P<exists_alias>[A-Za-z_]\w+)(?P<tail>[\s\S]*)$", after)
    if not before_match or not after_match:
        return None, None
    body_stripped = body.strip()
    if not re.match(r"(?is)^MATCH[\s\S]+RETURN[\s\S]+$", body_stripped):
        return None, None
    tail = after_match.group("tail") or ""
    if re.match(r"(?is)^\s*,", tail):
        return None, None

    rewritten = (
        f"{before_match.group('prefix').rstrip()} "
        f"RETURN {before_match.group('left_expr').strip()} AS {before_match.group('left_alias')}, "
        f"COUNT {{ {body_stripped} }} > 0 AS {after_match.group('exists_alias')}{tail}"
    )
    return "return_exists_to_count_gt_zero_bool", rewritten.strip()


def _rewrite_return_exists_function_to_exists_subquery(query: str) -> Tuple[str | None, str | None]:
    lowered_query = f" {query.lower()} "
    if any(
        token in lowered_query
        for token in [" call ", " union ", " create ", " merge ", " delete ", " set ", " remove ", " load csv ", " foreach ", " unwind "]
    ):
        return None, None
    pattern = re.compile(
        r"(?is)^(?P<head>.*)\bRETURN\s+(?P<items>[\s\S]+?)(?P<tail>\s+ORDER\s+BY[\s\S]*|\s+SKIP\s+[\s\S]*|\s+LIMIT\s+[\s\S]*|\s*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    items = _split_top_level_csv(m.group("items").strip())
    if not items:
        return None, None

    rewritten_items: List[str] = []
    changed = False
    for item in items:
        located = _find_outer_exists_function_pattern(item)
        if not located:
            rewritten_items.append(item)
            continue
        exists_start, exists_end, body = located
        prefix = item[:exists_start].strip()
        suffix = item[exists_end + 1 :]
        if prefix:
            rewritten_items.append(item)
            continue
        suffix_match = re.match(r"(?is)^\s*AS\s+(?P<exists_alias>[A-Za-z_]\w+)\s*$", suffix)
        if not suffix_match:
            rewritten_items.append(item)
            continue
        rewritten_items.append(f"EXISTS {{ MATCH {body.strip()} }} AS {suffix_match.group('exists_alias')}")
        changed = True

    if not changed:
        return None, None
    rewritten = f"{m.group('head').rstrip()} RETURN {', '.join(rewritten_items)}{m.group('tail')}"
    return "return_exists_function_to_exists_subquery", rewritten.strip()


def _rewrite_return_exists_function_to_count_gt_zero_bool(query: str) -> Tuple[str | None, str | None]:
    lowered_query = f" {query.lower()} "
    if any(
        token in lowered_query
        for token in [" call ", " union ", " create ", " merge ", " delete ", " set ", " remove ", " load csv ", " foreach ", " unwind "]
    ):
        return None, None
    pattern = re.compile(
        r"(?is)^(?P<head>.*)\bRETURN\s+(?P<items>[\s\S]+?)(?P<tail>\s+ORDER\s+BY[\s\S]*|\s+SKIP\s+[\s\S]*|\s+LIMIT\s+[\s\S]*|\s*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    items = _split_top_level_csv(m.group("items").strip())
    if not items:
        return None, None

    rewritten_items: List[str] = []
    changed = False
    for item in items:
        located = _find_outer_exists_function_pattern(item)
        if not located:
            rewritten_items.append(item)
            continue
        exists_start, exists_end, body = located
        prefix = item[:exists_start].strip()
        suffix = item[exists_end + 1 :]
        if prefix:
            rewritten_items.append(item)
            continue
        suffix_match = re.match(r"(?is)^\s*AS\s+(?P<exists_alias>[A-Za-z_]\w+)\s*$", suffix)
        if not suffix_match:
            rewritten_items.append(item)
            continue
        rewritten_items.append(f"COUNT {{ {body.strip()} }} > 0 AS {suffix_match.group('exists_alias')}")
        changed = True

    if not changed:
        return None, None
    rewritten = f"{m.group('head').rstrip()} RETURN {', '.join(rewritten_items)}{m.group('tail')}"
    return "return_exists_function_to_count_gt_zero_bool", rewritten.strip()


def _rewrite_where_any_membership_to_in(query: str) -> Tuple[str | None, str | None]:
    if not _is_simple_neo4j_metamorphic_outer_query(query):
        return None, None
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*?\bMATCH\b.*?\s)WHERE\s+(?P<neg>NOT\s+)?any\s*\(\s*(?P<var>[A-Za-z_]\w*)\s+IN\s+(?P<list_expr>[^\)]*?)\s+WHERE\s+(?P=var)\s*=\s*(?P<expr>[^)]+?)\s*\)\s+(?P<suffix>RETURN\b[\s\S]*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    list_expr = m.group("list_expr").strip()
    expr = m.group("expr").strip()
    if any(tok in list_expr.lower() for tok in [" any(", " all(", " none(", " collect(", " count(", " exists("]):
        return None, None
    if any(tok in expr.lower() for tok in [" any(", " all(", " none(", " collect(", " count(", " exists("]):
        return None, None
    neg = "NOT " if m.group("neg") else ""
    rewritten = f"{m.group('prefix')}WHERE {neg}{expr} IN {list_expr} {m.group('suffix').strip()}"
    return "where_any_membership_to_in", rewritten.strip()


def _rewrite_where_none_to_not_any(query: str) -> Tuple[str | None, str | None]:
    if not _is_simple_neo4j_metamorphic_outer_query(query):
        return None, None
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*?\bMATCH\b.*?\s)WHERE\s+none\s*\(\s*(?P<var>[A-Za-z_]\w*)\s+IN\s+(?P<list_expr>[^)]*?)\s+WHERE\s+(?P<pred>[^)]+?)\s*\)\s+(?P<suffix>RETURN\b[\s\S]*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    rewritten = (
        f"{m.group('prefix')}WHERE NOT any({m.group('var')} IN {m.group('list_expr').strip()} "
        f"WHERE {m.group('pred').strip()}) {m.group('suffix').strip()}"
    )
    return "where_none_to_not_any", rewritten.strip()


def _rewrite_call_return_only_to_with(query: str) -> Tuple[str | None, str | None]:
    lowered = f" {query.lower()} "
    if len(re.findall(r"\bcall\b", lowered)) != 1:
        return None, None
    if any(
        token in lowered
        for token in [
            " union ",
            " unwind ",
            " in transactions ",
            " load csv ",
            " foreach ",
            " optional call ",
        ]
    ):
        return None, None
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*?\b)(?:CALL)\s*\{\s*WITH\s+(?P<vars>[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s+RETURN\s+(?P<items>[^{}]+?)\s*\}\s*(?P<suffix>(?:WITH|RETURN|MATCH|OPTIONAL\s+MATCH|DELETE|SET|MERGE|REMOVE|CALL)\b[\s\S]*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    items = m.group("items").strip()
    if "*" in items:
        return None, None
    if any(token in items.lower() for token in [" collect(", " collect {", " count {", " exists {"]):
        return None, None
    rewritten = f"{m.group('prefix').rstrip()} WITH *, {items} {m.group('suffix').lstrip()}"
    return "call_return_only_to_with", rewritten.strip()


def _rewrite_cypher25_scoped_call_return_only_to_with(query: str) -> Tuple[str | None, str | None]:
    lowered = f" {query.lower()} "
    if len(re.findall(r"\bcall\b", lowered)) != 1:
        return None, None
    if any(
        token in lowered
        for token in [
            " union ",
            " in transactions ",
            " load csv ",
            " foreach ",
            " optional call ",
        ]
    ):
        return None, None
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*?)\bCALL\s*\(\s*(?P<vars>[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)?\s*\)\s*\{\s*RETURN\s+(?P<items>[^{}]+?)\s*\}\s*(?P<suffix>(?:WITH|RETURN|MATCH|OPTIONAL\s+MATCH|DELETE|SET|MERGE|REMOVE|CALL)\b[\s\S]*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    items = m.group("items").strip()
    if "*" in items:
        return None, None
    if any(token in items.lower() for token in [" collect(", " collect {", " count {", " exists {"]):
        return None, None
    rewritten = f"{m.group('prefix').rstrip()} WITH *, {items} {m.group('suffix').lstrip()}"
    return "cypher25_scoped_call_return_only_to_with", rewritten.strip()


def _rewrite_cypher25_let_to_with_star(query: str) -> Tuple[str | None, str | None]:
    if re.search(r"\bLET\b[\s\S]{0,120},", query, flags=re.I):
        return None, None
    pattern = re.compile(
        r"(?is)\bLET\s+(?P<alias>[A-Za-z_]\w*)\s*=\s*(?P<expr>.*?)(?P<next>\s+(?:MATCH|OPTIONAL\s+MATCH|WITH|RETURN|CALL|UNWIND|FILTER|NEXT|ORDER\s+BY|LIMIT|SKIP|OFFSET)\b)"
    )
    m = pattern.search(query)
    if not m:
        return None, None
    expr = m.group("expr").strip()
    alias = m.group("alias")
    if not expr or re.search(r"\b(?:CREATE|MERGE|DELETE|SET|REMOVE)\b", expr, flags=re.I):
        return None, None
    rewritten = query[: m.start()] + f"WITH *, {expr} AS {alias}{m.group('next')}" + query[m.end() :]
    return "cypher25_let_to_with_star", rewritten.strip()


def _rewrite_cypher25_filter_to_with_where(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)\bFILTER\s+(?P<cond>.*?)(?P<next>\s+(?:MATCH|OPTIONAL\s+MATCH|WITH|RETURN|CALL|UNWIND|LET|NEXT|ORDER\s+BY|LIMIT|SKIP|OFFSET)\b)"
    )
    m = pattern.search(query)
    if not m:
        return None, None
    cond = m.group("cond").strip()
    if not cond or re.search(r"\b(?:CREATE|MERGE|DELETE|SET|REMOVE)\b", cond, flags=re.I):
        return None, None
    rewritten = query[: m.start()] + f"WITH * WHERE {cond}{m.group('next')}" + query[m.end() :]
    return "cypher25_filter_to_with_where", rewritten.strip()


def _rewrite_cypher25_with_star_where_to_filter(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)\bWITH\s+\*\s+WHERE\s+(?P<cond>.*?)(?P<next>\s+(?:MATCH|OPTIONAL\s+MATCH|WITH|RETURN|CALL|UNWIND|LET|NEXT|ORDER\s+BY|LIMIT|SKIP|OFFSET)\b)"
    )
    m = pattern.search(query)
    if not m:
        return None, None
    cond = m.group("cond").strip()
    if not cond or re.search(r"\b(?:CREATE|MERGE|DELETE|SET|REMOVE)\b", cond, flags=re.I):
        return None, None
    rewritten = query[: m.start()] + f"FILTER {cond}{m.group('next')}" + query[m.end() :]
    return "cypher25_with_star_where_to_filter", rewritten.strip()


def _rewrite_cypher25_with_star_projection_to_let(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)\bWITH\s+\*\s*,\s*(?P<expr>.*?)\s+AS\s+(?P<alias>[A-Za-z_]\w*)"
        r"(?P<next>\s+(?:MATCH|OPTIONAL\s+MATCH|WITH|RETURN|CALL|UNWIND|FILTER|NEXT|ORDER\s+BY|LIMIT|SKIP|OFFSET)\b)"
    )
    m = pattern.search(query)
    if not m:
        return None, None
    expr = m.group("expr").strip()
    alias = m.group("alias")
    if not expr or re.search(r"\b(?:CREATE|MERGE|DELETE|SET|REMOVE|DISTINCT)\b", expr, flags=re.I):
        return None, None
    if any(token in expr.lower() for token in [" collect(", " count {", " collect {", " exists {"]):
        return None, None
    rewritten = query[: m.start()] + f"LET {alias} = {expr}{m.group('next')}" + query[m.end() :]
    return "cypher25_with_star_projection_to_let", rewritten.strip()


_TOP_LEVEL_CYPHER_KEYWORD_RE = re.compile(
    r"(?is)\b("
    r"OPTIONAL\s+MATCH|ORDER\s+BY|LOAD\s+CSV|DETACH\s+DELETE|"
    r"MATCH|WHERE|WITH|RETURN|CALL|UNWIND|LET|NEXT|FILTER|"
    r"CREATE|MERGE|SET|DELETE|REMOVE|FOREACH|FINISH|LIMIT|SKIP|OFFSET"
    r")\b"
)


def _top_level_cypher_keywords(query: str) -> List[Tuple[int, int, str]]:
    keywords: List[Tuple[int, int, str]] = []
    paren = bracket = brace = 0
    quote: str | None = None
    i = 0
    while i < len(query):
        ch = query[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue

        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch == "(":
            paren += 1
            i += 1
            continue
        if ch == ")":
            paren = max(paren - 1, 0)
            i += 1
            continue
        if ch == "[":
            bracket += 1
            i += 1
            continue
        if ch == "]":
            bracket = max(bracket - 1, 0)
            i += 1
            continue
        if ch == "{":
            brace += 1
            i += 1
            continue
        if ch == "}":
            brace = max(brace - 1, 0)
            i += 1
            continue

        if paren == 0 and bracket == 0 and brace == 0:
            m = _TOP_LEVEL_CYPHER_KEYWORD_RE.match(query, i)
            if m:
                keyword = re.sub(r"\s+", " ", m.group(1).upper())
                if keyword == "WITH":
                    previous_text = query[max(0, m.start() - 32) : m.start()]
                    if re.search(r"(?is)\b(?:STARTS|ENDS)\s+$", previous_text):
                        i = m.end()
                        continue
                keywords.append((m.start(), m.end(), keyword))
                i = m.end()
                continue

        i += 1
    return keywords


def _rewrite_cypher25_match_where_to_filter(query: str) -> Tuple[str | None, str | None]:
    # Only rewrite WHERE attached to a regular MATCH. WHERE after OPTIONAL MATCH
    # is not equivalent to a standalone FILTER because OPTIONAL row preservation
    # would change. Also avoid pattern-internal predicates such as
    # `(n WHERE ...)` and `[r:REL WHERE ...]`, which are not clause WHEREs.
    keywords = _top_level_cypher_keywords(query)
    if len(keywords) < 3:
        return None, None

    for idx, (_, _, keyword) in enumerate(keywords[:-2]):
        if keyword != "MATCH":
            continue
        where_start, where_end, where_keyword = keywords[idx + 1]
        if where_keyword != "WHERE":
            continue
        next_start, _, next_keyword = keywords[idx + 2]
        if next_keyword == "WHERE":
            continue

        cond = query[where_end:next_start].strip()
        if not cond or re.search(r"\b(?:CREATE|MERGE|DELETE|SET|REMOVE)\b", cond, flags=re.I):
            return None, None
        rewritten = f"{query[:where_start].rstrip()} FILTER {cond} {query[next_start:].lstrip()}"
        return "cypher25_match_where_to_filter", rewritten.strip()

    return None, None


def _rewrite_cypher25_return_all_identity(query: str) -> Tuple[str | None, str | None]:
    if re.search(r"\bRETURN\s+(?:DISTINCT|ALL)\b", query, flags=re.I):
        return None, None
    if _has_potential_fetch_limit_order_hazard(query):
        return None, None
    keywords = _top_level_cypher_keywords(query)
    return_positions = [item for item in keywords if item[2] == "RETURN"]
    if not return_positions:
        return None, None
    start, end, _ = return_positions[-1]
    body = query[end:].strip()
    if not body or body.startswith("*"):
        return None, None
    if re.search(r"\b(?:ORDER\s+BY|LIMIT|SKIP|OFFSET|UNION)\b", body, flags=re.I):
        return None, None
    rewritten = f"{query[:start]}RETURN ALL {query[end:].lstrip()}"
    return "cypher25_return_all_identity", rewritten.strip()


def _rewrite_cypher25_with_all_identity(query: str) -> Tuple[str | None, str | None]:
    if re.search(r"\bWITH\s+(?:DISTINCT|ALL)\b", query, flags=re.I):
        return None, None
    keywords = _top_level_cypher_keywords(query)
    for idx, (start, end, keyword) in enumerate(keywords[:-1]):
        if keyword != "WITH":
            continue
        next_start, _, next_keyword = keywords[idx + 1]
        if next_keyword in {"ORDER BY", "LIMIT", "SKIP", "OFFSET", "WHERE"}:
            continue
        body = query[end:next_start].strip()
        if not body or body.startswith("*"):
            continue
        if re.search(r"\b(?:DISTINCT|ALL)\b", body, flags=re.I):
            continue
        rewritten = f"{query[:start]}WITH ALL {body} {query[next_start:].lstrip()}"
        return "cypher25_with_all_identity", rewritten.strip()
    return None, None


def _rewrite_cypher25_return_next_identity(query: str) -> Tuple[str | None, str | None]:
    if _has_potential_fetch_limit_order_hazard(query):
        return None, None
    if re.search(r"\bNEXT\b", query, flags=re.I):
        return None, None
    m = _final_return_match(query)
    if not m:
        return None, None
    body = m.group("body").strip()
    tail = (m.group("tail") or "").strip()
    if tail:
        return None, None
    parts = _split_top_level_csv(body)
    aliases: List[str] = []
    for item in parts:
        expr, alias = _split_return_alias(item)
        if not expr or not alias:
            simple = re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?", item.strip())
            if not simple:
                return None, None
            alias = item.strip()
        if alias == "*" or "." in alias:
            return None, None
        aliases.append(alias)
    if not aliases:
        return None, None
    rewritten = f"{query.strip()} NEXT RETURN {', '.join(aliases)}"
    return "cypher25_return_next_identity", rewritten.strip()


def _rewrite_cypher25_predicate_partitioning(query: str) -> Tuple[str | None, str | None]:
    if not _is_dinkel_safe_read_query(query):
        return None, None
    lowered = f" {query.lower()} "
    if any(token in lowered for token in [" optional match ", " with ", " call ", " unwind ", " union ", " order by "]):
        return None, None
    if re.search(r"\b(?:count|collect|min|max|sum|avg)\s*\(", query, flags=re.I):
        return None, None
    if re.search(r"\b(?:COUNT|COLLECT|EXISTS)\s*\{", query, flags=re.I):
        return None, None

    keywords = _top_level_cypher_keywords(query)
    if len(keywords) != 3:
        return None, None
    if [item[2] for item in keywords] != ["MATCH", "WHERE", "RETURN"]:
        return None, None

    where_start, where_end, _ = keywords[1]
    return_start, _, _ = keywords[2]
    match_part = query[:where_start].strip()
    cond = query[where_end:return_start].strip()
    return_part = query[return_start:].strip()
    if not cond or not match_part or not return_part:
        return None, None
    if any(token in cond.lower() for token in [" rand(", " randomuuid(", " timestamp("]):
        return None, None

    rewritten = (
        f"{match_part} WHERE ({cond}) {return_part} "
        f"UNION ALL {match_part} WHERE NOT ({cond}) {return_part} "
        f"UNION ALL {match_part} WHERE ({cond}) IS NULL {return_part}"
    )
    return "cypher25_predicate_partitioning", rewritten.strip()


def _rewrite_cypher25_conditional_branch_swap_case_guard(query: str) -> Tuple[str | None, str | None]:
    if _has_nondeterministic_selfdiff_functions(query) or _has_cypher_comment(query):
        return None, None
    if re.search(r"\b(?:CREATE|MERGE|DELETE|DETACH\s+DELETE|SET|REMOVE|FOREACH|LOAD\s+CSV)\b", query, flags=re.I):
        return None, None
    # Shape: WHEN <cond> THEN RETURN <a> ELSE RETURN <b>.  The CASE guard makes
    # false and null both route to the original ELSE branch, preserving Cypher's
    # conditional-query truthiness instead of relying on plain NOT <cond>.
    pattern = re.compile(
        r"(?is)\bWHEN\s+(?P<cond>.*?)\s+THEN\s+RETURN\s+(?P<then_items>.*?)\s+ELSE\s+RETURN\s+(?P<else_items>.*?)(?P<next>\s+(?:NEXT|RETURN|WITH|FILTER|LET|MATCH|OPTIONAL\s+MATCH|CALL)\b)"
    )
    m = pattern.search(query)
    if not m:
        return None, None
    cond = m.group("cond").strip()
    then_items = m.group("then_items").strip()
    else_items = m.group("else_items").strip()
    if not cond or not then_items or not else_items:
        return None, None
    unsafe = r"\b(?:WHEN|THEN|ELSE|UNION|CALL\s*\{|COUNT\s*\{|COLLECT\s*\{|EXISTS\s*\{)\b"
    if re.search(unsafe, cond, flags=re.I) or re.search(unsafe, then_items, flags=re.I) or re.search(unsafe, else_items, flags=re.I):
        return None, None
    replacement = (
        f"WHEN CASE WHEN ({cond}) THEN false ELSE true END "
        f"THEN RETURN {else_items} ELSE RETURN {then_items}{m.group('next')}"
    )
    rewritten = query[: m.start()] + replacement + query[m.end() :]
    return "cypher25_conditional_branch_swap_case_guard", rewritten.strip()


def _rewrite_return_size_pattern_comprehension_to_count(query: str) -> Tuple[str | None, str | None]:
    lowered_query = f" {query.lower()} "
    if any(
        token in lowered_query
        for token in [" call ", " union ", " create ", " merge ", " delete ", " set ", " remove ", " load csv ", " foreach ", " unwind "]
    ):
        return None, None
    pattern = re.compile(
        r"(?is)^(?P<head>.*)\bRETURN\s+size\s*\(\s*\[\s*(?P<pattern>\([^)]+\)\s*(?:<-|-)\[[^\]]*\](?:->|-)\s*\([^)]+\))(?P<where>\s+WHERE\s+[^|\]]+?)?\s*\|\s*(?P<proj>[^\]]+?)\s*\]\s*\)\s+AS\s+(?P<alias>[A-Za-z_]\w+)(?P<tail>\s+ORDER\s+BY[\s\S]*|\s+LIMIT\s+[\s\S]*|\s*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    rewritten = (
        f"{m.group('head').rstrip()} RETURN COUNT {{ {m.group('pattern').strip()}"
        f"{m.group('where') or ''} }} AS {m.group('alias')}{m.group('tail')}"
    )
    return "return_size_pattern_comprehension_to_count", rewritten.strip()


def _rewrite_return_size_pattern_comprehension_gt_zero_to_exists(query: str) -> Tuple[str | None, str | None]:
    lowered_query = f" {query.lower()} "
    if any(
        token in lowered_query
        for token in [" call ", " union ", " create ", " merge ", " delete ", " set ", " remove ", " load csv ", " foreach ", " unwind "]
    ):
        return None, None
    pattern = re.compile(
        r"(?is)^(?P<head>.*)\bRETURN\s+size\s*\(\s*\[\s*(?P<pattern>\([^)]+\)\s*(?:<-|-)\[[^\]]*\](?:->|-)\s*\([^)]+\))(?P<where>\s+WHERE\s+[^|\]]+?)?\s*\|\s*(?P<proj>[^\]]+?)\s*\]\s*\)\s*>\s*0\s+AS\s+(?P<alias>[A-Za-z_]\w+)(?P<tail>\s+ORDER\s+BY[\s\S]*|\s+LIMIT\s+[\s\S]*|\s*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    exists_body = f"MATCH {m.group('pattern').strip()}{m.group('where') or ''}"
    rewritten = f"{m.group('head').rstrip()} RETURN EXISTS {{ {exists_body} }} AS {m.group('alias')}{m.group('tail')}"
    return "return_size_pattern_comprehension_gt_zero_to_exists", rewritten.strip()


def _rewrite_return_case_null_to_coalesce(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)^(?P<head>.*)\bRETURN\s+(?P<items>[\s\S]+?)(?P<tail>\s+ORDER\s+BY[\s\S]*|\s+SKIP\s+[\s\S]*|\s+LIMIT\s+[\s\S]*|\s*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    items = _split_top_level_csv(m.group("items").strip())
    if not items:
        return None, None

    rewritten_items: List[str] = []
    changed = False
    for item in items:
        mm = re.match(
            r"(?is)^CASE\s+WHEN\s+(?P<expr>.+?)\s+IS\s+NULL\s+THEN\s+(?P<alt>.+?)\s+ELSE\s+(?P<expr2>.+?)\s+END\s+AS\s+(?P<alias>[A-Za-z_]\w*)$",
            item.strip(),
        )
        if not mm:
            rewritten_items.append(item)
            continue
        expr = mm.group("expr").strip()
        expr2 = mm.group("expr2").strip()
        if expr != expr2:
            rewritten_items.append(item)
            continue
        rewritten_items.append(f"COALESCE({expr}, {mm.group('alt').strip()}) AS {mm.group('alias')}")
        changed = True

    if not changed:
        return None, None
    rewritten = f"{m.group('head').rstrip()} RETURN {', '.join(rewritten_items)}{m.group('tail')}"
    return "return_case_null_to_coalesce", rewritten.strip()


def _rewrite_return_coalesce_to_case_null(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)^(?P<head>.*)\bRETURN\s+(?P<items>[\s\S]+?)(?P<tail>\s+ORDER\s+BY[\s\S]*|\s+SKIP\s+[\s\S]*|\s+LIMIT\s+[\s\S]*|\s*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    items = _split_top_level_csv(m.group("items").strip())
    if not items:
        return None, None

    rewritten_items: List[str] = []
    changed = False
    for item in items:
        mm = re.match(r"(?is)^COALESCE\s*\((?P<args>.+)\)\s+AS\s+(?P<alias>[A-Za-z_]\w*)$", item.strip())
        if not mm:
            rewritten_items.append(item)
            continue
        args = _split_top_level_csv(mm.group("args").strip())
        if len(args) != 2:
            rewritten_items.append(item)
            continue
        expr, alt = args[0].strip(), args[1].strip()
        rewritten_items.append(f"CASE WHEN {expr} IS NULL THEN {alt} ELSE {expr} END AS {mm.group('alias')}")
        changed = True

    if not changed:
        return None, None
    rewritten = f"{m.group('head').rstrip()} RETURN {', '.join(rewritten_items)}{m.group('tail')}"
    return "return_coalesce_to_case_null", rewritten.strip()


def _rewrite_relationship_comprehension_size_to_length(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(r"(?is)size\s*\(\s*\[\s*r\s+IN\s+relationships\s*\(\s*([A-Za-z_]\w*)\s*\)\s*\|\s*r\s*\]\s*\)")
    if not pattern.search(query):
        return None, None
    rewritten = pattern.sub(lambda m: f"length({m.group(1)})", query)
    if rewritten == query:
        return None, None
    return "relationship_comprehension_size_to_length", rewritten.strip()


def _rewrite_relationships_size_to_length(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(r"(?is)size\s*\(\s*relationships\s*\(\s*([A-Za-z_]\w*)\s*\)\s*\)")
    if not pattern.search(query):
        return None, None
    rewritten = pattern.sub(lambda m: f"length({m.group(1)})", query)
    if rewritten == query:
        return None, None
    return "relationships_size_to_length", rewritten.strip()


def _rewrite_nodes_size_to_length_plus_one(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(r"(?is)size\s*\(\s*nodes\s*\(\s*([A-Za-z_]\w*)\s*\)\s*\)")
    if not pattern.search(query):
        return None, None
    rewritten = pattern.sub(lambda m: f"(length({m.group(1)}) + 1)", query)
    if rewritten == query:
        return None, None
    return "nodes_size_to_length_plus_one", rewritten.strip()


def _rewrite_length_path_to_relationships_size(query: str) -> Tuple[str | None, str | None]:
    path_vars = _extract_match_path_variables(query)
    if not path_vars:
        return None, None

    rewritten = query
    changed = False
    for var in path_vars:
        pattern = re.compile(rf"(?is)\blength\s*\(\s*{re.escape(var)}\s*\)")
        updated = pattern.sub(f"size(relationships({var}))", rewritten)
        if updated != rewritten:
            changed = True
            rewritten = updated

    if not changed or rewritten == query:
        return None, None
    return "length_path_to_relationships_size", rewritten.strip()


def _rewrite_optional_match_count_return_to_with(query: str) -> Tuple[str | None, str | None]:
    if not _is_simple_neo4j_metamorphic_outer_query(query):
        return None, None
    lowered = f" {query.lower()} "
    if " optional match " not in lowered:
        return None, None
    if " with " in lowered:
        return None, None
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*?\bOPTIONAL\s+MATCH\b.*)\bRETURN\s+(?P<expr1>[^,]+?)\s+AS\s+(?P<a1>[A-Za-z_]\w+)\s*,\s*(?P<agg>COUNT\s*\(\s*(?:DISTINCT\s+)?[^)]+\))\s+AS\s+(?P<a2>[A-Za-z_]\w+)(?P<tail>\s+ORDER\s+BY[\s\S]*|\s+LIMIT\s+\d+\s*|\s*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    rewritten = (
        f"{m.group('prefix').rstrip()} "
        f"WITH {m.group('expr1').strip()} AS {m.group('a1')}, {m.group('agg').strip()} AS {m.group('a2')} "
        f"RETURN {m.group('a1')}, {m.group('a2')}{m.group('tail')}"
    )
    return "optional_match_count_return_to_with", rewritten.strip()


def _rewrite_optional_match_collect_return_to_with(query: str) -> Tuple[str | None, str | None]:
    if not _is_simple_neo4j_metamorphic_outer_query(query):
        return None, None
    lowered = f" {query.lower()} "
    if " optional match " not in lowered:
        return None, None
    if " with " in lowered:
        return None, None
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*?\bOPTIONAL\s+MATCH\b.*)\bRETURN\s+(?P<expr1>[^,]+?)\s+AS\s+(?P<a1>[A-Za-z_]\w+)\s*,\s*(?P<agg>COLLECT\s*\(\s*(?:DISTINCT\s+)?[^)]+\))\s+AS\s+(?P<a2>[A-Za-z_]\w+)(?P<tail>\s+ORDER\s+BY[\s\S]*|\s+LIMIT\s+\d+\s*|\s*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    rewritten = (
        f"{m.group('prefix').rstrip()} "
        f"WITH {m.group('expr1').strip()} AS {m.group('a1')}, {m.group('agg').strip()} AS {m.group('a2')} "
        f"RETURN {m.group('a1')}, {m.group('a2')}{m.group('tail')}"
    )
    return "optional_match_collect_return_to_with", rewritten.strip()


def _rewrite_optional_match_aliased_return_to_with(query: str) -> Tuple[str | None, str | None]:
    lowered = f" {query.lower()} "
    if " optional match " not in lowered:
        return None, None
    if any(token in lowered for token in [" with ", " call ", " union ", " create ", " merge ", " delete ", " set ", " remove ", " load csv ", " foreach ", " unwind "]):
        return None, None

    pattern = re.compile(
        r"(?is)^(?P<prefix>.*?\bOPTIONAL\s+MATCH\b.*)\bRETURN\s+(?P<items>[\s\S]+?)(?P<tail>\s+ORDER\s+BY[\s\S]*|\s+SKIP\s+[\s\S]*|\s+LIMIT\s+[\s\S]*|\s*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    prefix = m.group("prefix").rstrip()
    if any(token in prefix.lower() for token in [" order by ", " skip ", " limit "]):
        return None, None

    items = _split_top_level_csv(m.group("items").strip())
    if len(items) < 2:
        return None, None

    aliased_items: List[str] = []
    aliases: List[str] = []
    aggregate_count = 0
    for item in items:
        mm = re.match(r"(?is)^(?P<expr>.+?)\s+AS\s+(?P<alias>[A-Za-z_]\w*)$", item)
        if not mm:
            return None, None
        expr = mm.group("expr").strip()
        alias = mm.group("alias")
        if "*" in expr:
            return None, None
        if re.search(r"(?is)\bcollect\s*\(", expr):
            return None, None
        if re.search(r"(?is)\bconvert\s*\(", expr):
            return None, None
        aliased_items.append(f"{expr} AS {alias}")
        aliases.append(alias)
        if re.search(r"(?is)\b(?:count|collect|avg|sum|min|max)\s*\(", expr):
            aggregate_count += 1

    if aggregate_count == 0:
        return None, None

    rewritten = f"{prefix} WITH {', '.join(aliased_items)} RETURN {', '.join(aliases)}{m.group('tail')}"
    return "optional_match_aliased_return_to_with", rewritten.strip()


def _rewrite_call_import_with_to_scope_clause(query: str) -> Tuple[str | None, str | None]:
    lowered = f" {query.lower()} "
    if any(token in lowered for token in [" load csv ", " foreach ", " union ", " in transactions ", " collect(", " collect {"]):
        return None, None
    pattern = re.compile(
        r"(?is)\bCALL\s*\{\s*WITH\s+(?P<vars>[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)(?=\s+(?:WITH|WHERE|RETURN|OPTIONAL\s+MATCH|MATCH|CALL|UNION|CREATE|SET|DELETE|REMOVE|MERGE|UNWIND)\b)"
    )
    m = pattern.search(query)
    if not m:
        return None, None
    rewritten = query[: m.start()] + f"CALL ({m.group('vars')}) {{" + query[m.end() :]
    return "call_import_with_to_scope_clause", rewritten.strip()


def _rewrite_call_scope_clause_to_import_with(query: str) -> Tuple[str | None, str | None]:
    lowered = f" {query.lower()} "
    if any(token in lowered for token in [" load csv ", " foreach ", " union ", " in transactions ", " collect(", " collect {"]):
        return None, None
    pattern = re.compile(
        r"(?is)\bCALL\s*\(\s*(?P<vars>[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*\)\s*\{"
    )
    m = pattern.search(query)
    if not m:
        return None, None
    remainder = query[m.end() :]
    # Conditional queries inside scoped CALL are a Cypher 25 form where the old
    # importing-WITH spelling is not equivalent syntactically:
    #   CALL (x) { WHEN ... THEN ... }
    # should not become:
    #   CALL { WITH x WHEN ... THEN ... }
    if re.match(r"(?is)^\s*(?:WHEN|ELSE)\b", remainder):
        return None, None
    if re.match(r"(?is)^\s*(?:WITH|USE)\b", remainder):
        return None, None
    rewritten = query[: m.start()] + f"CALL {{ WITH {m.group('vars')} " + remainder
    return "call_scope_clause_to_import_with", rewritten.strip()


def _rewrite_map_projection_to_literal(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)\bRETURN\s+([A-Za-z_]\w*)\s*\{\s*((?:\.[A-Za-z_]\w*\s*,?\s*)+)\}\s+AS\s+([A-Za-z_]\w*)\s*$"
    )
    m = pattern.search(query.strip())
    if not m:
        return None, None
    var_name, props_block, alias = m.groups()
    props = [p.strip().lstrip(".") for p in props_block.split(",") if p.strip()]
    if not props:
        return None, None
    explicit = ", ".join(f"{p}: {var_name}.{p}" for p in props)
    rewritten = pattern.sub(f"RETURN {{{explicit}}} AS {alias}", query.strip())
    return "map_projection_to_literal", rewritten


def _rewrite_literal_to_map_projection(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)\bRETURN\s+\{\s*((?:[A-Za-z_]\w*\s*:\s*[A-Za-z_]\w*\.[A-Za-z_]\w*\s*,?\s*)+)\}\s+AS\s+([A-Za-z_]\w*)\s*$"
    )
    m = pattern.search(query.strip())
    if not m:
        return None, None
    entries_block, alias = m.groups()
    entries = [e.strip() for e in entries_block.split(",") if e.strip()]
    base_var = None
    props: List[str] = []
    for entry in entries:
        mm = re.fullmatch(r"([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*)\.([A-Za-z_]\w*)", entry)
        if not mm:
            return None, None
        key, var_name, prop = mm.groups()
        if key != prop:
            return None, None
        if base_var is None:
            base_var = var_name
        elif base_var != var_name:
            return None, None
        props.append(prop)
    if not base_var or not props:
        return None, None
    proj = ", ".join(f".{p}" for p in props)
    rewritten = pattern.sub(f"RETURN {base_var} {{ {proj} }} AS {alias}", query.strip())
    return "literal_to_map_projection", rewritten


def _rewrite_return_distinct_to_with(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*)\bRETURN\s+DISTINCT\s+(?P<expr>[^,*]+?)\s+AS\s+(?P<alias>[A-Za-z_]\w*)\s+ORDER\s+BY\s+(?P=alias)(?P<tail>\s+LIMIT\s+\d+\s*)?$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    prefix = m.group("prefix").rstrip()
    expr = m.group("expr").strip()
    alias = m.group("alias")
    tail = m.group("tail") or ""
    lowered_query = query.lower()
    if len(re.findall(r"\breturn\b", lowered_query)) != 1:
        return None, None
    if re.search(r"\breturn\b", prefix, flags=re.IGNORECASE):
        return None, None
    if "*" in expr or "," in expr:
        return None, None
    rewritten = f"{prefix} WITH DISTINCT {expr} AS {alias} RETURN {alias} ORDER BY {alias}{tail}"
    return "return_distinct_to_with", rewritten.strip()


def _rewrite_return_distinct_no_order_to_with(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*)\bRETURN\s+DISTINCT\s+(?P<expr>[^,*]+?)\s+AS\s+(?P<alias>[A-Za-z_]\w*)\s*$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    prefix = m.group("prefix").rstrip()
    expr = m.group("expr").strip()
    alias = m.group("alias")
    lowered = query.lower()
    if len(re.findall(r"\breturn\b", lowered)) != 1:
        return None, None
    if re.search(r"\breturn\b", prefix, flags=re.IGNORECASE):
        return None, None
    if " order by " in lowered or " limit " in lowered:
        return None, None
    if "*" in expr or "," in expr:
        return None, None
    rewritten = f"{prefix} WITH DISTINCT {expr} AS {alias} RETURN {alias}"
    return "return_distinct_no_order_to_with", rewritten.strip()


def _rewrite_order_by_alias_to_expression(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)\bRETURN\s+(?P<expr>[^,*]+?)\s+AS\s+(?P<alias>[A-Za-z_]\w*)\s+ORDER\s+BY\s+(?P=alias)(?P<dir>\s+ASC|\s+DESC)?(?P<tail>(?:\s+LIMIT\s+\d+)?)\s*$"
    )
    m = pattern.search(query.strip())
    if not m:
        return None, None
    expr = m.group("expr").strip()
    alias = m.group("alias")
    direction = m.group("dir") or ""
    tail = m.group("tail") or ""
    lowered = f" {query.lower()} "
    if " return distinct " in lowered:
        return None, None
    if re.search(r"\b(?:count|collect|exists)\s*\{", lowered):
        return None, None
    if " call {" in lowered:
        return None, None
    if any(tok in expr.lower() for tok in [" count(", " collect(", " min(", " max(", " sum(", " avg("]):
        return None, None
    replacement = f"RETURN {expr} AS {alias} ORDER BY {expr}{direction}{tail}"
    rewritten = pattern.sub(replacement, query.strip())
    return "order_by_alias_to_expression", rewritten


def _rewrite_concat_collect_externalized(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)\bWITH\s+(?P<left>[A-Za-z_][\w\.\[\]'\"]*)\s*\+\s*COLLECT\((?P<expr>[^)]+)\)\s+AS\s+(?P<alias>[A-Za-z_]\w+)"
    )
    m = pattern.search(query)
    if not m:
        return None, None
    left = m.group("left").strip()
    expr = m.group("expr").strip()
    alias = m.group("alias").strip()
    left_tmp = f"__left_{alias}"
    col_tmp = f"__col_{alias}"
    replacement = (
        f"WITH {left} AS {left_tmp}, COLLECT({expr}) AS {col_tmp} "
        f"WITH {left_tmp} + {col_tmp} AS {alias}"
    )
    rewritten = pattern.sub(replacement, query, count=1)
    return "concat_collect_externalized", rewritten


def _rewrite_return_to_with_return(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*)\bRETURN\s+(?P<expr>[^,]+?)\s+AS\s+(?P<alias>[A-Za-z_]\w+)\s*$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    expr = m.group("expr").strip()
    alias = m.group("alias")
    prefix = m.group("prefix").rstrip()
    lowered_query = query.lower()
    if len(re.findall(r"\breturn\b", lowered_query)) != 1:
        return None, None
    if re.search(r"\b(?:exists|collect|count)\s*\{", lowered_query):
        return None, None
    if any(tok in lowered_query for tok in [" call ", " union "]):
        return None, None
    if " distinct " in f" {prefix.lower()} ":
        return None, None
    if any(tok in expr.lower() for tok in [" count(", " collect(", " min(", " max(", " sum(", " avg("]):
        return None, None
    rewritten = f"{prefix} WITH {expr} AS {alias} RETURN {alias}"
    return "return_to_with_return", rewritten.strip()


def _rewrite_map_projection_to_with_return(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*)\bRETURN\s+(?P<expr>[A-Za-z_]\w*\s*\{\s*.+?\})\s+AS\s+(?P<alias>[A-Za-z_]\w+)\s*$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    prefix = m.group("prefix").rstrip()
    expr = m.group("expr").strip()
    alias = m.group("alias")
    lowered_query = query.lower()
    if len(re.findall(r"\breturn\b", lowered_query)) != 1:
        return None, None
    if any(tok in lowered_query for tok in [" distinct ", " order by ", " limit ", " call ", " union "]):
        return None, None
    rewritten = f"{prefix} WITH {expr} AS {alias} RETURN {alias}"
    return "map_projection_to_with_return", rewritten.strip()


def _rewrite_literal_map_to_with_return(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*)\bRETURN\s+(?P<expr>\{\s*(?:[A-Za-z_]\w*\s*:\s*[^{}]+?\s*,?\s*)+\})\s+AS\s+(?P<alias>[A-Za-z_]\w+)\s*$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    prefix = m.group("prefix").rstrip()
    expr = m.group("expr").strip()
    alias = m.group("alias")
    lowered_query = query.lower()
    if len(re.findall(r"\breturn\b", lowered_query)) != 1:
        return None, None
    if any(tok in lowered_query for tok in [" distinct ", " order by ", " limit ", " call ", " union "]):
        return None, None
    rewritten = f"{prefix} WITH {expr} AS {alias} RETURN {alias}"
    return "literal_map_to_with_return", rewritten.strip()


def _rewrite_single_aggregate_to_with_return(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*)\bRETURN\s+(?P<agg>(?:COUNT|SUM|MIN|MAX|AVG)\s*\([^)]+\))\s+AS\s+(?P<alias>[A-Za-z_]\w+)\s*$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    prefix = m.group("prefix").rstrip()
    agg = m.group("agg").strip()
    alias = m.group("alias")
    lowered_query = query.lower()
    if len(re.findall(r"\breturn\b", lowered_query)) != 1:
        return None, None
    if re.search(r"\b(?:exists|collect|count)\s*\{", lowered_query):
        return None, None
    if any(tok in lowered_query for tok in [" call ", " union ", " order by ", " distinct "]):
        return None, None
    rewritten = f"{prefix} WITH {agg} AS {alias} RETURN {alias}"
    return "single_aggregate_to_with_return", rewritten.strip()


def _rewrite_final_aggregate_to_with(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*)\bRETURN\s+(?P<expr1>[^,]+?)\s+AS\s+(?P<a1>[A-Za-z_]\w*)\s*,\s*(?P<agg>(?:COUNT|SUM|MIN|MAX|AVG)\s*\([^)]+\))\s+AS\s+(?P<a2>[A-Za-z_]\w+)\s*$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    prefix = m.group("prefix").rstrip()
    expr1 = m.group("expr1").strip()
    a1 = m.group("a1")
    agg = m.group("agg").strip()
    a2 = m.group("a2")
    rewritten = f"{prefix} WITH {expr1} AS {a1}, {agg} AS {a2} RETURN {a1}, {a2}"
    return "final_aggregate_to_with", rewritten.strip()


def _rewrite_trivial_call_passthrough_inline(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*)\bCALL\s*\{\s*WITH\s+(?P<with_vars>[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s+RETURN\s+(?P<expr>.+?)\s+AS\s+(?P<alias>[A-Za-z_]\w+)\s*\}\s*RETURN\s+(?P=alias)\s*$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    prefix = m.group("prefix").rstrip()
    with_vars = m.group("with_vars").strip()
    expr = m.group("expr").strip()
    alias = m.group("alias")
    rewritten = f"{prefix} WITH {with_vars}, {expr} AS {alias} RETURN {alias}"
    return "trivial_call_passthrough_inline", rewritten.strip()


def _rewrite_with_alias_passthrough_split(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*)\bWITH\s+(?P<expr>[^,]+?)\s+AS\s+(?P<alias>[A-Za-z_]\w+)\s+RETURN\s+(?P=alias)\s*$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    prefix = m.group("prefix").rstrip()
    expr = m.group("expr").strip()
    alias = m.group("alias")
    lowered_query = query.lower()
    if len(re.findall(r"\bwith\b", lowered_query)) != 1 or len(re.findall(r"\breturn\b", lowered_query)) != 1:
        return None, None
    if any(tok in lowered_query for tok in [" distinct ", " order by ", " limit ", " call ", " union "]):
        return None, None
    rewritten = f"{prefix} WITH {expr} AS {alias} WITH {alias} AS {alias} RETURN {alias}"
    return "with_alias_passthrough_split", rewritten.strip()


def _rewrite_with_distinct_alias_passthrough_split(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*)\bWITH\s+DISTINCT\s+(?P<expr>[^,]+?)\s+AS\s+(?P<alias>[A-Za-z_]\w+)\s+RETURN\s+(?P=alias)\s*$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    prefix = m.group("prefix").rstrip()
    expr = m.group("expr").strip()
    alias = m.group("alias")
    lowered_query = query.lower()
    if len(re.findall(r"\bwith\b", lowered_query)) != 1 or len(re.findall(r"\breturn\b", lowered_query)) != 1:
        return None, None
    if any(tok in lowered_query for tok in [" order by ", " limit ", " call ", " union "]):
        return None, None
    rewritten = f"{prefix} WITH DISTINCT {expr} AS {alias} WITH {alias} AS {alias} RETURN {alias}"
    return "with_distinct_alias_passthrough_split", rewritten.strip()


# Dinkel-inspired metamorphic rewrites. These are intentionally read-only and
# conservative: Dinkel also uses write/state transformations, but mixing those
# into the low-noise Neo4j self-diff lane would make triage much harder.

def _dinkel_where_demorgan_and(query: str) -> Tuple[str | None, str | None]:
    if not _is_dinkel_predicate_rewrite_safe(query):
        return None, None
    if _has_call_subquery_or_union(query) or _has_dinkel_clause_rewrite_hazard(query):
        return None, None
    pattern = re.compile(
        rf"(?is)\bWHERE\s+(?P<cond>.*?)(?P<next>\s+(?:{_DINKEL_WHERE_STOP})\b)"
    )
    m = pattern.search(query)
    if not m:
        return None, None
    left, right = _split_top_level_boolean_operator(m.group("cond").strip(), "AND")
    if not left or not right:
        return None, None
    if not _is_safe_dinkel_expression_text(left) or not _is_safe_dinkel_expression_text(right):
        return None, None
    replacement = f"WHERE NOT (NOT ({left}) OR NOT ({right})){m.group('next')}"
    rewritten = query[: m.start()] + replacement + query[m.end() :]
    return "dinkel_op_demorgan_and", rewritten.strip()


def _dinkel_where_demorgan_or(query: str) -> Tuple[str | None, str | None]:
    if not _is_dinkel_predicate_rewrite_safe(query):
        return None, None
    if _has_call_subquery_or_union(query) or _has_dinkel_clause_rewrite_hazard(query):
        return None, None
    pattern = re.compile(
        rf"(?is)\bWHERE\s+(?P<cond>.*?)(?P<next>\s+(?:{_DINKEL_WHERE_STOP})\b)"
    )
    m = pattern.search(query)
    if not m:
        return None, None
    left, right = _split_top_level_boolean_operator(m.group("cond").strip(), "OR")
    if not left or not right:
        return None, None
    if not _is_safe_dinkel_expression_text(left) or not _is_safe_dinkel_expression_text(right):
        return None, None
    replacement = f"WHERE NOT (NOT ({left}) AND NOT ({right})){m.group('next')}"
    rewritten = query[: m.start()] + replacement + query[m.end() :]
    return "dinkel_op_demorgan_or", rewritten.strip()


def _dinkel_where_null_predicate_negation(query: str) -> Tuple[str | None, str | None]:
    if not _is_dinkel_predicate_rewrite_safe(query):
        return None, None
    if _has_call_subquery_or_union(query) or _has_dinkel_clause_rewrite_hazard(query):
        return None, None
    pattern = re.compile(
        rf"(?is)\bWHERE\s+(?P<expr>.+?)\s+IS\s+(?P<neg>NOT\s+)?NULL(?P<next>\s+(?:{_DINKEL_WHERE_STOP})\b)"
    )
    m = pattern.search(query)
    if not m:
        return None, None
    expr = m.group("expr").strip()
    if not expr or not _is_safe_dinkel_expression_text(expr):
        return None, None
    if _split_top_level_boolean_operator(expr, "AND") != (None, None):
        return None, None
    if _split_top_level_boolean_operator(expr, "OR") != (None, None):
        return None, None
    if m.group("neg"):
        replacement = f"WHERE NOT ({expr} IS NULL){m.group('next')}"
        rewrite_type = "dinkel_op_is_not_null_negation"
    else:
        replacement = f"WHERE NOT ({expr} IS NOT NULL){m.group('next')}"
        rewrite_type = "dinkel_op_is_null_negation"
    rewritten = query[: m.start()] + replacement + query[m.end() :]
    return rewrite_type, rewritten.strip()


def _dinkel_add_match_where_true(query: str) -> Tuple[str | None, str | None]:
    if not _is_dinkel_predicate_rewrite_safe(query):
        return None, None
    if _has_call_subquery_or_union(query):
        return None, None
    if _has_dinkel_clause_rewrite_hazard(query):
        return None, None
    pattern = re.compile(
        r"(?is)\b(?P<kw>OPTIONAL\s+MATCH|MATCH)\s+(?P<body>.*?)(?P<next>\s+(?:WITH|RETURN)\b)"
    )
    for m in pattern.finditer(query):
        body = m.group("body")
        if re.search(r"\bWHERE\b", body, flags=re.I):
            continue
        replacement = f"{m.group('kw')} {body.rstrip()} WHERE true{m.group('next')}"
        rewritten = query[: m.start()] + replacement + query[m.end() :]
        return "dinkel_is_add_match_where_true", rewritten.strip()
    return None, None


def _dinkel_add_where_and_true(query: str) -> Tuple[str | None, str | None]:
    if not _is_dinkel_predicate_rewrite_safe(query):
        return None, None
    if _has_call_subquery_or_union(query):
        return None, None
    if _has_dinkel_clause_rewrite_hazard(query):
        return None, None
    if re.search(r"\b(?:STARTS|ENDS)\s+WITH\b", query, flags=re.I):
        return None, None
    pattern = re.compile(
        rf"(?is)\bWHERE\s+(?P<cond>.*?)(?P<next>\s+(?:{_DINKEL_WHERE_STOP})\b)"
    )
    m = pattern.search(query)
    if not m:
        return None, None
    cond = m.group("cond").strip()
    if not cond or " true" in f" {cond.lower()} ":
        return None, None
    replacement = f"WHERE ({cond}) AND true{m.group('next')}"
    rewritten = query[: m.start()] + replacement + query[m.end() :]
    return "dinkel_mi_where_and_true", rewritten.strip()


def _dinkel_double_negate_where(query: str) -> Tuple[str | None, str | None]:
    if not _is_dinkel_predicate_rewrite_safe(query):
        return None, None
    if _has_call_subquery_or_union(query):
        return None, None
    if _has_dinkel_clause_rewrite_hazard(query):
        return None, None
    if re.search(r"\b(?:STARTS|ENDS)\s+WITH\b", query, flags=re.I):
        return None, None
    pattern = re.compile(
        rf"(?is)\bWHERE\s+(?P<cond>.*?)(?P<next>\s+(?:{_DINKEL_WHERE_STOP})\b)"
    )
    m = pattern.search(query)
    if not m:
        return None, None
    cond = m.group("cond").strip()
    if not cond or cond.lower().startswith("not "):
        return None, None
    replacement = f"WHERE NOT (NOT ({cond})){m.group('next')}"
    rewritten = query[: m.start()] + replacement + query[m.end() :]
    return "dinkel_mi_double_negate_where", rewritten.strip()


def _dinkel_add_unused_context_with_star(query: str) -> Tuple[str | None, str | None]:
    if not _is_dinkel_safe_read_query(query):
        return None, None
    if _has_call_subquery_or_union(query):
        return None, None
    if _has_potential_fetch_limit_order_hazard(query):
        return None, None
    lowered = query.lower()
    if re.search(r"(?is)\bRETURN\s+DISTINCT\b", query) or "__dinkel_unused" in lowered:
        return None, None
    if re.search(r"(?is)\bRETURN\s+\*", query):
        return None, None
    if re.search(r"(?is)\bRETURN\b[\s\S]*\b(?:count|collect|min|max|sum|avg)\s*\(", query):
        return None, None
    m = re.search(r"(?is)\bRETURN\b", query)
    if not m:
        return None, None
    rewritten = f"{query[:m.start()].rstrip()} WITH *, true AS __dinkel_unused {query[m.start():]}"
    return "dinkel_uc_add_unused_with_star", rewritten.strip()


def _dinkel_numeric_add_zero(query: str) -> Tuple[str | None, str | None]:
    if not _is_dinkel_safe_read_query(query):
        return None, None
    if _has_potential_fetch_limit_order_hazard(query):
        return None, None
    if _has_call_subquery_or_union(query):
        return None, None
    if _has_dinkel_clause_rewrite_hazard(query):
        return None, None
    m = _final_return_match(query)
    if not m:
        return None, None
    body = m.group("body").strip()
    tail = m.group("tail") or ""
    parts = _split_top_level_csv(body)
    for idx, item in enumerate(parts):
        expr, alias = _split_return_alias(item)
        if not expr or not alias or not _is_likely_numeric_expr(expr):
            continue
        if re.search(r"\b(?:count|collect|exists)\s*\{", expr, flags=re.I):
            continue
        parts[idx] = f"({expr}) + 0 AS {alias}"
        rewritten = query[: m.start("body")] + ", ".join(parts) + tail
        return "dinkel_mi_numeric_add_zero", rewritten.strip()
    return None, None


def _dinkel_case_true_identity(query: str) -> Tuple[str | None, str | None]:
    if not _is_dinkel_safe_read_query(query):
        return None, None
    if _has_potential_fetch_limit_order_hazard(query):
        return None, None
    if _has_call_subquery_or_union(query):
        return None, None
    if _has_dinkel_clause_rewrite_hazard(query):
        return None, None
    if re.search(r"\bWHEN\b|\bELSE\b", query, flags=re.I):
        return None, None
    m = _final_return_match(query)
    if not m:
        return None, None
    body = m.group("body").strip()
    tail = m.group("tail") or ""
    parts = _split_top_level_csv(body)
    for idx, item in enumerate(parts):
        expr, alias = _split_return_alias(item)
        if not expr or not alias:
            continue
        expr_lower = expr.lower()
        if "[" in expr_lower or "]" in expr_lower:
            continue
        if re.search(r"\b(?:count|collect|min|max|sum|avg)\s*\(", expr_lower):
            continue
        if re.search(r"\b(?:count|collect|exists)\s*\{", expr_lower):
            continue
        parts[idx] = f"CASE WHEN true THEN {expr} ELSE {expr} END AS {alias}"
        rewritten = query[: m.start("body")] + ", ".join(parts) + tail
        return "dinkel_cf_case_true_identity", rewritten.strip()
    return None, None


def _dinkel_return_abs_case_expansion(query: str) -> Tuple[str | None, str | None]:
    if not _is_dinkel_safe_read_query(query):
        return None, None
    if _has_potential_fetch_limit_order_hazard(query):
        return None, None
    if _has_call_subquery_or_union(query) or _has_dinkel_clause_rewrite_hazard(query):
        return None, None
    m = _final_return_match(query)
    if not m:
        return None, None
    body = m.group("body").strip()
    tail = m.group("tail") or ""
    parts = _split_top_level_csv(body)
    for idx, item in enumerate(parts):
        expr, alias = _split_return_alias(item)
        if not expr or not alias:
            continue
        abs_match = re.fullmatch(r"(?is)abs\s*\(\s*(?P<inner>[^()]+?)\s*\)", expr.strip())
        if not abs_match:
            continue
        inner = abs_match.group("inner").strip()
        if not _is_safe_dinkel_expression_text(inner):
            continue
        parts[idx] = f"CASE WHEN ({inner}) < 0 THEN -({inner}) ELSE {inner} END AS {alias}"
        rewritten = query[: m.start("body")] + ", ".join(parts) + tail
        return "dinkel_fn_abs_to_case", rewritten.strip()
    return None, None


def _dinkel_return_redundant_literal_cast(query: str) -> Tuple[str | None, str | None]:
    if not _is_dinkel_safe_read_query(query):
        return None, None
    if _has_potential_fetch_limit_order_hazard(query):
        return None, None
    if _has_call_subquery_or_union(query) or _has_dinkel_clause_rewrite_hazard(query):
        return None, None
    m = _final_return_match(query)
    if not m:
        return None, None
    body = m.group("body").strip()
    tail = m.group("tail") or ""
    parts = _split_top_level_csv(body)
    cast_patterns = [
        ("toInteger", r"-?\d+", "dinkel_fn_to_integer_literal_identity"),
        ("toFloat", r"-?\d+\.\d+", "dinkel_fn_to_float_literal_identity"),
        ("toBoolean", r"(?is:true|false)", "dinkel_fn_to_boolean_literal_identity"),
    ]
    for idx, item in enumerate(parts):
        expr, alias = _split_return_alias(item)
        if not expr or not alias:
            continue
        for fn_name, literal_pattern, rewrite_type in cast_patterns:
            cast_match = re.fullmatch(rf"(?is){fn_name}\s*\(\s*(?P<inner>{literal_pattern})\s*\)", expr.strip())
            if cast_match:
                parts[idx] = f"{cast_match.group('inner')} AS {alias}"
                rewritten = query[: m.start("body")] + ", ".join(parts) + tail
                return rewrite_type, rewritten.strip()
    return None, None


def _dinkel_neo4j_runtime_hint_slotted(query: str) -> Tuple[str | None, str | None]:
    if not _is_dinkel_safe_read_query(query):
        return None, None
    if _has_potential_fetch_limit_order_hazard(query):
        return None, None
    if re.match(r"(?is)^\s*CYPHER\b", query):
        return None, None
    if _has_call_subquery_or_union(query):
        return None, None
    return "dinkel_neo4j_runtime_slotted", f"CYPHER runtime=slotted {query.strip()}"


def _dinkel_call_import_with_to_scope_clause(query: str) -> Tuple[str | None, str | None]:
    if _has_cypher_comment(query):
        return None, None
    if re.search(r"\bLIMIT\b", query, flags=re.I):
        return None, None
    if re.search(r"\(\s*CALL\s*\{", query, flags=re.I):
        return None, None
    return _rewrite_call_import_with_to_scope_clause(query)


def _is_safe_dinkel_final_return_rewrite(query: str) -> bool:
    if not _is_dinkel_safe_read_query(query):
        return False
    if _has_potential_fetch_limit_order_hazard(query):
        return False
    if _has_call_subquery_or_union(query):
        return False
    if _has_dinkel_clause_rewrite_hazard(query):
        return False
    return True


def _dinkel_return_to_with_return(query: str) -> Tuple[str | None, str | None]:
    if not _is_safe_dinkel_final_return_rewrite(query):
        return None, None
    return _rewrite_return_to_with_return(query)


def _dinkel_where_exists_to_count_gt_zero(query: str) -> Tuple[str | None, str | None]:
    if not _is_safe_dinkel_final_return_rewrite(query):
        return None, None
    return _rewrite_where_exists_to_count_gt_zero(query)


def _dinkel_where_count_gt_zero_to_exists(query: str) -> Tuple[str | None, str | None]:
    if not _is_safe_dinkel_final_return_rewrite(query):
        return None, None
    return _rewrite_where_count_gt_zero_to_exists(query)


def _dinkel_return_case_null_to_coalesce(query: str) -> Tuple[str | None, str | None]:
    if not _is_safe_dinkel_final_return_rewrite(query):
        return None, None
    return _rewrite_return_case_null_to_coalesce(query)


def _dinkel_return_coalesce_to_case_null(query: str) -> Tuple[str | None, str | None]:
    if not _is_safe_dinkel_final_return_rewrite(query):
        return None, None
    return _rewrite_return_coalesce_to_case_null(query)


# risky neo4j-focused rewrites

def _risky_with_split(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(r"(?is)^(?P<prefix>.*)\bWITH\s+(?P<body>[^\n]+?)\s+(?P<next>MATCH|OPTIONAL MATCH|WHERE|RETURN)\b(?P<suffix>.*)$")
    m = pattern.match(query.strip())
    if not m:
        return None, None
    prefix = m.group('prefix').rstrip()
    body = m.group('body').strip()
    next_kw = m.group('next')
    suffix = m.group('suffix')
    lowered = body.lower()
    if any(tok in lowered for tok in [' distinct ', ' order by ', ' limit ', ' skip ', ' call ', ' union ']):
        return None, None
    if body.count(',') > 1:
        return None, None
    rewritten = f"{prefix} WITH {body} WITH {body} {next_kw}{suffix}"
    return 'risky_with_split', rewritten.strip()


def _risky_optional_match_aggregate_externalize(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*\bOPTIONAL\s+MATCH\b.*)\bRETURN\s+(?P<left>[A-Za-z_]\w*\.[A-Za-z_]\w*)\s+AS\s+(?P<a1>[A-Za-z_]\w+)\s*,\s*(?P<agg>COUNT\s*\([^\)]+\))\s+AS\s+(?P<a2>[A-Za-z_]\w+)\s*$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    prefix = m.group('prefix').rstrip()
    left = m.group('left').strip()
    a1 = m.group('a1')
    agg = m.group('agg').strip()
    a2 = m.group('a2')
    base_var = left.split('.')[0]
    if any(tok in query.lower() for tok in [' call ', ' union ', ' distinct ', ' order by ', ' limit ']):
        return None, None
    rewritten = f"{prefix} WITH {base_var}, {agg} AS {a2} RETURN {left} AS {a1}, {a2}"
    return 'risky_optional_match_aggregate_externalize', rewritten.strip()


def _risky_with_distinct_passthrough_before_match(query: str) -> Tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?is)^(?P<prefix>.*)\bWITH\s+DISTINCT\s+(?P<expr>[^,]+?)\s+AS\s+(?P<alias>[A-Za-z_]\w+)\s+(?P<next>MATCH|OPTIONAL MATCH)\b(?P<suffix>.*)$"
    )
    m = pattern.match(query.strip())
    if not m:
        return None, None
    prefix = m.group('prefix').rstrip()
    expr = m.group('expr').strip()
    alias = m.group('alias')
    next_kw = m.group('next')
    suffix = m.group('suffix')
    if any(tok in query.lower() for tok in [' call ', ' union ', ' order by ', ' limit ']):
        return None, None
    rewritten = f"{prefix} WITH DISTINCT {expr} AS {alias} WITH {alias} AS {alias} {next_kw}{suffix}"
    return 'risky_with_distinct_passthrough_before_match', rewritten.strip()


CONSERVATIVE_REWRITE_FUNCTIONS = [
    _rewrite_map_projection_to_literal,
    _rewrite_literal_to_map_projection,
    _rewrite_return_distinct_to_with,
    _rewrite_return_distinct_no_order_to_with,
    _rewrite_order_by_alias_to_expression,
    _rewrite_return_to_with_return,
    _rewrite_map_projection_to_with_return,
    _rewrite_literal_map_to_with_return,
    _rewrite_single_aggregate_to_with_return,
    _rewrite_final_aggregate_to_with,
    _rewrite_trivial_call_passthrough_inline,
    _rewrite_with_alias_passthrough_split,
    _rewrite_with_distinct_alias_passthrough_split,
]

RISKY_NEO4J_EXTRA_FUNCTIONS = [
    _risky_optional_match_aggregate_externalize,
    _risky_with_distinct_passthrough_before_match,
    _rewrite_where_pattern_predicate_to_exists,
    _rewrite_where_exists_to_pattern_predicate,
    _rewrite_where_exists_with_import_to_plain_exists,
    _rewrite_where_exists_to_count_gt_zero,
    _rewrite_where_count_gt_zero_to_exists,
    _rewrite_where_exists_function_to_exists_subquery,
    _rewrite_where_exists_function_to_count_gt_zero,
    _rewrite_where_exists_function_to_pattern_predicate,
    _rewrite_count_pattern_subquery_to_optional_match,
    _rewrite_collect_subquery_to_optional_match,
    _rewrite_return_exists_to_optional_match_bool,
    _rewrite_return_exists_to_count_gt_zero_bool,
    _rewrite_return_exists_function_to_exists_subquery,
    _rewrite_return_exists_function_to_count_gt_zero_bool,
    _rewrite_return_size_pattern_comprehension_to_count,
    _rewrite_return_size_pattern_comprehension_gt_zero_to_exists,
    _rewrite_relationship_comprehension_size_to_length,
    _rewrite_relationships_size_to_length,
    _rewrite_nodes_size_to_length_plus_one,
    _rewrite_return_case_null_to_coalesce,
    _rewrite_return_coalesce_to_case_null,
    _rewrite_where_none_to_not_any,
    _rewrite_call_return_only_to_with,
    _rewrite_optional_match_aliased_return_to_with,
    _rewrite_call_import_with_to_scope_clause,
    _rewrite_call_scope_clause_to_import_with,
]

SEMANTIC_NEO4J_REWRITE_FUNCTIONS = [
    _rewrite_cypher25_scoped_call_return_only_to_with,
    _rewrite_cypher25_let_to_with_star,
    _rewrite_cypher25_filter_to_with_where,
    _rewrite_cypher25_with_star_where_to_filter,
    _rewrite_cypher25_with_star_projection_to_let,
    _rewrite_cypher25_match_where_to_filter,
    _rewrite_cypher25_return_all_identity,
    _rewrite_cypher25_with_all_identity,
    _rewrite_cypher25_return_next_identity,
    _rewrite_cypher25_predicate_partitioning,
    _rewrite_cypher25_conditional_branch_swap_case_guard,
    _rewrite_where_pattern_predicate_to_exists,
    _rewrite_where_exists_to_pattern_predicate,
    _rewrite_where_exists_with_import_to_plain_exists,
    _rewrite_where_exists_to_count_gt_zero,
    _rewrite_where_count_gt_zero_to_exists,
    _rewrite_where_exists_function_to_exists_subquery,
    _rewrite_where_exists_function_to_count_gt_zero,
    _rewrite_where_exists_function_to_pattern_predicate,
    _rewrite_count_pattern_subquery_to_optional_match,
    _rewrite_collect_subquery_to_optional_match,
    _rewrite_return_exists_to_optional_match_bool,
    _rewrite_return_exists_to_count_gt_zero_bool,
    _rewrite_return_exists_function_to_exists_subquery,
    _rewrite_return_exists_function_to_count_gt_zero_bool,
    _rewrite_return_size_pattern_comprehension_to_count,
    _rewrite_return_size_pattern_comprehension_gt_zero_to_exists,
    _rewrite_relationship_comprehension_size_to_length,
    _rewrite_relationships_size_to_length,
    _rewrite_nodes_size_to_length_plus_one,
    _rewrite_return_case_null_to_coalesce,
    _rewrite_return_coalesce_to_case_null,
    _rewrite_where_any_membership_to_in,
    _rewrite_where_none_to_not_any,
    _rewrite_call_return_only_to_with,
    _rewrite_optional_match_count_return_to_with,
    _rewrite_optional_match_collect_return_to_with,
    _rewrite_optional_match_aliased_return_to_with,
    _rewrite_call_import_with_to_scope_clause,
    _rewrite_call_scope_clause_to_import_with,
]

PATHLIST_NEO4J_REWRITE_FUNCTIONS = [
    _rewrite_length_path_to_relationships_size,
    _rewrite_relationship_comprehension_size_to_length,
    _rewrite_relationships_size_to_length,
    _rewrite_nodes_size_to_length_plus_one,
    _rewrite_return_size_pattern_comprehension_to_count,
    _rewrite_return_size_pattern_comprehension_gt_zero_to_exists,
]

DINKEL_NEO4J_REWRITE_FUNCTIONS = [
    _dinkel_where_demorgan_and,
    _dinkel_where_demorgan_or,
    _dinkel_where_null_predicate_negation,
    _dinkel_add_match_where_true,
    _dinkel_add_where_and_true,
    _dinkel_double_negate_where,
    _dinkel_numeric_add_zero,
    _dinkel_case_true_identity,
    _dinkel_return_abs_case_expansion,
    _dinkel_return_redundant_literal_cast,
    _dinkel_neo4j_runtime_hint_slotted,
    _dinkel_add_unused_context_with_star,
    _dinkel_return_to_with_return,
    _dinkel_call_import_with_to_scope_clause,
    _dinkel_where_exists_to_count_gt_zero,
    _dinkel_where_count_gt_zero_to_exists,
    _dinkel_return_case_null_to_coalesce,
    _dinkel_return_coalesce_to_case_null,
]

CYPHER25_METAMORPHIC_REWRITE_FUNCTIONS = [
    _rewrite_cypher25_let_to_with_star,
    _rewrite_cypher25_with_star_projection_to_let,
    _rewrite_cypher25_filter_to_with_where,
    _rewrite_cypher25_with_star_where_to_filter,
    _rewrite_cypher25_match_where_to_filter,
    _rewrite_cypher25_scoped_call_return_only_to_with,
    _rewrite_cypher25_return_all_identity,
    _rewrite_cypher25_with_all_identity,
    _rewrite_cypher25_return_next_identity,
    _rewrite_cypher25_predicate_partitioning,
    _rewrite_cypher25_conditional_branch_swap_case_guard,
    _rewrite_return_case_null_to_coalesce,
    _rewrite_return_coalesce_to_case_null,
    _dinkel_where_demorgan_and,
    _dinkel_where_demorgan_or,
    _dinkel_where_null_predicate_negation,
    _dinkel_add_match_where_true,
    _dinkel_add_where_and_true,
    _dinkel_double_negate_where,
    _dinkel_numeric_add_zero,
    _dinkel_case_true_identity,
    _dinkel_return_abs_case_expansion,
    _dinkel_return_redundant_literal_cast,
]


def generate_rewrites(case: Dict[str, Any], profile: str = 'conservative') -> List[Dict[str, Any]]:
    query = str(case.get('main_query') or '').strip()
    if not query:
        return []
    if _has_nondeterministic_selfdiff_functions(query):
        return []
    language_prefix, rewrite_input_query = _split_language_prefix(query)
    if profile == 'cypher25_metamorphic' and _has_write_clause(rewrite_input_query):
        return []

    if profile == 'cypher25_metamorphic':
        functions = CYPHER25_METAMORPHIC_REWRITE_FUNCTIONS
    elif profile == 'semantic_neo4j':
        functions = SEMANTIC_NEO4J_REWRITE_FUNCTIONS
    elif profile == 'dinkel_neo4j':
        functions = DINKEL_NEO4J_REWRITE_FUNCTIONS
    elif profile == 'pathlist_neo4j':
        functions = PATHLIST_NEO4J_REWRITE_FUNCTIONS
    elif profile == 'risky_neo4j':
        functions = CONSERVATIVE_REWRITE_FUNCTIONS + RISKY_NEO4J_EXTRA_FUNCTIONS
    else:
        functions = CONSERVATIVE_REWRITE_FUNCTIONS

    rewrites: List[Dict[str, Any]] = []
    seen_queries = {query}
    case_id = str(case.get('case_id') or '')

    for fn in functions:
        rewrite_type, rewritten_body = fn(rewrite_input_query)
        rewritten_query = _join_language_prefix(language_prefix, rewritten_body or "")
        if not rewrite_type or not rewritten_query:
            continue
        rewritten_query = rewritten_query.strip()
        if not rewritten_query or rewritten_query in seen_queries:
            continue
        seen_queries.add(rewritten_query)
        pair_id = stable_pair_id(case_id, rewrite_type, rewritten_query)
        rewrites.append(
            {
                'pair_id': pair_id,
                'rewrite_type': rewrite_type,
                'original_case_id': case_id,
                'original_query_id': case.get('query_id'),
                'original_main_query': query,
                'rewritten_main_query': rewritten_query,
            }
        )
    return rewrites
