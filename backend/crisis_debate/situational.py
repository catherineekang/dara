"""The situational layer: what is happening now that changes what a party CAN offer.

Distinct from the positional layer. A flood in Java does not change Indonesia's
position on concession maps, and it does not change its structural dependence on
smallholder agriculture. It changes its capacity to fund anything this quarter.

Two rules from planner.md §3.3, both enforced here rather than by convention:

1. A situational fact may **constrain** the feasible set or **reweight**
   valuations. It may never invent or alter a stated position — that is the
   positional layer's job, and keeping them apart is what stops a news story
   silently overriding a documented commitment. `SituationalFact` has no field
   that could express a position, so the rule holds by construction.

2. Facts are **pinned to a run, not cached for speed**. Fetches are sub-second,
   so it is tempting to call them every run; do not. If two runs fetch at
   different moments they read different worlds, and B0, B1 and B2 stop being
   comparable. The snapshot is what makes a run reproducible.

Constraining is preferred over reweighting: it is visible in the outcome-space
chart — the frontier is unchanged, the reachable part of it shrinks — and it is
far easier to justify from a source than a numeric weight change is.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, Field

from crisis_debate.provenance import Source
from crisis_debate.schemas import Scenario

Effect = Literal["constrain", "reweight", "none"]
SNAPSHOT_STALE_HOURS = 12.0


class SituationalFact(BaseModel):
    """One current condition, with what it does to the model.

    Note what is absent: there is no `position` or `stance` field. A situational
    fact cannot express one.
    """

    party: str
    summary: str
    as_of: str                       # date the underlying condition refers to
    source: Source
    effect: Effect = "none"
    issue: str = ""                  # which issue it touches
    option: str = ""                 # constrain: which option becomes unavailable
    weight_delta: float = 0.0        # reweight: change to that issue's weight
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    accepted: bool = False           # a human accepted it (planner.md §5 Phase 2)

    def describe(self) -> str:
        if self.effect == "constrain":
            return (f"{self.summary} — removes '{self.option}' from {self.issue} "
                    f"for {self.party}")
        if self.effect == "reweight":
            return (f"{self.summary} — shifts {self.party}'s weight on {self.issue} "
                    f"by {self.weight_delta:+.2f}")
        return self.summary


class ConditionsSnapshot(BaseModel):
    """What was fetched, when, and which facts a human accepted.

    Pinned to a run. A re-run reuses it rather than fetching again, so repeated
    runs of one scenario stay comparable.
    """

    scenario_id: str
    fetched_at: str
    facts: List[SituationalFact] = Field(default_factory=list)
    sources_queried: List[str] = Field(default_factory=list)

    @property
    def snapshot_id(self) -> str:
        payload = json.dumps([f.model_dump() for f in self.facts],
                             sort_keys=True, ensure_ascii=False)
        return hashlib.sha256((self.fetched_at + payload).encode()).hexdigest()[:12]

    def accepted(self) -> List[SituationalFact]:
        """Only facts a human accepted reach the model."""
        return [f for f in self.facts if f.accepted]

    def age_hours(self, now: Optional[datetime] = None) -> Optional[float]:
        try:
            then = datetime.fromisoformat(self.fetched_at)
        except (ValueError, TypeError):
            return None
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return ((now or datetime.now(timezone.utc)) - then).total_seconds() / 3600.0

    def is_stale(self, hours: float = SNAPSHOT_STALE_HOURS) -> bool:
        age = self.age_hours()
        return age is None or age > hours

    def staleness_note(self) -> str:
        """Shown on the face of the brief (planner.md §10)."""
        age = self.age_hours()
        if age is None:
            return "conditions snapshot: age unknown"
        if age < 1:
            return "conditions snapshot: under an hour old"
        return (f"conditions snapshot: {age:.0f} hours old"
                + (" — stale" if self.is_stale() else ""))


# --------------------------------------------------------------------------
# Effects on the model
# --------------------------------------------------------------------------
def unavailable_options(snapshot: ConditionsSnapshot) -> Dict[str, List[str]]:
    """issue -> options removed from the feasible set."""
    out: Dict[str, List[str]] = {}
    for f in snapshot.accepted():
        if f.effect == "constrain" and f.issue and f.option:
            out.setdefault(f.issue, [])
            if f.option not in out[f.issue]:
                out[f.issue].append(f.option)
    return out


def feasible(scenario: Scenario, snapshot: Optional[ConditionsSnapshot]) -> List[Dict[str, str]]:
    """Every settlement still reachable once constraints apply.

    The frontier is computed on the FULL space and does not move — conditions
    shrink what is attainable, they do not change what was theoretically best.
    That distinction is the point: a run should be able to report *how much of
    the available value the constraints put out of reach*.
    """
    from crisis_debate.utilities import all_packages

    blocked = unavailable_options(snapshot) if snapshot else {}
    if not blocked:
        return all_packages(scenario)
    return [p for p in all_packages(scenario)
            if not any(p.get(issue) in opts for issue, opts in blocked.items())]


def reachable_report(scenario: Scenario, snapshot: Optional[ConditionsSnapshot]) -> Dict[str, float]:
    """How much the current conditions cost, in the units the rest of the tool uses."""
    from crisis_debate.utilities import Frontier, utility_vector

    frontier = Frontier.build(scenario)
    reach = feasible(scenario, snapshot)
    if not reach:
        return {"n_total": len(frontier.packages), "n_reachable": 0,
                "max_joint_unconstrained": round(frontier.max_joint, 4),
                "max_joint_reachable": 0.0, "value_put_out_of_reach": round(frontier.max_joint, 4)}
    best_reachable = max(sum(utility_vector(scenario, p).values()) for p in reach)
    return {
        "n_total": len(frontier.packages),
        "n_reachable": len(reach),
        "max_joint_unconstrained": round(frontier.max_joint, 4),
        "max_joint_reachable": round(best_reachable, 4),
        "value_put_out_of_reach": round(frontier.max_joint - best_reachable, 4),
    }


def apply_reweights(scenario: Scenario, snapshot: Optional[ConditionsSnapshot]) -> Scenario:
    """Shift valuation weights, renormalising so each party's weights still sum
    to 1. Use sparingly — a weight change is much harder to justify from a
    source than removing an option is."""
    if snapshot is None:
        return scenario
    deltas: Dict[str, Dict[str, float]] = {}
    for f in snapshot.accepted():
        if f.effect == "reweight" and f.issue and f.weight_delta:
            deltas.setdefault(f.party, {})
            deltas[f.party][f.issue] = deltas[f.party].get(f.issue, 0.0) + f.weight_delta
    if not deltas:
        return scenario

    countries = []
    for c in scenario.countries:
        d = deltas.get(c.name)
        if not d:
            countries.append(c)
            continue
        vals = []
        for v in c.valuations:
            w = max(0.0, min(1.0, v.weight + d.get(v.issue, 0.0)))
            vals.append(v.model_copy(update={"weight": w}))
        total = sum(v.weight for v in vals) or 1.0
        vals = [v.model_copy(update={"weight": v.weight / total}) for v in vals]
        countries.append(c.model_copy(update={"valuations": vals}))
    return scenario.model_copy(update={"countries": countries})


def render_for_prompt(snapshot: Optional[ConditionsSnapshot], party: str) -> str:
    """What a country-agent is told about current conditions affecting it."""
    if snapshot is None:
        return ""
    mine = [f for f in snapshot.accepted() if f.party == party]
    if not mine:
        return ""
    lines = "\n".join(f"  - {f.describe()} (as of {f.as_of}, {f.source.title})" for f in mine)
    return f"\nCurrent conditions affecting {party}:\n{lines}\n"
