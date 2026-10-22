# DARA — the contract

> **Rehearse the negotiation, so Singapore knows what it's walking into.**

One sentence. Everything in this repository either serves it or is cut.

This file exists because the code has drifted from it twice already. Read it
before adding anything.

---

## 1. The two jobs, and why they are different

DARA is doing two things at once. Both are legitimate, they share a codebase,
and confusing them is how the drift happened.

| | **The product** | **The research question** |
|---|---|---|
| Question | What will they refuse, and what will they trade? | Does structured multi-agent debate beat a single model? |
| Audience | A policymaker preparing for a real negotiation | The DSA4213 marker |
| Input | A crisis the user brings | Two fixed scenarios we authored |
| Succeeds when | The rehearsal surfaces a trade the team hadn't considered | A number, with a spread, that survives its own ablations |
| Fails when | It is confidently wrong about a country's position | The result is confounded, unreproducible, or contaminated |
| Entry point | `rehearse` / `POST /api/intake` — rankings and good/acceptable/unacceptable in, plain sentences out | `experiment` — B0/B1/B2 on the study set, every qualifier attached |

**The research question is the deliverable. The product is the motivation.**
When they conflict — and they do — the RQ wins, and the product feature gets a
flag rather than a redesign of the measurement.

**The product is usability-first; the measurement is untouched.** (Decided
2026-10-22.) A policymaker must be able to rehearse their own crisis without
supplying a single number or meeting B0/B1/B2. That is achieved by *adding* a
product path beside the research path, never by changing the research path: the
two meet only at the `Scenario` object and the frontier arithmetic, and the
product modules are never imported by the experiment (§8.8).

### The research question, stated so it can be answered

> On a multi-issue crisis negotiation with private valuations, does a structured
> debate with an independent judge (**B2**) recover more of the available joint
> value than a single model call (**B0**)?

**Primary comparison: B2 vs B0.** Three conditions only — B0, B1, B2.

**The budget gap is a declared limitation, not a control.** B2 spends 11 calls
against B0's 2, so a B2 win is confounded with having spent more. There was a
compute-matched arm (B0N: sample the baseline n times, keep the modal package)
and it has been removed for simplicity. The consequence must be stated wherever
a result is: **every reported comparison carries its call counts**, and the
honest reading of a B2 win is "structure *or* budget", with B1 as the only
internal discriminator — B1 spends 8 calls with a debate but no judge, so
B2 > B1 isolates the judge at near-equal cost.

---

## 2. The pipeline

Eleven stages and one human gate. Stages 1–5 build the problem, 6 builds the
yardstick, 7–9 are the rehearsal, 10–11 are the verdict and its fragility.

```
      ┌─ 1 SOURCE ────── the crisis arrives: typed, pasted documents, or a search
      │  2 FRAME ─────── issues + options — typed, or extract_issues() under a
BUILD │                  cutoff. The middle option is INTERPOLATED (§4)
      │  3 BRIEF ─────── interests, red lines, stance, style — every element
      │                  carries its source, or comes back unknown/unsupported
      │  4 CONDITIONS ── what is true in the world RIGHT NOW that constrains
      │                  them — a flood, an election, a committed budget
      └─ 5 ESTIMATE ─── valuations: YOURS known, THEIRS assumed (§3 row 4)

╔═ REVIEW ════════════════════════════════════════════════════════════════╗
║  HUMAN GATE. Nothing below runs until the issue set, its options and the ║
║  weights have been shown and confirmed. Not a formality — it is the only ║
║  place a wrong problem gets caught before it is rehearsed. (§4)          ║
╚═════════════════════════════════════════════════════════════════════════╝

YARD ─── 6 MAP ───────── frontier over the FULL option space; split baseline,
                         Nash max. BEFORE any agent call. Stage 4 does not move
                         this — it reports what it put out of REACH.

      ┌─ 7 REHEARSE ──── r=1..3, each turn commits to a concrete package
PLAY  │  8 ADJUDICATE ── judge scores how it was argued — sees no valuations
      └─ 9 ADVISE ───── recommendation from the judge alone — no transcript

      ┌─ 10 SCORE ────── package vs frontier; and vs history, where we have it
TELL  └─ 11 STRESS ───── re-run 6 under other guesses about them, and under a
                         stale-conditions check; report the spread
```

Three parts of that carry the main thing.

**Stage 2 decides which negotiation is being rehearsed at all.** You cannot trade
what is not on the menu, so a missing issue is not a small error — it is a
rehearsal of a different problem. §4.

**Stage 4 is what makes it meaningful rather than abstract.** A negotiation
simulated without knowing that Indonesia is currently committing its fiscal space
to flood response is rehearsing the wrong negotiation. §11.

