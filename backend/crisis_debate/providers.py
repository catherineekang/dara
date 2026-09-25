"""Provider selection: OpenAI when a key is present, recorded runs when it is not.

The whole point of this module is one decision, made in one place and announced
rather than hidden:

    OPENAI_API_KEY set   ->  live OpenAI calls through LangChain
    not set              ->  replay the recorded run in cached/

Nothing silently degrades. `backend_from_env` returns the backend *and* a
one-line description of which path was taken, and the CLI prints it, so a run
can never be mistaken for a live one because someone forgot to export a key.

The key is read from `.env` (gitignored) via python-dotenv; `.env.example` is
the committed template.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Type

from crisis_debate.schemas import Scenario

ROOT = Path(__file__).resolve().parents[1]


def find_env(name: str = ".env") -> Path:
    """Walk up from this package looking for the env file.

    The repo is split backend/ and frontend/, and `.env` sits at the top so one
    file serves both. Searching upward means the key is found whether you run
    from backend/, from the repo root, or from an editor's working directory.
    """
    here = Path(__file__).resolve()
    for d in [here.parent, *here.parents]:
        if (d / name).exists():
            return d / name
    return ROOT / name
DEFAULT_MODEL = "gpt-4o"


def load_env(path: Optional[Path] = None) -> None:
    """Load .env if python-dotenv is installed. A real environment variable
    already set always wins, so CI and shell exports are not overridden."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(dotenv_path=str(path or find_env()), override=False)


def openai_key() -> str:
    load_env()
    return (os.getenv("OPENAI_API_KEY") or "").strip()


def has_openai_key() -> bool:
    # Strip here rather than trusting the caller: this predicate decides
    # whether a run is live, so it must not depend on something upstream
    # having normalised the value first.
    key = (openai_key() or "").strip()
    # The committed template ships the variable empty; treat a placeholder as
    # absent so copying .env.example without editing it does not look live.
    return bool(key) and not key.lower().startswith(("your", "sk-xxx", "<", "paste"))


@dataclass
class OpenAIBackend:
    """Live backend over LangChain's ChatOpenAI.

    `with_structured_output` binds the same Pydantic models the rest of the
    pipeline uses, so a malformed reply fails at this boundary rather than
    degrading a metric downstream.
    """

    model: str = DEFAULT_MODEL
    temperature: float = 0.0
    llm: Any = None
    usage: Dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0, "calls": 0})
    is_cached: bool = False

    def __post_init__(self) -> None:
        if self.llm is None:
            # Imported lazily so the package works with no LangChain installed
            # as long as you stay on the cached path.
            from langchain_openai import ChatOpenAI

            load_env()
            self.model = os.getenv("OPENAI_MODEL", self.model)
            self.temperature = float(os.getenv("OPENAI_TEMPERATURE", self.temperature))
            self.llm = ChatOpenAI(model=self.model, temperature=self.temperature,
                                  api_key=openai_key())

    def parse(self, *, system: str, user: str, output_format: Type, max_tokens: int = 16000):
        from langchain_core.messages import HumanMessage, SystemMessage

        structured = self.llm.with_structured_output(output_format)
        result = structured.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        self.usage["calls"] += 1
        return result


def backend_from_env(scenario: Scenario, *, force: Optional[str] = None,
                     speed: Optional[float] = None) -> Tuple[Any, str]:
    """Return (backend, note). `force` is "openai" or "cached" to override."""
    if speed is None:
        load_env()
        speed = float(os.getenv("CACHE_PLAYBACK_SPEED", "0") or 0)

    def cached() -> Tuple[Any, str]:
        from crisis_debate.cached import CachedBackend, CacheExhausted  # noqa: F401

        try:
            return (CachedBackend.for_scenario(scenario, speed=speed),
                    f"CACHED — replaying the recorded run for {scenario.scenario_id}. "
                    f"No OpenAI key found; set OPENAI_API_KEY in .env for live calls.")
        except FileNotFoundError:
            from crisis_debate.fakes import ScriptedBackend

            return (ScriptedBackend(scenario=scenario),
                    f"SCRIPTED — no OpenAI key and no recorded run for "
                    f"{scenario.scenario_id}; serving placeholder turns.")

    if force == "cached":
        return cached()
    if force == "openai" or has_openai_key():
        if not has_openai_key():
            raise RuntimeError("--provider openai was requested but OPENAI_API_KEY is not set in .env")
        b = OpenAIBackend()
        return b, f"LIVE — OpenAI {b.model} via LangChain, billed to your key."
    return cached()
