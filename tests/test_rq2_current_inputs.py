from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path

import pytest

from src.stage2.build_fragment_corpus import build_fragment_corpus
from src.stage2.group_fragments import group_fragments
from src.stage2.retriever import LexicalRetriever


def _fixture(*, feature_count: int = 8, shared_skeleton: bool = False) -> dict:
    """Real Stage1/Stage2 record shapes; no LLM or DB dependencies."""
    skeletons = [
        {
            "query_id": query_id,
            "source_id": "report-a",
            "skeleton_id": "sk-shared" if shared_skeleton else f"sk-{query_id}",
            "skeleton_text": "MATCH (n:HOLE_LABEL_0) RETURN n",
            "query_text": "MATCH (n:Person) RETURN n",
            "holes": [{"name": "HOLE_LABEL_0", "type": "label", "original": ":Person"}],
        }
        for query_id in ("q1", "q2")
    ]
    base = [
        {**copy.deepcopy(row), "original_query_id": row["query_id"],
         "fragment_groups": [{"group_type": "other", "items": [{"text": "STALE RAG"}]}],
         "selected_feature_ids": ["stale"], "sample_source": "primary"}
        for row in skeletons
    ]
    features = [
        {
            "feature_id": f"f{i}", "query_id": f"history-{i}",
            "source_id": "report-a" if i < 3 else "report-b",
            "feature_name": f"feature_{i}", "feature_description": f"behavior {i}",
            "query_instance": "MATCH (n) RETURN n", "tags": ["match", "return"],
            "flags": {"is_write": False}, "entities": {"clauses": ["MATCH", "RETURN"]},
        }
        for i in range(feature_count)
    ]
    extras = [
        {
            "fragment_id": "doc-safe", "source_id": "cypher_manual",
            "source_type": "official_chunk", "text": "A feature behavior is a documented expression.",
            "tags": ["feature"], "metadata": {"url": "https://neo4j.com/docs/cypher-manual/current/"},
        },
        {
            "fragment_id": "doc-negative", "source_id": "cypher_manual",
            "source_type": "official_example",
            "text": "The following query is invalid. Feature behavior example.\n```cypher\nRETURN missing\n```",
            "tags": ["feature"], "metadata": {},
        },
    ]
    return dict(base_inputs=base, skeletons=skeletons, features=features, extra_fragments=extras)


def _builder(**overrides):
    try:
        module = importlib.import_module("scripts.rq2_current_inputs")
    except ModuleNotFoundError as exc:
        if exc.name != "scripts.rq2_current_inputs":
            raise
        pytest.fail("InputBuilder is not implemented")
    fixture = overrides.pop("fixture", None) or _fixture()
    return module.InputBuilder(**fixture, **overrides)


def test_four_arms_are_paired_and_only_s_adds_conditioned_soft_guidance() -> None:
    batch = _builder().make_batch(0, 2)

    assert set(batch) == {"W", "R", "C", "S"}
    ids = [row["query_id"] for row in batch["W"]]
    assert len(set(ids)) == 2
    for arm in batch:
        assert [row["query_id"] for row in batch[arm]] == ids
        for index, row in enumerate(batch[arm]):
            assert row["query_id"] == f"rq2_current_{index:07d}:{row['original_query_id']}"
            assert row["original_query_id"] in {"q1", "q2"}
            assert row["source_id"] == "report-a"
            assert row["sample_source"] == "primary"
            assert len(set(row["selected_feature_ids"])) == 3
            assert row["feature_selection_draw_index"] == row["draw_index"]
            assert row["retrieval_query"]["query_id"] == row["query_id"]
            assert row["retrieval_query"]["structure_keywords"] == []
            assert row["retrieval_query"]["hole_needs"] == []
            assert not any(item["text"] == "STALE RAG" for group in row["fragment_groups"] for item in group["items"])
    for c, s in zip(batch["C"], batch["S"]):
        for field in ("selected_feature_ids", "feature_items", "retrieval_query", "fragment_groups"):
            assert c[field] == s[field]
        expected_s = {**c, "guidance_skeleton_text": "MATCH (n:HOLE_LABEL_0) RETURN n",
                      "selected_skeleton_id": c["skeleton_id"], "generation_strategy": "feature-driven-soft"}
        assert s == expected_s
    for arm in ("R", "C"):
        for row in batch[arm]:
            assert [row[key] for key in ("skeleton_text", "guidance_skeleton_text", "holes", "selected_skeleton_id")] == ["", "", [], None]