**Stage 11 exists because the advice should not look more certain than it is.**
If the recommendation flips when you nudge your guess about the other side, or
when the conditions snapshot is a day older, the person walking into the room
needs to know that before they walk in.

**And the REVIEW gate is load-bearing, not ceremony.** Stages 2–5 are the only
ones where a human can still catch a wrong problem cheaply. After the gate every
number is computed correctly *about whatever was confirmed there*, which is
exactly why an unreviewed issue set produces confident, precise, wrong answers.

### Imagine before acting — two fidelities of stage 7

Stage 7 is a **world model** in the sense of Craik (1943) and Ha & Schmidhuber
(2018): a model of the other side, used to try out an action — an opening — before
committing to it in the real room. DARA has it at two fidelities:

| | **Imagined** — `imagine.py` | **Argued** — the debate, stages 7–9 |
|---|---|---|
| What runs | arithmetic over the scenario | language-model agents, a judge, a recommender |
| Model of them | an ENSEMBLE: 64 draws, ±20% on their weights | one brief, argued in prose |
| Answers | do they accept this opening? what do they change? where do I land? | what will they say, and how do they justify it? |
| Cost | free, instant, no key | ~10 calls per condition |
| Used for | the product only | the product **and** the research question |

The response model is stated so it can be argued with: a party accepts anything
worth at least the split-the-difference deal to them; otherwise it counters with
the acceptable package closest to your opening, best for *them*; an option a
party values at exactly 0 is a red line it never accepts or proposes (§8.9).
Planning tries every opening that crosses none of your own red lines and ranks
by deal reached, then where you land, then outright acceptance.

The imagined rehearsal does not replace the debate and is not a condition. It
exists so the policymaker can find the openings worth arguing before paying for
an argument.

### How the frontier is built — stage 6

Four steps, before any model is called. No LLM is involved in any of them — this
is arithmetic over the scenario file, which is precisely why it can serve as a
yardstick for the LLM.

**Step 1 — Enumerate every possible deal.** One option per issue, all
combinations. The haze case has 4 issues at 3 levels, so 3⁴ = **81 settlements**.
That is the complete space: nothing outside it can be agreed.

**Step 2 — Score each settlement for each party.** Utility is additive — the
party's weight for an issue multiplied by its value for the chosen option,
summed across issues.

| Issue | SG weight | Indonesia weight |
|---|---|---|
| concession_maps | **0.35** | 0.25 |
| enforcement | 0.30 | **0.35** |
| peatland_moratorium | 0.25 | 0.10 |
| transition_funding | 0.10 | 0.30 |

Worked, on split-the-difference (every issue at its middle option):

```
Singapore  = 0.35×0.45 + 0.30×0.60 + 0.25×0.55 + 0.10×0.55 = 0.5300
Indonesia  = 0.25×0.60 + 0.35×0.45 + 0.10×0.60 + 0.30×0.50 = 0.5175
                                                      joint = 1.0475
```

Every one of the 81 settlements gets a pair like that — a point in outcome space.

**Step 3 — Discard the dominated.** A settlement is *dominated* if some other
settlement is at least as good for **everyone** and strictly better for someone.
Nobody would rationally sign a dominated deal, because another one on the table
beats it for both. From the haze case:

```
  withheld / indonesian_courts_only / existing_exempt / none
      → Singapore 0.155   Indonesia 0.700

  withheld / indonesian_courts_only / partial_exemptions / full_donor_fund
      → Singapore 0.1825  Indonesia 0.960     ← better for BOTH
```

The first is dominated and drops out. Of 81 settlements, **14 survive**. Those
14 are the Pareto frontier: the deals where helping one side now requires hurting
the other.

**Step 4 — Compute the reference points.**

| Reference | Value | What it is for |
|---|---|---|
| Max joint | **1.3575** | the best total value available — the ceiling |
| Split-the-difference | **1.0475** | every issue halved; the naive deal needing no negotiation |
| Joint value at stake | **0.3100** | the gap between them — what skilled negotiation is worth here |
| Max Nash product | — | the balanced benchmark, so a lopsided deal cannot look good |

### What the frontier actually shows you

On the haze case the joint-maximising settlement is:

```
concession_maps      full_to_asean_hms      Singapore's #1 issue  (w 0.35)
enforcement          indonesian_courts_only Indonesia's #1 issue  (w 0.35)
peatland_moratorium  full_no_exemptions     Indonesia's LAST      (w 0.10)
transition_funding   full_donor_fund        Singapore's LAST      (w 0.10)
                                     → SG 0.645, ID 0.712, joint 1.3575
```

