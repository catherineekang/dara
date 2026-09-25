"""Metrics for the success criteria in proposal Section 1.

    criterion (a) brief consistency     -> brief_consistency, position_movement
    criterion (b) judge reproducibility -> judge_self_consistency, position_bias

Criterion (c) is a blind human comparison and has no automatic metric here by
design: asking the same model family to score whether its own output is fair
would measure agreement with itself, not fairness.

Everything is pure Python. These numbers go in the report, so they should not
depend on a library version that a marker cannot reproduce.
"""
from __future__ import annotations

import re
from statistics import mean
from typing import Dict, List, Sequence

from crisis_debate.schemas import CRITERIA, Argument, CountryBrief, RoundJudgment

_STOP = frozenset({
    "a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "be", "we", "our", "will", "not", "no", "must", "with", "that", "this", "it",
    "at", "as", "by", "from", "any", "all", "its",
})


def _tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _best_match(needle: str, haystack: Sequence[str]) -> float:
    nt = _tokens(needle)
    return max((_jaccard(nt, _tokens(h)) for h in haystack), default=0.0)


def brief_consistency(argument: Argument, brief: CountryBrief) -> Dict[str, float]:
    """How anchored is this argument in the brief it was given?

    Lexical overlap, deliberately: an embedding score would be smoother but
    would also make the metric depend on an embedding model whose behaviour is
    not part of the system under test. Read these as a drift alarm, not as a
    measure of argument quality - a high score means the agent is still talking
    about its brief, not that it argued well.

    `red_line_fidelity` is the load-bearing one. An agent that quietly drops a
    red line it was given has drifted into a generic position, which is exactly
    what success criterion (a) rules out.
    """
    stated = argument.red_lines or []
    given = brief.red_lines or []
    red_line_fidelity = mean([_best_match(r, stated) for r in given]) if given and stated else 0.0

    interest_anchoring = mean(
        [_best_match(c.claim, brief.interests) for c in argument.key_claims]
    ) if argument.key_claims and brief.interests else 0.0

    # A concession that repeats a red line is a contradiction, not a concession.
    conflicts = sum(1 for c in argument.concessions if _best_match(c, given) > 0.5)

    return {
        "red_line_fidelity": round(red_line_fidelity, 4),
        "interest_anchoring": round(interest_anchoring, 4),
        "n_red_lines_dropped": max(0, len(given) - len(stated)),
        "n_concessions_conflicting_with_red_lines": conflicts,
    }


def position_movement(rounds: Sequence[Sequence[Argument]], country: str) -> List[float]:
    """1 - similarity between consecutive positions, per round transition.

    Near zero across the board means the agent restated itself and the extra
    round bought nothing - the degeneration-of-thought failure Liang et al.
    describe, and the thing the round-count ablation is looking for.
    """
    positions = []
    for round_args in rounds:
        for a in round_args:
            if a.country == country:
                positions.append(a.position)
    return [
        round(1.0 - _jaccard(_tokens(positions[i]), _tokens(positions[i + 1])), 4)
        for i in range(len(positions) - 1)
    ]


def judge_self_consistency(repeats: Sequence[RoundJudgment]) -> Dict[str, Dict[str, float]]:
    """Agreement across repeated scorings of the SAME round (criterion b).

    Reports exact-agreement rate and mean absolute deviation from the per-run
    mean, per criterion. A judge that cannot reproduce its own scores cannot
    support any comparison built on top of it, so this runs before any
    between-condition result is reported, not after.
    """
    if len(repeats) < 2:
        raise ValueError("need at least two scorings of the same round")

    out: Dict[str, Dict[str, float]] = {}
    countries = [j.country for j in repeats[0].judgments]
    for country in countries:
        per_criterion: Dict[str, List[int]] = {c: [] for c in CRITERIA}
        for rj in repeats:
            for s in rj.for_country(country).scores:
                per_criterion[s.criterion].append(s.score)
        stats = {}
        for crit, scores in per_criterion.items():
            if not scores:
                continue
            m = mean(scores)
            stats[f"{crit}_exact_agreement"] = round(
                scores.count(max(set(scores), key=scores.count)) / len(scores), 4
            )
            stats[f"{crit}_mad"] = round(mean(abs(s - m) for s in scores), 4)
        out[country] = stats
    return out


