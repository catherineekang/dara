"""Tests for the extraction layer — planner.md §16 steps 1 and 13."""
from __future__ import annotations

import pytest

from crisis_debate.extraction import (
    Document,
    ExtractedElement,
    ExtractedIssue,
    ExtractedIssueSet,
    ExtractedParty,
    build_brief,
    filter_by_cutoff,
)
from crisis_debate.provenance import Source

PARTIES = ["Singapore", "Indonesia"]


def doc(i, published, text="Officials discussed concession maps and enforcement."):
    return Document(source=Source(source_id=f"d{i}", title=f"Doc {i}", published=published),
                    text=text)


class FakeExtractor:
    """Returns well-formed extractions. `cite` controls whether elements point at
    a real document, so the unsupported path can be exercised."""

    def __init__(self, cite=True, omit_stance_for=None):
        self.cite = cite
        self.omit = omit_stance_for or []
        self.seen = []

    def parse(self, *, system, user, output_format, max_tokens=16000):
        self.seen.append(user)
        if output_format is ExtractedIssueSet:
            return ExtractedIssueSet(issues=[
                ExtractedIssue(name="concession_maps", options=["withheld", "aggregated", "full"],
                               why="in dispute", source_ids=["d1"]),
                ExtractedIssue(name="enforcement", options=["domestic", "joint", "foreign"],
                               why="in dispute", source_ids=["d1"]),
            ])
        party = "Singapore" if "Singapore" in user.split("\n")[0] else "Indonesia"
        els = [
            ExtractedElement(kind="structural", party=party, value=f"{party} structural fact",
                             source_id="d1" if self.cite else "", quote="q" if self.cite else ""),
            ExtractedElement(kind="red_line", party=party, value=f"{party} red line",
                             source_id="d1" if self.cite else "", quote="q" if self.cite else ""),
            ExtractedElement(kind="stance", party=party, issue="concession_maps",
                             value=f"{party} on maps",
                             source_id="d1" if self.cite else "", quote="q" if self.cite else ""),
        ]
        if party in self.omit:
            els = [e for e in els if e.kind != "stance"]
        return ExtractedParty(party=party, elements=els)


# --------------------------------------------------------------------------
# Cutoff discipline
# --------------------------------------------------------------------------
def test_documents_after_the_cutoff_are_dropped_and_counted():
    docs = [doc(1, "2015-06-01"), doc(2, "2016-01-06")]
    usable, after, undated = filter_by_cutoff(docs, "2015-09-01")
    assert [d.source.source_id for d in usable] == ["d1"]
    assert after == 1 and undated == 0


def test_undated_documents_are_excluded_when_a_cutoff_is_set():
    """Unknown dating must not be able to smuggle post-cutoff material in."""
    usable, after, undated = filter_by_cutoff([doc(1, "")], "2015-09-01")
    assert usable == [] and undated == 1


def test_no_cutoff_keeps_everything():
    docs = [doc(1, "2015-06-01"), doc(2, "")]
    usable, after, undated = filter_by_cutoff(docs, None)
    assert len(usable) == 2 and after == 0 and undated == 1


def test_post_cutoff_documents_never_reach_the_model():
    be = FakeExtractor()
    build_brief("s1", [doc(1, "2015-06-01"), doc(2, "2016-01-06", "BRG agency created")],
                PARTIES, be, cutoff="2015-09-01")
    assert not any("BRG agency created" in p for p in be.seen)
    assert any("Doc 1" in p for p in be.seen)


# --------------------------------------------------------------------------
# The issue set comes from documents too — the contamination fix
# --------------------------------------------------------------------------
def test_the_issue_set_is_extracted_not_supplied():
    """planner.md §13: hindsight entered through the issues, so the issues must
    be derived under the same cutoff as everything else."""
    be = FakeExtractor()
    brief, issues, _ = build_brief("s1", [doc(1, "2015-06-01")], PARTIES, be, cutoff="2015-09-01")
    assert [i.name for i in issues.issues] == ["concession_maps", "enforcement"]
    assert all(len(i.options) >= 2 for i in issues.issues)
    assert "as of 2015-09-01" in be.seen[0] or "2015-09-01" in be.seen[0]


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------
def test_cited_elements_come_back_sourced():
    brief, _, rep = build_brief("s1", [doc(1, "2015-06-01")], PARTIES, FakeExtractor(cite=True))
    sg = brief.party("Singapore")
    assert all(e.status == "sourced" and e.is_cited() for e in sg.positional.red_lines)
    assert rep.sourced > 0 and rep.unsupported == 0


def test_uncited_elements_are_kept_and_labelled_not_dropped():
    """Dropping them would make the brief look better sourced than it is."""
    brief, _, rep = build_brief("s1", [doc(1, "2015-06-01")], PARTIES, FakeExtractor(cite=False))
    sg = brief.party("Singapore")
    assert sg.positional.red_lines, "unsupported elements must survive"
    assert all(e.status == "unsupported" for e in sg.positional.red_lines)
    assert rep.unsupported > 0 and rep.sourced == 0
    assert "without citing" in sg.positional.red_lines[0].note


def test_citing_a_document_we_never_supplied_is_unsupported():
    class Hallucinates(FakeExtractor):
        def parse(self, *, system, user, output_format, max_tokens=16000):
            out = super().parse(system=system, user=user, output_format=output_format,
                                max_tokens=max_tokens)
            if isinstance(out, ExtractedParty):
                for e in out.elements:
                    e.source_id = "d999"
            return out

    brief, _, rep = build_brief("s1", [doc(1, "2015-06-01")], PARTIES, Hallucinates())
    note = brief.party("Singapore").positional.red_lines[0].note
    assert "not supplied" in note and rep.unsupported > 0


def test_an_unestablished_stance_becomes_unknown_not_a_guess():
    brief, _, rep = build_brief("s1", [doc(1, "2015-06-01")], PARTIES,
                                FakeExtractor(omit_stance_for=["Indonesia"]))
    stances = brief.party("Indonesia").positional.stances
    assert all(s.status == "unknown" for s in stances.values())
    assert "do not assume" in stances["concession_maps"].for_prompt()
    assert rep.unknown >= 2


# --------------------------------------------------------------------------
# The report is the accuracy number for the step-2 evaluation
# --------------------------------------------------------------------------
def test_report_accounts_for_every_document_and_element():
    docs = [doc(1, "2015-06-01"), doc(2, "2016-01-06"), doc(3, "")]
    brief, _, rep = build_brief("s1", docs, PARTIES, FakeExtractor(), cutoff="2015-09-01")
    assert rep.documents_in == 3
    assert rep.documents_used + rep.documents_after_cutoff + rep.documents_undated == 3
    assert rep.elements == rep.sourced + rep.unsupported
    assert 0.0 <= rep.sourced_rate <= 1.0
    assert "documents used" in rep.summary()


def test_the_brief_is_versioned_and_hashable():
    brief, _, _ = build_brief("s1", [doc(1, "2015-06-01")], PARTIES, FakeExtractor(), version=3)
    assert brief.version == 3 and len(brief.content_hash()) == 16
    assert "extracted from" in brief.note
