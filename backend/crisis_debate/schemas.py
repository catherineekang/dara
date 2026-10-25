"""Schema-validated records for every stage of the pipeline.

Two properties this module has to deliver on.

**The debate is machine-readable end to end.** Every agent returns one of these
models via the SDK's structured-output support, so a malformed response fails at
the boundary rather than silently degrading a metric twenty lines later.

**Every argument commits to a concrete package.** An argument is not just prose:
it names one option per issue. That is what maps rhetoric into a point in
outcome space, and it is the only reason Pareto efficiency, Nash distance and
joint-value recovery can be computed at all (see `utilities.py`). Without it
"this argument was better" has no referent.

Two deliberate constraints:

* No optional fields and no defaults on agent-returned models. Structured
  outputs require every property to be required, and a defaulted field would let
  a model that simply omitted the field look identical to one that answered.
* `priority_tag` and the rubric criteria are `Literal`, not free strings, so the
  recommender cannot invent a category the evaluation code then silently drops.
"""
from __future__ import annotations

from typing import Dict, List, Literal

from pydantic import BaseModel, Field

from crisis_debate.provenance import Sourced

CRITERIA = (
    "feasibility",
    "specificity",
    "tradeoff_acknowledgement",
    # Paper §3(b): consistency with the agent's own previously stated position.
    # Consistency with the brief is role adherence, scored by an evaluator.
    "position_consistency",
    "responsiveness",
)
Criterion = Literal[
    "feasibility",
    "specificity",
    "tradeoff_acknowledgement",
    "position_consistency",
    "responsiveness",
]
PriorityTag = Literal["cost", "speed", "equity", "casualty_minimisation", "sovereignty"]

# Who the advice is written for. There is exactly one reader: a policymaker on
# the focus country's own negotiating team. This tool advises ONE side, so a
# mediator or multilateral reader would be advising someone we do not represent
# — an earlier `international_organisation` audience was removed for that
# reason. What varies between recommendations is the PRIORITY below, not the
# reader: same person, different thing to protect.
Audience = Literal["policymaker"]

AUDIENCE_BRIEF: dict[str, str] = {
    "policymaker": (
        "a national policymaker on {focus}'s negotiating team. They answer to a "
        "treasury, a cabinet and an electorate; they can commit money, trade "
        "concessions and be held to account for the result at home. Domestic "
        "defensibility is a real constraint, not a presentational one."
    ),
}

# What the READER is trying to protect.
#
# Deliberately NOT PriorityTag. That enum tags an individual conditional option
# ("if talks stall -> escalate [speed]"), and reusing it here conflated two
# different jobs: nobody walks into a negotiation trying to maximise
# "sovereignty" — that is a constraint they operate under, not an objective.
# These are objectives, and each one should be capable of selecting a different
# settlement from the others.
ReaderPriority = Literal[
    "casualty_minimisation",
    "cost",
    "continuity",
    "speed",
    "precedent",
    "none",
]

READER_PRIORITY_BRIEF: dict[str, str] = {
    "casualty_minimisation": (
        "LIVES AND HEALTH. Minimise deaths, illness, displacement and exposure, "
        "and minimise them soonest. Treat money as an instrument, not a "
        "constraint: if spending more reduces harm, spend more and say so."
    ),
    "cost": (
        "PUBLIC MONEY. Minimise what this costs the party you advise — spending, "
        "liabilities and open-ended commitments. Prefer a cheaper settlement you "
        "can defend to a better one you cannot fund."
    ),
    "continuity": (
        "CONTINUITY. Keep essential services and supply running — staffing, food, "
        "energy, medical capacity. A settlement that is better on paper but "
        "interrupts supply for a week is worse than a thinner one that does not."
    ),
    "speed": (
        "SPEED OF AGREEMENT. Get something signed and operating before the "
        "deadline that is actually binding. Prefer a partial settlement that "
        "starts now to a complete one that starts late, and say plainly what the "
        "haste gives up."
    ),
    "precedent": (
        "PRECEDENT. Concede as little as possible that binds the party you advise "
        "in FUTURE negotiations — legal authority, oversight, or a norm that will "
        "be cited back at them. Prefer reversible commitments to binding ones, "
        "even at some cost today."
    ),
    "none": (
        "NOTHING IN PARTICULAR. No priority has been stated. Weigh the interests "
        "as the case presents them and do not optimise for any single dimension. "
        "This is the untailored reading, and it is the baseline the tailored ones "
        "are compared against — so do not quietly adopt a priority of your own."
    ),
}


