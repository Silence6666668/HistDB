"""Fresh task-matched W/S feature-count sensitivity, without changing old runs."""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from itertools import product
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import threading
import warnings

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
from scipy.stats import rankdata
from statsmodels.stats.multitest import multipletests

from scripts.rq2_current_inputs import InputBuilder
from scripts.build_random_conditioned_skeleton_ablation import _require_skeletons
from scripts.build_within_report_skeleton_ablation import group_features_by_source
from src.stage2.feature_selection import score_skeleton_feature_affinity, select_feature_records
from scripts.run_rq2_ws_paired_completion import (
    DEFAULT_OUTPUT as THREE_FEATURE_RUN, LABELS, METRICS, aggregate_run_metrics,
    build_history_index, collect, commit_jobs, file_hash, iter_rows, json_file,
    paired_test, realized_run_metrics, selection_for_arm, utc_now,
    verify_files, verify_resume_dependencies, write_json, write_rows,
)
from scripts.run_rq2_completion import acquire_lock, execute_jobs, job_finished, release_lock, validate_job
from scripts.run_rq2_complexity_pilot import read_rows, stage3_command

DEFAULT_OUTPUT = REPO_ROOT/'data/ablation_runs/rq2_ws_5features_5000_20261015'


class FeatureCountInputBuilder(InputBuilder):
    """Reuse unchanged retrieval/row construction; reserve m-feature W/S draws."""

    def __init__(self, base_inputs, skeletons, features, extra_fragments, *, feature_count=5,
                 eligibility_feature_count=None, **kwargs):
        if not isinstance(feature_count, int) or feature_count < 1:
            raise ValueError('feature_count must be a positive integer')
        if eligibility_feature_count is None:
            eligibility_feature_count = feature_count
        if not isinstance(eligibility_feature_count, int) or eligibility_feature_count < feature_count:
            raise ValueError('eligibility_feature_count must be an integer >= feature_count')
        self.feature_count = feature_count
        self.eligibility_feature_count = eligibility_feature_count
        mapping = _require_skeletons(base_inputs, skeletons)
        by_source = group_features_by_source(features)
        eligible = []
        for base in base_inputs:
            skeleton = mapping[base['query_id']]
            source = str(skeleton.get('source_id') or '').strip()
            positive = sum(score_skeleton_feature_affinity(skeleton, f) > 0 for f in by_source.get(source, []))
            if positive >= eligibility_feature_count:
                eligible.append(base)
        if not eligible:
            raise ValueError('no tasks with enough same-report positive-support features')
        super().__init__(eligible, skeletons, features, extra_fragments, **kwargs)

    def _restore_history(self, prior_inputs):
        seen = set()
        for arm, rows in prior_inputs.items():
            if arm not in ('W', 'S'):
                raise ValueError('only W/S histories supported')
            if arm == 'W':
                continue
            for row in rows:
                ids = frozenset(row['selected_feature_ids'])
                if len(row['selected_feature_ids']) != self.feature_count or len(ids) != self.feature_count:
                    raise ValueError('prior feature count mismatch')
                key = self._historical_skeleton_key(row)
                identity = (key, row['query_id'], ids)
                if identity in seen:
                    continue
                seen.add(identity)
                self._used['C'][key].add(ids)
                self._draws['C'][key] += 1

    def make_batch(self, start, count):
        if not isinstance(start, int) or start != self._next_index:
            raise ValueError('start must equal next contiguous candidate index')
        if not isinstance(count, int) or count <= 0:
            raise ValueError('count must be positive')
        used = defaultdict(set, {k: set(v) for k, v in self._used['C'].items()})
        draws = defaultdict(int, self._draws['C'])
        result = {'W': [], 'S': []}
        for index in range(start, start+count):
            source_base = self._eligible[index % self.eligible_count]
            original = str(source_base['query_id']).strip()
            skeleton = self._skeletons[original]
            key = self._skeleton_key(skeleton, original)
            draw = draws[key]
            base = copy.deepcopy(source_base)
            base.update(query_id=f'rq2_fc{self.feature_count}_{index:07d}:{original}',
                        original_query_id=original, skeleton_id=skeleton.get('skeleton_id'),
                        rq2_candidate_index=index, feature_selection_skeleton_key=key,
                        feature_count=self.feature_count,
                        feature_combination_deduplication='unordered_feature_ids_per_original_skeleton')
            source = str(skeleton.get('source_id') or '').strip()
            selections = {
                'S': select_feature_records(skeleton, query_features=[], all_features=self._features,
                         mode='random_constrained', sample_size=self.feature_count, seed=self._seed,
                         draw_index=draw, used_combinations=used[key]),
                'W': select_feature_records(skeleton, query_features=[], all_features=self._features_by_source[source],
                         mode='random_constrained', sample_size=self.feature_count, seed=self._seed,
                         rng_salt='within-report', draw_index=draw),
            }
            for arm in 'WS':
                row = self._row(base, selections[arm], selection=(
                    'within_report_random_constrained' if arm == 'W' else 'random_constrained'), draw_index=draw)
                row.update(selected_skeleton_id=skeleton.get('skeleton_id'),
                           guidance_skeleton_text=str(skeleton.get('skeleton_text') or ''),
                           generation_strategy='feature-driven-soft')
                if arm == 'W':
                    row.update(composition_scope='within_report_features', same_report_source_id=source,
                               selected_feature_source_ids=sorted({f['source_id'] for f in selections[arm]}),
                               feature_combination_deduplication='not_applicable_within_report')
                result[arm].append(row)
            validate_inputs(result['W'][-1], result['S'][-1], self.feature_count)
            draws[key] += 1
        self._used['C'], self._draws['C'], self._next_index = used, draws, start+count
        return result


