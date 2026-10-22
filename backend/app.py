"""Flask API for the DARA console.

The console is a real client of the pipeline, not a replay baked into a page.
Every number it shows is computed here, by the same code the CLI and the tests
use, and the debate it streams is whatever the configured provider produced —
live OpenAI when a key is present, the recorded run when it is not.

That choice is made once, in `providers.backend_from_env`, and it is *reported*
rather than hidden: every run event carries `mode`, and `/api/health` says which
way the next run will go. A recorded run can never be mistaken for a live one
because the UI is told, in the same payload, which it received.

Streaming works by injecting a logger into `pipeline.run`. The pipeline already
emits one event per argument, judgment and recommendation; `QueueLogger` puts
those on a queue as they happen and the SSE route drains it, so the UI sees a
debate turn by turn instead of waiting for the whole run to return.

Run it with:  python app.py        (from backend/)
"""
from __future__ import annotations

import json
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from flask import Flask, Response, jsonify, request

from crisis_debate import cached, pipeline, providers, scenario as scenario_mod
from crisis_debate import imagine as imagine_mod, intake as intake_mod
from crisis_debate.schemas import AUDIENCE_BRIEF, READER_PRIORITY_BRIEF, Scenario
from crisis_debate.utilities import (
    Frontier,
    score_package,
    split_the_difference,
    utility_vector,
    validate_package,
)

app = Flask(__name__)

USER_PREFIX = "user_"
SAFE_ID = re.compile(r"^[a-z0-9_]{3,60}$")

# One reader only: this tool advises one side. What varies between the two
# recommendations is the PRIORITY, not the audience.
AUDIENCES = [
    {"id": "policymaker", "label": "National policymaker",
     "blurb": "Answers to a treasury, a cabinet and an electorate."},
]
PRIORITIES = [
    {"id": "casualty_minimisation", "label": "Lives and health",
     "blurb": "Fewest deaths, illness and displacement, soonest."},
    {"id": "cost", "label": "Public money",
     "blurb": "Least spending, liability and open-ended commitment."},
    {"id": "continuity", "label": "Continuity",
     "blurb": "Keep essential services and supply running."},
    {"id": "speed", "label": "Speed of agreement",
     "blurb": "Something operating before the binding deadline."},
    {"id": "precedent", "label": "Precedent",
     "blurb": "Least conceded that binds you in future negotiations."},
    {"id": "none", "label": "No stated priority",
     "blurb": "The untailored reading — the baseline the others are compared against."},
]


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@app.errorhandler(ApiError)
def _handle_api_error(exc: ApiError):
    # `detail` rather than `error`, so the browser client reads one field
    # whatever went wrong.
    return jsonify({"detail": exc.message}), exc.status


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _load(scenario_id: str) -> Scenario:
    try:
        return scenario_mod.load(scenario_id)
    except FileNotFoundError:
        raise ApiError(404, f"no scenario {scenario_id!r}")
    except Exception as exc:                      # malformed JSON, failed validation
        raise ApiError(422, f"scenario {scenario_id!r} is not loadable: {exc}")


def _has_recording(scenario: Scenario) -> bool:
    """Exactly the predicate the run path uses.

    Not `scenario_id in cached.available()`: that lists *file stems*, and a
    recording's filename need not match the scenario_id inside it. Checking the
    stem once reported "no recording" for cases that replay perfectly well, so
    ask the loader itself rather than guessing from filenames.
    """
    try:
        cached.load_cache(scenario.scenario_id)
        return True
    except FileNotFoundError:
        return False


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
            {"utilities": {k: round(v, 4) for k, v in vec.items()},
             "pareto": i in set(f.pareto_indices)}
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
        "has_recording": _has_recording(s),
        "user_created": scenario_id.startswith(USER_PREFIX),
    }


# --------------------------------------------------------------------------
# read
# --------------------------------------------------------------------------
@app.get("/api/health")
def health():
    """Whether the next run will call a model or replay a recording.

    The UI shows this before you press Run, so the answer to "am I about to
    spend money?" is never a surprise.
    """
    key = providers.has_openai_key()
    return jsonify({
        "ok": True,
        "key_present": key,
        "mode": "live" if key else "recorded",
        "model": providers.DEFAULT_MODEL if key else None,
        "scenarios": len(scenario_mod.available()),
        "recordings": cached.available(),
        "conditions": list(pipeline.CONDITIONS),
        # The UI renders these rather than keeping its own copy, so adding a
        # priority is a backend change only.
        "audiences": AUDIENCES,
        "priorities": PRIORITIES,
        "default_priority": pipeline.DEFAULT_PRIORITY,
        "default_priorities": list(pipeline.DEFAULT_PRIORITIES),
        "audience": pipeline.AUDIENCE,
    })


@app.get("/api/scenarios")
def list_scenarios():
    out = []
    for sid in scenario_mod.available():
        try:
            out.append(_listing(sid))
        except ApiError:
            continue                              # a broken file must not hide the good ones
    return jsonify(out)


