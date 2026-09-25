# Contamination check — result

Run 25 Sep 2026. Probe code: `crisis_debate/contamination.py`.

**Verdict: the backtest is not safe to run on these two cases as currently
constructed.** Not because the agents leaked — they did not — but because the
hindsight sits one level up, in the scenario design.

---

## What was run

| Probe | Needs a key | Status |
|---|---|---|
| **P0** leak scan — post-cutoff events named in agent output but never supplied | no | **run** |
| **P0b** issue-set audit — post-cutoff events baked into the choice architecture | no | **run** |
| P1 free recall — "what happened in these talks?" | yes | ready, not run |
| P2 cued prediction — does it volunteer post-cutoff facts from a pre-cutoff brief? | yes | ready, not run |
| P3 discrimination — real outcome vs a plausible fabrication | yes | ready, not run |

P1–P3 need `OPENAI_API_KEY`, which is not set. Prompts are written and reviewable
in the module so they can be inspected before any token is spent.

## P0 — agent output: clean

28 agent outputs scanned (positions, concessions, judge summaries,
unaddressed trade-offs, recommendation and baseline rationales and options).

| Case | Cutoff | Result |
|---|---|---|
| haze_real_v1 | 2015-09-01 | no post-cutoff markers matched at all |

Where a marker did match, it was one present in the scenario text the agents were
given — an agent using its brief, not recalling the future. That classification
was verified directly rather than trusted.

## P0b — scenario design: contaminated

This is the finding that matters.

| Case | Flagged | Why it is hindsight |
|---|---|---|
| haze | `peatland_moratorium` | Announced late 2015; BRG agency created January 2016. Both after the cutoff. Its presence as one of four top-level issues presumes the outcome. |

**P0 cannot detect this by construction.** A term written into the issue set is
"supplied" by definition, so it can never be scored as a leak. The agents argued
faithfully within a menu that already contained part of the answer.

The author of these scenarios — the model that wrote them — knew how both
negotiations ended. That is direct evidence of contamination at the frontier-model
level for these cases, independent of any P1 probe.

## What this means for the backtest

A backtest as currently designed would measure whether the agents can find an
answer already embedded in the options given to them. That is not anticipation.

Three ways forward, in order of preference:

1. **Derive the issue set from pre-cutoff documents.** The issues, not just the
   stances, come out of step 1 of the build order. This is the honest fix and it
   makes step 1 carry more weight: extraction has to identify *what was being
   negotiated* as of the cutoff, not only each party's position on it.
2. **Score the reasoning path, not the endpoint.** Report which issues traded
   against which and whether the trade direction matches the record, while stating
   that the option set was authored with hindsight.
3. **Find a negotiation that has not concluded**, where there is no outcome to
   recall. Most expensive; cleanest.

Run P1–P3 once a key is available. If P1 shows strong free recall — which is
likely — option 1 stops being sufficient on its own and option 3 becomes the only
clean backtest.

## Reproducing

```bash
python -c "
import sys; sys.path.insert(0,'.')
from crisis_debate import contamination as X, scenario as S
import json; from pathlib import Path
for sid,f in [('haze_real_v1','haze_real')]:
    sc=S.load(f); cache=json.loads(Path(f'cached/{f}.json').read_text())
    supplied=' '.join([sc.title,sc.description]+[x for c in sc.countries for x in
        (c.interests+c.resources+c.constraints+c.red_lines)]+
        [i.name for i in sc.issues]+[o for i in sc.issues for o in i.options])
    print(sid, X.scan(sid,cache,supplied).verdict)
    print(' ', X.audit_issue_set(sid,[i.name for i in sc.issues]+
          [o for i in sc.issues for o in i.options]))
"
```
