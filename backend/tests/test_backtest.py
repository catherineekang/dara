"""Scoring a recommendation against what actually happened.

The easy failure here is not a wrong number, it is a number that looks like
evidence and is not. These tests pin the guards that keep the claim honest:
ground truth stays out of every prompt, it is sourced issue by issue, and the
contamination caveat travels with the result rather than living in a docstring
nobody reads.
"""
from __future__ import annotations

import pytest

from crisis_debate import backtest, scenario as scenario_mod
from crisis_debate.utilities import Frontier, split_the_difference, validate_package


@pytest.fixture(params=["haze_real", "covid_my_2020"])
def case(request):
    name = request.param
    return scenario_mod.load(name), backtest.load(name)


# --------------------------------------------------------------------------
# the ground truth itself
# --------------------------------------------------------------------------
def test_both_bundled_cases_have_a_recorded_outcome():
    assert set(backtest.available()) >= {"haze_real", "covid_my_2020"}


def test_every_issue_is_coded_and_sourced(case):
    """An uncited coding is an opinion about history, not a ground truth."""
    scenario, truth = case
    issues = {i.name for i in scenario.issues}
    assert set(truth.settlement) == issues, "ground truth must settle every issue, and only those"
    assert set(truth.confidence) == issues, "every issue needs a stated confidence"
    for issue in issues:
        src = truth.source_for(issue)
        assert src is not None, f"{issue} has no evidence entry"
        assert src.get("url", "").startswith("http"), f"{issue} evidence has no URL"
        assert src.get("publisher"), f"{issue} evidence names no publisher"
        assert len(src.get("claim", "")) > 40, f"{issue} evidence claim is too thin to check"


def test_confidence_is_one_of_the_allowed_levels(case):
    _, truth = case
    assert set(truth.confidence.values()) <= {"high", "medium", "low"}


def test_weak_codings_are_declared_rather_than_buried(case):
    """Both cases have at least one judgement call. It must be visible, because
    a result that turns on it is one coding decision from a different answer."""
    _, truth = case
    assert truth.weakly_coded, "a ground truth with no admitted uncertainty is suspicious"
    assert truth.coding_notes, "the judgement calls need writing down"


def test_the_historical_settlement_is_expressible_in_the_issue_set(case):
    """It always is — and that is the contamination, stated as a test.

    The issue sets were authored knowing the outcome, so the option the parties
    actually chose is on the menu by construction. This passing is not a good
    sign; it is the thing that makes absolute agreement uninformative.
    """
    scenario, truth = case
    assert validate_package(scenario, truth.settlement) == []


# --------------------------------------------------------------------------
# the barrier
# --------------------------------------------------------------------------
def test_ground_truth_never_reaches_an_agent():
    """Structural, not aspirational: nothing that builds a prompt may import
    this module. If it ever does, the agents can be told the answer."""
    import inspect
    from crisis_debate import agents, imagine, intake, pipeline

    for module in (agents, pipeline, imagine, intake):
        src = inspect.getsource(module)
        assert "backtest" not in src, (
            f"{module.__name__} references backtest — ground truth must not be "
            f"reachable from anything that writes a prompt"
        )


def test_ground_truth_lives_outside_the_scenario_directory():
    assert (backtest.EVAL_DIR / "haze_real" / "ground_truth.json").exists()
    assert "ground_truth" not in (scenario_mod.scenario_path("haze_real")).read_text()


def test_unknown_scenario_raises_rather_than_returning_empty():
    with pytest.raises(backtest.NoGroundTruth):
        backtest.load("no_such_case")


# --------------------------------------------------------------------------
# the comparison
# --------------------------------------------------------------------------
def test_matching_history_exactly_scores_full_agreement(case):
    scenario, truth = case
    r = backtest.compare(scenario, truth.settlement, truth)
    assert r["exact_match"] is True
    assert r["issue_agreement"] == 1.0
    assert r["joint_gain_over_history"] == 0.0
    assert r["beat_history"] is False


def test_agreement_and_quality_are_reported_separately(case):
    """A recommendation can disagree with history on every issue and still be
    the better deal. Collapsing the two into one score would hide exactly the
    outcome this tool exists to produce."""
    scenario, truth = case
    f = Frontier.build(scenario)
    best = max(f.pareto_packages(),
               key=lambda p: sum(backtest.utility_vector(scenario, p).values()))
    r = backtest.compare(scenario, best, truth, frontier=f)
    assert r["our_joint"] >= r["history_joint"]
    assert r["beat_history"] == (r["our_joint"] > r["history_joint"] + 1e-9)
    # the two measures are independent: this is the best available deal, and it
    # need not agree with history at all
    assert 0.0 <= r["issue_agreement"] <= 1.0


def test_illegal_package_is_reported_not_scored(case):
    scenario, truth = case
    r = backtest.compare(scenario, {"nonsense": "option"}, truth)
    assert r["legal"] is False
    assert r["problems"]


def test_the_contamination_caveat_travels_with_the_number(case):
    """The caveat must be in the payload, not only in the documentation — a
    number that leaves this module without it will be quoted without it."""
    scenario, truth = case
    r = backtest.compare(scenario, truth.settlement, truth)
    assert r["contaminated"] is True
    assert "not evidence of predictive skill" in r["caveat"]
    assert r["weakly_coded_issues"] == truth.weakly_coded


def test_split_the_difference_is_scored_like_any_other_package(case):
    scenario, truth = case
    r = backtest.compare(scenario, split_the_difference(scenario), truth)
    assert r["legal"] and r["ground_truth_expressible"]
    assert 0.0 <= r["our_joint_value_recovered"] <= 1.0


def test_comparing_conditions_identifies_the_leaders(case):
    """The comparison that survives contamination: same menu, several arms."""
    scenario, truth = case
    f = Frontier.build(scenario)
    best = max(f.pareto_packages(),
               key=lambda p: sum(backtest.utility_vector(scenario, p).values()))
    out = backtest.compare_conditions(
        scenario, {"history": truth.settlement, "split": split_the_difference(scenario), "best": best}, truth)

    assert out["best_agreement"] == 1.0            # the history arm matches itself
    assert "history" in out["leaders"]
    assert "history" not in out["beat_history"]    # it cannot beat itself
    assert "interpretable" in out["note"]


@pytest.mark.parametrize("name", ["haze_real", "covid_my_2020"])
def test_ground_truth_is_findable_by_its_scenarios_id(name):
    """Callers holding a Scenario look history up by scenario_id, not folder
    name. A mismatched id makes the backtest silently skip the case."""
    s = scenario_mod.load(name)
    assert backtest.has_ground_truth(s.scenario_id)
    assert backtest.load(s.scenario_id).scenario_id == s.scenario_id
