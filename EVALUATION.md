# Evaluation guide

How to run DARA's evaluation, read what comes out, and report it honestly.
Everything here is from `backend/`. The design behind it is CONTRACT.md §2,
§13.4–§13.6; this file is the procedure.

## The question

> On a multi-issue crisis negotiation with private valuations, does a structured
> debate with an independent judge (**B2**) recover more of the available joint
> value than a single model call (**B0**)?

| Condition | What runs | Calls per run (3 rounds) |
|---|---|---|
| **B0** | one model call writes the recommendation | 1 |
| **B1** | two agents debate, no judge, then a recommendation | 7 |
| **B2** | two agents debate, a judge scores every round, then a recommendation | 10 |

B1 is there to separate *structure* from *budget*: B2 spends ten times the calls
of B0, so a B2 win over B0 alone could just be more compute. B2 beating B1,
which spends nearly as much, is the evidence that the judge is what helps.

**Study set:** `haze_real` (Singapore–Indonesia, 2015–16) and `covid_my_2020`
(Singapore–Malaysia, 2020). User-created scenarios never enter it.

**Scored pair:** every condition is scored on the same recommendation — the
national policymaker with no stated priority (`pipeline.rq_recommendation`).
Never on whichever recommendation happens to come first.

## Pre-declared outcomes

Declared before any live result exists. Report both, side by side; picking one
after seeing the numbers is picking the answer.

| | Metric | Meaning |
|---|---|---|
| **Primary (RQ)** | `joint_value_recovered` | joint utility of the package ÷ the best joint utility any package achieves (exact, all packages enumerated) |
| **Primary (product)** | `focus_gain_over_split` | Singapore's utility minus what splitting every issue down the middle gives it |
| Secondary | `integrative_gain`, `nash_ratio`, `is_pareto_optimal` | joint value and Nash can disagree; say so when they do |

A package that names an option that does not exist is **recorded as illegal and
not scored** — never repaired. Count those as a result in their own right.

