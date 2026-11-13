"""Offline experiments for the final report: no model calls, fully reproducible.

    python experiments/offline_experiments.py      ->  experiments/results/offline.json

O1  payoff structure of each study case (frontier, split, history)
O2  non-language baselines (oracle, Nash calculator on a guess, split, demand all)
O3  rehearsal world model: how many imagined worlds are enough (stability of
    the top opening across seeds)
O4  rehearsal world model: does planning on a GUESS of the other side still
    land well against the TRUE other side? (guess error swept, plus a guess
    that gets their priority order wrong)
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crisis_debate import backtest, evaluation, imagine, scenario as scenario_mod, stress  # noqa: E402
from crisis_debate.utilities import (  # noqa: E402
    Frontier, all_packages, score_package, split_the_difference, utility, utility_vector)

CASES = ("haze_real", "covid_my_2020")
OUT = Path(__file__).resolve().parent / "results"


def payoff_structure(s):
    f = Frontier.build(s)
    split = split_the_difference(s)
    sv = utility_vector(s, split)
    focus = s.focus_country
    out = {"packages": len(f.packages), "pareto": len(f.pareto_indices),
           "max_joint": round(f.max_joint, 4), "split_joint": round(sum(sv.values()), 4),
           "split_recovered": round(sum(sv.values()) / f.max_joint, 4),
           "focus_split": round(sv[focus], 4),
           "focus_best": round(max(v[focus] for v in f.vectors), 4)}
    if backtest.has_ground_truth(s.scenario_id):
        t = backtest.load(s.scenario_id)
        out["history"] = score_package(f, t.settlement)
        out["history_package"] = t.settlement
    return out


def true_response_share(s, model, opening_idx):
    """Where the focus country lands if the TRUE counterpart answers (spread 0)."""
    truth = imagine.WorldModel.build(s, samples=1, spread=0.0, seed=0)
    w = truth.worlds[0]
    landed = imagine._respond(truth, w, opening_idx)
    if landed is None:
        return None, None
    pkg = truth.packages[landed]
    f = Frontier.build(s)
    return round(truth.mine[landed] / truth.my_best, 4), score_package(f, pkg)["joint_value_recovered"]


def top_opening(s, samples, spread, seed, guess=None):
    base = guess if guess is not None else s
    m = imagine.WorldModel.build(base, samples=samples, spread=spread, seed=seed)
    r = imagine.plan(m, top=1)
    return (m.index(r[0].opening) if r else None), m


def world_count_stability(s, ks=(1, 2, 4, 8, 16, 32, 64, 128, 256), seeds=30):
    ref, _ = top_opening(s, 2048, 0.2, 999)
    rows = []
    for k in ks:
        picks = [top_opening(s, k, 0.2, seed)[0] for seed in range(seeds)]
        modal = max(set(picks), key=picks.count)
        rows.append({"worlds": k, "agree_with_reference": round(sum(p == ref for p in picks) / seeds, 4),
                     "modal_share": round(picks.count(modal) / seeds, 4),
                     "distinct_picks": len(set(picks))})
    return {"reference_worlds": 2048, "seeds": seeds, "rows": rows,
            "reference_opening": all_packages(s)[ref] if ref is not None else None}


def guess_robustness(s, errors=(0.0, 0.2, 0.4, 0.6), draws=40):
    """Plan on a guess of the other side, then meet the true other side."""
    rng = random.Random(4213)
    me = s.brief(s.focus_country)
    pk = all_packages(s)
    mine = [utility(me, p) for p in pk]
    best = max(mine)
    ask_all = max(range(len(pk)), key=lambda i: mine[i])
    rows = []

    def evaluate(make_guess, label):
        shares, joints = [], []
        for d in range(draws):
            g = make_guess()
            o, _ = top_opening(s, 64, 0.2, d, guess=g)
            sh, jv = true_response_share(s, None, o)
            shares.append(sh if sh is not None else 0.0)
            joints.append(jv if jv is not None else 0.0)
        rows.append({"guess": label, "true_share_mean": round(statistics.mean(shares), 4),
                     "true_share_min": round(min(shares), 4),
                     "true_joint_mean": round(statistics.mean(joints), 4)})

    for e in errors:
        evaluate((lambda e=e: stress.perturb_opponent_weights(s, e, rng)) if e else (lambda: s),
                 f"±{int(e * 100)}% weight error")

    def shuffled():
        g = s.model_copy(deep=True)
        for c in g.countries:
            if c.name != s.focus_country:
                ws = [v.weight for v in c.valuations]
                rng.shuffle(ws)
                for v, w in zip(c.valuations, ws):
                    v.weight = w
        return g
    evaluate(shuffled, "priority order unknown (shuffled)")

    # reference openings, met by the true counterpart
    refs = {}
    for label, idx in (("ask for everything", ask_all),
                       ("split the difference", pk.index(split_the_difference(s)))):
        sh, jv = true_response_share(s, None, idx)
        refs[label] = {"true_share": sh, "true_joint": jv}
    return {"rows": rows, "references": refs, "draws": draws, "focus_best": round(best, 4)}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    out = {}
    for c in CASES:
        s = scenario_mod.load(c)
        print(f"{c}: payoff structure", flush=True)
        o = {"payoff": payoff_structure(s)}
        print(f"{c}: non-language baselines", flush=True)
        o["baselines"] = evaluation.non_language_baselines(s)
        print(f"{c}: world-count stability", flush=True)
        o["world_count"] = world_count_stability(s)
        print(f"{c}: guess robustness", flush=True)
        o["guess_robustness"] = guess_robustness(s)
        out[c] = o
    path = OUT / "offline.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
