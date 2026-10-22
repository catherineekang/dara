"""Provenance: every claim a user can see must be traceable to something.

planner.md §10 requires a citation button on each brief element. That is only
possible if the element carries its support from the moment extraction produces
it, so support is part of the type rather than a lookup bolted on later.

The `status` field is the honest part. An element is one of:

    sourced      a document says it, and the quote is here
    projected    inferred from stated positions on analogous issues. Used by the
                 prospective cases, where the crisis has not happened and no
                 government has taken a position on it
    unsupported  extraction produced it but could not source it. Kept and
                 labelled, never silently dropped — the agent is told it is
                 unsupported
    unknown      the evidence does not establish a value. Agents must not invent
                 one (planner.md §6.2)
    user_edited  a human corrected it. Attributed to the person, not a document

Dropping an unsupported element would make the brief look better sourced than it
is, which is the failure mode this whole layer exists to prevent.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Status = Literal["sourced", "projected", "unsupported", "unknown", "user_edited"]


class Source(BaseModel):
    """A document. `who_said_it` matters: after a change of government, whether a
    statement came from a minister, a ministry or an opposition figure decides
    whether it represents the state at all."""

    source_id: str
    title: str
    url: str = ""
    published: str = ""       # ISO date of the document itself
    retrieved_at: str = ""    # ISO datetime we fetched it
    who_said_it: str = ""

    def is_before(self, cutoff: str) -> bool:
        """For the backtest: was this available before the evidence cutoff?
        An undated source is treated as NOT before, so unknown dating can never
        smuggle post-cutoff material into a backtest."""
        return bool(self.published) and self.published <= cutoff


class Evidence(BaseModel):
    source_id: str
    quote: str


class Sourced(BaseModel):
    """A value plus its support. The unit the citation button opens."""

    value: str
    status: Status = "sourced"
    evidence: List[Evidence] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    note: str = ""            # why it is projected/unsupported, or who edited it

    def is_cited(self) -> bool:
        return bool(self.evidence)

    def for_prompt(self) -> str:
        """How the element is rendered to an agent.

        An unsupported or projected element is labelled in the prompt itself. An
        agent told a claim is unsourced can hedge; an agent shown it as fact
        cannot.
        """
        if self.status == "unknown":
            return f"{self.value} [UNKNOWN — do not assume a position]"
        if self.status in ("unsupported", "projected"):
            return f"{self.value} [{self.status.upper()} — not established by a source]"
        return self.value


def unknown(what: str) -> Sourced:
    """The refusal case. planner.md §6.2: where evidence does not establish a
    position, say so rather than letting an agent invent one."""
    return Sourced(value=f"{what}: not established by available evidence",
                   status="unknown", confidence=0.0)
