"""LaTeX tables and number macros for the final report.

Reads experiments/results/summary.json, offline.json and the raw rep files, and
writes ../report/generated/{numbers,main_table,ablation_table,judge_table}.tex.
Every number in the report text is a macro from numbers.tex (\\N{key}), so
re-running this after more repeats updates the prose and the tables together.
Standard library only.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = Path(__file__).resolve().parent / "results"
GEN = ROOT.parent / "report" / "generated"
GEN.mkdir(parents=True, exist_ok=True)

CASES = {"haze_real": "hz", "covid_my_2020": "cv"}
SHORT = {"B0": "B0", "B1": "B1", "B2": "B2", "B2 · 1 round": "R1", "B2 · 2 rounds": "R2",
         "B2 · no final offers": "NO", "B2 · full information": "FI"}
CMP = {"B2 - B0": "B2-B0", "B2 - B1": "B2-B1", "B1 - B0": "B1-B0",
       "B2 - B2 · no final offers": "B2-NO", "B2 · full information - B2": "FI-B2"}
MET = {"joint_value_recovered": "jvr", "focus_gain_over_split": "foc"}
BASE = {"split the difference": "split", "demand everything": "demand",
        "calculator, ±20% guess of their weights": "calc20",
        "calculator, ±40% guess of their weights": "calc40",
        "calculator, their priorities unknown (random order)": "rand",
        "calculator, assumes they want the opposite of you": "opp",
        "oracle (needs both true tables)": "oracle"}
MINUS = r"\ensuremath{-}"   # works both in text and inside $...$

macros: dict[str, str] = {}


def f3(x):
    return "--" if x is None else f"{x:.3f}"


def f2(x):
    return "--" if x is None else f"{x:.2f}"


def sg(x, d=3):
    """Signed number; the minus sign is safe in text and in math mode."""
    if x is None:
        return "--"
    s = f"{x:+.{d}f}"
    return s.replace("-", MINUS) if x < 0 else s


def put(key, val):
    macros[key] = str(val)


def main():
    summ = json.loads((RES / "summary.json").read_text())
    off = json.loads((RES / "offline.json").read_text())
    tot_calls = tot_tok = 0
    tot_sec = 0.0
    for case, c in CASES.items():
        s = summ[case]
        put(f"{c}:n", s["labels"]["B2"]["joint_value_recovered"]["n"])
        for lab, sh in SHORT.items():
            L = s["labels"].get(lab)
            if not L:
                continue
            for m, mk in MET.items():
                for st in ("mean", "sd", "min", "max"):
                    v = L[m].get(st)
                    put(f"{c}:{sh}:{mk}:{st}", f3(v) if mk == "jvr" else sg(v) if st == "mean" else f3(v))
            put(f"{c}:{sh}:par", f"{round(100 * L['is_pareto_optimal']['mean'])}")
            put(f"{c}:{sh}:rec", f2(L["recommender_overall"].get("mean")))
            put(f"{c}:{sh}:deb", f2(L["debater_overall"].get("mean")))
            put(f"{c}:{sh}:calls", f"{L['calls']['mean']:.0f}")
            put(f"{c}:{sh}:tok", f"{L['tokens']['mean'] / 1000:.0f}k")
            put(f"{c}:{sh}:sec", f"{L['seconds']['mean']:.0f}")
            put(f"{c}:{sh}:stlo", f3(L["stress_lo"].get("mean")))
            put(f"{c}:{sh}:sthi", f3(L["stress_hi"].get("mean")))
            put(f"{c}:{sh}:hist", f2(L["history_agreement"].get("mean")))
            for k, v in L["package_agreement_by_round"].items():
                put(f"{c}:{sh}:agr{k}", f2(v["mean"]))
            put(f"{c}:{sh}:fc", L["false_consensus"])
            put(f"{c}:{sh}:nov", f2(L["novel_argument_rate"].get("mean")))
            put(f"{c}:{sh}:distinct", len({json.dumps(p, sort_keys=True) for p in L["packages"]}))
        # every variant against B2, paired by repeat (runs are in repeat order)
        b2 = s["labels"]["B2"]
        for lab, sh in SHORT.items():
            L = s["labels"].get(lab)
            if not L or sh == "B2":
                continue
            for mk, key in (("jvr", "jvr_runs"), ("foc", "focus_runs")):
                d = [a - b for a, b in zip(L[key], b2[key])]
                if d:
                    put(f"{c}:vs:{sh}:{mk}", sg(sum(d) / len(d)))
                    put(f"{c}:vs:{sh}:{mk}:wins", f"{sum(x > 1e-9 for x in d)}/{len(d)}")
        for k, ck in CMP.items():
            for m, mk in MET.items():
                d = s["comparisons"][f"{k} · {m}"]
                put(f"{c}:cmp:{ck}:{mk}", sg(d.get("mean")))
                put(f"{c}:cmp:{ck}:{mk}:wins", f"{d['a_better']}/{d['n']}")
                put(f"{c}:cmp:{ck}:{mk}:min", sg(d.get("min")))
                put(f"{c}:cmp:{ck}:{mk}:max", sg(d.get("max")))
        j = s["judge"]
        put(f"{c}:j:exact", f2(j["repeat_exact_agreement"]["mean"]))
        put(f"{c}:j:mad", f2(j["repeat_mad"]["mean"]))
        put(f"{c}:j:order", f2(j["order_swap_abs"]["mean"]))
        put(f"{c}:j:ordernz", f"{j['order_swap_nonzero']}/{j['order_swap_n']}")
        put(f"{c}:j:orderpct", f"{round(100 * j['order_swap_nonzero'] / j['order_swap_n'])}")
        put(f"{c}:j:label", f2(j["label_swap_mean_abs_diff"]["mean"]))
        put(f"{c}:j:vj", sg(j["validity_joint"]["spearman_rho"], 2))
        put(f"{c}:j:vo", sg(j["validity_own"]["spearman_rho"], 2))
        put(f"{c}:j:vn", j["validity_joint"]["n"])
        put(f"{c}:j:fp", f2(j["fact_check"]["precision"]))
        put(f"{c}:j:fr", f2(j["fact_check"]["recall"]))
        m = s.get("mirror", {})
        if m:
            for k, v in m["package_agreement_by_round"].items():
                put(f"{c}:mir:agr{k}", f2(v["mean"]))
            put(f"{c}:mir:text", f2(m["text_similarity"]["mean"]))
            put(f"{c}:mir:normtext", f2(m["normal_text_similarity"]["mean"]))
            put(f"{c}:mir:moved", f2(m["issues_moved"]["mean"]))
            put(f"{c}:mir:n", m["runs"])
        pr = (s.get("probes") or [{}])[0]
        p1 = pr.get("P1_free_recall", {})
        p3 = pr.get("P3_discrimination", {})
        put(f"{c}:p1", "yes" if p1.get("recalls_post_cutoff_events") else "no")
        put(f"{c}:p3conf", f2(p3.get("confidence")))
        put(f"{c}:p3", "correct" if p3.get("correct") else "wrong")
        # offline references
        o = off[case]
        for k, bk in BASE.items():
            x = o["baselines"][k].get("single") or o["baselines"][k].get("summary")
            v = lambda mm: x[mm] if not isinstance(x[mm], dict) else x[mm]["mean"]
            put(f"{c}:base:{bk}:jvr", f3(v("joint_value_recovered")))
            put(f"{c}:base:{bk}:foc", sg(v("focus_gain_over_split")))
            put(f"{c}:base:{bk}:par", f"{round(100 * v('is_pareto_optimal'))}")
        h = o["payoff"]["history"]
        put(f"{c}:base:hist:jvr", f3(h["joint_value_recovered"]))
        put(f"{c}:base:hist:foc", sg(h["focus_gain_over_split"]))
        put(f"{c}:base:hist:par", f"{round(100 * h['is_pareto_optimal'])}")
        put(f"{c}:pareto", o["payoff"]["pareto"])
        # totals (evaluation repeats + mirrored-brief runs)
        for p in sorted(RES.glob(f"{case}_rep*.json")):
            if "checkpoint" in p.name:
                continue
            d = json.loads(p.read_text())
            tot_calls += sum(r.get("calls", 0) for r in d["runs"]) + d["side_usage"]["calls"]
            tot_tok += sum(r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in d["runs"])
            tot_tok += d["side_usage"]["input"] + d["side_usage"]["output"]
            tot_sec += d.get("wall_seconds", 0)
        for p in sorted(RES.glob(f"{case}_mirror_rep*.json")):
            d = json.loads(p.read_text())
            u = d.get("usage", {})
            tot_calls += u.get("calls", 0)
            tot_tok += u.get("input", 0) + u.get("output", 0)
            tot_sec += d.get("seconds", 0)
    put("tot:calls", f"{tot_calls:,}".replace(",", "{,}"))
    put("tot:mtok", f"{tot_tok / 1e6:.1f}")
    put("tot:hours", f"{tot_sec / 3600:.1f}")
    for r in off["haze_real"]["world_count"]["rows"]:
        put(f"hz:wm:k{r['worlds']}", f2(r["agree_with_reference"]))
    gr = off["haze_real"]["guess_robustness"]
    for nm, r in zip(["e0", "e20", "e40", "e60", "shuf"], gr["rows"]):
        put(f"hz:gr:{nm}", f"{round(100 * r['true_share_mean'])}")
        put(f"hz:gr:{nm}:min", f"{round(100 * r['true_share_min'])}")
        put(f"hz:gr:{nm}:jvr", f2(r["true_joint_mean"]))
    put("hz:gr:askall", f"{round(100 * gr['references']['ask for everything']['true_share'])}")
    put("hz:gr:split", f"{round(100 * gr['references']['split the difference']['true_share'])}")

    lines = ["% generated by backend/experiments/tables_report.py -- do not edit", r"\makeatletter"]
    lines += [rf"\@namedef{{N@{k}}}{{{v}}}" for k, v in macros.items()]
    lines += [r"\newcommand{\N}[1]{\@ifundefined{N@#1}{\textbf{??#1}}{\@nameuse{N@#1}}}",
              r"\makeatother"]
    (GEN / "numbers.tex").write_text("\n".join(lines) + "\n")
    tables()
    print(f"{len(macros)} macros; calls={tot_calls} tokens={tot_tok} hours={tot_sec / 3600:.1f}")


def cell(c, sh):
    return (rf"\N{{{c}:{sh}:jvr:mean}} {{\scriptsize$\pm$\N{{{c}:{sh}:jvr:sd}}}} & "
            rf"\N{{{c}:{sh}:foc:mean}} & \N{{{c}:{sh}:par}}")


def bcell(c, bk):
    return rf"\N{{{c}:base:{bk}:jvr}} & \N{{{c}:base:{bk}:foc}} & \N{{{c}:base:{bk}:par}}"


def tables():
    T = [r"\begin{tabular}{@{}l ccc ccc c@{}}", r"\toprule",
         r" & \multicolumn{3}{c}{\textbf{Haze 2015 (SG--ID)}} & \multicolumn{3}{c}{\textbf{COVID-19 2020 (SG--MY)}} & \\",
         r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}",
         r"Method & JVR & $\Delta_\mathrm{SG}$ & Pareto\,\% & JVR & $\Delta_\mathrm{SG}$ & Pareto\,\% & Calls\\",
         r"\midrule",
         r"\multicolumn{8}{@{}l}{\emph{Arithmetic references (no LLM)}}\\"]
    for bk, name in (("split", "Split the difference"), ("demand", "Demand everything"),
                     ("calc40", r"Nash calculator, $\pm$40\% guess"),
                     ("rand", "Nash calculator, random priority order"),
                     ("opp", "Nash calculator, assumes opposite")):
        T.append(rf"\quad {name} & {bcell('hz', bk)} & {bcell('cv', bk)} & 0\\")
    T.append(rf"\quad Actual outcome (history) & {bcell('hz', 'hist')} & {bcell('cv', 'hist')} & --\\")
    T += [r"\midrule", r"\multicolumn{8}{@{}l}{\emph{LLM conditions (mean $\pm$ sd over repeats)}}\\"]
    for sh, name in (("B0", "B0 single call"), ("B1", "B1 debate, no judge"),
                     ("B2", r"\textbf{B2 debate + judge (full)}")):
        T.append(rf"\quad {name} & {cell('hz', sh)} & {cell('cv', sh)} & \N{{hz:{sh}:calls}}\\")
    T += [r"\bottomrule", r"\end{tabular}"]
    (GEN / "main_table.tex").write_text("\n".join(T) + "\n")

    A = [r"\begin{tabular}{@{}l l cc cc@{}}", r"\toprule",
         r" & & \multicolumn{2}{c}{\textbf{Haze}} & \multicolumn{2}{c}{\textbf{COVID-19}}\\",
         r"\cmidrule(lr){3-4}\cmidrule(lr){5-6}",
         r"Question & Variant & JVR [min, max] & $\Delta$ (wins) & JVR [min, max] & $\Delta$ (wins)\\",
         r"\midrule"]

    def rng(c, sh):
        return rf"\N{{{c}:{sh}:jvr:mean}} {{\scriptsize[\N{{{c}:{sh}:jvr:min}}, \N{{{c}:{sh}:jvr:max}}]}}"

    rows = [("Rounds", "B2, 1 round", "R1"), ("", "B2, 2 rounds", "R2"),
            ("", r"B2, 3 rounds (full system)", "B2"),
            ("Judge", "B1: no judge, recommender reads transcript", "B1"),
            ("Inputs", "B2, final offers withheld", "NO"),
            ("", "B2, full information", "FI")]
    for q, name, sh in rows:
        cells = []
        for c in ("hz", "cv"):
            d = "--" if sh == "B2" else rf"\N{{{c}:vs:{sh}:jvr}} (\N{{{c}:vs:{sh}:jvr:wins}})"
            cells.append(rf"{rng(c, sh)} & {d}")
        if q and q != "Rounds":
            A.append(r"\addlinespace[2pt]")
        A.append(rf"{q} & {name} & {cells[0]} & {cells[1]}\\")
    A += [r"\bottomrule", r"\end{tabular}"]
    (GEN / "ablation_table.tex").write_text("\n".join(A) + "\n")

    J = [r"\begin{tabular}{@{}l l cc@{}}", r"\toprule",
         r"Test & Ideal & Haze & COVID-19\\", r"\midrule",
         r"Repeat scoring: exact agreement per criterion ($k{=}3$) & 1.00 & \N{hz:j:exact} & \N{cv:j:exact}\\",
         r"Order swap: criterion scores that change & 0\,\% & \N{hz:j:orderpct}\,\% & \N{cv:j:orderpct}\,\%\\",
         r"Label swap: mean $|\Delta|$ when country names are exchanged & 0 & \N{hz:j:label} & \N{cv:j:label}\\",
         r"Validity: Spearman $\rho$(judge total, joint utility of proposal) & $>0$ & \N{hz:j:vj} & \N{cv:j:vj}\\",
         r"\quad $\rho$(judge total, proposer's own utility) & -- & \N{hz:j:vo} & \N{cv:j:vo}\\",
         r"Unsupported-claim flags vs.\ evaluator: precision / recall & high & \N{hz:j:fp} / \N{hz:j:fr} & \N{cv:j:fp} / \N{cv:j:fr}\\",
         r"\bottomrule", r"\end{tabular}"]
    (GEN / "judge_table.tex").write_text("\n".join(J) + "\n")


if __name__ == "__main__":
    main()
