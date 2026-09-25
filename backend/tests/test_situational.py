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
