"""Contamination check — does the model already know how these negotiations ended?

Both cases are historical. That is what makes a backtest possible, and it is also
what threatens it: a model that has read about the 2015 haze crisis can recite the
outcome instead of reasoning to it, and a backtest would then measure recall.

Four probes, in increasing cost. The first needs no API key and runs against
transcripts already on disk; the other three need a live model.

    P0  leak scan        Does agent output name events dated AFTER the evidence
                         cutoff that were never supplied to it? Entirely local.
    P1  free recall      "What happened in these talks?" If it can say, it knows.
    P2  cued prediction  Give it the pre-cutoff brief and see whether it volunteers
                         post-cutoff facts unprompted.
    P3  discrimination   Real outcome against a plausible fabricated one. Scoring
                         above chance is recall, not inference.

P0 is the sharpest of the four despite being the cheapest, because it tests the
*system as built* rather than the model in the abstract. A probe can show a model
knows something; only P0 shows that knowledge reaching the pipeline's output.

The distinction P0 turns on: a marker that appears in the material we supplied is
not a leak, it is the agent using its brief. Only markers absent from every input
and present in the output count.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "cached"


@dataclass(frozen=True)
class Marker:
    """An event datable to after the cutoff, with the patterns that name it."""

    label: str
    dated: str
    patterns: Sequence[str]

    def found_in(self, text: str) -> List[str]:
        hits = []
        for pat in self.patterns:
            if re.search(pat, text, flags=re.IGNORECASE):
                hits.append(pat)
        return hits


# Cutoffs chosen to sit before the decisive moves, so the backtest has something
# to predict. Markers are events a source published before the cutoff could not
# have reported.
CUTOFFS: Dict[str, str] = {"haze_real_v1": "2015-09-01",
            "covid_my_2020_v1": "2020-03-16"}

MARKERS: Dict[str, List[Marker]] = {
    "haze_real_v1": [
        Marker("Peatland Restoration Agency (BRG)", "2016-01-06",
               [r"\bBRG\b", r"peatland restoration agency", r"badan restorasi gambut"]),
        Marker("Presidential Regulation 1/2016", "2016-01-06",
               [r"presidential regulation 1/2016", r"perpres 1/2016"]),
        Marker("Indonesian peatland moratorium announcement", "2015-11",
               [r"moratorium (was |were )?(announced|declared|imposed)",
                r"announced (a |the )?moratorium"]),
        Marker("Paris Agreement", "2015-12-12", [r"paris agreement", r"\bCOP\s?21\b"]),
        Marker("post-cutoff year references", "2016+", [r"\b201[6-9]\b", r"\b202\d\b"]),
    ],
    "covid_my_2020_v1": [
        Marker("Periodic Commuting Arrangement", "2020-08",
               [r"periodic commuting arrangement", r"\bPCA\b"]),
        Marker("Reciprocal Green Lane", "2020-07",
               [r"reciprocal green lane", r"\bRGL\b", r"green lane"]),
        Marker("Vaccinated Travel Lane", "2021-11",
               [r"vaccinated travel lane", r"\bVTL\b", r"travel lane"]),
        Marker("Vaccination-era measures", "2021+",
               [r"vaccinat", r"\bbooster\b"]),
        Marker("post-cutoff year references", "2021+", [r"\b202[1-9]\b"]),
    ],
}


@dataclass
class LeakFinding:
    marker: str
    dated: str
    where: str
    pattern: str
    excerpt: str
    supplied: bool  # present in material we gave the agent -> not a leak


@dataclass
class LeakReport:
    scenario_id: str
    cutoff: str
    findings: List[LeakFinding] = field(default_factory=list)
    n_outputs: int = 0

    @property
    def leaks(self) -> List[LeakFinding]:
        return [f for f in self.findings if not f.supplied]

    @property
    def verdict(self) -> str:
        if not self.leaks:
            return "no post-cutoff leakage detected in agent output"
        return f"{len(self.leaks)} post-cutoff reference(s) in agent output"


def _excerpt(text: str, pattern: str, width: int = 70) -> str:
    m = re.search(pattern, text, flags=re.IGNORECASE)
    if not m:
        return ""
    a = max(0, m.start() - width // 2)
    return ("…" if a else "") + " ".join(text[a:m.end() + width // 2].split()) + "…"


def agent_outputs(cache: dict) -> List[tuple]:
    """Every span of text an agent produced. Scenario context and briefs are NOT
    here — they are inputs, and a marker occurring in them is supplied, not recalled."""
    out: List[tuple] = []
    for r, turns in enumerate(cache.get("rounds", []), start=1):
        for t in turns:
            who = t.get("country", "?")
            out.append((f"round {r} · {who} · position", t.get("position", "")))
            out.append((f"round {r} · {who} · concession", t.get("concession", "")))
    for j in cache.get("judge", []):
        r = j.get("round_index", "?")
        for jd in j.get("judgments", []):
            out.append((f"round {r} · judge on {jd.get('country','?')}", jd.get("summary", "")))
        for u in j.get("unaddressed", []):
            out.append((f"round {r} · judge · unaddressed", u))
    rec = cache.get("recommendation", {})
    out.append(("recommendation · rationale", rec.get("rationale", "")))
    for o in rec.get("options", []):
        out.append(("recommendation · option", f"{o.get('condition','')} {o.get('action','')}"))
    base = cache.get("baseline", {})
    out.append(("baseline · rationale", base.get("rationale", "")))
    for o in base.get("options", []):
        out.append(("baseline · option", f"{o.get('condition','')} {o.get('action','')}"))
    return [(w, t) for w, t in out if t]


def scan(scenario_id: str, cache: dict, supplied_text: str = "") -> LeakReport:
    """P0. Flag post-cutoff markers in agent output that were never supplied."""
    rep = LeakReport(scenario_id=scenario_id, cutoff=CUTOFFS.get(scenario_id, "unset"))
    outputs = agent_outputs(cache)
    rep.n_outputs = len(outputs)
    for marker in MARKERS.get(scenario_id, []):
        supplied_hits = bool(marker.found_in(supplied_text)) if supplied_text else False
        for where, text in outputs:
            for pat in marker.found_in(text):
                rep.findings.append(LeakFinding(
                    marker=marker.label, dated=marker.dated, where=where, pattern=pat,
                    excerpt=_excerpt(text, pat), supplied=supplied_hits))
    return rep


# ---------------------------------------------------------------------------
# P1-P3 need a live model. Prompts are defined here so the check is reviewable
# before anyone spends a token on it.
# ---------------------------------------------------------------------------
FREE_RECALL = {
    "covid_my_2020_v1": "In March 2020, when Malaysia's Movement Control Order took effect, "
                        "what did Singapore and Malaysia actually agree about cross-border "
                        "workers, who paid for their accommodation, and whether goods kept "
                        "moving? Name specific measures and their dates. If you do not know, say so.",
    "haze_real_v1": "In the 2015-16 transboundary haze dispute between Singapore and "
                    "Indonesia, what did each government actually do about concession "
                    "map disclosure, enforcement jurisdiction, and peatland policy? "
                    "Name specific measures and their dates. If you do not know, say so.",
}

DISCRIMINATION = {
    "covid_my_2020_v1": ("Which of these happened after Malaysia's March 2020 Movement Control Order?\n"
                         "A. Cross-border commuting continued daily under a screening exemption.\n"
                         "B. Malaysian workers remained in Singapore, with accommodation support.\n"
                         "Answer A or B, then state your confidence 0-1.", "B"),
    "haze_real_v1": ("Which of these happened in Indonesia in January 2016?\n"
                     "A. A Peatland Restoration Agency was established by presidential regulation.\n"
                     "B. Concession maps were released in full to the ASEAN Haze Monitoring System.\n"
                     "Answer A or B, then state your confidence 0-1.", "A"),
}


def cued_prediction_prompt(scenario_text: str, cutoff: str) -> str:
    """P2. Anything post-cutoff in the answer was volunteered, not supplied."""
    return (f"The following describes a negotiation as of {cutoff}. Using ONLY what is "
            f"stated and general knowledge available before {cutoff}, predict what each "
            f"party will refuse and what it will concede.\n\n{scenario_text}")


def load(scenario_id: str) -> dict:
    return json.loads((CACHE_DIR / f"{scenario_id.replace('_v1','')}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# P0b — issue-set hindsight audit
#
# P0 asks whether the AGENT named something it could not have known. This asks
# the prior question: whether the SCENARIO AUTHOR did. An issue option datable
# to after the cutoff means the choice architecture already contains part of the
# answer, and the agents are then picking from a menu written with hindsight.
# P0 cannot see this, because such a term is "supplied" by construction.
# ---------------------------------------------------------------------------
POST_CUTOFF_OPTIONS: Dict[str, Dict[str, str]] = {
    "covid_my_2020_v1": {
        "stay_in_singapore_with_support": "The arrangement under which Malaysian workers "
            "remained in Singapore with employer and state support is what actually emerged "
            "from these talks. Offering it as one of three options presumes the outcome. "
            "Hand-authored, not extracted — the same flaw the haze issue set has.",
    },
    "haze_real_v1": {
        "peatland_moratorium": "The moratorium was announced in late 2015 and the BRG "
                               "agency created in January 2016 — both after the cutoff. "
                               "Its presence as a top-level issue presumes the outcome.",
    },
}


def audit_issue_set(scenario_id: str, issue_options: Sequence[str]) -> List[tuple]:
    """Return (option, why) for options datable to after the cutoff."""
    flags = POST_CUTOFF_OPTIONS.get(scenario_id, {})
    return [(opt, why) for opt, why in flags.items()
            if any(opt == o or opt in o for o in issue_options)]
