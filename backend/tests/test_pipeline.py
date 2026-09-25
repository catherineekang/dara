"""Wiring tests for the three conditions."""
from __future__ import annotations

import pytest

from crisis_debate import pipeline, scenario as scenario_mod
from crisis_debate.schemas import Argument, Recommendation, RoundJudgment
from crisis_debate.fakes import ScriptedBackend, VaryingBackend
from crisis_debate.utilities import format_valuations


@pytest.fixture
def scenario():
    return scenario_mod.load("haze_real")


def test_scenario_loads_and_validates(scenario):
    assert scenario.focus_country in [c.name for c in scenario.countries]
    assert len(scenario.opponents(scenario.focus_country)) == 1
    assert scenario.brief(scenario.focus_country).red_lines


def test_b2_shape(scenario):
    run = pipeline.run(scenario, ScriptedBackend(scenario), condition="B2")
    assert len(run.rounds) == 3
    assert all(len(r) == len(scenario.countries) for r in run.rounds)
    assert len(run.judgments) == 3
    assert [j.round_index for j in run.judgments] == [1, 2, 3]
    assert run.recommendation.focus_country == scenario.focus_country


def test_b1_runs_debate_but_no_judge(scenario):
    run = pipeline.run(scenario, ScriptedBackend(scenario), condition="B1")
    assert len(run.rounds) == 3
    assert run.judgments == []


def test_b0_makes_exactly_one_call(scenario):
    backend = ScriptedBackend(scenario)
    run = pipeline.run(scenario, backend, condition="B0")
    assert backend.usage["calls"] == 1
    assert run.rounds == []
    assert run.judgments == []


def test_country_field_is_pinned_not_trusted(scenario):
    """The fake returns country='(unset)'; the agent must overwrite it, because
    every downstream metric keys on this field."""
    run = pipeline.run(scenario, ScriptedBackend(scenario), condition="B2")
    names = {c.name for c in scenario.countries}
    for round_args in run.rounds:
        assert {a.country for a in round_args} == names


def test_recommender_never_sees_the_transcript_in_b2(scenario):
    """The information barrier from proposal Section 4. If this breaks, ablation
    B1 stops measuring anything and the judge becomes decorative."""
    backend = ScriptedBackend(scenario)
    pipeline.run(scenario, backend, condition="B2")
    rec_calls = [c for c in backend.calls if c["schema"] == "Recommendation"]
    assert len(rec_calls) == 1
    assert "scripted position" not in rec_calls[0]["user"]
    assert "Judge assessments" in rec_calls[0]["user"]


def test_recommender_does_see_the_transcript_in_b1(scenario):
    backend = ScriptedBackend(scenario)
    pipeline.run(scenario, backend, condition="B1")
    rec_calls = [c for c in backend.calls if c["schema"] == "Recommendation"]
    assert "scripted position" in rec_calls[0]["user"]


def test_round_one_sees_no_opponent_and_later_rounds_do(scenario):
    backend = ScriptedBackend(scenario)
    pipeline.run(scenario, backend, condition="B2")
    arg_calls = [c for c in backend.calls if c["schema"] == "Argument"]
    n = len(scenario.countries)
    assert "round 1 of 3" in arg_calls[0]["user"]
    for call in arg_calls[:n]:
        assert "Opposing arguments" not in call["user"]
    for call in arg_calls[n:]:
        assert "Opposing arguments" in call["user"]
        assert "judge's assessment" in call["user"]


def test_country_agents_differ_only_by_brief(scenario):
    """Proposal Section 4.1: any behavioural difference must be attributable to
    the brief, so the prompt skeleton has to be byte-identical across agents.

    Masks every brief-derived string out of each system prompt and asserts the
    remainder is the same for all agents. If someone later special-cases the
    wording for one country, the brief-diversity ablation stops measuring
    assigned interests and this test is what catches it."""
    backend = ScriptedBackend(scenario)
    pipeline.run(scenario, backend, condition="B2", rounds=1)
    systems = [c["system"] for c in backend.calls if c["schema"] == "Argument"]
    assert len(systems) == len(scenario.countries)

    skeletons = []
    for brief in scenario.countries:
        matching = [s for s in systems if f"Country: {brief.name}" in s]
        assert len(matching) == 1, f"expected exactly one prompt for {brief.name}"
        skeleton = matching[0]
        # Valuations are brief-derived too, and are rendered into the prompt,
        # so they must be masked as well - otherwise this test would fail for
        # the legitimate reason that the briefs differ.
        skeleton = skeleton.replace(
            format_valuations(brief, scenario.issues), "<VALUATIONS>")
        for item in (brief.interests + brief.resources
                     + brief.constraints + brief.red_lines):
            skeleton = skeleton.replace(item, "<X>")
        skeletons.append(skeleton.replace(brief.name, "<COUNTRY>"))

    assert len(set(skeletons)) == 1, "country prompts differ by more than the brief"
    for token in ("<COUNTRY>", "<X>", "<VALUATIONS>"):
        assert token in skeletons[0], f"masking did not apply: {token}"


def test_round_count_is_a_parameter(scenario):
    for r in (1, 2, 3):
        run = pipeline.run(scenario, ScriptedBackend(scenario), condition="B2", rounds=r)
        assert len(run.rounds) == r and len(run.judgments) == r