def validate_inputs(w, s, feature_count):
    for field in ('query_id', 'original_query_id', 'source_id', 'selected_skeleton_id', 'guidance_skeleton_text'):
        if w.get(field) != s.get(field):
            raise ValueError(f'paired input mismatch: {field}')
    if not w.get('guidance_skeleton_text'):
        raise ValueError('empty skeleton guidance')
    for row in (w, s):
        ids = row.get('selected_feature_ids') or []
        if len(ids) != feature_count or len(set(ids)) != feature_count:
            raise ValueError('input feature count mismatch')


def preflight_arm_passed(job):
    """A retained or statically rejected completed task proves the arm can run."""
    out = Path(job['output'])
    completed = {str(r.get('query_id')) for r in read_rows(out/'completed_variants.jsonl', live=True)}
    non_errors = {
        str(r.get('query_id'))
        for name in ('instantiated_cases.jsonl', 'rejected_cases.jsonl')
        for r in read_rows(out/name, live=True)
    }
    return bool(completed & non_errors)


def build_preflight_probe(root, job):
    """Point Stage3 at the first unfinished committed row, preserving old errors."""
    out = Path(job['output'])
    completed = {str(r.get('query_id')) for r in read_rows(out/'completed_variants.jsonl', live=True)}
    row = next((r for r in iter_rows(Path(job['input'])) if str(r.get('query_id')) not in completed), None)
    if row is None:
        raise ValueError(f"no unfinished preflight input: {job['arm']}/shard-{job['shard']}")
    probe_path = (Path(root)/'preflight/probes'/
                  f"{job['arm']}_shard_{int(job['shard']):02d}_after_{len(completed):04d}.jsonl")
    if probe_path.exists():
        if list(iter_rows(probe_path)) != [row]:
            raise ValueError(f'preflight probe content mismatch: {probe_path}')
    else:
        write_rows(probe_path, [row])
    return dict(job, input=str(probe_path.resolve()), count=1,
                preflight_query_id=str(row['query_id']),
                preflight_error_count_before=len(read_rows(out/'errors.jsonl', live=True)))