def position_bias(original: RoundJudgment, swapped: RoundJudgment) -> Dict[str, Dict[str, int]]:
    """Score change when the argument order is swapped and nothing else is.

    LLM judges are documented to favour whichever response came first. Any
    non-zero delta here is the judge responding to presentation order rather
    than to content, and means the rubric needs revision before the judge's
    scores are used for anything.
    """
    out: Dict[str, Dict[str, int]] = {}
    for j in original.judgments:
        try:
            other = swapped.for_country(j.country)
        except KeyError:
            continue
        a, b = j.score_map(), other.score_map()
        out[j.country] = {c: b.get(c, 0) - a.get(c, 0) for c in a}
    return out


# --------------------------------------------------------------------------
# Upgrade C: is the judge measuring anything real?
# --------------------------------------------------------------------------
def _rank(values: Sequence[float]) -> List[float]:
    """Average ranks, so ties do not fabricate ordering."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Rank correlation, pure Python.

    Pearson on ranks rather than the 6*d^2 shortcut, because the shortcut is
    wrong in the presence of ties and judge scores are 1-5 integers, so ties
    are the common case rather than the exception.
    """
    if len(xs) != len(ys):
        raise ValueError("spearman needs equal-length inputs")
    if len(xs) < 3:
        raise ValueError("spearman needs at least 3 pairs to mean anything")
    rx, ry = _rank(xs), _rank(ys)
    mx, my = mean(rx), mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx)
    dy = sum((b - my) ** 2 for b in ry)
    if dx == 0 or dy == 0:
        return 0.0  # a constant series carries no ordering information
    return round(num / (dx * dy) ** 0.5, 4)


def judge_validity(pairs: Sequence[tuple]) -> Dict[str, float]:
    """Do the judge's scores track the objective quality of what was proposed?

    `pairs` is (judge_total, objective_score) - typically the judge's summed
    rubric score for one country in one round against the joint value of the
    package that country proposed in that round.

    This is the test that decides whether the judge is load-bearing or
    decorative. A correlation near zero means arguments the judge called strong
    produced no better settlements than ones it called weak - a clean negative
    result about LLM-as-judge in a negotiation setting, and worth more than a
    small positive effect on success rate.
    """
    if len(pairs) < 3:
        raise ValueError("need at least 3 (judge_total, objective) pairs")
    judge_scores = [p[0] for p in pairs]
    objective = [p[1] for p in pairs]
    rho = spearman(judge_scores, objective)
    return {
        "n": len(pairs),
        "spearman_rho": rho,
        "judge_score_mean": round(mean(judge_scores), 4),
        "objective_mean": round(mean(objective), 4),
        # Deliberately coarse. With a handful of rounds per scenario, anything
        # finer would read precision into noise.
        "reads_as": (
            "no relationship" if abs(rho) < 0.2
            else "weak" if abs(rho) < 0.5
            else "moderate" if abs(rho) < 0.7
            else "strong"
        ),
    }


def self_preference_bias(
    own_family: Sequence[RoundJudgment], other_family: Sequence[RoundJudgment]
) -> Dict[str, float]:
    """Does the judge score arguments from its own model family more highly?

    Both sequences must judge the SAME arguments-equivalent task, differing only
    in which model generated the arguments. A positive delta means the judge is
    rewarding provenance rather than content, which matters here because the
    judge shares a model family with both debaters by default.
    """
    if not own_family or not other_family:
        raise ValueError("need judgments from both families")

    def totals(seq: Sequence[RoundJudgment]) -> List[float]:
        return [float(j.total()) for rj in seq for j in rj.judgments]

    own, other = totals(own_family), totals(other_family)
    delta = mean(own) - mean(other)
    return {
        "own_family_mean": round(mean(own), 4),
        "other_family_mean": round(mean(other), 4),
        "delta": round(delta, 4),
        "max_possible": 5.0 * len(CRITERIA),
        "favours_own_family": delta > 0,
    }
