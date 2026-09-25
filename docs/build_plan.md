# DARA — build plan

**D**ebate · **A**djudicate · **R**ecommend · **A**ssess — the four stages of the
pipeline, in order. *Dara*: dove.

Singapore-centred crisis negotiation rehearsal. Before Singapore enters a
negotiation, simulate it against a model of the counterpart built from what that
counterpart has actually done.

Any crisis where two or more states must coordinate under
conflicting interests: transboundary haze, pandemic border and supply measures,
water and energy disputes, cyber incidents, export controls. The machinery is
indifferent to the domain; the scenario supplies it.

This file is the spec. Everything the system does, or is meant to do, is here.

---

## 1. Current state

| Module | Does |
|---|---|
| `schemas.py` | Pydantic models; every agent returns a validated one |
| `utilities.py` | Payoff layer: additive utilities, exact Pareto frontier, outcome scoring |
| `agents.py` | Country agents, judge, recommender, single-call baseline |
| `pipeline.py` / `graph.py` | Conditions; direct loop and LangGraph, pinned equal by test |
| `providers.py` | OpenAI key in `.env` → live; absent → cached replay, tagged everywhere |
| `provenance.py` | `Source`, `Evidence`, `Sourced` — status: sourced / projected / unsupported / unknown / user_edited |
| `briefs.py` | Three-layer versioned briefs, content hashing, diff, `administration_change()` |
| `situational.py` | Situational facts, conditions snapshot, constrain / reweight, reachable-value report |
| `signals.py` | Live adapters — GDACS and World Bank **verified**; ReliefWeb needs a registered appname |
| `extraction.py` | Documents → sourced draft brief, extracts the **issue set** too, cutoff-disciplined |
| `contamination.py` | Leak scan and issue-set hindsight audit |
| `cached/` | Recorded run for the haze case |
| `metrics.py` | Brief consistency, position movement, judge self-consistency, position bias, judge validity |
| `frontend/index.html` | Pipeline rail with tooltips, chat debate, intervention gate, citations, outcome space, add-crisis flow |

**103 tests pass**, no API key and no network required.

**Partly built:** the add-crisis flow (§7.1). Steps 1, 2, 3, 5 and 6 — summary,
parties, evidence route, review-and-edit, save — are live in the console, and a
saved scenario is scored in full against the same frontier the built-in cases
use. Step 4, extraction, is not: it is a model call and the console has no
network, so it states that on its face and hands the user a blank review screen
rather than inventing a brief. A user-created scenario therefore has no recorded
debate, and Run is disabled for it with the reason given.

**Not built:** cases 3–10 — they wait on extraction running against real
documents, which needs a key.

**Never executed:** `OpenAIBackend`. Run one cheap B0 before any sweep.

---

## 2. Your queue — what needs a human

Everything below is blocked on a person, not on more code.

### 2.1 Blocking almost everything

- [ ] **Put `OPENAI_API_KEY` in `.env`.** Without it: no extraction, no live runs,
      no cases 2–10, and contamination probes P1–P3 cannot run.
- [ ] **Run one cheap B0 first.** `OpenAIBackend` has never executed. If
      `with_structured_output` rejects one of the Pydantic schemas, that is where
      it surfaces — find out on one call, not on a sweep.
- [ ] **Decide where the key lives for shared runs.** `.env` is gitignored, so
      either every member has their own or one person runs the sweeps.

### 2.2 Before the proposal goes in

- [ ] Two student IDs — Graciela Wangshyanata and Sunjushre D/O Vengadesh Naidu.
- [ ] The repository URL.
- [ ] Compile the `.tex` and **check it renders** — no LaTeX was available here.
- [ ] Decide the framing: debate-centred (current) or brief-centred. Sections 1, 4
      and 4.1 change; section 2 mostly survives.

### 2.3 Verify before citing — do not take these on trust

Each was written from recall and is flagged in the documents themselves.

