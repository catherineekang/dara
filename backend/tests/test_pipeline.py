"""Wiring tests for the three conditions."""
from __future__ import annotations

import pytest

from crisis_debate import pipeline, scenario as scenario_mod
from crisis_debate.schemas import Argument, Recommendation, RoundJudgment
from crisis_debate.fakes import ScriptedBackend
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


def test_b0_makes_one_call_per_reader(scenario):
    """B0's identity is that it does no debate and consults no judge, not that
    it spends exactly one call: advice for two objectives is two pieces of
    reasoning and costs two calls in every condition."""
    backend = ScriptedBackend(scenario)
    run = pipeline.run(scenario, backend, condition="B0",
                       priorities=("casualty_minimisation",))
    assert backend.usage["calls"] == 1
    assert run.rounds == []
    assert run.judgments == []
    assert len(run.recommendations) == 1

    two = ScriptedBackend(scenario)
    run2 = pipeline.run(scenario, two, condition="B0")
    assert two.usage["calls"] == 2
    assert [r.priority for r in run2.recommendations] == list(pipeline.DEFAULT_PRIORITIES)
    # one reader for both — the difference is what is being protected, not who
    # is reading
    assert {r.audience for r in run2.recommendations} == {pipeline.AUDIENCE}
    assert run2.rounds == [] and run2.judgments == []


def test_each_recommendation_is_told_who_it_is_for(scenario):
    """The tailoring has to reach the prompt. If both calls got the same system
    prompt the two recommendations would differ only by label, which is exactly
    the thing this feature is not."""
    backend = ScriptedBackend(scenario)
    pipeline.run(scenario, backend, condition="B2",
                 priorities=("casualty_minimisation", "cost"))
    recs = [c for c in backend.calls if c["schema"] == "Recommendation"]
    assert len(recs) == 2
    assert "LIVES AND HEALTH" in recs[0]["system"]
    assert "PUBLIC MONEY" in recs[1]["system"]
    # both are written for the same reader
    assert all("electorate" in c["system"] for c in recs)
    # and the debate itself is shared, not re-run per objective
    args = [c for c in backend.calls if c["schema"] == "Argument"]
    assert len(args) == len(scenario.countries) * 3


def test_priority_changes_the_objective_the_advice_is_built_around(scenario):
    harm = ScriptedBackend(scenario)
    pipeline.run(scenario, harm, condition="B0",
                 priorities=("casualty_minimisation",))
    money = ScriptedBackend(scenario)
    pipeline.run(scenario, money, condition="B0", priorities=("cost",))

    h_sys = [c for c in harm.calls if c["schema"] == "Recommendation"][0]["system"]
    m_sys = [c for c in money.calls if c["schema"] == "Recommendation"][0]["system"]
    assert h_sys != m_sys
    assert "LIVES AND HEALTH" in h_sys
    assert "PUBLIC MONEY" in m_sys


def test_the_untailored_baseline_states_that_it_has_no_priority(scenario):
    """`none` is the control the tailored readings are measured against, so it
    must actively tell the model not to adopt an objective of its own."""
    backend = ScriptedBackend(scenario)
    pipeline.run(scenario, backend, condition="B0", priorities=("none",))
    sys_prompt = [c for c in backend.calls if c["schema"] == "Recommendation"][0]["system"]
    assert "NOTHING IN PARTICULAR" in sys_prompt
    assert "do not optimise for any single dimension" in sys_prompt


def test_an_unknown_priority_or_audience_raises_rather_than_defaulting(scenario):
    backend = ScriptedBackend(scenario)
    with pytest.raises(ValueError):
        pipeline.run(scenario, backend, condition="B2", priorities=("profit",))
    with pytest.raises(ValueError):
        pipeline.run(scenario, backend, condition="B2", audience="journalist")


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
    assert len(rec_calls) == len(pipeline.DEFAULT_PRIORITIES)
    # the barrier must hold for EVERY tailored recommendation, not just the
    # first: one leak is as fatal to ablation B1 as several
    for call in rec_calls:
        assert "scripted debater turn" not in call["user"]
        assert "Judge assessments" in call["user"]


