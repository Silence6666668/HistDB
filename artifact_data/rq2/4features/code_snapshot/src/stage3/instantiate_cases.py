from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List

from src.common.io_utils import read_text
from src.common.text_utils import normalize_ws
from src.llm.model_client import BaseModelClient
from src.stage2.fragment_policy import is_documented_invalid_example
from src.stage3.postprocess_cases import postprocess_case
from src.stage3.validate_queries import validate_case


FEATURE_PROMPT_FORMAT = "natural_language_with_machine_name_tags_evidence_v1"


def load_instantiation_prompt(path: str) -> str:
    return read_text(path)


def stable_case_id(query_id: str, main_query: str, variant_index: int = 0) -> str:
    return "case_" + hashlib.sha1(
        f"{query_id}||variant:{variant_index}||{main_query}".encode("utf-8")
    ).hexdigest()[:12]


def _format_feature_items(feature_items: List[Any]) -> str:
    lines: List[str] = []
    for ft in feature_items or []:
        if not isinstance(ft, dict):
            text = normalize_ws(str(ft)).strip()
            if text:
                lines.append(f"- Feature: {text}")
            continue

        machine_name = normalize_ws(
            str(ft.get("text") or ft.get("feature_text") or ft.get("feature_name") or "")
        ).strip()
        description = normalize_ws(
            str(ft.get("description") or ft.get("feature_description") or "")
        ).strip()
        tags = [normalize_ws(str(tag)).strip() for tag in ft.get("tags", []) or []]
        tags = [tag for tag in tags if tag]
        evidence = normalize_ws(str(ft.get("evidence") or "")).strip()

        # Put the semantic description first so the model reasons from intent,
        # while retaining the stable machine name for provenance and auditing.
        if description:
            lines.append(f"- Natural-language feature: {description}")
            if machine_name:
                lines.append(f"  machine_name: {machine_name}")
        elif machine_name:
            lines.append(f"- Feature: {machine_name.replace('_', ' ')}")
            lines.append(f"  machine_name: {machine_name}")
        else:
            continue

        if tags:
            lines.append(f"  tags: {', '.join(tags)}")
        if evidence:
            lines.append(f"  evidence: {evidence}")

    return "\n".join(lines) if lines else "- none"


def _family_bias_guidance(family_bias: str | None) -> str:
    bias = (family_bias or '').strip().lower()
    mapping = {
        'bug_rich_memgraph': 'Favor bug-rich Cypher interactions that have historically exposed Memgraph issues: CALL plus WITH/scope, UNION/UNION ALL plus projection, OPTIONAL MATCH plus aggregation, DELETE plus RETURN, and projection/order interactions.',
        'call_scope': 'Favor CALL subqueries, WITH boundaries, alias propagation, and outer-variable scope interactions.',
        'union_projection': 'Favor UNION or UNION ALL with projection, aliasing, RETURN *-like visible output behavior, and branch shape consistency.',
        'optional_agg': 'Favor OPTIONAL MATCH combined with aggregation, null-sensitive grouping, and row-preservation behavior.',
        'delete_return': 'Favor delete or detach-delete queries where returned values, aggregation after delete, or object visibility might matter.',
        'projection_order': 'Favor map projection, aliases, ORDER BY, DISTINCT, and output-column visibility interactions.',
    }
    return mapping.get(bias, '')


def _variant_focus(variant_index: int, family_bias: str | None = None) -> str:
    focuses = [
        "Favor a compact read query where the selected features interact tightly and the result shape is easy to compare.",
        "Favor a query where OPTIONAL MATCH, null handling, row preservation, or empty-group behavior matters.",
        "Favor a query where CALL subqueries, WITH, aliasing, or variable scope interact in a way that could affect row propagation.",
        "Favor a query where UNION or UNION ALL interacts with aggregation, grouping, or projection.",
        "Favor a query where path patterns, variable-length traversals, or path-derived values matter.",
        "Favor a query where ORDER BY, DISTINCT, LIMIT, or grouping keys interact with other clauses in a non-trivial way.",
        "Favor a query where map projection, returned aliases, or RETURN * semantics could affect visible output columns.",
        "Favor a query where read and write clauses interact, while keeping the testcase executable and easy to clean up.",
    ]
    base_focus = focuses[variant_index % len(focuses)]
    bias_focus = _family_bias_guidance(family_bias)
    if bias_focus:
        return f"{bias_focus} {base_focus}"
    return base_focus


