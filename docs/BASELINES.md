# Baselines and coverage

`paper/fixed_baseline_versions.md` records target versions;
`paper/baseline_expansion_matrix.md` records supported tool-target combinations.
Baseline source repositories are configured in
`configs/baseline_tool_repos.env.example`; external tools are fetched separately.

```bash
bash scripts/fetch_baseline_tools.sh --optional
```

The retained baseline runners are:

| Tool | Runner |
|---|---|
| Dinkel | `scripts/run_dinkel_neo4j_jacoco_24h.ps1` |
| GDsmith | `scripts/run_gdsmith_neo4j_jacoco_24h.ps1` |
| GDBMeter | `scripts/run_gdbmeter_external_neo4j_jacoco_24h.ps1` |
| Gamera | `scripts/run_gamera_neo4j_jacoco_24h.ps1` |
| GRev | `scripts/run_grev_fixed_target_longrun.ps1` |
| GRASpDB | `scripts/run_graspdb_baseline_24h.sh` |

Use PowerShell `Get-Help <runner> -Full` or inspect its `param` block to configure
tool paths, target endpoints, output directories, and time limits.

Neo4j JaCoCo collection helpers are included alongside these runners.
`scripts/run_memgraph_source_lcov_command.sh` runs a command against a
source-instrumented Memgraph build and collects lcov observations;
`scripts/run_histdb_memgraph_coverage_epoch.sh` invokes the HistDB replay path.
`scripts/run_stage4_coverage_only.py` supplies coverage-only replay.

The paper's archived coverage time series are in `paper/coverage_curves/`.
The archived seven-tool query corpora, four diversity measures, and 24
Mann-Whitney U / Cliff's delta comparisons are reproduced with:

```bash
python scripts/reproduce_archived_results.py rq3
```
