# Bug cases and database replay

The 148 cases are indexed in `bugs/index.csv`. Each case directory contains
`skeleton.json`, `features.json`, and `test.cypher` (or `test.sql` for Apache AGE).
Execute the setup statements in file order before the final query.

The case counts for Neo4j, Memgraph, FalkorDB, ArcadeDB, and Apache AGE are
14, 26, 40, 53, and 15. The 50 cases used in the fixed-version baseline
comparison are a subset of these 148 cases, with counts 3, 13, 11, 20, and 3.
Their execution manifests are in `reproduction/cases/`, and the report
correspondence is in `reproduction/family-map.csv`.

## Offline checks

```bash
python -m pip install -r requirements-offline.txt
python scripts/reproduce_archived_results.py all
python -m pytest tests -q
```

## Database replay

Install `requirements.txt` and provide the DBMS endpoints configured in
`reproduction/db_targets.yaml`. Docker service profiles are defined in
`reproduction/docker-compose.yml`; target versions are recorded in
`reproduction/targets.yaml` and the individual case manifests.

```bash
python -m src.cli.reproduce validate
python -m src.cli.reproduce audit
python -m src.cli.reproduce list --paper-set fixed-version-50
python -m src.cli.reproduce run --case HISTDB-MEM-001 --output-dir output/reproduction/memgraph
```

For an isolated Docker smoke run, use PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_reproduction_smoke.ps1 -Dbms memgraph
```

The smoke runner creates a unique Compose project and writes its observations
under `output/reproduction/smoke/`. Use the case-specific version in its manifest
when replaying a historical finding.
