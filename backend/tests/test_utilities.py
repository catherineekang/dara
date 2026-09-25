"""Tests for the payoff layer - the ground truth everything else is scored against."""
from __future__ import annotations

import pytest

from crisis_debate import scenario as scenario_mod
from crisis_debate.utilities import (
    Frontier,
    all_packages,
    distance_to_frontier,
    format_valuations,
    score_package,
    split_the_difference,
    utility,
    utility_vector,
    validate_package,
)


@pytest.fixture(scope="module")
def scenario():
    return scenario_mod.load("haze_real")


@pytest.fixture(scope="module")
def frontier(scenario):
    return Frontier.build(scenario)


def test_outcome_space_is_enumerated_exactly(scenario, frontier):
    expected = 1
    for issue in scenario.issues:
        expected *= len(issue.options)
    assert len(all_packages(scenario)) == expected == 81
    assert 0 < len(frontier.pareto_indices) < len(frontier.packages)


def test_weights_sum_to_one(scenario):
    for c in scenario.countries:
        assert abs(sum(v.weight for v in c.valuations) - 1.0) < 1e-9


def test_utilities_are_bounded(scenario, frontier):
    for v in frontier.vectors:
        for value in v.values():
            assert 0.0 <= value <= 1.0


def test_incomplete_package_scores_strictly_less(scenario):
    """An agent must not be able to score well by declining to commit."""
    full = split_the_difference(scenario)
    partial = {k: v for k, v in list(full.items())[:2]}
    brief = scenario.brief(scenario.focus_country)
    assert utility(brief, partial) < utility(brief, full)


def test_split_the_difference_is_the_zero_point(scenario, frontier):
    assert score_package(frontier, split_the_difference(scenario))["integrative_gain"] == 0.0


def test_integrative_optimum_beats_splitting(scenario, frontier):
    """The property that makes the whole project worth running: there exists a
    settlement both sides prefer to halving every issue. If this ever fails the
    scenario has no integrative structure and debate cannot help."""
    best = max(frontier.pareto_packages(),
               key=lambda p: sum(utility_vector(scenario, p).values()))
    split_v = utility_vector(scenario, split_the_difference(scenario))
    best_v = utility_vector(scenario, best)
    assert all(best_v[c] > split_v[c] for c in best_v), "no win-win trade exists"
    assert score_package(frontier, best)["integrative_gain"] == 1.0
    assert score_package(frontier, best)["is_pareto_optimal"] == 1.0


def test_distance_to_frontier_zero_only_on_frontier(scenario, frontier):
    best = max(frontier.pareto_packages(),
               key=lambda p: sum(utility_vector(scenario, p).values()))
    assert distance_to_frontier(frontier, best) == 0.0
    assert distance_to_frontier(frontier, split_the_difference(scenario)) > 0.0


def test_illegal_packages_are_reported_not_coerced(scenario):
    assert validate_package(scenario, split_the_difference(scenario)) == []
    bad = dict(split_the_difference(scenario), **{scenario.issues[0].name: "telepathy"})
    assert any("telepathy" in p for p in validate_package(scenario, bad))
    missing = {k: v for k, v in list(split_the_difference(scenario).items())[:2]}
    assert any("unsettled" in p for p in validate_package(scenario, missing))
    assert any("unknown issue" in p for p in validate_package(scenario, {"moon": "cheese"}))


def test_format_valuations_renders_only_that_country(scenario):
    """Information asymmetry starts here: this renders one brief, never two."""
    a, b = scenario.countries[0], scenario.countries[1]
    text = format_valuations(a, scenario.issues)
    assert scenario.issues[0].name in text and "importance" in text
    assert b.name not in text, "one party's card must not leak the other's"
