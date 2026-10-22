"""Tests for the three-layer versioned brief."""
from __future__ import annotations

import pytest

from crisis_debate import scenario as scenario_mod
from crisis_debate.briefs import (
    BriefVersion,
    PartyBrief,
    PositionalLayer,
    StructuralLayer,
    administration_change,
    diff,
)
from crisis_debate.provenance import Evidence, Source, Sourced, unknown


def _sourced(v, status="sourced"):
    ev = [Evidence(source_id="s1", quote=f"...{v}...")] if status == "sourced" else []
    return Sourced(value=v, status=status, evidence=ev)


@pytest.fixture
def brief():
    sc = scenario_mod.load("haze_real")
    return BriefVersion(
        scenario_id=sc.scenario_id, version=1,
        parties=[
            PartyBrief(
                name=sc.countries[0].name,
                structural=StructuralLayer(facts=[_sourced("imports over 90% of its food")]),
                positional=PositionalLayer(
                    interests=[_sourced("end hazardous air quality")],
                    red_lines=[_sourced("no settlement without verification")],
                    stances={"enforcement": _sourced("wants extraterritorial liability")},
                    as_of="2015-08-01T00:00:00+00:00"),
                valuations=sc.countries[0].valuations),
            PartyBrief(
                name=sc.countries[1].name,
                structural=StructuralLayer(facts=[_sourced("clearing by fire costs a fifteenth of mechanised")]),
                positional=PositionalLayer(
                    interests=[_sourced("protect smallholder livelihoods")],
                    red_lines=[_sourced("no foreign jurisdiction on its soil")],
                    stances={"concession_maps": _sourced("withholds parcel-level maps")},
                    as_of="2015-08-01T00:00:00+00:00"),
                valuations=sc.countries[1].valuations),
        ])


def test_compiles_to_a_scenario_the_pipeline_can_run(brief):
    """The whole point: the existing pipeline is untouched and just gets a
    better-sourced input."""
    base = scenario_mod.load("haze_real")
    sc = brief.to_scenario(base)
    assert [c.name for c in sc.countries] == [c.name for c in base.countries]
    assert sc.issues == base.issues
    assert sc.countries[0].red_lines  # carried through


def test_content_hash_ignores_timestamps(brief):
    same = brief.model_copy(update={"created_at": "2099-01-01T00:00:00+00:00",
                                    "note": "re-saved"})
    assert same.content_hash() == brief.content_hash()


def test_content_hash_changes_when_content_does(brief):
    p = brief.parties[0].model_copy(update={
        "positional": brief.parties[0].positional.model_copy(
            update={"red_lines": [_sourced("a different red line")]})})
    changed = brief.model_copy(update={"parties": [p, brief.parties[1]]})
    assert changed.content_hash() != brief.content_hash()


def test_round_trip_through_disk(brief, tmp_path):
    brief.save(tmp_path)
    back = BriefVersion.load(brief.scenario_id, 1, tmp_path)
    assert back.content_hash() == brief.content_hash()
    assert BriefVersion.latest_version(brief.scenario_id, tmp_path) == 1


def test_load_latest_when_no_version_given(brief, tmp_path):
    brief.save(tmp_path)
    brief.model_copy(update={"version": 2}).save(tmp_path)
    assert BriefVersion.load(brief.scenario_id, directory=tmp_path).version == 2


def test_staleness(brief):
    assert brief.parties[0].positional.is_stale(days=30)      # dated 2015
    fresh = PositionalLayer(as_of="2099-01-01T00:00:00+00:00")
    assert not fresh.is_stale(days=30)
    assert PositionalLayer().is_stale(), "an undated layer must count as stale"


def test_unsourced_elements_are_reported_not_hidden(brief):
    p = brief.parties[0]
    p.positional.interests.append(Sourced(value="wants a monitoring regime",
                                          status="unsupported"))
    p.positional.stances["funding"] = unknown("funding position")
    flagged = {s.status for s in p.unsourced()}
    assert "unsupported" in flagged and "unknown" in flagged


def test_unknown_tells_the_agent_not_to_assume(brief):
    assert "do not assume" in unknown("funding position").for_prompt()


# --------------------------------------------------------------------------
# Administration change — planner.md §3.1
# --------------------------------------------------------------------------
def test_administration_change_wipes_positions_but_keeps_structure(brief):
    party = brief.parties[1].name
    after = administration_change(brief, party, "change of government")
    old, new = brief.party(party), after.party(party)

    assert new.structural.facts == old.structural.facts, "structural facts survive a government"
    assert all(s.status == "unknown" for s in new.positional.interests)
    assert all(s.status == "unknown" for s in new.positional.red_lines)
    assert all(s.status == "unknown" for s in new.positional.stances.values())
    assert after.version == brief.version + 1
    assert party in after.note

    # the other party is untouched
    untouched = brief.parties[0].name
    assert after.party(untouched).positional == brief.party(untouched).positional


def test_administration_change_shows_up_as_a_large_diff(brief):
    """planner.md: no coup detector needed — a new government is a big diff."""
    after = administration_change(brief, brief.parties[1].name, "coup")
    changes = diff(brief, after)
    assert len(changes) >= 3
    assert all(c.party == brief.parties[1].name for c in changes)


# --------------------------------------------------------------------------
# Diff
# --------------------------------------------------------------------------
def test_diff_is_empty_for_identical_briefs(brief):
    assert diff(brief, brief) == []


def test_diff_detects_a_moved_stance(brief):
    p = brief.parties[1]
    moved = p.model_copy(update={"positional": p.positional.model_copy(
        update={"stances": {"concession_maps": _sourced("now offers aggregated maps")}})})
    after = brief.model_copy(update={"version": 2, "parties": [brief.parties[0], moved]})
    changes = diff(brief, after)
    assert any(c.field == "concession_maps" and "aggregated" in c.after for c in changes)


def test_diff_detects_additions_and_removals(brief):
    p = brief.parties[0]
    added = p.model_copy(update={"structural": StructuralLayer(
        facts=p.structural.facts + [_sourced("has no natural aquifer")])})
    after = brief.model_copy(update={"version": 2, "parties": [added, brief.parties[1]]})
    assert any(c.field == "added" and "aquifer" in c.after for c in diff(brief, after))
    assert any(c.field == "removed" for c in diff(after, brief))
