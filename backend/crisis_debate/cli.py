"""Command line entry point.

    python -m crisis_debate.cli rehearse haze_real
    python -m crisis_debate.cli rehearse --form examples/haze_form.json
    python -m crisis_debate.cli rehearse haze_real --open "transition_funding=none"
    python -m crisis_debate.cli run --scenario haze_real --condition B2
    python -m crisis_debate.cli experiment            # plan and cost; --dry-run / --live
    python -m crisis_debate.cli run --scenario haze_real --condition B0 --dry-run
    python -m crisis_debate.cli scenarios
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from crisis_debate import metrics, pipeline, scenario as scenario_mod
from crisis_debate.agents import debate_call_count
from crisis_debate.providers import DEFAULT_MODEL, backend_from_env
from crisis_debate.utilities import Frontier, score_package, distance_to_frontier


def _cmd_scenarios(_: argparse.Namespace) -> int:
    for name in scenario_mod.available():
        print(name)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    sc = scenario_mod.load(args.scenario)

    if args.dry_run:
        # Renders every prompt without spending anything. Worth running before
        # any sweep: a prompt bug found here costs nothing.
        from crisis_debate.fakes import ScriptedBackend

        backend = ScriptedBackend(scenario=sc, echo_prompts=args.echo,
                                  package_strategy=args.fake_strategy)
        note = "DRY RUN — prompts rendered, no provider contacted."
    else:
        backend, note = backend_from_env(sc, force=args.provider, speed=args.speed)
    print(f"[provider] {note}")

    priorities = tuple(p.strip() for p in args.priorities.split(",") if p.strip())
    run = pipeline.run(
        sc, backend, condition=args.condition, rounds=args.rounds,
        priorities=priorities,
        log_path=Path(args.log) if args.log else None,
    )

    tag = "cached" if getattr(backend, "is_cached", False) else "live"
    print(f"\n=== {run.condition} ({tag}) | {sc.title} | advising {sc.focus_country} ===")
    frontier = Frontier.build(sc)
    for rec in run.recommendations:
        print(f"\n##### protecting: {rec.priority} — {rec.headline}")
        for i, opt in enumerate(rec.options, 1):
            print(f"\n{i}. [{opt.priority_tag}] if {opt.condition}")
            print(f"   -> {opt.action}")
            print(f"   concession: {opt.requires_concession}")
            print(f"   risk: {opt.principal_risk}")
        print(f"\nrationale: {rec.rationale}")

        # ---- outcome metrics: the objective part of the evaluation ----
        outcome = score_package(frontier, rec.package())
        print("\n--- settlement quality ---")
        print(f"  package: {rec.package()}")
        for k, v in outcome.items():
            print(f"  {k}: {v}")
        print(f"  joint-utility gap to frontier: {distance_to_frontier(frontier, rec.package())}")
    print(f"\n  (frontier has {len(frontier.pareto_indices)} of {len(frontier.packages)} packages)")
    if run.illegal_packages:
        print(f"\n  !! {len(run.illegal_packages)} illegal package entries "
              f"- these are NOT coerced, so affected numbers are unreliable:")
        for problem in run.illegal_packages[:5]:
            print(f"     {problem}")

    if run.rounds:
        print("\n--- brief consistency (criterion a) ---")
        for country in [c.name for c in sc.countries]:
            last = [a for a in run.rounds[-1] if a.country == country]
            if last:
                m = metrics.brief_consistency(last[0], sc.brief(country))
                moves = metrics.position_movement(run.rounds, country)
                print(f"  {country}: {m} | movement per round: {moves}")

    if args.out:
        Path(args.out).write_text(run.model_dump_json(indent=2), encoding="utf-8")
        print(f"\nrun written to {args.out}")
    if run.usage:
        print(f"usage: {run.usage}")
    return 0


def _cmd_rehearse(args: argparse.Namespace) -> int:
    """The policymaker's command: imagine the negotiation before walking in.

    No key, no model call, no flags to understand. Without `--open` it tries
    every opening in imagination and shows the best three; with it, it answers
    "what happens if I open with this?".
    """
    from crisis_debate import intake
    from crisis_debate.imagine import WorldModel, imagine, parse_opening, plan

    if args.form:
        try:
            sc = intake.build(json.loads(Path(args.form).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            print(f"Could not read the form: {exc}")
            return 2
        print(f"{sc.title} — advising {sc.focus_country}\n")
        print("How your answers were read (check this before trusting anything below):")
        for line in intake.review(sc):
            print(f"  {line}")
        print()
    elif args.scenario:
        sc = scenario_mod.load(args.scenario)
        print(f"{sc.title} — advising {sc.focus_country}\n")
    else:
        print("Name a case (see: scenarios) or pass --form your_crisis.json "
              "(example: examples/haze_form.json).")
        return 2
    print("On the table:")
    for issue in sc.issues:
        print(f"  {issue.name}: {' | '.join(issue.options)}")
    print()

    model = WorldModel.build(sc)
    if args.open:
        try:
            opening = parse_opening(sc, args.open)
        except ValueError as exc:
            print(f"Could not read that opening: {exc}")
            return 2
        rehearsals = [imagine(model, opening)]
    else:
        rehearsals = plan(model)
        if not rehearsals:
            print("Every possible opening crosses one of your own red lines. Revisit what "
                  "you marked unacceptable.")
            return 1
        print("Best openings, found by trying every possible opening in imagination:\n")

    for n, r in enumerate(rehearsals, 1):
        label = f"Option {n}" if len(rehearsals) > 1 else "Your opening"
        print(f"{label}:")
        for issue, option in r.opening.items():
            print(f"  {issue}: {option}")
        for line in r.summary():
            print(f"  · {line}")
        print()
    print("This is a quick estimate from your stated read of the other side, not a "
          "prediction. To hear them argue it, run: "
          f"python -m crisis_debate.cli run --scenario {args.scenario or '<saved case>'}")
    return 0


def _cmd_experiment(args: argparse.Namespace) -> int:
    """The research question: B0/B1/B2 on the study set, with every qualifier.

    Spends money only with --live. Without a flag it prints the plan and the
    number of model calls, so the cost is known before it is incurred.
    """
    from crisis_debate import experiment
    from crisis_debate.fakes import ScriptedBackend

    cases = [c.strip() for c in args.cases.split(",") if c.strip()]
    total = 0
    print("Planned model calls:")
    for c in cases:
        calls = experiment.planned_calls(scenario_mod.load(c), rounds=args.rounds,
                                         repeats=args.repeats, judge_repeats=args.judge_repeats)
        total += calls["total"]
        print(f"  {c}: {calls}")
    print(f"  total: {total}\n")

    if args.live:
        from crisis_debate.providers import OpenAIBackend, has_openai_key
        if not has_openai_key():
            print("--live needs OPENAI_API_KEY in .env")
            return 2
        temps = set()

        def factory(sc, condition, rep):
            b = OpenAIBackend(temperature=args.temperature)
            temps.add(b.temperature)              # .env may override the flag; record the truth
            return b
        mode = "live"
    elif args.dry_run:
        def factory(sc, condition, rep):
            return ScriptedBackend(scenario=sc)
        mode = "DRY RUN — scripted agents, numbers are meaningless"
    else:
        print("Nothing run. Add --dry-run to check the plumbing for free, "
              "or --live to spend the calls above.")
        return 0

    result = experiment.run_experiment(
        cases, factory, rounds=args.rounds, repeats=args.repeats,
        judge_repeats=args.judge_repeats, mode=mode, out_dir=Path(args.out))
    if args.live:
        result["temperature"] = sorted(temps)
        Path(result["written_to"]).write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                              encoding="utf-8")
    for line in experiment.report(result):
        print(line)
    print(f"\nfull results: {result['written_to']}")
    return 0


def _cmd_providers(_: argparse.Namespace) -> int:
    """Show which path a run would take right now, without running anything."""
    from crisis_debate import cached as cache_mod
    from crisis_debate.providers import has_openai_key, openai_key

    key = openai_key()
    print(f"OPENAI_API_KEY : {'set (' + key[:7] + '…)' if has_openai_key() else 'not set'}")
    print(f"resolves to    : {'LIVE OpenAI via LangChain' if has_openai_key() else 'CACHED replay'}")
    print(f"recorded runs  : {', '.join(cache_mod.available()) or 'none'}")
    print(f"scenarios      : {', '.join(scenario_mod.available())}")
    if not has_openai_key():
        print("\nTo go live: put your key in .env (copy .env.example). .env is gitignored.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="crisis_debate")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scenarios", help="list bundled scenarios").set_defaults(func=_cmd_scenarios)
    sub.add_parser("providers", help="show whether a run would be live or cached").set_defaults(func=_cmd_providers)

    h = sub.add_parser("rehearse", help="imagine the negotiation: best openings, or test your own")
    h.add_argument("scenario", nargs="?", help="e.g. haze_real (see: scenarios)")
    h.add_argument("--form", help="your own crisis, described in rankings and "
                                  "good/acceptable/unacceptable (see examples/haze_form.json)")
    h.add_argument("--open", default="",
                   help='your opening, e.g. "enforcement=joint_asean_panel"; '
                        "issues you leave out are set to your own first choice")
    h.set_defaults(func=_cmd_rehearse)

    e = sub.add_parser("experiment", help="research: B0/B1/B2 on the study set, with judge checks")
    e.add_argument("--cases", default="haze_real,covid_my_2020")
    e.add_argument("--repeats", type=int, default=3)
    e.add_argument("--rounds", type=int, default=3)
    e.add_argument("--judge-repeats", type=int, default=3)
    e.add_argument("--temperature", type=float, default=0.7,
                   help="live only; repeats at 0.0 would measure API noise, not spread")
    e.add_argument("--out", default="results")
    g = e.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="scripted agents, no cost")
    g.add_argument("--live", action="store_true", help="call OpenAI and spend the calls")
    e.set_defaults(func=_cmd_experiment)

    r = sub.add_parser("run", help="run one condition on one scenario")
    r.add_argument("--scenario", required=True)
    r.add_argument("--condition", default="B2", choices=pipeline.CONDITIONS)
    r.add_argument("--rounds", type=int, default=3, help="round-count ablation")
    r.add_argument("--priorities", default=",".join(pipeline.DEFAULT_PRIORITIES),
                   help="comma-separated objectives, one recommendation each")
    r.add_argument("--provider", default=None, choices=["openai", "cached"],
                   help="default: openai when OPENAI_API_KEY is set in .env, else cached")
    r.add_argument("--speed", type=float, default=None,
                   help="cached playback: 0 instant, 1 real time, 4 = 4x faster")
    r.add_argument("--dry-run", action="store_true", help="render prompts, call no model")
    r.add_argument("--fake-strategy", default="split",
                   choices=["split", "integrative", "first"],
                   help="dry-run only: what the scripted agents converge on")
    r.add_argument("--echo", action="store_true", help="dry-run only: print every prompt")
    r.add_argument("--log", help="JSONL run log path")
    r.add_argument("--out", help="write the full run as JSON")
    r.set_defaults(func=_cmd_run)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
