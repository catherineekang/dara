"""The paper's evaluation (Section 5), run from the backend and written to disk.

Nothing here is part of the product: the console never calls it, and it writes
only to `results/eval_<time>/`:

    results.json            every run, every score
    report.md               the tables, laid out like the paper's
    human_eval_sheet.csv    a subset for people to score (paper §5.2, §5.4)

It also answers the question a reader asks first: *every settlement is
enumerated and scored before any agent runs, so why use language agents at
all?* The arithmetic only knows the best deal because both valuation tables
were authored. In real use the other side's table is private and the user has
a guess. So the agents are compared with **non-language baselines** that use
that guess, and with an **information-transmission probe** in which the agents
see both tables:

    oracle                 best joint deal; needs BOTH true tables (not available in real use)
    calculator ±20%/±40%   Nash bargaining solution on your guess of the other side
    split the difference   every issue at its middle option
    demand everything      the deal best for you, ignoring the other side
    B0 / B1 / B2           a single call / debate without judge / debate with judge
    B2 full information    both agents see both tables: the ceiling for dialogue

If B2 does not beat the calculator working from a guess, the debate has not
earned its cost on outcomes, and the report says so.

Deviations from the paper, stated rather than hidden:

* Position change and cross-agent similarity use a lexical cosine (bag of
  words), not sentence embeddings, so the evaluation runs with one API key and
  no extra model. Package movement, which is exact, is reported beside it.
* The evaluator is a language model from the same family as the agents (self-
  preference is possible). The human sheet exists to check it.
* The fact-checking reference is the evaluator's list of unsupported claims,
  not human annotation.
"""
from __future__ import annotations

import csv
import json
import math
import random
import re
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from crisis_debate import contamination, metrics, pipeline, scenario as scenario_mod, situational, stress
from crisis_debate.agents import JudgeAgent, _fmt_arguments, _fmt_brief, _fmt_issues, _fmt_sources
from crisis_debate.experiment import _judge_validity_pairs
from crisis_debate.schemas import Argument, DebateRun, RoundJudgment, Scenario
from crisis_debate.utilities import Frontier, all_packages, score_package, split_the_difference, utility_vector

BackendFactory = Callable[[Scenario, str, int], Any]

#: (label, condition, rounds, full information). B2 at three rounds is shared
#: by the main comparison and the round-count ablation.
MAIN = [("B0", "B0", 3, False), ("B1", "B1", 3, False), ("B2", "B2", 3, False)]
ROUNDS = [("B2 · 1 round", "B2", 1, False), ("B2 · 2 rounds", "B2", 2, False)]
FULL_INFO = [("B2 · full information", "B2", 3, True)]

OUTCOME = ("joint_value_recovered", "integrative_gain", "focus_gain_over_split",
           "nash_ratio", "is_pareto_optimal")
DEBATER = ("role_consistency", "crisis_relevance", "feasibility", "responsiveness", "evidence_use")
RECOMMENDER = ("feasibility", "tradeoff_coverage", "priority_consistency",
               "counterpart_consideration", "strong_arguments")


# ==========================================================================
# What the evaluator model returns
# ==========================================================================
class TurnScore(BaseModel):
    """One debater turn on the paper's Table 5 rubric, 1 (poor) to 5 (excellent)."""

    round_index: int
    country: str
    role_consistency: int = Field(ge=1, le=5)
    crisis_relevance: int = Field(ge=1, le=5)
    feasibility: int = Field(ge=1, le=5)
    responsiveness: int = Field(ge=1, le=5)
    evidence_use: int = Field(ge=1, le=5)
    claims: int = Field(ge=0, description="substantive claims made in this turn")
    new_claims: int = Field(ge=0, description="of those, how many this country had not raised before")
    unsupported_claims: List[str] = Field(default_factory=list)
    justification: str


class DebateEvaluation(BaseModel):
    turns: List[TurnScore]

    @classmethod
    def scripted(cls, user: str) -> "DebateEvaluation":
        turns = [TurnScore(round_index=int(r), country=c, role_consistency=3, crisis_relevance=3,
                           feasibility=3, responsiveness=3, evidence_use=3, claims=2,
                           new_claims=1 if int(r) > 1 else 2, justification="scripted")
                 for r, c in re.findall(r"^TURN round=(\d+) country=(.+)$", user, flags=re.M)]
        return cls(turns=turns)


class RecommendationEvaluation(BaseModel):
    """The recommendation on the paper's §5.4 rubric, 1 to 5, plus distinctness."""

    feasibility: int = Field(ge=1, le=5)
    tradeoff_coverage: int = Field(ge=1, le=5)
    priority_consistency: int = Field(ge=1, le=5)
    counterpart_consideration: int = Field(ge=1, le=5)
    strong_arguments: int = Field(ge=1, le=5)
    options: int = Field(ge=0)
    distinct_options: int = Field(ge=0, description="options that are not rewordings of another")
    justification: str

    @classmethod
    def scripted(cls, user: str) -> "RecommendationEvaluation":
        return cls(feasibility=3, tradeoff_coverage=3, priority_consistency=3,
                   counterpart_consideration=3, strong_arguments=3, options=1,
                   distinct_options=1, justification="scripted")


class ProbeAnswer(BaseModel):
    answer: str
    choice: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @classmethod
    def scripted(cls, user: str) -> "ProbeAnswer":
        return cls(answer="I do not know.", choice="", confidence=0.5)


EVALUATOR_SYSTEM = """You are an independent evaluator in a research study of
multi-agent negotiation. You score; you do not take sides and you do not advise.
Score strictly against the rubric anchors. 1 = clearly fails the criterion,
3 = adequate with clear gaps, 5 = excellent with nothing material missing. A score
must be justified by something specific in the text."""

