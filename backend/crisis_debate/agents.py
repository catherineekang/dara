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

from crisis_debate.contamination import CUTOFFS
from crisis_debate.schemas import (
    AUDIENCE_BRIEF,
    CRITERIA,
    READER_PRIORITY_BRIEF,
    Argument,
    CountryBrief,
    Issue,
    Recommendation,
    RoundJudgment,
    Scenario,
)
from crisis_debate.utilities import format_valuations, validate_package

T = TypeVar("T", bound=BaseModel)



class Backend(Protocol):
    """Anything that can turn (system, user, schema) into a validated model.

    Declared as a Protocol so the pipeline and the tests can run against a
    scripted fake with no SDK, no API key and no network.
    """

    def parse(self, *, system: str, user: str, output_format: Type[T],
              max_tokens: int = ..., tag: Optional[str] = ...) -> T: ...


def _fmt_brief(brief: CountryBrief) -> str:
    # Each element is rendered with its provenance label, so an agent told a
    # claim is unsourced can hedge rather than state it as fact.
    def items(xs: Sequence[str]) -> str:
        return "; ".join(brief.label(x) for x in xs)
    return (
        f"Country: {brief.name}\n"
        f"Interests: {items(brief.interests)}\n"
        f"Resources: {items(brief.resources)}\n"
        f"Constraints: {items(brief.constraints)}\n"
        f"Red lines (must not be crossed): {items(brief.red_lines)}"
    )


def _fmt_issues(issues: Sequence[Issue]) -> str:
    """The settlement menu. Public: both sides know what is on the table."""
    menu = "\n".join(f"  {i.name}: one of {i.options}" for i in issues)
    if not issues:
        return menu
    return (menu + f"\n  (In any package, write each option's name exactly as listed, "
                   f"e.g. {issues[0].name}={issues[0].options[0]}, never a sentence.)")


PACKAGE_RETRY = """

Your previous reply named options that are not on the menu:
{problems}
Reply again with the same content, but in the package give every issue exactly one
option NAME copied from the menu above (for example {example}), never a sentence."""


def _parse_with_legal_package(backend, scenario: Scenario, *, system: str, user: str,
                              output_format, **kw):
    """Ask once more when a reply names options that are not on the menu.

    A model sometimes writes a sentence ("Propose aggregated maps by district…")
    where an option name belongs, which leaves the run unscorable. One retry,
    with the problem spelled out, recovers it. The reply is never edited: if the
    retry is still illegal it is recorded as illegal, exactly as before."""
    out = backend.parse(system=system, user=user, output_format=output_format, **kw)
    problems = validate_package(scenario, out.package())
    if not problems:
        return out
    first = scenario.issues[0]
    return backend.parse(system=system, output_format=output_format, **kw,
                         user=user + PACKAGE_RETRY.format(
                             problems="\n".join(f"  - {p}" for p in problems),
                             example=f"{first.name}={first.options[0]}"))


def agent_sources(scenario: Scenario) -> List[tuple]:
    """(number, source) pairs an agent may cite. Numbers follow the scenario's
    own order, so [S2] means the same document in the prompt and on screen.
    On a case with an evidence cutoff only sources dated on or before it pass:
    a source that reports the settlement would hand the agents the answer
    (docs/CONTAMINATION.md). A source with no full date never passes there."""
    cutoff = scenario.evidence_cutoff or CUTOFFS.get(scenario.scenario_id)
    out = []
    for n, s in enumerate(scenario.sources, start=1):
        if cutoff is None or (len(s.published) == 10 and s.published <= cutoff):
            out.append((n, s))
    return out


def _fmt_sources(scenario: Scenario) -> str:
    sources = agent_sources(scenario)
    if not sources:
        return "  (none supplied: use only figures stated in the scenario)"
    lines = []
    for n, s in sources:
        lines.append(f"  [S{n}] {s.title} ({s.publisher or 'unknown publisher'}, {s.published or 'undated'})")
        lines.extend(f"      - {f}" for f in s.facts)
    return "\n".join(lines)


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

