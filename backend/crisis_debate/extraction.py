"""Documents in, a sourced draft brief out (planner.md §16 step 1).

The NLP core. Three things it must do that a naive version would not:

**Extract the issue set, not only the stances.** The contamination check found
that hindsight had entered through the *issues* rather than through any agent
turn: `peatland_moratorium` was chosen as a top-level issue by an author who knew
the moratorium happened. A leak scan cannot see that, because a term written into
the issue set counts as supplied by construction. So the issues have to come out
of the documents too, under the same cutoff.

**Respect an evidence cutoff.** Documents published after the cutoff are dropped
and counted, never silently included. An undated document is treated as *after*
the cutoff — unknown dating must not be able to smuggle material into a backtest.

**Never invent.** Where the documents do not establish a position, the element
comes back `unknown`, and the agent is told so in the prompt. Elements the model
asserts without pointing at a document come back `unsupported` rather than being
dropped; hiding them would make the brief look better sourced than it is.

The model is reached through the same `Backend` protocol the agents use, so this
module is testable with a scripted backend, no key and no network.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from crisis_debate.briefs import BriefVersion, PartyBrief, PositionalLayer, StructuralLayer
from crisis_debate.provenance import Evidence, Source, Sourced, unknown


class Document(BaseModel):
    source: Source
    text: str

    def excerpt(self, limit: int = 1800) -> str:
        t = " ".join(self.text.split())
        return t if len(t) <= limit else t[:limit] + "…"


# ---- what the model is asked to return -----------------------------------
class ExtractedIssue(BaseModel):
    name: str
    options: List[str]
    why: str
    source_ids: List[str] = Field(default_factory=list)


class ExtractedIssueSet(BaseModel):
    issues: List[ExtractedIssue]


class ExtractedElement(BaseModel):
    """One brief element the model claims, with the source it points at."""

    kind: str                       # interest | red_line | structural | stance
    party: str
    value: str
    issue: str = ""                 # for stance
    source_id: str = ""
    quote: str = ""
    confidence: float = 0.5


class ExtractedParty(BaseModel):
    party: str
    elements: List[ExtractedElement]


@dataclass
class ExtractionReport:
    documents_in: int
    documents_used: int
    documents_after_cutoff: int
    documents_undated: int
    elements: int
    sourced: int
    unsupported: int
    unknown: int

    @property
    def sourced_rate(self) -> float:
        return self.sourced / self.elements if self.elements else 0.0

    def summary(self) -> str:
        return (f"{self.documents_used}/{self.documents_in} documents used "
                f"({self.documents_after_cutoff} after cutoff, {self.documents_undated} undated) · "
                f"{self.elements} elements, {self.sourced} sourced "
                f"({self.sourced_rate:.0%}), {self.unsupported} unsupported, "
                f"{self.unknown} unknown")


# ---- prompts --------------------------------------------------------------
def _docs_block(docs: Sequence[Document]) -> str:
    return "\n\n".join(
        f"[{d.source.source_id}] {d.source.title} ({d.source.published or 'undated'}"
        f"{', ' + d.source.who_said_it if d.source.who_said_it else ''})\n{d.excerpt()}"
        for d in docs)


ISSUE_PROMPT = """You are building a negotiation model from source documents.

Read the documents and identify what was ACTUALLY BEING NEGOTIATED between {parties}
as of {cutoff}. Do not include matters that were settled later or that only became
live afterwards — use only what these documents show was in dispute at the time.

For each issue give 3 discrete settlement options ordered from one side's
preference to the other's, and cite the document ids that establish it is in
dispute.

Documents:
{docs}

Reply with ONLY JSON:
{{"issues":[{{"name":"snake_case_name","options":["a","b","c"],"why":"one sentence","source_ids":["id"]}}]}}
Give 3 to 5 issues."""

PARTY_PROMPT = """Build a brief for {party} from these documents only.

Return elements of four kinds:
  structural  enduring facts — geography, dependence, economy. Slow to change.
  interest    what {party} wants from this negotiation.
  red_line    what {party} has said it will not accept.
  stance      {party}'s position on one of these issues: {issues}

Every element must cite the document id it comes from and quote the span that
establishes it. If the documents do not establish {party}'s position on an issue,
DO NOT GUESS — omit it, and it will be recorded as unknown.

Documents:
{docs}