DEBATE_RUBRIC = """Score EVERY turn below (one entry per TURN marker) on:
  role_consistency   consistent with that country's assigned interests, priorities,
                     resources and constraints (its brief below)
  crisis_relevance   addresses this specific crisis and its negotiable issues
  feasibility        the proposed actions are realistic given the country's stated
                     resources and constraints
  responsiveness     meaningfully addresses what the other side argued in the
                     previous round (round 1: anticipates the other side's position)
  evidence_use       evidence is relevant and used appropriately; a source tag must
                     contain the fact it is cited for
Also count:
  claims             the substantive claims in the turn
  new_claims         how many of them this country had NOT already made in an
                     earlier round (in round 1, all of them are new)
  unsupported_claims every factual claim (figure, date, legal provision, event)
                     that is supported by NONE of: the scenario text, the listed
                     sources, or the speaking country's own brief. Restating the
                     country's own interests, resources, constraints or red lines
                     is supported; so is an opinion, a proposal or an argument.
                     List only checkable facts that nothing above backs up."""

RECOMMENDATION_RUBRIC = """Score the recommendation, written for {focus}, on:
  feasibility                 the options could actually be carried out
  tradeoff_coverage           the major trade-offs of this crisis are covered
  priority_consistency        consistent with {focus}'s priorities and red lines
  counterpart_consideration   takes the other side's interests seriously
  strong_arguments            reflects the strongest arguments made in the debate
                              (when there was no debate, the strongest points in
                              the two briefs)
and count its options and how many are genuinely distinct rather than rewordings
of one another."""


# ==========================================================================
# Small numeric helpers
# ==========================================================================
STOP = set("""a an the and or of to in on for with by at from as is are was were be been it its this that
these those their there they them we our you your not no but if then than so such into over under
about which who whom what when where while would will can could should may might must do does did
has have had also any all each per more most less very""".split())


def _bag(text: str) -> Counter:
    return Counter(w for w in re.findall(r"[a-z][a-z0-9'-]+", text.lower()) if w not in STOP)


def cosine(a: str, b: str) -> float:
    """Lexical cosine similarity; 1 = the same words, 0 = none shared."""
    x, y = _bag(a), _bag(b)
    dot = sum(x[w] * y[w] for w in x)
    nx, ny = math.sqrt(sum(v * v for v in x.values())), math.sqrt(sum(v * v for v in y.values()))
    return dot / (nx * ny) if nx and ny else 0.0