def _generation_profile_guidance(generation_profile: str | None) -> str:
    profile = (generation_profile or "portable_crossdb").strip().lower()
    if profile == "neo4j_cypher25":
        return (
            "GENERATION PROFILE: neo4j_cypher25\n"
            "- This is a Neo4j-only Cypher 25 positive-test lane. Do not optimize for Memgraph or openCypher portability.\n"
            "- The main_query must explicitly start with `CYPHER 25`.\n"
            "- Generate Cypher 25-supported syntax and semantics only.\n"
            "- If selected fragments or skeletons contain Cypher 5 forms that Cypher 25 no longer supports, convert them to Cypher 25-supported forms instead of emitting a negative test.\n"
            "- Required rewrites: `SET n = r` / `SET n += r` / `SET r = n` / `SET r += n` -> use `properties(...)`; same-clause MERGE property references -> bind the property with a prior WITH; indexProvider options -> remove them; old vector procedures -> prefer `CREATE VECTOR INDEX` plus `SEARCH` when vector index behavior is needed.\n"
            "- Prefer Cypher 25 features as first-class test targets: NEXT, LET, FILTER, WHEN/ELSE, FOR, RETURN/WITH ALL, CALL (...) scoped subqueries, expression subqueries, type predicates `IS ::`, IS LABELED, PROPERTY_EXISTS, VECTOR, SEARCH, coll.*, string.*, format(), allReduce(), ACYCLIC, REPEATABLE ELEMENTS, and DIFFERENT RELATIONSHIPS.\n"
            "- For scoped subqueries such as `CALL (o, items) { ... }`, do not add an initial `WITH o, items` import line inside the subquery body. Use the imported variables directly.\n"
            "- Never return an imported outer variable from a CALL subquery using the same name. Use fresh aliases such as `RETURN o.id AS order_id, items AS items_out`, then reference `items_out` outside.\n"
            "- Keep the testcase executable on a fresh Neo4j database with tiny deterministic setup and complete ORDER BY keys before LIMIT.\n"
            "- In notes, name any Cypher 5 -> Cypher 25 rewrite applied and the Cypher 25 feature family being stressed.\n"
        )
    if profile == "neo4j_cypher25_metamorphic":
        return (
            "GENERATION PROFILE: neo4j_cypher25_metamorphic\n"
            "- This is a Neo4j-only Cypher 25 self-diff / metamorphic testing lane for logic bugs.\n"
            "- The main_query must explicitly start with `CYPHER 25` and must be read-only. Setup may create a tiny deterministic graph, but main_query must not use CREATE, MERGE, SET, DELETE, REMOVE, FOREACH, or LOAD CSV.\n"
            "- Generate a query whose semantics has at least one clear equivalent rewrite. Prefer structures supported by the `cypher25_metamorphic` rewrite profile.\n"
            "- Strongly prefer combinations of these Cypher 25 features: NEXT, LET, FILTER, WHEN/ELSE conditional queries, scoped CALL (...), type predicates `IS ::`, RETURN ALL, WITH ALL, and expression subqueries.\n"
            "- Do not use nullable type predicate markers such as `LIST<STRING>?` or `STRING?`. Use non-null type predicates such as `IS :: LIST<STRING>`, `IS :: LIST<INTEGER>`, `IS :: STRING`, or `IS :: VECTOR`.\n"
            "- Best target shape A: row source -> NEXT -> WHEN/ELSE returning mixed value shapes -> NEXT -> FILTER value IS :: TYPE -> type-specific function such as size(), keys(), vector_dimension_count(), or string length.\n"
            "- For conditional queries after NEXT, use `RETURN ... NEXT WHEN ...`; do not use `WITH ... NEXT WHEN ...` because the conditional part may not see those variables as intended.\n"
            "- For multiple conditional branches, use repeated `WHEN ... THEN ...` branches followed by one final `ELSE ...`; do not write `ELSE WHEN ...`.\n"
            "- Best target shape B: MATCH/UNWIND -> WITH *, expr AS alias or LET alias = expr -> FILTER predicate -> RETURN deterministic scalar/table; this should be comparable to WITH/FILTER rewrites.\n"
            "- Best target shape C: scoped CALL (vars) { RETURN expr AS alias } followed by LET/FILTER/RETURN, where the subquery output can be flattened into WITH *, expr AS alias.\n"
            "- Best target shape D: MATCH ... WHERE predicate ... RETURN ... where predicate partitioning with predicate, NOT predicate, and predicate IS NULL should preserve the original rows.\n"
            "- Avoid path/list/collect ordering unless you return sorted scalar summaries. If using collect(), sort or return counts only.\n"
            "- Avoid id(), elementId(), rand(), randomUUID(), timestamp(), empty temporal now functions, APOC/GDS, admin commands, schema DDL, and global-state dependencies.\n"
            "- Use tiny setup and deterministic output with complete ORDER BY keys before LIMIT.\n"
            "- In notes, name the intended metamorphic rewrite: FILTER<->WITH WHERE, LET<->WITH projection, RETURN/WITH ALL identity, NEXT identity, conditional branch swap, or predicate partitioning.\n"
        )
    if profile == "neo4j_semantic":
        return (
            "GENERATION PROFILE: neo4j_semantic\n"
            "- Target Neo4j current/Cypher 25 semantics rather than cross-DB portability.\n"
            "- When the testcase uses Cypher 25-only syntax or compares Cypher language behavior, explicitly prefix the main query with `CYPHER 25`.\n"
            "- You may use Neo4j-specific features only when they are present in the selected features or official fragments.\n"
            "- Prefer Cypher 25 semantic-bug-rich interactions: scope boundaries, conditional branches, NEXT row flow, LET/FILTER, FOR, RETURN/WITH ALL, type predicates, IS LABELED, PROPERTY_EXISTS, vector/SEARCH, coll.* and string.* functions, temporal format(), and path modes such as ACYCLIC or REPEATABLE ELEMENTS.\n"
            "- Do not generate removed Cypher 5 forms as positive tests: `SET n = r`, `SET n += r`, `SET r = n`, `SET r += n`, same-clause MERGE property references, indexProvider options, removed database options, or removed vector procedures.\n"
            "- If you intentionally test a removed Cypher 5 form, mark it clearly in notes as a negative test and include the Cypher 25 rewrite idea.\n"
            "- Prefer `properties(x)` when copying properties from a node or relationship in SET.\n"
            "- Prefer `CREATE VECTOR INDEX` plus `SEARCH` over deprecated vector query procedures.\n"
            "- Keep the case oracleable: tiny deterministic setup, scalar or small ordered table output, complete ORDER BY before LIMIT, and no global engine state.\n"
            "- In notes, name the primary semantic edge and one or two control-query ideas that would help recheck it.\n"
        )
    if profile == "neo4j_engine_stress":
        return (
            "GENERATION PROFILE: neo4j_engine_stress\n"
            "- Target Neo4j engine-stress bug hunting, not cross-DB portability and not pretty application queries.\n"
            "- Generate legal or near-boundary Neo4j Cypher that should either execute or return a normal ClientError; useful failures are UnknownError, ExecutionFailed, ClassCast, assertion-like errors, hangs, or connection drops.\n"
            "- This profile intentionally allows schema DDL in setup: CREATE/DROP CONSTRAINT and CREATE/DROP INDEX are allowed when they interact with the main write query.\n"
            "- Strongly prefer Dinkel-style interaction families: relationship property uniqueness constraints, path-binding MERGE/CREATE (`MERGE p = (...)`), dense write patterns, `MERGE ... ON CREATE/ON MATCH SET`, property-map replacement/merge, multi-label nodes, repeated relationship types, temporal/point/extreme numeric literals, and tiny schema state.\n"
            "- At least two engine-stress ingredients should appear in the testcase: schema DDL, relationship constraint, path-binding write, ON CREATE/ON MATCH side effects, null entity side effect, extreme literal, temporal/point literal, or multi-label dense pattern.\n"
            "- Keep setup small but engine-relevant: prefer 1 to 3 setup statements, including one schema statement when possible, followed by one main write query that stresses the planner/runtime.\n"
            "- Avoid APOC/GDS, LOAD CSV, admin/user/database commands, random functions, id()/elementId(), and non-deterministic time-now functions.\n"
            "- Prefer generated labels/types/properties that are concrete but compact, e.g. `A`, `B`, `R`, `S`, `k`, `v`, `ts`, `pt`.\n"
            "- It is acceptable for the main query to be ugly if it is syntactically well-formed and the expected behavior is execution or a normal ClientError rather than an internal DB error.\n"
            "- In notes, explicitly list the Dinkel-inspired ingredients and the expected normal behavior/error tier.\n"
        )
    if profile == "neo4j_internal_tier":
        return (
            "GENERATION PROFILE: neo4j_internal_tier\n"
            "- Target legal or near-boundary Neo4j inputs that should either execute or produce a normal client error.\n"
            "- Do not intentionally generate invalid syntax, unsupported features, admin commands, or extension calls.\n"
            "- Favor wrong-error-tier surfaces such as type validation, variable scope, branch typing, vector/list boundaries, and path boundary values.\n"
            "- A useful case is one that might expose UnknownError, ExecutionFailed, ClassCast, timeout, or connection drop for an otherwise well-formed semantic boundary.\n"
        )
    return (
        "GENERATION PROFILE: portable_crossdb\n"
        "- Generate portable Cypher suitable for Neo4j-vs-Memgraph differential testing.\n"
        "- Do not prefix queries with `CYPHER 5` or `CYPHER 25`; other engines should not be forced to parse Neo4j language-version directives.\n"
        "- Avoid Neo4j-only Cypher 25 constructs even if they appear in nearby official fragments: NEXT, LET, FILTER, WHEN/ELSE conditional queries, FOR, CALL (...) scoped subqueries, expression subqueries `COUNT {}`/`COLLECT {}`/`EXISTS {}`, type predicates `IS ::`, IS LABELED, PROPERTY_EXISTS, RETURN/WITH ALL, ACYCLIC, REPEATABLE ELEMENTS, DIFFERENT RELATIONSHIPS, VECTOR, SEARCH, coll.*, string.*, format(), allReduce(), and GQL alias functions such as duration_between().\n"
        "- If the selected feature is Neo4j-only, rewrite toward a broadly portable equivalent or skip that feature rather than emitting nonportable syntax.\n"
        "- Prefer scalar outputs and deterministic ORDER BY tie-breakers so cross-engine results are comparable.\n"
    )