# --------------------------------------------------------------------------
# The bargaining problem
# --------------------------------------------------------------------------
class Issue(BaseModel):
    """One negotiable dimension with discrete, ordered settlement levels.

    Discrete rather than continuous so the outcome space is small enough to
    enumerate exactly: with 4-5 issues at 3-4 levels the Pareto frontier is
    computed by brute force, not approximated. An approximate frontier would
    make every efficiency number arguable.
    """

    name: str
    options: List[str]


class IssueValuation(BaseModel):
    """One country's PRIVATE valuation of one issue.

    Additive utility, the standard multi-issue bargaining form: a country's
    utility for a package is the weighted sum of its value for the chosen option
    on each issue. Weights across a country's issues sum to 1, so utilities are
    comparable on [0, 1] without a second normalisation step.

    These never appear in an opponent's prompt. That asymmetry is the point:
    it gives the debate an actual job - surfacing information the other side
    does not have - instead of only posturing over a known split.
    """

    issue: str
    weight: float = Field(ge=0.0, le=1.0)
    option_values: Dict[str, float]


class CountryBrief(BaseModel):
    """The only country-specific content in the system.

    Every country-agent shares one prompt template, so a behavioural difference
    between agents is attributable to this object rather than to prompt wording.
    That is what makes the brief-diversity ablation meaningful.
    """

    name: str
    interests: List[str]
    resources: List[str]
    constraints: List[str]
    red_lines: List[str]
    valuations: List[IssueValuation]
    # Element text -> its support (paper Fig. 1 stage 1: every brief element is
    # labelled, never dropped). An element with no entry has no recorded support.
    provenance: Dict[str, Sourced] = Field(default_factory=dict)

    def label(self, element: str) -> str:
        """The element as an agent sees it: unsourced ones say so."""
        p = self.provenance.get(element)
        return p.for_prompt() if p else element

    def valuation(self, issue: str) -> IssueValuation:
        for v in self.valuations:
            if v.issue == issue:
                return v
        raise KeyError(f"{self.name} has no valuation for issue {issue!r}")


class SourceLink(BaseModel):
    """A public document the scenario's background rests on. All are shown to
    the user; agents see only those published by the case's evidence cutoff
    (`agents.agent_sources`), with `facts` as the figures they may cite."""
    title: str
    publisher: str = ""
    url: str
    published: str = ""                  # ISO date; blank = never shown to agents
    facts: List[str] = Field(default_factory=list)


class Scenario(BaseModel):
    scenario_id: str
    title: str
    description: str
    focus_country: str
    issues: List[Issue]
    countries: List[CountryBrief]
    sources: List[SourceLink] = Field(default_factory=list)
    # ISO date after which no material may be used (paper Table 1). Blank means
    # none was set; a case added through the form defaults to the day it was made.
    evidence_cutoff: str = ""

    def brief(self, name: str) -> CountryBrief:
        for c in self.countries:
            if c.name == name:
                return c
        raise KeyError(f"no brief for {name!r}; have {[c.name for c in self.countries]}")

    def opponents(self, name: str) -> List[CountryBrief]:
        return [c for c in self.countries if c.name != name]

    def issue(self, name: str) -> Issue:
        for i in self.issues:
            if i.name == name:
                return i
        raise KeyError(f"no issue {name!r}; have {[i.name for i in self.issues]}")


# --------------------------------------------------------------------------
# Debate
# --------------------------------------------------------------------------
class Claim(BaseModel):
    claim: str
    evidence: str


class PackageItem(BaseModel):
    """One issue settled at one option.

    A list of these rather than a dict: structured outputs need a closed schema,
    and a free-keyed object would let the model invent issue names that the
    utility code then has to guess at.
    """

    issue: str
    option: str


class Argument(BaseModel):
    """One country's position in one round, with the package it is arguing for."""

    country: str
    position: str
    key_claims: List[Claim]
    concessions: List[str]
    red_lines: List[str]
    proposed_package: List[PackageItem]

    def package(self) -> Dict[str, str]:
        return {p.issue: p.option for p in self.proposed_package}


