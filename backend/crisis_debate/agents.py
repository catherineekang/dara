"""The three agent roles, plus the model backend they share.

Design constraints carried over from proposal Section 4:

* One prompt template per role. The country-agent template is assembled from a
  fixed skeleton plus the brief, so two country-agents differ only by their
  brief. If the templates were allowed to diverge, the brief-diversity ablation
  would measure prompt wording instead of assigned interests.
* The recommender never receives the transcript. That information barrier is
  what makes the judge's contribution measurable (ablation B1), so it is
  enforced here by the function signature rather than by prompt instruction:
  `RecommenderAgent.recommend` simply has no parameter for arguments.
* Every call returns a validated model. A response that does not fit the schema
  raises at the boundary.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Sequence, Type, TypeVar

from pydantic import BaseModel

from collections import Counter

from crisis_debate.schemas import (
    CRITERIA,
    Argument,
    CountryBrief,
    Issue,
    Recommendation,
    RoundJudgment,
    Scenario,
)
from crisis_debate.utilities import format_valuations

T = TypeVar("T", bound=BaseModel)



class Backend(Protocol):
    """Anything that can turn (system, user, schema) into a validated model.

    Declared as a Protocol so the pipeline and the tests can run against a
    scripted fake with no SDK, no API key and no network.
    """

    def parse(self, *, system: str, user: str, output_format: Type[T],
              max_tokens: int = ...) -> T: ...


def _fmt_brief(brief: CountryBrief) -> str:
    return (
        f"Country: {brief.name}\n"
        f"Interests: {'; '.join(brief.interests)}\n"
        f"Resources: {'; '.join(brief.resources)}\n"
        f"Constraints: {'; '.join(brief.constraints)}\n"
        f"Red lines (must not be crossed): {'; '.join(brief.red_lines)}"
    )


def _fmt_issues(issues: Sequence[Issue]) -> str:
    """The settlement menu. Public: both sides know what is on the table."""
    return "\n".join(f"  {i.name}: one of {i.options}" for i in issues)


def _fmt_arguments(args: Sequence[Argument]) -> str:
    out = []
    for a in args:
        claims = "\n".join(f"    - {c.claim} (evidence: {c.evidence})" for c in a.key_claims)
        out.append(
            f"  {a.country} position: {a.position}\n"
            f"  Claims:\n{claims}\n"
            f"  Offered concessions: {'; '.join(a.concessions) or 'none'}\n"
            f"  Stated red lines: {'; '.join(a.red_lines) or 'none'}\n"
            f"  Proposed package: {', '.join(f'{k}={v}' for k, v in a.package().items()) or 'none'}"
        )
    return "\n\n".join(out)


# --------------------------------------------------------------------------
# Country agent
# --------------------------------------------------------------------------
COUNTRY_SYSTEM = """You represent one country in a multilateral crisis negotiation.

{brief}

The settlement has these negotiable issues. Every final deal picks exactly one
option per issue:
{issues}

YOUR PRIVATE VALUATIONS (the other side cannot see these, and you should not
state the numbers aloud - they are your own accounting, not a bargaining chip):
{valuations}

Importance is how much the issue is worth to you; the number beside each option
is how good that option is for you. Note that the other side's weights are
almost certainly different from yours. That is the opening: an issue you barely
care about may be the one they care about most, so trading your low-weight
issues for their low-weight issues beats splitting every issue down the middle.
Probe for what they weight heavily, and offer where it costs you least.

Argue for the outcome that best serves the interests above, within your stated
constraints, and never propose anything that crosses your red lines. You are an
advocate, not a neutral analyst: do not present a balanced overview, and do not
concede a point merely because the other side asserted it. Concede only where
the concession buys something your interests value more.

Ground every key claim in something concrete from the scenario or your brief.
If you do not have evidence for a claim, say what would be needed to establish
it rather than inventing a figure.

Every argument must commit to a concrete package: one option per issue, the deal
you are actually proposing this round. Argue for it in prose and state it."""

COUNTRY_ROUND_1 = """Crisis scenario:
{scenario}

This is round 1 of 3. State your opening position."""

COUNTRY_ROUND_N = """Crisis scenario:
{scenario}

This is round {round_index} of 3.

Opposing arguments from the previous round:
{opposing}

The judge's assessment of your own previous argument:
{own_feedback}

Trade-offs the judge recorded as unaddressed by either side:
{unaddressed}

