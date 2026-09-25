"""A scripted backend, so the pipeline runs with no SDK, no API key and no network.

It returns schema-valid records rather than realistic ones. That is the point:
these tests check the wiring - that each stage receives what it should, that the
information barrier holds, that the payoff metrics fire - not that the model
argues well. Argument quality needs the real model and a human reading it.

`package_strategy` lets a test choose what the fake agents converge on, so the
outcome metrics can be exercised against known-good and known-bad settlements:

    "split"       every issue at its middle option -> integrative_gain 0.0
    "integrative" the joint-value-maximising package -> integrative_gain 1.0
    "first"       every issue at its first option (usually lopsided)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Type

from crisis_debate.schemas import (
    CRITERIA,
    Argument,
    ArgumentJudgment,
    Claim,
    CriterionScore,
    Option,
    PackageItem,
    Recommendation,
    RoundJudgment,
    Scenario,
)
from crisis_debate.utilities import (
    Frontier,
    split_the_difference,
    utility_vector,
)


def _package_for(scenario: Scenario, strategy: str) -> Dict[str, str]:
    if strategy == "split":
        return split_the_difference(scenario)
    if strategy == "first":
        return {i.name: i.options[0] for i in scenario.issues}
    if strategy == "integrative":
        frontier = Frontier.build(scenario)
        return max(frontier.pareto_packages(),
                   key=lambda p: sum(utility_vector(scenario, p).values()))
    raise ValueError(f"unknown package_strategy {strategy!r}")


@dataclass
class ScriptedBackend:
    scenario: Scenario
    echo_prompts: bool = False
    package_strategy: str = "split"
    # Every (system, user) pair, so a test can assert on what an agent was shown.
    calls: List[Dict[str, Any]] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0, "calls": 0})
    _round: int = 0

    def _items(self) -> List[PackageItem]:
        pkg = _package_for(self.scenario, self.package_strategy)
        return [PackageItem(issue=k, option=v) for k, v in pkg.items()]

    def parse(self, *, system: str, user: str, output_format: Type, max_tokens: int = 16000):
        self.calls.append({"system": system, "user": user, "schema": output_format.__name__})
        self.usage["calls"] += 1
        if self.echo_prompts:
            print(f"\n{'=' * 70}\n[{output_format.__name__}]\n--- system ---\n{system}"
                  f"\n--- user ---\n{user}")

        if output_format is Argument:
            return Argument(
                country="(unset)",
                position="scripted position",
                key_claims=[Claim(claim="scripted claim", evidence="scripted evidence")],
                concessions=["scripted concession"],
                red_lines=["scripted red line"],
                proposed_package=self._items(),
            )

        if output_format is RoundJudgment:
            self._round += 1
            return RoundJudgment(
                round_index=self._round,
                judgments=[
                    ArgumentJudgment(
                        country=c.name,
                        scores=[CriterionScore(criterion=crit, score=3,
                                               justification=f"scripted {crit}")
                                for crit in CRITERIA],
                        summary=f"scripted summary for {c.name}",
                    )
                    for c in self.scenario.countries
                ],
                unaddressed_tradeoffs=["scripted unaddressed trade-off"],
            )

        if output_format is Recommendation:
            return Recommendation(
                focus_country="(unset)",
                options=[Option(
                    condition="scripted condition",
                    action="scripted action",
                    priority_tag="cost",
                    requires_concession="scripted concession",
                    principal_risk="scripted risk",
                )],
                rationale="scripted rationale",
                recommended_package=self._items(),
            )

        raise AssertionError(f"ScriptedBackend has no script for {output_format!r}")


@dataclass
class VaryingBackend(ScriptedBackend):
    """Cycles through package strategies so best-of-N has something to select on.

    A backend that always returns the same package would make the modal-package
    selection in B0N trivially correct and the test meaningless.
    """

    cycle: tuple = ("split", "split", "first")
    _n: int = 0

    def _items(self) -> List[PackageItem]:
        strategy = self.cycle[self._n % len(self.cycle)]
        self._n += 1
        pkg = _package_for(self.scenario, strategy)
        return [PackageItem(issue=k, option=v) for k, v in pkg.items()]