@app.get("/api/scenarios/<scenario_id>")
def get_scenario(scenario_id: str):
    s = _load(scenario_id)
    return jsonify({
        "scenario": s.model_dump(),
        "frontier": _frontier_summary(s),
        "has_recording": _has_recording(s),
        # Which objectives can actually be replayed without a key. The console
        # greys out the rest rather than letting a run die halfway through.
        "recorded_priorities": cached.recorded_priorities(s.scenario_id, pipeline.AUDIENCE),
        "user_created": scenario_id.startswith(USER_PREFIX),
    })


@app.post("/api/scenarios/<scenario_id>/score")
def score(scenario_id: str):
    """Score one settlement. Problems are returned, never coerced away."""
    s = _load(scenario_id)
    body = request.get_json(silent=True) or {}
    package = body.get("package") or {}
    if not isinstance(package, dict):
        raise ApiError(422, "package must be an object of issue -> option")
    problems = validate_package(s, package)
    if problems:
        return jsonify({"legal": False, "problems": problems, "metrics": None})
    f = Frontier.build(s)
    return jsonify({"legal": True, "problems": [], "metrics": score_package(f, package)})


@app.post("/api/scenarios/<scenario_id>/imagine")
def imagine_opening(scenario_id: str):
    """Rehearse in imagination — no model call, answers instantly.

    With `{"opening": {issue: option, ...}}` (any subset of issues) it predicts
    how plausible versions of the other side respond to that opening. With no
    opening it tries every opening and returns the best three.
    """
    s = _load(scenario_id)
    body = request.get_json(silent=True) or {}
    opening = body.get("opening")
    if opening is not None and not isinstance(opening, dict):
        raise ApiError(422, "opening must be an object of issue -> option")
    model = imagine_mod.WorldModel.build(s)
    if opening is None:
        return jsonify({"mode": "plan",
                        "rehearsals": [r.as_dict() for r in imagine_mod.plan(model)]})
    try:
        r = imagine_mod.imagine(model, opening)
    except ValueError as exc:
        raise ApiError(422, str(exc))
    return jsonify({"mode": "what_if", "rehearsals": [r.as_dict()]})


# --------------------------------------------------------------------------
# create — the add-crisis flow posts here
# --------------------------------------------------------------------------
@app.post("/api/scenarios")
def create_scenario():
    """A crisis a user adds is not a second-class kind of input: it is validated
    by the same model the bundled files are, and written to the same folder."""
    body = request.get_json(silent=True) or {}
    raw = body.get("scenario")
    if raw is None:
        raise ApiError(422, "body must be {\"scenario\": {...}}")
    try:
        s = Scenario.model_validate(raw)
    except Exception as exc:
        raise ApiError(422, f"scenario did not validate: {exc}")
    return _save_user_scenario(s)


def _save_user_scenario(s: Scenario):
    """Validate and write a user scenario. Shared by the raw and the simple form."""
    sid = s.scenario_id if s.scenario_id.startswith(USER_PREFIX) else USER_PREFIX + s.scenario_id
    if not SAFE_ID.match(sid):
        raise ApiError(422, "scenario_id must be 3-60 chars of a-z, 0-9 and underscore")

    path = scenario_mod.SCENARIO_DIR / f"{sid}.json"
    if path.exists():
        raise ApiError(409, f"{sid} already exists")
    if len(s.issues) < 2:
        raise ApiError(422, "a negotiation needs at least two issues — with one there is nothing to trade")
    if len(s.countries) < 2:
        raise ApiError(422, "two parties minimum")

    # Weights are normalised per country so utilities stay comparable on [0, 1].
    # Done here rather than in the browser so the stored file is correct however
    # it was posted, and reported back so the normalisation is never silent.
    adjusted: List[str] = []
    for c in s.countries:
        total = sum(v.weight for v in c.valuations)
        if total <= 1e-9:
            raise ApiError(422, f"{c.name} weights every issue at zero — nothing on the table matters to them")
        if abs(total - 1.0) > 1e-6:
            for v in c.valuations:
                v.weight = round(v.weight / total, 6)
            adjusted.append(c.name)

    s.scenario_id = sid
    for problem in validate_package(s, split_the_difference(s)):
        raise ApiError(422, f"issue definition problem: {problem}")

    path.write_text(s.model_dump_json(indent=2), encoding="utf-8")
    return jsonify({"id": sid, "weights_normalised_for": adjusted,
                    "has_recording": False, "listing": _listing(sid)}), 201


EXAMPLE_FORM = Path(__file__).resolve().parent / "examples" / "haze_form.json"


@app.get("/api/intake/example")
def intake_example():
    """A filled-in form, so a first-time user sees what good answers look like."""
    return jsonify(json.loads(EXAMPLE_FORM.read_text(encoding="utf-8")))


