"""The LangGraph engine and the plain loop must agree.

Two orchestrations of the same stages is a liability unless they are pinned to
each other: if they ever diverge, one of them is wrong and no result from
either can be trusted. These run both against the same recorded backend and
compare what comes out.
"""
from __future__ import annotations

import pytest

from crisis_debate import graph, pipeline, scenario as scenario_mod
from crisis_debate.cached import CachedBackend

pytest.importorskip("langgraph", reason="graph engine needs langgraph installed")


@pytest.fixture(scope="module")
def real():
    return scenario_mod.load("haze_real")


def _both(real, condition, rounds=3):
    a = pipeline.run(real, CachedBackend.for_scenario(real), condition=condition, rounds=rounds)
    b = graph.run(real, CachedBackend.for_scenario(real), condition=condition, rounds=rounds)
    return a, b


@pytest.mark.parametrize("condition", ["B0", "B0N", "B1", "B2"])
def test_engines_agree(real, condition):
    a, b = _both(real, condition)
    assert a.recommendation.package() == b.recommendation.package()
    assert len(a.rounds) == len(b.rounds)
    assert len(a.judgments) == len(b.judgments)
    assert a.illegal_packages == b.illegal_packages


def test_graph_runs_the_requested_number_of_rounds(real):
    for r in (1, 2, 3):
        run = graph.run(real, CachedBackend.for_scenario(real), condition="B2", rounds=r)
        assert len(run.rounds) == r and len(run.judgments) == r


def test_graph_skips_the_judge_in_b1(real):
    run = graph.run(real, CachedBackend.for_scenario(real), condition="B1")
    assert run.rounds and run.judgments == []


def test_graph_rejects_bad_input(real):
    with pytest.raises(ValueError, match="unknown condition"):
        graph.run(real, CachedBackend.for_scenario(real), condition="B9")
    with pytest.raises(ValueError, match="n must be"):
        graph.run(real, CachedBackend.for_scenario(real), condition="B0N", best_of_n=0)


def test_recorded_debate_beats_the_recorded_baseline(real):
    """The cached runs should demonstrate the mechanism the project is about:
    deliberation recovering joint value a single call leaves unclaimed."""
    from crisis_debate.utilities import Frontier, score_package

    f = Frontier.build(real)
    b2 = graph.run(real, CachedBackend.for_scenario(real), condition="B2")
    b0 = graph.run(real, CachedBackend.for_scenario(real), condition="B0")
    s2 = score_package(f, b2.recommendation.package())
    s0 = score_package(f, b0.recommendation.package())
    assert s2["joint_value_recovered"] > s0["joint_value_recovered"]
    assert s2["is_pareto_optimal"] == 1.0