- [ ] The 2015 haze excess-mortality figure, against Koplitz et al. (2016).
- [ ] Smit et al. — full citation details for the MAD-strategies paper.
- [ ] Case 2 COVID·MY: MCO from 18 March 2020, ~300,000 daily commuters.
- [ ] Case 3: June 2022 chicken export halt, Singapore's share of supply.
- [ ] Case 4: 1962 Johor River Water Agreement, the raw water price dispute.
- [ ] Case 5: the three agreements signed 25 January 2022 and the sequencing dispute.
- [ ] Case 6: the 2021 "Singapore variant" dispute and Covishield recognition.
- [ ] Case 7: LTMS import volumes and start date.
- [ ] Case 8: which Article 6 implementation agreements Singapore has signed, and when.
- [ ] Case 9: Australia's current critical-minerals export and processing policy.
- [ ] Case 10: Linggiu Reservoir levels in 2016.

### 2.4 Needs people, not a key

- [ ] **Two annotators** for the extraction evaluation (build order step 2), and a
      reported agreement figure. This gates everything downstream of extraction.
- [ ] **Publish the console** somewhere the team can open it — GitHub Pages serves
      `frontend/` as-is.
- [ ] **Register a ReliefWeb appname** if you want that source; v1 is 410 Gone and
      v2 is 403 without one.

### 2.5 Decisions only you can make

- [ ] Ship all ten cases, or the core four?
- [ ] Intervention window default — currently 10s, with 3s / none / pause available.
- [ ] If the contamination check fails on a new case: score the reasoning path,
      or drop the case?
- [ ] Claim the supply-chain generalisation in the proposal's Section 1, or leave it out?

---

## 3. Cases

Country A is Singapore unless a case says otherwise. Eight scenarios live in
`simulations/` — six historical, four prospective. See §8.

### 3.1 Historical (6)

All are Singapore-centred. Indonesia and Malaysia dominate because they are who
Singapore has actually negotiated transboundary crises with — but case 6 reaches
beyond the two neighbours so the historical set is not a single bilateral
relation seen five times.

| # | Case | Counterpart | Year | Type | Status |
|---|---|---|---|---|---|
| 1 | Transboundary haze | Indonesia | 2015–16 | environmental | built, cached |
| 2 | COVID-19 border and supply measures | Malaysia | 2020 | health / mobility | to build |
| 3 | Chicken export ban | Malaysia | 2022 | food security / trade | to build |
| 4 | Johor River water pricing | Malaysia | 2018–19 | resource / treaty | to build |
| 5 | FIR realignment, extradition and defence package | Indonesia | 2022 | airspace / security | to build |
| 6 | COVID-19 travel corridor, vaccine recognition and the variant dispute | **India** | 2021 | health / mobility | to build |

Case 5 is the most useful addition for this tool specifically: Singapore and
Indonesia negotiated the Flight Information Region adjustment, an extradition
treaty and a defence cooperation agreement **as one bundle**, signed together in
January 2022. A package deal across unlike issues is exactly the integrative
structure the payoff layer is built to measure — one side trading an issue it
weights lightly for one the other weights heavily.

Anchor facts to verify before building each: COVID — Malaysia's Movement Control
Order from 18 March 2020, roughly 300,000 daily cross-border commuters, separate
tracks for worker accommodation and for food and goods flow. Chicken — Malaysia
halted live chicken exports in June 2022; Singapore sourced roughly a third of
its chicken from Malaysia. Water — the 1962 Johor River Water Agreement and the
recurring dispute over the raw water price. FIR — the three agreements signed
25 January 2022 and the sequencing dispute that followed ratification.

**Consequence of dropping the non-Singapore case.** There is no longer a test of
whether the tool generalises beyond Singapore's own negotiations. That is a
deliberate scope choice; state it as a limitation rather than letting a reader
assume the method was shown to transfer.

### 3.2 Prospective, set in the present (4)

Real cases are historical: their relevance is arguable and their outcomes are
already known, which is what contaminates them (§14). Three prospective
scenarios are set now, on crises that have not happened, so the tool can be
demonstrated on something current.

**Real countries, hypothetical crisis.** Fictional counterparts were considered
and rejected: an invented country has no ReliefWeb entry, no World Bank
indicators and no press, so the evidence layer — the thing these cases exist to
demonstrate — would have nothing to extract.

