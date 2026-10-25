"""The plain-language form: rankings and good/acceptable/unacceptable in,
a Scenario out — the same object the bundled cases are."""
import json
from pathlib import Path

import pytest

from crisis_debate import intake
from crisis_debate.imagine import WorldModel, plan
from crisis_debate.schemas import Scenario

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "haze_form.json"


@pytest.fixture
def form():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_the_example_builds_a_valid_scenario(form):
    s = intake.build(form)
    Scenario.model_validate(s.model_dump())
    assert s.focus_country == "Singapore"
    assert [i.name for i in s.issues][0] == "concession_maps"
    for c in s.countries:
        assert abs(sum(v.weight for v in c.valuations) - 1.0) < 1e-6


def test_rank_order_centroid_weights():
    w = intake.roc_weights(["a", "b", "c", "d"])
    assert [round(w[k], 4) for k in "abcd"] == [0.5208, 0.2708, 0.1458, 0.0625]
    tied = intake.roc_weights(["a", ["b", "c"], "d"])
    assert tied["b"] == tied["c"] == pytest.approx((w["b"] + w["c"]) / 2)
    assert sum(tied.values()) == pytest.approx(1.0)


def test_marks_become_values_and_unacceptable_becomes_a_red_line(form):
    s = intake.build(form)
    sg = s.brief("Singapore")
    maps = sg.valuation("concession_maps").option_values
    assert maps == {"withheld": 0.0, "aggregated_by_district": 0.5, "full_to_asean_hms": 1.0}
    assert "Will not accept 'withheld' on concession maps." in sg.red_lines
    # the person's own red lines are kept alongside the derived ones
    assert "Will not accept a settlement with no attribution mechanism" in sg.red_lines


def test_labels_are_forgiving(form):
    form["you"]["ranking"][0] = "  CONCESSION maps "
    assert intake.build(form).brief("Singapore").valuation("concession_maps").weight > 0.5


@pytest.mark.parametrize("break_it, says", [
    (lambda f: f["you"]["ranking"].pop(), "missing"),
    (lambda f: f["them"]["ranking"].append("Enforcement"), "repeated"),
    (lambda f: f["you"]["options"]["Enforcement"].pop("Joint ASEAN panel"), "mark how singapore sees “enforcement” → “joint asean panel”"),
    (lambda f: f["you"]["options"]["Enforcement"].update({"Joint ASEAN panel": "maybe"}), "good, acceptable or unacceptable"),
    (lambda f: f.update(issues=f["issues"][:1]), "at least two issues"),
    (lambda f: f["you"]["ranking"].__setitem__(0, "Weather"), "not one of the issues"),
])
def test_mistakes_are_explained_in_plain_words(form, break_it, says):
    break_it(form)
    with pytest.raises(intake.IntakeError, match=None) as exc:
        intake.build(form)
    assert says in str(exc.value).lower()


def test_review_reads_the_answers_back(form):
    lines = intake.review(intake.build(form))
    assert lines[0].startswith("What matters to you, in order: concession maps (52%)")
    assert any("Indonesia" in line and "enforcement" in line for line in lines)


def test_a_form_scenario_can_be_rehearsed(form):
    assert len(plan(WorldModel.build(intake.build(form), samples=16))) == 3


# --------------------------------------------------------------------------
# HTTP: preview is the review gate; confirm saves
# --------------------------------------------------------------------------
flask = pytest.importorskip("flask")


@pytest.fixture
def client():
    from app import app
    return app.test_client()


def test_intake_previews_without_saving(client, form):
    from crisis_debate import scenario as scenario_mod
    before = set(scenario_mod.EVAL_DIR.glob("user_*.json"))
    res = client.post("/api/intake", json={"form": form})
    assert res.status_code == 200
    body = res.get_json()
    assert body["saved"] is False and body["review"] and len(body["rehearsals"]) == 3
    assert set(scenario_mod.EVAL_DIR.glob("user_*.json")) == before


def test_intake_confirm_saves_a_user_scenario(client, form):
    from crisis_debate import scenario as scenario_mod
    form["title"] = "Intake confirm test"
    res = client.post("/api/intake", json={"form": form, "confirm": True})
    try:
        assert res.status_code == 201
        sid = res.get_json()["id"]
        assert sid.startswith("user_")
        assert client.get(f"/api/scenarios/{sid}").status_code == 200
    finally:
        (scenario_mod.EVAL_DIR / "user_intake_confirm_test.json").unlink(missing_ok=True)


def test_intake_errors_are_readable(client, form):
    form["you"]["ranking"].pop()
    res = client.post("/api/intake", json={"form": form})
    assert res.status_code == 422 and "missing" in res.get_json()["detail"]


def test_the_example_form_is_served_and_builds(client):
    res = client.get("/api/intake/example")
    assert res.status_code == 200
    assert intake.build(res.get_json()).focus_country == "Singapore"


def test_the_cutoff_defaults_to_today_and_can_be_set(form):
    from datetime import date

    assert intake.build(form).evidence_cutoff == date.today().isoformat()
    form["evidence_cutoff"] = "2015-09-01"
    assert intake.build(form).evidence_cutoff == "2015-09-01"
    form["evidence_cutoff"] = "last tuesday"
    with pytest.raises(intake.IntakeError, match="cutoff"):
        intake.build(form)


def test_typed_elements_are_labelled_as_typed_not_sourced(form):
    """Nothing typed into the form comes from a document: your side is yours,
    the other side is your estimate of them."""
    s = intake.build(form)
    you, them = s.countries
    assert {p.status for p in you.provenance.values()} == {"user_edited"}
    assert {p.status for p in them.provenance.values()} == {"projected"}
    assert set(you.provenance) == set(you.interests + you.resources + you.constraints + you.red_lines)


def test_extracted_support_survives_the_form(form):
    form["you"]["red_lines"] = ["No foreign court"]
    form["you"]["provenance"] = {"No foreign court": {
        "value": "No foreign court", "status": "sourced",
        "evidence": [{"source_id": "S1", "quote": "a quote"}]}}
    s = intake.build(form)
    assert s.countries[0].provenance["No foreign court"].status == "sourced"
