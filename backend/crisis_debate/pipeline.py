"""Orchestration for the three experimental conditions in proposal Section 4.1.

    B0  single-model baseline   one call per objective, no debate, no judge
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
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from crisis_debate.agents import (
    Backend,
    CountryAgent,
    JudgeAgent,
    RecommenderAgent,
    baseline_recommendation,
)
from crisis_debate.schemas import Argument, DebateRun, RoundJudgment, Scenario
from crisis_debate.situational import (
    ConditionsSnapshot,
    reachable_report,
    render_for_prompt,
)
from crisis_debate.utilities import validate_package

CONDITIONS = ("B0", "B1", "B2")

#: Paper contribution 4: a human may inspect and intervene at the judge stage.
#: Called after each round's judgment with (round, judgment); returns the
#: judgment to carry forward (edited or not) and an optional note for the
#: recommender. The debaters' next-round feedback and the recommender both read
#: what this returns, so an edit after round 1 shapes rounds 2 and 3.
JudgeReview = Callable[[int, RoundJudgment], Tuple[RoundJudgment, str]]


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


#: The only reader. This tool advises one side, so there is one audience; what
#: varies between recommendations is what the reader is trying to PROTECT.
AUDIENCE = "policymaker"

#: The default pair of objectives, and the default when the caller does not say.
#: Harm-to-people against public money is the trade that most often selects a
#: DIFFERENT settlement on these crises, which is the whole point of showing two:
#: if both objectives picked the same package there would be nothing to choose.
DEFAULT_PRIORITIES = ("casualty_minimisation", "cost")
DEFAULT_PRIORITY = DEFAULT_PRIORITIES[0]

#: The single objective every condition is scored on when the research question
#: is being measured. Declared as a constant, not taken as "whichever
#: recommendation happened to come first" — with two objectives per run that
#: would have let the headline number depend on list order.
#:
#: `none` is deliberate: tailoring for lives-or-money is a second free variable,
#: and the RQ is not about it. The untailored reading is the control.
RQ_AUDIENCE = AUDIENCE
RQ_PRIORITY = "none"


def rq_recommendation(run):
    """The one recommendation a between-condition comparison may be scored on.

    Raises rather than falling back, because a silent fallback is exactly how
    the arbitrary `recs[0]` got in: if the run does not contain the declared
    pair, the comparison is not the one the contract describes and should stop.
    """
    for rec in run.recommendations:
        if rec.audience == RQ_AUDIENCE and rec.priority == RQ_PRIORITY:
            return rec
    have = [r.priority for r in run.recommendations]
    raise ValueError(
        f"this run holds no {RQ_PRIORITY!r} recommendation, so it cannot be "
        f"scored for the research question; it holds {have}. Run with "
        f"priorities=('{RQ_PRIORITY}',)."
    )


def _package_problems(scenario, rounds, recommendations) -> List[str]:
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
    for rec in recommendations:
        for issue in validate_package(scenario, rec.package()):
            problems.append(f"recommendation ({rec.priority}): {issue}")
    return problems


def run_debate_rounds(
    scenario: Scenario,
    backend: Backend,
    *,
    rounds: int = 3,
    use_judge: bool = True,
    logger: Optional[RunLogger] = None,
    conditions: Optional[ConditionsSnapshot] = None,
    review: Optional[Callable[[int, RoundJudgment], RoundJudgment]] = None,
    full_information: bool = False,
) -> tuple[List[List[Argument]], List[RoundJudgment]]:
    """Run the debate. Returns (arguments per round, judgments per round).

    Every country argues before any judging happens, so no agent sees another's
    argument from the same round - otherwise whoever went last would be
    answering a moving target and the round would not be a fair comparison.
    """
    logger = logger or RunLogger()
    agents = [CountryAgent(brief=b, backend=backend,
                           revealed=scenario.opponents(b.name) if full_information else ())
              for b in scenario.countries]
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
                # stage 4: what is true right now that constrains THIS party
                conditions=render_for_prompt(conditions, agent.brief.name),
            )
            logger.log("argument", arg)
            round_args.append(arg)
        all_args.append(round_args)

        if judge is not None:
            rj = judge.judge(scenario, r, round_args, previous=prev_args)
            logger.log("judgment", rj)
            if review is not None:
                rj = review(r, rj)
            all_judgments.append(rj)

    return all_args, all_judgments


def run(
    scenario: Scenario,
    backend: Backend,
    *,
    condition: str = "B2",
    rounds: int = 3,
    log_path: Optional[Path] = None,
    logger: Optional[RunLogger] = None,
    priorities: Sequence[str] = DEFAULT_PRIORITIES,
    audience: str = AUDIENCE,
    conditions: Optional[ConditionsSnapshot] = None,
    review: Optional[JudgeReview] = None,
    full_information: bool = False,
) -> DebateRun:
    """Run one condition end to end.

    `logger` is injectable so a caller can watch the run as it happens rather
    than only reading the result: the HTTP layer passes a logger that pushes
    each event onto a queue, which is what lets the console stream a debate
    turn by turn. Left unset it behaves exactly as before.

    The run produces one recommendation per **priority** — per thing the reader
    is trying to protect. There is one reader (a policymaker on the focus
    country's team); what changes between the recommendations is the objective.
    Protecting lives and protecting public money often select different
    settlements, and putting those side by side is the comparison that matters:
    it shows the user the price of their own choice rather than hiding it.

    The debate itself runs *once*. The parties argue the same crisis whatever
    the reader is protecting, and only the advice built from it is tailored:
    re-running it per objective would let the two recommendations rest on
    different arguments, so a difference between them could no longer be
    attributed to the objective.

    `conditions` is stage 4 — what is currently true that constrains a party. It
    is a PINNED snapshot, never fetched here: every condition in a comparison
    must read the same world, and a run that fetched its own would leave B0 and
    B2 negotiating in different ones. The snapshot id is recorded on the run so
    that is checkable afterwards rather than promised.

    Note what conditions do NOT do: the frontier stays on the full option space.
    Constraints shrink what is *attainable*, not what was theoretically best, so
    the yardstick does not move when the world does — and the run can report how
    much of the available value the conditions put out of reach.
    """
    if condition not in CONDITIONS:
        raise ValueError(f"condition must be one of {CONDITIONS}, got {condition!r}")
    priorities = tuple(priorities) or DEFAULT_PRIORITIES
    logger = logger or RunLogger(log_path)
    logger.log("scenario", scenario)

    if condition == "B0":
        # One call per objective, no debate and no judge. B0 spends 2 calls against
        # B2's 11, so a B2 win is confounded with having spent more — the call
        # counts are reported with every result so that gap is visible rather
        # than argued away.
        recs = []
        for priority in priorities:
            rec = baseline_recommendation(scenario, backend,
                                          audience=audience, priority=priority)
            logger.log("recommendation", rec)
            recs.append(rec)
        return DebateRun(
            scenario_id=scenario.scenario_id, condition=condition,
            rounds=[], judgments=[], recommendations=recs,
            usage=getattr(backend, "usage", {}),
            illegal_packages=_package_problems(scenario, [], recs),
            conditions_id=conditions.snapshot_id if conditions else "",
            conditions_report=reachable_report(scenario, conditions) if conditions else {},
        )

    use_judge = condition == "B2"
    notes: List[str] = []
    stepped_in: List[int] = []

    def reviewed(r: int, rj: RoundJudgment) -> RoundJudgment:
        """Apply the human review and log any intervention, so a run that a
        person steered is never mistaken for one they only watched."""
        edited, note = review(r, rj)
        if note:
            notes.append(f"After round {r}: {note}")
        if note or edited.model_dump() != rj.model_dump():
            stepped_in.append(r)
            logger.log("intervention", {"round": r, "before": rj.model_dump(),
                                        "after": edited.model_dump(), "note": note})
        return edited

    all_args, judgments = run_debate_rounds(
        scenario, backend, rounds=rounds, use_judge=use_judge, logger=logger,
        conditions=conditions, review=reviewed if (use_judge and review) else None,
        full_information=full_information,
    )

    recommender = RecommenderAgent(backend=backend)
    recs = []
    for priority in priorities:
        if use_judge:
            rec = recommender.recommend(scenario, judgments,
                                        audience=audience, priority=priority,
                                        reviewer_note="\n".join(notes))
        else:
            rec = recommender.recommend_from_transcript(scenario, all_args,
                                                        audience=audience, priority=priority)
        logger.log("recommendation", rec)
        recs.append(rec)

    return DebateRun(
        scenario_id=scenario.scenario_id, condition=condition,
        rounds=all_args, judgments=judgments, recommendations=recs,
        usage=getattr(backend, "usage", {}),
        illegal_packages=_package_problems(scenario, all_args, recs),
        conditions_id=conditions.snapshot_id if conditions else "",
        conditions_report=reachable_report(scenario, conditions) if conditions else {},
        interventions=len(stepped_in),
    )