| # | Case | Counterpart | Why it is plausible |
|---|---|---|---|
| 7 | Low-carbon electricity import under a transmission constraint | **Vietnam** | Singapore has granted conditional approvals to import low-carbon power from the region, and already imports from Laos through the LTMS project |
| 8 | Article 6 carbon credit agreement under an integrity dispute | **Ghana** | Singapore's carbon tax lets firms offset part of their liability with international credits, which requires bilateral implementation agreements with host countries |
| 9 | Critical minerals processing and export licensing | **Australia** | Australia holds the reserves and a stated processing policy; Singapore is the regional trading and financing hub |
| 10 | **Johor River drought and water supply shortfall** | **Malaysia** | The nearest-term crisis of the four. Linggiu Reservoir fell to critical levels in 2016; a deeper drought would leave Malaysia unable to deliver Singapore's treaty entitlement |

**Deliberately not Malaysia or Indonesia.** The five historical cases are
concentrated on Singapore's two neighbours because that is who Singapore has
actually negotiated these crises with. The prospective set therefore reaches
further out — a mainland Southeast Asian supplier, an African credit host, and an
OECD resource exporter — so the tool is not shown only on one bilateral relation.

Each is genuinely multi-issue, which is what the payoff layer needs: case 6
trades volume against wheeling fees, carbon accounting and firm-supply
guarantees; case 8 trades credit price against the host's share of proceeds,
corresponding adjustments and integrity standards; case 9 trades export volume
against onshore processing requirements, pricing and offtake duration; case 10
trades allocation cuts against Linggiu drawdown rights, emergency desalination
cost-sharing and the duration of any temporary arrangement.

*Verify before building: the LTMS import volumes and start date, which Article 6
implementation agreements Singapore has actually signed and when, and Australia's
current critical minerals export and processing policy.*

**What is real and what is not, in each of these:**

| Layer | Status |
|---|---|
| Structural | **real and sourced** — dependence ratios, trade flows, economy |
| Situational | **real and sourced** — live fetch, same as any other case |
| Positional | **projected**, and labelled so. Derived from genuinely stated positions on analogous issues, never presented as a statement the government has made about this crisis |
| The crisis itself | **hypothetical**, labelled on the face of the scenario |

The rule that matters: the system may place a real country in a hypothetical
situation — that is what scenario planning is — but it may never assert that a
government has said something it has not. Any positional element without a source
is marked *projected*, with its confidence shown.

These cases also carry something the historical ones cannot: **immunity to
hindsight contamination**, because no outcome exists to recall.

## 4. Architecture — three layers

```
evidence layer   retrieved documents, timestamped, with provenance, web scrape it if needed
      ↓          (scheduled or manually triggered — NEVER inside a run)
brief layer      a versioned brief derived from that evidence
      ↓          (pinned and hashed at run time)
agent layer      reads one frozen brief version, and nothing else
```

A run pins a brief version. The tool stays current because re-ingestion makes a
new version; any single run stays reproducible because it reads a frozen one.

### 4.1 The brief has three layers, each on its own clock

| Layer | What it holds | Changes | Updated by | Cadence |
|---|---|---|---|---|
| **Structural** | enduring facts: geography, dependence, economy | slowly; survives a change of government | manual | quarterly |
| **Positional** | what the government has *said* about this negotiation | overnight | paste an article, edit directly, or a ministry feed | weekly, or on hearing something |
| **Situational** | current conditions affecting what a party *can* offer | daily | one-click fetch, pinned to the run | per run |

Examples, for the haze case:

- **Structural** — Indonesian smallholders clear by fire because it costs a
  fifteenth of mechanised clearing. Singapore imports over 90% of its food.
- **Positional** — Indonesia will not release parcel-level concession maps.
- **Situational** — severe flooding in Java this month; fiscal room and
  ministerial attention are both gone.

A coup or change of administration wipes the **positional** layer and leaves the
structural layer standing. The brief then reports *structural interests
unchanged, stated positions unknown as of N days ago, confidence low* rather than
inventing a stance for a government that has not taken one. **No coup detector is
needed** — a change of government is a large positional diff.

Structural facts justify the valuation weights. Stated positions justify the red
lines. Situational facts justify neither; they constrain (§4.3).

### 4.2 Situational signals — where they come from

All are structured fetches, not browser automation. Sub-second, in parallel.