## Setup

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env     # repo root; then put your key in OPENAI_API_KEY
pytest -q                      # expect: 195 passed (no key, no network needed)
```

`.env` lives at the repo root (one file for backend and frontend) and is gitignored. Never commit it, never paste the key in chat or an issue.

Optional `.env` settings: `OPENAI_MODEL` (default `gpt-4o`) and
`OPENAI_TEMPERATURE`. **If `OPENAI_TEMPERATURE` is set it overrides the
`--temperature` flag.** The results file records the temperature actually used —
check it rather than assuming.

## Procedure

Do these in order. Each step is cheap compared with the one after it.

### 1. See the plan — free

```bash
python -m crisis_debate.cli experiment
```

Prints the calls it would make (**116** for the defaults: 2 cases × 3 conditions
× 3 repeats, plus judge checks) and runs nothing.

### 2. Dry run — free

```bash
python -m crisis_debate.cli experiment --dry-run
```

The whole pipeline with scripted agents. Every number it prints is meaningless
(all conditions tie); the point is that it finishes and writes
`results/experiment_<timestamp>.json`. If this fails, stop and fix it before
spending anything.

### 3. One live call — about 1 call

The OpenAI path has **never been executed** (CONTRACT.md §13.1). Prove it works
on the cheapest possible run first:

```bash
python -m crisis_debate.cli providers                                   # should say live
python -m crisis_debate.cli run --scenario haze_real --condition B0 --provider openai --priorities none
```

You want a recommendation with a legal package and a score. If it errors
(auth, schema/parsing, model name), fix that here — not 40 calls into step 5.
Then one B2 run (10 calls) is a good second check:

```bash
python -m crisis_debate.cli run --scenario haze_real --condition B2 --provider openai --priorities none
```

### 4. Decide repeats and temperature — before looking at anything

Defaults: 3 repeats at temperature 0.7. Repeats at 0.0 measure API noise, not
spread, so keep it above zero. If budget allows, more repeats beats more cases:
with n = 3 per cell, differences are suggestive at best. Write the choice down
before step 5 and do not change it after seeing results.

### 5. The live experiment — ~116 calls

```bash
python -m crisis_debate.cli experiment --live
# options: --repeats N --rounds N --judge-repeats K --temperature T --cases haze_real,covid_my_2020 --out results
```

Output:
- `results/experiment_<timestamp>.json` — everything (gitignored; copy what you report)
- `results/logs_<timestamp>/<case>_<condition>_<repeat>.jsonl` — every prompt and response, for auditing
- a table on screen

## Reading the results — in this order

### a. Is the judge usable? (`cases[].judge`) — check FIRST

| Field | Pass | If it fails |
|---|---|---|
| `self_consistency` | same round re-scored K times gives (near-)identical scores; low mean absolute deviation | the judge cannot reproduce itself — B2's judge is noise |
| `position_bias` / `position_bias_nonzero` | `false` — swapping argument order changes nothing | the judge is scoring order, not content; revise the rubric before citing B2 |
| `validity` (`spearman_rho`, `reads_as`) | positive: higher rubric score ↔ higher joint value of what that side proposed | the judge rewards something other than good deals |

**If the judge fails, stop there and report that.** It is a real finding
(CONTRACT.md §13.4), and any B2-vs-B0 number built on a failing judge is not
evidence of anything.

### b. The comparison (`cases[].by_condition`, `cases[].comparisons`)

For each case and condition: mean ± sd, min, max for both primaries; calls per
run; unscored (illegal) runs. `comparisons` has the mean differences B2−B0,
B2−B1 and B1−B0 on both primaries.

| Pattern | Reads as |
|---|---|
| B2 > B1 ≈ B0 | the judge adds value — the claim we want |
| B2 ≈ B1 > B0 | debate helps, the judge does not; or it is budget |
| B2 > B0 but B2 ≈ B1 | "structure or budget" — cannot tell which |
| B2 ≈ B0 | no benefit for 10× the calls |
| primaries disagree | report both; say which way each points |

Look at sd and min/max, not just the mean: with 3 repeats, a difference smaller
than the spread is not a difference.

### c. Robustness (`runs[].stress`)

Each recommended package re-scored with the opponent's weights perturbed ±20%
(those weights are estimated, not known). `lo`/`hi` is the joint-value band,
`pareto_rate` how often it stays efficient, `verdict` a one-word reading. A
recommendation that wins only in the base case and falls apart in the band is a
fragile win — say so.

### d. History (`runs[].history`)

Agreement with what was actually settled (`issue_agreement`, `beat_history`).
**Compare across conditions only, never as an absolute score:** the issue sets
are contaminated (below), so a high agreement may just be the model
remembering the outcome.

## Caveats that go in the report

1. **Contamination.** Both study cases happened before the model's training
   cutoff, and the issue sets were written by people who knew the outcome
   (`docs/CONTAMINATION.md`). Every result is **illustrative**, not a clean test.
   The results file carries `"contaminated": true`; keep it in the write-up.
2. **Budget.** Always put calls per run beside every comparison.
3. **Small n.** 2 cases × 3 repeats. No significance claims; report spread.
4. **Valuations are modelling assumptions.** Each side's weights encode which
   issues they are documented to have fought hardest over; they are not
   official. The stress band in (c) is how much that matters.
5. **Interventions.** Runs with a human intervention are tagged and reported
   separately, never mixed in. The experiment runner does not intervene.

## What not to do

- Don't change prompts, the rubric, repeats, or which metric is "primary" after
  seeing live results. If you must, re-run everything and say why.
- Don't score `recs[0]` or pick a different recommendation per condition.
- Don't add `imagine`, `intake` or user-created scenarios to the experiment —
  they are product features, kept out of the measurement on purpose
  (invariant 8; `tests/test_experiment.py` checks it).
- Don't fix up illegal packages so they can be scored.

## Reporting template

```
Model: ____   Temperature (as recorded): ____   Repeats: ____   Rounds: ____   Date: ____

Judge check (per case): self-consistency ____ · position bias ____ · validity rho ____ (n=__)
→ judge usable? yes / no  (if no, stop here)

             joint value recovered      Singapore gain over split    calls/run   illegal
haze   B0    ____ ± ____                ____ ± ____                  1           __
       B1    ____ ± ____                ____ ± ____                  7           __
       B2    ____ ± ____                ____ ± ____                  10          __
covid  B0 / B1 / B2 … same

B2−B0: ____   B2−B1: ____   B1−B0: ____   (both primaries)
Stress band of each condition's recommendation: ____
Agreement with history, relative across conditions: ____

Reading: ____ (structure vs budget; do the primaries agree?)
Caveats: contaminated cases; n = 3; valuations assumed.
```

## If something breaks

| Symptom | Likely cause |
|---|---|
| `providers` says cached | no `OPENAI_API_KEY` in the repo-root `.env` |
| auth / 401 | key wrong or out of credit |
| parsing / validation error on a response | the model's structured output did not match the schema — keep the JSONL log and report it; this is the untested path |
| results temperature ≠ what you passed | `OPENAI_TEMPERATURE` in `.env` overrides the flag |
| `!! N run(s) proposed an illegal package` | a result, not a crash — report the count |
| 409 when saving a crisis in the web app | a `user_*` scenario with that title already exists in `eval/` |

Questions about why something is measured the way it is: CONTRACT.md.