HOW TO ARGUE. You are a skilled negotiator on the defensive: protect what your
country needs before reaching for more.
  - Rebut first. Open every round after the first by naming the strongest point
    the other side made against you and answering it directly, with a figure.
  - Pre-empt. State the most damaging objection to your own proposal and answer
    it before they can raise it.
  - Defend each red line with its concrete cost to your country, not just the
    fact that it is a red line.
  - Never concede for free. Every concession is conditional: "if you accept X,
    we can move on Y".
  - Reframe where it is honest: show how your preferred option serves an interest
    the other side has stated.

EVIDENCE. Persuade with numbers, not adjectives. Each key claim carries a
concrete real-world figure (people, money, days, legal limits, dates) taken from
the scenario or the sources below, and its evidence names the source by tag,
for example "[S2] section 5(5): fines capped at S$2 million". Your private
valuation numbers above are never evidence and are never stated.
Cite only the sources listed with the scenario and only the facts listed under
them. A figure that comes from the scenario text itself is tagged [Background],
never with a source tag. Never invent a source, a link or a statistic; if a figure you need is not
there, say what would establish it.

WRITING. A policymaker who is not a specialist must be able to follow you.
Write your position as connected prose, one or two short paragraphs, never as
fragments. Link each point to the last with connectors such as "because",
"therefore", "however", "in return" and "as a result", and weave your figures
and source tags into those sentences. Write each claim's evidence as a full
sentence that explains why the claim holds. Do not use em dashes; use commas,
colons or full stops instead.
State every proposal the way a negotiator would say it: what you offer or ask,
who does it (name the agency or ministry where the scenario or sources name it),
and what problem it solves. Never write the menu's option names (the words joined
by underscores) in your prose; the exact option names belong only in the package.

Every argument must commit to a concrete package: one option per issue, the deal
you are actually proposing this round. Argue for it in prose and state it."""

COUNTRY_ROUND_1 = """Crisis scenario:
{scenario}

Sources you may cite:
{sources}
{conditions}
This is round 1 of 3. State your opening position."""

COUNTRY_ROUND_N = """Crisis scenario:
{scenario}

Sources you may cite:
{sources}
{conditions}
This is round {round_index} of 3.

Opposing arguments from the previous round:
{opposing}

Your own argument and package from the previous round:
{own_previous}

The judge's assessment of your own previous argument:
{own_feedback}

Trade-offs the judge recorded as unaddressed by either side:
{unaddressed}

Respond to the opposing arguments and revise your position. Address at least one
unaddressed trade-off.

LOOK FOR THE DEAL, NOT ONLY THE ARGUMENT:
  - Open with one sentence saying what you changed since your last round, or why
    you are holding firm and what would make you move.
  - From what the other side actually argued, name what they seem to care about
    most, and aim your offer at it.
  - Put forward at least one trade that links two issues: give ground on an issue
    that matters less to you to gain on one that matters more, as a conditional
    offer ("if you accept X, we will do Y").
  - Propose a different package from your last one, unless you explain why your
    last package is still the best one available.
