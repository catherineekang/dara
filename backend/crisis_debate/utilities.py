"""Payoff structure: the ground truth the whole evaluation stands on.

Without this module "the debate produced a better outcome" has no referent and
every result reduces to whether a human liked the prose. With it, each argument
commits to a package, each package maps to a utility vector, and the questions
become arithmetic:

* Is the settlement on the Pareto frontier, or did both sides leave value unclaimed?
* How much of the available joint value did the process recover?
* Is it near the Nash bargaining solution, or lopsided?
* Did the process find *integrative* trades - log-rolling across issues that each
  side weights differently - or just split every issue down the middle?

That last question is the one the project actually cares about. Distributive
bargaining (halve everything) needs no debate. Integrative bargaining requires
discovering that I weight timeline heavily and you weight penalties heavily, so
we should trade rather than split - and that discovery has to travel through the
dialogue, because neither side is shown the other's valuations.

Utilities are additive and issues are discrete, so the outcome space is
enumerated exactly. No sampling, no approximation, no frontier that shifts
between runs.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from crisis_debate.schemas import CountryBrief, Issue, Scenario

Package = Dict[str, str]


class IllegalPackage(ValueError):
    """A package naming an unknown issue or an option that is not on offer."""


def validate_package(scenario: Scenario, package: Package) -> List[str]:
    """Return a list of problems. Empty list means the package is a legal settlement.

    Returns problems rather than raising so a single malformed agent response
    degrades one data point instead of killing a run that costs real money.
    The caller records them; nothing is silently coerced.
    """
    problems: List[str] = []
    known = {i.name: i for i in scenario.issues}
    for issue_name, option in package.items():
        if issue_name not in known:
            problems.append(f"unknown issue {issue_name!r}")
        elif option not in known[issue_name].options:
            problems.append(
                f"issue {issue_name!r}: option {option!r} not in {known[issue_name].options}"
            )
    for name in known:
        if name not in package:
            problems.append(f"issue {name!r} left unsettled")
    return problems


def utility(brief: CountryBrief, package: Package) -> float:
    """Additive utility in [0, 1]. Unsettled or illegal entries score zero for
    that issue, so an incomplete package is worth strictly less than a complete
    one - an agent cannot score well by declining to commit."""
    total = 0.0
    for v in brief.valuations:
        option = package.get(v.issue)
        if option is None:
            continue
        total += v.weight * v.option_values.get(option, 0.0)
    return total


def utility_vector(scenario: Scenario, package: Package) -> Dict[str, float]:
    return {c.name: utility(c, package) for c in scenario.countries}


def all_packages(scenario: Scenario) -> List[Package]:
    """Every legal settlement. Enumerated, not sampled: with 4-5 issues at 3-4
    levels this is a few hundred points, and an exact frontier means efficiency
    numbers are facts rather than estimates."""
    names = [i.name for i in scenario.issues]
    option_lists = [i.options for i in scenario.issues]
    return [dict(zip(names, combo)) for combo in itertools.product(*option_lists)]


def _dominates(a: Dict[str, float], b: Dict[str, float]) -> bool:
    return all(a[k] >= b[k] for k in a) and any(a[k] > b[k] for k in a)


@dataclass
class Frontier:
    """Precomputed reference points for one scenario. Build once per scenario."""

    scenario: Scenario
    packages: List[Package]
    vectors: List[Dict[str, float]]
    pareto_indices: List[int]
    max_joint: float
    max_nash: float

    @classmethod
    def build(cls, scenario: Scenario) -> "Frontier":
        packages = all_packages(scenario)
        vectors = [utility_vector(scenario, p) for p in packages]
        pareto = [
            i for i, v in enumerate(vectors)
            if not any(_dominates(other, v) for j, other in enumerate(vectors) if j != i)
        ]
        max_joint = max(sum(v.values()) for v in vectors)
        max_nash = max(_nash_product(v) for v in vectors)
        return cls(scenario, packages, vectors, pareto, max_joint, max_nash)

    def pareto_packages(self) -> List[Package]:
        return [self.packages[i] for i in self.pareto_indices]

    def is_pareto_optimal(self, package: Package) -> bool:
        v = utility_vector(self.scenario, package)
        return not any(_dominates(other, v) for other in self.vectors)


def _nash_product(vector: Dict[str, float]) -> float:
    product = 1.0
    for value in vector.values():
        product *= value
    return product


def split_the_difference(scenario: Scenario) -> Package:
    """The naive distributive settlement: every issue at its middle option.

    The reference point for whether debate bought anything. A process that
    cannot beat halving every issue has not discovered a trade, whatever its
    transcript reads like.
    """
    return {
        i.name: i.options[len(i.options) // 2] for i in scenario.issues
    }


def score_package(frontier: Frontier, package: Package) -> Dict[str, float]:
    """The outcome metrics for one settlement.

    `integrative_gain` is the headline: joint value above the split-the-difference
    baseline, as a fraction of the joint value still available above it. Positive
    means the process found trades across differently-weighted issues. Zero or
    negative means it did no better than halving everything, which is the null
    result the whole project is testing for.
    """
    scenario = frontier.scenario
    vector = utility_vector(scenario, package)
    joint = sum(vector.values())
    nash = _nash_product(vector)

    baseline = split_the_difference(scenario)
    baseline_joint = sum(utility_vector(scenario, baseline).values())
    headroom = frontier.max_joint - baseline_joint
    integrative = (joint - baseline_joint) / headroom if headroom > 1e-9 else 0.0

    values = sorted(vector.values())
    return {
        **{f"utility_{k}": round(v, 4) for k, v in vector.items()},
        "joint_utility": round(joint, 4),
        "joint_value_recovered": round(joint / frontier.max_joint, 4) if frontier.max_joint else 0.0,
        "is_pareto_optimal": float(frontier.is_pareto_optimal(package)),
        "nash_ratio": round(nash / frontier.max_nash, 4) if frontier.max_nash > 1e-12 else 0.0,
        "inequality": round(values[-1] - values[0], 4) if values else 0.0,
        "integrative_gain": round(integrative, 4),
    }


def distance_to_frontier(frontier: Frontier, package: Package) -> float:
    """Smallest joint-utility gap to any Pareto point. Zero iff on the frontier."""
    joint = sum(utility_vector(frontier.scenario, package).values())
    best = max(sum(frontier.vectors[i].values()) for i in frontier.pareto_indices)
    return round(max(0.0, best - joint), 4)


def format_valuations(brief: CountryBrief, issues: Sequence[Issue]) -> str:
    """Render one country's PRIVATE valuations for its own prompt only.

    Called exactly once per agent, with that agent's own brief. If this string
    ever reaches an opponent's prompt the information asymmetry is gone and the
    integrative-bargaining result becomes meaningless, so the call site is
    guarded by a test.
    """
    lines = []
    for issue in issues:
        try:
            v = brief.valuation(issue.name)
        except KeyError:
            continue
        ranked = sorted(v.option_values.items(), key=lambda kv: -kv[1])
        rendered = ", ".join(f"{opt} ({val:.2f})" for opt, val in ranked)
        lines.append(f"  {issue.name} (importance {v.weight:.2f}): {rendered}")
    return "\n".join(lines)
