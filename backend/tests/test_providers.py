"""The one decision this project makes about providers: key -> live, no key -> cached."""
from __future__ import annotations

import json
import subprocess

import pytest

from crisis_debate import cached as cache_mod
from crisis_debate import providers, scenario as scenario_mod
from crisis_debate.cached import CachedBackend, CacheExhausted
from crisis_debate.schemas import Argument, Recommendation, RoundJudgment


@pytest.fixture(scope="module")
def real():
    return scenario_mod.load("haze_real")


# ---------------------------------------------------------------- key check
def test_no_key_falls_back_to_cached(real, monkeypatch):
    monkeypatch.setattr(providers, "openai_key", lambda: "")
    backend, note = providers.backend_from_env(real)
    assert isinstance(backend, CachedBackend)
    assert backend.is_cached is True
    assert "CACHED" in note and "OPENAI_API_KEY" in note


def test_key_present_selects_openai(real, monkeypatch):
    monkeypatch.setattr(providers, "openai_key", lambda: "sk-live-abc123")
    made = {}

    class FakeOpenAI:
        model, is_cached, usage = "gpt-4o", False, {"calls": 0}
        def __init__(self, *a, **k): made["built"] = True

    monkeypatch.setattr(providers, "OpenAIBackend", FakeOpenAI)
    backend, note = providers.backend_from_env(real)
    assert made.get("built") and backend.is_cached is False
    assert note.startswith("LIVE")


def test_unedited_template_placeholder_counts_as_no_key(monkeypatch):
    """Copying .env.example without editing it must not look live."""
    for placeholder in ("", "   ", "your-key-here", "<paste key>"):
        monkeypatch.setattr(providers, "openai_key", lambda p=placeholder: p)
        assert providers.has_openai_key() is False
    monkeypatch.setattr(providers, "openai_key", lambda: "sk-proj-real")
    assert providers.has_openai_key() is True


def test_forcing_openai_without_a_key_raises_rather_than_silently_caching(real, monkeypatch):
    monkeypatch.setattr(providers, "openai_key", lambda: "")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
        providers.backend_from_env(real, force="openai")


def test_real_env_var_beats_dotenv(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-shell")
    assert providers.openai_key() == "sk-from-shell"


# ---------------------------------------------------------------- cached replay
def test_cached_backend_serves_the_recorded_turns_in_order(real):
    b = CachedBackend.for_scenario(real)
    rec = b.cache
    for rnd in rec["rounds"]:
        for turn in rnd:
            a = b.parse(system="", user="", output_format=Argument)
            assert a.country == turn["country"]
            assert a.package() == turn["package"]
        j = b.parse(system="", user="", output_format=RoundJudgment)
        assert j.unaddressed_tradeoffs == rec["judge"][j.round_index - 1]["unaddressed"]


def test_cache_exhaustion_raises_instead_of_inventing_a_turn(real):
    b = CachedBackend.for_scenario(real)
    total = sum(len(r) for r in b.cache["rounds"])
    for _ in range(total):
        b.parse(system="", user="", output_format=Argument)
    with pytest.raises(CacheExhausted, match="argument turns"):
        b.parse(system="", user="", output_format=Argument)


def test_baseline_and_synthesis_recommendations_are_different_recordings(real):
    fresh = CachedBackend.for_scenario(real)
    baseline = fresh.parse(system="", user="", output_format=Recommendation)

    after_debate = CachedBackend.for_scenario(real)
    for _ in range(sum(len(r) for r in after_debate.cache["rounds"])):
        after_debate.parse(system="", user="", output_format=Argument)
    synthesis = after_debate.parse(system="", user="", output_format=Recommendation)

    assert baseline.package() != synthesis.package(), \
        "the baseline condition must not be served the debate's recorded outcome"


def test_every_recorded_package_is_a_legal_settlement(real):
    from crisis_debate.utilities import validate_package

    rec = cache_mod.load_cache(real.scenario_id)
    pkgs = [t["package"] for r in rec["rounds"] for t in r]
    pkgs += [rec["recommendation"]["package"], rec["baseline"]["package"]]
    for p in pkgs:
        assert validate_package(real, p) == [], f"recorded package is not legal: {p}"


@pytest.mark.parametrize("name", ["haze_real"])
def test_recorded_runs_cover_the_real_cases(name):
    sc = scenario_mod.load(name)
    rec = cache_mod.load_cache(sc.scenario_id)
    assert len(rec["rounds"]) == 3 and len(rec["judge"]) == 3
    assert {c.name for c in sc.countries} == {t["country"] for t in rec["rounds"][0]}
    assert "not official" in sc.description or "reconstructed" in sc.description


# ---------------------------------------------------------------- secrets
def test_dotenv_is_gitignored():
    out = subprocess.run(["git", "check-ignore", "-v", ".env"], capture_output=True, text=True)
    assert out.returncode == 0, ".env must be gitignored — it holds a real key"


def test_example_template_is_committed_and_carries_no_key():
    from crisis_debate.providers import find_env

    path = find_env(".env.example")
    assert path.exists(), f".env.example not found (looked up from the package, got {path})"
    text = path.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=" in text
    assert "sk-" not in text, ".env.example must never contain a real key"
