"""Orchestration for the three experimental conditions in proposal Section 4.1.

    B0  single-model baseline   one call, no debate, no judge
    B1  debate without judge    three rounds, recommender reads the transcript
    B2  full system             three rounds, judge each round, recommender
                                reads only the judge

The conditions share one code path so that a difference between them is a
difference in structure, not in scaffolding. `rounds` is a parameter rather
than a constant so the round-count ablation is a flag, not a fork.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from crisis_debate.agents import (
    Backend,
    CountryAgent,
    JudgeAgent,
    RecommenderAgent,
    baseline_best_of_n,
    baseline_recommendation,
    debate_call_count,
)
from crisis_debate.schemas import Argument, DebateRun, RoundJudgment, Scenario
from crisis_debate.utilities import validate_package

CONDITIONS = ("B0", "B0N", "B1", "B2")


class RunLogger:
    """Appends one JSONL record per pipeline event.

    Section 4 stage 5: any reported number must be traceable to the exchange
    that produced it. A no-op when no path is given, so tests stay silent.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    def log(self, kind: str, payload: Any) -> None:
        if not self.path:
            return
        record = {
            "ts": time.time(),
            "kind": kind,
            "payload": payload.model_dump() if hasattr(payload, "model_dump") else payload,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _package_problems(scenario, rounds, recommendation) -> List[str]:
    """Collect every illegal package, labelled by where it came from.

    Recorded, never coerced: snapping an illegal option to the nearest legal one
    would quietly change the utility vector and corrupt every efficiency number
    computed from it. A run with entries here has a measurement problem that the
    report has to state, not hide.
    """
    problems: List[str] = []
    for r, round_args in enumerate(rounds, start=1):
        for arg in round_args:
            for issue in validate_package(scenario, arg.package()):
                problems.append(f"round {r} {arg.country}: {issue}")
    for issue in validate_package(scenario, recommendation.package()):
        problems.append(f"recommendation: {issue}")
    return problems


def run_debate_rounds(
    scenario: Scenario,
    backend: Backend,
    *,
    rounds: int = 3,
    use_judge: bool = True,
    logger: Optional[RunLogger] = None,
) -> tuple[List[List[Argument]], List[RoundJudgment]]:
    """Run the debate. Returns (arguments per round, judgments per round).

    Every country argues before any judging happens, so no agent sees another's
    argument from the same round - otherwise whoever went last would be
    answering a moving target and the round would not be a fair comparison.
    """
    logger = logger or RunLogger()
    agents = [CountryAgent(brief=b, backend=backend) for b in scenario.countries]
    judge = JudgeAgent(backend=backend) if use_judge else None

    all_args: List[List[Argument]] = []
    all_judgments: List[RoundJudgment] = []

    for r in range(1, rounds + 1):
        prev_args = all_args[-1] if all_args else []
        prev_judgment = all_judgments[-1] if all_judgments else None
        unaddressed = prev_judgment.unaddressed_tradeoffs if prev_judgment else []

        round_args: List[Argument] = []
        for agent in agents:
            opposing = [a for a in prev_args if a.country != agent.brief.name]
            own_feedback = None
            if prev_judgment is not None:
                try:
                    own_feedback = prev_judgment.for_country(agent.brief.name).summary
                except KeyError:
                    own_feedback = None
            arg = agent.argue(
                scenario, r,
                previous_opposing=opposing,
                own_feedback=own_feedback,
                unaddressed=unaddressed,
            )
            logger.log("argument", arg)
            round_args.append(arg)
        all_args.append(round_args)

        if judge is not None:
            rj = judge.judge(scenario, r, round_args)
            logger.log("judgment", rj)
            all_judgments.append(rj)

    return all_args, all_judgments


def run(
    scenario: Scenario,
    backend: Backend,
    *,
    condition: str = "B2",
    rounds: int = 3,
    best_of_n: Optional[int] = None,
    log_path: Optional[Path] = None,
    logger: Optional[RunLogger] = None,
) -> DebateRun:
    """Run one condition end to end.

    `logger` is injectable so a caller can watch the run as it happens rather
    than only reading the result: the HTTP layer passes a logger that pushes
    each event onto a queue, which is what lets the console stream a debate
    turn by turn. Left unset it behaves exactly as before.
    """
    if condition not in CONDITIONS:
        raise ValueError(f"condition must be one of {CONDITIONS}, got {condition!r}")
    logger = logger or RunLogger(log_path)
    logger.log("scenario", scenario)

    if condition in ("B0", "B0N"):
        if condition == "B0":
            rec = baseline_recommendation(scenario, backend)
        else:
            # Matched to whatever the debate condition would have spent, so the
            # comparison isolates structure rather than budget.
            # `is None`, not `or`: best_of_n=0 is invalid input that must
            # raise, and `or` would silently swap it for the default.
            n = debate_call_count(scenario, rounds=rounds) if best_of_n is None else best_of_n
            rec = baseline_best_of_n(scenario, backend, n)
        logger.log("recommendation", rec)
        return DebateRun(
            scenario_id=scenario.scenario_id, condition=condition,
            rounds=[], judgments=[], recommendation=rec,
            usage=getattr(backend, "usage", {}),
            illegal_packages=_package_problems(scenario, [], rec),
        )

    use_judge = condition == "B2"
    all_args, judgments = run_debate_rounds(
        scenario, backend, rounds=rounds, use_judge=use_judge, logger=logger,
    )

    recommender = RecommenderAgent(backend=backend)
    if use_judge:
        rec = recommender.recommend(scenario, judgments)
    else:
        rec = recommender.recommend_from_transcript(scenario, all_args)
    logger.log("recommendation", rec)

    return DebateRun(
        scenario_id=scenario.scenario_id, condition=condition,
        rounds=all_args, judgments=judgments, recommendation=rec,
        usage=getattr(backend, "usage", {}),
        illegal_packages=_package_problems(scenario, all_args, rec),
    )