def _diversity_profile_guidance(diversity_profile: str | None, variant_index: int) -> str:
    profile = (diversity_profile or "balanced").strip().lower()
    axes = [
        "null and OPTIONAL row preservation",
        "scope and alias visibility",
        "branch order or branch result typing",
        "aggregation/grouping boundaries",
        "path cardinality and repeated-element behavior",
        "list/map/vector type boundaries",
        "write-then-read visibility",
        "ORDER BY/LIMIT with complete deterministic tie-breakers",
    ]
    selected = axes[variant_index % len(axes)]
    if profile == "semantic_edges":
        return (
            "DIVERSITY PROFILE: semantic_edges\n"
            f"- For this variant, emphasize: {selected}.\n"
            "- The final main_query must include at least one explicit semantic-edge construct: CALL subquery/scope, conditional WHEN/ELSE, NEXT, LET, FILTER, type predicate `IS ::`, vector function, path mode/variable-length path, or aggregation.\n"
            "- A plain MATCH/WHERE/RETURN query with only ordering is not acceptable for this profile.\n"
            "- Avoid repeating the same WITH + OPTIONAL MATCH + count pattern unless that is the selected edge.\n"
        )
    if profile == "boundary_heavy":
        return (
            "DIVERSITY PROFILE: boundary_heavy\n"
            f"- For this variant, emphasize: {selected}.\n"
            "- The final main_query must include at least one explicit semantic-edge construct: CALL subquery/scope, conditional WHEN/ELSE, NEXT, LET, FILTER, type predicate `IS ::`, vector function, path mode/variable-length path, or aggregation.\n"
            "- Prefer tiny boundary values: empty lists, singleton lists, zero or duplicate values, null/missing properties, branch order swaps, and mixed but documented value types.\n"
            "- Keep expected row order and result shape deterministic despite using boundary values.\n"
        )
    if profile == "engine_stress":
        axes = [
            "schema constraint plus write planner interaction",
            "path-binding MERGE/CREATE plus ON CREATE/ON MATCH side effects",
            "dense multi-label write patterns plus temporal/point/extreme literals",
            "null entity side effects as a wrong-error-tier boundary",
            "property-map replacement/merge across node or relationship variables",
            "relationship uniqueness constraint plus conflicting or matching MERGE pattern",
        ]
        selected = axes[variant_index % len(axes)]
        return (
            "DIVERSITY PROFILE: engine_stress\n"
            f"- For this variant, emphasize: {selected}.\n"
            "- The final testcase must include at least two engine-stress ingredients: schema DDL, relationship constraint, path-binding write, ON CREATE/ON MATCH side effect, null entity side effect, extreme literal, temporal/point literal, or multi-label dense pattern.\n"
            "- Prefer a short sequence of setup statements and one main write query over a long read-only query.\n"
            "- Do not sanitize the query into a simple Person/KNOWS read example.\n"
        )
    return (
        "DIVERSITY PROFILE: balanced\n"
        f"- For this variant, emphasize: {selected} while keeping the query compact and executable.\n"
    )


