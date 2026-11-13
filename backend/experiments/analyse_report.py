"""Aggregate experiments/results/*.json into the numbers and figures the report cites.

    python experiments/analyse_report.py   ->  experiments/results/summary.json
                                           ../report/figures/*.pdf
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crisis_debate import backtest, metrics, scenario as scenario_mod, stress  # noqa: E402
from crisis_debate.utilities import Frontier, score_package  # noqa: E402

RES = Path(__file__).resolve().parent / "results"
FIG = ROOT.parent / "report" / "figures"
CASES = {"haze_real": "haze_real_v1", "covid_my_2020": "covid_my_2020_v1"}
LABELS = ["B0", "B1", "B2", "B2 · 1 round", "B2 · 2 rounds",
          "B2 · no final offers", "B2 · full information"]


def ms(xs, d=3):
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean": round(st.mean(xs), d),
            "sd": round(st.stdev(xs), d) if len(xs) > 1 else 0.0,
            "min": round(min(xs), d), "max": round(max(xs), d)}


def load_case(case):
    reps = sorted(RES.glob(f"{case}_rep*.json"))
    reps = [p for p in reps if "checkpoint" not in p.name]
    return [json.loads(p.read_text()) for p in reps]


def main():
    out = {}
    for case, sid in CASES.items():
        files = load_case(case)
        if not files:
            continue
        s = scenario_mod.load(case)
        f = Frontier.build(s)
        truth = backtest.load(sid) if backtest.has_ground_truth(sid) else None
        rows = [r for fl in files for r in fl["runs"]]
        c = {"files": len(files), "labels": {}}
        for lab in LABELS:
            mine = [r for r in rows if r.get("label") == lab]
            if not mine:
                continue
            ok = [r for r in mine if "outcome" in r]
            stress_lo, stress_hi, pareto_rate, hist = [], [], [], []
            for r in ok:
                b = stress.stress(s, r["package"])
                stress_lo.append(b.joint_recovered_lo)
                stress_hi.append(b.joint_recovered_hi)
                pareto_rate.append(b.pareto_rate)
                if truth is not None:
                    h = backtest.compare(s, r["package"], truth, frontier=f)
                    hist.append(h.get("issue_agreement"))
            dyn = [r.get("dynamics", {}) for r in mine if r.get("dynamics")]
            agree = defaultdict(list)
            for d in dyn:
                for i, a in enumerate(d.get("package_agreement_by_round", []), 1):
                    agree[i].append(a)
            role = defaultdict(list)
            for d in dyn:
                for k, v in (d.get("role_consistency_by_round") or {}).items():
                    role[int(k)].append(v)
            c["labels"][lab] = {
                "runs": len(mine),
                "failed": sum(1 for r in mine if r.get("failed")),
                "illegal": sum(1 for r in mine if r.get("illegal")),
                **{m: ms([r["outcome"][m] for r in ok]) for m in
                   ("joint_value_recovered", "integrative_gain", "focus_gain_over_split",
                    "nash_ratio", "is_pareto_optimal")},
                "recommender_overall": ms([r.get("recommender_overall") for r in mine]),
                "debater_overall": ms([r.get("debater_overall") for r in mine]),
                "calls": ms([r.get("calls") for r in mine], 1),
                "tokens": ms([(r.get("tokens_in", 0) + r.get("tokens_out", 0)) for r in mine], 0),
                "tokens_out": ms([r.get("tokens_out", 0) for r in mine], 0),
                "seconds": ms([r.get("seconds") for r in mine], 1),
                "stress_lo": ms(stress_lo), "stress_hi": ms(stress_hi),
                "stress_pareto_rate": ms(pareto_rate),
                "history_agreement": ms(hist),
                "package_agreement_by_round": {k: ms(v) for k, v in sorted(agree.items())},
                "role_consistency_by_round": {k: ms(v) for k, v in sorted(role.items())},
                "issues_moved": ms([d.get("issues_moved_between_rounds", {}).get("mean") for d in dyn]),
                "novel_argument_rate": ms([d.get("novel_argument_rate") for d in dyn]),
                "false_consensus": sum(1 for d in dyn if d.get("false_consensus")),
                "leaks": sum(r.get("leak_scan", {}).get("leaks", 0) for r in mine),
                "packages": [r.get("package") for r in mine],
                "jvr_runs": [r["outcome"]["joint_value_recovered"] for r in ok],
                "focus_runs": [r["outcome"]["focus_gain_over_split"] for r in ok],
            }
        # paired deltas by repeat
        def by(lab, m):
            return {r["repeat"]: r["outcome"][m] for r in rows if r.get("label") == lab and "outcome" in r}
        comps = {}
        for a, b in (("B2", "B0"), ("B2", "B1"), ("B1", "B0"), ("B2", "B2 · no final offers"),
                     ("B2 · full information", "B2")):
            for m in ("joint_value_recovered", "focus_gain_over_split"):
                x, y = by(a, m), by(b, m)
                d = [x[k] - y[k] for k in x if k in y]
                comps[f"{a} - {b} · {m}"] = {**ms(d), "a_better": sum(v > 1e-9 for v in d),
                                             "ties": sum(abs(v) <= 1e-9 for v in d)}
        c["comparisons"] = comps
        # judge
        jt = [fl["judge"] for fl in files]
        mads, exact, order, label = [], [], [], []
        for j in jt:
            rep = j.get("repeated_judgment") or {}
            for country, stats in rep.items():
                mads += [v for k, v in stats.items() if k.endswith("_mad")]
                exact += [v for k, v in stats.items() if k.endswith("_exact_agreement")]
            for country, deltas in (j.get("order_swap") or {}).items():
                order += list(deltas.values())
            if j.get("label_swap_mean_abs_diff") is not None:
                label.append(j["label_swap_mean_abs_diff"])
        pairs = []
        for fl in files:
            for r in fl["runs"]:
                if r.get("label") != "B2" or not r.get("judgments"):
                    continue
                for rnd, rj in zip(r["transcript"], r["judgments"]):
                    for a in rnd:
                        tot = next((sum(x["score"] for x in jj["scores"]) for jj in rj["judgments"]
                                    if jj["country"] == a["country"]), None)
                        if tot is None:
                            continue
                        pkg = {p["issue"]: p["option"] for p in a["proposed_package"]}
                        try:
                            u = score_package(f, pkg)
                        except Exception:
                            continue
                        pairs.append((tot, u["joint_utility"], u[f"utility_{a['country']}"]))
        fc = [fl["judge"].get("fact_checking") for fl in files if fl["judge"].get("fact_checking")]
        tp = sum(x["tp"] for x in fc); fp = sum(x["fp"] for x in fc); fn = sum(x["fn"] for x in fc)
        c["judge"] = {
            "repeat_mad": ms(mads), "repeat_exact_agreement": ms(exact),
            "order_swap_abs": ms([abs(x) for x in order]), "order_swap_nonzero": sum(1 for x in order if x),
            "order_swap_n": len(order), "order_swap_signed": ms(order),
            "label_swap_mean_abs_diff": ms(label),
            "validity_joint": metrics.judge_validity([(a, b) for a, b, _ in pairs]) if len(pairs) >= 3 else None,
            "validity_own": metrics.judge_validity([(a, c_) for a, _, c_ in pairs]) if len(pairs) >= 3 else None,
            "fact_check": {"tp": tp, "fp": fp, "fn": fn,
                           "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                           "recall": round(tp / (tp + fn), 3) if tp + fn else None},
        }
        c["probes"] = [fl.get("contamination_probes") for fl in files if fl.get("contamination_probes")]
        c["baselines"] = files[0]["baselines"]
        c["side_usage"] = [fl.get("side_usage") for fl in files]
        c["wall_seconds"] = [fl.get("wall_seconds") for fl in files]
        # mirror
        mir = [json.loads(p.read_text()) for p in sorted(RES.glob(f"{case}_mirror_rep*.json"))]
        if mir:
            ag = defaultdict(list)
            for m in mir:
                for i, a in enumerate(m["dynamics"].get("package_agreement_by_round", []), 1):
                    ag[i].append(a)
            c["mirror"] = {"runs": len(mir),
                           "package_agreement_by_round": {k: ms(v) for k, v in sorted(ag.items())},
                           "issues_moved": ms([m["dynamics"].get("issues_moved_between_rounds", {}).get("mean")
                                               for m in mir]),
                           "text_similarity": ms([x for m in mir for x in
                                                  m["dynamics"].get("cross_agent_text_similarity_by_round", [])]),
                           "illegal": sum(1 for m in mir if m["illegal"])}
            normal = [r.get("dynamics", {}) for r in rows if r.get("label") == "B2" and r.get("dynamics")]
            c["mirror"]["normal_text_similarity"] = ms([x for d in normal for x in
                                                        d.get("cross_agent_text_similarity_by_round", [])])
        out[case] = c
    RES.joinpath("summary.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps({k: {l: {m: v[m] for m in ("joint_value_recovered", "focus_gain_over_split",
                                                   "recommender_overall", "calls")}
                              for l, v in c["labels"].items()} for k, c in out.items()}, indent=1))


if __name__ == "__main__":
    main()