def test_w_keeps_same_report_features_and_explicitly_allows_repeated_sets() -> None:
    batch = _builder().make_batch(0, 6)

    for row in batch["W"]:
        assert set(row["selected_feature_ids"]) == {"f0", "f1", "f2"}
        assert row["selected_feature_source_ids"] == ["report-a"]
        assert row["same_report_source_id"] == "report-a"
        assert row["composition_scope"] == "within_report_features"
        assert row["feature_selection"] == "within_report_random_constrained"
        assert row["feature_combination_deduplication"] == "not_applicable_within_report"
        assert row["guidance_skeleton_text"] == "MATCH (n:HOLE_LABEL_0) RETURN n"


def test_eligibility_deduplicates_base_ids_and_excludes_nonpositive_same_report() -> None:
    fixture = _fixture()
    fixture["base_inputs"].append(copy.deepcopy(fixture["base_inputs"][0]))
    fixture["base_inputs"].append({"query_id": "q-excluded", "source_id": "report-c"})
    fixture["skeletons"].append({"query_id": "q-excluded", "source_id": "report-c", "skeleton_id": "sk-c", "skeleton_text": "CREATE (n)", "holes": []})
    fixture["features"].extend([
        {"feature_id": f"c{i}", "source_id": "report-c", "tags": ["procedure"], "flags": {"is_write": False}}
        for i in range(3)
    ])
    builder = _builder(fixture=fixture)

    assert builder.eligible_count == 2
    assert builder.source_report_count == 1
    assert {row["original_query_id"] for row in builder.make_batch(0, 4)["W"]} == {"q1", "q2"}


def test_r_ignores_skeleton_scores_and_can_choose_features_with_zero_affinity() -> None:
    fixture = _fixture(feature_count=12)
    for feature in fixture["features"][4:]:
        feature.update(tags=["procedure"], flags={"is_write": True}, entities={})
    changed = copy.deepcopy(fixture)
    for skeleton in changed["skeletons"]:
        skeleton["skeleton_text"] = "MATCH (n) WHERE n.x > 0 RETURN n ORDER BY n.x"

    r = _builder(fixture=fixture).make_batch(0, 8)["R"]
    other = _builder(fixture=changed).make_batch(0, 8)["R"]

    assert [row["selected_feature_ids"] for row in r] == [row["selected_feature_ids"] for row in other]
    assert any(set(row["selected_feature_ids"]) - {"f0", "f1", "f2", "f3"} for row in r)


def test_same_skeleton_across_original_tasks_and_batches_has_unique_sets_with_overlap() -> None:
    builder = _builder(fixture=_fixture(feature_count=4, shared_skeleton=True))
    first, second = builder.make_batch(0, 2), builder.make_batch(2, 2)

    for arm in ("R", "C", "S"):
        rows = first[arm] + second[arm]
        sets = [frozenset(row["selected_feature_ids"]) for row in rows]
        assert len(set(sets)) == 4
        assert all(len(a & b) == 2 for a, b in zip(sets, sets[1:]))
        assert [row["draw_index"] for row in rows] == [0, 1, 2, 3]


def test_sequential_replay_and_historical_restore_match_uninterrupted_sequence() -> None:
    full = _builder().make_batch(0, 8)
    builder = _builder()
    prefix = builder.make_batch(0, 3)
    suffix = builder.make_batch(3, 5)
    restarted = _builder(prior_inputs=prefix, start_index=3).make_batch(3, 5)
    replay = _builder()
    replay.make_batch(0, 3)

    assert suffix == restarted == replay.make_batch(3, 5)
    assert {arm: prefix[arm] + suffix[arm] for arm in full} == full


def test_prior_pilot_ids_and_lane_names_reserve_combinations_by_original_skeleton() -> None:
    fixture = _fixture(feature_count=4, shared_skeleton=True)
    prior = {
        "random_feature_only": [{"query_id": "seed_17:q1", "pilot_original_query_id": "q1", "selected_feature_ids": ["f0", "f1", "f2"]}],
        "conditioned_feature_only": [{"query_id": "seed_17:q2", "pilot_original_query_id": "q2", "selected_feature_ids": ["f0", "f1", "f2"]}],
        "skeleton_feature": [{"query_id": "seed_17:q2", "pilot_original_query_id": "q2", "selected_feature_ids": ["f2", "f1", "f0"]}],
    }

    batch = _builder(fixture=fixture, prior_inputs=prior, start_index=12).make_batch(12, 1)

    for arm in ("R", "C", "S"):
        row = batch[arm][0]
        assert set(row["selected_feature_ids"]) != {"f0", "f1", "f2"}
        assert row["draw_index"] == 1
        assert row["query_id"].startswith("rq2_current_0000012:")