| Signal | Source | What it changes |
|---|---|---|
| Hazard, live | NASA FIRMS active fire hotspots; OpenAQ or national AQI | Is the crisis burning *today*? For haze this is the core signal |
| Disaster / humanitarian | ReliefWeb (UN OCHA), GDACS | Capacity to spend and to pay attention |
| Political calendar | election guides, national commissions | A government months from an election cannot sell an unpopular concession |
| Fiscal / macro | World Bank Indicators, IMF | Capacity to fund |
| Commodity / trade | FAO food price index, energy prices, UN Comtrade | The stakes in a resource dispute |
| Health | WHO outbreak reporting | The pandemic-type case |
| Conflict | ACLED | Attention and bargaining posture |

*Verify access terms per source before use; ACLED requires registration.*

### 4.3 How a situational fact reaches the model

Prose in the prompt is not enough — the agent would mention the flood and
negotiate identically. A fact must do one of two things:

- **Constrain** — remove settlements from the feasible set. Indonesia cannot fund
  X this quarter, so packages requiring it come off the table. The frontier stays
  where it is; the *reachable* part of it shrinks. Preferred: it is visible in the
  outcome-space chart.
- **Reweight** — shift valuation weights. Use sparingly; it is harder to justify.

**A situational fact may never invent or alter a stated position.** That is the
positional layer's job, and keeping the two separate is what stops a news story
silently overriding a documented commitment.

Each fact carries `(source, url, as_of, fetched_at, effect, confidence)` so the
citation button can show *why* the recommendation moved.

### 4.4 Relevance is the hard part

Deciding which current events matter to *this* negotiation is a judgment call and
the likeliest place for the system to go wrong: over-reacting to unrelated news,
or letting a situational note quietly override a documented position. Every
accepted fact is shown to the human with its proposed effect before the run.

## 5. Scraping: cadence, latency, cost

**Rule: a simulation run never scrapes.** Two reasons, the first decisive:

1. **It would break the evaluation.** If every run fetched fresh evidence, B0, B1
   and B2 would read different facts and no comparison would be valid. Users also
   re-run the same scenario repeatedly; those runs must be comparable.
2. TinyFish automation is slow. The client in ALORE uses a 1800-second timeout,
   and real runs take minutes to tens of minutes per goal.

### 5.1 Latency budget, shown to the user before they choose

| Path | Latency | Use |
|---|---|---|
| Run on a stored brief | instant | default; every simulation |
| User types a description | seconds | fast path for a new scenario |
| User pastes articles or text | 10–60 s | medium path; extraction runs on what they paste |
| User give documents (pdf or docx) | 60–120 s | medium path; extraction runs on what they upload |
| **Web scraper (TinyFish)** | **5–30 min** | opt-in only, with the estimate on the button |

The scraper toggle must state the wait before it is pressed, e.g. *"Search the
web for sources — usually 5–30 minutes. Use the paste option if you need this
now."*

### 5.2 Cadence

| What | When |
|---|---|
| Structural layer | quarterly, or manual |
| Positional layer | weekly scheduled |
| Manual refresh | any time, user-triggered, one scenario at a time |
| Per-scenario cooldown | no automatic re-scrape within 24 h |
| Dedup | skip documents already stored; only new ones are extracted |

### 5.3 Situational fetches are pinned, not cached

Situational signals are sub-second, so it is tempting to fetch them every run.
Do not. The objection is not speed, it is **comparability** — the same one that
rules out per-session scraping, and latency does not fix it. If run A fetches at
09:00 and run B at 14:00 and a flood report lands between them, B0, B1 and B2
read different worlds and the comparison is void. A user re-running one scenario
twenty times would get twenty slightly different worlds, plus twenty identical
API calls and a run that fails when a source is down.

The rule:

- Fetch on demand, in parallel, sub-second — so it feels live.
- **The run records the snapshot it used**, with timestamps and per-source
  provenance.
- Re-running the same scenario reuses the pinned snapshot unless the user presses
  **Refresh conditions**.
- A snapshot goes stale after 12 hours and the UI says so on its face.

This is pinning for reproducibility, not caching for speed.

For the coursework, **no scheduler is required.** Run ingestion by hand when a
new brief version is wanted. Versioning carries the value; automation is optional.

---

## 6. How it runs, end to end

Three phases, each on its own clock.

### Phase 1 — Create a scenario *(once per crisis, minutes)*

