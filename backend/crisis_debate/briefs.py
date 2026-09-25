"""Versioned three-layer briefs (planner.md §3.1).

Each layer runs on its own clock:

    structural   geography, dependence, economy. Survives a change of government.
    positional   what the government has SAID about this negotiation. Overnight.
    situational  what is happening now that changes what a party CAN offer.

A change of administration wipes the positional layer and leaves the structural
layer standing, so the brief reports *positions unknown, confidence low* instead
of inventing a stance. No coup detector is needed: a new government is a large
positional diff.

Two properties this module has to deliver.

**Versioning.** A run pins a brief version and records its hash, so the run is
reproducible and a later diff can say exactly what changed. The hash covers
content only — not timestamps — so re-saving an unchanged brief does not look
like an edit.

**Compilation.** `BriefVersion.to_scenario()` produces the plain `Scenario` the
existing pipeline already consumes. The whole debate/judge/recommender stack is
untouched by this module; it only gains a better-sourced input.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from crisis_debate.provenance import Sourced, Source, unknown
from crisis_debate.schemas import CountryBrief, Issue, IssueValuation, Scenario

ROOT = Path(__file__).resolve().parents[1]
BRIEF_DIR = ROOT / "briefs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StructuralLayer(BaseModel):
    """Slow facts. These justify the valuation weights."""

    facts: List[Sourced] = Field(default_factory=list)
    reviewed_at: str = ""

    def values(self) -> List[str]:
        return [f.value for f in self.facts]


class PositionalLayer(BaseModel):
    """What the party has stated. These justify the red lines."""

    interests: List[Sourced] = Field(default_factory=list)
    red_lines: List[Sourced] = Field(default_factory=list)
    stances: Dict[str, Sourced] = Field(default_factory=dict)   # issue -> stance
    as_of: str = ""

    def age_days(self, now: Optional[datetime] = None) -> Optional[float]:
        if not self.as_of:
            return None
        try:
            then = datetime.fromisoformat(self.as_of)
        except ValueError:
            return None
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return ((now or datetime.now(timezone.utc)) - then).total_seconds() / 86400.0

    def is_stale(self, days: float = 30.0) -> bool:
        age = self.age_days()
        return age is None or age > days

    def wiped(self, reason: str) -> "PositionalLayer":
        """A change of administration. Interests and red lines the previous
        government stated no longer bind the new one, so they become unknown
        rather than being carried forward as if still in force."""
        return PositionalLayer(
            interests=[unknown("interests under the new administration")],
            red_lines=[unknown("red lines under the new administration")],
            stances={k: unknown(f"stance on {k}") for k in self.stances},
            as_of=_now(),
        )


class PartyBrief(BaseModel):
    name: str
    structural: StructuralLayer = Field(default_factory=StructuralLayer)
    positional: PositionalLayer = Field(default_factory=PositionalLayer)
    valuations: List[IssueValuation] = Field(default_factory=list)
    # Situational facts live on the run's conditions snapshot, not here: they
    # change per run and pinning them into the brief would make every fetch a
    # new brief version.

    def to_country_brief(self) -> CountryBrief:
        """Compile down to what the existing pipeline consumes."""
        return CountryBrief(
            name=self.name,
            interests=[s.for_prompt() for s in self.positional.interests],
            resources=self.structural.values(),
            constraints=[f.value for f in self.structural.facts if "constraint" in f.note.lower()],
            red_lines=[s.for_prompt() for s in self.positional.red_lines],
            valuations=self.valuations,
        )

    def unsourced(self) -> List[Sourced]:
        """Every element extraction could not source. Reported, never hidden."""
        out = [s for s in self.positional.interests + self.positional.red_lines
               + list(self.positional.stances.values()) + self.structural.facts
               if s.status in ("unsupported", "unknown", "projected")]
        return out


class BriefVersion(BaseModel):
    scenario_id: str
    version: int
    created_at: str = Field(default_factory=_now)
    parties: List[PartyBrief] = Field(default_factory=list)
    note: str = ""

    def party(self, name: str) -> PartyBrief:
        for p in self.parties:
            if p.name == name:
                return p
        raise KeyError(f"no brief for {name!r}")

    def content_hash(self) -> str:
        """Content only — created_at and note are excluded, so re-saving an
        unchanged brief does not register as an edit."""
        payload = json.dumps([p.model_dump() for p in self.parties],
                             sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_scenario(self, base: Scenario) -> Scenario:
        """Produce the Scenario the pipeline runs on, keeping the base scenario's
        issues and description and replacing only the briefs."""
        return base.model_copy(update={
            "countries": [self.party(c.name).to_country_brief() for c in base.countries]
        })

    # ---- storage ----
    def save(self, directory: Optional[Path] = None) -> Path:
        d = Path(directory or BRIEF_DIR) / self.scenario_id
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"v{self.version:03d}.json"
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, scenario_id: str, version: Optional[int] = None,
             directory: Optional[Path] = None) -> "BriefVersion":
        d = Path(directory or BRIEF_DIR) / scenario_id
        files = sorted(d.glob("v*.json"))
        if not files:
            raise FileNotFoundError(f"no brief versions for {scenario_id} in {d}")
        path = d / f"v{version:03d}.json" if version is not None else files[-1]
        if not path.exists():
            raise FileNotFoundError(f"brief version {version} not found for {scenario_id}")
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    @classmethod
    def latest_version(cls, scenario_id: str, directory: Optional[Path] = None) -> int:
        d = Path(directory or BRIEF_DIR) / scenario_id
        files = sorted(d.glob("v*.json"))
        return int(files[-1].stem[1:]) if files else 0


class BriefChange(BaseModel):
    party: str
    layer: str
    field: str
    before: str
    after: str

    def __str__(self) -> str:
        return f"{self.party} · {self.layer}.{self.field}: {self.before!r} -> {self.after!r}"


def diff(old: BriefVersion, new: BriefVersion) -> List[BriefChange]:
    """What changed between two versions.

    This is both the product alert — *positions moved, your opening package
    should shift* — and the experiment in planner.md §16 step 9.
    """
    changes: List[BriefChange] = []
    for np in new.parties:
        try:
            op = old.party(np.name)
        except KeyError:
            changes.append(BriefChange(party=np.name, layer="brief", field="party",
                                       before="(absent)", after="(added)"))
            continue
        for layer, o_list, n_list in (
            ("structural", op.structural.facts, np.structural.facts),
            ("positional", op.positional.interests, np.positional.interests),
            ("positional", op.positional.red_lines, np.positional.red_lines),
        ):
            o_vals = [s.value for s in o_list]
            n_vals = [s.value for s in n_list]
            for v in n_vals:
                if v not in o_vals:
                    changes.append(BriefChange(party=np.name, layer=layer, field="added",
                                               before="", after=v))
            for v in o_vals:
                if v not in n_vals:
                    changes.append(BriefChange(party=np.name, layer=layer, field="removed",
                                               before=v, after=""))
        for issue, n_stance in np.positional.stances.items():
            o_stance = op.positional.stances.get(issue)
            if o_stance is None:
                changes.append(BriefChange(party=np.name, layer="positional", field=issue,
                                           before="(absent)", after=n_stance.value))
            elif o_stance.value != n_stance.value:
                changes.append(BriefChange(party=np.name, layer="positional", field=issue,
                                           before=o_stance.value, after=n_stance.value))
        for issue in op.positional.stances:
            if issue not in np.positional.stances:
                changes.append(BriefChange(party=np.name, layer="positional", field=issue,
                                           before=op.positional.stances[issue].value,
                                           after="(removed)"))
    return changes


def administration_change(brief: BriefVersion, party: str, reason: str) -> BriefVersion:
    """A coup or change of government: wipe that party's positional layer, keep
    its structural layer, bump the version."""
    parties = []
    for p in brief.parties:
        if p.name == party:
            p = p.model_copy(update={"positional": p.positional.wiped(reason)})
        parties.append(p)
    return brief.model_copy(update={
        "version": brief.version + 1, "parties": parties,
        "created_at": _now(), "note": f"administration change — {party}: {reason}",
    })