Stay within the rules: facts only from the scenario and the listed sources,
options only from the menu, and never cross your red lines. Do not repeat your
previous argument unchanged."""


@dataclass
class CountryAgent:
    brief: CountryBrief
    backend: Backend
    # The information-transmission probe (paper §4.1) only: the other side's
    # valuation tables, shown openly. Empty in every real condition, where the
    # private valuations are the whole point.
    revealed: Sequence[CountryBrief] = ()

    def argue(
        self,
        scenario: Scenario,
        round_index: int,
        previous_opposing: Optional[Sequence[Argument]] = None,
        own_feedback: Optional[str] = None,
        previous_own: Optional[Argument] = None,
        unaddressed: Optional[Sequence[str]] = None,
        conditions: str = "",
    ) -> Argument:
        """`conditions` is what is true in the world right now that constrains
        THIS party — a flood, an election, a committed budget. It is rendered by
        `situational.render_for_prompt` and states facts, never positions: a
        stance belongs in the brief where it is sourced (CONTRACT.md §11)."""
        system = COUNTRY_SYSTEM.format(
            brief=_fmt_brief(self.brief),
            issues=_fmt_issues(scenario.issues),
            # Own brief only. If another country's valuations ever reach this
            # string the information asymmetry is gone and the integrative
            # result is meaningless - guarded by test_private_valuations_*.
            valuations=format_valuations(self.brief, scenario.issues),
        )
        for other in self.revealed:
            system += (f"\n\nPROBE ONLY: {other.name}'s valuations, revealed to you:\n"
                       + format_valuations(other, scenario.issues))
        if round_index == 1:
            user = COUNTRY_ROUND_1.format(scenario=scenario.description,
                                          sources=_fmt_sources(scenario),
                                          conditions=conditions)
        else:
            user = COUNTRY_ROUND_N.format(
                scenario=scenario.description,
                sources=_fmt_sources(scenario),
                conditions=conditions,
                round_index=round_index,
                opposing=_fmt_arguments(previous_opposing or []),
                own_previous=_fmt_arguments([previous_own]) if previous_own else "(none recorded)",
                own_feedback=own_feedback or "(none recorded)",
                unaddressed="; ".join(unaddressed or []) or "(none recorded)",
            )
        arg = _parse_with_legal_package(self.backend, scenario, system=system, user=user,
                                        output_format=Argument)
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
  position_consistency     - is it consistent with the position this agent stated
                             in the previous round? A change is fine when it is
                             explained as a concession or a response; an unexplained
                             reversal is not. In round 1 score it against the
                             agent's own opening statements.
  responsiveness           - does it engage what the opponent actually said?

Every score needs a justification that cites something specific in the argument.
A score you cannot justify from the text is a score you should not give.
Write summaries in plain words a policymaker can follow: describe what a side
proposed, never by the menu's option names (words joined by underscores).

Fact-check as you go. For each argument, list in unsupported_claims every factual
claim (a figure, a date, a legal provision, an event) that neither the scenario
nor the listed sources supports, including a source tag that does not contain the
fact it is cited for. Leave the list empty when every claim is supported.

Also record trade-offs that NEITHER side addressed. These are fed back to both
agents, so they must be genuinely unaddressed, not merely under-argued."""

JUDGE_USER = """Crisis scenario:
{scenario}

Sources the agents may cite:
{sources}

Negotiable issues:
{issues}

Each agent's previous-round argument (for position consistency):
{previous}

Round {round_index} arguments:

{arguments}

Score each argument on the rubric and record the unaddressed trade-offs."""


@dataclass
class JudgeAgent:
    backend: Backend

    def judge(self, scenario: Scenario, round_index: int,
              arguments: Sequence[Argument],
              previous: Optional[Sequence[Argument]] = None) -> RoundJudgment:
        user = JUDGE_USER.format(
            scenario=scenario.description,
            sources=_fmt_sources(scenario),
            previous=_fmt_arguments(previous) if previous else "(round 1: none)",
            issues=_fmt_issues(scenario.issues),
            round_index=round_index,
            arguments=_fmt_arguments(arguments),
        )
        rj = self.backend.parse(system=JUDGE_SYSTEM, user=user, output_format=RoundJudgment)
        return rj.model_copy(update={"round_index": round_index})


