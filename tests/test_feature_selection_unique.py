from __future__ import annotations

import itertools
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.common.jsonl_utils import write_jsonl
from src.stage2.feature_selection import select_feature_records


def _features(count: int = 4) -> list[dict]:
    return [
        {
            "feature_id": f"f{i}",
            "query_id": f"history_{i}",
            "source_id": f"report_{i}",
            "feature_name": f"feature_{i}",
            "feature_description": f"historical behavior {i}",
            "query_instance": "RETURN 1",
            "tags": ["return"],
            "flags": {"is_write": False},
            "entities": {},
        }
        for i in range(count)
    ]


def _select(pool: list[dict], used: set[frozenset[str]], **kwargs) -> list[dict]:
    return select_feature_records(
        {"query_id": "q1", "skeleton_text": "MATCH (n) RETURN n", "holes": []},
        query_features=[],
        all_features=pool,
        mode="random_constrained",
        sample_size=3,
        seed=42,
        used_combinations=used,
        **kwargs,
    )


def test_unique_selection_allows_partial_overlap_but_exhausts_all_four_sets() -> None:
    used: set[frozenset[str]] = set()
    draws = [frozenset(f["feature_id"] for f in _select(_features(), used, draw_index=i)) for i in range(4)]

    assert set(draws) == {
        frozenset(("f0", "f1", "f2")),
        frozenset(("f0", "f1", "f3")),
        frozenset(("f0", "f2", "f3")),
        frozenset(("f1", "f2", "f3")),
    }
    assert all(len(a & b) == 2 for a, b in itertools.combinations(draws, 2))
    assert used == set(draws)
    with pytest.raises(ValueError, match="exhausted"):
        _select(_features(), used, draw_index=4)
    assert used == set(draws)


def test_reordering_a_used_set_does_not_make_it_new() -> None:
    used = {
        frozenset(("f2", "f0", "f1")),
        frozenset(("f3", "f0", "f1")),
        frozenset(("f3", "f0", "f2")),
    }

    result = _select(_features(), used, draw_index=0)

    assert {f["feature_id"] for f in result} == {"f1", "f2", "f3"}


def test_unique_selection_is_reproducible_with_the_same_draw_sequence() -> None:
    histories = [set(), set()]
    sequences = [
        [[f["feature_id"] for f in _select(_features(20), history, draw_index=i)] for i in range(30)]
        for history in histories
    ]

    assert sequences[0] == sequences[1]
    assert len({frozenset(ids) for ids in sequences[0]}) == 30


def test_duplicate_library_records_cannot_fill_two_feature_slots() -> None:
    pool = _features(3)
    pool = [pool[0], pool[0], pool[1], pool[1], pool[2]]
    used: set[frozenset[str]] = set()

    assert {f["feature_id"] for f in _select(pool, used)} == {"f0", "f1", "f2"}
    with pytest.raises(ValueError, match="exhausted"):
        _select(pool, used)


def test_unique_selection_does_not_expand_the_positive_score_pool() -> None:
    pool = _features(3)
    pool[0].update(tags=["write"], flags={"is_write": True})
    used: set[frozenset[str]] = set()
    skeleton = {"query_id": "write", "skeleton_text": "CREATE (n)", "holes": []}
    kwargs = dict(query_features=[], all_features=pool, mode="random_constrained", sample_size=1, seed=42, used_combinations=used)

    assert [f["feature_id"] for f in select_feature_records(skeleton, **kwargs)] == ["f0"]
    with pytest.raises(ValueError, match="exhausted"):
        select_feature_records(skeleton, **kwargs)


def test_unique_selection_rejects_an_undersized_pool_without_shortening_the_set() -> None:
    with pytest.raises(ValueError, match="need 3"):
        _select(_features(2), set())


def test_draw_index_changes_sampling_without_changing_the_run_seed() -> None:
    first = _select(_features(100), set(), draw_index=0)
    second = _select(_features(100), set(), draw_index=1)

    assert [f["feature_id"] for f in first] != [f["feature_id"] for f in second]


def test_legacy_stateless_calls_remain_repeatable() -> None:
    kwargs = dict(query_features=[], all_features=_features(20), mode="random_constrained", sample_size=3, seed=42)
    skeleton = {"query_id": "q1", "skeleton_text": "MATCH (n) RETURN n", "holes": []}

    assert select_feature_records(skeleton, **kwargs) == select_feature_records(skeleton, **kwargs)