1. **Name it.** One sentence plus the parties. Country A defaults to Singapore.
2. **Choose the evidence route** — describe it (seconds), paste sources
   (10–60 s), or search the web (5–30 min, estimate on the button).
3. **Extraction builds a draft brief**: issues, options per issue, stated
   positions, red lines, proposed valuation weights — each element carrying the
   quote and source it came from.
4. **Review and correct.** The weights especially are never applied unseen; they
   are the most contestable element in the system. Edits are attributed to the
   user, not to a document.
5. **Save** as scenario v1 / brief v1. Every later run reuses it.

### Phase 2 — Refresh conditions *(optional, seconds, before a run)*

Press **Refresh conditions** and the situational sources in §4.2 are fetched in
parallel, under two seconds. Each candidate fact is shown with its proposed
effect:

> **Severe flooding, Java, as of 3 days ago** — ReliefWeb
> Effect: *constrain* — packages requiring Indonesian domestic spend this quarter
> are unavailable. `[accept] [reject] [citation]`

Accepted facts become a conditions snapshot pinned to the run. Skip the step and
the previous snapshot is used, with its age displayed.

### Phase 3 — The run *(no network except the model)*

The run pins three things — scenario version, brief version, conditions snapshot
— and records them in the log, so it can be reproduced exactly. Then:

1. **Frontier.** Every settlement enumerated and scored locally, instantly.
   Situational constraints apply here: the frontier is unchanged, the reachable
   part of it shrinks.
2. **Three rounds.** Each party argues and commits to a package, seeing only its
   own valuations. The judge scores both, seeing none. After each judge, the
   intervention window (the citations section).
3. **Recommender.** Judge scores only; transcript withheld.
4. **Scoring.** Final package against the frontier.

Nothing here touches the network except model calls. That is what makes B0, B1
and B2 comparable: identical pinned inputs.

### Phase 4 — After

Re-run freely; identical pinned inputs, comparable results, no fetching. Refresh
conditions or update the brief to get v2, and the **diff** reports how far the
recommendation moved — simultaneously the product alert and the experiment.

### Engineering setup

`OPENAI_API_KEY` in `.env` runs live. Blank or absent replays the recorded runs
— same pipeline, zero cost. That is how the tests and the demo work today.

---

## 7. Scenarios: built-in and user-created

Built-in scenarios are JSON in `scenarios/`. Users must also be able to add their
own, or the tool is a demo rather than a tool.

### 7.1 Add-crisis flow

1. **One-sentence summary.** Required. *"Haze from Indonesian peatland fires has
   pushed Singapore's air quality into the hazardous band."*
2. **Parties.** Two or more names. Country A defaults to Singapore.
3. **Evidence source — a toggle, one of three:**
   - **Describe it** — free text. Seconds.
   - **Paste sources** — articles, statements, URLs as text. 10–60 s.
   - **Search the web** — TinyFish. 5–30 min, estimate shown on the control.
4. **Extraction** produces a draft brief: issues, options, per-party stances,
   red lines, proposed valuation weights — each element carrying its source.
5. **Review and edit.** The user can correct any element before saving. Edits
   persist and are attributed to the user, not to a source.
6. **Save** as a versioned scenario.

### 7.2 Guardrails on user scenarios

- A brief element with no supporting evidence is marked *unsupported* and the
  agent is told it is unsupported, rather than the element being dropped silently.
- Where evidence does not establish a position, the brief says **unknown**. Agents
  must not invent one.
- Valuation weights proposed by extraction are always shown for review; they are
  the most contestable element and must never be applied unseen.

---

## 8. `simulations/` — the proof set

Ten scenarios whose **entire input came through the user-facing add-crisis
flow**, not hand-authored JSON. That is the point of the folder: it demonstrates
that a user can bring a crisis the tool has never seen and get a working
simulation out of it. If a scenario here had to be hand-edited into shape, the
flow is not finished.

### 8.1 Layout