@app.post("/api/intake")
def intake_form():
    """The policymaker's add-crisis: rankings and good/acceptable/unacceptable.

    `{"form": {...}}` returns how the answers were read and a rehearsal of the
    best openings, WITHOUT saving — that is the review gate (CONTRACT.md §2).
    Add `"confirm": true` once the person has read it, and it is saved like any
    other user scenario.
    """
    body = request.get_json(silent=True) or {}
    try:
        s = intake_mod.build(body.get("form") or {})
    except intake_mod.IntakeError as exc:
        raise ApiError(422, str(exc))
    if body.get("confirm"):
        return _save_user_scenario(s)
    return jsonify({
        "saved": False,
        "review": intake_mod.review(s),
        "scenario": s.model_dump(),
        "rehearsals": [r.as_dict() for r in imagine_mod.plan(imagine_mod.WorldModel.build(s))],
    })


@app.delete("/api/scenarios/<scenario_id>")
def delete_scenario(scenario_id: str):
    """Only user-created scenarios. The bundled cases are the experiment's
    fixtures, and deleting one through the UI would silently change what a
    reported result refers to."""
    if not scenario_id.startswith(USER_PREFIX):
        raise ApiError(403, "only user-created scenarios can be deleted")
    path = scenario_mod.SCENARIO_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise ApiError(404, f"no scenario {scenario_id!r}")
    path.unlink()
    return "", 204


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


def _run_events(scenario_id: str, condition: str, rounds: int, provider: Optional[str],
                speed: float, priorities: str) -> Iterator[str]:
    try:
        s = _load(scenario_id)
    except ApiError as exc:
        yield _sse({"type": "error", "message": exc.message})
        return

    if condition not in pipeline.CONDITIONS:
        yield _sse({"type": "error", "message": f"unknown condition {condition!r}"})
        return

    priority_list = [p.strip() for p in priorities.split(",") if p.strip()]
    unknown = [p for p in priority_list if p not in READER_PRIORITY_BRIEF]
    if unknown:
        yield _sse({"type": "error",
                    "message": f"unknown priority {unknown[0]!r}; have {sorted(READER_PRIORITY_BRIEF)}"})
        return
    if not priority_list:
        priority_list = list(pipeline.DEFAULT_PRIORITIES)

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
        "priorities": priority_list, "audience": pipeline.AUDIENCE,
    })

    q: "queue.Queue[Any]" = queue.Queue()
    result: Dict[str, Any] = {}

    def work() -> None:
        try:
            result["run"] = pipeline.run(s, backend, condition=condition, rounds=rounds,
                                         logger=QueueLogger(q),
                                         priorities=priority_list)
        except Exception as exc:                   # cache exhausted, provider error, bad key
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            q.put(None)                            # sentinel: the worker is done

    thread = threading.Thread(target=work, daemon=True)
    thread.start()

    round_index = 0
    seen_args = 0
    rec_index = 0
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
                "audience": payload["audience"], "priority": payload["priority"],
                "headline": payload["headline"],
                "index": rec_index, "of": len(priority_list),
                "rationale": payload["rationale"], "options": payload["options"],
                "package": package, "score": enrich(package),
                "elapsed": round(time.time() - started, 2),
            })
            rec_index += 1

    thread.join(timeout=5)

    if "error" in result:
        yield _sse({"type": "error", "message": result["error"]})
        return

    run = result["run"]
    # One summary per reader. There is deliberately no single "the" result:
    # picking one reader's package to report would be choosing whose mandate
    # counts, which is the thing this design removes.
    results = [
        {"priority": rec.priority, "audience": rec.audience, "headline": rec.headline,
         "package": rec.package(), "score": enrich(rec.package())}
        for rec in run.recommendations
    ]
    packages = {json.dumps(r["package"], sort_keys=True) for r in results}
    yield _sse({
        "type": "done", "mode": mode, "condition": condition,
        "results": results,
        "objectives_diverged": len(packages) > 1,
        "usage": run.usage, "illegal_packages": run.illegal_packages,
        "elapsed": round(time.time() - started, 2),
    })


@app.get("/api/run")
def run_stream():
    """Stream one run as server-sent events.

    `speed` only affects recorded runs: it replays the recorded latency so the
    console paces like the real thing (0 instant, 1 real time, 4 four times
    faster). A live run takes however long the model takes.

    `priorities` tailors the advice: a comma-separated list of what the reader
    is trying to protect. The debate runs once; one recommendation is produced
    per priority, all written for the same reader.
    """
    args = request.args
    try:
        rounds = max(1, min(5, int(args.get("rounds", 3))))
        speed = max(0.0, min(100.0, float(args.get("speed", 0))))
    except ValueError:
        raise ApiError(422, "rounds and speed must be numbers")

    stream = _run_events(
        args.get("scenario", ""),
        args.get("condition", "B2"),
        rounds,
        args.get("provider") or None,
        speed,
        args.get("priorities", ",".join(pipeline.DEFAULT_PRIORITIES)),
    )
    return Response(stream, mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    # 127.0.0.1 explicitly, not "localhost": a server that binds only the IPv6
    # loopback is unreachable at 127.0.0.1, which is exactly how the previous
    # setup failed to open.
    # threaded=True because a run holds its connection open while it streams;
    # single-threaded, one run would block every other request.
    print("DARA API  ->  http://127.0.0.1:8000/api/health")
    app.run(host="127.0.0.1", port=8000, debug=False, threaded=True)
