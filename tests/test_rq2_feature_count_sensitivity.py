"""Five-feature inputs, shared eligibility, resumable draws, and small-N stats."""
import copy
import importlib

import pytest

from tests.test_rq2_current_inputs import _fixture
from scripts.run_rq2_ws_paired_completion import write_rows


def module():
    return importlib.import_module('scripts.run_rq2_feature_count_sensitivity')


def fixture():
    data = _fixture(feature_count=14)
    for f in data['features'][:5]:
        f['source_id'] = 'report-a'
    return data


def builder(**kwargs):
    return module().FeatureCountInputBuilder(**fixture(), feature_count=5, **kwargs)


def test_five_feature_rows_are_paired_and_retrieved_without_stale_fragments():
    rows = builder().make_batch(0, 3)
    assert set(rows) == {'W', 'S'}
    assert [r['query_id'] for r in rows['W']] == [r['query_id'] for r in rows['S']]
    for w, s in zip(rows['W'], rows['S']):
        assert w['original_query_id'] == s['original_query_id']
        assert w['guidance_skeleton_text'] == s['guidance_skeleton_text']
        assert set(w['selected_feature_ids']) == {'f0', 'f1', 'f2', 'f3', 'f4'}
        for row in (w, s):
            assert len(row['selected_feature_ids']) == len(set(row['selected_feature_ids'])) == 5
            assert len(row['feature_items']) == 5
            assert not any(item['text'] == 'STALE RAG' for g in row['fragment_groups'] for item in g['items'])


def test_eligibility_requires_five_positive_features_for_both_arms():
    data = fixture()
    skeleton = dict(data['skeletons'][0], query_id='excluded', source_id='report-c', skeleton_id='sk-c')
    data['skeletons'].append(skeleton)
    data['base_inputs'].append(copy.deepcopy(skeleton))
    data['features'] += [dict(data['features'][0], feature_id=f'c{i}', source_id='report-c') for i in range(4)]
    obj = module().FeatureCountInputBuilder(**data, feature_count=5)
    assert obj.eligible_count == 2
    assert obj.source_report_count == 1
    assert {r['original_query_id'] for r in obj.make_batch(0, 8)['S']} == {'q1', 'q2'}


def test_restoring_all_committed_five_feature_inputs_preserves_next_batch():
    full = builder()
    prefix = full.make_batch(0, 8)
    resumed = builder(prior_inputs=prefix, start_index=8)
    assert resumed.make_batch(8, 4) == full.make_batch(8, 4)


def test_s_never_repeats_whole_five_feature_sets_for_same_skeleton():
    rows = builder().make_batch(0, 12)['S']
    seen = {}
    for row in rows:
        group = seen.setdefault(row['selected_skeleton_id'], set())
        current = frozenset(row['selected_feature_ids'])
        assert current not in group
        group.add(current)


def test_new_builder_leaves_original_three_feature_builder_unchanged():
    from scripts.rq2_current_inputs import InputBuilder
    obj = InputBuilder(**fixture())
    assert len(obj.make_batch(0, 1)['S'][0]['selected_feature_ids']) == 3


def test_five_group_exact_signed_rank_cannot_claim_thirty_group_significance():
    result = module().paired_exact_test([0]*5, [1, 2, 3, 4, 5])
    assert result['n_pairs'] == 5
    assert result['p_raw'] == .0625
    assert result['r_magnitude'] == pytest.approx(0.9045340337332909)


def test_exact_signed_rank_removes_zero_differences_and_preserves_symmetry():
    a = module().paired_exact_test([0]*5, [0, 1, 1, 2, 2])
    b = module().paired_exact_test([0, 1, 1, 2, 2], [0]*5)
    assert a['n_pairs'] == 5 and a['n_nonzero'] == 4
    assert a['p_raw'] == b['p_raw'] == .125
    assert a['rank_biserial_S_minus_W'] == 1
    assert b['rank_biserial_S_minus_W'] == -1


def test_five_feature_pair_validation_rejects_three_feature_inputs():
    rows = builder().make_batch(0, 1)
    w, s = rows['W'][0], rows['S'][0]
    module().validate_inputs(w, s, 5)
    s['selected_feature_ids'] = s['selected_feature_ids'][:3]
    with pytest.raises(ValueError, match='feature count'):
        module().validate_inputs(w, s, 5)


def test_four_feature_protocol_can_reuse_the_five_feature_eligibility_pool():
    data = fixture()
    five = module().FeatureCountInputBuilder(
        **data, feature_count=5, eligibility_feature_count=5)
    four = module().FeatureCountInputBuilder(
        **data, feature_count=4, eligibility_feature_count=5)

    assert [row['query_id'] for row in four._eligible] == [
        row['query_id'] for row in five._eligible]
    rows = four.make_batch(0, 8)
    assert [row['query_id'] for row in rows['W']] == [
        row['query_id'] for row in rows['S']]
    for w, s in zip(rows['W'], rows['S']):
        assert w['original_query_id'] == s['original_query_id']
        assert len(w['selected_feature_ids']) == len(set(w['selected_feature_ids'])) == 4
        assert len(s['selected_feature_ids']) == len(set(s['selected_feature_ids'])) == 4


def test_preflight_probe_advances_past_a_completed_error(tmp_path):
    job = {
        'arm': 'W', 'shard': 1, 'count': 2,
        'input': str(tmp_path / 'inputs.jsonl'),
        'output': str(tmp_path / 'output'),
    }
    write_rows(tmp_path / 'inputs.jsonl', [{'query_id': 'q0'}, {'query_id': 'q1'}])
    write_rows(tmp_path / 'output/errors.jsonl', [{'query_id': 'q0', 'variant_index': 0}])
    write_rows(tmp_path / 'output/completed_variants.jsonl', [{'query_id': 'q0', 'variant_index': 0}])

    probe = module().build_preflight_probe(tmp_path, job)

    assert probe['preflight_query_id'] == 'q1'
    assert list(module().iter_rows(probe['input'])) == [{'query_id': 'q1'}]
    assert probe['preflight_error_count_before'] == 1
    assert module().preflight_arm_passed(job) is False


@pytest.mark.parametrize('disposition', ['instantiated_cases.jsonl', 'rejected_cases.jsonl'])
def test_completed_non_error_disposition_satisfies_preflight(tmp_path, disposition):
    job = {
        'arm': 'S', 'shard': 1, 'count': 1,
        'input': str(tmp_path / 'inputs.jsonl'),
        'output': str(tmp_path / 'output'),
    }
    write_rows(tmp_path / 'inputs.jsonl', [{'query_id': 'q0'}])
    write_rows(tmp_path / f'output/{disposition}', [{'query_id': 'q0', 'variant_index': 0}])
    write_rows(tmp_path / 'output/completed_variants.jsonl', [{'query_id': 'q0', 'variant_index': 0}])

    assert module().preflight_arm_passed(job) is True