```
simulations/
  real/
    01-haze-sg-id-2015/
      input.md            exactly what the user typed or pasted
      draft-brief.json    what extraction produced, untouched
      edits.json          every human correction, attributed and dated
      scenario.json       the saved result
      conditions.json     pinned situational snapshot
      provenance.json     route, per-stage timings, sources, model + version
      runs/               B0, B1, B2 logs
    02-covid-sg-my-2020/
    03-chicken-sg-my-2022/
    04-water-sg-my-2018/
    05-fir-sg-id-2022/
    06-covid-sg-in-2021/
  prospective/
    07-power-sg-vn/
    08-carbon-sg-gh/
    09-minerals-sg-au/
    10-drought-sg-my/
  README.md               index, and the one-line claim each case supports
```

### 8.2 Build priority

Ten cases is more than one semester allows if each is built fully. Four are the
core; the rest are stretch, and the coverage table says what each adds.

| Tier | Cases | Why |
|---|---|---|
| **Core** | 1 haze · 2 COVID·MY · 10 drought·MY · 8 carbon·GH | one built already, two most urgent, one non-neighbour prospective |
| Stretch | 3, 4, 5, 6, 7, 9 | breadth of crisis type and counterpart |

### 8.3 What each case must record

- **Which evidence route** was used: describe, paste, or search.
- **Wall-clock time** for extraction, and for the scraper where used. These are
  the numbers quoted in the latency budget, and once these exist they should come
  from measurement rather than estimate.
- **How much the human changed.** Edited elements over total, so extraction
  accuracy carries a number. This feeds the extraction evaluation in the build
  order.
- **Unsupported elements** — brief entries extraction could not source. Reported,
  not hidden.
- **Contamination result** for every real case.

### 8.4 Coverage the ten cases are chosen to give

| Dimension | Covered by |
|---|---|
| Evidence route: describe / paste / search | at least two cases each |
| Crisis type: environmental, health, trade, resource, energy, carbon | 1 · 2, 6 · 3, 9 · 4, 5, 10 · 7 · 8 |
| Counterpart spread | Indonesia 1, 5 · Malaysia 2, 3, 4, 10 · India 6 · Vietnam 7 · Ghana 8 · Australia 9 |
| Historical vs present | 1–6 vs 7–10 |
| Contaminated vs clean | 1–6 vs 7–10, which have no outcome to recall |

### 8.5 Rules

- Prospective cases use real countries in a hypothetical crisis, labelled as
  hypothetical. Their structural and situational layers are sourced; positional
  elements without a source are marked *projected* and never presented as
  statements the government has made.
- A simulation folder is never edited by hand after the fact. To change a
  scenario, re-run the flow and save a new version.
- Every case ships with its run logs, so numbers in the report trace back to the
  exchange that produced them.

---

## 9. Pipeline

Stages, with the hover text each must show in the UI.

| Stage | Hover text |
|---|---|
| **case + briefs** | Gives the agents their context: the crisis, each party's interests, and its own private valuations. |
| **frontier** | Scores every possible settlement before any agent runs, so the yardstick cannot move to fit the result. |
| **⟨party⟩** | This party argues and commits to a package. It sees only its own valuations, never the other side's. |
| **judge** | Scores both arguments 1–5 on five criteria. Holds no brief and sees no valuations. |
| **recommender** | Builds the advice from the judge's scores only — the debate transcript is withheld. |
| **scoring** | Scores the final package against the frontier. Illegal packages are recorded, never coerced. |

Three rounds. Round 1 sees only the scenario and own brief; rounds 2–3
additionally see the opposing argument and the judge's note on the party's own
previous turn. Every stage emits a schema-validated JSON record and is appended
to a JSONL run log.

### 9.1 Agent roles

- **Country-agents.** Propose and defend settlements consistent with their brief.
  An argument contains position, claims with evidence, concessions, red lines,
  and a concrete package naming one option per issue.
- **Judge.** No brief, no valuations. Rubric: feasibility, specificity, trade-off
  acknowledgement, interest consistency, responsiveness. Each 1–5 with a written
  justification. Also records trade-offs neither side addressed, which are fed to
  both agents next round.
- **Recommender.** Does not participate. Receives the judge's assessments, not the
  transcript. Emits conditional options — condition → action, with the priority
  served, the concession required and the principal risk — plus one opening
  package.

---

## 10. Human intervention — the misinformation guardrail

**After every judge output, a 3-second window with an Intervene button.**

- Not pressed within the window → the pipeline continues automatically.
- Pressed → pause. Show the judge's scores and summary in full. The human may
  edit the summary, flag a claim as misinformation, or reject the round and
  re-run it.
