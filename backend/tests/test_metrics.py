"""Tests for the success-criteria metrics."""
from __future__ import annotations

import pytest

from crisis_debate import metrics
from crisis_debate.schemas import (
    CRITERIA,
    Argument,
    IssueValuation,
    PackageItem,
    ArgumentJudgment,
    Claim,
    CountryBrief,
    CriterionScore,
    RoundJudgment,
)


BRIEF = CountryBrief(
    name="Aurelia",
    interests=["end hazardous air quality in its cities"],
    resources=["regional development fund"],
    constraints=["cannot compel action abroad"],
    red_lines=["will not accept a settlement with no independent verification"],
    valuations=[IssueValuation(issue="verification", weight=1.0,
                               option_values={"none": 0.0, "satellite_independent": 1.0})],
)


def _arg(position="hold the line", red_lines=None, concessions=None, claims=None):
    return Argument(
        country="Aurelia",
        position=position,
        key_claims=claims or [Claim(claim="hazardous air quality must end in cities",
                                    evidence="eleven days above threshold")],
        concessions=concessions if concessions is not None else ["phase the timeline"],
        red_lines=red_lines if red_lines is not None else
        ["will not accept a settlement with no independent verification"],
        proposed_package=[PackageItem(issue="verification", option="satellite_independent")],
    )


def test_faithful_argument_scores_high():
    m = metrics.brief_consistency(_arg(), BRIEF)
    assert m["red_line_fidelity"] > 0.8
    assert m["interest_anchoring"] > 0.3
    assert m["n_red_lines_dropped"] == 0


def test_dropped_red_line_is_detected():
    """Criterion (a): drifting into a generic position must be visible."""
    m = metrics.brief_consistency(_arg(red_lines=[]), BRIEF)
    assert m["red_line_fidelity"] == 0.0
    assert m["n_red_lines_dropped"] == 1


def test_conceding_a_red_line_is_flagged():
    m = metrics.brief_consistency(
        _arg(concessions=["accept a settlement with no independent verification"]), BRIEF
    )
    assert m["n_concessions_conflicting_with_red_lines"] == 1


def test_position_movement_zero_when_agent_restates_itself():
    rounds = [[_arg(position="we demand verified monitoring")] for _ in range(3)]
    assert metrics.position_movement(rounds, "Aurelia") == [0.0, 0.0]


def test_position_movement_positive_when_position_changes():
    rounds = [
        [_arg(position="we demand an immediate enforced ban")],
        [_arg(position="we accept a phased timeline with compensation")],
    ]
    assert metrics.position_movement(rounds, "Aurelia")[0] > 0.5


def _judgment(scores, country="Aurelia", idx=1):
    return RoundJudgment(
        round_index=idx,
        judgments=[ArgumentJudgment(
            country=country,
            scores=[CriterionScore(criterion=c, score=s, justification="x")
                    for c, s in zip(CRITERIA, scores)],
            summary="s",
        )],
        unaddressed_tradeoffs=[],
    )


def test_identical_rescoring_is_perfectly_consistent():
    reps = [_judgment([3, 4, 2, 5, 3]) for _ in range(3)]
    stats = metrics.judge_self_consistency(reps)["Aurelia"]
    assert stats["feasibility_exact_agreement"] == 1.0
    assert stats["feasibility_mad"] == 0.0


def test_unstable_judge_shows_deviation():
    reps = [_judgment([1, 4, 2, 5, 3]), _judgment([5, 4, 2, 5, 3])]
    stats = metrics.judge_self_consistency(reps)["Aurelia"]
    assert stats["feasibility_mad"] == 2.0
    assert stats["specificity_mad"] == 0.0


def test_self_consistency_needs_repeats():
    with pytest.raises(ValueError, match="at least two"):
        metrics.judge_self_consistency([_judgment([3] * 5)])


def test_position_bias_is_zero_when_order_does_not_matter():
    a = _judgment([3, 3, 3, 3, 3])
    assert set(metrics.position_bias(a, a)["Aurelia"].values()) == {0}


def test_position_bias_detected_on_swap():
    original, swapped = _judgment([3, 3, 3, 3, 3]), _judgment([5, 3, 3, 3, 3])
    assert metrics.position_bias(original, swapped)["Aurelia"]["feasibility"] == 2


# --------------------------------------------------------------------------
# Upgrade C
# --------------------------------------------------------------------------
def test_spearman_basics():
    assert metrics.spearman([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0
    assert metrics.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    # A constant series carries no ordering, so it must not read as correlated.
    assert metrics.spearman([1, 1, 1, 1], [1, 2, 3, 4]) == 0.0


def test_spearman_refuses_tiny_samples():
    with pytest.raises(ValueError, match="at least 3"):
        metrics.spearman([1, 2], [1, 2])


def test_judge_validity_detects_a_useless_judge():
    """The headline negative result the project is set up to be able to find:
    judge scores that do not track objective settlement quality."""
    pairs = [(25, 0.4), (10, 0.9), (20, 0.5), (15, 0.85), (22, 0.45)]
    out = metrics.judge_validity(pairs)
    assert out["spearman_rho"] < 0
    assert out["reads_as"] in {"moderate", "strong"}


def test_judge_validity_detects_a_useful_judge():
    pairs = [(10, 0.2), (15, 0.4), (20, 0.6), (25, 0.9)]
    assert metrics.judge_validity(pairs)["spearman_rho"] == 1.0


def test_judge_validity_flat_reads_as_no_relationship():
    pairs = [(20, 0.5), (20, 0.7), (20, 0.3), (20, 0.9)]
    assert metrics.judge_validity(pairs)["reads_as"] == "no relationship"


def test_self_preference_bias_detected():
    own = [_judgment([5, 5, 5, 5, 5])]
    other = [_judgment([3, 3, 3, 3, 3])]
    out = metrics.self_preference_bias(own, other)
    assert out["delta"] == 10.0 and out["favours_own_family"] is True


def test_self_preference_bias_absent():
    a = [_judgment([4, 4, 4, 4, 4])]
    assert metrics.self_preference_bias(a, a)["delta"] == 0.0