# --------------------------------------------------------------------------
# Judging
# --------------------------------------------------------------------------
class CriterionScore(BaseModel):
    criterion: Criterion
    score: int = Field(ge=1, le=5)
    # Required, not optional: a bare number cannot be audited, and success
    # criterion (b) is about justifiability rather than agreement.
    justification: str


class ArgumentJudgment(BaseModel):
    country: str
    scores: List[CriterionScore]
    summary: str
    # Claims the argument makes that no listed source or the scenario supports
    # (paper Table 6, fact-checking accuracy). Empty means none were found.
    unsupported_claims: List[str] = Field(default_factory=list)

    def score_map(self) -> Dict[str, int]:
        return {s.criterion: s.score for s in self.scores}

    def total(self) -> int:
        return sum(s.score for s in self.scores)


class RoundJudgment(BaseModel):
    round_index: int
    judgments: List[ArgumentJudgment]
    unaddressed_tradeoffs: List[str]

    def for_country(self, name: str) -> ArgumentJudgment:
        for j in self.judgments:
            if j.country == name:
                return j
        raise KeyError(f"judge returned no verdict for {name!r}")


# --------------------------------------------------------------------------
# Recommendation
# --------------------------------------------------------------------------
class Option(BaseModel):
    """A conditional option: 'if Country B will not compromise on X, propose Y'."""

    condition: str
    action: str
    priority_tag: PriorityTag
    requires_concession: str
    principal_risk: str


class Recommendation(BaseModel):
    """One line of advice, written for one reader with one thing to protect.

    `audience` and `priority` are what make a run produce *several* of these
    rather than one. They are not presentation: a settlement that is right when
    you are minimising deaths is frequently the wrong one when you are
    minimising cost, and the recommended_package is expected to differ between
    them. Where it does not, that is a finding — the priorities did not pull
    apart on this crisis — and the rationale should say so rather than
    manufacturing a difference.
    """

    focus_country: str
    audience: Audience
    priority: ReaderPriority
    # One line naming the trade this recommendation makes, so two of them can be
    # told apart at a glance before either is read in full.
    headline: str
    options: List[Option]
    rationale: str
    # The package the team should open with. This is what gets scored against
    # the Pareto frontier; without it the recommendation is unmeasurable prose.
    recommended_package: List[PackageItem]

    def package(self) -> Dict[str, str]:
        return {p.issue: p.option for p in self.recommended_package}


# --------------------------------------------------------------------------
# Whole run
# --------------------------------------------------------------------------
class DebateRun(BaseModel):
    scenario_id: str
    condition: str
    rounds: List[List[Argument]]
    judgments: List[RoundJudgment]
    # One per requested priority. A list rather than a single field because the
    # intended users — national policymakers and multilateral officials — arrive
    # with different things to protect, and a single answer forces the choice on
    # them silently.
    recommendations: List[Recommendation]
    usage: Dict[str, int]
    # Packages the agents proposed that were not legal settlements. Recorded
    # rather than coerced: silently snapping an illegal option to the nearest
    # legal one would corrupt every efficiency number downstream.
    illegal_packages: List[str]

    #: Which pinned conditions snapshot this run read (stage 4). Empty when the
    #: run had none. Recorded so that two runs being compared can be CHECKED to
    #: have read the same world, rather than assumed to have.
    conditions_id: str = ""
    # Rounds where a human reviewer edited the judgment or left a note.
    interventions: int = 0
    #: What those conditions put out of reach: n_reachable of n_total
    #: settlements, and how much joint value became unattainable.
    conditions_report: Dict[str, float] = Field(default_factory=dict)

    @property
    def recommendation(self) -> Recommendation:
        """The first recommendation.

        Kept so that metrics and reports written against a single-recommendation
        run still resolve, and for the interactive path where the first reader
        drives the board.

        **Never use this for a between-condition comparison.** It is list order,
        not a decision. `pipeline.rq_recommendation(run)` names the declared
        (reader, objective) pair and raises if the run does not hold it — that
        is what a reported number must be built on (CONTRACT.md §14).
        """
        return self.recommendations[0]