def execute_preflight_jobs(root, probes, workers):
    """Run singleton probes in their real shard outputs and assess only the new task."""
    cancelled = threading.Event()

    def execute(probe):
        if cancelled.is_set() or (Path(root)/'STOP').exists():
            raise RuntimeError('stopped before launching a preflight probe')
        out = Path(probe['output'])
        out.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
        env.pop('PYTHONPATH', None)
        log = out/'preflight.log'
        with log.open('a', encoding='utf-8') as handle:
            result = subprocess.run(stage3_command(probe, Path(root), preflight=False),
                                    cwd=Path(root)/'code_snapshot', env=env, stdout=handle,
                                    stderr=subprocess.STDOUT, timeout=86400)
        if result.returncode:
            cancelled.set()
            raise RuntimeError(f'Stage3 exited {result.returncode}; inspect {log}')
        qid = probe['preflight_query_id']
        completed = {str(r.get('query_id')) for r in read_rows(out/'completed_variants.jsonl')}
        errors = {str(r.get('query_id')) for r in read_rows(out/'errors.jsonl')}
        non_errors = {
            str(r.get('query_id'))
            for name in ('instantiated_cases.jsonl', 'rejected_cases.jsonl')
            for r in read_rows(out/name)
        }
        if qid not in completed:
            cancelled.set()
            raise RuntimeError(f'preflight task lacks final disposition: {qid}')
        if qid in errors:
            cancelled.set()
            raise RuntimeError(f'preflight task recorded an operational error: {qid}; inspect {out/"errors.jsonl"}')
        if qid not in non_errors:
            cancelled.set()
            raise RuntimeError(f'preflight task lacks accepted/rejected outcome: {qid}')
        return dict(arm=probe['arm'], shard=probe['shard'], query_id=qid,
                    prior_errors=probe['preflight_error_count_before'])

    failures, results = [], []
    with ThreadPoolExecutor(max_workers=min(workers, len(probes))) as pool:
        futures = {pool.submit(execute, probe): probe for probe in probes}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                cancelled.set()
                failures.append(str(exc))
    if failures:
        raise RuntimeError('; '.join(failures))
    return results


def paired_exact_test(w, s):
    # Keep the original standardized Z/effect, but do not use small-N normal p.
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message='Sample size too small')
        result = paired_test(w, s)
    differences = np.round(np.asarray(s, dtype=float)-np.asarray(w, dtype=float), 12)
    nonzero = differences[differences != 0]
    ranks = rankdata(np.abs(nonzero))
    total = float(ranks.sum())
    observed = min(float(ranks[nonzero > 0].sum()), float(ranks[nonzero < 0].sum()))
    if len(nonzero) > 15:
        raise ValueError('exact enumeration is reserved for this small-cohort experiment')
    extreme = sum(min(sum(r for r, bit in zip(ranks, signs) if bit),
                      total-sum(r for r, bit in zip(ranks, signs) if bit)) <= observed+1e-12
                  for signs in product((0, 1), repeat=len(nonzero)))
    result['p_normal_approximation_diagnostic_only'] = result['p_raw']
    result['p_raw'] = extreme/(2**len(nonzero))
    result['test'] = 'two-sided exact sign-enumerated Wilcoxon signed-rank'
    result['sign_assignments'] = 2**len(nonzero)
    return result


def verify(root):
    manifest = json_file(root/'manifest.json')
    verify_files(manifest['source_files']+manifest['frozen_files'])
    for batch in json_file(root/'batches.json')['batches']:
        verify_files(batch['frozen_files'])


def make_builder(root):
    verify(root)
    frozen = root/'frozen'
    manifest = json_file(root/'manifest.json')
    prior, start = {'W': [], 'S': []}, 0
    for batch in json_file(root/'batches.json')['batches']:
        start = max(start, batch['start']+batch['count'])
        for arm in 'WS':
            for row in iter_rows(Path(batch['inputs'][arm])):
                prior[arm].append({k: row[k] for k in ('query_id', 'original_query_id', 'skeleton_id', 'selected_feature_ids')})
    extras = [dict(r, text=r.get('embedding_text') or r.get('text') or '', source_type='official_chunk')
              for name in ('neo4j_docs', 'memgraph_docs') for r in iter_rows(frozen/f'{name}.jsonl')]
    return FeatureCountInputBuilder(list(iter_rows(frozen/'base_inputs.jsonl')), list(iter_rows(frozen/'skeletons.jsonl')),
                                   list(iter_rows(frozen/'features.jsonl')), extras,
                                   feature_count=manifest['feature_count'],
                                   eligibility_feature_count=manifest.get('eligibility_feature_count',
                                                                          manifest['feature_count']),
                                   seed=manifest['sampling_seed'],
                                   prior_inputs=prior, start_index=start)


