<p>
  <img src="/brand/dara-logo-primary.png" alt="" height="156">
</p>

---

Before entering a negotiation, a policymaker needs to anticipate what the other
side will refuse and what it will trade. DARA builds a model of each party from
its documented positions, simulates the negotiation between language-model
agents, and scores the outcome against the best settlement that was actually
available.

The crisis is posed as a multi-issue bargaining problem: four issues with
discrete settlement levels and a **private** utility function per party. Every
proposal therefore maps to a point in outcome space with an exactly enumerated
Pareto frontier — so "a better recommendation" means *recovered more of the
available joint value*, not *read better*.

## Layout

```
backend/            Python package, tests, scenario and recorded-run data
  crisis_debate/    the pipeline
  scenarios/        the problem: parties, issues, options, private valuations
  cached/           the answers: recorded agent turns for offline replay
  tests/            103 tests, no API key and no network required
frontend/           the console — a single self-contained page
docs/               build plan and the contamination report
brand/              logo and palette
```

## Quick start

### Backend

```bash
python3 -m venv .venv
source .venv/bin/activate                  # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt

cd backend
pytest -q                                  # tests run with no key and no network
python -m crisis_debate.cli scenarios      # what's bundled
python -m crisis_debate.cli providers      # would this run be live or cached?
python -m crisis_debate.cli run --scenario haze_real --condition B2
```

Run the CLI from `backend/` — `pytest.ini` puts the package on the path, and the
scenario and recorded-run folders are resolved relative to it.

### Frontend

The console is one self-contained page with no build step and no dependencies.
Serve `frontend/` and open the address it prints:

```bash
python3 -m http.server 8000 --directory frontend
# → http://localhost:8000
```

Any static server does the same job — `npx serve frontend`, or the "Open with
Live Server" command in VS Code. Opening `frontend/index.html` directly as a
`file://` URL also works today, since the page fetches nothing; a server is the
safer habit and is what GitHub Pages will do when you publish `frontend/`.

The console runs entirely in the browser and never calls the backend: it replays
recorded turns embedded in the page itself, the same runs `backend/cached/` holds
for the Python side. So the two halves start independently — there is no "server
first, then client" order to get right.

### Live vs recorded

Copy `.env.example` to `.env` and set `OPENAI_API_KEY` to run against a live
model. Leave it blank and the pipeline replays the recorded runs in
`backend/cached/` — the same pipeline, at zero cost, which is how the tests and
the demo work. Every run prints which mode it used; nothing silently pretends to
be live.

## The pipeline

| Stage | What it does |
|---|---|
| briefs | Each party gets its interests, red lines and its own private valuations |
| frontier | Every settlement is enumerated and scored *before* any agent runs, so the yardstick cannot move to fit the result |
| debate ×3 | Each party argues and commits to a concrete package, seeing only its own valuations |
| judge | Scores both arguments 1–5 on five criteria. Holds no brief, sees no valuations |
| recommender | Builds the advice from the judge's scores only — the transcript is withheld |
| scoring | The final package against the frontier |

Three conditions: **B0** one call, no debate · **B1** debate without a judge ·
**B2** the full system. Call counts are reported alongside every result, so the
budget difference between them is visible.

## Design invariants

Four rules the tests enforce, because breaking any of them quietly invalidates
the results:

1. **The recommender never sees the transcript in B2.** Enforced by the function
   signature having no parameter for it. Remove the barrier and B1 isolates
   nothing and the judge becomes decorative.
2. **Country agents differ only by their brief.** One prompt template; a test
   masks each brief out and asserts the remaining skeletons are identical.
3. **Private valuations never reach an opponent, and never reach the judge.**
4. **Illegal packages are recorded, never coerced.** Snapping an out-of-range
   option to the nearest legal one would change the utility vector and corrupt
   every efficiency number computed from it.

## Cases

Two are built; `docs/build_plan.md` §3 lists the ten planned.

| Case | Parties | B2 | B0 |
|---|---|---|---|
| Transboundary haze, 2015–16 | Singapore · Indonesia | 96.9% of joint value | 74.4% |
| COVID-19 border and supply, 2020 | Singapore · Malaysia | 100% | 56.7% |

## A limitation you should read before citing anything

Both cases are **hand-authored, not extracted from documents**, and the
contamination audit in `docs/CONTAMINATION.md` shows what that costs. Agent
output is clean — no post-cutoff facts are recalled — but the *issue sets* carry
hindsight: `peatland_moratorium` and `stay_in_singapore_with_support` are both
datable after their evidence cutoffs, and both were chosen by an author who knew
how the negotiations ended.

A leak scan cannot detect this, because a term written into the issue set counts
as supplied by construction. Until the issue sets are derived from pre-cutoff
documents by the extraction layer, any backtest on these cases is illustrative
rather than evidential.

Recorded agent turns were generated by a language model and are labelled as such
in each file's provenance field. They are not transcripts of real negotiations.

## Status

Built: payoff layer, three conditions, three-layer versioned briefs with
provenance, situational signals (GDACS and World Bank verified live), extraction,
contamination audit, and the console — tooltips, a human-intervention gate,
per-claim citations and the add-crisis flow.

Partly built: **add-crisis**. The console takes a summary, the parties, an
evidence route (described / pasted / web-searched, each with its wait shown) and
a review grid for issues, options and valuations, and scores what you save
against the same frontier the built-in cases use. Extraction — turning documents
into a sourced issue set — is a model call, so the offline console says so and
hands you a blank grid rather than inventing a brief. A scenario you add has no
recorded debate, so Run stays disabled on it.

Not built: cases 3–10, which wait on extraction running against real documents.
`docs/build_plan.md` §2 lists what needs a human.
