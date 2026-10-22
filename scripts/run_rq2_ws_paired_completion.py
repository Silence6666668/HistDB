"""Complete 30,000 genuinely task-matched W/S outputs, then form 30x1,000 pairs.

Reuse registered source outcomes. Previously rejected/error tasks stay final;
only never-attempted counterparts and fresh paired tasks receive new LLM calls.
No DB execution and no outcome- or metric-driven feature selection.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import hashlib
import json
import math
from pathlib import Path
import random
import shutil
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
from scipy.stats import rankdata, wilcoxon
from statsmodels.stats.multitest import multipletests

from scripts.analyze_historical_composition_ablation import (
    aggregate_run_metrics, build_history_index, realized_run_metrics, selection_run_metrics,
)
from scripts.rq2_current_inputs import InputBuilder
from scripts.run_rq2_completion import (
    acquire_lock, build_jobs, execute_jobs, job_finished, json_file, release_lock,
    validate_job, verify_files,
)
from scripts.run_rq2_complexity_pilot import file_hash, read_rows, utc_now, write_json, write_rows

DEFAULT_SOURCE = REPO_ROOT / 'data/ablation_runs/rq2_completion_30k_20261013'
DEFAULT_OUTPUT = REPO_ROOT / 'data/ablation_runs/rq2_ws_paired_30x1000_20261014'
METRICS = ('unique_mechanism_pairs', 'unique_unseen_mechanism_pairs',
           'unique_hupr', 'query_unseen_composition_rate')
LABELS = ('Distinct query-element pairs', 'Historically unseen query-element pairs',
          'Historically unseen pairs (%)', 'Queries containing an unseen pair (%)')


def project_history_row(row):
    keep = ('query_id', 'skeleton_id', 'feature_selection_skeleton_key', 'selected_skeleton_id',
            'original_query_id', 'pilot_original_query_id', 'seed_query_id',
            'selected_feature_ids', 'pilot_generation_seed', 'rq2_provenance')
    return {key: row[key] for key in keep if key in row}


def verify_resume_dependencies(root):
    manifest = json_file(root/'manifest.json')
    verify_files(manifest['frozen_files'] + manifest['source_files'])
    source = Path(manifest['source'])
    original = json_file(source/'manifest.json')
    # Original hashes cover live Stage2 helpers, non-Python rules, legacy
    # histories, frozen corpora, and the original raw legacy input/output files.
    verify_files(original['frozen_files'] + original.get('legacy_source_files', []))
    for batch in json_file(source/'batches.json')['batches']:
        verify_files(batch['frozen_files'])  # All W/R/C/S history, not only W/S shards.
    copied = []
    for entry in original['frozen_files']:
        path = Path(entry['path'])
        if path.is_relative_to(source/'code_snapshot'):
            copied.append(dict(path=str(root/'code_snapshot'/path.relative_to(source/'code_snapshot')),
                               sha256=entry['sha256']))
    verify_files(copied)
    # Paired driver and analysis helper were not part of the original snapshot.
    verify_files(json_file(root/'resume_integrity.json')['files'])


def selection_for_arm(rows, history, arm):
    result = selection_run_metrics(rows, history)
    if arm == 'W':
        for row in rows:
            if not set(row['selected_feature_ids']) <= history['report_feature_ids'].get(row['source_id'], set()):
                raise ValueError('within-report feature-source mismatch')
        if result['input_strict_crr_rate'] != 0 or result['selected_occurrence_hupr'] != 0:
            raise ValueError('within-report selection invariant failed')
        result.update(input_assigned_crr_rate=0., selected_cross_report_pair_rate=0., selected_crnp_rate=None)
    return result


def pair_index(rows):
    result = {}
    for row in rows:
        key = (row.get('rq2_pair_keys') or {}).get('WS')
        if key is None:
            continue
        if key in result:
            raise ValueError(f'duplicate WS task key: {key}')
        if not row.get('validation', {}).get('is_valid'):
            raise ValueError(f'non-retained output in accepted pool: {key}')
        result[key] = row
    return result


def validate_pair(key, w, s, wi, si):
    fields = ('source_id', 'original_query_id')
    for field in fields:
        if wi.get(field) != si.get(field):
            raise ValueError(f'paired input mismatch: {key}/{field}')
    skeleton = lambda row: row.get('selected_skeleton_id') or row.get('skeleton_id')
    if skeleton(wi) != skeleton(si):
        raise ValueError(f'paired skeleton mismatch: {key}')
    guidance = lambda row: row.get('guidance_skeleton_text') or row.get('skeleton_text') or ''
    if guidance(wi) != guidance(si) or not guidance(wi):
        raise ValueError(f'paired skeleton text mismatch: {key}')
    if w.get('source_id') != s.get('source_id') or w.get('source_id') != wi.get('source_id'):
        raise ValueError(f'paired output source mismatch: {key}')
    if (w.get('rq2_provenance') or {}).get('protocol') != (s.get('rq2_provenance') or {}).get('protocol'):
        raise ValueError(f'paired protocol mismatch: {key}')
    for row in (wi, si):
        ids = row.get('selected_feature_ids') or []
        if len(ids) != 3 or len(set(ids)) != 3:
            raise ValueError(f'three distinct input features required: {key}')


def missing_unattempted(accepted, attempted, candidates):
    result = {a: [] for a in 'WS'}
    for arm, other in (('W', 'S'), ('S', 'W')):
        for key in sorted(set(accepted[other]) - set(accepted[arm]) - attempted[arm]):
            if key in candidates[arm]:
                result[arm].append(candidates[arm][key])
    return result


def select_joint_keys(maps, existing_keys, target):
    common = set(maps['W']) & set(maps['S'])
    if len(common) < target:
        raise ValueError(f'insufficient real paired tasks: {len(common)}/{target}')
    existing = [key for key in existing_keys if key in common]
    if len(existing) != len(set(existing)):
        raise ValueError('duplicate precommitted existing key')
    new = sorted(common - set(existing))
    return (existing + new)[:target]


def paired_test(w, s):
    if len(w) != len(s) or not len(w):
        raise ValueError('paired arrays must have equal nonzero length')
    differences = np.round(np.asarray(s, dtype=float) - np.asarray(w, dtype=float), 12)
    if not np.all(np.isfinite(differences)):
        raise ValueError('nonfinite paired measurement')
    nonzero = differences[differences != 0]
    if len(nonzero):
        test = wilcoxon(differences, zero_method='wilcox', correction=False,
                        alternative='two-sided', method='approx')
        ranks = rankdata(np.abs(nonzero))
        positive = float(ranks[nonzero > 0].sum())
        negative = float(ranks[nonzero < 0].sum())
        z, p, statistic = float(test.zstatistic), float(test.pvalue), float(test.statistic)
        rank_biserial = (positive - negative)/(positive + negative)
    else:
        z, p, statistic, positive, negative, rank_biserial = 0., 1., 0., 0., 0., 0.
    return dict(n_pairs=len(w), n_nonzero=len(nonzero), zero_pairs=int(sum(differences == 0)),
                statistic=statistic, z_scipy=z, p_raw=p,
                r_from_reported_Z=z/math.sqrt(len(w)), r_magnitude=abs(z)/math.sqrt(len(w)),
                positive_rank_sum=positive, negative_rank_sum=negative,
                rank_biserial_S_minus_W=rank_biserial,
                positive_pairs=int(sum(differences > 0)), negative_pairs=int(sum(differences < 0)),
                paired_differences_S_minus_W=differences.tolist(),
                mean_difference_S_minus_W=float(differences.mean()))


def iter_rows(path):
    with Path(path).open(encoding='utf-8-sig') as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def source_recovery(source):
    accepted, attempted = {a: [] for a in 'WS'}, {a: set() for a in 'WS'}
    files = {source/'manifest.json', source/'batches.json'}
    for arm in 'WS':
        path = source/'legacy'/f'{arm}.jsonl'
        files.add(path)
        for original in iter_rows(path):
            row = copy.deepcopy(original)
            provenance = row['rq2_provenance']
            row['_paired_input_path'] = provenance['input_path']
            row['_paired_input_query_id'] = provenance.get('original_accepted_query_id', row['query_id'])
            accepted[arm].append(row)
    for batch in json_file(source/'batches.json')['batches']:
        for job in batch['jobs']:
            arm = job['arm']
            if arm not in accepted:
                continue
            validate_job(job)
            out = Path(job['output'])
            for name in ('instantiated_cases.jsonl', 'rejected_cases.jsonl', 'errors.jsonl', 'completed_variants.jsonl'):
                path = out/name
                if path.exists():
                    files.add(path)
            files.add(Path(job['input']))
            attempted[arm].update(r['query_id'] for r in iter_rows(out/'completed_variants.jsonl'))
            for original in iter_rows(out/'instantiated_cases.jsonl'):
                row = dict(original, rq2_pair_keys={'WS': original['query_id']},
                           rq2_candidate_index=int(original['query_id'].split(':', 1)[0].removeprefix('rq2_current_')),
                           rq2_provenance={'protocol': 'current_unique_filtered', 'input_path': job['input'],
                                           'accepted_path': job['output'], 'batch': batch['index']},
                           _paired_input_path=job['input'], _paired_input_query_id=original['query_id'])
                accepted[arm].append(row)
    maps = {a: pair_index(accepted[a]) for a in 'WS'}
    candidates = {a: {} for a in 'WS'}
    for arm, other in (('W', 'S'), ('S', 'W')):
        wanted = {k for k in set(maps[other])-set(maps[arm])-attempted[arm] if k.startswith('rq2_current_')}
        if not wanted:
            continue
        for batch in json_file(source/'batches.json')['batches']:
            if not any(batch['start'] <= int(k.split(':', 1)[0].removeprefix('rq2_current_')) < batch['start']+batch['count'] for k in wanted):
                continue
            path = Path(batch['inputs'][arm])
            files.add(path)
            candidates[arm].update({r['query_id']: r for r in iter_rows(path) if r['query_id'] in wanted})
        if set(candidates[arm]) != wanted:
            raise ValueError(f'cannot recover never-attempted input: {arm}')
    return accepted, maps, attempted, missing_unattempted(maps, attempted, candidates), files


def commit_jobs(root, rows, *, kind, start=None, count=None, shards=24):
    batches = json_file(root/'batches.json')['batches']
    batch_root = root/'batches'/f'batch_{len(batches):04d}'
    if batch_root.exists():
        if any((batch_root/'cells').rglob('*.jsonl')) or any((batch_root/'cells').rglob('*.log')):
            raise ValueError(f'unregistered batch has LLM outcomes/logs; manual recovery required: {batch_root}')
        archived = root/'archive'/f'orphan_{batch_root.name}_{utc_now().replace(":", "").replace(".", "_")}'
        if not batch_root.resolve().is_relative_to(root.resolve()) or not archived.resolve().is_relative_to(root.resolve()):
            raise ValueError('batch archive escapes experiment root')
        archived.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(batch_root), str(archived))
    inputs, entries = {}, []
    for arm in 'WS':
        path = batch_root/'inputs'/f'{arm}.jsonl'
        write_rows(path, rows.get(arm, []))
        inputs[arm] = str(path.resolve())
        entries.append({'path': str(path.resolve()), 'sha256': file_hash(path)})
    active = [f'default/{a}' for a in 'WS' if rows.get(a)]
    jobs = build_jobs(batch_root, rows, active, shards)
    entries.extend({'path': j['input'], 'sha256': file_hash(Path(j['input']))} for j in jobs)
    batch = dict(index=len(batches), kind=kind, start=start, count=count,
                 inputs=inputs, jobs=jobs, frozen_files=entries, created_utc=utc_now())
    write_json(batch_root/'manifest.json', batch)
    write_json(root/'batches.json', {'batches': batches+[batch]})
    return batch


def prepare(source, root, *, target=30000, seed=20261014, batch_size=1000, shards=24):
    if root.exists():
        raise FileExistsError(f'refusing to overwrite experiment: {root}')
    source_manifest = json_file(source/'manifest.json')
    for name in ('run_rq2_completion.py', 'run_rq2_complexity_pilot.py', 'rq2_current_inputs.py'):
        if file_hash(REPO_ROOT/'scripts'/name) != file_hash(source/'code_snapshot/scripts'/name):
            raise ValueError(f'live helper differs from frozen source helper: {name}')
    verify_files([entry for entry in source_manifest['frozen_files'] if Path(entry['path']).is_relative_to(source)])
    accepted, maps, attempted, missing, source_files = source_recovery(source)
    root.mkdir(parents=True)
    for name in ('frozen', 'code_snapshot'):
        shutil.copytree(source/name, root/name)
    shutil.copyfile(Path(__file__), root/'code_snapshot/scripts'/Path(__file__).name)
    entries = []
    for arm in 'WS':
        path = root/'reused'/f'{arm}.jsonl'
        write_rows(path, accepted[arm])
        entries.append({'path': str(path.resolve()), 'sha256': file_hash(path)})
    for path in (root/'frozen').rglob('*'):
        if path.is_file():
            entries.append({'path': str(path.resolve()), 'sha256': file_hash(path)})
    for path in (root/'code_snapshot').rglob('*.py'):
        entries.append({'path': str(path.resolve()), 'sha256': file_hash(path)})
    baseline_keys = sorted(set(maps['W']) & set(maps['S']))
    write_json(root/'baseline_common_keys.json', baseline_keys)
    write_json(root/'source_attempted_keys.json', {a: sorted(attempted[a]) for a in 'WS'})
    for name in ('baseline_common_keys.json', 'source_attempted_keys.json'):
        path = root/name
        entries.append({'path': str(path.resolve()), 'sha256': file_hash(path)})
    manifest = dict(created_utc=utc_now(), source=str(source.resolve()), target_pairs=target,
                    cohorts=30, cohort_size=1000, shuffle_seed=seed, batch_size=batch_size,
                    shards_per_arm=shards, concurrency_cap=48, database_execution=False,
                    paired_tasks=True, independent_generator_runs=False,
                    baseline_available_pairs=len(baseline_keys),
                    never_attempted_backfill={a: len(missing[a]) for a in 'WS'},
                    s_legacy_reuse_limit=4000, prior_failures_are_final=True,
                    selection='existing common keys first, then sorted newly matched task keys; joint retention only',
                    source_files=[{'path': str(p.resolve()), 'sha256': file_hash(p)} for p in sorted(source_files)],
                    frozen_files=entries, new_candidate_cap=source_manifest['new_candidate_cap'])
    write_json(root/'manifest.json', manifest)
    write_json(root/'resume_integrity.json', {'created_utc': utc_now(), 'files': [
        {'path': str(p.resolve()), 'sha256': file_hash(p)} for p in (
            Path(__file__), REPO_ROOT/'scripts/analyze_historical_composition_ablation.py')]})
    write_json(root/'batches.json', {'batches': []})
    if any(missing.values()):
        commit_jobs(root, missing, kind='never_attempted_counterpart', shards=shards)
    write_json(root/'progress.json', {'matched_pairs': len(baseline_keys), 'target_pairs': target})
    return manifest


def collect(root):
    rows = {a: read_rows(root/'reused'/f'{a}.jsonl') for a in 'WS'}
    for batch in json_file(root/'batches.json')['batches']:
        for job in batch['jobs']:
            out = Path(job['output'])
            completed = {r['query_id'] for r in read_rows(out/'completed_variants.jsonl', live=True)}
            for original in read_rows(out/'instantiated_cases.jsonl', live=True):
                if original['query_id'] not in completed:
                    continue
                row = dict(original, rq2_pair_keys={'WS': original['query_id']},
                           rq2_provenance={'protocol': 'current_unique_filtered', 'input_path': job['input'],
                                           'accepted_path': job['output'], 'paired_topup_batch': batch['index']},
                           _paired_input_path=job['input'], _paired_input_query_id=original['query_id'])
                rows[job['arm']].append(row)
    return {a: pair_index(rows[a]) for a in 'WS'}


def restore_builder(root):
    verify_resume_dependencies(root)
    manifest = json_file(root/'manifest.json')
    source = Path(manifest['source'])
    source_manifest = json_file(source/'manifest.json')
    prior = {a: [] for a in 'WRCS'}
    for arm in 'WRCS':
        prior[arm].extend(project_history_row(r) for r in iter_rows(source/'legacy/prior_inputs'/f'{arm}.jsonl'))
    starts = []
    for batch in json_file(source/'batches.json')['batches']:
        starts.append(batch['start']+batch['count'])
        for arm in 'WRCS':
            prior[arm].extend(project_history_row(r) for r in iter_rows(batch['inputs'][arm]))
    for batch in json_file(root/'batches.json')['batches']:
        if batch['kind'] != 'fresh_pair':
            continue  # These combinations were already reserved by the source builder.
        starts.append(batch['start']+batch['count'])
        for arm in 'WS':
            prior[arm].extend(project_history_row(r) for r in iter_rows(batch['inputs'][arm]))
    frozen = root/'frozen'
    extras = [dict(r, text=r.get('embedding_text') or r.get('text') or '', source_type='official_chunk')
              for name in ('neo4j_docs', 'memgraph_docs') for r in iter_rows(frozen/f'{name}.jsonl')]
    return InputBuilder(read_rows(frozen/'base_inputs.jsonl'), read_rows(frozen/'skeletons.jsonl'),
                        read_rows(frozen/'features.jsonl'), extras,
                        seed=source_manifest['sampling_seed'], prior_inputs=prior, start_index=max(starts, default=0))


def restore_selected_inputs(maps, keys):
    wanted = defaultdict(lambda: defaultdict(list))
    for arm in 'WS':
        for key in keys:
            row = maps[arm][key]
            wanted[Path(row['_paired_input_path'])][row['_paired_input_query_id']].append((arm, key))
    inputs = {a: {} for a in 'WS'}
    source_hashes = []
    for path, ids in sorted(wanted.items()):
        found = set()
        source_hashes.append({'path': str(path.resolve()), 'sha256': file_hash(path)})
        for row in iter_rows(path):
            qid = row['query_id']
            if qid not in ids:
                continue
            if qid in found:
                raise ValueError(f'duplicate selected input: {path}/{qid}')
            found.add(qid)
            for arm, key in ids[qid]:
                inputs[arm][key] = row
        if found != set(ids):
            raise ValueError(f'missing selected inputs: {path}')
    for key in keys:
        validate_pair(key, maps['W'][key], maps['S'][key], inputs['W'][key], inputs['S'][key])
    return inputs, source_hashes


def finalize(root, maps):
    verify_resume_dependencies(root)
    manifest = json_file(root/'manifest.json')
    existing = json_file(root/'baseline_common_keys.json')
    keys = select_joint_keys(maps, existing, manifest['target_pairs'])
    inputs, input_hashes = restore_selected_inputs(maps, keys)
    shuffled = list(keys)
    random.Random(manifest['shuffle_seed']).shuffle(shuffled)
    history = build_history_index(iter_rows(root/'frozen/features.jsonl'))
    per_arm = {a: [] for a in 'WS'}
    selected_rows = {a: [] for a in 'WS'}
    pair_manifest = []
    for i, key in enumerate(shuffled):
        pair_manifest.append(dict(task_key=key, cohort=i//1000+1,
                                  source_id=maps['W'][key]['source_id'],
                                  protocol=maps['W'][key]['rq2_provenance']['protocol'],
                                  skeleton_id=inputs['W'][key].get('selected_skeleton_id') or inputs['W'][key].get('skeleton_id'),
                                  query_ids={a: maps[a][key]['query_id'] for a in 'WS'}))
        for arm in 'WS':
            selected_rows[arm].append(dict(maps[arm][key], selected_feature_ids=inputs[arm][key]['selected_feature_ids'],
                                           paired_task_key=key, paired_cohort=i//1000+1))
    if len(shuffled) != 30000 or len(set(shuffled)) != 30000:
        raise ValueError('final cohort construction requires 30000 distinct paired tasks')
    for arm in 'WS':
        write_rows(root/'retained'/f'{arm}.jsonl', selected_rows[arm])
        for i in range(30):
            rows = selected_rows[arm][i*1000:(i+1)*1000]
            write_rows(root/'cohorts'/arm/f'cohort_{i+1:02d}.jsonl', rows)
            per_arm[arm].append(dict(cohort=i+1, **selection_for_arm(rows, history, arm), **realized_run_metrics(rows, history)))
    write_json(root/'matched_task_manifest.json', pair_manifest)
    stats = []
    for metric, label in zip(METRICS, LABELS):
        w, s = [r[metric] for r in per_arm['W']], [r[metric] for r in per_arm['S']]
        stats.append(dict(metric=metric, label=label, W_values=w, S_values=s, **paired_test(w, s)))
    adjusted = multipletests([r['p_raw'] for r in stats], method='holm')[1]
    for row, value in zip(stats, adjusted):
        row['p_holm_4'] = float(value)
    aggregate = {a: aggregate_run_metrics(per_arm[a]) for a in 'WS'}
    pooled = {a: dict(**selection_for_arm(selected_rows[a], history, a), **realized_run_metrics(selected_rows[a], history)) for a in 'WS'}
    # Supplement inference with paired resampling of whole source-report clusters.
    clusters = defaultdict(list)
    for record in pair_manifest:
        key = record['task_key']
        values = []
        for arm in 'WS':
            q = maps[arm][key]['main_query']
            from src.stage3e_diversity.metrics import detect_semantic_features, feature_pairs
            values.append(bool(feature_pairs(detect_semantic_features(q))-history['mechanism_pairs']))
        clusters[record['source_id']].append(int(values[1])-int(values[0]))
    ordered = sorted(clusters)
    totals = np.array([[sum(clusters[c]), len(clusters[c])] for c in ordered], dtype=np.int64)
    rng = np.random.default_rng(manifest['shuffle_seed'])
    sampled = rng.integers(0, len(ordered), size=(10000, len(ordered)))
    boot = totals[sampled, 0].sum(axis=1)/totals[sampled, 1].sum(axis=1)*100
    result = dict(computed_utc=utc_now(), complete=True, matched_tasks=30000,
                  cohorts=30, queries_per_arm_per_cohort=1000, paired_tasks=True,
                  independent_generator_runs=False, database_execution=False,
                  history={k: len(history[k]) for k in ('report_mechanisms', 'mechanism_pairs', 'historical_feature_pairs')},
                  reused_common_pairs=len(set(keys)&set(existing)),
                  protocol_counts=dict(Counter(r['protocol'] for r in pair_manifest)),
                  aggregate=aggregate, per_arm=per_arm, pooled=pooled, statistics=stats,
                  statistical_options=dict(test='two-sided Wilcoxon signed-rank', method='approx', zero_method='wilcox',
                                           correction=False, difference_rounding_decimals=12,
                                           holm_family='four RQ2 output comparisons',
                                           r='abs(scipy zstatistic)/sqrt(30); signed reported-Z value also stored'),
                  paired_source_report_cluster_bootstrap=dict(repetitions=10000, source_report_clusters=len(ordered),
                       estimate_percentage_points=float(totals[:,0].sum()/totals[:,1].sum()*100),
                       ci95_percentage_points=np.quantile(boot,[.025,.975]).tolist()))
    write_json(root/'results.json', result)
    write_json(root/'selected_input_hashes.json', input_hashes)
    lines = ['# RQ2 W/S: 30 matched 1,000-task cohorts', '',
             'Each cohort contains the same 1,000 underlying tasks under W and S. Cohorts are partitions, not independent generator runs.', '',
             '| Measure | W mean +/- SD | S mean +/- SD | Wilcoxon raw p | Holm p | r = abs(Z)/sqrt(30) | Paired rank-biserial (S-W) |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for metric, label, stat in zip(METRICS, LABELS, stats):
        factor = 100 if metric in METRICS[2:] else 1
        cells = [f"{aggregate[a][metric]['mean']*factor:.2f} +/- {aggregate[a][metric]['sd']*factor:.2f}" for a in 'WS']
        lines.append(f"| {label} | {' | '.join(cells)} | {stat['p_raw']:.6e} | {stat['p_holm_4']:.6e} | {stat['r_magnitude']:.6f} | {stat['rank_biserial_S_minus_W']:.6f} |")
    write_json(root/'summary.json', {k: result[k] for k in ('complete','matched_tasks','cohorts','queries_per_arm_per_cohort','paired_tasks','independent_generator_runs','protocol_counts')})
    (root/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({'status':'complete', 'matched_pairs':30000, 'results':str(root/'results.json')}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--output-root', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--status-only', action='store_true')
    parser.add_argument('--workers', type=int, default=48)
    args = parser.parse_args()
    root, source = args.output_root.resolve(), args.source_root.resolve()
    if not args.resume and not args.status_only:
        prepare(source, root)
    manifest = json_file(root/'manifest.json')
    if args.prepare_only:
        print(json.dumps({k:manifest[k] for k in ('baseline_available_pairs','never_attempted_backfill','target_pairs')}), flush=True)
        return 0
    if args.status_only:
        maps = collect(root)
        print(json.dumps({'matched_pairs':len(set(maps['W'])&set(maps['S'])), 'accepted':{a:len(maps[a]) for a in 'WS'}}))
        return 0
    if not 1 <= args.workers <= 48:
        raise ValueError('workers must be 1..48 globally')
    verify_resume_dependencies(root)
    guard = acquire_lock(root)
    try:
        write_json(root/'state.json', {'status':'running','updated_utc':utc_now(),'error':None})
        builder = None
        while True:
            if (root/'STOP').exists():
                raise RuntimeError('STOP exists; no new calls scheduled')
            batches = json_file(root/'batches.json')['batches']
            for batch in batches:
                verify_files(batch['frozen_files'])
            pending = [j for b in batches for j in b['jobs'] if not job_finished(j)]
            maps = collect(root)
            common = set(maps['W'])&set(maps['S'])
            write_json(root/'progress.json', dict(updated_utc=utc_now(), matched_pairs=len(common), target_pairs=manifest['target_pairs'],
                       accepted={a:len(maps[a]) for a in 'WS'}, pending_jobs=len(pending)))
            print(json.dumps({'matched_pairs':len(common), 'pending_jobs':len(pending)}, ensure_ascii=False), flush=True)
            if len(common) >= manifest['target_pairs'] and not pending:
                finalize(root, maps)
                return 0
            if pending:
                verify_resume_dependencies(root)
                if not (root/'preflight.json').exists():
                    smoke = [next(j for j in pending if j['arm']==a) for a in 'WS' if any(j['arm']==a for j in pending)]
                    execute_jobs(root, smoke, min(args.workers,len(smoke)), preflight=True)
                    write_json(root/'preflight.json', {'passed':True,'created_utc':utc_now(),'jobs':len(smoke)})
                execute_jobs(root, pending, args.workers, preflight=False)
                continue
            if builder is None:
                builder = restore_builder(root)
            start = builder._next_index
            remaining_cap = manifest['new_candidate_cap']-start
            if remaining_cap <= 0:
                raise RuntimeError('precommitted candidate cap reached; no automatic expansion')
            count = min(manifest['batch_size'], remaining_cap)
            rows = builder.make_batch(start, count)
            commit_jobs(root, rows, kind='fresh_pair', start=start, count=count, shards=manifest['shards_per_arm'])
    except BaseException as exc:
        write_json(root/'state.json', {'status':'interrupted_or_failed','updated_utc':utc_now(),'error':str(exc)})
        raise
    finally:
        release_lock(root, guard)


if __name__ == '__main__':
    raise SystemExit(main())
