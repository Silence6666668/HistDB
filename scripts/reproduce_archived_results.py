"""Verify and recompute packaged HistDB results without LLM or DBMS calls."""
from __future__ import annotations

import argparse
from collections import defaultdict
import gzip
import hashlib
from itertools import product
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import warnings

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def rows(path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8-sig') as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def verify_package(root):
    root = root.resolve()
    manifest = load(root / 'ARTIFACT_MANIFEST.json')
    seen = set()
    for item in manifest['files']:
        path = (root / item['path']).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"manifest path outside package: {item['path']}")
        if item['path'] in seen:
            raise ValueError(f"duplicate manifest path: {item['path']}")
        seen.add(item['path'])
        if not path.is_file() or digest(path) != item['sha256']:
            raise ValueError(f"missing file or hash mismatch: {item['path']}")
    print(f'Integrity: {len(seen)} file hashes match', flush=True)
    return manifest


def validate_cohort_pair(w, s, *, feature_count, size=1000):
    if len(w) != size or len(s) != size:
        raise ValueError('paired cohort size mismatch')
    keys = []
    for left, right in zip(w, s):
        if (left.get('paired_task_key') != right.get('paired_task_key') or
                left.get('source_id') != right.get('source_id') or
                not left.get('paired_task_key')):
            raise ValueError('paired task identity or order mismatch')
        keys.append(left['paired_task_key'])
        for row in (left, right):
            ids = row.get('selected_feature_ids') or []
            if len(ids) != feature_count or len(set(ids)) != feature_count:
                raise ValueError('distinct selected feature count mismatch')
    if len(keys) != len(set(keys)):
        raise ValueError('repeated paired task inside cohort')
    return keys


def same(actual, expected, location):
    if expected is None:
        if actual is not None:
            raise ValueError(f'{location}: expected undefined denominator')
    elif not math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-12):
        raise ValueError(f'{location}: recomputed {actual}, archived {expected}')


def compare_metrics(actual, expected, location):
    for key, value in actual.items():
        if key in expected and (value is None or isinstance(value, (float, int))):
            same(value, expected[key], f'{location}/{key}')


def paired_statistics(w, s, exact=False):
    import numpy as np
    from scipy.stats import rankdata, wilcoxon
    differences = np.round(np.asarray(s, dtype=float) - np.asarray(w, dtype=float), 12)
    nonzero = differences[differences != 0]
    if len(nonzero):
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message='Sample size too small')
            test = wilcoxon(differences, zero_method='wilcox', correction=False,
                            alternative='two-sided', method='approx')
        ranks = rankdata(np.abs(nonzero))
        positive = float(ranks[nonzero > 0].sum())
        negative = float(ranks[nonzero < 0].sum())
        z, p = float(test.zstatistic), float(test.pvalue)
    else:
        ranks = []
        positive = negative = z = 0.
        p = 1.
    if exact:
        if len(nonzero) > 15:
            raise ValueError('exact enumeration limited to small sensitivity samples')
        total, observed = float(sum(ranks)), min(positive, negative)
        extreme = 0
        for signs in product((0, 1), repeat=len(nonzero)):
            rank_sum = sum(r for r, bit in zip(ranks, signs) if bit)
            extreme += min(rank_sum, total - rank_sum) <= observed + 1e-12
        p = extreme / 2**len(nonzero)
    return {'p_raw': p, 'z_scipy': z, 'r_magnitude': abs(z) / math.sqrt(len(w)),
            'rank_biserial_S_minus_W': ((positive-negative)/(positive+negative)
                                        if positive+negative else 0.)}