def test_recommender_does_see_the_transcript_in_b1(scenario):
    backend = ScriptedBackend(scenario)
    pipeline.run(scenario, backend, condition="B1")
    rec_calls = [c for c in backend.calls if c["schema"] == "Recommendation"]
    assert "scripted debater turn" in rec_calls[0]["user"]


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
        # An element is rendered with its provenance label, which is part of
        # the brief too: mask the labelled form, not just the bare text.
        for item in (brief.interests + brief.resources
                     + brief.constraints + brief.red_lines):
            skeleton = skeleton.replace(brief.label(item), "<X>")
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


def test_the_budget_gap_between_b0_and_b2_is_reported(scenario):
    """B0N is gone, so the compute gap is no longer controlled for — it is
    declared instead. This pins the numbers a result must be reported with, so
    nobody can quote a B2 win without the call counts beside it."""
    from crisis_debate.agents import debate_call_count

    b0 = ScriptedBackend(scenario)
    pipeline.run(scenario, b0, condition="B0")
    b2 = ScriptedBackend(scenario)
    pipeline.run(scenario, b2, condition="B2", rounds=3)

    objectives = len(pipeline.DEFAULT_PRIORITIES)
    assert b0.usage["calls"] == objectives                    # one per objective
    assert b2.usage["calls"] == debate_call_count(
        scenario, rounds=3, recommendations=objectives) == 11
    assert b2.usage["calls"] > b0.usage["calls"], (
        "a B2 win is confounded with spending more; the gap must be visible"
    )


# --------------------------------------------------------------------------
# Provenance, cutoff and the judge's inputs (paper §3–4)
# --------------------------------------------------------------------------
def test_every_brief_element_carries_a_provenance_label(scenario):
    """Paper Fig. 1 stage 1: each element is labelled, never dropped."""
    for c in scenario.countries:
        for el in c.interests + c.resources + c.constraints + c.red_lines:
            assert el in c.provenance, f"{c.name}: {el!r} has no provenance"
            p = c.provenance[el]
            assert p.status in ("sourced", "projected", "unsupported", "unknown", "user_edited")
            if p.status == "sourced":
                assert p.evidence, f"{el!r} is marked sourced with no evidence"


def test_an_unsourced_element_is_labelled_in_the_agents_prompt(scenario):
    backend = ScriptedBackend(scenario)
    pipeline.run(scenario, backend, condition="B2", rounds=1)
    system = next(c["system"] for c in backend.calls if c["schema"] == "Argument")
    assert "[UNSUPPORTED" in system


def test_the_cutoff_lives_in_the_scenario_file_and_agrees_with_the_leak_scan():
    from crisis_debate.contamination import CUTOFFS

    for case in ("haze_real", "covid_my_2020"):
        s = scenario_mod.load(case)
        assert s.evidence_cutoff, f"{case} has no evidence_cutoff"
        assert CUTOFFS[s.scenario_id] == s.evidence_cutoff


def test_no_source_after_the_cutoff_reaches_an_agent():
    from crisis_debate.agents import agent_sources

    for case in ("haze_real", "covid_my_2020"):
        s = scenario_mod.load(case)
        for _, src in agent_sources(s):
            assert src.published and src.published <= s.evidence_cutoff


def test_the_judge_sees_the_previous_round_and_the_sources(scenario):
    """Position consistency is scored against the agent's own previous round, and
    fact-checking needs the sources the agents were allowed to cite."""
    backend = ScriptedBackend(scenario)
    pipeline.run(scenario, backend, condition="B2", rounds=2)
    judge_calls = [c["user"] for c in backend.calls if c["schema"] == "RoundJudgment"]
    assert "(round 1: none)" in judge_calls[0]
    assert "scripted debater turn" in judge_calls[1].split("Round 2 arguments")[0]
    assert "[S1]" in judge_calls[0]