def summary(values: Sequence[float]) -> Dict[str, float]:
    vals = [float(v) for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not vals:
        return {"n": 0}
    mean = statistics.mean(vals)
    sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
    return {"n": len(vals), "mean": round(mean, 4), "sd": round(sd, 4),
            "min": round(min(vals), 4), "max": round(max(vals), 4),
            "cv": round(sd / abs(mean), 4) if abs(mean) > 1e-9 else None}


def _turn_text(a: Argument) -> str:
    return " ".join([a.position] + [f"{c.claim} {c.evidence}" for c in a.key_claims] + a.concessions)


# ==========================================================================
# Non-language baselines: what arithmetic alone gets you
# ==========================================================================
def non_language_baselines(scenario: Scenario, *, samples: int = 200, seed: int = 4213) -> Dict[str, Any]:
    """Scored on the TRUE valuations. Only the oracle sees the other side's
    true table; the calculators see a ±20% or ±40% guess of it."""
    f = Frontier.build(scenario)
    pkgs = all_packages(scenario)
    focus = scenario.focus_country

    def scored(pkg) -> Dict[str, float]:
        s = score_package(f, pkg)
        return {k: s[k] for k in OUTCOME}

    oracle = max(pkgs, key=lambda p: sum(utility_vector(scenario, p).values()))
    greedy = max(pkgs, key=lambda p: (utility_vector(scenario, p)[focus],
                                      sum(utility_vector(scenario, p).values())))
    out = {
        "oracle (needs both true tables)": {"single": scored(oracle), "calls": 0},
        "split the difference": {"single": scored(split_the_difference(scenario)), "calls": 0},
        "demand everything": {"single": scored(greedy), "calls": 0},
    }
    def nash_pick(guess: Scenario):
        def nash(p):
            prod = 1.0
            for v in utility_vector(guess, p).values():
                prod *= v
            return prod
        return max(pkgs, key=nash)

    def calculator(name: str, make_guess) -> None:
        rows = [scored(nash_pick(make_guess())) for _ in range(samples)]
        out[name] = {"summary": {k: summary([r[k] for r in rows]) for k in OUTCOME},
                     "samples": samples, "calls": 0}

    # From most to least knowledge of the other side. A ±x% guess keeps their
    # priority ORDER right, which is the kindest way to be wrong; the next two
    # are how a guess usually goes wrong at the table.
    rng = random.Random(seed)
    for spread in (0.20, 0.40):
        calculator(f"calculator, ±{int(spread * 100)}% guess of their weights",
                   lambda s=spread: stress.perturb_opponent_weights(scenario, s, rng))

    def shuffled() -> Scenario:
        g = scenario.model_copy(deep=True)
        for c in g.countries:
            if c.name != focus:
                ws = [v.weight for v in c.valuations]
                rng.shuffle(ws)
                for v, w in zip(c.valuations, ws):
                    v.weight = w
        return g
    calculator("calculator, their priorities unknown (random order)", shuffled)

    def zero_sum() -> Scenario:
        g = scenario.model_copy(deep=True)
        mine = g.brief(focus)
        for c in g.countries:
            if c.name == focus:
                continue
            for v in c.valuations:
                own = mine.valuation(v.issue)
                v.weight = 1.0 / len(c.valuations)
                v.option_values = {o: round(1.0 - own.option_values.get(o, 0.5), 6)
                                   for o in v.option_values}
        return g
    out["calculator, assumes they want the opposite of you"] = {
        "single": scored(nash_pick(zero_sum())), "calls": 0}
    return out


# ==========================================================================
# One run, and its evaluation
# ==========================================================================
def _run_once(scenario: Scenario, backend, spec, conditions) -> Tuple[DebateRun, float]:
    label, condition, rounds, full_info = spec
    started = time.time()
    run = pipeline.run(scenario, backend, condition=condition, rounds=rounds,
                       priorities=(pipeline.RQ_PRIORITY,), conditions=conditions,
                       full_information=full_info)
    return run, time.time() - started


def _transcript_for_evaluator(scenario: Scenario, run: DebateRun) -> str:
    parts = []
    for r, args in enumerate(run.rounds, start=1):
        for a in args:
            parts.append(f"TURN round={r} country={a.country}\n{_fmt_arguments([a])}")
    return "\n\n".join(parts)


def _context(scenario: Scenario) -> str:
    # The evaluator sees the briefs and public sources, never the valuations.
    briefs = "\n\n".join(_fmt_brief(b) for b in scenario.countries)
    return (f"Crisis scenario:\n{scenario.description}\n\nSources:\n{_fmt_sources(scenario)}\n\n"
            f"Negotiable issues:\n{_fmt_issues(scenario.issues)}\n\nCountry briefs:\n{briefs}")


def evaluate_debate(scenario: Scenario, run: DebateRun, evaluator) -> Optional[DebateEvaluation]:
    if not run.rounds:
        return None
    user = (f"{_context(scenario)}\n\n{DEBATE_RUBRIC}\n\nTranscript:\n\n"
            f"{_transcript_for_evaluator(scenario, run)}")
    return evaluator.parse(system=EVALUATOR_SYSTEM, user=user, output_format=DebateEvaluation)


def evaluate_recommendation(scenario: Scenario, run: DebateRun, evaluator) -> RecommendationEvaluation:
    rec = pipeline.rq_recommendation(run)
    debate = (f"Debate transcript:\n{_transcript_for_evaluator(scenario, run)}" if run.rounds
              else "There was no debate (single-model condition).")
    options = "\n".join(f"  - If {o.condition}: {o.action} (concession: {o.requires_concession}; "
                        f"risk: {o.principal_risk}; serves {o.priority_tag})" for o in rec.options)
    user = (f"{_context(scenario)}\n\n{debate}\n\n"
            f"{RECOMMENDATION_RUBRIC.format(focus=scenario.focus_country)}\n\n"
            f"Recommendation:\nHeadline: {rec.headline}\nRationale: {rec.rationale}\n"
            f"Options:\n{options}\nOpening package: {rec.package()}")
    return evaluator.parse(system=EVALUATOR_SYSTEM, user=user, output_format=RecommendationEvaluation)


def round_dynamics(run: DebateRun, ev: Optional[DebateEvaluation]) -> Dict[str, Any]:
    """Paper Table 4: how positions move, whether new arguments appear, and
    whether the two sides converge while drifting from their roles."""
    names = [a.country for a in run.rounds[0]] if run.rounds else []
    by = {n: [next(a for a in rnd if a.country == n) for rnd in run.rounds] for n in names}
    change, moved = [], []
    for n, turns in by.items():
        for prev, cur in zip(turns, turns[1:]):
            change.append(cosine(_turn_text(prev), _turn_text(cur)))
            p, c = prev.package(), cur.package()
            moved.append(sum(p.get(k) != v for k, v in c.items()))
    cross_text, agree = [], []
    for rnd in run.rounds:
        if len(rnd) >= 2:
            a, b = rnd[0], rnd[1]
            cross_text.append(round(cosine(_turn_text(a), _turn_text(b)), 4))
            pa, pb = a.package(), b.package()
            agree.append(round(sum(pa.get(k) == v for k, v in pb.items()) / max(1, len(pb)), 4))
    out: Dict[str, Any] = {
        "position_similarity_between_rounds": summary(change),   # lower = more change
        "issues_moved_between_rounds": summary(moved),
        "cross_agent_text_similarity_by_round": cross_text,
        "package_agreement_by_round": agree,
    }
    if ev and ev.turns:
        later = [t for t in ev.turns if t.round_index > 1]
        out["novel_argument_rate"] = (round(sum(t.new_claims for t in later)
                                            / max(1, sum(t.claims for t in later)), 4) if later else None)
        role = {r: statistics.mean(t.role_consistency for t in ev.turns if t.round_index == r)
                for r in sorted({t.round_index for t in ev.turns})}
        out["role_consistency_by_round"] = {str(k): round(v, 3) for k, v in role.items()}
        # False consensus: the two packages converge while role adherence falls.
        out["false_consensus"] = bool(
            len(agree) >= 2 and agree[-1] > agree[0] and agree[-1] >= 0.75
            and len(role) >= 2 and list(role.values())[-1] < list(role.values())[0])
    return out


def leak_scan(scenario: Scenario, run: DebateRun) -> Dict[str, Any]:
    """P0 on one run: post-cutoff markers in agent output that were not supplied."""
    rec = run.recommendations[0] if run.recommendations else None
    cache = {
        "rounds": [[{"country": a.country, "position": _turn_text(a),
                     "concession": "; ".join(a.concessions)} for a in rnd] for rnd in run.rounds],
        "judge": [{"round_index": j.round_index,
                   "judgments": [{"country": x.country, "summary": x.summary} for x in j.judgments],
                   "unaddressed": j.unaddressed_tradeoffs} for j in run.judgments],
        "recommendation": ({"rationale": rec.rationale,
                            "options": [o.model_dump() for o in rec.options]} if rec else {}),
    }
    supplied = " ".join([scenario.description] + [f for s in scenario.sources for f in s.facts])
    rep = contamination.scan(scenario.scenario_id, cache, supplied_text=supplied)
    return {"leaks": len(rep.leaks), "verdict": rep.verdict,
            "found": [{"marker": f.marker, "where": f.where, "excerpt": f.excerpt} for f in rep.leaks]}


# ==========================================================================
# The judge (paper Tables 6 and 7)
# ==========================================================================
def _swap_labels(args: Sequence[Argument]) -> List[Argument]:
    """The same arguments with the two country names exchanged everywhere."""
    a, b = args[0].country, args[1].country
    out = []
    for x in args:
        raw = json.dumps(x.model_dump(), ensure_ascii=False)
        raw = raw.replace(a, "\u0000A").replace(b, a).replace("\u0000A", b)
        out.append(Argument.model_validate(json.loads(raw)))
    return out


def judge_tests(scenario: Scenario, run: DebateRun, backend, k: int) -> Dict[str, Any]:
    if not run.rounds:
        return {}
    judge = JudgeAgent(backend=backend)
    first = run.rounds[0]
    repeats = [judge.judge(scenario, 1, first) for _ in range(max(1, k))]
    swapped_order = judge.judge(scenario, 1, list(reversed(first)))
    swapped_labels = judge.judge(scenario, 1, _swap_labels(first))
    a, b = first[0].country, first[1].country
    # content judged under its own label vs the same content under the other label
    label_diffs = []
    for name, other in ((a, b), (b, a)):
        try:
            orig = repeats[0].for_country(name).score_map()
            moved = swapped_labels.for_country(other).score_map()
        except KeyError:
            continue
        label_diffs += [abs(orig[c] - moved.get(c, orig[c])) for c in orig]
    return {
        # needs at least two scorings of the same round
        "repeated_judgment": metrics.judge_self_consistency(repeats) if len(repeats) >= 2 else None,
        "order_swap": metrics.position_bias(repeats[0], swapped_order),
        "label_swap_mean_abs_diff": round(statistics.mean(label_diffs), 4) if label_diffs else None,
        "calls": k + 2,
    }


def _match(a: str, b: str) -> bool:
    return cosine(a, b) >= 0.35


def fact_check_agreement(pairs: Sequence[Tuple[RoundJudgment, DebateEvaluation]]) -> Dict[str, Any]:
    """Judge's unsupported-claim flags against the evaluator's (the reference)."""
    tp = fp = fn = 0
    for rjs, ev in pairs:
        ref = {(t.round_index, t.country): t.unsupported_claims for t in ev.turns}
        for rj in rjs:
            for j in rj.judgments:
                gold = list(ref.get((rj.round_index, j.country), []))
                for claim in j.unsupported_claims:
                    hit = next((g for g in gold if _match(claim, g)), None)
                    if hit is not None:
                        tp += 1
                        gold.remove(hit)
                    else:
                        fp += 1
                fn += len(gold)
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p and r else None
    return {"precision": p, "recall": r, "f1": f1, "tp": tp, "fp": fp, "fn": fn,
            "reference": "evaluator model, not human annotation"}


# ==========================================================================
# Contamination probes P1 and P3 (one call each per case)
# ==========================================================================
def probes(scenario: Scenario, backend) -> Dict[str, Any]:
    sid = scenario.scenario_id
    out: Dict[str, Any] = {}
    if sid in contamination.FREE_RECALL:
        a = backend.parse(system="Answer from memory. Be honest about uncertainty.",
                          user=contamination.FREE_RECALL[sid], output_format=ProbeAnswer)
        hits = [m.label for m in contamination.MARKERS.get(sid, []) if m.found_in(a.answer)]
        out["P1_free_recall"] = {"recalls_post_cutoff_events": bool(hits), "markers": hits,
                                 "answer": a.answer[:600]}
    if sid in contamination.DISCRIMINATION:
        q, truth = contamination.DISCRIMINATION[sid]
        a = backend.parse(system="Answer the question. Put A or B in `choice`.", user=q,
                          output_format=ProbeAnswer)
        out["P3_discrimination"] = {"correct": a.choice.strip().upper()[:1] == truth,
                                    "confidence": a.confidence,
                                    "reads_as": ("recall (picks the real outcome confidently)"
                                                 if a.choice.strip().upper()[:1] == truth and a.confidence >= 0.7
                                                 else "no clear recall")}
    return out


# ==========================================================================
# The whole study
# ==========================================================================
def specs(include_rounds: bool, include_full_info: bool) -> List[tuple]:
    return MAIN + (ROUNDS if include_rounds else []) + (FULL_INFO if include_full_info else [])


def planned_calls(scenario: Scenario, *, repeats: int, include_rounds: bool = True,
                  include_full_info: bool = True, evaluator: bool = True, judge_repeats: int = 3,
                  include_probes: bool = True) -> Dict[str, int]:
    n = len(scenario.countries)
    per: Dict[str, int] = {}
    for label, cond, rounds, _ in specs(include_rounds, include_full_info):
        if cond == "B0":
            calls = 1
        else:
            calls = n * rounds + (rounds if cond == "B2" else 0) + 1
        if evaluator:
            calls += 1 + (1 if cond != "B0" else 0)          # recommendation (+ debate)
        per[label] = calls * repeats
    per["judge tests"] = judge_repeats + 2
    if include_probes:
        per["contamination probes"] = 2
    per["total"] = sum(per.values())
    return per


def run_case(case_name: str, factory: BackendFactory, *, repeats: int, judge_repeats: int,
             include_rounds: bool, include_full_info: bool, evaluator: bool,
             include_probes: bool, log: Callable[[str], None] = print,
             checkpoint: Optional[Path] = None) -> Dict[str, Any]:
    scenario = scenario_mod.load(case_name)
    case = scenario.scenario_id
    snap = situational.load_snapshot(case_name)        # pinned: every run reads the same world
    side_backends: List[Any] = []                       # evaluator, judge tests, probes
    frontier = Frontier.build(scenario)
    rows: List[Dict[str, Any]] = []
    fact_pairs: List[tuple] = []
    validity: List[tuple] = []
    judge_out: Dict[str, Any] = {}
    evaluator_backend = factory(scenario, "evaluator", 0) if evaluator else None
    if evaluator_backend is not None:
        side_backends.append(evaluator_backend)

    for rep in range(repeats):
        for spec in specs(include_rounds, include_full_info):
            label = spec[0]
            backend = factory(scenario, label, rep)
            log(f"  {case} · {label} · repeat {rep + 1}/{repeats}")
            try:
                run, seconds = _run_once(scenario, backend, spec, snap)
            except Exception as exc:                       # a failed run is a finding, not a crash
                rows.append({"label": label, "repeat": rep, "failed": f"{type(exc).__name__}: {exc}"})
                continue
            rec = pipeline.rq_recommendation(run)
            usage = dict(getattr(backend, "usage", {}))
            row: Dict[str, Any] = {
                "label": label, "condition": spec[1], "rounds": spec[2], "full_information": spec[3],
                "repeat": rep, "seconds": round(seconds, 2), "calls": usage.get("calls", 0),
                "tokens_in": usage.get("input", 0), "tokens_out": usage.get("output", 0),
                "illegal": run.illegal_packages, "package": rec.package(),
                "conditions_id": run.conditions_id, "leak_scan": leak_scan(scenario, run),
                "transcript": [[a.model_dump() for a in rnd] for rnd in run.rounds],
                "judgments": [j.model_dump() for j in run.judgments],
                "recommendation": rec.model_dump(),
            }
            if not run.illegal_packages:
                row["outcome"] = {k: score_package(frontier, rec.package())[k] for k in OUTCOME}
            ev = None
            if evaluator_backend is not None:
                try:
                    ev = evaluate_debate(scenario, run, evaluator_backend)
                    rev = evaluate_recommendation(scenario, run, evaluator_backend)
                    row["recommender_rubric"] = rev.model_dump()
                    row["recommender_overall"] = round(statistics.mean(getattr(rev, c) for c in RECOMMENDER), 4)
                except Exception as exc:
                    row["evaluator_error"] = f"{type(exc).__name__}: {exc}"
            if ev is not None:
                row["debater_rubric"] = [t.model_dump() for t in ev.turns]
                row["debater_overall"] = round(statistics.mean(
                    getattr(t, c) for t in ev.turns for c in DEBATER), 4) if ev.turns else None
            if run.rounds:
                row["dynamics"] = round_dynamics(run, ev)
            if spec[1] == "B2" and ev is not None:
                fact_pairs.append(([RoundJudgment.model_validate(j) for j in row["judgments"]], ev))
            if spec[0] == "B2":
                validity += _judge_validity_pairs(scenario, run)
                if rep == 0 and judge_repeats:
                    jb = factory(scenario, "judge tests", 0)
                    side_backends.append(jb)
                    judge_out = judge_tests(scenario, run, jb, judge_repeats)
            rows.append(row)
            if checkpoint is not None:
                # Saved as each run finishes, so a crash late in a long, paid
                # evaluation loses one run rather than all of them.
                with checkpoint.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"case": case, **row}, ensure_ascii=False) + "\n")

    out = {
        "scenario_id": case, "title": scenario.title,
        "baselines": non_language_baselines(scenario),
        "runs": rows,
        "judge": {**judge_out,
                  "validity": metrics.judge_validity(validity) if len(validity) >= 3 else {"n": len(validity)},
                  "fact_checking": fact_check_agreement(fact_pairs) if fact_pairs else None},
    }
    if include_probes:
        try:
            pb = factory(scenario, "probes", 0)
            side_backends.append(pb)
            out["contamination_probes"] = probes(scenario, pb)
        except Exception as exc:
            out["contamination_probes"] = {"error": f"{type(exc).__name__}: {exc}"}
    out["aggregates"] = aggregate(rows)
    out["side_usage"] = {k: sum(int(getattr(b, "usage", {}).get(k, 0)) for b in side_backends)
                         for k in ("calls", "input", "output")}
    return out