Reply with ONLY JSON:
{{"party":"{party}","elements":[{{"kind":"structural|interest|red_line|stance","party":"{party}",
"value":"the claim","issue":"issue name if kind is stance else empty","source_id":"id",
"quote":"the span from the document","confidence":0.0}}]}}"""


# ---- the pass -------------------------------------------------------------
def filter_by_cutoff(docs: Sequence[Document], cutoff: Optional[str]
                     ) -> Tuple[List[Document], int, int]:
    """Returns (usable, dropped_after_cutoff, undated). An undated document is
    dropped when a cutoff is set: unknown dating must not be able to smuggle
    post-cutoff material into a backtest."""
    if not cutoff:
        return list(docs), 0, sum(1 for d in docs if not d.source.published)
    usable, after, undated = [], 0, 0
    for d in docs:
        if not d.source.published:
            undated += 1
        elif d.source.is_before(cutoff):
            usable.append(d)
        else:
            after += 1
    return usable, after, undated


def extract_issues(docs: Sequence[Document], parties: Sequence[str], backend,
                   cutoff: Optional[str] = None) -> ExtractedIssueSet:
    usable, _, _ = filter_by_cutoff(docs, cutoff)
    prompt = ISSUE_PROMPT.format(parties=" and ".join(parties),
                                 cutoff=cutoff or "the present",
                                 docs=_docs_block(usable))
    return backend.parse(system="You extract negotiation structure from documents.",
                         user=prompt, output_format=ExtractedIssueSet)


def extract_party(docs: Sequence[Document], party: str, issues: Sequence[str],
                  backend, cutoff: Optional[str] = None) -> ExtractedParty:
    usable, _, _ = filter_by_cutoff(docs, cutoff)
    prompt = PARTY_PROMPT.format(party=party, issues=", ".join(issues),
                                 docs=_docs_block(usable))
    return backend.parse(system="You extract one party's negotiating brief from documents.",
                         user=prompt, output_format=ExtractedParty)


def _to_sourced(el: ExtractedElement, known: Dict[str, Source]) -> Sourced:
    """An element that points at a document we actually have is `sourced`. One
    that cites nothing, or cites an id we never supplied, is `unsupported` — kept
    and labelled, because dropping it would flatter the brief."""
    if el.source_id and el.source_id in known and el.quote:
        return Sourced(value=el.value, status="sourced", confidence=el.confidence,
                       evidence=[Evidence(source_id=el.source_id, quote=el.quote)])
    note = ("cited a document that was not supplied" if el.source_id
            else "model asserted this without citing a document")
    return Sourced(value=el.value, status="unsupported",
                   confidence=min(el.confidence, 0.4), note=note)


def build_brief(scenario_id: str, docs: Sequence[Document], parties: Sequence[str],
                backend, cutoff: Optional[str] = None, version: int = 1
                ) -> Tuple[BriefVersion, ExtractedIssueSet, ExtractionReport]:
    """Full pass: documents in, a versioned sourced brief out."""
    usable, after, undated = filter_by_cutoff(docs, cutoff)
    known = {d.source.source_id: d.source for d in usable}

    issue_set = extract_issues(usable, parties, backend, cutoff)
    issue_names = [i.name for i in issue_set.issues]

    party_briefs: List[PartyBrief] = []
    n_el = n_sourced = n_unsupported = n_unknown = 0

    for party in parties:
        got = extract_party(usable, party, issue_names, backend, cutoff)
        structural, interests, red_lines = [], [], []
        stances: Dict[str, Sourced] = {}
        for el in got.elements:
            s = _to_sourced(el, known)
            n_el += 1
            n_sourced += s.status == "sourced"
            n_unsupported += s.status == "unsupported"
            if el.kind == "structural":
                structural.append(s)
            elif el.kind == "interest":
                interests.append(s)
            elif el.kind == "red_line":
                red_lines.append(s)
            elif el.kind == "stance" and el.issue:
                stances[el.issue] = s
        # an issue the documents never settle for this party is unknown, not absent
        for name in issue_names:
            if name not in stances:
                stances[name] = unknown(f"{party}'s stance on {name}")
                n_unknown += 1
        party_briefs.append(PartyBrief(
            name=party,
            structural=StructuralLayer(facts=structural),
            positional=PositionalLayer(interests=interests, red_lines=red_lines,
                                       stances=stances,
                                       as_of=cutoff or ""),
        ))

    report = ExtractionReport(documents_in=len(docs), documents_used=len(usable),
                              documents_after_cutoff=after, documents_undated=undated,
                              elements=n_el, sourced=n_sourced,
                              unsupported=n_unsupported, unknown=n_unknown)
    brief = BriefVersion(scenario_id=scenario_id, version=version, parties=party_briefs,
                         note=f"extracted from {len(usable)} documents"
                              + (f", cutoff {cutoff}" if cutoff else ""))
    return brief, issue_set, report