Each side wins the issue it cares most about, and pays on the issue it cares
least about. That is the integrative trade, and it is worth **0.31 of joint
utility over splitting everything down the middle**. Finding it requires
discovering what the other side weights heavily — which neither agent is shown.
That is the whole experiment in one settlement.

### We advise Singapore — so why a joint criterion?

The obvious objection, and the first one a marker will raise: DARA is
Singapore-centred. The recommender advises Singapore. So why is the yardstick
the *Pareto frontier* and *joint* value, which are a mediator's criteria?

**Because Pareto is not a fairness criterion. It is a waste criterion.**

Checked against the haze case: of the 81 settlements, 67 are dominated, and for
**all 67** there exists another settlement that is *strictly better for
Singapore*. Not one is a case where being off the frontier means Singapore was
merely being generous. Off the frontier always means Singapore left value on the
table that it could have taken.

And this settles the objection outright:

```
Singapore's own best possible deal:  SG 1.000   Indonesia 0.062
                                     → and it IS on the frontier
```

The frontier is not the set of compromises. It is the set of non-wasteful deals,
and Singapore's dream outcome is one of them. So:

> **The frontier is the map — what is achievable at all.
> Singapore's utility is the destination — where on that map we are trying to get.**

You cannot navigate toward your own corner without knowing the shape of the
space you are navigating.

**Why the research question is still scored on joint value.** If we scored on
Singapore's utility alone, the winning strategy would be *demand everything*:
SG 1.000 is reachable with no debate, no judge, and no discovery of what
Indonesia cares about. One call gets it. Singapore-utility therefore **cannot
distinguish B2 from B0** — it would reward an agent that learned nothing. Joint
value recovered rises only if the process discovered that Indonesia weights
enforcement 0.35 and funding 0.30, which neither agent is shown. That discovery
is the NLP question.

**So two primary metrics, one per job (§1), and neither is "the" number:**

| | Primary metric | Answers |
|---|---|---|
| **Research question** | joint value recovered / integrative gain, **always with call counts** | did deliberation find the trade? |
| **Product** | Singapore's utility vs split-the-difference | did this help my client? |

Both are already computed by `score_package`. Reporting only the first would be
answering the marker's question and ignoring the user's.

**One inversion to keep in mind.** In the eval set we authored both valuation
tables, so joint value is exact. In production Indonesia's weights are the
user's *estimate* while Singapore's are known — so there, **Singapore's own
utility is the reliable number and joint value is the assumption-laden one.**
That is the reverse of the study set, and stage 11 exists to measure it.

### Why enumerate rather than sample

Because an approximate frontier moves between runs, and then "recovered 96.9%"
is an estimate with an error bar we would have to defend. At 81 points the exact
answer is free, so every efficiency number in this repo is a fact about the
constructed problem rather than a measurement of it.

### Three assumptions it rests on — and what each costs

1. **Additive utility.** No interaction between issues. Standard and cheap, but
   "I'll concede X only if I also get Y" is not representable as a *preference* —
   it can only appear as an argument in the debate.
2. **Discrete levels.** Three per issue keeps enumeration exact. The cost is
   granularity: "60% of the fund" cannot be expressed, only none/partial/full.
3. **Weights sum to 1 per party.** This puts both utilities on [0,1] so they can
   be added. **Joint utility is a scoring convention, not a welfare claim** — it
   does not assert that one unit of Singaporean utility equals one unit of
   Indonesian. This is why the Nash product is reported alongside it: the two can
   disagree, and when they do, that disagreement is a finding rather than a
   problem to be smoothed over.

### Do we still need the precomputed frontier?

**Yes. It is the single thing separating this from roleplay.** Remove it and:

- "recovered 96.9% of joint value" has no referent;
- "left 0.31 unclaimed" cannot be said;
- the RQ becomes unanswerable, because "better" collapses back to whether a
  human liked the prose;
- the product loses its only real claim — *you left value on the table* — and
  becomes a chatbot that acts out a negotiation.

Cost is not the objection. 3⁴ = 81 settlements; even 4⁵ = 1024 is instant. The
frontier is enumerated exactly, never sampled, so no efficiency number is ever
an estimate.

**But its epistemic status changes between the two jobs, and this must be said
on the face of the output:**

| | Eval set | Production |
|---|---|---|
| Your valuations | we authored them | the user's own — they know their priorities |
| Their valuations | we authored them | **the user's estimate** |
| So the frontier is | ground truth about the constructed problem | *the frontier under your stated assumption about them* |
| "Recovered 96.9%" means | exactly that | that, **conditional on the guess** |

Which is why stage 11 exists. In production the frontier is a lens, not a fact,
and the honest move is to show how much the recommendation depends on it.

---

## 3. Where every input comes from

We test the tool on scenarios **we** built. Users will run it on crises **they**
bring. Almost every input therefore has two different provenances, and the
failure modes are different in each column.

