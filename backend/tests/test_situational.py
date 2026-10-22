"""Tests for the situational layer — planner.md §3.3."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from crisis_debate import scenario as scenario_mod
from crisis_debate.provenance import Source
from crisis_debate.situational import (
    ConditionsSnapshot,
    SituationalFact,
    apply_reweights,
    feasible,
    reachable_report,
    render_for_prompt,
    unavailable_options,
)
from crisis_debate.utilities import Frontier, all_packages

SRC = Source(source_id="reliefweb-1", title="Floods — Indonesia", url="https://example.org",
             published="2026-09-20", retrieved_at="2026-09-26T00:00:00+00:00")


def _now_iso(hours_ago=0.0):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


@pytest.fixture
def sc():
    return scenario_mod.load("haze_real")


def _flood(sc, accepted=True, option="full_donor_fund"):
    return SituationalFact(
        party=sc.countries[1].name, summary="Severe flooding in Java; fiscal capacity constrained",
        as_of="2026-09-20", source=SRC, effect="constrain",
        issue="transition_funding", option=option, confidence=0.8, accepted=accepted)


def _snap(sc, facts):
    return ConditionsSnapshot(scenario_id=sc.scenario_id, fetched_at=_now_iso(), facts=facts)


# --------------------------------------------------------------------------
# The rule that matters: a situational fact cannot express a position
# --------------------------------------------------------------------------
def test_a_situational_fact_has_no_field_for_a_position():
    """planner.md §3.3: a news story must never silently override a documented
    commitment. Enforced by construction — there is nowhere to put one."""
    fields = set(SituationalFact.model_fields)
    assert not fields & {"position", "stance", "red_line", "interest"}
    assert fields >= {"effect", "issue", "option", "weight_delta"}


# --------------------------------------------------------------------------
# Constraining
# --------------------------------------------------------------------------
def test_constraint_removes_settlements_but_not_the_frontier(sc):
    """The frontier is computed on the full space and does not move; only the
    reachable part shrinks."""
    snap = _snap(sc, [_flood(sc)])
    before = Frontier.build(sc)
    reach = feasible(sc, snap)

    assert len(reach) < len(all_packages(sc))
    assert all(p["transition_funding"] != "full_donor_fund" for p in reach)
    after = Frontier.build(sc)
    assert after.max_joint == before.max_joint, "conditions must not move the yardstick"


def test_unaccepted_facts_do_nothing(sc):
    """Only facts a human accepted reach the model."""
    snap = _snap(sc, [_flood(sc, accepted=False)])
    assert unavailable_options(snap) == {}
    assert len(feasible(sc, snap)) == len(all_packages(sc))


def test_no_snapshot_means_no_constraint(sc):
    assert len(feasible(sc, None)) == len(all_packages(sc))


def test_reachable_report_prices_the_constraint(sc):
    snap = _snap(sc, [_flood(sc)])
    r = reachable_report(sc, snap)
    assert r["n_reachable"] < r["n_total"]
    assert r["max_joint_reachable"] <= r["max_joint_unconstrained"]
    assert r["value_put_out_of_reach"] >= 0
    # the unconstrained maximum needed the full fund, so something is now lost
    assert r["value_put_out_of_reach"] > 0


def test_constraining_everything_leaves_nothing_reachable(sc):
    facts = [_flood(sc, option=o) for o in sc.issue("transition_funding").options]
    r = reachable_report(sc, _snap(sc, facts))
    assert r["n_reachable"] == 0 and r["max_joint_reachable"] == 0.0


# --------------------------------------------------------------------------
# Reweighting
# --------------------------------------------------------------------------
def test_reweight_renormalises_to_one(sc):
    f = SituationalFact(party=sc.countries[1].name, summary="fiscal stress",
                        as_of="2026-09-20", source=SRC, effect="reweight",
                        issue="transition_funding", weight_delta=0.2, accepted=True)
    out = apply_reweights(sc, _snap(sc, [f]))
    for c in out.countries:
        assert abs(sum(v.weight for v in c.valuations) - 1.0) < 1e-9
    before = {v.issue: v.weight for v in sc.countries[1].valuations}
    after = {v.issue: v.weight for v in out.countries[1].valuations}
    assert after["transition_funding"] > before["transition_funding"]


def test_reweight_leaves_the_other_party_alone(sc):
    f = SituationalFact(party=sc.countries[1].name, summary="x", as_of="2026-09-20",
                        source=SRC, effect="reweight", issue="transition_funding",
                        weight_delta=0.2, accepted=True)
    out = apply_reweights(sc, _snap(sc, [f]))
    assert out.countries[0].valuations == sc.countries[0].valuations


# --------------------------------------------------------------------------
# Pinning and staleness
# --------------------------------------------------------------------------
def test_snapshot_id_is_stable_for_identical_content(sc):
    a = _snap(sc, [_flood(sc)])
    b = a.model_copy(deep=True)
    assert a.snapshot_id == b.snapshot_id


def test_snapshot_id_changes_with_content(sc):
    a = _snap(sc, [_flood(sc)])
    b = a.model_copy(update={"facts": [_flood(sc, option="partial_donor_fund")]})
    assert a.snapshot_id != b.snapshot_id


def test_staleness_is_shown_not_hidden(sc):
    fresh = _snap(sc, [_flood(sc)])
    assert not fresh.is_stale()
    old = fresh.model_copy(update={"fetched_at": _now_iso(hours_ago=30)})
    assert old.is_stale()
    assert "stale" in old.staleness_note()
    assert ConditionsSnapshot(scenario_id="x", fetched_at="not-a-date").is_stale()


# --------------------------------------------------------------------------
# Prompt rendering
# --------------------------------------------------------------------------
def test_prompt_shows_only_that_partys_conditions(sc):
    snap = _snap(sc, [_flood(sc)])
    mine = render_for_prompt(snap, sc.countries[1].name)
    theirs = render_for_prompt(snap, sc.countries[0].name)
    assert "flooding" in mine.lower() and "removes" in mine
    assert theirs == "", "a party must not be shown the other side's conditions"


def test_prompt_is_empty_without_a_snapshot(sc):
    assert render_for_prompt(None, sc.countries[0].name) == ""


# --------------------------------------------------------------------------
# stage 4 wired into the run — CONTRACT.md §11
# --------------------------------------------------------------------------
def _snapshot(scenario, issue, option, party, fetched_at="2015-08-01T00:00:00"):
    from crisis_debate.situational import ConditionsSnapshot, SituationalFact
    from crisis_debate.provenance import Source
    return ConditionsSnapshot(
        scenario_id=scenario.scenario_id, fetched_at=fetched_at,
        facts=[SituationalFact(
            party=party, summary="Fiscal space is committed to flood response.",
            effect="constrain", issue=issue, option=option, as_of="2015-07-20",
            accepted=True,
            source=Source(source_id="bnpb_2015_07", title="National disaster agency briefing",
                          publisher="BNPB",
                          published="2015-07-20", url="https://example.org/x"),
        )],
    )


def test_conditions_reach_the_agent_that_they_constrain():
    """A fact about Indonesia must appear in Indonesia's prompt and nowhere else
    — it is that party's situation, not a shared announcement."""
    from crisis_debate import pipeline, scenario as scenario_mod
    from crisis_debate.fakes import ScriptedBackend

    s = scenario_mod.load("haze_real")
    snap = _snapshot(s, "transition_funding", "none", "Indonesia")
    backend = ScriptedBackend(s)
    pipeline.run(s, backend, condition="B1", rounds=1, conditions=snap)

    args = [c for c in backend.calls if c["schema"] == "Argument"]
    mine = [c for c in args if "Current conditions affecting Indonesia" in c["user"]]
    theirs = [c for c in args if "Current conditions affecting Singapore" in c["user"]]
    assert len(mine) == 1, "Indonesia's agent was not told its own conditions"
    assert not theirs, "Singapore was told about conditions that are not its own"


