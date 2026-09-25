"""The pipeline as a LangGraph state machine.

`pipeline.py` runs the same stages as a plain loop and is what the tests pin.
This module expresses them as an explicit graph, which buys three things the
loop does not: the round cycle is a visible edge rather than a `for`, each
stage is an addressable node, and the state passed between stages is one typed
object you can checkpoint or stream.

Both engines call the same agent functions and the same backend, so they must
produce identical output for a given backend. `test_graph.py` asserts that
against the cached backend — if the two ever diverge, one of them is wrong.

    setup ─▶ argue ─▶ judge ─┐
              ▲              │  another round?
              └──────────────┘
                             ▼
                        recommend ─▶ score
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

from crisis_debate.agents import (
    CountryAgent,
    JudgeAgent,
    RecommenderAgent,
    baseline_best_of_n,
    baseline_recommendation,
    debate_call_count,
)
from crisis_debate.schemas import Argument, DebateRun, Recommendation, RoundJudgment, Scenario
from crisis_debate.utilities import Frontier, score_package, validate_package


class DebateState(TypedDict, total=False):
    scenario: Scenario
    backend: Any
    condition: str
    rounds: int
    best_of_n: Optional[int]
    r: int
    arguments: List[List[Argument]]
    judgments: List[RoundJudgment]
    recommendation: Recommendation
    illegal: List[str]
    outcome: Dict[str, float]


# --------------------------------------------------------------------------
# nodes
# --------------------------------------------------------------------------
def n_setup(s: DebateState) -> DebateState:
    return {"r": 0, "arguments": [], "judgments": [], "illegal": []}


def n_argue(s: DebateState) -> DebateState:
    r = s["r"] + 1
    prev = s["arguments"][-1] if s["arguments"] else []
    prev_j = s["judgments"][-1] if s["judgments"] else None
    unaddressed = prev_j.unaddressed_tradeoffs if prev_j else []

    turns: List[Argument] = []
    for brief in s["scenario"].countries:
        agent = CountryAgent(brief=brief, backend=s["backend"])
        own = None
        if prev_j is not None:
            try:
                own = prev_j.for_country(brief.name).summary
            except KeyError:
                own = None
        turns.append(agent.argue(s["scenario"], r,
                                 previous_opposing=[a for a in prev if a.country != brief.name],
                                 own_feedback=own, unaddressed=unaddressed))
    return {"r": r, "arguments": s["arguments"] + [turns]}


def n_judge(s: DebateState) -> DebateState:
    if s["condition"] != "B2":
        return {}
    rj = JudgeAgent(backend=s["backend"]).judge(s["scenario"], s["r"], s["arguments"][-1])
    return {"judgments": s["judgments"] + [rj]}


def n_recommend(s: DebateState) -> DebateState:
    rec = RecommenderAgent(backend=s["backend"])
    if s["condition"] == "B2":
        out = rec.recommend(s["scenario"], s["judgments"])
    else:
        out = rec.recommend_from_transcript(s["scenario"], s["arguments"])
    return {"recommendation": out}


def n_baseline(s: DebateState) -> DebateState:
    sc, be = s["scenario"], s["backend"]
    if s["condition"] == "B0":
        return {"recommendation": baseline_recommendation(sc, be)}
    n = s.get("best_of_n") or debate_call_count(sc, rounds=s["rounds"])
    return {"recommendation": baseline_best_of_n(sc, be, n)}


def n_score(s: DebateState) -> DebateState:
    sc, rec = s["scenario"], s["recommendation"]
    problems: List[str] = []
    for i, rnd in enumerate(s.get("arguments", []), start=1):
        for a in rnd:
            problems += [f"round {i} {a.country}: {p}" for p in validate_package(sc, a.package())]
    problems += [f"recommendation: {p}" for p in validate_package(sc, rec.package())]
    outcome = {} if problems else score_package(Frontier.build(sc), rec.package())
    return {"illegal": problems, "outcome": outcome}


# --------------------------------------------------------------------------
# edges
# --------------------------------------------------------------------------
def _entry(s: DebateState) -> str:
    return "baseline" if s["condition"] in ("B0", "B0N") else "argue"


def _more_rounds(s: DebateState) -> str:
    return "argue" if s["r"] < s["rounds"] else "recommend"


def build_graph():
    from langgraph.graph import END, START, StateGraph

    g = StateGraph(DebateState)
    for name, fn in (("setup", n_setup), ("argue", n_argue), ("judge", n_judge),
                     ("recommend", n_recommend), ("baseline", n_baseline), ("score", n_score)):
        g.add_node(name, fn)
    g.add_edge(START, "setup")
    g.add_conditional_edges("setup", _entry, {"argue": "argue", "baseline": "baseline"})
    g.add_edge("argue", "judge")
    g.add_conditional_edges("judge", _more_rounds, {"argue": "argue", "recommend": "recommend"})
    g.add_edge("recommend", "score")
    g.add_edge("baseline", "score")
    g.add_edge("score", END)
    return g.compile()


def run(scenario: Scenario, backend: Any, *, condition: str = "B2", rounds: int = 3,
        best_of_n: Optional[int] = None) -> DebateRun:
    """Run the graph and return the same DebateRun `pipeline.run` returns."""
    if condition not in ("B0", "B0N", "B1", "B2"):
        raise ValueError(f"unknown condition {condition!r}")
    if best_of_n is not None and best_of_n < 1:
        raise ValueError("n must be >= 1")

    final = build_graph().invoke({
        "scenario": scenario, "backend": backend, "condition": condition,
        "rounds": rounds, "best_of_n": best_of_n,
    })
    return DebateRun(
        scenario_id=scenario.scenario_id, condition=condition,
        rounds=final.get("arguments", []), judgments=final.get("judgments", []),
        recommendation=final["recommendation"],
        usage=getattr(backend, "usage", {}), illegal_packages=final.get("illegal", []),
    )
