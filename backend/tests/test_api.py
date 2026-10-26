"""The HTTP layer the console talks to.

These tests exist because the console is now a real client: if a field changes
shape here, a page stops rendering. They run with no key and no network — the
provider layer falls back to the recorded run, which is the same path a user
without a key takes.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

import app as app_module
from app import app
from crisis_debate import scenario as scenario_mod

client = app.test_client()


@pytest.fixture
def cleanup_user_scenarios():
    """Remove anything a test created, so the scenarios/ folder is not a mutable
    fixture that later tests inherit."""
    # The API writes user cases flat (eval/user_<id>.json), not as case folders,
    # so both shapes have to be watched or nothing is ever cleaned up and every
    # later run fails on a 409.
    def created():
        return (set(scenario_mod.EVAL_DIR.glob("*/scenario.json"))
                | set(scenario_mod.EVAL_DIR.glob("user_*.json")))

    before = created()
    yield
    for p in created() - before:
        p.unlink()


def _new_scenario(scenario_id="test_case"):
    """Two issues, two parties, opposed preferences with one integrative trade."""
    return {
        "scenario": {
            "scenario_id": scenario_id,
            "title": "Test crisis",
            "description": "A crisis invented by a test.",
            "focus_country": "Alpha",
            "issues": [
                {"name": "water", "options": ["low", "mid", "high"]},
                {"name": "access", "options": ["closed", "open"]},
            ],
            "countries": [
                {
                    "name": "Alpha", "interests": ["water"], "resources": [], "constraints": [],
                    "red_lines": [],
                    "valuations": [
                        {"issue": "water", "weight": 0.8,
                         "option_values": {"low": 0.0, "mid": 0.5, "high": 1.0}},
                        {"issue": "access", "weight": 0.2,
                         "option_values": {"closed": 1.0, "open": 0.0}},
                    ],
                },
                {
                    "name": "Beta", "interests": ["access"], "resources": [], "constraints": [],
                    "red_lines": [],
                    "valuations": [
                        {"issue": "water", "weight": 0.2,
                         "option_values": {"low": 1.0, "mid": 0.5, "high": 0.0}},
                        {"issue": "access", "weight": 0.8,
                         "option_values": {"closed": 0.0, "open": 1.0}},
                    ],
                },
            ],
        }
    }


# --------------------------------------------------------------------------
# read
# --------------------------------------------------------------------------
def test_health_states_which_way_a_run_will_go():
    body = client.get("/api/health").get_json()
    assert body["ok"] is True
    assert body["mode"] in ("live", "recorded")
    # The mode must agree with the key, or the UI badge lies about whether
    # pressing Run spends money.
    assert body["mode"] == ("live" if body["key_present"] else "recorded")


def test_health_does_not_offer_the_retired_condition():
    assert "B0N" not in client.get("/api/health").get_json()["conditions"]


def test_scenarios_list_reports_the_frontier():
    body = client.get("/api/scenarios").get_json()
    assert body, "no scenarios bundled"
    haze = next(x for x in body if x["file"] == "haze_real")
    assert haze["settlements"] == 81          # 3^4, enumerated not sampled
    assert haze["pareto_count"] >= 1
    assert haze["has_recording"] is True


def test_scenario_detail_carries_the_full_outcome_space():
    body = client.get("/api/scenarios/haze_real").get_json()
    f = body["frontier"]
    assert len(f["points"]) == f["settlements"]
    assert f["at_stake"] == pytest.approx(f["max_joint"] - f["split_joint"])
    # the yardstick arrives before any run, not after it
    assert set(f["split_package"]) == {i["name"] for i in body["scenario"]["issues"]}


def test_unknown_scenario_is_404():
    assert client.get("/api/scenarios/nope_not_here").status_code == 404


def test_scoring_reports_illegal_packages_rather_than_coercing_them():
    body = client.post(
        "/api/scenarios/haze_real/score",
        json={"package": {"concession_maps": "not_an_option"}},
    ).get_json()
    assert body["legal"] is False
    assert body["metrics"] is None
    assert any("not_an_option" in p for p in body["problems"])


# --------------------------------------------------------------------------
# create — the add-crisis flow
# --------------------------------------------------------------------------
def test_created_scenario_is_loadable_by_the_pipeline(cleanup_user_scenarios):
    res = client.post("/api/scenarios", json=_new_scenario())
    assert res.status_code == 201
    sid = res.get_json()["id"]
    assert sid.startswith("user_")

    # the real assertion: it round-trips through the normal loader, so a
    # user-created crisis is not a second-class kind of input
    loaded = scenario_mod.load(sid)
    assert loaded.title == "Test crisis"
    assert len(loaded.issues) == 2

    detail = client.get(f"/api/scenarios/{sid}").get_json()
    assert detail["frontier"]["settlements"] == 6      # 3 * 2
    assert detail["has_recording"] is False
    assert detail["user_created"] is True


def test_weights_are_normalised_and_the_change_is_reported(cleanup_user_scenarios):
    payload = _new_scenario("weights_case")
    for c in payload["scenario"]["countries"]:
        for v in c["valuations"]:
            v["weight"] = 0.5                          # sums to 1.0 for neither party... it does
    payload["scenario"]["countries"][0]["valuations"][0]["weight"] = 0.9   # now sums to 1.4
    res = client.post("/api/scenarios", json=payload)
    assert res.status_code == 201
    assert "Alpha" in res.get_json()["weights_normalised_for"]

    loaded = scenario_mod.load(res.get_json()["id"])
    total = sum(v.weight for v in loaded.countries[0].valuations)
    assert total == pytest.approx(1.0)


def test_one_issue_is_refused(cleanup_user_scenarios):
    payload = _new_scenario("one_issue")
    s = payload["scenario"]
    s["issues"] = s["issues"][:1]
    for c in s["countries"]:
        c["valuations"] = c["valuations"][:1]
    res = client.post("/api/scenarios", json=payload)
    assert res.status_code == 422
    assert "nothing to trade" in res.get_json()["detail"]


def test_a_party_that_weights_everything_at_zero_is_refused(cleanup_user_scenarios):
    payload = _new_scenario("zero_weights")
    for v in payload["scenario"]["countries"][1]["valuations"]:
        v["weight"] = 0.0
    res = client.post("/api/scenarios", json=payload)
    assert res.status_code == 422
    assert "nothing on the table matters" in res.get_json()["detail"]


def test_duplicate_id_is_refused_rather_than_overwriting(cleanup_user_scenarios):
    assert client.post("/api/scenarios", json=_new_scenario("dup")).status_code == 201
    assert client.post("/api/scenarios", json=_new_scenario("dup")).status_code == 409


def test_bundled_scenarios_cannot_be_deleted():
    # they are the experiment's fixtures; deleting one would silently change
    # what a reported result refers to
    assert client.delete("/api/scenarios/haze_real").status_code == 403
    assert scenario_mod.scenario_path("haze_real").exists()


def test_user_scenarios_can_be_deleted(cleanup_user_scenarios):
    sid = client.post("/api/scenarios", json=_new_scenario("deletable")).get_json()["id"]
    assert client.delete(f"/api/scenarios/{sid}").status_code == 204
    assert client.get(f"/api/scenarios/{sid}").status_code == 404


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------
def _confirm(scenario_id):
    """What a person does at the review gate before anything runs."""
    res = client.post(f"/api/scenarios/{scenario_id}/review", json={"confirm": True})
    assert res.status_code == 200
    return res.get_json()["token"]


def _events(confirmed=True, **params):
    if confirmed and "review" not in params:
        try:
            params["review"] = _confirm(params["scenario"])
        except AssertionError:
            pass                                    # an unknown scenario: the run reports it
    res = client.get("/api/run", query_string=params)
    assert res.status_code == 200
    return [json.loads(line[5:].strip())
            for line in res.get_data(as_text=True).splitlines()
            if line.startswith("data:")]


def test_a_run_is_refused_until_the_review_is_confirmed():
    """Paper Fig. 1 stage 2: nothing downstream runs until a person confirms the
    problem. Enforced by the server, not just the page."""
    events = _events(confirmed=False, scenario="haze_real", condition="B2")
    assert [e["type"] for e in events] == ["error"]
    assert "review" in events[0]["message"].lower()


def test_a_confirmation_does_not_carry_over_to_a_changed_problem():
    events = _events(scenario="haze_real", condition="B2", review="0000000000000000")
    assert [e["type"] for e in events] == ["error"]


def test_a_run_reads_the_cases_pinned_conditions():
    events = _events(scenario="haze_real", condition="B2", rounds=3)
    start = next(e for e in events if e["type"] == "start")
    done = next(e for e in events if e["type"] == "done")
    assert start["conditions_id"] and start["conditions_id"] == done["conditions_id"]


def test_a_reviewer_can_edit_the_judge_after_each_round():
    """Paper contribution 4. The run pauses after every judgment; the edited
    verdict is what carries forward, and the intervention is logged."""
    token = _confirm("haze_real")
    answered = []

    def answer():
        # Answer each pause as it appears, from a second thread, the way a
        # person in the browser would.
        while len(answered) < 3:
            for run_id, slot in list(app_module.PAUSED.items()):
                if slot["round"] not in answered:
                    answered.append(slot["round"])
                    client.post(f"/api/runs/{run_id}/review", json={
                        "summaries": {"Singapore": f"edited after round {slot['round']}"},
                        "note": "watch the funding issue" if slot["round"] == 1 else ""})
            time.sleep(0.01)

    t = threading.Thread(target=answer, daemon=True)
    t.start()
    res = client.get("/api/run", query_string={"scenario": "haze_real", "condition": "B2",
                                               "rounds": 3, "review": token, "pause": "1"})
    events = [json.loads(l[5:]) for l in res.get_data(as_text=True).splitlines()
              if l.startswith("data:")]
    t.join(timeout=5)
    kinds = [e["type"] for e in events]
    assert kinds.count("review") == 3
    assert kinds.count("intervention") == 3
    done = next(e for e in events if e["type"] == "done")
    assert done["interventions"] == 3


def test_a_reviewer_can_stop_the_pauses_for_the_rest_of_the_run():
    token = _confirm("haze_real")

    def answer():
        for _ in range(500):
            for run_id in list(app_module.PAUSED):
                client.post(f"/api/runs/{run_id}/review", json={"no_more_stops": True})
                return
            time.sleep(0.01)

    t = threading.Thread(target=answer, daemon=True)
    t.start()
    res = client.get("/api/run", query_string={"scenario": "haze_real", "condition": "B2",
                                               "rounds": 3, "review": token, "pause": "1"})
    kinds = [json.loads(l[5:])["type"] for l in res.get_data(as_text=True).splitlines()
             if l.startswith("data:")]
    assert kinds.count("review") == 1 and "done" in kinds


def test_reviewing_a_run_that_is_not_paused_is_a_404():
    assert client.post("/api/runs/nope/review", json={}).status_code == 404


def test_b2_streams_a_whole_debate_in_order():
    events = _events(scenario="haze_real", condition="B2", rounds=3, speed=0)
    kinds = [e["type"] for e in events]

    assert kinds[0] == "start"
    assert kinds[-1] == "done"
    assert "error" not in kinds

    args = [e for e in events if e["type"] == "argument"]
    judgments = [e for e in events if e["type"] == "judgment"]
    assert len(args) == 6                      # 2 parties x 3 rounds
    assert len(judgments) == 3
    assert [e["round"] for e in judgments] == [1, 2, 3]

    # a judgment for round r must arrive after both arguments of round r, or the
    # UI would render a verdict on turns it has not shown yet
    for r in (1, 2, 3):
        last_arg = max(i for i, e in enumerate(events)
                       if e["type"] == "argument" and e["round"] == r)
        judged = next(i for i, e in enumerate(events)
                      if e["type"] == "judgment" and e["round"] == r)
        assert judged > last_arg


def test_every_event_says_whether_it_was_live_or_recorded():
    events = _events(scenario="haze_real", condition="B2", rounds=1, speed=0)
    assert all(e.get("mode") in ("live", "recorded") for e in events if e["type"] != "error")


def test_b1_has_no_judge_and_b0_has_no_debate():
    b1 = [e["type"] for e in _events(scenario="haze_real", condition="B1", rounds=2, speed=0)]
    assert "judgment" not in b1
    assert b1.count("argument") == 4

    b0 = [e["type"] for e in _events(scenario="haze_real", condition="B0", rounds=3, speed=0)]
    assert "argument" not in b0
    assert "judgment" not in b0
    assert b0.count("recommendation") == 2      # one per default priority


# --------------------------------------------------------------------------
# tailored recommendations
# --------------------------------------------------------------------------
def test_health_publishes_the_audience_and_priority_vocabulary():
    """The UI renders these rather than keeping its own copy, so a priority
    added in Python must appear here without a frontend change."""
    body = client.get("/api/health").get_json()
    ids = {p["id"] for p in body["priorities"]}
    assert {"cost", "casualty_minimisation"} <= ids
    # one reader only: this tool advises one side
    assert {a["id"] for a in body["audiences"]} == {"policymaker"}
    assert body["audience"] == "policymaker"
    assert all(p["label"] and p["blurb"] for p in body["priorities"])
    assert body["default_priority"] in ids


def test_a_run_returns_one_recommendation_per_objective():
    events = _events(scenario="haze_real", condition="B2", rounds=3, speed=0,
                     priorities="casualty_minimisation,cost")
    recs = [e for e in events if e["type"] == "recommendation"]
    assert [r["priority"] for r in recs] == ["casualty_minimisation", "cost"]
    assert [r["index"] for r in recs] == [0, 1]
    assert all(r["of"] == 2 for r in recs)
    assert all(r["headline"] for r in recs)
    # one reader — the objective is what varies
    assert {r["audience"] for r in recs} == {"policymaker"}

    done = events[-1]
    assert done["type"] == "done"
    assert [r["priority"] for r in done["results"]] == ["casualty_minimisation", "cost"]


def test_the_two_objectives_select_different_settlements_on_the_haze_case():
    """The point of the feature. If protecting lives and protecting money opened
    at the same package there would be nothing to compare, and the console would
    be offering a choice that does not exist."""
    events = _events(scenario="haze_real", condition="B2", rounds=3, speed=0,
                     priorities="casualty_minimisation,cost")
    recs = [e for e in events if e["type"] == "recommendation"]
    assert recs[0]["package"] != recs[1]["package"]
    assert events[-1]["objectives_diverged"] is True

    # and the difference is measurable, not just textual
    assert all(r["score"]["legal"] for r in recs)
    assert (recs[0]["score"]["joint_value_recovered"]
            != recs[1]["score"]["joint_value_recovered"])


def test_the_objective_moves_the_package_for_a_fixed_reader():
    """Holding the reader fixed, changing what it protects must change what it
    opens with — otherwise the objective is not reaching the decision."""
    def pkg_for(priority):
        events = _events(scenario="haze_real", condition="B2", rounds=3, speed=0,
                         priorities=priority)
        recs = [e for e in events if e["type"] == "recommendation"]
        assert len(recs) == 1
        return recs[0]["package"]

    assert pkg_for("casualty_minimisation") != pkg_for("cost")


def test_asking_for_one_objective_costs_one_recommendation():
    events = _events(scenario="haze_real", condition="B2", rounds=3, speed=0,
                     priorities="cost")
    recs = [e for e in events if e["type"] == "recommendation"]
    assert len(recs) == 1
    assert events[-1]["objectives_diverged"] is False


def test_an_unrecorded_objective_says_so_rather_than_serving_another_ones_advice():
    """Relabelling recorded advice would show tailoring that never happened."""
    events = _events(scenario="haze_real", condition="B2", rounds=3, speed=0,
                     priorities="precedent")
    assert events[-1]["type"] == "error"
    assert "precedent" in events[-1]["message"]


def test_an_unknown_priority_is_rejected_before_any_call():
    events = _events(scenario="haze_real", condition="B2", rounds=1, speed=0,
                     priorities="profit")
    assert events[0]["type"] == "error"
    assert "profit" in events[0]["message"]


def test_arguments_carry_a_scored_package():
    args = [e for e in _events(scenario="haze_real", condition="B1", rounds=1, speed=0)
            if e["type"] == "argument"]
    for a in args:
        assert a["package"], "an argument with no package cannot be scored"
        assert a["score"]["legal"] is True
        assert 0.0 <= a["score"]["joint_value_recovered"] <= 1.0


def test_unknown_condition_yields_an_error_event_not_a_crash():
    events = _events(scenario="haze_real", condition="B9", rounds=1, speed=0)
    assert events[0]["type"] == "error"


def test_a_scenario_with_no_recording_reports_the_reason(cleanup_user_scenarios):
    """Without a key and without a recording there is nothing to serve. The run
    must say so rather than fabricating turns."""
    payload = _new_scenario("no_recording")
    sid = client.post("/api/scenarios", json=payload).get_json()["id"]
    events = _events(scenario=sid, condition="B2", rounds=1, speed=0)
    # Either it errors cleanly, or a key is present and it ran live.
    assert events[-1]["type"] in ("error", "done")
    if events[-1]["type"] == "done":
        assert events[-1]["mode"] == "live"


def test_a_client_that_leaves_releases_a_paused_run():
    """Pressing Stop while a check card waits must not leave the run waiting out
    its timeout: the run carries on without stopping and finishes."""
    s = scenario_mod.load("haze_real")
    stream = app_module._run_events("haze_real", "B2", 3, None, 0.0, "casualty_minimisation",
                                    review_token=app_module._fingerprint(s), pause=True)
    for chunk in stream:
        if '"type": "review"' in chunk:
            break
    assert app_module.PAUSED, "the run should be waiting for a person"
    stream.close()                                   # the browser stopped listening
    for _ in range(300):
        if not app_module.PAUSED:
            break
        time.sleep(0.01)
    assert not app_module.PAUSED