| # | Input | What it is | In the eval set (ours) | In production (theirs) |
|---|---|---|---|---|
| 1 | Parties | who is at the table | we author | user types |
| 2 | **Issues & options** | the menu of possible settlements | we author — **carries hindsight** (§9) | extraction from their documents, or typed |
| 3 | Your valuations | focus country's weights and option values | we author | **user ranks and marks** (§4, the form) — they know their own priorities |
| 4 | **Their valuations** | the opponent's weights | we author | **user ranks and marks their guess** — the weakest input in the system |
| 5 | Briefs | interests, constraints, red lines | we author from the public record | extraction from pasted documents, or web search |
| 6 | Style & tone | how a party speaks (§11) | we author; one-shots on unrelated topics | shipped defaults; user may override |
| 7 | Evidence cutoff | date beyond which nothing may be used | we set, per case | user sets; defaults to today |
| 8 | **Situational facts** | live constraints — a flood, an election, a committed budget (§11) | frozen in `eval/<case>/conditions.json`, every fact dated **≤ the evidence cutoff** | fetched live once per session: GDACS, World Bank, ReliefWeb, web search — then pinned |
| 9 | Reader + objective | who is asking, what they protect | fixed: `policymaker` × `none` | reader fixed (`policymaker`); user selects objectives |
| 10 | **Ground truth** | what was actually settled | we author, sourced per issue (§10) | **none exists** |
| 11 | Recorded debate | replay material so it runs with no key | model-generated, labelled as such | n/a — live calls |

Three rows deserve their own warning.

**Row 4 is the production error term.** In a real negotiation you do not know
the other side's utility function — that is the entire problem. Everything
downstream inherits that uncertainty, which is what stage 11 measures.

**Row 10 does not exist in production.** There is no ground truth for a
negotiation that has not happened. The tool must therefore never present a
historical-accuracy figure to a user running their own crisis; that number
belongs to the eval set only.

**Row 2 is the contamination.** See §9, and §4 for how it is meant to be solved.

---

## 4. Where the issue set comes from

This is the hardest input and the one everything else rests on. The issue set
*defines the space of possible deals* — you cannot trade something that is not on
the menu — so getting it wrong does not make the rehearsal slightly off, it makes
it a rehearsal of a different negotiation.

### What an issue actually is

> **An issue is a dimension on which the parties have taken opposed positions.**

That definition does the work. If both sides say the same thing, it is a settled
premise, not an issue. If only one side has spoken, it is a demand that has not
yet met resistance. A negotiation exists exactly where two stated positions
collide, which is what `ISSUE_PROMPT` in `extraction.py` asks the model to find:

> *"identify what was ACTUALLY BEING NEGOTIATED between {parties} as of {cutoff}
> … For each issue give 3 discrete settlement options ordered from one side's
> preference to the other's, and cite the document ids that establish it is in
> dispute."*

### The three options, and which one is invented

The two extremes come from the documents: each side's stated position becomes
one end. **The middle option is usually not stated by anyone** — it is
interpolated by the model.

```
  Indonesia said:  "concession maps are not for release"    → withheld
  Singapore said:  "full disclosure to the ASEAN system"    → full_to_asean_hms
  Neither said:    aggregated to district level             → INTERPOLATED
```

That interpolated middle is doing real work, because **the settlement the parties
eventually reach is frequently the one neither of them stated**. It is also the
most fragile element in the whole model, for the same reason: nothing in the
record supports it. It must be shown to the user as an assumption, not a finding.

### Three routes, in the order a user meets them

| Route | What the user gives | Where issues come from | Wait |
|---|---|---|---|
| **Fill the form** | issues and options, a ranking per side, good / acceptable / unacceptable per option | the user — `intake.py` turns it into a scenario | minutes |
| **Type them** | the issues directly | the user — they are preparing for this negotiation and usually know what is on the table | seconds |
| **Paste sources** | press statements, ministry releases, reporting | `extract_issues()` over the pasted text, under a cutoff | 10–60s |
| **Search** | a one-line summary | web search, then the same extraction | 5–30 min |

Typing them is not a fallback or an admission of failure. A policymaker
rehearsing a negotiation generally knows the four things being argued about;
extraction exists for the cases where they want the record checked against their
assumption, or where they are exploring a crisis they do not own.

### Review is the most important screen in the product

Whatever the route, the extracted issue set is shown before anything runs, and
nothing is applied unseen. Three failure modes make this non-negotiable:

| If the issue set… | Then |
|---|---|
| **misses an issue** | the trade that would have unlocked the deal is invisible; the rehearsal understates what is achievable |
| **invents an issue** | the frontier is inflated by a dimension nobody cares about, and the debate spends turns on it |
| **has the wrong options** | the settlement the parties would actually reach is not expressible, and the run cannot find it however well it argues |

