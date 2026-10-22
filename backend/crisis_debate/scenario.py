"""Scenario loading.

A scenario is the INPUT to the system: parties, issues, options and each party's
private valuations. It contains no debate, no agent text and no outcome — those
live in `demo/` (recorded runs) and `eval/<case>/ground_truth.json` (what was
actually settled). Keeping the three apart is CONTRACT.md §5, and it is what
stops a model-generated transcript being quoted as a fixture.

Versioned JSON rather than literals in code, so a run can be reproduced from the
file that produced it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from crisis_debate.schemas import Scenario

#: One directory per case: `eval/<case>/scenario.json`, beside its
#: `ground_truth.json`. The study set holds nothing model-generated — recorded
#: runs live in `demo/` — so that a generated transcript can never be quoted as
#: a fixture (CONTRACT.md §5).
EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"

#: Kept as an alias because the add-crisis flow writes here and several callers
#: still say SCENARIO_DIR. Both name the same place.
SCENARIO_DIR = EVAL_DIR


def case_dir(case: str) -> Path:
    return EVAL_DIR / case


def scenario_path(case: str) -> Path:
    return EVAL_DIR / case / "scenario.json"


def load(path: str | Path) -> Scenario:
    p = Path(path)
    if not p.exists() and not p.is_absolute():
        for candidate in (
            EVAL_DIR / p / "scenario.json",      # eval/<case>/scenario.json
            EVAL_DIR / p,                        # an explicit relative path
            EVAL_DIR / f"{p}.json",              # a flat file, for anything legacy
        ):
            if candidate.exists():
                p = candidate
                break
    scenario = Scenario.model_validate_json(Path(p).read_text(encoding="utf-8"))
    names = [c.name for c in scenario.countries]
    if scenario.focus_country not in names:
        raise ValueError(
            f"focus_country {scenario.focus_country!r} has no brief; have {names}"
        )
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate country names in {p}: {names}")
    return scenario


def available() -> List[str]:
    """Case names — the directory names under `eval/`."""
    if not EVAL_DIR.exists():
        return []
    return sorted(d.name for d in EVAL_DIR.iterdir()
                  if d.is_dir() and (d / "scenario.json").exists())
