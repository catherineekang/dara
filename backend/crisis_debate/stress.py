"""Stage 11 — how much of the advice rests on a guess.

CONTRACT.md §2: in the study set we authored both valuation tables, so the
frontier is exact. In production the user knows their own weights but is
*estimating* the opponent's, so the frontier is "the frontier under your stated
assumption about them". Every joint number inherits that guess.

This module measures the inheritance. It perturbs the opponent's weights, rebuilds
the frontier, and reports how far the conclusions move. The point is not to
produce a better number — it is to stop the tool presenting an estimate with the
same confidence as a measurement, to someone about to walk into a real room.

Nothing here calls a model. It is arithmetic over the scenario, which is why it
can run on every result for free.

Two things are deliberately NOT done:

* **The recommended package is not re-derived.** Re-running the debate under
  each perturbation would cost a full run per sample and confound the answer
  with sampling noise. We hold the recommendation fixed and ask what it is worth
  in the neighbouring worlds — which is the question a reader actually has:
  *if I am wrong about them, is this still a good opening?*
* **The focus country's own weights are never perturbed.** They are the one
  input that is known rather than estimated, so moving them would be measuring
  the wrong uncertainty.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from crisis_debate.schemas import Scenario
from crisis_debate.utilities import (
    Frontier,
    Package,
    score_package,
    split_the_difference,
    utility_vector,
    validate_package,
)

DEFAULT_SPREAD = 0.20      # ±20% on each opponent weight
DEFAULT_SAMPLES = 64


def perturb_opponent_weights(scenario: Scenario, spread: float, rng: random.Random) -> Scenario:
    """A copy of the scenario with every non-focus party's weights jittered.

    Weights are re-normalised to sum to 1 per party, because the whole utility
    scale depends on that; a perturbation that broke it would change the units
    rather than the assumption.
    """
    s = scenario.model_copy(deep=True)
    for country in s.countries:
        if country.name == s.focus_country:
            continue                                  # known, not estimated
        raw = []
        for v in country.valuations:
            factor = 1.0 + rng.uniform(-spread, spread)
            raw.append(max(1e-9, v.weight * factor))
        total = sum(raw)
        for v, w in zip(country.valuations, raw):
            v.weight = w / total
    return s


@dataclass
class StressReport:
    """What survives being wrong about the other side."""

    samples: int
    spread: float
    package: Package

    # the focus country's own view — the number that does NOT depend on the guess
    focus_country: str
    focus_utility: float

    # joint-family numbers, which do
    joint_recovered_base: float
    joint_recovered_mean: float
    joint_recovered_lo: float
    joint_recovered_hi: float

    pareto_base: bool
    pareto_rate: float
    still_beats_split_rate: float
    better_available_rate: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "samples": self.samples,
            "spread": self.spread,
            "focus_country": self.focus_country,
            "focus_utility": self.focus_utility,
            "joint_recovered_base": self.joint_recovered_base,
            "joint_recovered_mean": self.joint_recovered_mean,
            "joint_recovered_lo": self.joint_recovered_lo,
            "joint_recovered_hi": self.joint_recovered_hi,
            "pareto_base": self.pareto_base,
            "pareto_rate": self.pareto_rate,
            "still_beats_split_rate": self.still_beats_split_rate,
            "better_available_rate": self.better_available_rate,
            "verdict": self.verdict,
        }

    @property
    def verdict(self) -> str:
        """One sentence a person can act on, rather than four numbers."""
        if self.pareto_rate >= 0.9 and self.still_beats_split_rate >= 0.9:
            return (
                f"Robust. Across ±{self.spread:.0%} on {self._them()}'s weights this opening stays "
                f"efficient in {self.pareto_rate:.0%} of worlds and still beats splitting the "
                f"difference in {self.still_beats_split_rate:.0%}."
            )
        if self.still_beats_split_rate >= 0.6:
            return (
                f"Holds, with caveats. It stays efficient in only {self.pareto_rate:.0%} of worlds "
                f"within ±{self.spread:.0%}, so if your read of {self._them()} is off there is "
                f"probably a better deal on the table — worth probing at the opening."
            )
        return (
            f"Fragile. In {1 - self.still_beats_split_rate:.0%} of the worlds within ±{self.spread:.0%} "
            f"this opening does no better than splitting every issue down the middle. It rests on "
            f"your read of {self._them()} being close to right."
        )

    def _them(self) -> str:
        return "the other side"


def stress(scenario: Scenario, package: Package, *,
           spread: float = DEFAULT_SPREAD,
           samples: int = DEFAULT_SAMPLES,
           seed: Optional[int] = 0) -> StressReport:
    """How well `package` holds up if we are wrong about the opponent.

    `seed` is fixed by default so a reported band is reproducible; pass None for
    a fresh draw.
    """
    problems = validate_package(scenario, package)
    if problems:
        raise ValueError(f"cannot stress an illegal package: {problems}")

    rng = random.Random(seed)
    base_frontier = Frontier.build(scenario)
    base = score_package(base_frontier, package)

    recovered: List[float] = []
    pareto_hits = 0
    beats_split = 0
    better_available = 0

    for _ in range(samples):
        alt = perturb_opponent_weights(scenario, spread, rng)
        f = Frontier.build(alt)
        m = score_package(f, package)
        recovered.append(m["joint_value_recovered"])
        if m["is_pareto_optimal"]:
            pareto_hits += 1
        split_joint = sum(utility_vector(alt, split_the_difference(alt)).values())
        if m["joint_utility"] > split_joint + 1e-9:
            beats_split += 1
        # is there a settlement this focus country would prefer that is ALSO
        # at least as good jointly? that is a missed trade in this world
        if not m["is_pareto_optimal"]:
            better_available += 1

    recovered.sort()
    n = len(recovered)
    lo = recovered[int(0.05 * n)] if n else 0.0
    hi = recovered[min(n - 1, int(0.95 * n))] if n else 0.0

    return StressReport(
        samples=samples,
        spread=spread,
        package=dict(package),
        focus_country=scenario.focus_country,
        # the focus country's utility does not move: its own weights are known,
        # so this is the number a reader can lean on when the rest is a guess
        focus_utility=round(base["focus_utility"], 4),
        joint_recovered_base=round(base["joint_value_recovered"], 4),
        joint_recovered_mean=round(sum(recovered) / n, 4) if n else 0.0,
        joint_recovered_lo=round(lo, 4),
        joint_recovered_hi=round(hi, 4),
        pareto_base=bool(base["is_pareto_optimal"]),
        pareto_rate=round(pareto_hits / n, 4) if n else 0.0,
        still_beats_split_rate=round(beats_split / n, 4) if n else 0.0,
        better_available_rate=round(better_available / n, 4) if n else 0.0,
    )


def compare_stress(scenario: Scenario, packages: Dict[str, Package],
                   **kw: Any) -> Dict[str, Dict[str, Any]]:
    """Stress several conditions' recommendations under the same draws.

    Same `seed` for every arm, so a difference between them is the package and
    not the sample.
    """
    return {name: stress(scenario, pkg, **kw).as_dict() for name, pkg in packages.items()}
