"""Command line entry point.

    python -m crisis_debate.cli run --scenario haze_real --condition B2
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

    if args.engine == "graph":
        from crisis_debate import graph
        run = graph.run(sc, backend, condition=args.condition, rounds=args.rounds,
                        best_of_n=args.best_of_n)
    else:
        run = pipeline.run(
            sc, backend, condition=args.condition, rounds=args.rounds,
            best_of_n=args.best_of_n,
            log_path=Path(args.log) if args.log else None,
        )

    tag = "cached" if getattr(backend, "is_cached", False) else "live"
    print(f"\n=== {run.condition} ({tag}) | {sc.title} | advising {sc.focus_country} ===")
    for i, opt in enumerate(run.recommendation.options, 1):
        print(f"\n{i}. [{opt.priority_tag}] if {opt.condition}")
        print(f"   -> {opt.action}")
        print(f"   concession: {opt.requires_concession}")
        print(f"   risk: {opt.principal_risk}")
    print(f"\nrationale: {run.recommendation.rationale}")

    # ---- outcome metrics: the objective part of the evaluation ----
    frontier = Frontier.build(sc)
    outcome = score_package(frontier, run.recommendation.package())
    print("\n--- settlement quality ---")
    print(f"  package: {run.recommendation.package()}")
    for k, v in outcome.items():
        print(f"  {k}: {v}")
    print(f"  joint-utility gap to frontier: {distance_to_frontier(frontier, run.recommendation.package())}")
    print(f"  (frontier has {len(frontier.pareto_indices)} of {len(frontier.packages)} packages)")
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

    r = sub.add_parser("run", help="run one condition on one scenario")
    r.add_argument("--scenario", required=True)
    r.add_argument("--condition", default="B2", choices=pipeline.CONDITIONS)
    r.add_argument("--rounds", type=int, default=3, help="round-count ablation")
    r.add_argument("--best-of-n", type=int, default=None,
                   help="B0N sample count; default matches the debate's call count")
    r.add_argument("--provider", default=None, choices=["openai", "cached"],
                   help="default: openai when OPENAI_API_KEY is set in .env, else cached")
    r.add_argument("--engine", default="graph", choices=["graph", "direct"],
                   help="graph = LangGraph state machine; direct = the plain loop")
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