def test_the_pinned_conditions_reach_the_agents_and_are_recorded():
    from crisis_debate import situational

    s = scenario_mod.load("haze_real")
    snap = situational.load_snapshot("haze_real")
    assert snap is not None and all(f.as_of <= s.evidence_cutoff for f in snap.facts)
    backend = ScriptedBackend(s)
    run = pipeline.run(s, backend, condition="B2", rounds=1, conditions=snap)
    assert run.conditions_id == snap.snapshot_id
    users = [c["user"] for c in backend.calls if c["schema"] == "Argument"]
    assert any("Current conditions affecting Singapore" in u for u in users)


# --------------------------------------------------------------------------
# A sentence where an option name belongs (seen in the first live evaluation)
# --------------------------------------------------------------------------
class _SentenceFirst(ScriptedBackend):
    """Writes a sentence into the package on its first recommendation, the way
    the live model did, then complies when told what went wrong."""

    def parse(self, *, system, user, output_format, max_tokens=16000, tag=None):
        out = super().parse(system=system, user=user, output_format=output_format, tag=tag)
        if output_format is Recommendation and "not on the menu" not in user:
            bad = out.recommended_package[0].model_copy(
                update={"option": "Propose aggregated maps by district as a compromise."})
            return out.model_copy(update={"recommended_package": [bad] + out.recommended_package[1:]})
        return out


def test_an_option_written_as_a_sentence_is_asked_for_again_not_edited(scenario):
    backend = _SentenceFirst(scenario)
    run = pipeline.run(scenario, backend, condition="B0", priorities=("none",))
    assert run.illegal_packages == []
    retries = [c for c in backend.calls if c["schema"] == "Recommendation" and "not on the menu" in c["user"]]
    assert len(retries) == 1
    assert "Propose aggregated maps by district" in retries[0]["user"]


class _AlwaysSentence(_SentenceFirst):
    def parse(self, *, system, user, output_format, max_tokens=16000, tag=None):
        out = ScriptedBackend.parse(self, system=system, user=user, output_format=output_format, tag=tag)
        if output_format is Recommendation:
            bad = out.recommended_package[0].model_copy(update={"option": "a sentence"})
            return out.model_copy(update={"recommended_package": [bad] + out.recommended_package[1:]})
        return out


def test_a_second_bad_reply_is_still_recorded_as_illegal(scenario):
    run = pipeline.run(scenario, _AlwaysSentence(scenario), condition="B0", priorities=("none",))
    assert run.illegal_packages, "a reply that is still illegal must be recorded, never coerced"


def test_b2_recommender_sees_the_final_offers_but_still_no_arguments(scenario):
    """Option (b): the deal on the table reaches the advice; the prose does not."""
    backend = ScriptedBackend(scenario)
    run = pipeline.run(scenario, backend, condition="B2", rounds=3)
    rec_user = next(c["user"] for c in backend.calls if c["schema"] == "Recommendation")
    assert "final offer" in rec_user
    for a in run.rounds[-1]:
        assert f"{a.country}: " + ", ".join(f"{k}={v}" for k, v in a.package().items()) in rec_user
    for marker in ("scripted debater turn", "scripted claim", "scripted concession", "scripted red line"):
        assert marker not in rec_user


