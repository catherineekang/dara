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

CRITERIA = (
    "feasibility",
    "specificity",
    "tradeoff_acknowledgement",
    "interest_consistency",
    "responsiveness",
)
Criterion = Literal[
    "feasibility",
    "specificity",
    "tradeoff_acknowledgement",
    "interest_consistency",
    "responsiveness",
]
PriorityTag = Literal["cost", "speed", "equity", "casualty_minimisation", "sovereignty"]


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

    def valuation(self, issue: str) -> IssueValuation:
        for v in self.valuations:
            if v.issue == issue:
                return v
        raise KeyError(f"{self.name} has no valuation for issue {issue!r}")


class Scenario(BaseModel):
    scenario_id: str
    title: str
    description: str
    focus_country: str
    issues: List[Issue]
    countries: List[CountryBrief]

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
    focus_country: str
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
    recommendation: Recommendation
    usage: Dict[str, int]
    # Packages the agents proposed that were not legal settlements. Recorded
    # rather than coerced: silently snapping an illegal option to the nearest
    # legal one would corrupt every efficiency number downstream.
    illegal_packages: List[str]
