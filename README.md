# HistDB

Reproduction materials for **HistDB: Historical Bug Knowledge-Guided Testing of Cypher-Compatible Graph Databases**. The reference manuscript is `paper/HistDB.pdf`.

## Bugs

The 148 reported bugs are indexed in [`bugs/index.csv`](bugs/index.csv). Each bug directory contains exactly three files:

```text
bugs/MEM-4180/
  skeleton.json
  features.json
  test.cypher
```

Each directory provides the case's skeleton, feature records, and minimized test. All cases use the same directory structure and JSON fields. `test.cypher` contains the ordered setup statements and the minimized query; Apache AGE cases use `test.sql`.

## Reproduce the archived results

Use Python 3.12:

```bash
python -m venv .venv
# Linux: source .venv/bin/activate
# Windows: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-offline.txt
python scripts/reproduce_archived_results.py all
python -m pytest tests -q
```

No API key or DBMS is needed for these checks. Results are written to `output/archived/`. Individual commands are `verify`, `rq1`, `rq2`, and `rq3`.

| Paper material | Package location |
|---|---|
| Historical skeleton and feature libraries | `artifact_data/historical_knowledge/` |
| RQ1: 148 bug cases | `bugs/` |
| RQ2: WR/CR, m=3, 30 × 1,000 paired queries per setting | `artifact_data/rq2/main/` |
| RQ2: m=4 and m=5 supplementary experiments | `artifact_data/rq2/4features/`, `5features/` |
| RQ3: seven tools, 30 × 1,000 queries per tool | `artifact_data/rq3/` |
| Table 5 and Table 6 numerical data | `paper/tables/` |
| Coverage time series | `paper/coverage_curves/` |
| Bug-family correspondence | `paper/evaluation_data_audit_20261010/baseline_family_correspondence/` |
| Prompts, code and configurations | `prompts/`, `src/`, `configs/` |

The archived RQ2 codes W/S correspond to the paper's WR/CR. RQ2 uses paired Wilcoxon signed-rank tests and |Z|/sqrt(30), with Holm correction over four metrics. The five-cohort m=4/5 supplements use exact tests. RQ3 uses Mann–Whitney U tests and Cliff's delta, with Holm correction over 24 comparisons. The offline command recomputes these values from compressed query records.

The complete historical library contains 1,646 reports, 4,574 skeleton records, 3,591 distinct skeleton texts, and 18,603 feature records. `artifact_data/knowledge/` stores the experiment-specific RQ2 subset and retrieval context. Source hashes identify each snapshot.

## Live generation and database replay

Install `requirements.txt` and set `DEEPSEEK_API_KEY`. Use `configs/model.yaml` for the recorded service settings. Method commands are in `docs/PIPELINE.md`, replay commands in `docs/CATALOG.md`, and baseline/coverage commands in `docs/BASELINES.md`.

The code is limited to the paper's representation, selection/retrieval, construction, execution, validation, reduction, and evaluation paths. `CODE_SCOPE.json` lists their entry points and required dependencies.

The reference paper reports Ubuntu 24.04.1 LTS, 48 virtual CPUs, Intel Xeon Gold 5117 at 2.00 GHz, and 125 GiB RAM.

## Integrity

`ARTIFACT_MANIFEST.json` lists package-relative paths and SHA-256 hashes. `artifact_data/SOURCE_INDEX.json` records source hashes. GitHub Actions runs offline checks on Ubuntu. Live model calls and 24-hour DBMS campaigns are separate from archived-result verification. Attribution is preserved in `NOTICE.md`.