# --------------------------------------------------------------------------
# Every bundled case, not just the first two
# --------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["haze_real", "covid_my_2020", "haze_2026", "gfc_2008"])
def test_every_case_is_framed_completely(case):
    from crisis_debate import situational
    from crisis_debate.agents import agent_sources
    from crisis_debate.contamination import CUTOFFS

    s = scenario_mod.load(case)
    assert s.evidence_cutoff and CUTOFFS[s.scenario_id] == s.evidence_cutoff
    assert len(s.issues) == 4 and all(len(i.options) == 3 for i in s.issues)
    for c in s.countries:
        assert abs(sum(v.weight for v in c.valuations) - 1.0) < 1e-6
        for el in c.interests + c.resources + c.constraints + c.red_lines:
            assert el in c.provenance
    for _, src in agent_sources(s):                 # nothing after the cutoff reaches an agent
        assert src.published and src.published <= s.evidence_cutoff
    snap = situational.load_snapshot(case)
    assert snap is not None and all(f.as_of <= s.evidence_cutoff for f in snap.facts)


@pytest.mark.parametrize("case", ["haze_2026", "gfc_2008"])
def test_new_cases_run_end_to_end(case):
    s = scenario_mod.load(case)
    run = pipeline.run(s, ScriptedBackend(s), condition="B2", rounds=3, priorities=("none",))
    assert not run.illegal_packages and len(run.rounds) == 3


def test_baseline_and_recommender_get_the_same_plain_language_rule(scenario):
    """B0 and B2 must be asked for the same kind of advice, or a difference in
    the rubric would be a difference in instructions, not in deliberation."""
    for condition in ("B0", "B2"):
        backend = ScriptedBackend(scenario)
        pipeline.run(scenario, backend, condition=condition, rounds=1, priorities=("casualty_minimisation",))
        system = next(c["system"] for c in backend.calls if c["schema"] == "Recommendation")
        for phrase in ("WRITE FOR A BUSY MINISTER", "plain_summary",
                       "never name the other\nside's concession as yours"):
            assert phrase in system, (condition, phrase)


def test_old_recordings_without_plain_fields_still_load():
    rec = Recommendation(focus_country="Singapore", audience="policymaker", priority="cost",
                         headline="h", options=[], rationale="r", recommended_package=[])
    assert rec.plain_summary == ""


def test_from_round_two_each_side_sees_its_own_last_offer_and_is_asked_to_trade(scenario):
    backend = ScriptedBackend(scenario)
    pipeline.run(scenario, backend, condition="B2", rounds=2)
    users = [c["user"] for c in backend.calls if c["schema"] == "Argument"]
    round_two = users[len(scenario.countries):]
    for u in round_two:
        own = u.split("Your own argument and package from the previous round:")[1].split("The judge's")[0]
        assert "scripted debater turn" in own
        for rule in ("what you changed since your last round", "at least one trade that links two issues",
                     "different package from your last one"):
            assert rule in u
    assert "Your own argument" not in users[0]           # round 1 has no previous round


def test_debaters_run_warmer_than_the_judge_and_recommender(monkeypatch):
    from crisis_debate.providers import OpenAIBackend

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.delenv("OPENAI_TEMPERATURE", raising=False)
    monkeypatch.delenv("OPENAI_DEBATER_TEMPERATURE", raising=False)
    b = OpenAIBackend(temperature=0.0)
    assert b.temperature == 0.0 and b.debater_temperature == 0.7
    assert b.debater_llm is not b.llm
    same = OpenAIBackend(temperature=0.7)               # the evaluation's agents: one setting
    assert same.debater_llm is same.llm


@pytest.mark.parametrize("condition", ["B0", "B1", "B2"])
def test_every_condition_advises_with_the_clients_brief_and_cost_rules(scenario, condition):
    """The adviser must see its own client's red lines, and every condition the
    same ones, or B0 and B2 would differ in what they were told."""
    backend = ScriptedBackend(scenario)
    pipeline.run(scenario, backend, condition=condition, rounds=1, priorities=("cost",))
    call = next(c for c in backend.calls if c["schema"] == "Recommendation")
    me = scenario.brief(scenario.focus_country)
    assert "Your client's own brief" in call["user"]
    assert all(r in call["user"] for r in me.red_lines)
    assert 'spending is never "saving"' in call["system"]
    assert "who is causing the problem" in call["system"]