def _interaction_knowledge_guidance(
    generation_profile: str | None,
    complexity_profile: str | None,
    diversity_profile: str | None,
) -> str:
    gen_profile = (generation_profile or "portable_crossdb").strip().lower()
    comp_profile = (complexity_profile or "default").strip().lower()
    div_profile = (diversity_profile or "balanced").strip().lower()
    if gen_profile == "portable_crossdb" and comp_profile == "default" and div_profile == "balanced":
        return ""

    if gen_profile == "neo4j_engine_stress":
        return (
            "NEO4J ENGINE-STRESS KNOWLEDGE:\n"
            "- The goal is to hit planner/runtime/error-tier bugs, especially around schema state plus write planning.\n"
            "- Use Dinkel-inspired ingredients deliberately rather than randomly: relationship uniqueness constraints; path-binding MERGE/CREATE; dense connected patterns; repeated labels/types; ON CREATE/ON MATCH SET; SET n = {...} or SET n += {...}; null entity expressions as wrong-error-tier boundaries; temporal/point/extreme numeric values.\n"
            "- Productive recipe A: setup creates a relationship uniqueness constraint, then main_query performs a MERGE pattern with two relationships of that type and a constrained property.\n"
            "- Productive recipe B: setup creates a tiny graph and schema, then main_query uses `MERGE p = (...) ON CREATE SET ... ON MATCH SET ...` with dense labels and relationship properties.\n"
            "- Productive recipe C: main_query binds a path in CREATE/MERGE, carries relationship variables into SET/REMOVE/DELETE, and includes a normal-error-tier boundary such as null entity property access.\n"
            "- Keep generated values concrete and deterministic. Use extreme literals sparingly but intentionally: `9223372036854775807`, `4.9E-324`, `1.7976931348623157E308`, `date('2000-01-01')`, `localdatetime('2015-06-24T12:50:35.556')`, and `point({x: 1, y: 2})`.\n"
            "- Avoid global-state exhaustion: use at most two schema objects per testcase and compact labels/types.\n"
            "- In notes, identify whether the expected normal outcome is success or a normal ClientError.\n"
        )

    if gen_profile == "neo4j_cypher25_metamorphic":
        return (
            "CYPHER 25 METAMORPHIC KNOWLEDGE:\n"
            "- The query should be designed for self-diff: another equivalent query can be generated mechanically and compared on the same Neo4j version.\n"
            "- Favor read-only clause interactions where equivalent forms are documented: `FILTER p` and `WITH * WHERE p`; `LET x = expr` and `WITH *, expr AS x`; `RETURN x` and `RETURN ALL x`; `WITH x` and `WITH ALL x`; `RETURN expr AS x NEXT RETURN x`; `MATCH ... WHERE p` and `MATCH ... FILTER p`.\n"
            "- Conditional query targets are especially valuable: use `WHEN cond THEN RETURN expr1 AS a ELSE RETURN expr2 AS a`, then guard with `FILTER a IS :: TYPE` before a type-specific operation. Branch order should not change the semantics after a type guard.\n"
            "- Type narrowing targets are especially valuable: combine mixed branch value types such as LIST/MAP/STRING/VECTOR, then apply `IS :: LIST<INTEGER>`, `IS :: STRING`, or `IS :: VECTOR` before a matching function.\n"
            "- Keep outputs oracleable: return scalars, counts, sizes, sorted names, or normalized type strings rather than raw nodes/paths when possible.\n"
            "- If a setup graph is needed, use 2 to 5 nodes and 1 to 4 relationships with simple labels such as Person, City, Item, A, B and properties id/name/score/age.\n"
        )

    portable_note = ""
    if gen_profile == "portable_crossdb":
        portable_note = (
            "- Because this is still a portable_crossdb run, use only broadly portable axes: "
            "scope/WITH, OPTIONAL/null/aggregation, path cardinality, UNION projection, "
            "ORDER BY/LIMIT, and write-read visibility. Avoid Neo4j-only NEXT, LET, FILTER, "
            "CALL scope, Cypher 25 conditional queries, and vector functions unless explicitly grounded.\n"
        )

    return (
        "NEO4J INTERACTION KNOWLEDGE:\n"
        "- Use the interaction knowledge base conceptually: configs/neo4j_interaction_knowledge_v1.json.\n"
        "- Do not merely lengthen the query. Expand by combining 2 to 4 semantic interaction axes in one execution flow.\n"
        "- High-yield axes: scope/alias boundaries; row flow via WITH/UNWIND/NEXT/FILTER; branch result typing; "
        "OPTIONAL/null/aggregation; path cardinality/path modes; list/map/vector type boundaries; "
        "write-read visibility; UNION projection shape; no-op metamorphic perturbations.\n"
        "- Prefer one high-yield recipe: scope sandwich, branch type guard, path aggregate rematch, "
        "write subquery visibility, UNION no-op slot, or NEXT/FILTER equivalence.\n"
        "- For every extra clause, make it affect row flow, scope, grouping, typing, write visibility, or final result shape.\n"
        "- Prefer Dinkel-style micro-interactions: short-to-medium query, high planner/runtime interaction density, "
        "and clear equivalent/control rewrite ideas.\n"
        "- In notes, name the chosen interaction axes and one control or metamorphic rewrite that would help recheck the case.\n"
        f"{portable_note}"
    )


