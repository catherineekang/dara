"""The backend evaluation (paper §5): plumbing only, with scripted agents."""
from __future__ import annotations

import json

from crisis_debate import evaluation, scenario as scenario_mod
from crisis_debate.fakes import ScriptedBackend


def test_the_whole_evaluation_runs_and_writes_its_three_files(tmp_path):
    out = evaluation.run_evaluation(["covid_my_2020"], lambda sc, label, rep: ScriptedBackend(scenario=sc),
                                    repeats=1, judge_repeats=1, mode="test", out_dir=tmp_path,
                                    log=lambda _: None)
    folder = tmp_path / out["folder"].split("/")[-1]
    data = json.loads((folder / "results.json").read_text())
    labels = {r["label"] for r in data["cases"][0]["runs"]}
    assert {"B0", "B1", "B2", "B2 · 1 round", "B2 · 2 rounds", "B2 · full information"} <= labels
    report = (folder / "report.md").read_text()
    for section in ("Why language agents", "Round-count ablation", "Debater evaluation", "Judge",
                    "Recommender", "aggregates", "Contamination"):
        assert section in report
    assert (folder / "human_eval_sheet.csv").exists() and out["human_rows"] > 0
    plan = evaluation.planned_calls(scenario_mod.load("covid_my_2020"), repeats=1, judge_repeats=1)
    assert out["calls_spent"] == plan["total"]


def test_arithmetic_baselines_are_scored_on_the_true_tables():
    b = evaluation.non_language_baselines(scenario_mod.load("haze_real"), samples=20)
    assert b["oracle (needs both true tables)"]["single"]["joint_value_recovered"] == 1.0
    assert b["split the difference"]["single"]["integrative_gain"] == 0.0
    # a worse guess of the other side cannot beat the oracle
    for name, v in b.items():
        j = v["single"]["joint_value_recovered"] if "single" in v else v["summary"]["joint_value_recovered"]["max"]
        assert j <= 1.0 + 1e-9, name


def test_full_information_shows_each_agent_the_other_table_only_in_the_probe():
    from crisis_debate import pipeline
    s = scenario_mod.load("haze_real")
    private, full = ScriptedBackend(scenario=s), ScriptedBackend(scenario=s)
    pipeline.run(s, private, condition="B2", rounds=1)
    pipeline.run(s, full, condition="B2", rounds=1, full_information=True)
    sys_private = [c["system"] for c in private.calls if c["schema"] == "Argument"]
    sys_full = [c["system"] for c in full.calls if c["schema"] == "Argument"]
    assert not any("PROBE ONLY" in x for x in sys_private)
    assert all("PROBE ONLY" in x for x in sys_full)


def test_each_run_is_saved_as_it_finishes(tmp_path):
    out = evaluation.run_evaluation(["covid_my_2020"], lambda sc, label, rep: ScriptedBackend(scenario=sc),
                                    repeats=1, judge_repeats=2, mode="test", out_dir=tmp_path,
                                    include_rounds=False, include_full_info=False, log=lambda _: None)
    lines = (tmp_path / out["folder"].split("/")[-1] / "runs_so_far.jsonl").read_text().splitlines()
    assert len(lines) == 3 and all(json.loads(l)["case"] == "covid_my_2020_v1" for l in lines)