def _cli_fixture(tmp_path: Path, *, same_skeleton: bool = False) -> list[str]:
    skeletons = [
        {
            "query_id": f"q{i}",
            "source_id": "source",
            "skeleton_id": "shared" if same_skeleton else f"sk{i}",
            "skeleton_text": "MATCH (n) RETURN n",
            "query_text": "MATCH (n) RETURN n",
            "holes": [],
        }
        for i in range(1, 3 if same_skeleton else 2)
    ]
    write_jsonl(skeletons, tmp_path / "skeletons.jsonl")
    write_jsonl(_features(), tmp_path / "features.jsonl")
    return [
        sys.executable, "-m", "src.cli.run_stage2",
        "--skeletons", str(tmp_path / "skeletons.jsonl"),
        "--features", str(tmp_path / "features.jsonl"),
        "--feature-selection", "random_constrained",
        "--feature-sample-size", "3",
        "--feature-draws-per-skeleton", "4" if not same_skeleton else "2",
        "--feature-seed", "42",
        "--feature-run-salt", "test-run",
    ]


def _run(command: list[str], output: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*command, "--output-dir", str(output), *extra],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False,
    )


def _read_inputs(output: Path) -> list[dict]:
    return [json.loads(line) for line in (output / "instantiation_inputs.jsonl").read_text("utf-8").splitlines()]


def test_stage2_deduplicates_by_skeleton_not_by_generated_task_id(tmp_path: Path) -> None:
    command = _cli_fixture(tmp_path, same_skeleton=True)
    output = tmp_path / "output"

    result = _run(command, output)

    assert result.returncode == 0, result.stderr
    rows = _read_inputs(output)
    assert len(rows) == 4
    assert len({row["query_id"] for row in rows}) == 4
    assert len({frozenset(row["selected_feature_ids"]) for row in rows}) == 4
    assert len({row["feature_selection_skeleton_key"] for row in rows}) == 1
    assert [row["seed_query_id"] for row in rows] == ["q1", "q1", "q2", "q2"]
    assert [row["feature_selection_draw_index"] for row in rows] == [0, 1, 0, 1]
    for row in rows:
        assert row["retrieval_query"]["query_id"] == row["query_id"]
        assert all(item.get("query_id") != row["seed_query_id"] for group in row["fragment_groups"] for item in group["items"])


def test_stage2_resume_restores_history_and_matches_an_uninterrupted_run(tmp_path: Path) -> None:
    command = _cli_fixture(tmp_path)
    full = tmp_path / "full"
    resumed = tmp_path / "resumed"
    assert _run(command, full).returncode == 0
    assert _run(command, resumed, "--max-items", "2").returncode == 0

    result = _run(command, resumed, "--resume")

    assert result.returncode == 0, result.stderr
    assert _read_inputs(resumed) == _read_inputs(full)
    assert len({frozenset(row["selected_feature_ids"]) for row in _read_inputs(resumed)}) == 4


def test_stage2_fails_clearly_after_all_combinations_are_consumed(tmp_path: Path) -> None:
    command = _cli_fixture(tmp_path)
    command[command.index("--feature-draws-per-skeleton") + 1] = "5"
    output = tmp_path / "output"

    result = _run(command, output)

    assert result.returncode != 0
    assert "exhausted" in result.stderr
    assert len(_read_inputs(output)) == 4


def test_stage2_resume_rejects_a_changed_selection_seed(tmp_path: Path) -> None:
    command = _cli_fixture(tmp_path)
    output = tmp_path / "output"
    assert _run(command, output, "--max-items", "1").returncode == 0
    before = _read_inputs(output)
    command[command.index("--feature-seed") + 1] = "43"

    result = _run(command, output, "--resume")

    assert result.returncode != 0
    assert "feature_seed" in result.stderr
    assert _read_inputs(output) == before


def test_stage2_resume_cannot_ignore_missing_combination_history(tmp_path: Path) -> None:
    command = _cli_fixture(tmp_path)
    output = tmp_path / "output"
    assert _run(command, output, "--max-items", "2").returncode == 0
    (output / "instantiation_inputs.jsonl").unlink()

    result = _run(command, output, "--resume")

    assert result.returncode != 0
    assert "saved feature selections" in result.stderr
    assert not (output / "instantiation_inputs.jsonl").exists()