def test_bad_condition_rejected(scenario):
    with pytest.raises(ValueError, match="condition must be one of"):
        pipeline.run(scenario, ScriptedBackend(scenario), condition="B9")


def test_run_log_is_written(scenario, tmp_path):
    import json
    log = tmp_path / "run.jsonl"
    pipeline.run(scenario, ScriptedBackend(scenario), condition="B2", log_path=log)
    kinds = [json.loads(l)["kind"] for l in log.read_text().splitlines()]
    assert kinds[0] == "scenario"
    assert kinds.count("argument") == 6
    assert kinds.count("judgment") == 3
    assert kinds[-1] == "recommendation"


# --------------------------------------------------------------------------
# Upgrade A: information asymmetry
# --------------------------------------------------------------------------
def test_private_valuations_never_reach_an_opponent(scenario):
    """The integrative-bargaining result is only meaningful if neither side is
    shown the other's payoffs. This is the guard on that."""
    backend = ScriptedBackend(scenario)
    pipeline.run(scenario, backend, condition="B2", rounds=1)
    for call in backend.calls:
        if call["schema"] != "Argument":
            continue
        owner = next(c for c in scenario.countries if f"Country: {c.name}" in call["system"])
        for other in scenario.opponents(owner.name):
            rendered = format_valuations(other, scenario.issues)
            assert rendered not in call["system"], f"{owner.name} saw {other.name}'s payoffs"
            assert rendered not in call["user"]


def test_judge_is_shown_no_valuations_at_all(scenario):
    """The judge scores how a case was argued. Showing it the payoffs would let
    it score outcomes instead, and the judge-validity correlation would then be
    measuring the judge reading the answer key."""
    backend = ScriptedBackend(scenario)
    pipeline.run(scenario, backend, condition="B2", rounds=1)
    for call in backend.calls:
        if call["schema"] != "RoundJudgment":
            continue
        for c in scenario.countries:
            assert format_valuations(c, scenario.issues) not in call["system"] + call["user"]


def test_arguments_commit_to_a_legal_package(scenario):
    run = pipeline.run(scenario, ScriptedBackend(scenario), condition="B2")
    assert run.illegal_packages == []
    for round_args in run.rounds:
        for arg in round_args:
            assert set(arg.package()) == {i.name for i in scenario.issues}


def test_illegal_packages_are_recorded_not_silently_fixed(scenario):
    from crisis_debate.schemas import PackageItem

    bad_issue = scenario.issues[0].name

    class BadBackend(ScriptedBackend):
        def _items(self):
            return [PackageItem(issue=bad_issue, option="telepathy")]

    run = pipeline.run(scenario, BadBackend(scenario), condition="B2", rounds=1)
    assert run.illegal_packages, "an illegal option must surface, not be coerced"
    assert any("telepathy" in p for p in run.illegal_packages)


# --------------------------------------------------------------------------
# Upgrade B: compute-matched baseline
# --------------------------------------------------------------------------
def test_b0n_matches_the_debate_call_count(scenario):
    """B0 spends 1 call against B2's 10, so a B2 win confounds structure with
    budget. B0N must spend the same as B2."""
    from crisis_debate.agents import debate_call_count

    b2 = ScriptedBackend(scenario)
    pipeline.run(scenario, b2, condition="B2", rounds=3)

    b0n = ScriptedBackend(scenario)
    pipeline.run(scenario, b0n, condition="B0N", rounds=3)

    assert b2.usage["calls"] == debate_call_count(scenario, rounds=3) == 10
    assert b0n.usage["calls"] == b2.usage["calls"]


def test_b0n_selects_the_modal_package(scenario):
    """Self-consistency selection: with a 2:1 split the majority package wins."""
    from crisis_debate.utilities import split_the_difference

    backend = VaryingBackend(scenario, cycle=("split", "split", "first"))
    run = pipeline.run(scenario, backend, condition="B0N", best_of_n=3)
    assert run.recommendation.package() == split_the_difference(scenario)


def test_b0n_rejects_nonsense_n(scenario):
    with pytest.raises(ValueError, match="n must be"):
        pipeline.run(scenario, ScriptedBackend(scenario), condition="B0N", best_of_n=0)


def test_outcome_metrics_separate_good_and_bad_settlements(scenario):
    """End to end: the pipeline's own output feeds the payoff metrics, and a
    process that splits everything scores strictly worse than one that trades."""
    from crisis_debate.utilities import Frontier, score_package

    frontier = Frontier.build(scenario)
    splitting = pipeline.run(scenario, ScriptedBackend(scenario, package_strategy="split"),
                             condition="B2")
    trading = pipeline.run(scenario, ScriptedBackend(scenario, package_strategy="integrative"),
                           condition="B2")

    s = score_package(frontier, splitting.recommendation.package())
    t = score_package(frontier, trading.recommendation.package())
    assert t["joint_value_recovered"] > s["joint_value_recovered"]
    assert t["integrative_gain"] == 1.0 and s["integrative_gain"] == 0.0
    assert t["is_pareto_optimal"] == 1.0 and s["is_pareto_optimal"] == 0.0