# ==========================================================================
# Aggregates (paper Table 10)
# ==========================================================================
def _failed(row: Dict[str, Any]) -> bool:
    if row.get("failed") or row.get("illegal"):
        return True
    if any(t["role_consistency"] <= 2 for t in row.get("debater_rubric", [])):
        return True
    rub = row.get("recommender_rubric")
    return bool(rub and rub["feasibility"] <= 2)


def aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    labels = list(dict.fromkeys(r["label"] for r in rows))
    by: Dict[str, Any] = {}
    for label in labels:
        mine = [r for r in rows if r["label"] == label]
        ok = [r for r in mine if "outcome" in r]
        by[label] = {
            "runs": len(mine),
            "failure_rate": round(sum(_failed(r) for r in mine) / max(1, len(mine)), 4),
            **{m: summary([r["outcome"][m] for r in ok]) for m in OUTCOME},
            "recommender_overall": summary([r.get("recommender_overall") for r in mine]),
            "debater_overall": summary([r.get("debater_overall") for r in mine]),
            "calls": summary([r.get("calls", 0) for r in mine]),
            "tokens": summary([r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in mine]),
            "seconds": summary([r.get("seconds") for r in mine]),
            "leaks": sum(r.get("leak_scan", {}).get("leaks", 0) for r in mine),
        }
    # Matched comparisons: the same repeat index, B2 against each other condition.
    comparisons: Dict[str, Any] = {}
    for other in ("B0", "B1"):
        for metric in ("joint_value_recovered", "integrative_gain", "focus_gain_over_split",
                       "recommender_overall"):
            deltas, wins = [], []
            for rep in sorted({r["repeat"] for r in rows}):
                a = next((r for r in rows if r["label"] == "B2" and r["repeat"] == rep), None)
                b = next((r for r in rows if r["label"] == other and r["repeat"] == rep), None)
                va = (a or {}).get("outcome", {}).get(metric, (a or {}).get(metric))
                vb = (b or {}).get("outcome", {}).get(metric, (b or {}).get(metric))
                if va is None or vb is None:
                    continue
                deltas.append(va - vb)
                wins.append(va > vb)
            comparisons[f"B2 - {other} · {metric}"] = {
                "delta": summary(deltas),
                "B2_better_in": f"{sum(wins)}/{len(wins)}" if wins else "n/a"}
    return {"by_label": by, "comparisons": comparisons}


