"""The research question, answered in one command (CONTRACT.md §13.4–§13.6).

> On a multi-issue crisis negotiation with private valuations, does a structured
> debate with an independent judge (B2) recover more of the available joint
> value than a single model call (B0)?

Everything a reported number needs is produced together, so no number can be
quoted without the things that qualify it:

* **Every condition is scored on the declared pair** — `pipeline.rq_recommendation`
  (policymaker × no stated priority), never on list order (§14).
* **Both pre-declared primaries** (§13.5): joint value recovered, for the RQ, and
  the focus country's gain over split-the-difference, for the product. Reported
  side by side because they can disagree and choosing afterwards is choosing the
  answer.
* **Call counts beside every comparison.** B2 spends more calls than B0, so a B2
  win reads "structure or budget"; B1 is the internal discriminator (§1).
* **Judge reliability before any between-condition claim** (§13.4): self-consistency
  over k re-scorings, position bias with argument order swapped, and validity —
  Spearman between the judge's rubric total and the joint value of the package
  that argument proposed.
* **Stage 11 sensitivity** (§13.6): each recommended package stressed under ±20% on
  the opponent's weights, with a verdict.
* **History, relatively** (§10): agreement with the real settlement across
  conditions, carrying the contamination flag.

The product features (`imagine`, `intake`) are not used here. That is what keeps
them from touching the measurement (§15.4).
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from crisis_debate import backtest, metrics, pipeline, scenario as scenario_mod, situational, stress
from crisis_debate.agents import JudgeAgent, debate_call_count
from crisis_debate.schemas import Scenario
from crisis_debate.utilities import Frontier, score_package, utility_vector

#: The study set. User-created scenarios are never part of it.
STUDY_CASES = ("haze_real", "covid_my_2020")

#: The two pre-declared primaries, and the secondaries reported with them.
PRIMARIES = ("joint_value_recovered", "focus_gain_over_split")
SECONDARIES = ("integrative_gain", "nash_ratio", "is_pareto_optimal")

BackendFactory = Callable[[Scenario, str, int], Any]


def planned_calls(scenario: Scenario, *, rounds: int, repeats: int,
                  judge_repeats: int) -> Dict[str, int]:
    """Model calls the experiment will make for one case — shown before spending."""
    per = {
        "B0": 1,
        "B1": debate_call_count(scenario, rounds, use_judge=False, recommendations=1),
        "B2": debate_call_count(scenario, rounds, use_judge=True, recommendations=1),
    }
    out = {c: n * repeats for c, n in per.items()}
    out["judge_checks"] = judge_repeats + 1              # k re-scorings + one swapped
    out["total"] = sum(out.values())
    return out


def _summary(values: Sequence[float]) -> Dict[str, float]:
    vals = [float(v) for v in values]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 4),
        "sd": round(statistics.stdev(vals), 4) if len(vals) > 1 else 0.0,
        "min": round(min(vals), 4),
        "max": round(max(vals), 4),
    }


def _judge_validity_pairs(scenario: Scenario, run) -> List[tuple]:
    """(judge total, joint utility of the package that argument proposed)."""
    pairs = []
    for args, rj in zip(run.rounds, run.judgments):
        for a in args:
            try:
                total = rj.for_country(a.country).total()
            except KeyError:
                continue
            pkg = a.package()
            pairs.append((float(total), sum(utility_vector(scenario, pkg).values())))
    return pairs


def _judge_reliability(scenario: Scenario, backend, run, k: int) -> Dict[str, Any]:
    """Re-judge round 1 of one B2 run k times, and once with the order swapped."""
    if not run.rounds:
        return {}
    judge = JudgeAgent(backend=backend)
    first = run.rounds[0]
    repeats = [judge.judge(scenario, 1, first) for _ in range(k)]
    swapped = judge.judge(scenario, 1, list(reversed(first)))
    bias = metrics.position_bias(repeats[0], swapped)
    return {
        "self_consistency": metrics.judge_self_consistency(repeats) if k >= 2 else {},
        "position_bias": bias,
        "position_bias_nonzero": any(d != 0 for c in bias.values() for d in c.values()),
    }


def run_case(scenario: Scenario, factory: BackendFactory, *, rounds: int = 3,
             repeats: int = 3, judge_repeats: int = 3,
             log_dir: Optional[Path] = None,
             conditions: Optional[situational.ConditionsSnapshot] = None) -> Dict[str, Any]:
    """Every condition, `repeats` times, on one case. `conditions` is the case's
    pinned snapshot: every condition and repeat reads the same world."""
    frontier = Frontier.build(scenario)
    truth = (backtest.load(scenario.scenario_id)
             if backtest.has_ground_truth(scenario.scenario_id) else None)

    rows: List[Dict[str, Any]] = []
    validity_pairs: List[tuple] = []
    reliability: Dict[str, Any] = {}

    for condition in pipeline.CONDITIONS:
        for rep in range(repeats):
            backend = factory(scenario, condition, rep)
            log = (log_dir / f"{scenario.scenario_id}_{condition}_{rep}.jsonl") if log_dir else None
            run = pipeline.run(scenario, backend, condition=condition, rounds=rounds,
                               priorities=(pipeline.RQ_PRIORITY,), log_path=log,
                               conditions=conditions)
            rec = pipeline.rq_recommendation(run)
            pkg = rec.package()
            row: Dict[str, Any] = {
                "condition": condition, "repeat": rep,
                "calls": int(getattr(backend, "usage", {}).get("calls", 0)),
                "illegal": len(run.illegal_packages),
                "conditions_id": run.conditions_id,
                "package": pkg,
            }
            if run.illegal_packages:
                row["scored"] = False          # recorded, never coerced (invariant 4)
            else:
                row.update(score_package(frontier, pkg), scored=True)
                band = stress.stress(scenario, pkg)
                row["stress"] = {"lo": band.joint_recovered_lo, "hi": band.joint_recovered_hi,
                                 "pareto_rate": band.pareto_rate, "verdict": band.verdict}
                if truth is not None:
                    h = backtest.compare(scenario, pkg, truth, frontier=frontier)
                    row["history"] = {k: h.get(k) for k in
                                      ("issue_agreement", "beat_history", "contaminated")}
            rows.append(row)

            if condition == "B2":
                validity_pairs += _judge_validity_pairs(scenario, run)
                if rep == 0 and judge_repeats:
                    reliability = _judge_reliability(
                        scenario, factory(scenario, "judge_check", 0), run, judge_repeats)

    by_condition: Dict[str, Any] = {}
    for condition in pipeline.CONDITIONS:
        mine = [r for r in rows if r["condition"] == condition]
        scored = [r for r in mine if r.get("scored")]
        by_condition[condition] = {
            "runs": len(mine),
            "unscored_illegal": len(mine) - len(scored),
            "calls_per_run": _summary([r["calls"] for r in mine]),
            **{m: _summary([r[m] for r in scored]) for m in PRIMARIES + SECONDARIES},
            "history_agreement": _summary([r["history"]["issue_agreement"]
                                           for r in scored if "history" in r]),
        }

    def delta(a: str, b: str, m: str) -> Optional[float]:
        x, y = by_condition[a][m], by_condition[b][m]
        return round(x["mean"] - y["mean"], 4) if x.get("n") and y.get("n") else None

    return {
        "scenario_id": scenario.scenario_id,
        "contaminated": True,                  # docs/CONTAMINATION.md, until extraction lands
        "by_condition": by_condition,
        "comparisons": {
            f"{a}-{b}": {m: delta(a, b, m) for m in PRIMARIES}
            for a, b in (("B2", "B0"), ("B2", "B1"), ("B1", "B0"))
        },
        "judge": {
            "validity": (metrics.judge_validity(validity_pairs)
                         if len(validity_pairs) >= 3 else {"n": len(validity_pairs)}),
            **reliability,
        },
        "runs": rows,
    }


def run_experiment(cases: Sequence[str], factory: BackendFactory, *, rounds: int = 3,
                   repeats: int = 3, judge_repeats: int = 3, mode: str = "",
                   out_dir: Optional[Path] = None) -> Dict[str, Any]:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    log_dir = (out_dir / f"logs_{stamp}") if out_dir else None
    result = {
        "research_question": "Does B2 recover more joint value than B0?",
        "mode": mode, "rounds": rounds, "repeats": repeats,
        "rq_pair": [pipeline.RQ_AUDIENCE, pipeline.RQ_PRIORITY],
        "primaries": list(PRIMARIES),
        "cases": [run_case(scenario_mod.load(c), factory, rounds=rounds, repeats=repeats,
                           judge_repeats=judge_repeats, log_dir=log_dir,
                           conditions=situational.load_snapshot(c)) for c in cases],
    }
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"experiment_{stamp}.json"
        path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        result["written_to"] = str(path)
    return result


def report(result: Dict[str, Any]) -> List[str]:
    """The results table, with its qualifiers attached."""
    lines = [f"Research question: {result['research_question']}  "
             f"[{result['mode']}, {result['repeats']} repeats, {result['rounds']} rounds]"]
    for case in result["cases"]:
        lines.append(f"\n{case['scenario_id']}  (issue set contaminated: results are "
                     f"illustrative — docs/CONTAMINATION.md)")
        lines.append(f"  {'cond':<5}{'joint recovered':>22}{'focus gain/split':>22}"
                     f"{'calls':>8}{'history agr.':>14}")
        for cond, s in case["by_condition"].items():
            j, f = s["joint_value_recovered"], s["focus_gain_over_split"]
            h = s["history_agreement"]
            fmt = lambda x: f"{x['mean']:.3f} ± {x['sd']:.3f}" if x.get("n") else "—"
            lines.append(f"  {cond:<5}{fmt(j):>22}{fmt(f):>22}"
                         f"{s['calls_per_run'].get('mean', 0):>8.0f}"
                         f"{(h['mean'] if h.get('n') else float('nan')):>14.2f}")
            if s["unscored_illegal"]:
                lines.append(f"       !! {s['unscored_illegal']} run(s) proposed an illegal "
                             f"package and are not scored")
        for name, d in case["comparisons"].items():
            lines.append(f"  {name}: joint {d['joint_value_recovered']}, "
                         f"focus {d['focus_gain_over_split']}")
        v = case["judge"].get("validity", {})
        if "spearman_rho" in v:
            lines.append(f"  judge validity: rho={v['spearman_rho']} ({v['reads_as']}, n={v['n']})")
        if "position_bias_nonzero" in case["judge"]:
            lines.append("  judge position bias: "
                         + ("PRESENT — revise the rubric before citing B2"
                            if case["judge"]["position_bias_nonzero"] else "none detected"))
    lines.append("\nRead a B2 win as 'structure or budget' unless B2 also beats B1, "
                 "which spends nearly the same number of calls.")
    return lines