Respond to the opposing arguments and revise your position. Address at least one
unaddressed trade-off. Do not repeat your previous argument unchanged; if you
still hold a position, defend it against what was actually said."""


@dataclass
class CountryAgent:
    brief: CountryBrief
    backend: Backend

    def argue(
        self,
        scenario: Scenario,
        round_index: int,
        previous_opposing: Optional[Sequence[Argument]] = None,
        own_feedback: Optional[str] = None,
        unaddressed: Optional[Sequence[str]] = None,
    ) -> Argument:
        system = COUNTRY_SYSTEM.format(
            brief=_fmt_brief(self.brief),
            issues=_fmt_issues(scenario.issues),
            # Own brief only. If another country's valuations ever reach this
            # string the information asymmetry is gone and the integrative
            # result is meaningless - guarded by test_private_valuations_*.
            valuations=format_valuations(self.brief, scenario.issues),
        )
        if round_index == 1:
            user = COUNTRY_ROUND_1.format(scenario=scenario.description)
        else:
            user = COUNTRY_ROUND_N.format(
                scenario=scenario.description,
                round_index=round_index,
                opposing=_fmt_arguments(previous_opposing or []),
                own_feedback=own_feedback or "(none recorded)",
                unaddressed="; ".join(unaddressed or []) or "(none recorded)",
            )
        arg = self.backend.parse(system=system, user=user, output_format=Argument)
        # The model is told which country it is, but the field is authoritative
        # for every downstream metric, so pin it rather than trust the response.
        return arg.model_copy(update={"country": self.brief.name})


# --------------------------------------------------------------------------
# Judge
# --------------------------------------------------------------------------
JUDGE_SYSTEM = """You are an independent judge in a multilateral crisis negotiation.

You represent no country, hold no brief, and are not shown any country's private
valuations - you score how the case was argued, not which side is better off. You are not choosing a winner. You
are scoring how well each side argued, on a fixed rubric, so that a separate
agent can later build a recommendation from your assessment alone.

Score every argument on each of these criteria, 1 to 5:
  feasibility              - could this actually be carried out with the stated resources?
  specificity              - concrete commitments and numbers, or vague intent?
  tradeoff_acknowledgement - does it admit what its proposal costs the other side?
  interest_consistency     - is it still arguing its own country's stated interests?
  responsiveness           - does it engage what the opponent actually said?

Every score needs a justification that cites something specific in the argument.
A score you cannot justify from the text is a score you should not give.

Also record trade-offs that NEITHER side addressed. These are fed back to both
agents, so they must be genuinely unaddressed, not merely under-argued."""

JUDGE_USER = """Crisis scenario:
{scenario}

Negotiable issues:
{issues}

Round {round_index} arguments:

{arguments}

Score each argument on the rubric and record the unaddressed trade-offs."""


@dataclass
class JudgeAgent:
    backend: Backend

    def judge(self, scenario: Scenario, round_index: int,
              arguments: Sequence[Argument]) -> RoundJudgment:
        user = JUDGE_USER.format(
            scenario=scenario.description,
            issues=_fmt_issues(scenario.issues),
            round_index=round_index,
            arguments=_fmt_arguments(arguments),
        )
        rj = self.backend.parse(system=JUDGE_SYSTEM, user=user, output_format=RoundJudgment)
        return rj.model_copy(update={"round_index": round_index})


# --------------------------------------------------------------------------
# Recommender
# --------------------------------------------------------------------------
RECOMMENDER_SYSTEM = """You advise one country's negotiating team.

You did not observe the debate. You are given an independent judge's round-by-round
assessments of it, and you build a negotiating position from those assessments.

Produce conditional options, not a single plan: each option pairs a condition the
team can actually observe at the table with the action to take when it holds.
Tag each option with the priority it serves, state the concession it requires,
and name its principal risk. An option with no cost is an option you have not
thought through.

Where the judge scored your own country's arguments poorly, treat that as a
weakness to fix, not a point to repeat.

Also commit to one opening package: exactly one option per issue, the deal the
team should table first."""

RECOMMENDER_USER_JUDGE = """Crisis scenario:
{scenario}

Negotiable issues:
{issues}

You are advising: {focus}

Judge assessments across the three rounds:
{assessments}

Produce the conditional options."""

RECOMMENDER_USER_TRANSCRIPT = """Crisis scenario:
{scenario}

Negotiable issues:
{issues}

You are advising: {focus}

Full debate transcript:
{transcript}