def test_registered_inputs_keep_cross_seed_selections_and_deduplicate_c_s_copies() -> None:
    fixture = _fixture(feature_count=4, shared_skeleton=True)
    prior = {arm: [] for arm in ("R", "C", "S")}
    for arm in prior:
        for seed, selected in ((11, ["f0", "f1", "f2"]), (12, ["f0", "f1", "f3"])):
            prior[arm].append({
                "query_id": "q1", "skeleton_id": "sk-shared",
                "selected_feature_ids": list(reversed(selected)) if arm == "S" else selected,
                "rq2_provenance": {"input_path": f"D:\\runs\\inputs\\seed_{seed}\\{arm}.jsonl"},
            })

    batch = _builder(fixture=fixture, prior_inputs=prior).make_batch(0, 1)
    reversed_prior = {arm: list(reversed(rows)) for arm, rows in reversed(list(prior.items()))}
    assert _builder(fixture=fixture, prior_inputs=reversed_prior).make_batch(0, 1) == batch
    for arm in ("R", "C", "S"):
        assert batch[arm][0]["draw_index"] == 2
        assert set(batch[arm][0]["selected_feature_ids"]) in ({"f0", "f2", "f3"}, {"f1", "f2", "f3"})


def test_same_set_in_distinct_generation_seeds_counts_all_draws_but_pilot_copy_once() -> None:
    prior = {arm: [] for arm in ("R", "C", "S")}
    for arm in prior:
        for seed in (11, 12, 13):
            prior[arm].append({
                "query_id": "q1", "skeleton_id": "sk-shared", "selected_feature_ids": ["f0", "f1", "f2"],
                "rq2_provenance": {"input_path": f"/runs/inputs/seed_{seed}/{arm}.jsonl"},
            })
        prior[arm].append({
            "query_id": "seed_11:q1", "pilot_original_query_id": "q1", "pilot_generation_seed": 11,
            "skeleton_id": "sk-shared", "selected_feature_ids": ["f2", "f1", "f0"],
            "rq2_provenance": {"input_path": f"/pilot/inputs/{arm}.jsonl"},
        })

    batch = _builder(fixture=_fixture(feature_count=4, shared_skeleton=True), prior_inputs=prior).make_batch(0, 1)

    for arm in ("R", "C", "S"):
        assert batch[arm][0]["draw_index"] == 3
        assert set(batch[arm][0]["selected_feature_ids"]) != {"f0", "f1", "f2"}


def test_lexical_context_matches_existing_retriever_and_filters_counterexamples() -> None:
    fixture = _fixture()
    corpus = build_fragment_corpus(fixture["skeletons"], fixture["features"], fixture["extra_fragments"])
    batch = _builder(fixture=fixture, top_k=100, per_group_k=100).make_batch(0, 2)
    retriever = LexicalRetriever(corpus)

    for arm in ("W", "R", "C", "S"):
        for row in batch[arm]:
            expected = group_fragments(retriever.retrieve(row["retrieval_query"], top_k=100, exclude_query_id=row["original_query_id"]), per_group_k=100)
            assert row["fragment_groups"] == expected
            ids = {item["fragment_id"] for group in row["fragment_groups"] for item in group["items"]}
            assert "doc-safe" in ids
            assert "doc-negative" not in ids
            assert all(item["query_id"] != row["original_query_id"] for group in row["fragment_groups"] for item in group["items"])


def test_current_reviewed_official_counterexamples_never_enter_retrieval() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "data/diagnostics/rag_full_corpus_counterexamples_20261012/removed_counterexamples.jsonl"
    if not path.exists():
        pytest.skip("local reviewed official-corpus fixture is unavailable")
    removed = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(removed) == 214
    extras = [{**row["chunk"], "source_type": "official_chunk"} for row in removed]
    fixture = _fixture()
    fixture["extra_fragments"] = extras
    builder = _builder(fixture=fixture, top_k=10000, per_group_k=10000)

    rows = builder.make_batch(0, 1)
    for lane in rows.values():
        assert all(not item["source_type"].startswith("official") for group in lane[0]["fragment_groups"] for item in group["items"])


@pytest.mark.parametrize("start,count", [(0, 0), (0, -1), (-1, 1), (1, 1)])
def test_make_batch_rejects_nonpositive_count_or_noncontiguous_start(start, count) -> None:
    builder = _builder()

    with pytest.raises(ValueError):
        builder.make_batch(start, count)
    assert len(builder.make_batch(0, 1)["S"]) == 1


def test_empty_eligible_pool_and_negative_start_index_fail_clearly() -> None:
    fixture = _fixture()
    fixture["base_inputs"] = []
    with pytest.raises(ValueError, match="eligible"):
        _builder(fixture=fixture)
    with pytest.raises(ValueError, match="start_index"):
        _builder(start_index=-1)


def test_prior_history_cannot_silently_lose_an_unresolvable_skeleton() -> None:
    with pytest.raises(ValueError, match="skeleton"):
        _builder(prior_inputs={"C": [{"query_id": "unknown", "selected_feature_ids": ["f0", "f1", "f2"]}]})