def reproduce_rq2(root, output):
    from statsmodels.stats.multitest import multipletests
    from scripts.analyze_historical_composition_ablation import (
        aggregate_run_metrics, build_history_index, realized_run_metrics, selection_run_metrics)
    from src.stage3e_diversity.metrics import detect_semantic_features, feature_pairs
    import numpy as np

    metrics = ('unique_mechanism_pairs', 'unique_unseen_mechanism_pairs',
               'unique_hupr', 'query_unseen_composition_rate')
    history = build_history_index(rows(root/'artifact_data/knowledge/features.jsonl.gz'))
    reports = {}
    for directory in sorted((root/'artifact_data/rq2').iterdir()):
        if not directory.is_dir():
            continue
        expected = load(directory/'results.json')
        protocol = load(directory/'protocol.json')
        per_arm, seen, clusters = {'W': [], 'S': []}, set(), defaultdict(list)
        count = expected['cohorts']
        for index in range(1, count+1):
            pair = {a: list(rows(directory/'cohorts'/a/f'cohort_{index:02d}.jsonl.gz')) for a in 'WS'}
            keys = validate_cohort_pair(pair['W'], pair['S'], feature_count=protocol['feature_count'])
            if seen.intersection(keys):
                raise ValueError('task repeated across cohorts')
            seen.update(keys)
            for arm in 'WS':
                selection = selection_run_metrics(pair[arm], history)
                if arm == 'W':
                    for row in pair[arm]:
                        if not set(row['selected_feature_ids']) <= history['report_feature_ids'][row['source_id']]:
                            raise ValueError('W features cross report boundary')
                    selection.update(input_assigned_crr_rate=0., selected_cross_report_pair_rate=0.,
                                     selected_crnp_rate=None)
                measured = dict(cohort=index, **selection, **realized_run_metrics(pair[arm], history))
                compare_metrics(measured, expected['per_arm'][arm][index-1], f'{directory.name}/{arm}/{index}')
                per_arm[arm].append(measured)
            if 'paired_source_report_cluster_bootstrap' in expected:
                for w, s in zip(pair['W'], pair['S']):
                    indicators = [int(bool(feature_pairs(detect_semantic_features(r['main_query'])) -
                                           history['mechanism_pairs'])) for r in (w, s)]
                    clusters[w['source_id']].append(indicators[1]-indicators[0])
            print(f'RQ2 {directory.name}: cohort {index}/{count} matches', flush=True)
        if len(seen) != expected['matched_tasks']:
            raise ValueError('total paired task count mismatch')
        aggregate = {a: aggregate_run_metrics(per_arm[a]) for a in 'WS'}
        for arm in 'WS':
            for metric in aggregate[arm]:
                if metric in expected['aggregate'][arm]:
                    compare_metrics(aggregate[arm][metric], expected['aggregate'][arm][metric], metric)
        stats = []
        for metric in metrics:
            w, s = [v[metric] for v in per_arm['W']], [v[metric] for v in per_arm['S']]
            stats.append(dict(metric=metric, **paired_statistics(w, s, exact=count == 5)))
        for actual, archived, adjusted in zip(stats, expected['statistics'],
                multipletests([v['p_raw'] for v in stats], method='holm')[1]):
            actual['p_holm_4'] = float(adjusted)
            compare_metrics(actual, archived, directory.name+'/statistics')
        report = dict(passed=True, matched_tasks=len(seen), feature_count=protocol['feature_count'],
                      cohorts=count, independent_generator_runs=False,
                      aggregate=aggregate, per_arm=per_arm, statistics=stats)
        if clusters:
            totals = np.asarray([[sum(clusters[k]), len(clusters[k])] for k in sorted(clusters)], dtype=np.int64)
            rng = np.random.default_rng(protocol['shuffle_seed'])
            sampled = rng.integers(0, len(totals), size=(10000, len(totals)))
            boot = totals[sampled, 0].sum(axis=1)/totals[sampled, 1].sum(axis=1)*100
            ci = np.quantile(boot, [.025, .975]).tolist()
            archived = expected['paired_source_report_cluster_bootstrap']
            same(totals[:,0].sum()/totals[:,1].sum()*100, archived['estimate_percentage_points'], 'bootstrap estimate')
            for a, b in zip(ci, archived['ci95_percentage_points']):
                same(a, b, 'bootstrap confidence interval')
            report['cluster_bootstrap_ci95_percentage_points'] = ci
        save(output/f'rq2_{directory.name}.json', report)
        reports[directory.name] = {'passed': True, 'paired_tasks': len(seen), 'cohorts': count}
    return reports


