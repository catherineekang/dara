"""Imagine before you act — a world model of the other side.

Craik (1943) put the idea in one sentence: an organism that carries "a
small-scale model of external reality and of its own possible actions" can try
out alternatives in its head before committing to one. Ha & Schmidhuber's World
Models (2018) made it an agent architecture: learn a model of the environment,
then rehearse — even train — inside that "dream" instead of in the real world.

A policymaker walking into a negotiation is exactly that agent. The real
environment is the room, where a bad opening cannot be taken back. This module is
the dream:

    world model   the scenario (issues, options, your own valuations) plus an
                  ENSEMBLE of guesses about the other side's private weights.
                  Their weights are estimated, not known, so we never imagine
                  one counterpart — we imagine many plausible ones
                  (`stress.perturb_opponent_weights`).
    action        the package you open with.
    imagined      in each world: do they accept it as it stands? If not, what is
    rollout       the smallest change they would ask for? Where do you land?
    planning      try every opening in imagination and keep the ones that leave
                  you best off across the worlds — before anything is said aloud.

The response model is deliberately simple, and stated so it can be argued with:

* A party **accepts** any settlement worth at least as much to them as splitting
  every issue down the middle. Halving everything is the deal any party can
  propose and expect to defend at home, so it is the floor they will not go
  below.
* A party that does not accept **counters with the acceptable settlement closest
  to your opening** (fewest issues changed), and of those, the one best for
  THEM. Closest, because a counter-offer that rewrites everything is a walk-out,
  not a counter; best for them, because assuming the other side will be generous
  is how you get anchored. The issues they change are the useful output: they
  are the sticking points.
* **Unacceptable means unacceptable.** An option a party values at exactly zero
  (what the intake form's "unacceptable" produces) is a red line, not a low
  score: they never accept or propose a deal containing it, the planner never
  suggests you open with one of yours, and a counter that would cross your red
  line is one you would refuse. If no deal survives both sides' red lines in a
  world, that world ends in no deal, and the briefing says so.

Nothing here calls a model. The LLM debate is the expensive, high-fidelity dream
— agents arguing in prose; this is the cheap one, and it runs on every keystroke.
It is a product feature (CONTRACT.md §15.4): it never feeds the B0/B1/B2 metric.
"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from crisis_debate.schemas import Scenario
from crisis_debate.stress import DEFAULT_SAMPLES, DEFAULT_SPREAD, perturb_opponent_weights
from crisis_debate.utilities import (
    Package,
    all_packages,
    split_the_difference,
    utility,
    validate_package,
)


# --------------------------------------------------------------------------
# the world model
# --------------------------------------------------------------------------
@dataclass
class _World:
    """One plausible version of the other side, precomputed over every package."""

    acceptable: List[int]          # packages every opponent accepts
    agreeable: List[int]           # ...that also cross none of your red lines
    theirs: List[float]            # opponents' summed utility per package


@dataclass
class WorldModel:
    """The scenario, enumerated once, plus `samples` imagined counterparts.

    Built once and queried many times — `imagine` and `plan` are lookups over
    precomputed utilities, which is what makes trying every opening cheap.
    """

    scenario: Scenario
    packages: List[Package]
    mine: List[float]              # focus country's utility per package (known)
    my_ok: List[int]               # packages crossing none of your red lines
    worlds: List[_World]
    spread: float
    my_split: float
    my_best: float

    @classmethod
    def build(cls, scenario: Scenario, *, samples: int = DEFAULT_SAMPLES,
              spread: float = DEFAULT_SPREAD, seed: Optional[int] = 0) -> "WorldModel":
        rng = random.Random(seed)
        packages = all_packages(scenario)
        me = scenario.brief(scenario.focus_country)
        mine = [utility(me, p) for p in packages]
        my_ok = [i for i, p in enumerate(packages) if not red_lines_crossed(me, p)]
        split = split_the_difference(scenario)

        worlds = []
        for _ in range(samples):
            alt = perturb_opponent_weights(scenario, spread, rng)
            acceptable = set(range(len(packages)))
            theirs = [0.0] * len(packages)
            for them in alt.opponents(alt.focus_country):
                floor = utility(them, split)
                for i, p in enumerate(packages):
                    u = utility(them, p)
                    theirs[i] += u
                    if u < floor - 1e-9 or red_lines_crossed(them, p):
                        acceptable.discard(i)
            ok = set(my_ok)
            worlds.append(_World(acceptable=sorted(acceptable),
                                 agreeable=sorted(acceptable & ok), theirs=theirs))

        return cls(scenario=scenario, packages=packages, mine=mine, my_ok=my_ok, worlds=worlds,
                   spread=spread, my_split=utility(me, split), my_best=max(mine))

    def index(self, package: Package) -> int:
        return self.packages.index(package)


def red_lines_crossed(brief, package: Package) -> List[str]:
    """Issues on which `package` picks an option this party values at zero."""
    out = []
    for v in brief.valuations:
        option = package.get(v.issue)
        if option is not None and v.option_values.get(option, 0.0) <= 0.0:
            out.append(v.issue)
    return out


def _changed(a: Package, b: Package) -> List[str]:
    return [k for k in a if a[k] != b.get(k)]


def _respond(model: WorldModel, world: _World, opening: int) -> Optional[int]:
    """The settlement this counterpart arrives at, or None for no deal."""
    if opening in world.acceptable:
        return opening
    if not world.agreeable:             # nothing survives both sides' red lines
        return None
    o = model.packages[opening]
    return min(world.agreeable,
               key=lambda i: (len(_changed(o, model.packages[i])), -world.theirs[i]))


# --------------------------------------------------------------------------
# imagining one opening
# --------------------------------------------------------------------------
@dataclass
class Rehearsal:
    """What happens to one opening, across every imagined counterpart."""

    focus_country: str
    opening: Package
    filled_in: List[str]                    # issues you did not specify
    worlds: int
    spread: float
    accept_rate: float                      # they take it as it stands
    sticking_points: List[Tuple[str, float]]  # issue, share of pushbacks it appears in
    likely_counter: Optional[Package]       # most common counter, None if they accept
    expected_share: float                   # of your best possible outcome
    worst_share: float                      # the worst imagined world
    split_share: float                      # what halving everything gives you
    below_split_rate: float                 # worlds where you end up worse than halving
    no_deal_rate: float                     # worlds where no deal survives the red lines
    crosses_your_red_lines: List[str]       # issues where you open with your own red line

    def as_dict(self) -> Dict[str, object]:
        return {
            "focus_country": self.focus_country,
            "opening": self.opening,
            "filled_in": self.filled_in,
            "worlds": self.worlds,
            "spread": self.spread,
            "accept_rate": self.accept_rate,
            "sticking_points": [{"issue": i, "share": s} for i, s in self.sticking_points],
            "likely_counter": self.likely_counter,
            "expected_share": self.expected_share,
            "worst_share": self.worst_share,
            "split_share": self.split_share,
            "below_split_rate": self.below_split_rate,
            "no_deal_rate": self.no_deal_rate,
            "crosses_your_red_lines": self.crosses_your_red_lines,
            "summary": self.summary(),
        }

    def summary(self) -> List[str]:
        """The rehearsal in sentences a minister can read in thirty seconds."""
        lines = [
            f"We imagined {self.worlds} plausible versions of the other side "
            f"(their priorities varied by ±{self.spread:.0%} around your estimate).",
            f"They accept this opening as it stands in {self.accept_rate:.0%} of them.",
        ]
        if self.crosses_your_red_lines:
            lines.insert(0, "This opening includes something you marked unacceptable: "
                         + ", ".join(_pretty(i) for i in self.crosses_your_red_lines) + ".")
        if self.no_deal_rate >= 1.0:
            lines.append("No deal is possible: every settlement crosses a red line on one "
                         "side or the other. Revisit what is marked unacceptable, or add an "
                         "issue that gives them something to trade for.")
            return lines
        if self.no_deal_rate > 0:
            lines.append(f"In {self.no_deal_rate:.0%} of them nothing survives both sides' "
                         f"red lines, and talks end without a deal.")
        if self.sticking_points:
            top = ", ".join(f"{_pretty(i)} ({s:.0%})" for i, s in self.sticking_points[:3])
            lines.append(f"When they push back, it is over: {top}.")
        if self.likely_counter:
            changes = _changed(self.opening, self.likely_counter)
            asks = "; ".join(f"{_pretty(k)} → {_pretty(self.likely_counter[k])}" for k in changes)
            lines.append(f"Their most likely counter-offer asks you to move on {asks}.")
        lines.append(
            f"Where you are likely to land: {self.expected_share:.0%} of the best you could "
            f"get (worst imagined case {self.worst_share:.0%}; splitting every issue "
            f"down the middle gets you {self.split_share:.0%})."
        )
        if self.below_split_rate > 0:
            lines.append(
                f"Warning: in {self.below_split_rate:.0%} of the imagined worlds you end up "
                f"worse off than simply splitting the difference. This opening depends on "
                f"your read of them being right."
            )
        if self.filled_in:
            lines.append("Issues you did not specify were set to your own first choice: "
                         + ", ".join(_pretty(i) for i in self.filled_in) + ".")
        return lines


def _pretty(s: str) -> str:
    return s.replace("_", " ")


def _share(model: WorldModel, u: float) -> float:
    return round(u / model.my_best, 4) if model.my_best > 1e-9 else 0.0


def complete_opening(scenario: Scenario, partial: Dict[str, str]) -> Tuple[Package, List[str]]:
    """Fill unspecified issues with the focus country's own first choice.

    A policymaker asks "what if I open with full disclosure on the maps?", not a
    four-issue package. The rest is filled with what they would ask for anyway —
    an opening is an ask — and the briefing says which issues were filled.
    """
    me = scenario.brief(scenario.focus_country)
    package, filled = {}, []
    for issue in scenario.issues:
        if issue.name in partial:
            package[issue.name] = partial[issue.name]
        else:
            values = me.valuation(issue.name).option_values
            package[issue.name] = max(issue.options, key=lambda o: values.get(o, 0.0))
            filled.append(issue.name)
    problems = validate_package(scenario, package)
    if problems:
        raise ValueError("; ".join(problems))
    return package, filled


def imagine(model: WorldModel, opening: Dict[str, str]) -> Rehearsal:
    """Roll one opening forward through every imagined counterpart."""
    package, filled = complete_opening(model.scenario, opening)
    o = model.index(package)

    landed: List[float] = []
    accepted = no_deal = 0
    pushback: Counter = Counter()
    counters: Counter = Counter()
    for world in model.worlds:
        s = _respond(model, world, o)
        if s is None:
            no_deal += 1
            continue
        landed.append(model.mine[s])
        if s == o:
            accepted += 1
        else:
            pushback.update(_changed(package, model.packages[s]))
            counters[s] += 1

    n = len(model.worlds)
    rejected = n - accepted - no_deal
    deals = len(landed) or 1
    me = model.scenario.brief(model.scenario.focus_country)
    return Rehearsal(
        focus_country=model.scenario.focus_country,
        opening=package,
        filled_in=filled,
        worlds=n,
        spread=model.spread,
        accept_rate=round(accepted / n, 4),
        sticking_points=[(i, round(c / max(rejected, 1), 4)) for i, c in pushback.most_common()],
        likely_counter=model.packages[counters.most_common(1)[0][0]] if counters else None,
        expected_share=_share(model, sum(landed) / deals),
        worst_share=_share(model, min(landed, default=0.0)),
        split_share=_share(model, model.my_split),
        below_split_rate=round(sum(1 for u in landed if u < model.my_split - 1e-9) / n, 4),
        no_deal_rate=round(no_deal / n, 4),
        crosses_your_red_lines=red_lines_crossed(me, package),
    )


# --------------------------------------------------------------------------
# planning in imagination
# --------------------------------------------------------------------------
def plan(model: WorldModel, *, top: int = 3) -> List[Rehearsal]:
    """Try every opening in imagination; return the best `top`.

    Only openings that cross none of your own red lines are considered. Ranked
    first by how often a deal is reached at all, then by where you are expected
    to land when it is (to the nearest percent of your best case — finer than
    that is noise in a guessed model), then by how often they accept outright:
    two openings that end in the same place are not equal if one of them gets
    there without a fight. Openings that land identically in every world are
    collapsed, so the alternatives shown are genuinely different.

    Returns an empty list when you have no opening that respects your red lines.
    """
    scored = []
    for o in model.my_ok:
        outcome = tuple(_respond(model, w, o) for w in model.worlds)
        deals = [s for s in outcome if s is not None]
        reach = len(deals) / len(outcome)
        expected = sum(model.mine[s] for s in deals) / len(deals) if deals else 0.0
        accept = sum(1 for s in outcome if s == o) / len(outcome)
        scored.append((round(reach, 2), round(_share(model, expected), 2), accept,
                       model.mine[o], outcome, o))
    scored.sort(key=lambda t: (-t[0], -t[1], -t[2], -t[3]))

    seen, picks = set(), []
    for *_, outcome, o in scored:
        if outcome in seen:
            continue
        seen.add(outcome)
        picks.append(o)
        if len(picks) == top:
            break
    return [imagine(model, model.packages[o]) for o in picks]


def parse_opening(scenario: Scenario, text: str) -> Dict[str, str]:
    """`issue=option,issue=option` → a dict, forgiving about case and spaces.

    Errors name the valid choices, because the person typing this is a
    policymaker, not someone who has the scenario file open.
    """
    by_issue = {i.name.lower(): i for i in scenario.issues}
    out: Dict[str, str] = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        if "=" not in part and ":" not in part:
            raise ValueError(f"expected issue=option, got {part!r}")
        k, v = (x.strip().lower().replace(" ", "_") for x in part.replace(":", "=", 1).split("=", 1))
        if k not in by_issue:
            raise ValueError(f"unknown issue {k!r}; choose from {[i.name for i in scenario.issues]}")
        issue = by_issue[k]
        match = [o for o in issue.options if o.lower() == v]
        if not match:
            raise ValueError(f"{issue.name}: unknown option {v!r}; choose from {issue.options}")
        out[issue.name] = match[0]
    return out
