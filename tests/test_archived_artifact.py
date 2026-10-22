import hashlib
import json

import pytest


def test_integrity_check_detects_a_modified_dataset(tmp_path):
    from scripts.reproduce_archived_results import verify_package

    data = tmp_path / 'sample.json'
    data.write_text('original', encoding='utf-8')
    manifest = {'files': [{'path': 'sample.json',
                          'sha256': hashlib.sha256(data.read_bytes()).hexdigest()}]}
    (tmp_path / 'ARTIFACT_MANIFEST.json').write_text(json.dumps(manifest))
    verify_package(tmp_path)
    data.write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='hash mismatch'):
        verify_package(tmp_path)


def test_integrity_check_rejects_paths_outside_package(tmp_path):
    from scripts.reproduce_archived_results import verify_package

    (tmp_path / 'ARTIFACT_MANIFEST.json').write_text(json.dumps({
        'files': [{'path': '../outside', 'sha256': 'unused'}]}))
    with pytest.raises(ValueError, match='outside'):
        verify_package(tmp_path)


def test_pair_validation_detects_reordered_or_missing_tasks(tmp_path):
    from scripts.reproduce_archived_results import validate_cohort_pair

    rows = [{'paired_task_key': 'a', 'source_id': 'report',
             'selected_feature_ids': ['f1', 'f2', 'f3']},
            {'paired_task_key': 'b', 'source_id': 'report',
             'selected_feature_ids': ['f1', 'f2', 'f3']}]
    validate_cohort_pair(rows, rows, feature_count=3, size=2)
    with pytest.raises(ValueError, match='task'):
        validate_cohort_pair(rows, rows[::-1], feature_count=3, size=2)
    with pytest.raises(ValueError, match='size'):
        validate_cohort_pair(rows, rows[:1], feature_count=3, size=2)
