"""HTTP API for the DARA console.

The console is a real client of the pipeline, not a replay of a recording baked
into a page. Every number it shows is computed here, by the same code the CLI
and the tests use, and the debate it streams is whatever the configured provider
produced — live OpenAI when a key is present, the recorded run when it is not.

That choice is made once, in `providers.backend_from_env`, and it is *reported*
rather than hidden: every run event carries `mode`, and `/api/health` says which
way the next run will go. A cached run can never be mistaken for a live one
because the UI is told, in the same payload, which it received.

Streaming works by injecting a logger into `pipeline.run`. The pipeline already
emits one event per argument, judgment and recommendation; `QueueLogger` puts
those on a queue as they happen and the SSE endpoint drains it, so the UI sees a
debate turn by turn instead of waiting for the whole run to return.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from crisis_debate import cached, pipeline, providers, scenario as scenario_mod
from crisis_debate.schemas import Scenario
from crisis_debate.utilities import (
    Frontier,
    score_package,
    split_the_difference,
    utility_vector,
    validate_package,
)

app = FastAPI(title="DARA", version="1.0")

# The dev frontend runs on Vite's port; in production the same origin serves
# both, and this list is simply unused.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

USER_PREFIX = "user_"
SAFE_ID = re.compile(r"^[a-z0-9_]{3,60}$")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _load(scenario_id: str) -> Scenario:
    try:
        return scenario_mod.load(scenario_id)
    except FileNotFoundError:
        raise HTTPException(404, f"no scenario {scenario_id!r}")
    except Exception as exc:                      # malformed JSON, failed validation
        raise HTTPException(422, f"scenario {scenario_id!r} is not loadable: {exc}")


def _has_recording(scenario_id: str) -> bool:
    return scenario_id in cached.available()


def _frontier_summary(scenario: Scenario) -> Dict[str, Any]:
    """The yardstick, computed before any agent runs.

    Sent to the client on load rather than at the end of a run, because a
    reference point that arrives after the result is not a reference point.
    """
    f = Frontier.build(scenario)
    split = split_the_difference(scenario)
    split_joint = sum(utility_vector(scenario, split).values())
    return {
        "settlements": len(f.packages),
        "pareto_count": len(f.pareto_indices),
        "max_joint": round(f.max_joint, 4),
        "split_package": split,
        "split_joint": round(split_joint, 4),
        "at_stake": round(f.max_joint - split_joint, 4),
        "points": [
            {
                "utilities": {k: round(v, 4) for k, v in vec.items()},
                "pareto": i in set(f.pareto_indices),
            }
            for i, vec in enumerate(f.vectors)
        ],
    }


def _listing(scenario_id: str) -> Dict[str, Any]:
    s = _load(scenario_id)
    f = Frontier.build(s)
    split_joint = sum(utility_vector(s, split_the_difference(s)).values())
    return {
        "id": s.scenario_id,
        "file": scenario_id,
        "title": s.title,
        "description": s.description,
        "focus_country": s.focus_country,
        "parties": [c.name for c in s.countries],
        "issues": len(s.issues),
        "settlements": len(f.packages),
        "pareto_count": len(f.pareto_indices),
        "at_stake": round(f.max_joint - split_joint, 4),
        "has_recording": _has_recording(s.scenario_id),
        "user_created": scenario_id.startswith(USER_PREFIX),
    }


def _mode(scenario: Scenario, force: Optional[str]) -> Dict[str, Any]:
    backend, note = providers.backend_from_env(scenario, force=force)
    live = not getattr(backend, "is_cached", False)
    return {"backend": backend, "note": note, "mode": "live" if live else "recorded"}


# --------------------------------------------------------------------------
# read
# --------------------------------------------------------------------------
@app.get("/api/health")
def health() -> Dict[str, Any]:
    """Whether the next run will call a model or replay a recording.

    The UI shows this before you press Run, so the answer to "am I about to
    spend money?" is never a surprise.
    """
    key = providers.has_openai_key()
    return {
        "ok": True,
        "key_present": key,
        "mode": "live" if key else "recorded",
        "model": providers.DEFAULT_MODEL if key else None,
        "scenarios": len(scenario_mod.available()),
        "recordings": cached.available(),
        "conditions": [c for c in pipeline.CONDITIONS if c != "B0N"],
    }


@app.get("/api/scenarios")
def list_scenarios() -> List[Dict[str, Any]]:
    out = []
    for sid in scenario_mod.available():
        try:
            out.append(_listing(sid))
        except HTTPException:
            continue                              # a broken file must not hide the good ones
    return out


@app.get("/api/scenarios/{scenario_id}")
def get_scenario(scenario_id: str) -> Dict[str, Any]:
    s = _load(scenario_id)
    return {
        "scenario": s.model_dump(),
        "frontier": _frontier_summary(s),
        "has_recording": _has_recording(s.scenario_id),
        "user_created": scenario_id.startswith(USER_PREFIX),
    }


class ScorePayload(BaseModel):
    package: Dict[str, str]


@app.post("/api/scenarios/{scenario_id}/score")
def score(scenario_id: str, payload: ScorePayload) -> Dict[str, Any]:
    """Score one settlement. Problems are returned, never coerced away."""
    s = _load(scenario_id)
    problems = validate_package(s, payload.package)
    if problems:
        return {"legal": False, "problems": problems, "metrics": None}
    f = Frontier.build(s)
    return {"legal": True, "problems": [], "metrics": score_package(f, payload.package)}


# --------------------------------------------------------------------------
# create — the add-crisis flow posts here
# --------------------------------------------------------------------------
class NewScenario(BaseModel):
    """What the add-crisis form sends.

    Deliberately the same shape as a stored scenario: a crisis a user adds is
    not a second-class kind of input, it is the input, and it goes through the
    same validation the bundled files do.
    """

    scenario: Scenario


@app.post("/api/scenarios", status_code=201)
def create_scenario(payload: NewScenario = Body(...)) -> Dict[str, Any]:
    s = payload.scenario
    sid = s.scenario_id if s.scenario_id.startswith(USER_PREFIX) else USER_PREFIX + s.scenario_id
    if not SAFE_ID.match(sid):
        raise HTTPException(422, "scenario_id must be 3-60 chars of a-z, 0-9 and underscore")

    path = scenario_mod.SCENARIO_DIR / f"{sid}.json"
    if path.exists():
        raise HTTPException(409, f"{sid} already exists")

    if len(s.issues) < 2:
        raise HTTPException(422, "a negotiation needs at least two issues — with one there is nothing to trade")
    if len(s.countries) < 2:
        raise HTTPException(422, "two parties minimum")

    # Weights are normalised per country so utilities stay comparable on [0, 1].
    # Done here rather than in the browser so the stored file is correct however
    # it was posted, and reported back so the normalisation is never silent.
    adjusted: List[str] = []
    for c in s.countries:
        total = sum(v.weight for v in c.valuations)
        if total <= 1e-9:
            raise HTTPException(422, f"{c.name} weights every issue at zero — nothing on the table matters to them")
        if abs(total - 1.0) > 1e-6:
            for v in c.valuations:
                v.weight = round(v.weight / total, 6)
            adjusted.append(c.name)

    s.scenario_id = sid
    for problem in validate_package(s, split_the_difference(s)):
        raise HTTPException(422, f"issue definition problem: {problem}")

    path.write_text(s.model_dump_json(indent=2), encoding="utf-8")
    return {
        "id": sid,
        "weights_normalised_for": adjusted,
        "has_recording": False,
        "listing": _listing(sid),
    }


@app.delete("/api/scenarios/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: str) -> None:
    """Only user-created scenarios. The bundled cases are the experiment's
    fixtures and deleting one through the UI would silently change what a
    reported result refers to."""
    if not scenario_id.startswith(USER_PREFIX):
        raise HTTPException(403, "only user-created scenarios can be deleted")
    path = scenario_mod.SCENARIO_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise HTTPException(404, f"no scenario {scenario_id!r}")
    path.unlink()


# --------------------------------------------------------------------------
# run — server-sent events
# --------------------------------------------------------------------------
class QueueLogger(pipeline.RunLogger):
    """A RunLogger that streams instead of writing a file."""

    def __init__(self, q: "queue.Queue[Any]"):
        super().__init__(None)
        self.q = q

    def log(self, kind: str, payload: Any) -> None:
        self.q.put((kind, payload.model_dump() if hasattr(payload, "model_dump") else payload))


def _sse(event: Dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _run_events(
    scenario_id: str, condition: str, rounds: int, provider: Optional[str], speed: float
) -> Iterator[str]:
    s = _load(scenario_id)
    if condition not in pipeline.CONDITIONS:
        yield _sse({"type": "error", "message": f"unknown condition {condition!r}"})
        return

    try:
        backend, note = providers.backend_from_env(s, force=provider, speed=speed)
    except Exception as exc:
        yield _sse({"type": "error", "message": str(exc)})
        return

    live = not getattr(backend, "is_cached", False)
    mode = "live" if live else "recorded"
    frontier = Frontier.build(s)
    names = [c.name for c in s.countries]

    def enrich(package: Dict[str, str]) -> Dict[str, Any]:
        """Utility vector for a proposed package, or the reason it has none."""
        problems = validate_package(s, package)
        if problems:
            return {"legal": False, "problems": problems}
        return {"legal": True, **score_package(frontier, package)}

    yield _sse({
        "type": "start", "scenario_id": s.scenario_id, "title": s.title,
        "condition": condition, "rounds": rounds, "mode": mode, "note": note,
        "parties": names, "issues": [{"name": i.name, "options": i.options} for i in s.issues],
    })

    q: "queue.Queue[Any]" = queue.Queue()
    result: Dict[str, Any] = {}

    def work() -> None:
        try:
            run = pipeline.run(s, backend, condition=condition, rounds=rounds,
                               logger=QueueLogger(q))
            result["run"] = run
        except Exception as exc:                   # cache exhausted, provider error, bad key
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            q.put(None)                            # sentinel: the worker is done

    thread = threading.Thread(target=work, daemon=True)
    thread.start()

    round_index = 0
    seen_args = 0
    started = time.time()
    while True:
        item = q.get()
        if item is None:
            break
        kind, payload = item

        if kind == "argument":
            if seen_args % len(names) == 0:
                round_index += 1
            seen_args += 1
            package = {p["issue"]: p["option"] for p in payload["proposed_package"]}
            yield _sse({
                "type": "argument", "round": round_index, "mode": mode,
                "country": payload["country"], "position": payload["position"],
                "concessions": payload["concessions"], "package": package,
                "claims": payload["key_claims"], "score": enrich(package),
                "elapsed": round(time.time() - started, 2),
            })
        elif kind == "judgment":
            yield _sse({
                "type": "judgment", "round": payload["round_index"], "mode": mode,
                "judgments": [
                    {"country": j["country"], "summary": j["summary"],
                     "scores": {x["criterion"]: x["score"] for x in j["scores"]},
                     "total": sum(x["score"] for x in j["scores"])}
                    for j in payload["judgments"]
                ],
                "unaddressed": payload["unaddressed_tradeoffs"],
                "elapsed": round(time.time() - started, 2),
            })
        elif kind == "recommendation":
            package = {p["issue"]: p["option"] for p in payload["recommended_package"]}
            yield _sse({
                "type": "recommendation", "mode": mode,
                "focus_country": payload["focus_country"],
                "rationale": payload["rationale"], "options": payload["options"],
                "package": package, "score": enrich(package),
                "elapsed": round(time.time() - started, 2),
            })

    thread.join(timeout=5)

    if "error" in result:
        yield _sse({"type": "error", "message": result["error"]})
        return

    run = result["run"]
    final = run.recommendation.package()
    yield _sse({
        "type": "done", "mode": mode, "condition": condition,
        "package": final, "score": enrich(final),
        "usage": run.usage, "illegal_packages": run.illegal_packages,
        "elapsed": round(time.time() - started, 2),
    })


@app.get("/api/run")
def run_stream(
    scenario: str,
    condition: str = "B2",
    rounds: int = Query(3, ge=1, le=5),
    provider: Optional[str] = Query(None, pattern="^(openai|cached)$"),
    speed: float = Query(0.0, ge=0.0, le=100.0),
) -> StreamingResponse:
    """Stream one run as server-sent events.

    `speed` only affects recorded runs: it replays the recorded latency so the
    console paces like the real thing (0 instant, 1 real time, 4 four times
    faster). A live run takes however long the model takes.
    """
    return StreamingResponse(
        _run_events(scenario, condition, rounds, provider, speed),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