def _quality_error_avoidance_guidance(errors: List[str]) -> str:
    if not errors:
        return ""
    lines = [
        "QUALITY RETRY GUIDANCE:",
        "- Rewrite the testcase from scratch to satisfy the quality gates below.",
        "- Keep the same overall feature intent, but do not preserve invalid syntax or weak interactions.",
    ]
    for error in errors:
        if error == "quality_gate:missing_semantic_edge_feature":
            lines.extend(
                [
                    "- The previous query lacked a required semantic edge.",
                    "- Include at least one explicit semantic-edge feature: CALL subquery/scope, conditional WHEN/ELSE, NEXT, LET, FILTER, type predicate `IS ::`, vector function, path mode/variable-length path, or aggregation.",
                    "- Prefer combining that edge with another interaction axis such as WITH scope, OPTIONAL/null behavior, ORDER BY/LIMIT, UNION, or write-read visibility.",
                ]
            )
        elif error == "quality_gate:non_property_value_map_assignment":
            lines.extend(
                [
                    "- The previous testcase stored a map or list-of-map as a node/relationship property.",
                    "- Use only primitive property values or arrays of primitive values in CREATE/MERGE/SET.",
                    "- If a map is useful, keep it as a RETURN/WITH value rather than storing it as a property.",
                ]
            )
        elif error == "quality_gate:call_subquery_returns_imported_variable_name":
            lines.extend(
                [
                    "- The previous CALL subquery returned an imported outer variable with the same name.",
                    "- Use scoped CALL such as `CALL (p, x) { ... }`; do not put an initial `WITH p, x` import line inside the body.",
                    "- Return only newly computed values, or alias imported values to fresh names.",
                    "- Bad: `CALL (p, xs) { WITH p, xs RETURN p.name AS name, xs } RETURN name, xs`.",
                    "- Good: `CALL (p, xs) { RETURN p.name AS name_out, xs AS xs_out } RETURN name_out AS name, xs_out AS xs`.",
                ]
            )
        elif error == "quality_gate:deprecated_call_import_style":
            lines.extend(
                [
                    "- The previous query used deprecated `CALL { WITH ... }` import style.",
                    "- Rewrite it using a scoped subquery such as `CALL (p, x) { ... }` or `CALL (*) { ... }`.",
                    "- Do not include an initial `WITH p, x` import line inside the CALL body when using scoped CALL.",
                ]
            )
        elif error == "quality_gate:limit_without_order_by":
            lines.append("- Add complete deterministic ORDER BY keys before every LIMIT/SKIP.")
        elif error == "quality_gate:nondeterministic_function":
            lines.append("- Remove nondeterministic functions such as rand(), randomUUID(), timestamp(), or zero-argument temporal now functions.")
        elif error == "quality_gate:duplicate_query_fingerprint":
            lines.append("- Produce a structurally different query, not just renamed variables or changed literals.")
        elif error == "quality_gate:nonportable_neo4j_feature":
            lines.extend(
                [
                    "- For portable_crossdb mode, remove Neo4j-only Cypher 25 syntax and use broadly portable Cypher.",
                    "- Avoid language-version prefixes, NEXT, LET, FILTER, WHEN/ELSE conditional queries, FOR, CALL (...) scoped subqueries, expression subqueries, type predicates, vector/SEARCH, coll.*, string.*, and explicit path modes.",
                ]
            )
        elif error == "quality_gate:cypher25_removed_or_deprecated_construct":
            lines.extend(
                [
                    "- The previous testcase used a construct removed or deprecated for Cypher 25 positive tests.",
                    "- Rewrite `SET n = r` / `SET n += r` / `SET r = n` / `SET r += n` as `SET ... = properties(...)` or `SET ... += properties(...)`.",
                    "- Avoid same-clause MERGE property references; bind the value with WITH before MERGE.",
                    "- Avoid indexProvider options and deprecated vector procedures; prefer native Cypher 25 forms.",
                ]
            )
        elif error == "quality_gate:missing_cypher25_prefix":
            lines.extend(
                [
                    "- This run targets Cypher 25 positive tests.",
                    "- Prefix the main query with `CYPHER 25`.",
                    "- Do not use `CYPHER 5` unless explicitly building a comparison case; for this lane, rewrite to Cypher 25.",
                ]
            )
        elif error == "quality_gate:unsupported_nullable_type_predicate_marker":
            lines.extend(
                [
                    "- The previous testcase used an unsupported nullable type predicate marker such as `LIST<STRING>?`.",
                    "- Remove the `?` marker and use non-null type predicates such as `IS :: LIST<STRING>`, `IS :: LIST<INTEGER>`, `IS :: STRING`, or `IS :: VECTOR`.",
                    "- If null rows are relevant, filter them explicitly with `value IS NOT NULL` before the type predicate.",
                ]
            )
        elif error == "quality_gate:use_return_before_next_conditional":
            lines.extend(
                [
                    "- The previous testcase used `WITH ... NEXT WHEN ...`, which can lose variables before the conditional query.",
                    "- Rewrite the row-producing part as `RETURN alias1, alias2 NEXT WHEN ...` before the conditional query.",
                    "- Keep aliases simple and reference only those aliases inside the WHEN/ELSE branches.",
                ]
            )
        elif error == "quality_gate:invalid_else_when_conditional_query":
            lines.extend(
                [
                    "- The previous testcase used invalid `ELSE WHEN ...` conditional-query syntax.",
                    "- Use repeated `WHEN condition THEN ...` branches and then one final `ELSE ...` branch; do not prefix later WHEN branches with ELSE.",
                ]
            )
        elif error == "quality_gate:let_self_redeclares_existing_variable":
            lines.extend(
                [
                    "- The previous testcase used a useless redeclaration such as `LET x = x`, which can be rejected because `x` already exists.",
                    "- Either remove that LET clause or assign the expression to a fresh alias, for example `LET x2 = x + 1`.",
                ]
            )
        elif error == "quality_gate:conditional_next_references_unreturned_variable":
            lines.extend(
                [
                    "- The previous testcase referenced a dotted variable such as `p.age` after `NEXT`, but the variable `p` was not exported by the preceding RETURN.",
                    "- If a conditional branch needs `p.age`, use `RETURN p, p.name AS name NEXT WHEN p.age > ...`; otherwise rewrite the condition to use an exported alias such as `age`.",
                ]
            )
        else:
            lines.append(f"- Avoid this rejected pattern: {error}.")
    return "\n".join(lines) + "\n"


def _ensure_cypher25_main_query(case: Dict[str, Any], generation_profile: str | None) -> Dict[str, Any]:
    if (generation_profile or "").strip().lower() not in {"neo4j_cypher25", "neo4j_cypher25_metamorphic"}:
        return case
    main_query = str(case.get("main_query") or "").strip()
    if not main_query:
        return case
    if re.match(r"(?is)^CYPHER\s+25\b", main_query):
        case["main_query"] = main_query
        return case
    if re.match(r"(?is)^CYPHER\s+5\b", main_query):
        case["main_query"] = re.sub(r"(?is)^CYPHER\s+5\b", "CYPHER 25", main_query, count=1)
        return case
    case["main_query"] = "CYPHER 25\n" + main_query
    return case