That third case is already detectable in the study set: `backtest.compare`
returns `ground_truth_expressible: False` when the historical settlement cannot
be written in the issue set. In production there is no ground truth to catch it,
which is exactly why a human has to look.

### What extraction refuses to do

Carried over from `extraction.py` and not to be softened:

- **Cutoff discipline.** Documents after the cutoff are dropped and counted. An
  **undated** document is treated as *after* the cutoff — unknown dating must not
  be able to smuggle material in.
- **Never invent a position.** Where the documents do not establish a party's
  stance, it comes back `unknown` and the agent is told it is unknown.
- **Never hide a guess.** An element the model asserts without pointing at a
  document comes back `unsupported` rather than being dropped, because dropping
  it would make the brief look better sourced than it is.
- **No weights from extraction without review.** Emphasis in a document is weak
  evidence of how much a party actually cares. Proposed weights are always shown
  (§3, rows 3–4).

### The form — how words become numbers

A policymaker cannot give a weight; they can give an order and a verdict.
`intake.py` converts them by two stated rules:

- **Ranks → weights by rank-order centroid** (Barron & Barrett 1996). Ties share
  the average of their positions. Four issues give 52 / 27 / 15 / 6%.
- **Marks → option values:** good 1.0, acceptable 0.5, unacceptable 0.0. Every
  unacceptable option is also written into that party's red lines, so the
  debating agent is told it in words.

`intake.review()` reads the result back in sentences, and nothing is saved until
the person confirms (`POST /api/intake` previews; `"confirm": true` saves). That
is the REVIEW gate of §2 for this route. Checked on the haze case: the form alone
reproduces the authored case's best opening on 3 of 4 issues.

### Status

`extract_issues()` and `extract_party()` are written and tested against a
scripted backend. **Neither has run against a real model**, so the paste and
search routes are not wired up. Production today means **the form** — which
works, needs no numbers, and is honest about what it assumed.

---

## 5. Repository layout

The study set and the demo material are kept apart, because one is a research
fixture and the other is model-authored replay content, and mixing them is how a
generated transcript gets quoted as a finding.

```
backend/
  app.py                the one HTTP server (Flask); the console proxies to it
  crisis_debate/        the pipeline
    pipeline.py         B0/B1/B2 — the one engine (graph.py removed)
    experiment.py       RESEARCH: the RQ table with every qualifier (§13.5)
    imagine.py          PRODUCT: the world model — imagined rehearsal (§2)
    intake.py           PRODUCT: the plain-language form (§4)
  eval/                 THE STUDY SET — contains nothing model-generated
    haze_real/
      scenario.json     the problem: parties, issues, options, valuations
      ground_truth.json what was actually settled, sourced per issue
    covid_my_2020/
    user_*.json         crises users added — product inputs, NOT the study set
  demo/                 model-authored replays, so it runs with no key
    haze_real.json      every turn in here was written by a model
    covid_my_2020.json
  examples/             a crisis written as the form (haze_form.json)
  styles/               per-country voice — no strategic content (§12; planned)
  results/              experiment output (gitignored)
  runs/                 real run logs, JSONL (gitignored)
  tests/
```

The study set is exactly `experiment.STUDY_CASES`. A `ground_truth.json` must
carry its scenario's `scenario_id`; a test asserts it, because a mismatch made
the COVID backtest silently skip.

**`eval/` holds no model output.** A scenario and its ground truth are inputs and
facts; neither was generated by an LLM. **`demo/` is entirely model-authored**
and labelled so in its provenance field. If you ever cannot tell which folder a
file belongs in, it belongs in `demo/`.

---

## 6. In scope

- The bargaining problem: issues, options, private additive valuations.
- The payoff layer: exact frontier, Nash, integrative gain, inequality (§2),
  reported as two primaries — joint value for the RQ, Singapore's own utility
  for the product.
- **Situational awareness** — what is currently true that constrains each party
  (§11). Frozen for the study set, fetched live in production.
- Three conditions: B0, B1, B2 — one code path, differing only in structure.
- The three information barriers (§8).
- Measurement: judge validity, self-consistency, position bias, self-preference.
- The contamination audit that gates any backtest.
- Sensitivity of the advice to the opponent-valuation assumption and to
  conditions staleness (stage 11).
- Enough interface to *show* a rehearsal to a human.
- **A policymaker path that needs no numbers:** the form (§4) and the imagined
  rehearsal (§2), both free and instant, both outside the measurement.

## 7. Out of scope

Considered and declined.