# --------------------------------------------------------------------------
# Recommender
# --------------------------------------------------------------------------
RECOMMENDER_SYSTEM = """You advise on the settlement of a multilateral crisis.

You did not observe the debate. You are given an independent judge's round-by-round
assessments of it, and you build a negotiating position from those assessments.

YOU ARE WRITING FOR {audience_brief}

WHAT THEY ARE TRYING TO PROTECT: {priority_brief}

That priority is the objective, not a flavour of wording. Two people reading the
same crisis with different priorities should often open with *different
settlements*, and you are writing for this one. Where the priority points to a
settlement that is worse on some other dimension, choose it anyway and name what
it costs. Where the crisis genuinely does not pull apart — where the package you
would table is the one you would table under any priority — say that plainly
instead of manufacturing a difference.

Produce conditional options, not a single plan: each option pairs a condition the
team can actually observe at the table with the action to take when it holds.
Tag each option with the priority it serves, state the concession it requires,
and name its principal risk. An option with no cost is an option you have not
thought through.

Where the judge scored the arguments poorly, treat that as a weakness to fix,
not a point to repeat.

Give a one-line headline of at most 15 words, written from the point of view of
the country you advise. It must say in plain, concrete words what that country
gives and what it gets, and the gain must serve what the reader is trying to
protect. A concession is only one your country can make: never name the other
side's concession as yours. No abstractions such as "regional cooperation" or
"balanced approach". The shape to aim for: "Give up [what we concede] to get
[what we gain], protecting [the priority]".
It should be told apart at a glance from advice written for a different priority.

WRITE FOR A BUSY MINISTER, NOT A DIPLOMAT. Be brief: the reader is rehearsing a
negotiation and should take in your advice in under a minute.
  - plain_summary: at most two short sentences: what to offer, what to ask for
    in return, and how that protects the priority.
  - At most three options. Each condition is something the other side says or
    does that the team can observe, and it must differ from the action. Each
    action, in at most 25 words, says who gives what to whom and how that serves
    the priority.
  - Name the responsible agency or ministry wherever the scenario or the sources
    name it; never invent one. Use figures from the scenario or sources where
    they exist; never invent one.
  - rationale: at most two sentences. The first says who is causing the problem
    and who should bear its cost; advise from that.
  - Never present something that costs your country as serving a priority it
    works against: spending is never "saving". Anything your country gives up
    (money, authority, information) is a conditional trade that names what it
    buys: "only if they ...".
  - Count what the crisis itself costs your country (for example health care,
    protective equipment, lost work), not only what is paid at the table.
    Spending is justified only when it avoids a larger cost, and say which.
  - Never write the menu's option names (words joined by underscores) in any
    text; say what the option does in plain words. Avoid "sovereignty",
    "regional cooperation" or "balanced approach" unless you say what they mean
    concretely in this crisis.

Also commit to one opening package: exactly one option per issue, the deal to
table first."""

RECOMMENDER_USER_JUDGE = """Crisis scenario:
{scenario}

Negotiable issues:
{issues}

You are advising: {focus}

Your client's own brief (its interests, constraints and red lines):
{client}

Judge assessments across the three rounds:
{assessments}
{offers}{reviewer}
Produce the conditional options."""

RECOMMENDER_USER_TRANSCRIPT = """Crisis scenario:
{scenario}

Negotiable issues:
{issues}

You are advising: {focus}

Your client's own brief (its interests, constraints and red lines):
{client}

Full debate transcript:
{transcript}

Produce the conditional options."""


def _fmt_offers(offers: Optional[Dict[str, Dict[str, str]]]) -> str:
    if not offers:
        return ""
    lines = "\n".join(f"  {country}: " + ", ".join(f"{k}={v}" for k, v in pkg.items())
                      for country, pkg in offers.items())
    return f"\nEach side's final offer, from the last round (packages only, no arguments):\n{lines}\n"