def test_resume_randomization_flag_cannot_replace_an_existing_empty_run_salt(tmp_path: Path) -> None:
    command = _cli_fixture(tmp_path)
    salt_pos = command.index("--feature-run-salt")
    del command[salt_pos:salt_pos + 2]
    full = tmp_path / "full"
    resumed = tmp_path / "resumed"
    assert _run(command, full).returncode == 0
    assert _run(command, resumed, "--max-items", "2").returncode == 0

    result = _run(command, resumed, "--resume", "--randomize-feature-selection")

    assert result.returncode == 0, result.stderr
    assert _read_inputs(resumed) == _read_inputs(full)
    assert json.loads((resumed / "summary.json").read_text("utf-8"))["feature_run_salt"] is None


def test_identical_skeletons_from_different_reports_share_combination_history(tmp_path: Path) -> None:
    command = _cli_fixture(tmp_path, same_skeleton=True)
    skeleton_path = tmp_path / "skeletons.jsonl"
    skeletons = [json.loads(line) for line in skeleton_path.read_text("utf-8").splitlines()]
    skeletons[1]["source_id"] = "another-report"
    write_jsonl(skeletons, skeleton_path)
    output = tmp_path / "output"

    result = _run(command, output)

    assert result.returncode == 0, result.stderr
    rows = _read_inputs(output)
    assert len({row["feature_selection_skeleton_key"] for row in rows}) == 1
    assert len({frozenset(row["selected_feature_ids"]) for row in rows}) == 4


def test_stage2_paired_mode_cannot_pretend_to_make_multiple_random_draws(tmp_path: Path) -> None:
    command = _cli_fixture(tmp_path)
    command[command.index("--feature-selection") + 1] = "paired"

    result = _run(command, tmp_path / "output")

    assert result.returncode != 0
    assert "paired" in result.stderr


def test_stage2_legacy_opt_out_allows_repeated_sets(tmp_path: Path) -> None:
    command = _cli_fixture(tmp_path)
    write_jsonl(_features(3), tmp_path / "features.jsonl")
    output = tmp_path / "legacy"

    result = _run(command, output, "--allow-repeated-feature-combinations")

    assert result.returncode == 0, result.stderr
    rows = _read_inputs(output)
    assert len(rows) == 4
    assert {frozenset(row["selected_feature_ids"]) for row in rows} == {frozenset(("f0", "f1", "f2"))}


def test_legacy_interrupted_run_can_resume_without_an_old_summary(tmp_path: Path) -> None:
    command = _cli_fixture(tmp_path, same_skeleton=True)
    command[command.index("--feature-draws-per-skeleton") + 1] = "1"
    command.append("--allow-repeated-feature-combinations")
    output = tmp_path / "legacy"
    assert _run(command, output, "--max-items", "1").returncode == 0
    (output / "summary.json").unlink()
    old_rows = _read_inputs(output)
    for row in old_rows:
        for key in ("feature_selection_protocol", "feature_selection_skeleton_key", "feature_selection_draw_index", "seed_query_id"):
            row.pop(key, None)
    write_jsonl(old_rows, output / "instantiation_inputs.jsonl")

    result = _run(command, output, "--resume")

    assert result.returncode == 0, result.stderr
    rows = _read_inputs(output)
    assert rows[0] == old_rows[0]
    assert [row["query_id"] for row in rows] == ["q1", "q2"]
    assert rows[1]["feature_run_salt"] == "test-run"
    summary = json.loads((output / "summary.json").read_text("utf-8"))
    assert summary["resume_metadata_recovered_from_rows"] is True
    assert summary["original_input_hashes_unavailable"] is True


def test_missing_summary_cannot_downgrade_a_unique_run_to_legacy(tmp_path: Path) -> None:
    command = _cli_fixture(tmp_path)
    output = tmp_path / "unique"
    assert _run(command, output, "--max-items", "1").returncode == 0
    before = _read_inputs(output)
    (output / "summary.json").unlink()

    result = _run(command, output, "--resume", "--allow-repeated-feature-combinations")

    assert result.returncode != 0
    assert "protocol" in result.stderr
    assert _read_inputs(output) == before