def _format_fragment_groups(fragment_groups: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for group in fragment_groups or []:
        # Guard old Stage2/cache records as well as newly retrieved fragments.
        items = [item for item in (group.get('items') or [])
                 if not isinstance(item, dict) or not is_documented_invalid_example(item)]
        if not items:
            continue
        lines.append(f"[{group.get('group_type', 'group')}]")
        for item in items[:5]:
            text = item.get('text') if isinstance(item, dict) else str(item)
            lines.append(f"- {normalize_ws(str(text))}")
    return "\n".join(lines)


def _complexity_guidance(generation_strategy: str, complexity_profile: str | None = None) -> str:
    profile = (complexity_profile or "default").strip().lower()
    base = (
        "COMPLEXITY GUIDANCE:\n"
        "- Prefer a query with at least 3 meaningful clauses when the features allow it.\n"
        "- Prefer at least one WITH clause if it helps feature interaction.\n"
        "- Prefer semantic interaction over filler length.\n"
        "- Good sources of useful complexity include OPTIONAL MATCH + aggregation, CALL + WITH, UNION + projection, path patterns + aggregation, and ORDER BY/LIMIT + grouping.\n"
        "- Avoid trivial single-pattern queries unless the feature set clearly only supports a simple query.\n"
    )
    if generation_strategy == "feature-fragment-only":
        strategy_guidance = (
            "- In feature-fragment-only mode, prefer structurally rich queries that still remain close to retrieved fragment semantics.\n"
        )
    elif generation_strategy in {"feature-driven", "feature-driven-soft"}:
        strategy_guidance = (
            "- In feature-driven modes, make the chosen features interact in the same execution flow rather than appearing in isolated clauses.\n"
        )
    else:
        strategy_guidance = (
            "- In skeleton mode, increase complexity mainly by filling and connecting the existing skeleton structure rather than inventing unrelated clauses.\n"
        )

    if profile == "large_neo4j":
        profile_guidance = (
            "- This run is targeting larger Neo4j-facing queries. Prefer 8 to 14 meaningful clauses when the feature set supports it.\n"
            "- Prefer at least 2 WITH boundaries.\n"
            "- Prefer at least one subquery-shaped construct such as CALL, EXISTS { ... }, COUNT { ... }, or COLLECT { ... }.\n"
            "- Prefer at least one path-oriented construct plus one aggregation or DISTINCT/ORDER BY/LIMIT interaction.\n"
            "- Prefer one write->read or write->rematch interaction when it remains executable.\n"
            "- Prefer setup queries with richer graph shape: fan-out, short chains, triangles, duplicate property values, and some missing properties.\n"
            "- Do not pad with meaningless repeated clauses; every extra clause should affect row flow, scope, grouping, or visible results.\n"
        )
    elif profile == "planner_stress_neo4j":
        profile_guidance = (
            "- This run is targeting large Neo4j planner/runtime stress queries. Prefer 10 to 16 meaningful clauses when feasible.\n"
            "- Prefer multiple MATCH / OPTIONAL MATCH segments that are later joined through WITH, aggregation, or rematching.\n"
            "- Prefer at least 2 WITH boundaries and at least one DISTINCT, ORDER BY, or LIMIT that changes row shape.\n"
            "- Prefer at least one subquery-shaped construct such as CALL, EXISTS { ... }, COUNT { ... }, or COLLECT { ... }.\n"
            "- Prefer repeated rematching of earlier aliases, path-derived values, or post-write visibility checks.\n"
            "- Prefer setup queries with 20 or more entities only when the query actually uses that extra structure.\n"
            "- Still keep the query semantically coherent and executable; avoid random filler complexity.\n"
        )
    elif profile == "engine_stress_neo4j":
        profile_guidance = (
            "- This run is targeting Neo4j engine-stress writes. Prefer 3 to 8 meaningful clauses/statements rather than long read pipelines.\n"
            "- Prefer one schema setup statement plus one dense write main query.\n"
            "- Prefer compact but high-interaction write patterns: relationship uniqueness constraint + MERGE, path binding + ON CREATE/ON MATCH, or property-map replacement + dense labels/types.\n"
            "- Do not add filler MATCH/WITH clauses unless they affect schema/write planning or error-tier behavior.\n"
        )
    return base + (
        strategy_guidance
        + profile_guidance if profile in {"large_neo4j", "planner_stress_neo4j", "engine_stress_neo4j"} else strategy_guidance
    )


DIALECT_FILTER_PATTERNS = [
    ("admin_or_planner", re.compile(r"\b(PROFILE|EXPLAIN|SHOW)\b", re.IGNORECASE)),
    ("schema_or_catalog", re.compile(r"\b(CREATE|DROP)\s+(INDEX|CONSTRAINT|DATABASE|USER|ROLE)\b|\bdb\.", re.IGNORECASE)),
    ("extension_or_external_resource", re.compile(r"\b(apoc|gds)\.|\bLOAD\s+CSV\b|file://", re.IGNORECASE)),
    ("expression_subquery", re.compile(r"\b(COUNT|COLLECT|EXISTS)\s*\{", re.IGNORECASE)),
    ("gql_type_predicate", re.compile(r"\bIS\s*::", re.IGNORECASE)),
    ("pattern_internal_where", re.compile(r"\[[^\]]+\s+WHERE\s+", re.IGNORECASE)),
    ("path_quantifier_sugar", re.compile(r"(<-|->|-)\+")),
    ("internal_or_nondeterministic_value", re.compile(r"\b(id|elementId|rand|randomUUID|timestamp|datetime|localdatetime)\s*\(", re.IGNORECASE)),
    ("return_star", re.compile(r"\bRETURN\s+\*", re.IGNORECASE)),
    ("parameter_reference", re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")),
    ("collect_order_by_syntax", re.compile(r"\bcollect\s*\([^)]*\bORDER\s+BY\b", re.IGNORECASE)),
    (
        "order_limit_before_later_match",
        re.compile(
            r"\b(?:MATCH|WITH)\b[\s\S]{0,240}\bORDER\s+BY\b[\s\S]{0,240}\bLIMIT\b[\s\S]{0,240}\bMATCH\b",
            re.IGNORECASE,
        ),
    ),
    ("scoped_call_parentheses", re.compile(r"\bCALL\s*\([^)]*\)\s*\{", re.IGNORECASE)),
    ("raw_return_value", re.compile(r"\bRETURN\b[^;]*(?:\bpath\b|\{\.?\*\}|friend\s*\{|p\s*\{)", re.IGNORECASE)),
]


def _dialect_filter_violations(case: Dict[str, Any]) -> List[str]:
    text = "\n".join(
        [str(query) for query in case.get("setup_queries", []) or []]
        + [str(case.get("main_query") or "")]
        + [str(query) for query in case.get("cleanup_queries", []) or []]
    )
    return [name for name, pattern in DIALECT_FILTER_PATTERNS if pattern.search(text)]


def _merge_dialect_validation(validation: Dict[str, Any], violations: List[str]) -> Dict[str, Any]:
    merged = dict(validation)
    errors = list(merged.get("errors") or [])
    errors.extend(f"dialect_filter:{violation}" for violation in violations)
    merged["errors"] = errors
    merged["is_valid"] = bool(merged.get("is_valid")) and not violations
    return merged


def heuristic_instantiate(
    record: Dict[str, Any],
    variant_index: int = 0,
    total_variants: int = 1,
    family_bias: str | None = None,
    complexity_profile: str | None = None,
    generation_profile: str | None = None,
    diversity_profile: str | None = None,
) -> Dict[str, Any]:
    skeleton = normalize_ws(str(record.get("skeleton_text") or ""))
    main_query = skeleton
    replacements = {
        "NODE_LABEL_0": "Person",
        "REL_TYPE_0": "KNOWS",
        "LEN_0": "4",
        "CONST_0": "26",
        "RET_EXPR_0": "n",
        "AGG_EXPR_0": "count(*)",
    }
    for k, v in replacements.items():
        main_query = main_query.replace(k, v)
    setup = [
        "CREATE (:Person {name:'A', age:26})-[:KNOWS]->(:Person {name:'B', age:27})"
    ] if "Person" in main_query or "KNOWS" in main_query else []
    case = {
        "query_id": record.get("query_id"),
        "source_id": record.get("source_id"),
        "prepared_source_name": record.get("prepared_source_name"),
        "prepared_source_path": record.get("prepared_source_path"),
        "skeleton_id": record.get("skeleton_id"),
        "setup_queries": setup,
        "main_query": main_query,
        "cleanup_queries": [],
        "used_group_types": [g.get("group_type") for g in record.get("fragment_groups", [])],
        "notes": "heuristic instantiation",
        "generation_method": "heuristic",
        "model": None,
        "variant_index": variant_index,
        "total_variants": total_variants,
        "family_bias": family_bias,
        "complexity_profile": complexity_profile or "default",
        "generation_profile": generation_profile or "portable_crossdb",
        "diversity_profile": diversity_profile or "balanced",
        "feature_prompt_format": FEATURE_PROMPT_FORMAT,
    }
    case = postprocess_case(case)
    case = _ensure_cypher25_main_query(case, generation_profile)
    case["case_id"] = stable_case_id(str(record.get("query_id")), case["main_query"], variant_index=variant_index)
    case["validation"] = validate_case(case)
    return case


def build_instantiation_user_prompt(
    record: Dict[str, Any],
    variant_index: int = 0,
    total_variants: int = 1,
    family_bias: str | None = None,
    complexity_profile: str | None = None,
    generation_profile: str | None = None,
    diversity_profile: str | None = None,
) -> Dict[str, Any]:
    generation_strategy = str(record.get("generation_strategy") or "skeleton")
    guidance_skeleton = str(record.get("guidance_skeleton_text") or record.get("skeleton_text") or "")
    skeleton_text = str(record.get("skeleton_text") or "")
    features_block = _format_feature_items(record.get("feature_items", []) or [])
    dialect_filter_guidance = normalize_ws(str(record.get("dialect_filter_guidance") or ""))
    quality_retry_errors = [str(err) for err in record.get("stage3_quality_retry_errors", []) or []]
    dialect_filter_block = (
        "DIALECT FILTER GUIDANCE:\n"
        f"- {dialect_filter_guidance}\n\n"
        if dialect_filter_guidance
        else ""
    )
    if generation_strategy == "feature-fragment-only":
        strategy_block = (
            "GENERATION STRATEGY: feature-fragment-only\n"
            "- Ignore the skeleton completely.\n"
            "- Generate one semantically correct executable Cypher query using only the listed features and retrieved fragments as guidance.\n"
            "- The final query should include all listed features and should be grounded in the retrieved fragments when choosing syntax and semantics.\n"
            "- Prefer feature interaction over unrelated feature decoration.\n"
            "- Avoid management statements, administration commands, query-planning commands, or extension-only callables unless the retrieved fragments clearly require them.\n"
            "- Avoid PROFILE, EXPLAIN, SHOW, db.*, apoc.*, gds.*, CREATE/DROP INDEX, CREATE/DROP CONSTRAINT, CREATE/DROP DATABASE, CREATE USER/ROLE, LOAD CSV, and replication/backup commands.\n"
            "- Prefer portable Cypher constructs that are likely to execute on both Neo4j and Memgraph.\n"
        )
    elif generation_strategy == "feature-driven":
        strategy_block = (
            "GENERATION STRATEGY: feature-driven\n"
            "- Do not treat any skeleton as binding.\n"
            "- Generate one semantically correct executable Cypher query that includes all listed features.\n"
            "- Prefer a compact but non-trivial query where the features interact, rather than a textbook trivial query.\n"
        )
    elif generation_strategy == "feature-driven-soft":
        strategy_block = (
            "GENERATION STRATEGY: feature-driven-soft\n"
            "- Use the guidance skeleton only as inspiration.\n"
            "- You may change the structure if needed to produce a semantically correct query that includes all listed features.\n"
            "- Prefer preserving some shape from the guidance skeleton when it does not conflict with the feature set.\n"
        )
    else:
        strategy_block = (
            "GENERATION STRATEGY: skeleton\n"
            "- Use the skeleton as the primary structural guide.\n"
            "- Preserve the skeleton shape as much as possible while making the query executable.\n"
        )
    user_prompt = (
        "Instantiate one executable Cypher testcase from the given skeleton, features, and retrieved fragments. Return JSON only.\n\n"
        "Hard requirements:\n"
        "- Do not leave any placeholder tokens in the output. No HOLE_* tokens are allowed anywhere.\n"
        "- Replace labels, relationship types, property names, numbers, strings, and parameters with concrete executable values.\n"
        "- For read queries or path-dependent queries, include setup_queries that create the minimal required graph data.\n"
        "- For metadata queries, include setup only if the object must exist before the main query.\n"
        "- If you cannot instantiate all placeholders, set main_query to an empty string and explain why in notes.\n\n"
        "Noise-avoidance requirements:\n"
        "- Do not emit PROFILE or EXPLAIN.\n"
        "- Do not emit db.*, apoc.*, gds.*, SHOW, CREATE/DROP INDEX, CREATE/DROP CONSTRAINT, CREATE/DROP DATABASE, CREATE USER, CREATE ROLE, LOAD CSV, replica.*, snapshot, dump, or backup commands unless the generation strategy explicitly demands them.\n"
        "- Prefer query semantics over administration or tooling features.\n\n"
        f"{strategy_block}\n"
        f"{_generation_profile_guidance(generation_profile)}\n"
        f"{_diversity_profile_guidance(diversity_profile, variant_index)}\n"
        f"{_interaction_knowledge_guidance(generation_profile, complexity_profile, diversity_profile)}\n"
        f"{_quality_error_avoidance_guidance(quality_retry_errors)}\n"
        f"{dialect_filter_block}"
        f"COMPLEXITY_PROFILE: {complexity_profile or 'default'}\n"
        f"{_complexity_guidance(generation_strategy, complexity_profile=complexity_profile)}\n"
        f"VARIANT_INDEX: {variant_index + 1} of {total_variants}\n"
        f"VARIANT_FOCUS: {_variant_focus(variant_index, family_bias=family_bias)}\n"
        "- Generate a distinct valid variant for this input instead of repeating the most obvious shape.\n\n"
        f"QUERY_ID: {record.get('query_id')}\n"
        f"SKELETON_ID: {record.get('skeleton_id')}\n"
        f"SKELETON:\n{skeleton_text}\n\n"
        f"GUIDANCE_SKELETON:\n{guidance_skeleton}\n\n"
        f"FEATURES:\n{features_block}\n\n"
        f"FRAGMENT_GROUPS:\n{_format_fragment_groups(record.get('fragment_groups', []))}\n"
    )
    return user_prompt


def llm_instantiate(
    record: Dict[str, Any],
    client: BaseModelClient,
    system_prompt: str,
    model_name: str | None = None,
    variant_index: int = 0,
    total_variants: int = 1,
    family_bias: str | None = None,
    complexity_profile: str | None = None,
    generation_profile: str | None = None,
    diversity_profile: str | None = None,
) -> Dict[str, Any]:
    generation_strategy = str(record.get("generation_strategy") or "skeleton")
    user_prompt = build_instantiation_user_prompt(
        record,
        variant_index=variant_index,
        total_variants=total_variants,
        family_bias=family_bias,
        complexity_profile=complexity_profile,
        generation_profile=generation_profile,
        diversity_profile=diversity_profile,
    )
    dialect_filter_guidance = normalize_ws(str(record.get("dialect_filter_guidance") or ""))
    retry_limit = 0
    if dialect_filter_guidance:
        retry_limit = int(record.get("dialect_filter_retry_limit", 2) or 0)
    retry_limit = max(retry_limit, int(record.get("quality_retry_limit", 0) or 0))

    rejection_reasons: List[str] = []
    last_case: Dict[str, Any] | None = None
    for attempt in range(retry_limit + 1):
        attempt_prompt = user_prompt
        if rejection_reasons:
            attempt_prompt += (
                "\n\nRETRY:\n"
                "- The previous attempt was rejected for these reasons: "
                + ", ".join(rejection_reasons)
                + ".\n"
                "- Rewrite the testcase from scratch.\n"
                "- Do not keep the rejected syntax by renaming variables; choose a semantically comparable but valid shape.\n"
                "- Return JSON only.\n"
                + _quality_error_avoidance_guidance(rejection_reasons)
            )
        data = client.chat_json(system_prompt=system_prompt, user_prompt=attempt_prompt)
        case = {
            "query_id": record.get("query_id"),
            "source_id": record.get("source_id"),
            "prepared_source_name": record.get("prepared_source_name"),
            "prepared_source_path": record.get("prepared_source_path"),
            "skeleton_id": record.get("skeleton_id"),
            "setup_queries": data.get("setup_queries", []),
            "main_query": data.get("main_query", ""),
            "cleanup_queries": data.get("cleanup_queries", []),
            "used_group_types": [g.get("group_type") for g in record.get("fragment_groups", [])],
            "notes": data.get("notes", ""),
            "generation_method": "llm",
            "model": model_name,
            "generation_strategy": generation_strategy,
            "variant_index": variant_index,
            "total_variants": total_variants,
            "family_bias": family_bias,
            "complexity_profile": complexity_profile or "default",
            "generation_profile": generation_profile or "portable_crossdb",
            "diversity_profile": diversity_profile or "balanced",
            "feature_prompt_format": FEATURE_PROMPT_FORMAT,
            "dialect_filter_retry_attempts": attempt,
        }
        case = postprocess_case(case)
        case = _ensure_cypher25_main_query(case, generation_profile)
        case["case_id"] = stable_case_id(str(record.get("query_id")), case["main_query"], variant_index=variant_index)
        validation = validate_case(case)
        violations = _dialect_filter_violations(case) if dialect_filter_guidance else []
        case["dialect_filter_violations"] = violations
        case["validation"] = _merge_dialect_validation(validation, violations) if violations else validation
        last_case = case

        if case["validation"].get("is_valid"):
            return case
        rejection_reasons = list(case["validation"].get("errors") or [])

    return last_case if last_case is not None else {
        "query_id": record.get("query_id"),
        "source_id": record.get("source_id"),
        "skeleton_id": record.get("skeleton_id"),
        "setup_queries": [],
        "main_query": "",
        "cleanup_queries": [],
        "used_group_types": [g.get("group_type") for g in record.get("fragment_groups", [])],
        "notes": "LLM instantiation failed before producing a case.",
        "generation_method": "llm",
        "model": model_name,
        "generation_strategy": generation_strategy,
        "variant_index": variant_index,
        "total_variants": total_variants,
        "family_bias": family_bias,
        "complexity_profile": complexity_profile or "default",
        "generation_profile": generation_profile or "portable_crossdb",
        "diversity_profile": diversity_profile or "balanced",
        "feature_prompt_format": FEATURE_PROMPT_FORMAT,
        "dialect_filter_retry_attempts": retry_limit,
        "dialect_filter_violations": ["no_case_produced"],
        "case_id": stable_case_id(str(record.get("query_id")), "", variant_index=variant_index),
        "validation": {"is_valid": False, "errors": ["empty_main_query", "dialect_filter:no_case_produced"]},
    }