def test_a_run_records_which_snapshot_it_read():
    """Pinned, not fetched: two runs can be CHECKED to have read the same world
    rather than assumed to have."""
    from crisis_debate import pipeline, scenario as scenario_mod
    from crisis_debate.fakes import ScriptedBackend

    s = scenario_mod.load("haze_real")
    snap = _snapshot(s, "transition_funding", "none", "Indonesia")
    a = pipeline.run(s, ScriptedBackend(s), condition="B1", rounds=1, conditions=snap)
    b = pipeline.run(s, ScriptedBackend(s), condition="B0", conditions=snap)
    assert a.conditions_id == b.conditions_id == snap.snapshot_id
    assert a.conditions_id != ""


def test_a_run_without_conditions_says_so_rather_than_pretending():
    from crisis_debate import pipeline, scenario as scenario_mod
    from crisis_debate.fakes import ScriptedBackend

    s = scenario_mod.load("haze_real")
    run = pipeline.run(s, ScriptedBackend(s), condition="B1", rounds=1)
    assert run.conditions_id == ""
    assert run.conditions_report == {}


def test_constraints_shrink_what_is_reachable_without_moving_the_frontier():
    """The yardstick must not move when the world does, or runs stop being
    comparable. Conditions report what became unattainable instead."""
    from crisis_debate import pipeline, scenario as scenario_mod
    from crisis_debate.fakes import ScriptedBackend
    from crisis_debate.utilities import Frontier

    s = scenario_mod.load("haze_real")
    snap = _snapshot(s, "transition_funding", "full_donor_fund", "Indonesia")
    run = pipeline.run(s, ScriptedBackend(s), condition="B1", rounds=1, conditions=snap)
    rep = run.conditions_report

    assert rep["n_reachable"] < rep["n_total"]
    # the ceiling is unchanged; what changed is how much of it can be reached
    assert rep["max_joint_unconstrained"] == pytest.approx(Frontier.build(s).max_joint)
    assert rep["max_joint_reachable"] <= rep["max_joint_unconstrained"]
    assert rep["value_put_out_of_reach"] >= 0.0
