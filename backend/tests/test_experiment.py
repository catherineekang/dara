"""The research runner, exercised with scripted agents — no key, no cost."""
import pytest

from crisis_debate import experiment, pipeline, scenario as scenario_mod
from crisis_debate.fakes import ScriptedBackend


@pytest.fixture(scope="module")
def haze():
    return scenario_mod.load("haze_real")


@pytest.fixture(scope="module")
def result(haze):
    backends = []

    def factory(sc, condition, rep):
        b = ScriptedBackend(scenario=sc)
        backends.append((condition, b))
        return b

    out = experiment.run_case(haze, factory, rounds=2, repeats=2, judge_repeats=2)
    out["_backends"] = backends
    return out


def test_every_condition_runs_and_is_scored(result):
    assert set(result["by_condition"]) == set(pipeline.CONDITIONS)
    for s in result["by_condition"].values():
        assert s["runs"] == 2
        for m in experiment.PRIMARIES:
            assert s[m]["n"] == 2


def test_every_condition_is_scored_on_the_declared_rq_pair(result):
    """CONTRACT §14: never list order. Every recommender call must have been
    asked for the RQ priority and nothing else."""
    for condition, b in result["_backends"]:
        recs = [c for c in b.calls if c["schema"] == "Recommendation"]
        assert len(recs) == (0 if condition == "judge_check" else 1)
        for c in recs:
            assert "NOTHING IN PARTICULAR" in c["system"]


def test_planned_calls_match_what_is_spent(haze, result):
    """The call counts are reported beside every comparison, so the estimate
    shown before spending must be the number actually spent."""
    plan = experiment.planned_calls(haze, rounds=2, repeats=2, judge_repeats=2)
    spent = {}
    for condition, b in result["_backends"]:
        key = "judge_checks" if condition == "judge_check" else condition
        spent[key] = spent.get(key, 0) + b.usage["calls"]
    assert spent == {k: v for k, v in plan.items() if k != "total"}


def test_judge_checks_and_qualifiers_are_attached(result):
    j = result["judge"]
    assert "spearman_rho" in j["validity"]
    assert "self_consistency" in j and "position_bias" in j
    assert result["contaminated"] is True
    assert set(result["comparisons"]) == {"B2-B0", "B2-B1", "B1-B0"}
    for row in result["runs"]:
        assert "stress" in row and "history" in row


def test_report_carries_the_budget_caveat(haze, tmp_path):
    out = experiment.run_experiment(
        ["haze_real"], lambda sc, c, r: ScriptedBackend(scenario=sc),
        rounds=1, repeats=1, judge_repeats=0, mode="test", out_dir=tmp_path)
    text = "\n".join(experiment.report(out))
    assert "structure or budget" in text and "contaminated" in text
    assert (tmp_path / out["written_to"].split("/")[-1]).exists()


def test_product_features_never_feed_the_measurement():
    """CONTRACT §8.8: the research runner must not reach the product modules,
    and no form-entered crisis may be in the study set."""
    import inspect
    src = inspect.getsource(experiment)
    for module in ("imagine", "intake"):
        assert f"import {module}" not in src and f"{module}." not in src
    assert all(not c.startswith("user_") for c in experiment.STUDY_CASES)
    assert set(experiment.STUDY_CASES) == {"haze_real", "covid_my_2020"}