Produce the conditional options."""


def _fmt_judgments(judgments: Sequence[RoundJudgment]) -> str:
    out = []
    for rj in judgments:
        lines = [f"Round {rj.round_index}:"]
        for j in rj.judgments:
            scores = ", ".join(f"{s.criterion}={s.score}" for s in j.scores)
            lines.append(f"  {j.country}: {scores}")
            lines.append(f"    summary: {j.summary}")
            for s in j.scores:
                lines.append(f"    {s.criterion}: {s.justification}")
        lines.append(f"  unaddressed trade-offs: {'; '.join(rj.unaddressed_tradeoffs) or 'none'}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


@dataclass
class RecommenderAgent:
    backend: Backend

    def recommend(self, scenario: Scenario,
                  judgments: Sequence[RoundJudgment]) -> Recommendation:
        """Judge-only path (condition B2). Note there is no transcript parameter:
        the information barrier is enforced by this signature."""
        user = RECOMMENDER_USER_JUDGE.format(
            scenario=scenario.description,
            issues=_fmt_issues(scenario.issues),
            focus=scenario.focus_country,
            assessments=_fmt_judgments(judgments),
        )
        rec = self.backend.parse(system=RECOMMENDER_SYSTEM, user=user,
                                 output_format=Recommendation)
        return rec.model_copy(update={"focus_country": scenario.focus_country})

    def recommend_from_transcript(self, scenario: Scenario,
                                  rounds: Sequence[Sequence[Argument]]) -> Recommendation:
        """No-judge path (ablation B1): the recommender reads the raw debate."""
        transcript = "\n\n".join(
            f"Round {i}:\n{_fmt_arguments(args)}" for i, args in enumerate(rounds, start=1)
        )
        user = RECOMMENDER_USER_TRANSCRIPT.format(
            scenario=scenario.description,
            issues=_fmt_issues(scenario.issues),
            focus=scenario.focus_country,
            transcript=transcript,
        )
        rec = self.backend.parse(system=RECOMMENDER_SYSTEM, user=user,
                                 output_format=Recommendation)
        return rec.model_copy(update={"focus_country": scenario.focus_country})


# --------------------------------------------------------------------------
# Single-model baseline (condition B0)
# --------------------------------------------------------------------------
BASELINE_SYSTEM = """You advise one country's negotiating team on a crisis response.

Produce conditional options: each pairs a condition the team can observe with the
action to take when it holds, tagged with the priority it serves, the concession
it requires, and its principal risk. Also commit to one opening package: exactly
one option per issue."""

BASELINE_USER = """Crisis scenario:
{scenario}

Negotiable issues:
{issues}

You are advising: {focus}

Other parties to the negotiation:
{others}

Produce the conditional options."""


def baseline_recommendation(scenario: Scenario, backend: Backend) -> Recommendation:
    """B0: one call, no debate and no judge. The comparison the research
    question is stated against, so it gets the same output schema and the same
    instruction about conditional options - only the deliberation is removed."""
    others = "\n".join(_fmt_brief(b) for b in scenario.opponents(scenario.focus_country))
    user = BASELINE_USER.format(
        scenario=scenario.description, issues=_fmt_issues(scenario.issues),
        focus=scenario.focus_country, others=others,
    )
    rec = backend.parse(system=BASELINE_SYSTEM, user=user, output_format=Recommendation)
    return rec.model_copy(update={"focus_country": scenario.focus_country})


def debate_call_count(scenario: Scenario, rounds: int = 3, use_judge: bool = True) -> int:
    """Model calls the debate condition spends, so B0N can be matched to it."""
    n = len(scenario.countries) * rounds + 1          # arguments + recommender
    return n + rounds if use_judge else n


def baseline_best_of_n(scenario: Scenario, backend: Backend, n: int) -> Recommendation:
    """B0N: the compute-matched control.

    B0 spends one call against B2's ten, so a B2 win confounds *debate helped*
    with *ten calls helped*. This spends the same budget on the simplest thing
    that can absorb it - sample n independent recommendations and keep the modal
    package (self-consistency). If structured debate cannot beat drawing ten
    samples and taking the mode, the structure is not what is doing the work.

    Ties break on the sorted package string, so a tie does not silently depend
    on dict ordering or sampling order.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    drafts = [baseline_recommendation(scenario, backend) for _ in range(n)]
    keys = [tuple(sorted(d.package().items())) for d in drafts]
    counts = Counter(keys)
    best = max(counts.items(), key=lambda kv: (kv[1], str(kv[0])))[0]
    for draft, key in zip(drafts, keys):
        if key == best:
            return draft
    return drafts[0]  # unreachable while keys is non-empty