def _fmt_judgments(judgments: Sequence[RoundJudgment]) -> str:
    out = []
    for rj in judgments:
        lines = [f"Round {rj.round_index}:"]
        for j in rj.judgments:
            scores = ", ".join(f"{s.criterion}={s.score}" for s in j.scores)
            lines.append(f"  {j.country}: {scores}")
            lines.append(f"    summary: {j.summary}")
            if j.unsupported_claims:
                lines.append(f"    unsupported claims: {'; '.join(j.unsupported_claims)}")
            for s in j.scores:
                lines.append(f"    {s.criterion}: {s.justification}")
        lines.append(f"  unaddressed trade-offs: {'; '.join(rj.unaddressed_tradeoffs) or 'none'}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def recommender_system(scenario: Scenario, audience: str, priority: str) -> str:
    """Build the recommender's system prompt for one reader and one objective.

    One template, filled two ways — the same discipline the country agents use.
    If the tailored prompts were allowed to diverge structurally, a difference
    between two recommendations would be attributable to prompt wording rather
    than to the priority, and the whole point of producing several would go.
    """
    if audience not in AUDIENCE_BRIEF:
        raise ValueError(f"unknown audience {audience!r}; have {sorted(AUDIENCE_BRIEF)}")
    if priority not in READER_PRIORITY_BRIEF:
        raise ValueError(f"unknown priority {priority!r}; have {sorted(READER_PRIORITY_BRIEF)}")
    return RECOMMENDER_SYSTEM.format(
        audience_brief=AUDIENCE_BRIEF[audience].format(focus=scenario.focus_country),
        priority_brief=READER_PRIORITY_BRIEF[priority],
    )


@dataclass
class RecommenderAgent:
    backend: Backend

    def recommend(self, scenario: Scenario, judgments: Sequence[RoundJudgment],
                  *, audience: str = "policymaker",
                  priority: str = "casualty_minimisation",
                  reviewer_note: str = "",
                  final_offers: Optional[Dict[str, Dict[str, str]]] = None) -> Recommendation:
        """Judge-only path (condition B2). Note there is no transcript parameter:
        the information barrier is enforced by this signature.

        `final_offers` is each side's last proposed package, as option names
        only: no position, no claims, no concessions, nothing argued. Without it
        the recommender could not see the deal on the table and fell back to
        splitting every issue (first live evaluation, haze case), so the debate's
        value never reached the advice. The barrier still holds for the prose."""
        user = RECOMMENDER_USER_JUDGE.format(
            scenario=scenario.description,
            issues=_fmt_issues(scenario.issues),
            focus=scenario.focus_country,
            client=_fmt_brief(scenario.brief(scenario.focus_country)),
            assessments=_fmt_judgments(judgments),
            offers=_fmt_offers(final_offers),
            # Paper contribution 4: a human may intervene at the judge stage.
            reviewer=(f"\nNote from the human reviewer, who checked the assessments "
                      f"above:\n{reviewer_note}\n" if reviewer_note else ""),
        )
        rec = _parse_with_legal_package(
            self.backend, scenario,
            system=recommender_system(scenario, audience, priority),
            user=user, output_format=Recommendation, tag=(audience, priority))
        # audience and priority are ours, not the model's: they were the
        # instruction, so a response that reported a different one would make
        # the run unattributable.
        return rec.model_copy(update={"focus_country": scenario.focus_country,
                                      "audience": audience, "priority": priority})

    def recommend_from_transcript(self, scenario: Scenario,
                                  rounds: Sequence[Sequence[Argument]],
                                  *, audience: str = "policymaker",
                                  priority: str = "casualty_minimisation") -> Recommendation:
        """No-judge path (ablation B1): the recommender reads the raw debate."""
        transcript = "\n\n".join(
            f"Round {i}:\n{_fmt_arguments(args)}" for i, args in enumerate(rounds, start=1)
        )
        user = RECOMMENDER_USER_TRANSCRIPT.format(
            scenario=scenario.description,
            issues=_fmt_issues(scenario.issues),
            focus=scenario.focus_country,
            client=_fmt_brief(scenario.brief(scenario.focus_country)),
            transcript=transcript,
        )
        rec = _parse_with_legal_package(
            self.backend, scenario,
            system=recommender_system(scenario, audience, priority),
            user=user, output_format=Recommendation, tag=(audience, priority))
        return rec.model_copy(update={"focus_country": scenario.focus_country,
                                      "audience": audience, "priority": priority})


# --------------------------------------------------------------------------
# Single-model baseline (condition B0)
# --------------------------------------------------------------------------
BASELINE_SYSTEM = """You advise on the settlement of a multilateral crisis.

YOU ARE WRITING FOR {audience_brief}

WHAT THEY ARE TRYING TO PROTECT: {priority_brief}

Produce conditional options: each pairs a condition the team can observe with the
action to take when it holds, tagged with the priority it serves, the concession
it requires, and its principal risk. Also commit to one opening package: exactly one option per
issue.

Give a one-line headline of at most 15 words, written from the point of view of
the country you advise. It must say in plain, concrete words what that country
gives and what it gets, and the gain must serve what the reader is trying to
protect. A concession is only one your country can make: never name the other
side's concession as yours. No abstractions such as "regional cooperation" or
"balanced approach". The shape to aim for: "Give up [what we concede] to get
[what we gain], protecting [the priority]".

WRITE FOR A BUSY MINISTER, NOT A DIPLOMAT. Be brief: the reader is rehearsing a
negotiation and should take in your advice in under a minute.
  - plain_summary: at most two short sentences: what to offer, what to ask for
    in return, and how that protects the priority.
  - At most three options. Each condition is something the other side says or
    does that the team can observe, and it must differ from the action. Each
    action, in at most 25 words, says who gives what to whom and how that serves
    the priority.
  - Name the responsible agency or ministry wherever the scenario or the sources
    name it; never invent one. Use figures from the scenario or sources where
    they exist; never invent one.
  - rationale: at most two sentences. The first says who is causing the problem
    and who should bear its cost; advise from that.
  - Never present something that costs your country as serving a priority it
    works against: spending is never "saving". Anything your country gives up
    (money, authority, information) is a conditional trade that names what it
    buys: "only if they ...".
  - Count what the crisis itself costs your country (for example health care,
    protective equipment, lost work), not only what is paid at the table.
    Spending is justified only when it avoids a larger cost, and say which.
  - Never write the menu's option names (words joined by underscores) in any
    text; say what the option does in plain words. Avoid "sovereignty",
    "regional cooperation" or "balanced approach" unless you say what they mean
    concretely in this crisis.
"""

BASELINE_USER = """Crisis scenario:
{scenario}

Negotiable issues:
{issues}

You are advising: {focus}

Your client's own brief (its interests, constraints and red lines):
{client}

Other parties to the negotiation:
{others}

Public sources (the same ones the debating agents may cite):
{sources}

Produce the conditional options."""


def baseline_recommendation(scenario: Scenario, backend: Backend, *,
                            audience: str = "policymaker",
                            priority: str = "casualty_minimisation") -> Recommendation:
    """B0: one call, no debate and no judge. The comparison the research
    question is stated against, so it gets the same output schema, the same
    instruction about conditional options, and the same audience and priority
    framing - only the deliberation is removed. Tailoring the baseline too is
    what keeps the comparison about deliberation rather than about one arm
    having been told who it was writing for."""
    others = "\n".join(_fmt_brief(b) for b in scenario.opponents(scenario.focus_country))
    user = BASELINE_USER.format(
        scenario=scenario.description, issues=_fmt_issues(scenario.issues),
        focus=scenario.focus_country, others=others, sources=_fmt_sources(scenario),
        client=_fmt_brief(scenario.brief(scenario.focus_country)),
    )
    system = BASELINE_SYSTEM.format(
        audience_brief=AUDIENCE_BRIEF[audience].format(focus=scenario.focus_country),
        priority_brief=READER_PRIORITY_BRIEF[priority],
    )
    rec = _parse_with_legal_package(backend, scenario, system=system, user=user,
                                    output_format=Recommendation, tag=(audience, priority))
    return rec.model_copy(update={"focus_country": scenario.focus_country,
                                  "audience": audience, "priority": priority})


def debate_call_count(scenario: Scenario, rounds: int = 3, use_judge: bool = True,
                      recommendations: int = 1) -> int:
    """Model calls a debate condition spends, so the budget gap to B0 is
    reportable rather than invisible.

    `recommendations` is the number of readers advised: each is its own call, so
    advising both readers costs two. Every condition must be asked for the same
    number, or the call counts are not comparable.
    """
    n = len(scenario.countries) * rounds + recommendations   # arguments + recommenders
    return n + rounds if use_judge else n
