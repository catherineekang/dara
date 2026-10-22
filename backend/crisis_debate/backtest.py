"""Score a recommendation against what the parties actually settled on.

This is the measure the project is asked for most often — "how close was it to
real life?" — and it is also the easiest one to report dishonestly. Three things
have to be said plainly before any number is used.

**1. Ground truth is a separate file, on purpose.**
`eval/<case>/ground_truth.json` sits beside the scenario but is never part of
it, and is read only by this module — never by `agents.py`, `pipeline.py` or
anything an agent can see. If the historical settlement lived inside the
scenario it would be one careless template change away from a prompt. A test
asserts the separation rather than trusting it.

**2. Absolute agreement is not evidence, because the menu is contaminated.**
The issue sets were authored by someone who knew how these negotiations ended,
so the option the parties actually chose is guaranteed to be on the menu. That
makes "DARA matched history on 3 of 4 issues" an artefact of the construction,
not a finding. `docs/CONTAMINATION.md` has the detail. Until issue sets are
derived from pre-cutoff documents by `extraction.py`, every absolute number this
module produces carries `contaminated: True`.

**3. The comparison between conditions is still fair.**
B0, B1 and B2 all face the same contaminated menu, so *relative* agreement —
does B2 land on the historical settlement more often than B0? — is a real
question this can answer. That is the number to report.

There is also a question worth more than agreement itself: the historical
settlement has a joint utility like any other package, and it is frequently not
a good one. Scoring it against the frontier says how much value the real parties
left on the table, and whether the rehearsal found a deal *better* than the one
that was actually struck. For a tool whose job is to rehearse a negotiation
before you walk into it, beating history is the more useful claim than matching
it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from crisis_debate.schemas import Scenario
from crisis_debate.utilities import (
    Frontier,
    Package,
    score_package,
    utility_vector,
    validate_package,
)

#: Ground truth sits beside its scenario, inside the case directory, because
#: they are two halves of one fixture: the problem and what actually happened.
#: Still read ONLY by this module — a test asserts nothing that builds a prompt
#: imports it (CONTRACT.md §8, invariant 5).
EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"
GROUND_TRUTH_DIR = EVAL_DIR


class NoGroundTruth(FileNotFoundError):
    """No recorded historical settlement for this scenario."""


@dataclass
class GroundTruth:
    """What the parties actually settled on, and how well evidenced it is."""

    scenario_id: str
    title: str
    as_of: str
    summary: str
    settlement: Package
    confidence: Dict[str, str]
    evidence: List[Dict[str, str]]
    coding_notes: List[str]

    def source_for(self, issue: str) -> Optional[Dict[str, str]]:
        for e in self.evidence:
            if e.get("issue") == issue:
                return e
        return None

    @property
    def weakly_coded(self) -> List[str]:
        """Issues coded at less than high confidence.

        Reported alongside any agreement figure: an agreement that rests on a
        medium-confidence coding is one judgement call away from a different
        number, and the reader is entitled to know which issues those are.
        """
        return sorted(i for i, c in self.confidence.items() if c != "high")


def available() -> List[str]:
    """Cases that have a recorded historical settlement."""
    if not EVAL_DIR.exists():
        return []
    return sorted(d.name for d in EVAL_DIR.iterdir()
                  if d.is_dir() and (d / "ground_truth.json").exists())


def load(scenario_id_or_name: str) -> GroundTruth:
    """Load by file stem, falling back to a scan for the scenario_id inside —
    the same lookup `cached.load_cache` uses, for the same reason: a file's name
    and the scenario_id it refers to need not match."""
    p = EVAL_DIR / scenario_id_or_name / "ground_truth.json"
    if not p.exists():
        for cand in sorted(EVAL_DIR.glob("*/ground_truth.json")):
            if json.loads(cand.read_text(encoding="utf-8")).get("scenario_id") == scenario_id_or_name:
                p = cand
                break
        else:
            raise NoGroundTruth(
                f"no recorded historical settlement for {scenario_id_or_name!r}; have {available()}"
            )
    d = json.loads(p.read_text(encoding="utf-8"))
    return GroundTruth(
        scenario_id=d["scenario_id"], title=d.get("title", ""), as_of=d.get("as_of", ""),
        summary=d.get("summary", ""), settlement=d["settlement"],
        confidence=d.get("confidence", {}), evidence=d.get("evidence", []),
        coding_notes=d.get("coding_notes", []),
    )


def has_ground_truth(scenario_id: str) -> bool:
    try:
        load(scenario_id)
        return True
    except NoGroundTruth:
        return False


def compare(scenario: Scenario, package: Package, truth: GroundTruth,
            *, frontier: Optional[Frontier] = None,
            contaminated: bool = True) -> Dict[str, Any]:
    """Score one recommended package against the historical settlement.

    Returns agreement AND the utility comparison, because they answer different
    questions and only the second one is about whether the advice was any good.
    A recommendation can disagree with history on every issue and still be the
    better deal — indeed that is the outcome this tool exists to produce.
    """
    problems = validate_package(scenario, package)
    if problems:
        return {"legal": False, "problems": problems}

    truth_problems = validate_package(scenario, truth.settlement)
    if truth_problems:
        # The historical settlement is not expressible in this issue set. That
        # is a modelling failure, not a result: the menu does not contain what
        # actually happened, so nothing can be concluded from agreement.
        return {"legal": True, "ground_truth_expressible": False,
                "problems": truth_problems}

    f = frontier or Frontier.build(scenario)
    issues = [i.name for i in scenario.issues]
    per_issue = {i: package.get(i) == truth.settlement.get(i) for i in issues}
    matched = sum(per_issue.values())

    ours = score_package(f, package)
    theirs = score_package(f, truth.settlement)
    names = [c.name for c in scenario.countries]

    return {
        "legal": True,
        "ground_truth_expressible": True,
        "as_of": truth.as_of,

        # --- agreement with history -------------------------------------
        "issue_agreement": round(matched / len(issues), 4) if issues else 0.0,
        "issues_matched": matched,
        "issues_total": len(issues),
        "per_issue": per_issue,
        "exact_match": matched == len(issues),

        # --- was the advice any GOOD, against what actually happened? ----
        "history_joint": theirs["joint_utility"],
        "history_joint_value_recovered": theirs["joint_value_recovered"],
        "history_is_pareto_optimal": theirs["is_pareto_optimal"],
        "our_joint": ours["joint_utility"],
        "our_joint_value_recovered": ours["joint_value_recovered"],
        "joint_gain_over_history": round(ours["joint_utility"] - theirs["joint_utility"], 4),
        "beat_history": ours["joint_utility"] > theirs["joint_utility"] + 1e-9,
        "per_party_gain_over_history": {
            n: round(utility_vector(scenario, package)[n]
                     - utility_vector(scenario, truth.settlement)[n], 4)
            for n in names
        },

        # --- what the reader must be told alongside the numbers ----------
        "contaminated": contaminated,
        "weakly_coded_issues": truth.weakly_coded,
        "caveat": (
            "The issue set was authored with knowledge of the outcome, so the historical "
            "settlement is on the menu by construction. Absolute agreement is therefore not "
            "evidence of predictive skill. Compare agreement ACROSS conditions instead — they "
            "face the same menu."
        ) if contaminated else "",
    }


def compare_conditions(scenario: Scenario, packages: Dict[str, Package],
                       truth: GroundTruth) -> Dict[str, Any]:
    """The comparison that survives the contamination: same menu, four arms.

    `packages` maps a condition name to the package that condition recommended.
    """
    f = Frontier.build(scenario)
    rows = {cond: compare(scenario, pkg, truth, frontier=f)
            for cond, pkg in packages.items()}
    scored = {c: r for c, r in rows.items() if r.get("ground_truth_expressible")}
    best_agreement = max((r["issue_agreement"] for r in scored.values()), default=0.0)
    return {
        "by_condition": rows,
        "best_agreement": best_agreement,
        "leaders": sorted(c for c, r in scored.items()
                          if abs(r["issue_agreement"] - best_agreement) < 1e-9),
        "beat_history": sorted(c for c, r in scored.items() if r["beat_history"]),
        "note": (
            "Agreement differences between conditions are interpretable; the absolute "
            "level is not, while the issue set carries hindsight."
        ),
    }
