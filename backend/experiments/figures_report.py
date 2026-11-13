"""Figures for the final report, from experiments/results/summary.json + offline.json."""
from __future__ import annotations

import json
import random
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RES = Path(__file__).resolve().parent / "results"
FIG = ROOT.parent / "report" / "figures"
FIG.mkdir(parents=True, exist_ok=True)

ACCENT = "#2a78d6"
GREY = "#8a8985"
LIGHT = "#c9c8c3"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e6e5e0"

plt.rcParams.update({"font.family": "sans-serif", "font.size": 7.5, "axes.edgecolor": LIGHT,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK,
                     "axes.titlesize": 8, "axes.titleweight": "bold"})

CASES = [("haze_real", "Haze (SG–ID)"), ("covid_my_2020", "COVID-19 (SG–MY)")]
METRICS = [("joint_value_recovered", "Joint value recovered"),
           ("focus_gain_over_split", "Singapore gain over split")]
COND = [("B0", "B0  single call"), ("B1", "B1  debate, no judge"), ("B2", "B2  full system"),
        ("B2 · 1 round", "B2, 1 round"), ("B2 · 2 rounds", "B2, 2 rounds"),
        ("B2 · no final offers", "B2, no final offers"),
        ("B2 · full information", "B2, full information")]
REFS = [("split the difference", "Split the difference"),
        ("history", "Actual outcome (history)"),
        ("demand everything", "Demand everything"),
        ("calculator, ±40% guess of their weights", "Nash calculator, ±40% guess")]


def ref_value(off, case, key, metric):
    if key == "history":
        return off[case]["payoff"]["history"][metric]
    b = off[case]["baselines"][key]
    return b["single"][metric] if "single" in b else b["summary"][metric]["mean"]


def forest():
    summ = json.loads((RES / "summary.json").read_text())
    off = json.loads((RES / "offline.json").read_text())
    rows = REFS + [("", "")] + COND
    ys = list(range(len(rows)))[::-1]
    fig, axes = plt.subplots(1, 4, figsize=(6.9, 2.75), sharey=True)
    rng = random.Random(0)
    for ci, (case, ctitle) in enumerate(CASES):
        for mi, (metric, mtitle) in enumerate(METRICS):
            ax = axes[mi * 2 + ci]
            ax.set_title(f"{ctitle}", loc="left", fontsize=7.5)
            ax.set_xlabel(mtitle, fontsize=7)
            ax.grid(axis="x", color=GRID, lw=0.6)
            ax.set_axisbelow(True)
            for side in ("top", "right", "left"):
                ax.spines[side].set_visible(False)
            ax.tick_params(axis="y", length=0)
            for (key, label), y in zip(rows, ys):
                if not key:
                    ax.axhline(y, color=LIGHT, lw=0.6)
                    continue
                if key in dict(REFS):
                    v = ref_value(off, case, key, metric)
                    ax.plot([v], [y], marker="D", ms=3.6, color=GREY, mfc="white", mew=1.0)
                    continue
                lab = summ.get(case, {}).get("labels", {}).get(key)
                if not lab:
                    continue
                runs = lab["jvr_runs"] if metric == "joint_value_recovered" else lab["focus_runs"]
                col = ACCENT if key == "B2" else INK2
                for v in runs:
                    ax.plot([v], [y + rng.uniform(-0.18, 0.18)], "o", ms=2.2, color=LIGHT, zorder=2)
                m = lab[metric]
                if m.get("n"):
                    ax.errorbar([m["mean"]], [y], xerr=[[m["mean"] - m["min"]], [m["max"] - m["mean"]]],
                                fmt="o", ms=4, color=col, ecolor=col, elinewidth=1.0, capsize=0, zorder=3)
            if metric == "joint_value_recovered":
                ax.set_xlim(0.5, 1.03)
            else:
                ax.axvline(0, color=LIGHT, lw=0.8)
                ax.set_xlim(-0.32, 0.52)
    axes[0].set_yticks(ys)
    axes[0].set_yticklabels([r[1] for r in rows])
    for t, (key, _) in zip(axes[0].get_yticklabels(), rows):
        if key in dict(REFS):
            t.set_color(INK2); t.set_fontstyle("italic")
        if key == "B2":
            t.set_fontweight("bold")
    fig.tight_layout(w_pad=0.6)
    fig.savefig(FIG / "main_forest.png", dpi=300, bbox_inches="tight")
    print("wrote main_forest.png")


if __name__ == "__main__":
    forest()