def reproduce_rq3(root, output):
    from scipy.stats import mannwhitneyu
    from statsmodels.stats.multitest import multipletests
    from scripts.analyze_repeated_query_diversity import aggregate_run_metrics
    from scripts.analyze_tool_query_sample_metrics import query_metrics, summarize

    directory = root/'artifact_data/rq3'
    expected = load(directory/'summary.json')
    expected_stats = load(directory/'mannwhitney_cliffs_delta.json')
    measured, current, buffer = defaultdict(list), None, []
    seen_groups = set()

    def finish(key, values):
        if key in seen_groups or len(values) != 1000:
            raise ValueError('RQ3 sample is repeated, out of order or incomplete')
        tool, group = key
        if len({r.get('query') or r.get('main_query') for r in values}) != 1000:
            raise ValueError('RQ3 sample contains duplicate query text')
        result = dict(run=group, **summarize(values))
        archived = next(v for v in expected['tools'][tool]['per_run'] if v['run'] == group)
        compare_metrics(result, archived, f'RQ3/{tool}/{group}')
        measured[tool].append(result)
        seen_groups.add(key)
        print(f'RQ3 {tool}: sample {group}/30 matches', flush=True)

    for row in rows(directory/'baseline_samples.jsonl.gz'):
        key = (row['tool'], int(row['run']))
        if current is not None and key != current:
            finish(current, buffer)
            buffer = []
        current = key
        buffer.append(query_metrics(row))
    if current is not None:
        finish(current, buffer)
    if set(measured) != set(expected['tools']) or len(measured) != 7:
        raise ValueError('expected seven RQ3 tools')
    for tool, values in measured.items():
        if {r['run'] for r in values} != set(range(1, 31)):
            raise ValueError(f'incomplete 30-sample tool: {tool}')
        for metric, value in aggregate_run_metrics(values).items():
            compare_metrics(value, expected['tools'][tool]['aggregate'][metric], tool+'/'+metric)
    comparisons = []
    for archived in expected_stats['comparisons']:
        metric, baseline = archived['metric'], archived['baseline']
        x, y = [r[metric] for r in measured['HistDB']], [r[metric] for r in measured[baseline]]
        test = mannwhitneyu(x, y, alternative='two-sided', method='asymptotic', use_continuity=True)
        delta = sum((a > b)-(a < b) for a in x for b in y)/(len(x)*len(y))
        actual = dict(metric=metric, baseline=baseline, U_histdb=float(test.statistic),
                      p_raw_two_sided=float(test.pvalue), cliffs_delta_histdb_minus_baseline=delta)
        compare_metrics(actual, archived, f'RQ3/{baseline}/{metric}')
        comparisons.append(actual)
    for actual, archived, adjusted in zip(comparisons, expected_stats['comparisons'],
            multipletests([v['p_raw_two_sided'] for v in comparisons], method='holm')[1]):
        same(adjusted, archived['p_holm_24'], 'RQ3/Holm')
        actual['p_holm_24'] = float(adjusted)
    report = dict(passed=True, tools=7, samples_per_tool=30, queries_per_sample=1000,
                  independent_generator_runs=False, per_tool=dict(measured), comparisons=comparisons)
    save(output/'rq3_diversity.json', report)
    return {'passed': True, 'tools': 7, 'samples': len(seen_groups), 'queries': 210000, 'comparisons': len(comparisons)}


def reproduce_rq1(root, output):
    if (root/'bugs/index.json').is_file():
        index = load(root/'bugs/index.json')
        if len(index) != 148 or len({r['report_id'] for r in index}) != 148:
            raise ValueError('expected 148 different bug entries')
        for item in index:
            directory = root/'bugs'/item['report_id']
            required = {'skeleton.json', 'features.json', item['test_file']}
            if {p.name for p in directory.iterdir()} != required:
                raise ValueError('compact bug directory must contain exactly three files')
            if not load(directory/'skeleton.json').get('skeleton_text') or not load(directory/'features.json'):
                raise ValueError('empty skeleton or selected features')
            same_hash = digest(directory/item['test_file']) == item['test_sha256']
            if not same_hash:
                raise ValueError('compact test hash mismatch')
        result = {'passed': True, 'bug_entries': 148, 'files_per_bug': 3,
                  'scope': 'preserved input records and reported minimized reproduction programs'}
        save(output/'rq1.json', result)
        print('RQ1: 148 compact bug entries verified', flush=True)
        return result
    package = root/'artifact_data/rq1'
    subprocess.run([sys.executable, str(package/'repro.py'), 'audit'], cwd=root, check=True)
    with tempfile.TemporaryDirectory(prefix='histdb-rq1-') as temporary:
        subprocess.run([sys.executable, str(package/'repro.py'), 'reconstruct',
                        '--output', str(Path(temporary)/'submitted')], cwd=root, check=True)
    result = {'passed': True, 'submitted_reports_reconstructed': 148,
              'scope': 'submitted report programs; archived generation linkage available for 92/148'}
    save(output/'rq1.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('verify', 'rq1', 'rq2', 'rq3', 'all'))
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    verify_package(root)
    if args.command == 'verify':
        return
    output = (args.output_dir or root/'output/archived').resolve()
    results = {}
    for name, run in (('rq1', reproduce_rq1), ('rq2', reproduce_rq2), ('rq3', reproduce_rq3)):
        if args.command in ('all', name):
            results[name] = run(root, output)
    save(output/'verification.json', results)
    print(json.dumps({'passed': True, 'results': results, 'output': str(output)}, indent=2), flush=True)


if __name__ == '__main__':
    main()