def overall(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Across both scenarios: overall mean ± SD and scenario sensitivity."""
    rows = [r for c in cases for r in c["runs"]]
    agg = aggregate(rows)
    sens = {}
    for label in agg["by_label"]:
        means = [c["aggregates"]["by_label"].get(label, {}).get("joint_value_recovered", {}).get("mean")
                 for c in cases]
        means = [m for m in means if m is not None]
        sens[label] = {"per_scenario": means, "range": round(max(means) - min(means), 4) if means else None}
    agg["scenario_sensitivity"] = sens
    return agg


# ==========================================================================
# Writing it up
# ==========================================================================
def _ms(s: Dict[str, Any], d: int = 3) -> str:
    if not s or not s.get("n"):
        return "n/a"
    return f"{s['mean']:.{d}f} ± {s['sd']:.{d}f}"


def write_report(result: Dict[str, Any]) -> str:
    L: List[str] = []
    L += [f"# DARA evaluation", "",
          f"- Mode: **{result['mode']}**",
          f"- Cases: {', '.join(c['scenario_id'] for c in result['cases'])}; "
          f"{result['repeats']} repeat(s) per condition",
          f"- Model calls: {result['calls_spent']}, tokens: {result['tokens_spent']}",
          "- The issue sets were authored with hindsight, so absolute agreement with history is "
          "contaminated; comparisons between conditions are what to report.", ""]
    if "DRY RUN" in result["mode"]:
        L += ["> **Dry run.** Scripted agents: the numbers check the plumbing and mean nothing.", ""]

    L += ["## 1. Why language agents? Debate against arithmetic", "",
          "Every settlement is scored in advance, but only because **both** valuation tables were "
          "authored. In real use the other side's table is a guess. The calculators below pick the "
          "Nash bargaining deal from such a guess; the oracle needs the true table. All rows are "
          "scored on the true valuations.", ""]
    for c in result["cases"]:
        agg = c["aggregates"]["by_label"]
        L += [f"### {c['title']}", "",
              "| Approach | Joint value recovered | Integrative gain | Gain for you over split | Pareto rate | Model calls |",
              "|---|---|---|---|---|---|"]
        for name, b in c["baselines"].items():
            if "single" in b:
                s = b["single"]
                L.append(f"| {name} | {s['joint_value_recovered']:.3f} | {s['integrative_gain']:.3f} | "
                         f"{s['focus_gain_over_split']:.3f} | {s['is_pareto_optimal']:.0%} | 0 |")
            else:
                s = b["summary"]
                L.append(f"| {name} | {_ms(s['joint_value_recovered'])} | {_ms(s['integrative_gain'])} | "
                         f"{_ms(s['focus_gain_over_split'])} | {s['is_pareto_optimal']['mean']:.0%} | 0 |")
        for label, a in agg.items():
            if label in ("B2 · 1 round", "B2 · 2 rounds"):
                continue
            pr = a["is_pareto_optimal"]
            L.append(f"| {label} | {_ms(a['joint_value_recovered'])} | {_ms(a['integrative_gain'])} | "
                     f"{_ms(a['focus_gain_over_split'])} | {pr['mean']:.0%} | "
                     f"{a['calls'].get('mean', 0):.0f} |" if pr.get("n") else
                     f"| {label} | n/a | n/a | n/a | n/a | {a['calls'].get('mean', 0):.0f} |")
        calc = c["baselines"].get("calculator, ±20% guess of their weights", {}).get("summary", {})
        b2 = agg.get("B2", {}).get("joint_value_recovered", {})
        if calc and b2.get("n"):
            gap = b2["mean"] - calc["joint_value_recovered"]["mean"]
            L += ["", f"**Reading:** B2 recovers {b2['mean']:.3f} of the available joint value against "
                      f"{calc['joint_value_recovered']['mean']:.3f} for the calculator working from a ±20% "
                      f"guess ({'+' if gap >= 0 else ''}{gap:.3f}). "
                      + ("The debate does better than arithmetic on a close guess." if gap > 0.005 else
                         "With a close guess, arithmetic alone does as well or better; compare B2 with the "
                         "worse guesses above, which are closer to what a negotiator really knows, and "
                         "remember the debate's other output is the rehearsal itself (the arguments, "
                         "objections and red lines).")]
        L.append("")

    L += ["## 2. Main comparison (paper §5.5, Table 9)", "",
          "| Comparison | Mean Δ ± SD | B2 better in |", "|---|---|---|"]
    for c in result["cases"]:
        for name, d in c["aggregates"]["comparisons"].items():
            L.append(f"| {c['scenario_id']} · {name} | {_ms(d['delta'])} | {d['B2_better_in']} |")
    L += ["", "B2 spends more calls than B0, so a B2 win reads *structure or budget* unless B2 also "
              "beats B1, which spends nearly as many.", ""]

    L += ["## 3. Round-count ablation (paper §5.1, Table 4)", "",
          "| Case | Rounds | Joint recovered | Position similarity R→R+1 (lower = more change) | "
          "Issues moved per turn | Novel argument rate | Role consistency by round | "
          "False consensus | Tokens | Seconds |", "|---|---|---|---|---|---|---|---|---|---|"]
    for c in result["cases"]:
        for label, rounds in (("B2 · 1 round", 1), ("B2 · 2 rounds", 2), ("B2", 3)):
            runs = [r for r in c["runs"] if r["label"] == label and "dynamics" in r]
            if not runs:
                continue
            a = c["aggregates"]["by_label"][label]
            dyn = [r["dynamics"] for r in runs]
            sim = summary([d["position_similarity_between_rounds"].get("mean") for d in dyn
                           if d["position_similarity_between_rounds"].get("n")])
            mv = summary([d["issues_moved_between_rounds"].get("mean") for d in dyn
                          if d["issues_moved_between_rounds"].get("n")])
            nov = summary([d.get("novel_argument_rate") for d in dyn])
            role = dyn[0].get("role_consistency_by_round", {})
            fc = sum(bool(d.get("false_consensus")) for d in dyn)
            L.append(f"| {c['scenario_id']} | {rounds} | {_ms(a['joint_value_recovered'])} | {_ms(sim)} | "
                     f"{_ms(mv, 2)} | {_ms(nov)} | {', '.join(f'R{k} {v}' for k, v in role.items()) or 'n/a'} | "
                     f"{fc}/{len(dyn)} | {_ms(a['tokens'], 0)} | {_ms(a['seconds'], 1)} |")
    L += ["", "Marginal improvement = the change in joint value recovered from one row to the next.", ""]

    L += ["## 4. Debater evaluation (paper §5.2, Table 5)", "",
          "Scored 1 to 5 by an evaluator model, averaged over rounds and repeats.", "",
          "| Case | Condition | " + " | ".join(c.replace("_", " ") for c in DEBATER) + " |",
          "|---|---|" + "---|" * len(DEBATER)]
    for c in result["cases"]:
        for label in ("B1", "B2", "B2 · full information"):
            turns = [t for r in c["runs"] if r["label"] == label for t in r.get("debater_rubric", [])]
            if turns:
                L.append(f"| {c['scenario_id']} | {label} | " + " | ".join(
                    _ms(summary([t[k] for t in turns]), 2) for k in DEBATER) + " |")
    L.append("")

    L += ["## 5. Judge (paper §5.3, Tables 6 and 7)", ""]
    for c in result["cases"]:
        j = c["judge"]
        L.append(f"**{c['scenario_id']}**")
        rj = j.get("repeated_judgment") or {}
        if rj:
            L.append(f"- Repeated judgment: {json.dumps(rj)}")
        if j.get("order_swap") is not None:
            L.append(f"- Order swap (score change when the arguments are presented in reverse): "
                     f"{json.dumps(j['order_swap'])}")
        if j.get("label_swap_mean_abs_diff") is not None:
            L.append(f"- Country-label swap: mean absolute score change {j['label_swap_mean_abs_diff']} "
                     f"(0 = the judge ignores who is speaking)")
        v = j.get("validity", {})
        if "spearman_rho" in v:
            L.append(f"- Validity: Spearman ρ = {v['spearman_rho']} between rubric total and the joint "
                     f"value of the proposed package ({v.get('reads_as', '')}, n = {v['n']})")
        fc = j.get("fact_checking")
        if fc:
            fmt = lambda x: "n/a" if x is None else f"{x:.2f}"
            L.append(f"- Fact-checking against the evaluator: precision {fmt(fc['precision'])}, recall "
                     f"{fmt(fc['recall'])}, F1 {fmt(fc['f1'])} (reference: {fc['reference']})")
        L.append("")
    L += ["Score agreement and ranking agreement with independent reference scores need human "
          "reference scores: fill in `human_eval_sheet.csv`.", ""]

    L += ["## 6. Recommender (paper §5.4)", "",
          "| Case | Condition | " + " | ".join(c.replace("_", " ") for c in RECOMMENDER) +
          " | Overall | Distinct options |", "|---|---|" + "---|" * (len(RECOMMENDER) + 2)]
    for c in result["cases"]:
        for label in ("B0", "B1", "B2"):
            rubs = [r["recommender_rubric"] for r in c["runs"] if r["label"] == label and r.get("recommender_rubric")]
            if rubs:
                dist = sum(x["distinct_options"] for x in rubs) / max(1, sum(x["options"] for x in rubs))
                L.append(f"| {c['scenario_id']} | {label} | " + " | ".join(
                    _ms(summary([x[k] for x in rubs]), 2) for k in RECOMMENDER) +
                    f" | {_ms(summary([statistics.mean(x[k] for k in RECOMMENDER) for x in rubs]), 2)} | {dist:.0%} |")
    L.append("")

    L += ["## 7. Repeated runs and aggregates (paper §5.6, Table 10)", "",
          "| Scope | Condition | Joint recovered (mean ± SD) | CV | Failure rate | Leaks (P0) |",
          "|---|---|---|---|---|---|"]
    for scope, agg in [(c["scenario_id"], c["aggregates"]) for c in result["cases"]] + [("overall", result["overall"])]:
        for label in ("B0", "B1", "B2"):
            a = agg["by_label"].get(label)
            if a:
                j = a["joint_value_recovered"]
                L.append(f"| {scope} | {label} | {_ms(j)} | {j.get('cv', 'n/a')} | "
                         f"{a['failure_rate']:.0%} | {a['leaks']} |")
    sens = result["overall"].get("scenario_sensitivity", {})
    L += ["", "Scenario sensitivity (joint value recovered per scenario, and the range): "
          + "; ".join(f"{k}: {v['per_scenario']} (range {v['range']})" for k, v in sens.items()
                      if k in ("B0", "B1", "B2")), "",
          "A failed run is one with an illegal package, a debater scored ≤ 2 on role consistency, "
          "or a recommendation scored ≤ 2 on feasibility.", ""]

    L += ["## 8. Contamination", ""]
    for c in result["cases"]:
        p = c.get("contamination_probes") or {}
        leaks = sum(r.get("leak_scan", {}).get("leaks", 0) for r in c["runs"])
        L.append(f"- **{c['scenario_id']}**: P0 leak scan found {leaks} post-cutoff reference(s) across "
                 f"{len(c['runs'])} runs."
                 + (f" P1 free recall: {'recalls post-cutoff events' if p['P1_free_recall']['recalls_post_cutoff_events'] else 'no recall detected'}."
                    if "P1_free_recall" in p else "")
                 + (f" P3 discrimination: {p['P3_discrimination']['reads_as']}." if "P3_discrimination" in p else ""))
    L += ["", "## 9. Limitations of this evaluation", "",
          "- Position change uses lexical cosine similarity, not sentence embeddings.",
          "- The evaluator is from the same model family as the agents; check it against the human sheet.",
          "- The fact-checking reference is the evaluator, not human annotation.",
          "- Two cases only; results may not generalise to other crises or regions.", ""]
    return "\n".join(L)


def write_human_sheet(result: Dict[str, Any], path: Path) -> int:
    """Paper §5.2 and §5.4: a subset for people to score with the same rubric.
    Repeat 1 of B2 per case: every turn, plus each condition's recommendation."""
    rows = []
    for c in result["cases"]:
        for r in c["runs"]:
            if r.get("repeat") != 0:
                continue
            ev = {(t["round_index"], t["country"]): t for t in r.get("debater_rubric", [])}
            if r["label"] == "B2":
                for i, rnd in enumerate(r.get("transcript", []), start=1):
                    for a in rnd:
                        e = ev.get((i, a["country"]), {})
                        rows.append({"case": c["scenario_id"], "condition": r["label"], "item": "debater turn",
                                     "round": i, "country": a["country"], "text": a["position"],
                                     **{f"evaluator_{k}": e.get(k, "") for k in DEBATER},
                                     **{f"human_{k}": "" for k in DEBATER}})
            if r["label"] in ("B0", "B1", "B2") and r.get("recommender_rubric"):
                rec = r["recommendation"]
                rows.append({"case": c["scenario_id"], "condition": r["label"], "item": "recommendation",
                             "round": "", "country": "", "text": f"{rec['headline']}: {rec['rationale']}",
                             **{f"evaluator_{k}": r["recommender_rubric"][k] for k in RECOMMENDER},
                             **{f"human_{k}": "" for k in RECOMMENDER}})
    if not rows:
        return 0
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def human_agreement(path: Path) -> Dict[str, Any]:
    """Once people have filled the sheet in: how far the evaluator is from them."""
    pairs: Dict[str, List[Tuple[float, float]]] = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            for key, val in row.items():
                if key.startswith("human_") and val.strip():
                    crit = key[len("human_"):]
                    ev = row.get(f"evaluator_{crit}", "")
                    if ev.strip():
                        pairs.setdefault(crit, []).append((float(ev), float(val)))
    out = {}
    for crit, ps in pairs.items():
        mae = statistics.mean(abs(a - b) for a, b in ps)
        rho = metrics.spearman([a for a, _ in ps], [b for _, b in ps]) if len(ps) >= 3 else None
        out[crit] = {"n": len(ps), "mae": round(mae, 3), "spearman": rho}
    return out


def run_evaluation(cases: Sequence[str], factory: BackendFactory, *, repeats: int = 3,
                   judge_repeats: int = 3, include_rounds: bool = True,
                   include_full_info: bool = True, evaluator: bool = True,
                   include_probes: bool = True, mode: str = "",
                   out_dir: Path = Path("results"), log: Callable[[str], None] = print) -> Dict[str, Any]:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    tag = "live" if mode == "live" else "dryrun" if "DRY RUN" in mode else "test"
    folder = out_dir / f"eval_{stamp}_{tag}"
    folder.mkdir(parents=True, exist_ok=True)
    results = [run_case(c, factory, repeats=repeats, judge_repeats=judge_repeats,
                        include_rounds=include_rounds, include_full_info=include_full_info,
                        evaluator=evaluator, include_probes=include_probes, log=log,
                        checkpoint=folder / "runs_so_far.jsonl")
               for c in cases]
    rows = [r for c in results for r in c["runs"]]
    result = {
        "mode": mode, "repeats": repeats, "cases": results, "overall": overall(results),
        "calls_spent": sum(r.get("calls", 0) for r in rows)
                       + sum(c["side_usage"]["calls"] for c in results),
        "tokens_spent": sum(r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in rows)
                        + sum(c["side_usage"]["input"] + c["side_usage"]["output"] for c in results),
        "written": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (folder / "results.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (folder / "report.md").write_text(write_report(result), encoding="utf-8")
    n = write_human_sheet(result, folder / "human_eval_sheet.csv")
    result["folder"] = str(folder)
    result["human_rows"] = n
    return result
