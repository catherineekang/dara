# Experiments for the final report

Everything behind the numbers in the DSA4213 final report. Run from `backend/`
with the project virtualenv (`../.venv/bin/python`).

| Script | Model calls | What it does | Output |
|---|---|---|---|
| `offline_experiments.py` | none | Payoff structure of each case (Pareto frontier, split, history), non-LLM baselines (Nash calculator on a guessed weighting, split, demand everything), world-model stability and guess robustness | `results/offline.json` |
| `report_experiments.py eval --case C --rep R` | ~67 | One repeat of the live evaluation: B0, B1, B2, B2 with 1/2 rounds, B2 without final offers, B2 with full information, plus judge reliability tests, evaluator rubrics and (rep 0) contamination probes | `results/C_repR.json` |
| `report_experiments.py mirror --case C --rep R` | ~10 | Brief-diversity ablation: both agents get the focus country's brief | `results/C_mirror_repR.json` |
| `analyse_report.py` | none | Aggregates all repeats: means, spreads, paired deltas, judge statistics | `results/summary.json` |
| `tables_report.py` | none | LaTeX tables and number macros for the report (standard library only) | `../report/generated/` |
| `figures_report.py` | none | Per-run forest plot | `../report/figures/` |

Cases: `haze_real`, `covid_my_2020`. The report uses reps 0–4 for `eval` and 0–2 for `mirror`.

Live runs use the Claude Code CLI in headless mode (`crisis_debate/claude_cli.py`,
model `claude-sonnet-5-5`, billed to a Claude Max subscription). Temperature is
the service default and cannot be set, so reruns will not match exactly.

```bash
cd backend
../.venv/bin/python experiments/offline_experiments.py
for c in haze_real covid_my_2020; do for r in 0 1 2 3 4; do
  ../.venv/bin/python experiments/report_experiments.py eval --case $c --rep $r
done; done
../.venv/bin/python experiments/analyse_report.py
../.venv/bin/python experiments/tables_report.py
../.venv/bin/python experiments/figures_report.py
```

`results/logs/` and `*.checkpoint.jsonl` are run-time byproducts and are not committed.
