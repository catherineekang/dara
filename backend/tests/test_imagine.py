"""The world model — imagining the negotiation before walking in.

No key and no network: the world model is arithmetic over the scenario.
"""
import pytest

from crisis_debate import scenario as scenario_mod
from crisis_debate.imagine import WorldModel, imagine, parse_opening, plan
from crisis_debate.utilities import split_the_difference, utility


@pytest.fixture(scope="module")
def haze():
    return scenario_mod.load("haze_real")


@pytest.fixture(scope="module")
def model(haze):
    return WorldModel.build(haze, samples=32)


def test_the_split_is_always_accepted(haze, model):
    """The acceptance floor is the split, so the split itself must clear it in
    every imagined world — otherwise a counter-offer could have nowhere to go."""
    r = imagine(model, split_the_difference(haze))
    assert r.accept_rate == 1.0
    assert r.likely_counter is None and r.sticking_points == []


def test_an_accepted_opening_is_where_you_land(haze, model):
    r = imagine(model, split_the_difference(haze))
    assert r.expected_share == r.split_share == r.worst_share
    assert r.below_split_rate == 0.0


def test_a_counter_is_always_acceptable_to_them(haze, model):
    """Their counter must clear their own floor in that world — it is THEIR offer."""
    from crisis_debate.imagine import _respond
    greedy = model.index(imagine(model, {}).opening)
    for world in model.worlds:
        landed = _respond(model, world, greedy)
        assert landed in world.acceptable


def test_your_own_utility_is_never_perturbed(haze):
    """Your weights are known, theirs are guessed: only theirs vary by world."""
    a = WorldModel.build(haze, samples=4, seed=1)
    b = WorldModel.build(haze, samples=4, seed=2)
    assert a.mine == b.mine
    me = haze.brief(haze.focus_country)
    assert a.my_split == utility(me, split_the_difference(haze))


def test_unspecified_issues_are_filled_and_reported(haze, model):
    r = imagine(model, {"enforcement": "joint_asean_panel"})
    assert r.opening["enforcement"] == "joint_asean_panel"
    assert set(r.filled_in) == {i.name for i in haze.issues} - {"enforcement"}
    assert any("did not specify" in line for line in r.summary())


def test_the_plan_beats_asking_for_everything(model):
    """The finding that makes the feature worth having: on the haze case,
    anchoring on your own first choice everywhere lands you lower than the
    best opening found in imagination."""
    best = plan(model)[0]
    greedy = imagine(model, {})
    assert best.expected_share >= greedy.expected_share
    assert best.accept_rate > greedy.accept_rate


def test_plan_alternatives_are_genuinely_different(model):
    openings = [tuple(sorted(r.opening.items())) for r in plan(model, top=3)]
    assert len(set(openings)) == len(openings) == 3


def test_build_is_reproducible(haze):
    a = plan(WorldModel.build(haze, samples=16, seed=0))
    b = plan(WorldModel.build(haze, samples=16, seed=0))
    assert [r.as_dict() for r in a] == [r.as_dict() for r in b]


def test_parse_opening_is_forgiving_and_errors_name_the_choices(haze):
    assert parse_opening(haze, "Enforcement = Joint ASEAN Panel") == \
        {"enforcement": "joint_asean_panel"}
    with pytest.raises(ValueError, match="choose from"):
        parse_opening(haze, "enforcement=courts")
    with pytest.raises(ValueError, match="unknown issue"):
        parse_opening(haze, "weather=sunny")


# --------------------------------------------------------------------------
# HTTP — the Flask server README tells you to run
# --------------------------------------------------------------------------
flask = pytest.importorskip("flask")


@pytest.fixture(scope="module")
def client():
    from app import app
    return app.test_client()


def test_imagine_endpoint_plans_without_an_opening(client):
    res = client.post("/api/scenarios/haze_real/imagine", json={})
    assert res.status_code == 200
    body = res.get_json()
    assert body["mode"] == "plan" and len(body["rehearsals"]) == 3
    assert all(r["summary"] for r in body["rehearsals"])


def test_imagine_endpoint_answers_a_what_if(client):
    res = client.post("/api/scenarios/haze_real/imagine",
                      json={"opening": {"transition_funding": "none"}})
    body = res.get_json()
    assert body["mode"] == "what_if"
    assert body["rehearsals"][0]["opening"]["transition_funding"] == "none"


def test_imagine_endpoint_rejects_an_illegal_opening(client):
    res = client.post("/api/scenarios/haze_real/imagine",
                      json={"opening": {"enforcement": "courts"}})
    assert res.status_code == 422
    assert "joint_asean_panel" in res.get_json()["detail"]   # names the legal options


# --------------------------------------------------------------------------
# red lines are hard constraints
# --------------------------------------------------------------------------
def test_plan_never_opens_with_your_own_red_line(haze, model):
    from crisis_debate.imagine import red_lines_crossed
    me = haze.brief(haze.focus_country)
    for r in plan(model, top=3):
        assert red_lines_crossed(me, r.opening) == []


def test_they_never_settle_on_their_own_red_line(haze, model):
    from crisis_debate.imagine import _respond, red_lines_crossed
    them = haze.opponents(haze.focus_country)[0]
    for o in range(len(model.packages)):
        for world in model.worlds[:4]:
            s = _respond(model, world, o)
            if s is not None:
                assert red_lines_crossed(them, model.packages[s]) == []


def test_a_what_if_on_your_own_red_line_is_flagged(model):
    r = imagine(model, {"concession_maps": "withheld"})     # 0.0 for Singapore
    assert r.crosses_your_red_lines == ["concession_maps"]
    assert "unacceptable" in r.summary()[0]


def test_no_zone_of_agreement_is_reported_not_hidden(haze):
    """If each side's every acceptable option is the other's red line, there is
    nothing to plan — the briefing must say so rather than invent a deal."""
    s = haze.model_copy(deep=True)
    for c in s.countries:
        for v in c.valuations:
            ranked = sorted(v.option_values, key=v.option_values.get)
            for opt in ranked[:-1]:                 # only one's own favourite survives
                v.option_values[opt] = 0.0
    m = WorldModel.build(s, samples=8)
    r = imagine(m, {})
    assert r.no_deal_rate == 1.0
    assert any("No deal is possible" in line for line in r.summary())