- Every intervention is logged: what was shown, what was changed, by whom, when.
  A run that was intervened in is tagged, and tagged runs are excluded from
  reported metrics unless the report says otherwise.

**Make the window configurable and default it higher than 3 s in practice.** Three
seconds is not long enough to read a judge summary, so as a pure auto-continue
timer it will mostly expire unused. Options: keep 3 s as the auto-advance default
but pause indefinitely on hover, or raise the default to 10 s in interactive mode
and 0 s in batch.

---

## 11. Citations

Every claim a user can see must be traceable.

- Brief elements carry `(source_id, url, quote, date, who_said_it)`.
- A **citation button** on each brief element, and on each agent claim that cites
  one, opens the supporting quote and its source.
- Agent turns cite brief elements by id, so a claim in the debate resolves back to
  a document.
- Staleness is shown on the face of the brief: *"positional layer 4 days old;
  funding position sourced from a 2015 statement."*
- Where there is no source, the citation button says so rather than being hidden.

---

## 12. Conditions and ablations

**Three conditions.**

| | Runs | Isolates |
|---|---|---|
| **B0** | one model call, no debate, no judge | the baseline the research question is stated against |
| **B1** | three rounds, no judge; recommender reads the transcript | the judge's contribution |
| **B2** | three rounds, judge each round, transcript-blind recommender | the full system |

B0 spends one call against B2's ten, so **report calls per run alongside every
result** — a reader must be able to see the budget asymmetry even though there is
no compute-matched arm.

**Ablations.**

- **Round count** (r = 1, 2, 3). Measure position movement per round; near-zero
  movement means the round restated rather than advanced.
- **Brief diversity.** Identical briefs for both agents. If the debate still looks
  adversarial, the disagreement is sampling noise.
- **Judge self-consistency.** Re-score one fixed round k times; report
  per-criterion agreement and mean absolute deviation.
- **Position bias.** Re-judge with argument order swapped and nothing else changed.
- **Self-preference.** Judge arguments from its own model family against another's.
- **Brief swap.** Give an agent the opposing brief; proposals should invert.

---

## 13. Metrics

**Outcome** (`utilities.py`) — joint value recovered, Pareto optimality, distance
to frontier, Nash ratio, inequality, and **integrative gain**: joint value won
above splitting every issue down the middle. Splitting needs no deliberation, so a
process that cannot beat it has discovered nothing.

**Process** (`metrics.py`) — brief consistency (red-line integrity, own-utility
tracking), position movement, judge self-consistency, position bias, and judge
validity: Spearman between a party's summed rubric score and the joint value of
the package it proposed that round. Near zero means the judge is decorative.

**Pre-declare the primary comparison.** A pilot run on a Nile-dam case — since
dropped from the set as out of scope — showed the single-call baseline recovering
*more* joint value while the debate produced a settlement that was far more equal
and won on Nash:

| Cond | Joint recovered | Nash | Inequality | Pareto |
|---|---|---|---|---|
| B0 | **93.4%** | 0.75 | 0.47 | yes |
| B2 | 92.5% | **0.84** | **0.19** | no |

B0 wins on joint value with a settlement worth 0.84 to one side and 0.37 to the
other — which that side would never sign. **Which metric is privileged decides
the winner.** Joint value recovered is therefore the pre-declared primary
outcome, with Nash ratio and inequality reported beside it, chosen before any
result is seen rather than after.

Cite this as a pilot observation, not as a result of the shipped case set.

---

## 14. Contamination check — keep, and re-run per case

Status: **run, see `CONTAMINATION.md`.**

- **Agent output is clean.** No post-cutoff facts recalled in any turn of either case.
- **Issue sets are contaminated.** `peatland_moratorium` (haze), `au_panel` and
  `binding_with_review` (from the dropped Nile-dam pilot) all post-date their
  cutoffs and were chosen by an author who knew the outcomes. A leak scan cannot detect this, because a term
  written into the issue set counts as supplied by construction.

**Consequence:** the issue set must itself be derived from pre-cutoff documents.
Until it is, any backtest is reported as illustrative, not evidential.

