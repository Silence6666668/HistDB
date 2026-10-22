from __future__ import annotations

import hashlib
import math
import random
import re
from typing import Any, Dict, Iterable, List, Sequence

CLAUSE_PATTERN = re.compile(
    r"\b(OPTIONAL\s+MATCH|MATCH|UNWIND|WITH|WHERE|CALL|CREATE|MERGE|SET|DELETE|REMOVE|RETURN|ORDER\s+BY|SKIP|LIMIT|UNION)\b",
    re.IGNORECASE,
)

WRITE_CLAUSES = {"CREATE", "MERGE", "SET", "DELETE", "REMOVE"}


def _stable_rng(seed: int, query_id: str, salt: str = "") -> random.Random:
    basis = f"{seed}||{query_id}||{salt}"
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
    return random.Random(int(digest, 16))


def _skeleton_clauses(skeleton_record: Dict[str, Any]) -> List[str]:
    text = str(skeleton_record.get("skeleton_text") or "")
    clauses: List[str] = []
    for match in CLAUSE_PATTERN.finditer(text):
        clause = match.group(1).upper().replace("  ", " ")
        if not clauses or clauses[-1] != clause:
            clauses.append(clause)
    return clauses


def _hole_types(skeleton_record: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for hole in skeleton_record.get("holes", []) or []:
        hole_type = str(hole.get("type") or hole.get("name") or "").strip().lower()
        if hole_type:
            out.append(hole_type)
    return out


def score_skeleton_feature_affinity(
    skeleton_record: Dict[str, Any], feature_record: Dict[str, Any]
) -> int:
    clauses = _skeleton_clauses(skeleton_record)
    clause_tags = {c.lower().replace(" ", "_") for c in clauses}
    holes = _hole_types(skeleton_record)

    tags = {str(tag).strip().lower() for tag in feature_record.get("tags", []) or [] if str(tag).strip()}
    flags = feature_record.get("flags", {}) or {}
    entities = feature_record.get("entities", {}) or {}
    entity_clauses = {
        str(clause).strip().lower().replace(" ", "_")
        for clause in entities.get("clauses", []) or []
        if str(clause).strip()
    }

    score = 0
    score += len(clause_tags & tags) * 4
    score += len(clause_tags & entity_clauses) * 4

    if any(clause in WRITE_CLAUSES for clause in clauses):
        if bool(flags.get("is_write")) or "write" in tags:
            score += 6
    else:
        if not bool(flags.get("is_write")):
            score += 2

    if "OPTIONAL MATCH" in clauses:
        if bool(flags.get("has_optional_match")) or "optional_match" in tags:
            score += 6
        if "case" in tags or bool(flags.get("has_case")):
            score += 1

    if "CALL" in clauses and (bool(flags.get("has_subquery")) or "subquery" in tags):
        score += 6
    if "ORDER BY" in clauses and (bool(flags.get("has_order_by")) or "order_by" in tags):
        score += 5
    if any(clause in {"SKIP", "LIMIT"} for clause in clauses) and bool(flags.get("has_pagination")):
        score += 4
    if "WHERE" in clauses and ("predicate" in tags or "where" in tags):
        score += 2
    if any("path" in hole for hole in holes):
        if bool(flags.get("has_path_pattern")) or "path_pattern" in tags:
            score += 6
        if bool(flags.get("has_pattern_comprehension")) or "pattern_comprehension" in tags:
            score += 2
    if any("label" in hole or "rel" in hole for hole in holes) and ("match" in tags or "merge" in tags):
        score += 1
    if bool(flags.get("has_aggregation")) or "aggregation" in tags:
        if "RETURN" in clauses or "WITH" in clauses:
            score += 2
    return score


def _sample_without_replacement(
    rng: random.Random,
    records: Sequence[Dict[str, Any]],
    sample_size: int,
    weight_fn,
) -> List[Dict[str, Any]]:
    remaining = list(records)
    chosen: List[Dict[str, Any]] = []
    while remaining and len(chosen) < sample_size:
        weights = [max(float(weight_fn(rec)), 0.0) for rec in remaining]
        total = sum(weights)
        if total <= 0:
            idx = rng.randrange(len(remaining))
        else:
            threshold = rng.random() * total
            idx = 0
            running = 0.0
            for pos, weight in enumerate(weights):
                running += weight
                if running >= threshold:
                    idx = pos
                    break
        chosen.append(remaining.pop(idx))
    return chosen


def select_feature_records(
    skeleton_record: Dict[str, Any],
    query_features: Sequence[Dict[str, Any]],
    all_features: Sequence[Dict[str, Any]],
    mode: str,
    sample_size: int,
    seed: int,
    rng_salt: str | None = None,
    *,
    draw_index: int = 0,
    used_combinations: set[frozenset[str]] | None = None,
    max_resample_attempts: int = 256,
) -> List[Dict[str, Any]]:
    """Select features, optionally reserving an unused unordered ID set.

    Callers share ``used_combinations`` for the same skeleton within a run.
    Omitting it retains historical stateless sampling (including draw zero's
    original seed derivation). A generation retry reuses the returned records;
    only a new composition calls this function again.
    """
    if draw_index < 0:
        raise ValueError("draw_index must be nonnegative")
    salt = str(rng_salt or "")
    if draw_index:
        salt = f"{salt}||feature_draw={draw_index}"
    if used_combinations is not None:
        if mode not in {"random", "random_constrained"}:
            raise ValueError("Unique feature combinations require random or random_constrained selection")
        if sample_size <= 0:
            raise ValueError("Unique feature selection requires a positive sample_size")
        if max_resample_attempts <= 0:
            raise ValueError("max_resample_attempts must be positive")

        # Repeated library records must not occupy multiple feature slots.
        by_id: Dict[str, Dict[str, Any]] = {}
        for record in (all_features or query_features):
            feature_id = str(record.get("feature_id") or "").strip()
            if not feature_id:
                raise ValueError("Unique feature selection requires nonempty feature_id values")
            by_id.setdefault(feature_id, record)
        pool = list(by_id.values())
        scores = {
            feature_id: score_skeleton_feature_affinity(skeleton_record, record)
            for feature_id, record in by_id.items()
        }
        if mode == "random_constrained":
            positive = [record for feature_id, record in by_id.items() if scores[feature_id] > 0]
            # Preserve the existing all-zero fallback, not an exhaustion fallback.
            if positive:
                pool = positive
        if len(pool) < sample_size:
            raise ValueError(f"Feature candidate pool has {len(pool)} unique features; need {sample_size}")
        pool_ids = frozenset(str(record["feature_id"]).strip() for record in pool)
        used_here = sum(
            len(combo) == sample_size and combo <= pool_ids
            for combo in used_combinations
        )
        if used_here >= math.comb(len(pool), sample_size):
            raise ValueError(
                f"Feature combinations exhausted for skeleton {skeleton_record.get('skeleton_id') or skeleton_record.get('query_id')}: "
                f"all {used_here} unordered sets have been used"
            )

        rng = _stable_rng(seed, str(skeleton_record.get("query_id") or ""), salt=salt)
        for _ in range(max_resample_attempts):
            if mode == "random":
                shuffled = list(pool)
                rng.shuffle(shuffled)
                chosen = shuffled[:sample_size]
            else:
                chosen = _sample_without_replacement(
                    rng, pool, sample_size,
                    weight_fn=lambda record: scores[str(record["feature_id"]).strip()] + 1.0,
                )
            combination = frozenset(str(record["feature_id"]).strip() for record in chosen)
            if combination not in used_combinations:
                used_combinations.add(combination)
                return chosen
        # Do not silently repeat a set, broaden the pool, or claim exhaustion.
        raise ValueError(
            f"Unable to sample an unused feature combination after {max_resample_attempts} attempts; "
            "unused combinations remain, but resampling limit was reached"
        )

    if mode == "paired":
        return list(query_features[:sample_size]) if sample_size > 0 else list(query_features)

    query_id = str(skeleton_record.get("query_id") or "")
    rng = _stable_rng(seed, query_id, salt=salt)
    pool = list(all_features)
    if not pool:
        return list(query_features[:sample_size]) if sample_size > 0 else list(query_features)

    if mode == "random":
        rng.shuffle(pool)
        return pool[:sample_size]

    if mode == "random_constrained":
        scored = []
        for record in pool:
            score = score_skeleton_feature_affinity(skeleton_record, record)
            scored.append((score, record))
        compatible = [record for score, record in scored if score > 0]
        if not compatible:
            compatible = pool
        return _sample_without_replacement(
            rng,
            compatible,
            sample_size,
            weight_fn=lambda rec: score_skeleton_feature_affinity(skeleton_record, rec) + 1.0,
        )

    raise ValueError(f"Unsupported feature selection mode: {mode}")


def selected_feature_metadata(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    records = list(records)
    return {
        "selected_feature_ids": [str(rec.get("feature_id") or "") for rec in records],
        "selected_feature_query_ids": sorted(
            {str(rec.get("query_id") or "").strip() for rec in records if str(rec.get("query_id") or "").strip()}
        ),
    }
