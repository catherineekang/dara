"""The policymaker's way in: describe a crisis in words and rankings, not numbers.

A minister cannot tell you that the other side weights enforcement at 0.35. They
can tell you that enforcement matters more to them than the money, and that a
foreign court ruling on their companies is unacceptable. This module takes
answers of that second kind and builds the same `Scenario` the bundled cases
use, so everything downstream — the frontier, `imagine`, the debate — runs on it
unchanged.

The form asks four things:

    1. who the two parties are
    2. what is on the table: each issue and its possible settlements
    3. how each side RANKS the issues — most important first (ties allowed)
    4. how each side views each option: good / acceptable / unacceptable

and turns them into numbers by two stated rules, so the conversion can be argued
with rather than hidden:

* **Ranks → weights by rank-order centroid** (Barron & Barrett, 1996): the
  standard decision-analysis way to turn an ordering into weights when nobody
  can honestly give the numbers. Rank k of n gets (1/n)·Σ_{i=k..n} 1/i; tied
  issues share the average of their positions' weights.
* **Marks → option values**: good = 1.0, acceptable = 0.5, unacceptable = 0.0.
  Every unacceptable option also becomes a red line in that party's brief, so
  the debating agent is told it in words, not only through a number.

`review()` reads the result back in sentences. That is CONTRACT.md §2's REVIEW
gate: nothing is saved until a person has seen how their answers were read.

Scenarios built here are product inputs. They are saved as `user_*` and never
join the study set, so they cannot touch the research question.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Union

from pydantic import BaseModel, Field

from crisis_debate.schemas import CountryBrief, Issue, IssueValuation, Scenario

MARKS = {"good": 1.0, "acceptable": 0.5, "unacceptable": 0.0}

#: A ranking entry is one issue name, or a list of names tied at that position.
RankEntry = Union[str, List[str]]


class PartyView(BaseModel):
    """What one side cares about. Everything but the ranking and marks is optional."""

    name: str
    ranking: List[RankEntry] = Field(description="issues, most important first; a list = a tie")
    options: Dict[str, Dict[str, str]] = Field(
        description="issue -> option -> good | acceptable | unacceptable")
    interests: List[str] = Field(default_factory=list)
    red_lines: List[str] = Field(default_factory=list)
    resources: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)


class IssueForm(BaseModel):
    name: str
    options: List[str]


class CrisisForm(BaseModel):
    """The whole form. `you` is the side being advised."""

    title: str
    situation: str = ""
    issues: List[IssueForm]
    you: PartyView
    them: PartyView


class IntakeError(ValueError):
    """A problem with the form, worded for the person who filled it in."""


# --------------------------------------------------------------------------
# conversion
# --------------------------------------------------------------------------
def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    if not s:
        raise IntakeError(f"{text!r} needs at least one letter or digit")
    return s


def roc_weights(ranking: Sequence[RankEntry]) -> Dict[str, float]:
    """Rank-order centroid weights; ties share the average of their positions."""
    flat = [[e] if isinstance(e, str) else list(e) for e in ranking]
    n = sum(len(g) for g in flat)
    centroid = [sum(1.0 / i for i in range(k, n + 1)) / n for k in range(1, n + 1)]
    out, pos = {}, 0
    for group in flat:
        share = sum(centroid[pos:pos + len(group)]) / len(group)
        for name in group:
            out[name] = share
        pos += len(group)
    return out


def _party(form: CrisisForm, view: PartyView, issues: Dict[str, Issue],
           names: Dict[str, str], labels: Dict[str, Dict[str, str]],
           role: str) -> CountryBrief:
    def issue_id(label: str) -> str:
        key = slug(label)
        if key not in issues:
            raise IntakeError(f"{role}: {label!r} is not one of the issues "
                              f"({', '.join(names[i] for i in issues)})")
        return key

    ranked = [[issue_id(e)] if isinstance(e, str) else [issue_id(x) for x in e]
              for e in view.ranking]
    seen = [i for g in ranked for i in g]
    missing = [names[i] for i in issues if i not in seen]
    doubled = sorted({names[i] for i in seen if seen.count(i) > 1})
    if missing:
        raise IntakeError(f"{role}: please rank every issue — missing {', '.join(missing)}")
    if doubled:
        raise IntakeError(f"{role}: each issue should appear once in the ranking — "
                          f"repeated {', '.join(doubled)}")
    weights = roc_weights(ranked)

    marks = {issue_id(k): v for k, v in view.options.items()}
    valuations, red_lines = [], list(view.red_lines)
    for iid, issue in issues.items():
        given = {slug(o): m.strip().lower() for o, m in marks.get(iid, {}).items()}
        values = {}
        for opt in issue.options:
            mark = given.get(opt)
            if mark is None:
                raise IntakeError(f"Mark how {view.name} sees “{names[iid]}” → "
                                  f"“{labels[iid][opt]}”.")
            if mark not in MARKS:
                raise IntakeError(f"{role}: {mark!r} should be good, acceptable or unacceptable")
            values[opt] = MARKS[mark]
            if mark == "unacceptable":
                red_lines.append(f"Will not accept {opt.replace('_', ' ')!r} "
                                 f"on {names[iid].lower()}.")
        valuations.append(IssueValuation(issue=iid, weight=weights[iid],
                                         option_values=values))

    interests = list(view.interests) or [
        f"Most important to {view.name}: {names[ranked[0][0]]}"
        + (f", then {names[ranked[1][0]]}" if len(ranked) > 1 else "") + "."
    ]
    return CountryBrief(name=view.name, interests=interests, resources=list(view.resources),
                        constraints=list(view.constraints), red_lines=red_lines,
                        valuations=valuations)


def build(form: Union[CrisisForm, Dict[str, Any]]) -> Scenario:
    """Turn the form into a Scenario. Raises IntakeError with a readable message."""
    if not isinstance(form, CrisisForm):
        try:
            form = CrisisForm.model_validate(form)
        except Exception as exc:
            raise IntakeError(f"the form is incomplete: {exc}") from exc

    if len(form.issues) < 2:
        raise IntakeError("add at least two issues — with one there is nothing to trade")
    if slug(form.you.name) == slug(form.them.name):
        raise IntakeError("the two parties need different names")

    issues: Dict[str, Issue] = {}
    names: Dict[str, str] = {}
    labels: Dict[str, Dict[str, str]] = {}     # option slug -> what the person typed
    for i in form.issues:
        iid = slug(i.name)
        if iid in issues:
            raise IntakeError(f"issue {i.name!r} is listed twice")
        opts = [slug(o) for o in i.options]
        if len(opts) < 2 or len(set(opts)) != len(opts):
            raise IntakeError(f"{i.name!r} needs at least two different options")
        issues[iid], names[iid] = Issue(name=iid, options=opts), i.name
        labels[iid] = dict(zip(opts, i.options))

    you = _party(form, form.you, issues, names, labels, "your side")
    them = _party(form, form.them, issues, names, labels, "their side")
    return Scenario(
        scenario_id=slug(form.title)[:50],
        title=form.title,
        description=form.situation or form.title,
        focus_country=you.name,
        issues=list(issues.values()),
        countries=[you, them],
    )


# --------------------------------------------------------------------------
# the review gate
# --------------------------------------------------------------------------
def review(scenario: Scenario) -> List[str]:
    """How the answers were read, in sentences, for the person to confirm."""
    lines = []
    for c in scenario.countries:
        who = "you" if c.name == scenario.focus_country else c.name
        order = sorted(c.valuations, key=lambda v: -v.weight)
        ranked = ", ".join(f"{v.issue.replace('_', ' ')} ({v.weight:.0%})" for v in order)
        lines.append(f"What matters to {who}, in order: {ranked}.")
        for v in order:
            bad = [o.replace("_", " ") for o, x in v.option_values.items() if x == 0.0]
            if bad:
                lines.append(f"  Unacceptable to {who} on {v.issue.replace('_', ' ')}: "
                             f"{', '.join(bad)}.")
    them = [c.name for c in scenario.countries if c.name != scenario.focus_country]
    lines.append(f"Your view of {', '.join(them)} is a guess; the rehearsal tests how much "
                 f"the advice depends on it.")
    return lines