**Run this on every new case, including user-created historical ones.** Probes P1
(free recall), P2 (cued prediction) and P3 (discrimination) need an API key and
are written and ready in `contamination.py`.

---

## 15. Design invariants — do not break

1. **The recommender never sees the transcript in B2.** Enforced by
   `RecommenderAgent.recommend` having no parameter for arguments, guarded by a
   test. Remove it and B1 isolates nothing and the judge is decorative.
2. **Country-agents differ only by their brief.** One prompt template. Guarded by
   a test that masks each brief and asserts the remaining skeletons are identical.
3. **Private valuations never reach an opponent, and never reach the judge.** A
   judge that saw the payoffs would score outcomes and the judge-validity
   correlation would become it reading the answer key.
4. **Illegal packages are recorded, never coerced.** Snapping to the nearest legal
   option would change the utility vector and corrupt every efficiency number.
5. **A run never scrapes.** §5.
6. **Nothing invents a position.** Unknown is a valid brief value.

---

## 16. Success criteria

(a) Each country-agent demonstrably represents its brief — brief swap inverts its
proposals, it opens near its own utility maximum and concedes measurably, and it
never concedes what its brief forbids.

(b) The judge's scores are reproducible under re-scoring and stable under
argument-order swap, and we report whether they correlate with objective
settlement quality.

(c) We can state whether debate beats the single-call baseline on joint value
recovered, with call counts reported.

A negative answer to any of these is a result this design can produce and would
report.

---

## 17. Build order

1. ~~**Position extraction + provenance.**~~ **DONE** — Documents in,
   `(party, issue, stance, evidence quote, date, source)` out. The NLP core.
   Must extract the **issue set** as well as the stances — §14.
2. **Evaluate the extraction before building on it.** Held-out documents; two
   members annotate a sample independently; report agreement. If briefs are wrong,
   everything downstream is confidently wrong.
3. ~~**Versioned briefs**~~ **DONE** — structural and positional layers, staleness on the face.
4. **Wire into the pipeline** — swap the brief source; pipeline unchanged.
5. ~~**Add-crisis flow** with the three evidence toggles and the latency estimates.~~
   **DONE except extraction** — the sheet takes a summary, parties and stances, the
   evidence route (seconds / 10–60s / 5–30 min, stated on each card), and a
   review-and-edit grid for issues, options and valuations; on save the scenario is
   scored against the same frontier as the built-in cases. Extraction (step 4) needs
   a key, so the console says so instead of inventing a brief, and Run stays disabled
   on a user scenario because there is no recorded debate to play.
6. ~~**Human intervention** after each judge~~ **DONE** — configurable window, edits feed the next round, runs tagged.
7. ~~**Citations** surfaced in the UI~~ **DONE** — per-element source, post-cutoff sources flagged in the panel.
8. **COVID case** built from the evidence layer, not hand-authored.
9. **Brief-diff experiment** — old brief vs new; measure how far the
   recommendation moves. Both the product alert and the experiment.
10. **Backtest**, if contamination permits.

---

## 18. Risks

| Risk | Mitigation |
|---|---|
| Hindsight contamination | the contamination section gates the backtest; otherwise score the reasoning path |
| Extraction from news is noisy; parties posture | Step 2 gates step 3 |
| Sourced ≠ true — models what a party *said*, not what it would accept | State it plainly in the report |
| Scraper latency wrecks the experience | Never in a run; estimates shown before opt-in |
| Valuation weights are inferred, not sourced | Derive from structural facts; always shown for review |
| Judge shares a model family with the debaters | Self-preference ablation |
| Small effects, few runs | Multiple seeds, paired comparison, one pre-declared primary outcome |
| User-supplied sources may be misinformation | Human intervention after each judge; citations on every claim |
| `OpenAIBackend` unexecuted | One cheap B0 before any sweep |

---

## 19. Open questions

1. Is case 5 the FIR/extradition/defence package, or the 2007 Indonesian sand
   export ban? The package deal is the better fit for multi-issue bargaining; the
   sand ban is simpler to source.
2. Who annotates for step 2, and what agreement figure is acceptable?
3. Intervention window default — 3 s auto-advance, or longer in interactive mode?
4. If the contamination check fails on a new case, which fallback: score the
   reasoning path, or drop the case?
5. Where does the OpenAI key live for shared runs?