def prepare(root, source, feature_count, eligibility_feature_count=None):
    if root.exists():
        raise FileExistsError(f'refusing to overwrite run: {root}')
    verify_resume_dependencies(source)
    source_paths = [source/'manifest.json', source/'results.json', source/'final_verification.json']
    source_paths += [p for p in (source/'frozen').rglob('*') if p.is_file()]
    sources = [dict(path=str(p.resolve()), sha256=file_hash(p)) for p in source_paths]
    root.mkdir(parents=True)
    shutil.copytree(source/'frozen', root/'frozen')
    shutil.copytree(source/'code_snapshot', root/'code_snapshot')
    scripts = ['run_rq2_feature_count_sensitivity.py', 'run_rq2_ws_paired_completion.py',
               'analyze_historical_composition_ablation.py', 'rq2_current_inputs.py',
               'run_rq2_completion.py', 'run_rq2_complexity_pilot.py',
               'build_random_conditioned_skeleton_ablation.py', 'build_within_report_skeleton_ablation.py',
               'rq2_existing_sources.py', 'split_jsonl_into_shards.py']
    live = list((REPO_ROOT/'src').rglob('*.py'))+[REPO_ROOT/'src/stage2/documentation_example_review.json']
    live += [REPO_ROOT/'scripts'/name for name in scripts]
    for p in live:
        dest = root/'code_snapshot'/p.relative_to(REPO_ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
        sources.append(dict(path=str(p.resolve()), sha256=file_hash(p)))
    frozen = [dict(path=str(p.resolve()), sha256=file_hash(p))
              for directory in ('frozen', 'code_snapshot') for p in (root/directory).rglob('*')
              if p.is_file() and p.suffix != '.pyc']
    if eligibility_feature_count is None:
        eligibility_feature_count = feature_count
    manifest = dict(created_utc=utc_now(), source=str(source), feature_count=feature_count,
                    eligibility_feature_count=eligibility_feature_count,
                    target_pairs=5000, cohorts=5, cohort_size=1000, batch_size=1000, candidate_cap=15000,
                    sampling_seed=20261013, shuffle_seed=20261014, shards_per_arm=24, concurrency_cap=48,
                    independent_generator_runs=False, paired_tasks=True, database_execution=False,
                    source_files=sources, frozen_files=frozen, old_output_reuse=0,
                    quality_retry_limit=2, generation_profile='portable_crossdb', complexity_profile='default',
                    diversity_profile='semantic_edges', prior_rejections_and_errors_final=True)
    write_json(root/'manifest.json', manifest)
    write_json(root/'batches.json', {'batches': []})
    for arm in 'WS':
        write_rows(root/'reused'/f'{arm}.jsonl', [])
    builder = make_builder(root)
    manifest.update(eligible_tasks=builder.eligible_count, eligible_source_reports=builder.source_report_count)
    write_json(root/'manifest.json', manifest)
    write_json(root/'state.json', dict(status='prepared', error=None, updated_utc=utc_now()))
    return builder


def finalize(root, maps):
    verify(root)
    manifest = json_file(root/'manifest.json')
    keys = sorted(set(maps['W']) & set(maps['S']))[:manifest['target_pairs']]
    if len(keys) != 5000 or len(set(keys)) != 5000:
        raise ValueError('requires 5000 distinct jointly retained tasks')
    inputs, output_hashes = {'W': {}, 'S': {}}, []
    wanted = set(keys)
    for batch in json_file(root/'batches.json')['batches']:
        for arm in 'WS':
            for row in iter_rows(Path(batch['inputs'][arm])):
                if row['query_id'] in wanted:
                    if row['query_id'] in inputs[arm]:
                        raise ValueError('duplicate committed input')
                    inputs[arm][row['query_id']] = row
        for job in batch['jobs']:
            validate_job(job)
            for path in Path(job['output']).glob('*.jsonl'):
                output_hashes.append(dict(path=str(path), sha256=file_hash(path)))
    for key in keys:
        validate_inputs(inputs['W'][key], inputs['S'][key], manifest['feature_count'])
        if maps['W'][key]['source_id'] != inputs['W'][key]['source_id'] or maps['S'][key]['source_id'] != inputs['S'][key]['source_id']:
            raise ValueError('output source mismatch')
    random.Random(manifest['shuffle_seed']).shuffle(keys)
    history = build_history_index(iter_rows(root/'frozen/features.jsonl'))
    per_arm, retained = {'W': [], 'S': []}, {'W': [], 'S': []}
    for arm in 'WS':
        for key in keys:
            retained[arm].append(dict(maps[arm][key], selected_feature_ids=inputs[arm][key]['selected_feature_ids'],
                                      paired_task_key=key, feature_count=manifest['feature_count']))
        write_rows(root/'retained'/f'{arm}.jsonl', retained[arm])
        for group in range(5):
            rows = retained[arm][group*1000:(group+1)*1000]
            write_rows(root/'cohorts'/arm/f'cohort_{group+1:02d}.jsonl', rows)
            per_arm[arm].append(dict(cohort=group+1, **selection_for_arm(rows, history, arm),
                                     **realized_run_metrics(rows, history)))
    stats = []
    for metric, label in zip(METRICS, LABELS):
        w, s = [r[metric] for r in per_arm['W']], [r[metric] for r in per_arm['S']]
        stats.append(dict(metric=metric, label=label, W_values=w, S_values=s, **paired_exact_test(w, s)))
    for row, p in zip(stats, multipletests([r['p_raw'] for r in stats], method='holm')[1]):
        row['p_holm_4'] = float(p)
    aggregate = {a: aggregate_run_metrics(per_arm[a]) for a in 'WS'}
    pooled = {a: dict(**selection_for_arm(retained[a], history, a), **realized_run_metrics(retained[a], history)) for a in 'WS'}
    result = dict(complete=True, computed_utc=utc_now(), feature_count=manifest['feature_count'], matched_tasks=5000,
                  available_pairs=len(set(maps['W']) & set(maps['S'])), cohorts=5, cohort_size=1000,
                  independent_generator_runs=False, database_execution=False, aggregate=aggregate,
                  per_arm=per_arm, pooled=pooled, statistics=stats,
                  test='two-sided exact sign-enumerated Wilcoxon signed-rank',
                  effect_size='abs(standardized Wilcoxon Z)/sqrt(actual paired cohort count=5)',
                  history_boundary='report-level union of recorded instance element labels; generated main_query only')
    write_json(root/'results.json', result)
    write_json(root/'matched_task_manifest.json', [dict(task_key=k, cohort=i//1000+1,
               source_id=inputs['W'][k]['source_id'], skeleton_id=inputs['W'][k]['selected_skeleton_id']) for i, k in enumerate(keys)])
    write_json(root/'analysis_output_hashes.json', output_hashes)
    verify_files(output_hashes)
    verify(root)
    write_json(root/'verification.json', dict(passed=True, paired_input_identities_checked=5000,
               distinct_features_per_input=manifest['feature_count'], no_task_reuse=True,
               cohorts=5, cohort_size=1000,
               source_and_frozen_files_unchanged=True, generation_outputs_unchanged_during_analysis=True))
    lines = ['# RQ2：五特征，5组×1,000对任务', '',
             '全新生成，每侧5,000条共同保留主查询；5个组为配对产物划分，不是独立生成运行。', '',
             '| 指标 | W：均值 ± SD | S：均值 ± SD | exact raw p | Holm p | 效应强度 \\|r\\| |',
             '|---|---:|---:|---:|---:|---:|']
    for metric, label, stat in zip(METRICS, LABELS, stats):
        factor = 100 if metric in METRICS[2:] else 1
        cells = [f"{aggregate[a][metric]['mean']*factor:.2f} ± {aggregate[a][metric]['sd']*factor:.2f}" for a in 'WS']
        lines.append(f"| {label} | {' | '.join(cells)} | {stat['p_raw']:.6g} | {stat['p_holm_4']:.6g} | {stat['r_magnitude']:.6f} |")
    lines += ['', '## 所选特征对的来源检查', '',
              '| 设置 | 跨报告任务 (%) | 所选对中的历史未见比例 (%) | 跨报告所选对中的历史未见比例 (%) |',
              '|---|---:|---:|---:|']
    for arm in 'WS':
        cells = []
        for metric in ('input_strict_crr_rate', 'selected_occurrence_hupr', 'selected_crnp_rate'):
            entry = aggregate[arm].get(metric)
            cells.append('—' if entry is None else f"{entry['mean']*100:.2f} ± {entry['sd']*100:.2f}")
        lines.append(f"| {arm} | {' | '.join(cells)} |")
    lines += ['', '来源检查以feature IDs为单位，输出指标以主查询元素对为单位；不能把两者当成同一指标。', '',
              '小样本检验采用所有非零配对差值的精确符号枚举，Holm覆盖四项比较。样本量为5，效应分母为√5；正态近似p仅保存作诊断，不用于正文。5组双侧精确p最小0.0625，不能以此宣称p<0.05。',
              '结果以共同静态保留为条件；本轮未执行数据库。五特征可用任务范围不同，不能把与原三特征数据的全部差异归因于特征数。',
              '生成配置、可选范围及全部拒绝/错误见manifest.json和各批目录；全部逐组值、合并值、Z及统计见results.json。']
    (root/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    write_json(root/'state.json', dict(status='complete', updated_utc=utc_now(), error=None, matched_pairs=5000))
    print(json.dumps(dict(status='complete', matched_pairs=5000, results=str(root/'results.json'))), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--source-root', type=Path, default=THREE_FEATURE_RUN)
    parser.add_argument('--feature-count', type=int, default=5)
    parser.add_argument('--eligibility-feature-count', type=int)
    parser.add_argument('--workers', type=int, default=48)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--status-only', action='store_true')
    args = parser.parse_args()
    root = args.output_root.resolve()
    if not 1 <= args.workers <= 48:
        raise ValueError('workers must be 1..48')
    builder = None
    if not args.resume and not args.status_only:
        builder = prepare(root, args.source_root.resolve(), args.feature_count,
                          args.eligibility_feature_count)
    if args.prepare_only:
        print(json.dumps({k: json_file(root/'manifest.json')[k] for k in
              ('feature_count', 'eligibility_feature_count', 'target_pairs', 'eligible_tasks',
               'eligible_source_reports')}), flush=True)
        return
    if args.status_only:
        maps = collect(root)
        print(json.dumps(dict(matched_pairs=len(set(maps['W']) & set(maps['S'])), accepted={a: len(maps[a]) for a in 'WS'})))
        return
    verify(root)
    manifest = json_file(root/'manifest.json')
    if args.feature_count != manifest['feature_count']:
        raise ValueError('resume feature count mismatch')
    requested_eligibility = (args.eligibility_feature_count if args.eligibility_feature_count is not None
                             else args.feature_count)
    if requested_eligibility != manifest.get('eligibility_feature_count', manifest['feature_count']):
        raise ValueError('resume eligibility feature count mismatch')
    guard = acquire_lock(root)
    try:
        write_json(root/'state.json', dict(status='running', updated_utc=utc_now(), error=None))
        while True:
            if (root/'STOP').exists():
                raise RuntimeError('STOP exists; no new calls scheduled')
            verify(root)
            batches = json_file(root/'batches.json')['batches']
            pending = [j for b in batches for j in b['jobs'] if not job_finished(j)]
            maps = collect(root)
            common = len(set(maps['W']) & set(maps['S']))
            progress = dict(updated_utc=utc_now(), matched_pairs=common, target_pairs=5000,
                            accepted={a: len(maps[a]) for a in 'WS'}, pending_jobs=len(pending))
            write_json(root/'progress.json', progress)
            print(json.dumps(progress), flush=True)
            if common >= 5000 and not pending:
                finalize(root, maps)
                return
            if pending:
                if not (root/'preflight.json').exists():
                    all_jobs = [j for b in batches for j in b['jobs']]
                    passed = {a: any(preflight_arm_passed(j) for j in all_jobs if j['arm'] == a) for a in 'WS'}
                    probes = [build_preflight_probe(root, next(j for j in pending if j['arm'] == a))
                              for a in 'WS' if not passed[a]]
                    attempts = execute_preflight_jobs(root, probes, min(args.workers, len(probes))) if probes else []
                    passed = {a: any(preflight_arm_passed(j) for j in all_jobs if j['arm'] == a) for a in 'WS'}
                    if not all(passed.values()):
                        raise RuntimeError(f'preflight incomplete after probes: {passed}')
                    write_json(root/'preflight.json', dict(passed=True, created_utc=utc_now(),
                                                           arms=passed, attempts=attempts))
                execute_jobs(root, pending, args.workers, preflight=False)
                continue
            if builder is None:
                builder = make_builder(root)
            start = builder._next_index
            count = min(manifest['batch_size'], manifest['candidate_cap']-start)
            if count <= 0:
                raise RuntimeError('precommitted candidate cap reached; no automatic expansion')
            commit_jobs(root, builder.make_batch(start, count), kind='fresh_pair', start=start,
                        count=count, shards=manifest['shards_per_arm'])
    except BaseException as exc:
        write_json(root/'state.json', dict(status='interrupted_or_failed', updated_utc=utc_now(), error=str(exc)))
        raise
    finally:
        release_lock(root, guard)


if __name__ == '__main__':
    main()
