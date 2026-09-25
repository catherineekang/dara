"""Scenario loading. Scenarios are versioned JSON, not literals in code, so a
run can be reproduced from the file that produced it."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from crisis_debate.schemas import Scenario

SCENARIO_DIR = Path(__file__).resolve().parents[1] / "scenarios"


def load(path: str | Path) -> Scenario:
    p = Path(path)
    if not p.exists() and not p.is_absolute():
        candidate = SCENARIO_DIR / p
        if candidate.exists():
            p = candidate
        elif (SCENARIO_DIR / f"{p}.json").exists():
            p = SCENARIO_DIR / f"{p}.json"
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
    return sorted(p.stem for p in SCENARIO_DIR.glob("*.json"))