- **Predicting what will happen.** DARA rehearses; it does not forecast.
  Situational facts say what is constraining a party *now*, which is a different
  claim from what they will do next.
- **A standing monitoring service.** DARA fetches conditions when you start a
  rehearsal, not continuously. A daily disruption monitor is a different product
  (that is ALORE).
- **Being right about real countries.** Positions are reconstructed from the
  public record and no government reviewed them. The claim is internal
  consistency, not correspondence.
- **A general negotiation platform.** Two parties, four issues, three levels.
  Widening this breaks exact enumeration, which is the basis of every metric.

---

## 8. Invariants

Break any of these and the numbers stop meaning anything. Each is a test.

1. **The recommender never sees the transcript in B2** — enforced by the
   function signature having no parameter for it.
2. **Private valuations never reach an opponent or the judge.** This asymmetry
   is what makes the debate an information-transmission problem.
3. **Country agents differ only by their brief** — and, once §12 lands, their
   style block. One template, filled per party.
4. **Illegal packages are recorded, never coerced.**
5. **Ground truth never reaches a prompt** — it lives in `eval/`, is read only
   by `backtest.py`, and a test greps the prompt-building modules to prove it.
6. **Recorded advice is never relabelled.**
7. **The provider path is announced, never inferred** — every run says `live` or
   `recorded`.
8. **Product features never feed the measurement.** `experiment.py` does not
   import `imagine` or `intake`, and form-entered crises (`user_*`) are never in
   `STUDY_CASES`. A test asserts both.
9. **Unacceptable is a hard constraint in the world model.** An option valued at
   exactly 0 is never in a planned opening for its owner, and never in a deal
   the other side accepts or proposes. If nothing survives, the briefing says
   "no deal", it does not invent one.
10. **Every experiment number is scored on the declared RQ pair** through
    `pipeline.rq_recommendation`, and reported with its call count.

---

## 9. The contamination gate

`docs/CONTAMINATION.md` in full. The operative finding:

Agent output is clean — no post-cutoff facts recalled. But the **issue sets carry
hindsight**: `peatland_moratorium` and `stay_in_singapore_with_support` both
post-date their evidence cutoffs and were chosen by an author who knew how the
negotiations ended. A leak scan cannot detect this, because a term written into
the issue set counts as supplied by construction.

**Consequence.** Until issue sets are derived from pre-cutoff documents by
`extraction.py`:

- absolute agreement with history is **not evidence of predictive skill**;
- the B2-vs-B0 comparison **is still valid** — all arms face the same menu.

---

## 10. Ground truth and backtesting

Each eval case carries `ground_truth.json`: what the parties actually settled
on, coded issue by issue, every issue carrying a URL, a publisher and a claim,
plus a declared confidence and written-down judgement calls.

Two numbers come out, and they answer different questions:

- **Agreement with history** — did we land on the settlement they reached?
  Interpretable *only* across conditions, per §9.
- **Joint value against history** — was our package *better* than the one they
  actually struck? This is the more useful claim, and it is not contaminated:
  the frontier does not care who authored the menu.

For a tool whose job is rehearsing a negotiation *before* you walk in, beating
the real outcome is worth more than matching it. A recommendation can disagree
with history on every issue and be the better deal — that is the outcome this
tool exists to produce.

---

## 11. Situational awareness — stage 4

**Why it is core, not an extra.** A party's brief says what they want in
general. It does not say that their fiscal space is committed to a flood
response this month, that an election is six weeks away, or that a drought has
already cut the supply they were going to trade away. Without that, the debate
is about an abstraction. With it, the rehearsal is about the negotiation you are
actually walking into — which is the whole point.

### What a situational fact is allowed to do

`situational.py` already encodes the right shape, and it must stay that way:

> **A situational fact has no field for a position.** It states what is true and
> what that makes infeasible. It never says what a party *wants*.

This matters because positions belong in the brief, where they are sourced and
auditable. A "fact" that smuggles in a stance is an unsourced brief edit. So a
fact **constrains** — it removes options from the *reachable* set. The frontier
at stage 6 is still computed over the full space, and the run reports how much
of the available value the conditions put out of reach.

```
  Fact:        Johor's reservoirs are at 38% and under an emergency drawdown order.
  Dated:       2019-08-12, from a named source
  Constrains:  removes `unrestricted_export` from the water issue
  Does NOT:    say Malaysia wants to charge more
```

### Two paths, because there are two jobs

