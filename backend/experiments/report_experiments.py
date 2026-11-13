"""Experiments for the final report, run on the Claude CLI backend.

    python experiments/report_experiments.py eval   --case haze_real --rep 0
    python experiments/report_experiments.py mirror --case haze_real --rep 0

`eval`   one repeat of the paper's evaluation (evaluation.run_case with
         repeats=1): B0, B1, B2, B2 at 1 and 2 rounds, B2 with full
         information, B2 without final offers, plus the evaluator rubric,
         judge tests and (repeat 0 only) the contamination probes.
`mirror` the brief-diversity ablation (proposal §4.1): the counterpart is
         given Singapore's own brief and valuations, so any disagreement left
         in the debate comes from sampling, not from assigned interests.

Each invocation writes one JSON file to experiments/results/, so repeats can run in
parallel processes and a failed one is re-run alone.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crisis_debate import evaluation, pipeline, scenario as scenario_mod, situational  # noqa: E402
from crisis_debate.claude_cli import ClaudeCLIBackend  # noqa: E402
from crisis_debate.utilities import Frontier, score_package  # noqa: E402

OUT = Path(__file__).resolve().parent / "results"


def factory(scenario, label, rep):
    return ClaudeCLIBackend()


def cmd_eval(case: str, rep: int) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    ckpt = OUT / f"{case}_rep{rep}.checkpoint.jsonl"
    started = time.time()
    res = evaluation.run_case(case, factory, repeats=1, judge_repeats=3,
                              include_rounds=True, include_full_info=True, evaluator=True,
                              include_probes=(rep == 0), checkpoint=ckpt,
                              log=lambda m: print(m, flush=True))
    for r in res["runs"]:
        r["repeat"] = rep
    res["repeat"] = rep
    res["wall_seconds"] = round(time.time() - started, 1)
    path = OUT / f"{case}_rep{rep}.json"
    path.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def mirror_scenario(case: str):
    s = scenario_mod.load(case)
    focus = s.brief(s.focus_country)
    m = s.model_copy(deep=True)
    for i, c in enumerate(m.countries):
        if c.name != s.focus_country:
            clone = focus.model_copy(deep=True)
            clone.name = c.name
            m.countries[i] = clone
    m.scenario_id = s.scenario_id          # same cutoff and sources
    return m


def cmd_mirror(case: str, rep: int) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    s = mirror_scenario(case)
    backend = ClaudeCLIBackend()
    started = time.time()
    run = pipeline.run(s, backend, condition="B2", rounds=3,
                       priorities=(pipeline.RQ_PRIORITY,),
                       conditions=situational.load_snapshot(case))
    rec = pipeline.rq_recommendation(run)
    row = {
        "case": case, "repeat": rep, "label": "B2 · mirrored briefs",
        "seconds": round(time.time() - started, 1), "usage": backend.usage,
        "model": backend.resolved_model, "illegal": run.illegal_packages,
        "package": rec.package(),
        "dynamics": evaluation.round_dynamics(run, None),
        "transcript": [[a.model_dump() for a in rnd] for rnd in run.rounds],
        "judgments": [j.model_dump() for j in run.judgments],
    }
    if not run.illegal_packages:
        row["outcome_mirror"] = score_package(Frontier.build(s), rec.package())
    path = OUT / f"{case}_mirror_rep{rep}.json"
    path.write_text(json.dumps(row, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("eval", "mirror"))
    ap.add_argument("--case", required=True)
    ap.add_argument("--rep", type=int, default=0)
    a = ap.parse_args()
    p = cmd_eval(a.case, a.rep) if a.mode == "eval" else cmd_mirror(a.case, a.rep)
    print(f"wrote {p}")