| | **Study set** (haze, covid) | **Production** (the user's crisis) |
|---|---|---|
| Where from | `eval/<case>/conditions.json` — a file | GDACS, World Bank, ReliefWeb, web search |
| Dated | every fact **≤ the evidence cutoff** | every fact carries its fetch time |
| Fetched | never — it is a fixture | **once**, before the run, then pinned |
| Stale? | n/a, historical | snapshot expires after 12h and the UI says so on its face |
| Why | reproducible, and cannot import hindsight | the world moved since the case was written |

### Three rules that keep it from breaking the measurement

1. **A run never fetches.** Conditions resolve to a pinned `ConditionsSnapshot`
   *before* stage 6. Every condition in a comparison — B0, B1, B2 — reads
   the same `snapshot_id`. If one arm fetched separately, the arms would be
   negotiating in different worlds and the comparison would be void.
2. **Facts arrive unaccepted.** A fetched fact is a candidate until it is
   accepted into the snapshot. Nothing from an API silently changes the problem.
3. **In the study set, a post-cutoff fact is contamination.** A flood that
   happened after the evidence cutoff is hindsight, exactly like a post-cutoff
   issue option. `Source.is_before(cutoff)` already treats an undated source as
   *not* before — keep it that way.

### What this changes about the frontier — and what it deliberately does not

**The frontier does not move.** Conditions shrink what is *attainable*, not what
was theoretically best. A yardstick that shifted every time the world did would
make two runs incomparable, and the payoff layer exists precisely to stop that.

So a constrained run reports a second set of numbers instead:

```
  n_total                 81     settlements in the space
  n_reachable             54     still available under current conditions
  max_joint_unconstrained 1.358  the ceiling, unchanged
  max_joint_reachable     1.201  the best still attainable
  value_put_out_of_reach  0.157  what the conditions cost
```

That last line is the useful one, and it only exists because the frontier held
still: *these conditions have taken 0.157 of joint value off the table.*

A run still must not add a constraint mid-flight — the snapshot is pinned before
stage 6 and its id recorded on the run, so two runs being compared can be
checked to have read the same world rather than assumed to have.

---

## 12. Style and tone per country

**Why.** Every agent currently argues in the same register. A Singaporean
ministerial statement and an Indonesian one do not read alike, and a rehearsal
that flattens that is less useful for anticipating the room.

**Why it is dangerous.** It collides with invariant 3: if each country gets its
own exemplar, a behavioural difference may be the style rather than the brief,
and the brief-diversity ablation stops measuring assigned interests.

**The rule.**

> Style may carry *how* a party speaks. It must never carry *what* they want,
> concede, or refuse.

"Indonesia opens by invoking sovereignty" is strategic content and belongs in
the brief. "Indonesia writes long formal sentences with institutional subjects"
is style.

`backend/styles/<country>.json`:

```json
{
  "country": "Indonesia",
  "register": "Formal, institutional. Prefers the passive and the collective subject.",
  "sentence_shape": "Long, subordinate clauses, conclusion last.",
  "habits": ["Names the legal instrument before the position it supports"],
  "avoid": ["Contractions", "Rhetorical questions", "First-person singular"],
  "one_shot": {
    "prompt": "State a position on an unrelated matter: fishing quotas in a shared strait.",
    "response": "The management of shared stocks falls under the framework agreed in 2014, and any revision of quota allocations would properly proceed through the joint technical committee rather than through bilateral adjustment."
  }
}
```

The one-shot is deliberately **on an unrelated matter** — it shows the voice
while carrying no information about this crisis. That is what keeps it style
rather than a covert brief.

**Obligations:** invariant 3 amended and its test extended to mask style too; a
**style ablation** (on vs off, same seeds); a **leak check** asserting no issue
name or option value appears in any style file.

---

## 13. To do

Ordered so each item unblocks the next.

### 13.1 Make a live run possible — *blocks everything*
- [ ] `OPENAI_API_KEY` in `.env`; `pip install flask`.
- [ ] Run **one** cheap B0 live. `OpenAIBackend` has never executed — assume it
      is broken until it isn't.
- [x] Port the API tests to Flask's `test_client()`. `api/main.py` (FastAPI) removed;
      `app.py` is the one server.

### 13.2 Restructure to §5
- [x] `scenarios/` + `ground_truth/` → `eval/<case>/{scenario,ground_truth}.json`.
- [x] `cached/` → `demo/`.
- [x] Update `SCENARIO_DIR`, `CACHE_DIR`, `GROUND_TRUTH_DIR` and their tests.

### 13.3 Fix the measurement before measuring
- [x] Declare the RQ pair (`policymaker` × `none`) as a constant; stop scoring
      `recs[0]` (§12).
- [x] Assert every condition is scored on that same pair (`tests/test_experiment.py`).
- [x] Decide whether `graph.py` stays. Removed: it had drifted from `pipeline.py`
      (it dropped stage-4 conditions) and duplicated it.

### 13.4 Validate the judge — *before any between-condition claim*
- [x] Self-consistency: re-score one round k times; report agreement and MAD.
      *(built into `experiment`; needs a live run)*
- [x] Position bias: re-judge with argument order swapped. *(same)*
- [ ] If the judge fails here, stop and report *that*. It is a real finding.

### 13.5 Run the experiment
- [x] Runner: `python -m crisis_debate.cli experiment` — 2 cases × 3 conditions ×
      3 repeats + judge checks = **116 calls**, shown before spending; `--dry-run`
      free, `--live` to spend; temperature 0.7 by default (recorded as actually
      used). Logs JSONL per run under `results/`.
- [ ] **Run it live.** This is the step that answers the RQ; nothing above does.
- [x] Report call counts beside every comparison — with B0N gone they are the
      only thing keeping a budget-driven win from reading as a structural one.
- [x] Pre-declare **both** primaries before looking at any result (§2): joint
      value recovered for the RQ, Singapore's utility against
      split-the-difference for the product. Joint value and Nash *can* disagree,
      and choosing after the fact is choosing the answer.
- [x] Report agreement with history across conditions (§9), never absolutely.
- [x] Judge validity: Spearman between rubric score and the objective joint
      value of the package that argument proposed.

### 13.6 Stage 11 — sensitivity
- [x] Perturb the opponent's weights ±20%; re-map the frontier; report how far
      the recommended package moves. *(`stress.py`, attached to every experiment row)*
- [ ] Re-run against a conditions snapshot 24h older; report whether the advice
      changes. If it does, staleness is not cosmetic.
- [ ] Surface both in the UI as a confidence band, not a single number.

### 13.7 Style and tone (§12)
- [ ] `styles/singapore.json`, `styles/indonesia.json`; style slot in the one
      template; extend the symmetry test; leak check; ablation.

### 13.8 Situational awareness (§11)
- [ ] `eval/<case>/conditions.json` for both study cases, every fact dated at or
      before that case's evidence cutoff, each with a source.
- [ ] Wire `situational.constrain` into stage 4 so the frontier at stage 6 is
      computed over the surviving options.
- [ ] Pin the snapshot per run; assert in a test that all four conditions in one
      comparison share a `snapshot_id`.
- [ ] Test: a post-cutoff fact is rejected from a study-set snapshot.
- [ ] Production path: fetch once at session start, show the snapshot age, never
      fetch mid-run.

### 13.9 Only after the above
- [ ] Extraction-derived issue sets — lifts the contamination gate.
- [ ] Cases 3–10; add-crisis extraction; interventions reaching the live prompt.

### 13.10 Usability (decided 2026-10-22)
- [x] Plain-language form → scenario, with a read-back review (`intake.py`).
- [x] Imagined rehearsal: best openings and what-ifs, no key (`imagine.py`).
- [x] One server (Flask), one engine (`pipeline.py`); FastAPI and LangGraph removed.
- [x] Console: the form (`#/add`), the read-back review, and the rehearsed
      openings — nothing saved until confirmed.
- [x] Hide B0/B1/B2, rounds and frontier numbers from the product view. A
      **Policymaker | Research** switch in the console header (default
      Policymaker, remembered per browser). Policymaker view runs B2 × 3 rounds
      only, shows ranks instead of weights at review, the imagined openings and a
      what-if on every case, and results as "% of your best case". Research view
      is the full console, unchanged.

### Stop doing
- [ ] No more frontends. One is enough.
- [ ] No new objectives or audiences until §13.5 reports a number.
- [ ] **Amended 2026-10-22:** the ban on new product surfaces is lifted for the
      usability path in §13.10 only, because each piece answers §15.4 — it is
      flagged and excluded from the metric by invariant 8. Anything beyond
      §13.10 waits for §13.5.

---

## 14. Known defect to fix before any run — fixed

Fixed: `graph.py` is gone, and `crisis_debate/experiment.py` scores every
condition through `pipeline.rq_recommendation`. Kept for the record:

`graph.py:126` scored `recs[0]`, and `DebateRun.recommendation` returns
`recommendations[0]`. Since the two-reader feature landed, "the" result is
whichever reader happens to be first in the list — an arbitrary choice sitting
underneath the headline number. Fix per §13.3.

---

## 15. How to tell if a change belongs

1. Does it help someone rehearse a negotiation Singapore is walking into?
2. Does it make the B2-vs-B0 comparison sharper, or at least not blunter?
3. Does it preserve every invariant in §8?
4. If it is a product feature, can it be flagged and excluded from the metric?
   (The test for this is invariant 8: if `experiment.py` would have to import
   it, it is not a product feature.)

**Two "no"s and it does not go in.** A "yes" to 1 and a "no" to 2 is allowed —
that is a product feature — but it must answer 4.
