/* DARA console — plain browser JavaScript, no framework and no build step.
   Talks to the Flask API through this server's /api proxy, so every URL here is
   relative and there is no CORS to configure. */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));
const pretty = (s) => String(s ?? "").replace(/_/g, " ");
const fmt = (n, d = 3) => Number(n).toFixed(d);
const pkgLine = (pkg) => Object.entries(pkg || {}).map(([k, v]) => `${pretty(k)} = ${pretty(v)}`).join(" · ");

/* ------------------------------------------------------------------ api */
async function api(path, options) {
  const res = await fetch("/api" + path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch { /* not JSON — keep the status line */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

/* ---------------------------------------------------------------- state */
let health = null;
let detail = null;          // the loaded scenario + frontier
let board = {};             // the settlement currently on the board
let trail = [];             // utility vectors the debate walked through
let abort = null;
let stages = [];
let confirmed = false;      // the human review gate — see drawGate()
let replayable = null;      // objectives this recording holds; null = live, no limit
let canRun = false;         // a recording exists, or a key does

/* ------------------------------------------------------------ view mode */
/* Two audiences, one page (CONTRACT.md §1). Policymaker view hides the research
   apparatus — conditions, rounds, the pipeline, frontier metrics, judge scores —
   and words results as "% of your best case". Research view is the full console.
   Markup opts in with .px (policymaker only) and .rx (research only); the choice
   is a per-viewer convenience, so localStorage, and it must work without it. */
const MODE_KEY = "dara.view";
const isResearch = () => document.body.classList.contains("research");
function setMode(mode) {
  document.body.classList.toggle("research", mode === "research");
  document.querySelectorAll(".modeseg button").forEach((b) =>
    b.setAttribute("aria-checked", String(b.dataset.mode === mode)));
  try { localStorage.setItem(MODE_KEY, mode); } catch { /* private window: fine */ }
  if (mode !== "research") {
    // The policymaker runs the full system; a research choice left in a hidden
    // select must not silently change what "Hear them argue it" does.
    $("cond").value = "B2"; $("rounds").value = "3";
    stages = []; drawStages();
    showTab("debate");
  }
}
document.querySelectorAll(".modeseg button").forEach((b) => { b.onclick = () => setMode(b.dataset.mode); });

/* --------------------------------------------------------------- router */
function route() {
  const hash = location.hash || "#/";
  const run = hash.match(/^#\/case\/(.+)$/);
  $("viewHome").hidden = hash !== "#/";
  $("viewAdd").hidden = hash !== "#/add";
  $("viewRun").hidden = !run;
  if (hash === "#/") loadHome();
  else if (hash === "#/add") drawAddForm();
  else if (run) loadCase(run[1]);
  window.scrollTo(0, 0);
}
addEventListener("hashchange", route);

/* --------------------------------------------------------------- header */
function drawProvider() {
  const el = $("providerPill");
  if (!health) { el.className = "pill off"; el.innerHTML = '<span class="dot"></span>checking…'; return; }
  if (health.mode === "live") {
    el.className = "pill live";
    el.title = `Live calls to ${health.model}`;
    el.innerHTML = `<span class="dot"></span>live · ${esc(health.model)}`;
  } else {
    el.className = "pill rec";
    el.title = "No OPENAI_API_KEY in .env — runs replay the recording in demo/";
    el.innerHTML = '<span class="dot"></span>recorded';
  }
}

/* ----------------------------------------------------------------- home */
async function loadHome() {
  try {
    const [cases, h] = await Promise.all([api("/scenarios"), api("/health")]);
    health = h;
    drawProvider();
    $("caseCount").textContent = `${cases.length} loaded`;

    $("homeNote").innerHTML = h.mode === "recorded"
      ? `<div class="note" style="margin-bottom:20px">No <b>OPENAI_API_KEY</b> in <b>.env</b>, so runs
         replay the recorded debate in <b>backend/demo/</b> — the same pipeline, at no cost. Add a
         key and the identical run calls the model live; nothing else changes.</div>`
      : "";

    const add = `<a class="case add" href="#/add">
        <span class="plus">+</span><h3>Add a crisis</h3>
        <p class="desc">Bring a negotiation DARA has never seen. Name the sides and the issues, then
        mark each outcome good, OK or no for each side. No numbers.</p></a>`;

    $("gallery").innerHTML = add + cases.map((c) => `
      <div class="case" role="button" tabindex="0" data-open="${esc(c.file)}">
        <div class="top">
          <div><h3>${esc(c.title)}</h3><div class="who">${esc(c.parties.join(" · "))}</div></div>
          ${c.has_recording
            ? '<span class="pill rec" title="A recorded run exists, so this plays with no key">recorded</span>'
            : '<span class="pill off" title="No recorded run — needs a live model call">no recording</span>'}
        </div>
        <p class="desc">${esc(c.description)}</p>
        <div class="facts">
          <div><b>${c.issues}</b><span>issues</span></div>
          <div class="rx"><b>${c.settlements}</b><span>settlements</span></div>
          <div class="rx"><b>${c.pareto_count}</b><span>on frontier</span></div>
          <div class="rx"><b>${fmt(c.at_stake, 2)}</b><span>at stake</span></div>
          ${c.user_created ? `<button class="mini" style="margin-left:auto;align-self:center"
             data-delete="${esc(c.file)}">delete</button>` : ""}
        </div>
      </div>`).join("");

    $("gallery").querySelectorAll("[data-open]").forEach((el) => {
      const go = () => { location.hash = `#/case/${el.dataset.open}`; };
      el.onclick = go;
      el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } };
    });
    $("gallery").querySelectorAll("[data-delete]").forEach((b) => {
      b.onclick = async (e) => {
        e.stopPropagation();
        if (!confirm("Delete this scenario? This removes the file.")) return;
        await api(`/scenarios/${b.dataset.delete}`, { method: "DELETE" });
        loadHome();
      };
    });
  } catch (e) {
    // The proxy already explains an unreachable API in full, so don't prefix it
    // again — a doubled "Cannot reach the API — Cannot reach the API at…" reads
    // like two separate failures.
    $("gallery").innerHTML = `<div class="err">${esc(e.message)}</div>`;
  }
}

/* ------------------------------------------------------------------ run */
async function loadCase(id) {
  try {
    if (!health) { health = await api("/health"); drawProvider(); }
    detail = await api(`/scenarios/${id}`);
  } catch (e) {
    $("runError").innerHTML = `<div class="err">${esc(e.message)}</div>`;
    return;
  }
  const s = detail.scenario, f = detail.frontier;

  $("runMeta").textContent = `${s.countries.map((c) => c.name).join(" · ")} · advising ${s.focus_country}`;
  $("runTitle").textContent = s.title;
  $("runLede").textContent = s.description;
  $("runStats").innerHTML = `
    <div class="stat"><b>${f.settlements}</b><span>possible settlements</span></div>
    <div class="stat"><b>${f.pareto_count}</b><span>on the frontier</span></div>
    <div class="stat hot"><b>${fmt(f.at_stake, 2)}</b><span>joint value at stake</span></div>`;

  const opts = health.priorities
    .map((p) => `<option value="${p.id}" title="${esc(p.blurb)}">${esc(p.label)}</option>`).join("");
  const dflt = health.default_priorities || [health.default_priority];
  $("priority1").innerHTML = opts;
  $("priority2").innerHTML = opts;
  // In recorded mode only the objectives that were actually recorded can be
  // replayed; offering the others would fail mid-run.
  replayable = health.mode === "live" ? null : (detail.recorded_priorities || []);
  $("priority1").value = dflt[0] || health.default_priority;
  $("priority2").value = dflt[1] || health.priorities.find((p) => p.id !== $("priority1").value).id;
  if (replayable && replayable.length) {
    if (!replayable.includes($("priority1").value)) $("priority1").value = replayable[0];
    if (!replayable.includes($("priority2").value)) {
      $("priority2").value = replayable.find((p) => p !== $("priority1").value) || replayable[0];
    }
  }
  syncPriorities();

  canRun = detail.has_recording || health.mode === "live";
  $("runNote").textContent = health.mode === "live" ? "live model calls"
    : detail.has_recording ? "replaying the recording" : "no recording for this case";
  $("runError").innerHTML = canRun ? "" : `<div class="note warn" style="margin-bottom:16px">
      This case has no recorded run and there is no <b>OPENAI_API_KEY</b> in <b>.env</b>. Its payoff
      structure below is exact; the debate needs a model.</div>`;

  board = { ...f.split_package };
  trail = [];
  $("chat").innerHTML = "";
  $("resultCard").hidden = true;
  // A new crisis is a new problem, so the previous confirmation cannot carry
  // over — that would be the gate approving something nobody looked at.
  drawGate(); setConfirmed(false);
  if (!isResearch()) { $("cond").value = "B2"; $("rounds").value = "3"; stages = []; }
  drawStages(); drawBoard(); drawParties(); drawPlot(); scoreBoard();
  showTab("debate");
  drawImagine(id);
}

/* ---- imagined rehearsal (policymaker) ---- */
/* The world model: the best openings and a what-if, from arithmetic alone, so
   the page has advice on it before anyone pays for a debate. */
const caseLabel = (s) => { const t = pretty(s); return t.charAt(0).toUpperCase() + t.slice(1); };
async function drawImagine(id) {
  $("imagineBody").innerHTML = '<p class="status">Rehearsing…</p>';
  $("whatIfOut").innerHTML = "";
  const sc = detail.scenario;
  const me = sc.countries.find((c) => c.name === sc.focus_country) || sc.countries[0];
  $("whatIfPick").innerHTML = sc.issues.map((i) => {
    const vals = (me.valuations.find((v) => v.issue === i.name) || {}).option_values || {};
    const first = [...i.options].sort((a, b) => (vals[b] ?? 0) - (vals[a] ?? 0))[0];
    return `<label class="lab">${esc(caseLabel(i.name))}
      <select data-issue="${esc(i.name)}">${i.options.map((o) =>
        `<option value="${esc(o)}" ${o === first ? "selected" : ""}>${esc(caseLabel(o))}</option>`).join("")}</select></label>`;
  }).join("");
  $("whatIfBtn").onclick = async () => {
    const opening = {};
    $("whatIfPick").querySelectorAll("select").forEach((s) => { opening[s.dataset.issue] = s.value; });
    try {
      const r = await api(`/scenarios/${id}/imagine`, { method: "POST", body: JSON.stringify({ opening }) });
      $("whatIfOut").innerHTML = renderOpenings(r.rehearsals, caseLabel, ["Your opening"]);
    } catch (e) { $("whatIfOut").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  };
  try {
    const r = await api(`/scenarios/${id}/imagine`, { method: "POST", body: "{}" });
    if (location.hash.split("/").pop() !== id) return;      // navigated away meanwhile
    $("imagineBody").innerHTML = r.rehearsals.length
      ? `<p class="status" style="margin-top:0">${esc(r.rehearsals[0].summary[0])}</p>`
        + renderOpenings(r.rehearsals, caseLabel)
      : `<div class="err">Every possible opening crosses one of your own red lines.</div>`;
  } catch (e) { $("imagineBody").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}

/* ---- objectives ---- */
function priorityLabel(id) {
  return ((health?.priorities || []).find((p) => p.id === id) || {}).label || id;
}
function selectedPriorities() {
  return [$("priority1").value, $("priority2").value].filter(Boolean);
}
/* The same objective twice would spend two calls to answer one question, so the
   option chosen on one side is disabled on the other rather than deduplicated
   silently. */
function syncPriorities() {
  const a = $("priority1"), b = $("priority2");
  const off = (o, other) =>
    o.value === other.value || (replayable !== null && !replayable.includes(o.value));
  [...a.options].forEach((o) => { o.disabled = off(o, b); });
  [...b.options].forEach((o) => { o.disabled = off(o, a); });
  const n = replayable === null ? 0 : replayable.length;
  $("replayNote").textContent = replayable === null ? ""
    : `recorded mode — ${n} of ${health.priorities.length} objectives are replayable without a key`;
}

/* ---- the human review gate ---- */
/* Stage REVIEW. Everything downstream is computed correctly *about whatever is
   confirmed here*, which is exactly why an unreviewed issue set produces
   confident, precise, wrong answers. So the run stays locked until a person has
   looked at the issues, their options and the weights. */
function drawGate() {
  if (!detail) return;
  const sc = detail.scenario;
  const focus = sc.focus_country;
  const me = sc.countries.find((c) => c.name === focus) || sc.countries[0] || {};
  const w = {};
  (me.valuations || []).forEach((v) => { w[v.issue] = v.weight; });
  // equal weights share a rank, so a tie entered on the form reads as a tie
  const distinct = [...new Set(Object.values(w))].sort((a, b) => b - a);
  const RANK = ["Most important", "2nd", "3rd", "4th", "5th", "6th"];
  const rankOf = (name) => RANK[distinct.indexOf(w[name] ?? 0)] || "—";
  $("gateBody").innerHTML = `
    <table class="gt">
      <thead><tr>
        <th>Issue</th><th>Options it can settle at</th><th><span class="rx">Weight</span><span class="px">Matters</span> to ${esc(focus)}</th>
      </tr></thead>
      <tbody>${sc.issues.map((i) => `<tr>
        <td class="gi">${esc(pretty(i.name))}</td>
        <td>${i.options.map((x) => `<span class="gopt">${esc(pretty(x))}</span>`).join("")}</td>
        <td class="gw"><span class="rx">${fmt(w[i.name] ?? 0, 2)}</span><span class="px">${rankOf(i.name)}</span></td>
      </tr>`).join("")}</tbody>
    </table>`;
}

function setConfirmed(v) {
  confirmed = v;
  $("gateCard").classList.toggle("ok", v);
  $("gateBtn").textContent = v ? "Confirmed — reopen to change" : "Confirm and unlock the run";
  $("gateState").textContent = v
    ? `confirmed — ${detail ? detail.scenario.issues.length : 0} issues, ${detail ? detail.frontier.settlements : 0} possible settlements`
    : "not confirmed — the run is locked";
  refreshRunBtn();
}

function refreshRunBtn() {
  $("runBtn").disabled = !canRun || !confirmed;
  $("runBtn").title = !canRun ? "no recording for this case, and no API key"
    : !confirmed ? "confirm the problem above first" : "";
}

/* ---- pipeline rail ---- */
function buildStages(cond, rounds, parties) {
  const s = [{ id: "setup", grp: "Setup", lbl: "briefs" }, { id: "frontier", grp: "Setup", lbl: "frontier" }];
  if (cond === "B0") {
    selectedPriorities().forEach((id) => s.push({ id: `rec.${id}`, grp: "Baseline", lbl: priorityLabel(id).toLowerCase() }));
  } else {
    for (let r = 1; r <= rounds; r++) {
      parties.forEach((p) => s.push({ id: `r${r}.${p}`, grp: `Round ${r}`, lbl: p.toLowerCase() }));
      if (cond === "B2") s.push({ id: `r${r}.judge`, grp: `Round ${r}`, lbl: "judge" });
    }
    selectedPriorities().forEach((id) => s.push({ id: `rec.${id}`, grp: "Advice", lbl: priorityLabel(id).toLowerCase() }));
  }
  s.push({ id: "score", grp: "Advice", lbl: "scoring" });
  return s.map((x) => ({ ...x, status: "idle", ms: 0 }));
}
function drawStages() {
  const parties = detail ? detail.scenario.countries.map((c) => c.name) : [];
  if (!stages.length) stages = buildStages($("cond").value, +$("rounds").value, parties);
  const groups = [];
  stages.forEach((st) => {
    const last = groups[groups.length - 1];
    if (last && last.name === st.grp) last.items.push(st);
    else groups.push({ name: st.grp, items: [st] });
  });
  $("rail").innerHTML = groups.map((g) => `<div class="grp"><span class="gl">${esc(g.name)}</span>
    <div class="nodes">${g.items.map((st) => `<div class="node" data-s="${st.status}">
      <span class="dotrow"><i class="dot"></i><i class="bar2"></i></span>
      <span class="lbl">${esc(st.lbl)}</span>
      <span class="t">${st.status === "done" ? (st.ms / 1000).toFixed(1) + "s" : st.status === "run" ? "running" : "—"}</span>
    </div>`).join("")}</div></div>`).join("");
}
function mark(id, status, ms) {
  const st = stages.find((x) => x.id === id);
  if (st) { st.status = status; if (ms != null) st.ms = ms; drawStages(); }
}

/* ---- chat ---- */
function bubble(side, name, round, text, pkg, concessions, score) {
  const initials = name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  const legal = score && score.legal;
  const line = legal
    ? `joint ${fmt(score.joint_utility)} · ${score.is_pareto_optimal ? "on the frontier" : "off the frontier"} · recovered ${(score.joint_value_recovered * 100).toFixed(1)}%`
    : score ? `Not legal: ${esc((score.problems || []).join("; "))}` : "";
  return `<div class="msg ${side}"><div class="av">${esc(initials)}</div>
    <div class="bub"><div class="nm">${esc(name)} · round ${round}</div>
      <div class="bd">${esc(text)}</div>
      <div class="attach"><b>Proposed package</b>${esc(pkgLine(pkg))}
        ${concessions && concessions.filter(Boolean).length ? `<span class="cede">Concedes: ${esc(concessions.filter(Boolean).join("; "))}</span>` : ""}
        <span class="cede rx" style="color:var(--ink-3)">${line}</span>
      </div></div></div>`;
}
function say(html) { $("chat").insertAdjacentHTML("beforeend", html); const c = $("chat"); c.scrollTop = c.scrollHeight; }

/* ---- the run ---- */
async function runCase() {
  const cond = $("cond").value;
  const rounds = +$("rounds").value;
  const parties = detail.scenario.countries.map((c) => c.name);

  abort = new AbortController();
  stages = buildStages(cond, rounds, parties);
  trail = [];
  $("chat").innerHTML = "";
  $("runError").innerHTML = "";
  $("resultCard").hidden = true;
  $("runBtn").hidden = true; $("stopBtn").hidden = false;
  $("pipeMeta").textContent = "running";
  drawStages(); showTab("debate");

  const q = new URLSearchParams({
    scenario: detail.scenario.scenario_id && location.hash.split("/").pop(),
    condition: cond, rounds: String(rounds), speed: $("speed").value,
    priorities: selectedPriorities().join(","),
  });

  let recIndex = 0;
  try {
    const res = await fetch(`/api/run?${q}`, { signal: abort.signal, headers: { Accept: "text/event-stream" } });
    if (!res.ok || !res.body) throw new Error(`run failed: ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let split;
      while ((split = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        for (const line of frame.split("\n")) {
          if (!line.startsWith("data:")) continue;
          let ev;
          try { ev = JSON.parse(line.slice(5).trim()); } catch { continue; }
          recIndex = onEvent(ev, recIndex);
        }
      }
    }
  } catch (e) {
    if (!abort.signal.aborted) $("runError").innerHTML = `<div class="err">${esc(e.message)}</div>`;
  } finally {
    $("runBtn").hidden = false; $("stopBtn").hidden = true;
    abort = null;
  }
}

function onEvent(ev, recIndex) {
  if (ev.type === "error") {
    $("runError").innerHTML = `<div class="err">${esc(ev.message)}</div>`;
    return recIndex;
  }
  if (ev.type === "start") {
    mark("setup", "done", 1); mark("frontier", "done", 1);
    say(`<div class="divider"><span class="rx">${esc(ev.condition)} · </span>${esc(ev.mode)}</div>`);
    return recIndex;
  }
  if (ev.type === "argument") {
    const side = ev.country === detail.scenario.countries[0].name ? "left" : "right";
    mark(`r${ev.round}.${ev.country}`, "done", ev.elapsed * 1000);
    if (!$("chat").querySelector(`[data-r="${ev.round}"]`))
      say(`<div class="divider" data-r="${ev.round}">Round ${ev.round}</div>`);
    say(bubble(side, ev.country, ev.round, ev.position, ev.package, ev.concessions, ev.score));
    if (ev.score && ev.score.legal) {
      trail.push(detail.scenario.countries.map((c) => ev.score[`utility_${c.name}`] ?? 0));
      drawPlot();
    }
    return recIndex;
  }
  if (ev.type === "judgment") {
    mark(`r${ev.round}.judge`, "done", ev.elapsed * 1000);
    say(`<div class="sys"><div class="hd">Judge · round ${ev.round}</div>
      ${ev.judgments.map((j) => `<div class="row"><span class="who2">${esc(j.country)}</span>
        <span class="pk rx">${j.total}/25</span><span class="sm">${esc(j.summary)}</span>
        <span class="rx">${Object.entries(j.scores).map(([k, v]) => `<span class="sc">${esc(pretty(k))} ${v}</span>`).join("")}</span>
      </div>`).join("")}
      ${ev.unaddressed.length ? `<div class="un">Unaddressed: ${esc(ev.unaddressed.join("; "))}</div>` : ""}
    </div>`);
    return recIndex;
  }
  if (ev.type === "recommendation") {
    mark(`rec.${ev.priority}`, "done", ev.elapsed * 1000);
    const label = priorityLabel(ev.priority);
    if (ev.index === 0) {
      board = { ...ev.package };
      say(`<div class="sys final reccompare">
        <div class="hd">Recommendations — one per objective, same crisis, same reader</div>
        <div class="reccols" id="recCols"></div></div>`);
    }
    const cols = $("recCols");
    if (cols) {
      cols.insertAdjacentHTML("beforeend", `<div class="reccard">
        <div class="top"><span class="sc prio">${esc(label)}</span></div>
        <h4>${esc(ev.headline)}</h4>
        <div class="bd">${esc(ev.rationale)}</div>
        ${ev.options.map((o) => `<div class="opt2">If ${esc(o.condition)} → ${esc(o.action)} <em>${esc(pretty(o.priority_tag))}</em></div>`).join("")}
        <div class="pk">${esc(pkgLine(ev.package))}</div></div>`);
    }
    drawBoard(); drawParties(); scoreBoard();
    return recIndex + 1;
  }
  if (ev.type === "done") {
    mark("score", "done", 1);
    $("pipeMeta").textContent = `${fmt(ev.elapsed, 1)}s · ${ev.usage.calls || 0} calls`;
    drawResults(ev);
    if (isResearch()) showTab("analysis");
    else $("resultCard").scrollIntoView({ behavior: "smooth", block: "start" });
  }
  return recIndex;
}

function drawResults(ev) {
  $("resultCard").hidden = false;
  const cards = ev.results.map((r) => {
    const label = priorityLabel(r.priority);
    const s = r.score;
    const share = (u) => s.focus_utility_best > 1e-9 ? `${Math.round((u / s.focus_utility_best) * 100)}%` : "—";
    return `<div class="reccard">
      <div class="top"><span class="sc prio">${esc(label)}</span></div>
      <h4>${esc(r.headline)}</h4>
      ${s.legal ? `<div class="stats px">
        <div class="stat"><b>${share(s.focus_utility)}</b><span>of your best case</span></div>
        <div class="stat"><b>${share(s.focus_utility_split)}</b><span>if you just split every issue</span></div>
      </div>` : ""}
      ${s.legal ? `<dl class="ro rx">
        <dt>For ${esc(s.focus_country || "")}</dt>
          <dd class="${s.focus_gain_over_split > 0 ? "up" : s.focus_gain_over_split < 0 ? "down" : ""}">${s.focus_gain_over_split >= 0 ? "+" : ""}${fmt(s.focus_gain_over_split ?? 0, 3)} vs split</dd>
        <dt>Joint value recovered</dt><dd>${(s.joint_value_recovered * 100).toFixed(1)}%</dd>
        <dt>Nash ratio</dt><dd>${fmt(s.nash_ratio, 2)}</dd>
        <dt>Inequality</dt><dd>${fmt(s.inequality, 2)}</dd>
        <dt>Integrative gain</dt><dd>${fmt(s.integrative_gain, 2)}</dd>
        <dt>Pareto-optimal</dt><dd>${s.is_pareto_optimal ? '<span class="tick y">yes</span>' : '<span class="tick n">no</span>'}</dd>
      </dl>` : `<p class="withheld">Not legal: ${esc((s.problems || []).join("; "))}</p>`}
      <div class="pk">${esc(pkgLine(r.package))}</div>
      <button class="mini rx" data-board='${esc(JSON.stringify(r.package))}'>put on the board</button>
    </div>`;
  }).join("");

  const verdict = ev.objectives_diverged
    ? `<p class="diverge"><b>The two objectives open differently</b> on the same crisis, for the same
       reader. The gap between these boxes is the price of the choice: whichever one you take, the
       other column is what you gave up to take it. That trade is the thing to walk into the room
       knowing.</p>`
    : `<p class="diverge"><b>Both objectives open the same way</b> on this crisis. That is a finding
       rather than a fault — here you are not being asked to trade one against the other.</p>`;

  $("results").innerHTML = `<div class="reccols">${cards}</div>${verdict}
    <p class="status rx"><b>${esc(ev.condition)}</b>, ${esc(ev.mode)} · ${ev.usage.calls || 0} calls · ${fmt(ev.elapsed, 1)}s</p>
    ${ev.illegal_packages.length ? `<p class="pk rx">Illegal packages recorded (never coerced): ${esc(ev.illegal_packages.join("; "))}</p>` : ""}`;

  $("results").querySelectorAll("[data-board]").forEach((b) => {
    b.onclick = () => { board = JSON.parse(b.dataset.board); drawBoard(); drawParties(); scoreBoard(); };
  });
}

/* ---- analysis panels ---- */
function drawBoard() {
  if (!detail) return;
  $("board").innerHTML = detail.scenario.issues.map((i) => `<div class="iss">
    <div class="nm">${esc(pretty(i.name))}</div>
    <div class="opts">${i.options.map((o) => `<button class="opt" type="button"
      aria-pressed="${board[i.name] === o}" data-i="${esc(i.name)}" data-o="${esc(o)}">${esc(pretty(o))}</button>`).join("")}</div>
  </div>`).join("");
  $("board").querySelectorAll(".opt").forEach((b) => {
    b.onclick = () => { board[b.dataset.i] = b.dataset.o; drawBoard(); drawParties(); drawPlot(); scoreBoard(); };
  });
}

function drawParties() {
  if (!detail) return;
  $("parties").innerHTML = detail.scenario.countries.map((c, ix) => `
    <div class="party ${ix ? "b" : "a"}">
      <div class="prow"><span class="nm">${esc(c.name)}</span></div>
      <div class="rl">${c.red_lines.map((r) => `<span>${esc(r)}</span>`).join("")}</div>
      <div style="margin-top:9px">${c.valuations.map((v) => {
        const now = v.option_values[board[v.issue]] ?? 0;
        return `<div class="vrow"><span>${esc(pretty(v.issue))}</span>
          <span class="meter" style="color:${ix ? "var(--verm)" : "var(--teal)"}"><i style="width:${(now * 100).toFixed(0)}%"></i></span>
          <span>${fmt(v.weight, 2)}w</span></div>`;
      }).join("")}</div>
    </div>`).join("");
}

async function scoreBoard() {
  if (!detail) return;
  try {
    const id = location.hash.split("/").pop();
    const r = await api(`/scenarios/${id}/score`, { method: "POST", body: JSON.stringify({ package: board }) });
    if (!r.legal) { $("payoff").innerHTML = `<dt>Not a legal settlement</dt><dd>${esc(r.problems.join("; "))}</dd>`; return; }
    const m = r.metrics, f = detail.frontier;
    // Two primaries, neither of them "the" number (CONTRACT.md §2): what this
    // does for the country being advised, and whether the deal found the trade.
    const gain = m.focus_gain_over_split ?? 0;
    $("payoff").innerHTML = `
      <dt class="grp">For ${esc(m.focus_country || detail.scenario.focus_country)}</dt><dd></dd>
      <dt>Utility</dt><dd>${fmt(m.focus_utility ?? 0)} <span style="color:var(--ink-3);font-weight:400">/ ${fmt(m.focus_utility_best ?? 0)} possible</span></dd>
      <dt>Gain over splitting the difference</dt>
        <dd class="${gain > 0 ? "up" : gain < 0 ? "down" : ""}">${gain >= 0 ? "+" : ""}${fmt(gain, 3)}</dd>
      <dt>Share of its own headroom</dt><dd>${((m.focus_share_of_own_headroom ?? 0) * 100).toFixed(1)}%</dd>
      <dt class="grp">Both parties</dt><dd></dd>` +
      detail.scenario.countries.map((c) =>
      `<dt>Utility · ${esc(c.name)}</dt><dd>${fmt(m[`utility_${c.name}`] ?? 0)}</dd>`).join("") + `
      <dt>Joint utility</dt><dd>${fmt(m.joint_utility)} <span style="color:var(--ink-3);font-weight:400">/ ${fmt(f.max_joint)}</span></dd>
      <dt>Joint value recovered</dt><dd>${(m.joint_value_recovered * 100).toFixed(1)}%</dd>
      <dt>Nash ratio</dt><dd>${fmt(m.nash_ratio, 2)}</dd>
      <dt>Inequality</dt><dd>${fmt(m.inequality, 2)}</dd>
      <dt>Integrative gain</dt><dd>${fmt(m.integrative_gain, 2)}</dd>
      <dt>Pareto-optimal</dt><dd>${m.is_pareto_optimal ? '<span class="tick y">yes</span>' : '<span class="tick n">no</span>'}</dd>`;
  } catch { /* a scoring hiccup must not take the page down */ }
}

/* Every settlement plotted by what it is worth to each party. Hand-drawn SVG:
   the frontier and the path the debate walked are not a standard chart type. */
function drawPlot() {
  if (!detail) return;
  const W = 520, H = 380, M = { l: 46, r: 22, t: 28, b: 40 };
  const sx = (u) => M.l + u * (W - M.l - M.r);
  const sy = (u) => H - M.b - u * (H - M.t - M.b);
  const [a, b] = detail.scenario.countries.map((c) => c.name);
  const pts = detail.frontier.points;
  const g = [];

  [0, 0.5, 1].forEach((u) => {
    g.push(`<line x1="${sx(0)}" y1="${sy(u)}" x2="${sx(1)}" y2="${sy(u)}" stroke="var(--line-2)"/>`,
           `<line x1="${sx(u)}" y1="${sy(0)}" x2="${sx(u)}" y2="${sy(1)}" stroke="var(--line-2)"/>`,
           `<text x="${M.l - 8}" y="${sy(u) + 4}" text-anchor="end" fill="var(--ink-3)" font-size="11">${u.toFixed(1)}</text>`,
           `<text x="${sx(u)}" y="${H - M.b + 16}" text-anchor="middle" fill="var(--ink-3)" font-size="11">${u.toFixed(1)}</text>`);
  });
  pts.filter((p) => !p.pareto).forEach((p) =>
    g.push(`<circle cx="${sx(p.utilities[a])}" cy="${sy(p.utilities[b])}" r="3" fill="var(--ink-3)" opacity=".16"/>`));
  const front = pts.filter((p) => p.pareto).sort((p, q) => p.utilities[a] - q.utilities[a]);
  g.push(`<polyline points="${front.map((p) => `${sx(p.utilities[a])},${sy(p.utilities[b])}`).join(" ")}"
           fill="none" stroke="var(--gold-mark)" stroke-width="2" stroke-linejoin="round"/>`);
  front.forEach((p) => g.push(`<circle cx="${sx(p.utilities[a])}" cy="${sy(p.utilities[b])}" r="4" fill="var(--gold-mark)"/>`));
  if (trail.length > 1)
    g.push(`<polyline points="${trail.map((v) => `${sx(v[0])},${sy(v[1])}`).join(" ")}" fill="none"
             stroke="var(--accent)" stroke-width="1.5" stroke-dasharray="4 3" opacity=".7"/>`);
  trail.forEach((v, i) => g.push(`<circle cx="${sx(v[0])}" cy="${sy(v[1])}" r="${i === trail.length - 1 ? 6 : 3.5}"
             fill="var(--accent)" opacity="${i === trail.length - 1 ? 1 : 0.45}"/>`));
  g.push(`<text x="${sx(0.5)}" y="${H - 6}" text-anchor="middle" fill="var(--ink-2)" font-size="12" font-weight="600">utility to ${esc(a)}</text>`,
         `<text x="14" y="${sy(0.5)}" text-anchor="middle" fill="var(--ink-2)" font-size="12" font-weight="600" transform="rotate(-90 14 ${sy(0.5)})">utility to ${esc(b)}</text>`);

  $("plot").innerHTML = `<svg class="plot" viewBox="0 0 ${W} ${H}" role="img"
    aria-label="Settlements plotted by utility to each party, with the frontier and the path the debate walked">${g.join("")}</svg>`;
}

/* ---- tabs ---- */
function showTab(which) {
  $("paneDebate").hidden = which !== "debate";
  $("paneAnalysis").hidden = which !== "analysis";
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.dataset.tab === which));
  if (which === "analysis") drawPlot();
}
document.querySelectorAll(".tab").forEach((t) => { t.onclick = () => showTab(t.dataset.tab); });

/* --------------------------------------------------------- add a crisis */
/* The policymaker's form. No numbers anywhere: each issue is ranked by how much
   it matters, and each option is marked good / OK / no for each side. The
   backend (/api/intake) turns that into weights, reads it back in sentences,
   and rehearses the best openings — nothing is saved until the person has seen
   that read-back and pressed Save. CONTRACT.md §4, "The form". */
const MARKS = [
  { v: "good", label: "Good" },
  { v: "acceptable", label: "OK" },
  { v: "unacceptable", label: "No" },
];
const MAX_ISSUES = 6;
const MAX_OPTIONS = 4;

const blankIssue = (rank) => ({
  name: "",
  options: ["", "", ""],
  you: { rank, marks: ["", "", ""] },
  them: { rank, marks: ["", "", ""] },
});
let intake = null;          // the form's state; survives going to review and back
let intakePreview = null;   // the last /api/intake response

function freshIntake() {
  return {
    title: "", situation: "",
    you: { name: "Singapore", red: "" },
    them: { name: "", red: "" },
    issues: [blankIssue(1), blankIssue(2)],
  };
}

/* An example's "options" object is keyed by labels; map it onto the state. */
function intakeFromForm(f) {
  const s = {
    title: f.title || "", situation: f.situation || "",
    you: { name: f.you.name, red: (f.you.red_lines || []).join("; ") },
    them: { name: f.them.name, red: (f.them.red_lines || []).join("; ") },
    issues: [],
  };
  const rankOf = (ranking, name) => {
    const ix = ranking.findIndex((e) => (Array.isArray(e) ? e : [e]).includes(name));
    return ix < 0 ? 1 : ix + 1;
  };
  for (const i of f.issues) {
    s.issues.push({
      name: i.name,
      options: [...i.options],
      you: { rank: rankOf(f.you.ranking, i.name), marks: i.options.map((o) => f.you.options?.[i.name]?.[o] || "") },
      them: { rank: rankOf(f.them.ranking, i.name), marks: i.options.map((o) => f.them.options?.[i.name]?.[o] || "") },
    });
  }
  return s;
}

/* State -> the payload /api/intake expects. Blank rows are dropped, not sent. */
function intakePayload() {
  const issues = intake.issues
    .map((i) => {
      const keep = i.options.map((o, ix) => [o.trim(), ix]).filter(([o]) => o);
      return { ...i, name: i.name.trim(), keep };
    })
    .filter((i) => i.name && i.keep.length);

  const side = (who) => {
    // equal ranks are a tie; the backend takes [a, [b, c], d]
    const byRank = new Map();
    for (const i of issues) {
      const r = Number(i[who].rank);
      if (!byRank.has(r)) byRank.set(r, []);
      byRank.get(r).push(i.name);
    }
    const ranking = [...byRank.keys()].sort((a, b) => a - b)
      .map((r) => (byRank.get(r).length === 1 ? byRank.get(r)[0] : byRank.get(r)));
    const options = {};
    for (const i of issues) {
      options[i.name] = {};
      for (const [o, ix] of i.keep) if (i[who].marks[ix]) options[i.name][o] = i[who].marks[ix];
    }
    const p = intake[who];
    return {
      name: p.name.trim(), ranking, options,
      red_lines: p.red.split(";").map((x) => x.trim()).filter(Boolean),
    };
  };
  return {
    title: intake.title.trim(), situation: intake.situation.trim(),
    issues: issues.map((i) => ({ name: i.name, options: i.keep.map(([o]) => o) })),
    you: side("you"), them: side("them"),
  };
}

function drawAddForm() {
  if (!intake) intake = freshIntake();
  $("addCard").hidden = false;
  $("addReview").hidden = true;
  const youName = esc(intake.you.name || "You"), themName = esc(intake.them.name || "Them");
  const n = intake.issues.length;

  const rankSelect = (ix, who) => `<select class="irank" data-ix="${ix}" data-who="${who}"
      aria-label="How much issue ${ix + 1} matters to ${who === "you" ? youName : themName}">
      ${Array.from({ length: n }, (_, r) => `<option value="${r + 1}" ${Number(intake.issues[ix][who].rank) === r + 1 ? "selected" : ""}>
        ${["Most important", "2nd", "3rd", "4th", "5th", "6th"][r]}</option>`).join("")}
    </select>`;

  const marks = (ix, ox, who) => {
    const cur = intake.issues[ix][who].marks[ox];
    return `<div class="seg" role="radiogroup" aria-label="${who === "you" ? youName : themName}: option ${ox + 1}">
      ${MARKS.map((m) => `<button type="button" role="radio" class="m-${m.v}" aria-checked="${cur === m.v}"
        data-ix="${ix}" data-ox="${ox}" data-who="${who}" data-v="${m.v}">${m.label}</button>`).join("")}
    </div>`;
  };

  const issueCard = (i, ix) => `<div class="icard">
    <div class="ihead">
      <input type="text" class="iname" data-ix="${ix}" value="${esc(i.name)}"
        placeholder="Issue ${ix + 1} — e.g. Who enforces the rules">
      ${n > 2 ? `<button type="button" class="mini" data-rmissue="${ix}" aria-label="Remove issue ${ix + 1}">remove</button>` : ""}
    </div>
    <div class="ogrid">
      <span class="hd">Possible outcome</span><span class="hd">${youName}</span><span class="hd">${themName} <i>your guess</i></span>
      ${i.options.map((o, ox) => `
        <span class="ocell">
          <input type="text" class="oname" data-ix="${ix}" data-ox="${ox}" value="${esc(o)}"
            placeholder="${["e.g. Their courts only", "e.g. A joint panel", "e.g. Our law applies", "another outcome"][ox]}">
          ${i.options.length > 2 ? `<button type="button" class="x" data-rmopt="${ix}:${ox}" aria-label="Remove this outcome">×</button>` : ""}
        </span>
        ${marks(ix, ox, "you")}${marks(ix, ox, "them")}`).join("")}
      ${i.options.length < MAX_OPTIONS ? `<span><button type="button" class="mini" data-addopt="${ix}">+ outcome</button></span>` : ""}
    </div>
    <div class="irow">
      <span class="hd">How much it matters</span>
      <label>${youName} ${rankSelect(ix, "you")}</label>
      <label>${themName} ${rankSelect(ix, "them")}</label>
    </div>
  </div>`;

  $("addForm").innerHTML = `
    <div class="note">Four short steps, no numbers. DARA reads your answers back to you before using
      them, and nothing is saved until you say so.
      <button type="button" class="mini" id="nExample">Fill in the haze example</button></div>

    <div class="step"><div class="sh">Step 1 · What is happening</div>
      <input type="text" id="nTitle" value="${esc(intake.title)}" placeholder="Short title — e.g. Johor River drought shortfall">
      <textarea id="nSum" placeholder="One or two sentences on the situation.">${esc(intake.situation)}</textarea></div>

    <div class="step"><div class="sh">Step 2 · Who is at the table <i>— DARA advises the first</i></div>
      <div class="two">
        <label class="lab">You<input type="text" id="nA" value="${esc(intake.you.name)}"></label>
        <label class="lab">The other side<input type="text" id="nB" value="${esc(intake.them.name)}" placeholder="e.g. Malaysia"></label>
      </div>
      <div class="two">
        <label class="lab">Your red lines, in words <i>optional, separate with ;</i>
          <input type="text" id="nAr" value="${esc(intake.you.red)}" placeholder="e.g. No foreign court rules on our companies"></label>
        <label class="lab">Theirs, as far as you know <i>optional</i>
          <input type="text" id="nBr" value="${esc(intake.them.red)}"></label>
      </div></div>

    <div class="step"><div class="sh">Step 3 · What is on the table <i>— and how each side sees every outcome</i></div>
      <div class="note"><b>Good</b> = they would be happy with it. <b>OK</b> = they could live with it.
        <b>No</b> = they would walk away rather than accept it. For the other side, give your best guess —
        DARA tests how much the advice depends on that guess.</div>
      ${intake.issues.map(issueCard).join("")}
      ${n < MAX_ISSUES ? `<button type="button" class="go ghost sm" id="nAddIssue">+ Add an issue</button>` : ""}
    </div>

    <div class="foot"><span class="msg2" id="nMsg" role="status"></span>
      <a class="go ghost" href="#/" style="display:grid;place-items:center">Cancel</a>
      <button class="go" id="nReview" type="button">Review →</button></div>`;

  /* text: update state without redrawing, so typing keeps focus */
  const bind = (id, set) => { $(id).oninput = (e) => { set(e.target.value); }; };
  bind("nTitle", (v) => (intake.title = v));
  bind("nSum", (v) => (intake.situation = v));
  bind("nAr", (v) => (intake.you.red = v));
  bind("nBr", (v) => (intake.them.red = v));
  $("nA").oninput = (e) => { intake.you.name = e.target.value; relabel(); };
  $("nB").oninput = (e) => { intake.them.name = e.target.value; relabel(); };
  const form = $("addForm");
  form.querySelectorAll(".iname").forEach((el) => { el.oninput = () => { intake.issues[el.dataset.ix].name = el.value; }; });
  form.querySelectorAll(".oname").forEach((el) => { el.oninput = () => { intake.issues[el.dataset.ix].options[el.dataset.ox] = el.value; }; });
  form.querySelectorAll(".irank").forEach((el) => { el.onchange = () => { intake.issues[el.dataset.ix][el.dataset.who].rank = Number(el.value); }; });

  /* marks: a click toggles; a second click on the same mark clears it */
  form.querySelectorAll(".seg button").forEach((b) => {
    b.onclick = () => {
      const m = intake.issues[b.dataset.ix][b.dataset.who].marks;
      m[b.dataset.ox] = m[b.dataset.ox] === b.dataset.v ? "" : b.dataset.v;
      b.parentElement.querySelectorAll("button").forEach((x) =>
        x.setAttribute("aria-checked", String(x.dataset.v === m[b.dataset.ox])));
    };
  });

  /* structure: these redraw */
  form.querySelectorAll("[data-addopt]").forEach((b) => {
    b.onclick = () => {
      const i = intake.issues[b.dataset.addopt];
      i.options.push(""); i.you.marks.push(""); i.them.marks.push("");
      drawAddForm();
    };
  });
  form.querySelectorAll("[data-rmopt]").forEach((b) => {
    b.onclick = () => {
      const [ix, ox] = b.dataset.rmopt.split(":").map(Number);
      const i = intake.issues[ix];
      i.options.splice(ox, 1); i.you.marks.splice(ox, 1); i.them.marks.splice(ox, 1);
      drawAddForm();
    };
  });
  form.querySelectorAll("[data-rmissue]").forEach((b) => {
    b.onclick = () => { intake.issues.splice(Number(b.dataset.rmissue), 1); clampRanks(); drawAddForm(); };
  });
  if ($("nAddIssue")) $("nAddIssue").onclick = () => {
    intake.issues.push(blankIssue(intake.issues.length + 1));
    drawAddForm();
  };
  $("nExample").onclick = async () => {
    try { intake = intakeFromForm(await api("/intake/example")); drawAddForm(); }
    catch (e) { $("nMsg").textContent = e.message; }
  };
  $("nReview").onclick = reviewCrisis;
}

function relabel() {
  const a = esc(intake.you.name || "You"), b = esc(intake.them.name || "Them");
  $("addForm").querySelectorAll(".ogrid").forEach((g) => {
    const hd = g.querySelectorAll(".hd");
    hd[1].innerHTML = a; hd[2].innerHTML = `${b} <i>your guess</i>`;
  });
  $("addForm").querySelectorAll(".irow label").forEach((l, k) => {
    l.firstChild.textContent = (k % 2 === 0 ? intake.you.name || "You" : intake.them.name || "Them") + " ";
  });
}

function clampRanks() {
  const n = intake.issues.length;
  for (const i of intake.issues) for (const who of ["you", "them"]) i[who].rank = Math.min(i[who].rank, n);
}

/* Client-side checks only for what the server cannot word well; everything
   else — a missing mark, a repeated rank — comes back from the server already
   in plain language. */
function intakeProblem(p) {
  if (!p.title) return "Give the crisis a short title.";
  if (!p.you.name || !p.them.name) return "Name both sides.";
  if (p.issues.length < 2) return "Add at least two issues, each with at least two outcomes.";
  const short = p.issues.find((i) => i.options.length < 2);
  if (short) return `“${short.name}” needs at least two possible outcomes.`;
  return "";
}

async function reviewCrisis() {
  const msg = (t) => { $("nMsg").textContent = t; };
  const payload = intakePayload();
  const problem = intakeProblem(payload);
  if (problem) return msg(problem);
  try {
    msg("Reading your answers…");
    intakePreview = await api("/intake", { method: "POST", body: JSON.stringify({ form: payload }) });
    msg("");
    drawReview();
  } catch (e) { msg(e.message); }
}

/* The server answers in slugs; show the person their own words back. Same rule
   as intake.slug() on the server: lower-case, non-alphanumerics to "_". */
const slugOf = (s) => String(s).trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
function labelMap() {
  const m = {};
  for (const i of intake.issues) {
    m[slugOf(i.name)] = i.name.trim();
    for (const o of i.options) if (o.trim()) m[slugOf(o)] = o.trim();
  }
  return (s) => m[s] || pretty(s);
}

/* One or more rehearsed openings as cards. `titles` overrides the headings. */
function renderOpenings(rehearsals, label, titles) {
  const pct = (x) => `${Math.round(x * 100)}%`;
  const isWarn = (line) => /^(Warning|No deal|This opening includes|In \d+% of them nothing)/.test(line);
  // The tiles carry acceptance and landing, so those two sentences are not repeated.
  const shown = (line) => !/^(We imagined|They accept this opening|Where you are likely to land)/.test(line);
  const best = rehearsals[0];
  const same = (a) => a.accept_rate === best.accept_rate
    && a.expected_share === best.expected_share && a.worst_share === best.worst_share;
  return rehearsals.map((reh, k) => {
    const rest = reh.summary.filter(shown);
    const title = titles?.[k] || (k === 0 ? "Best opening" : same(reh) ? "Equally good alternative" : `Alternative ${k}`);
    return `<div class="opening ${k === 0 && !titles ? "best" : ""}">
    <div class="otitle">${esc(title)}</div>
    <div class="chips">${Object.entries(reh.opening).map(([i, o]) =>
      `<span class="chip"><i>${esc(label(i))}</i> ${esc(label(o))}</span>`).join("")}</div>
    <div class="stats">
      <div class="stat"><b>${pct(reh.accept_rate)}</b><span>chance they accept it as it stands</span></div>
      <div class="stat"><b>${pct(reh.expected_share)}</b><span>of your best case — where you likely land</span></div>
      <div class="stat"><b>${pct(reh.worst_share)}</b><span>in the worst imagined case</span></div>
      <div class="stat"><b>${pct(reh.split_share)}</b><span>if you just split every issue</span></div>
    </div>
    ${rest.length ? `<ul class="said">${rest.map((line) =>
      `<li class="${isWarn(line) ? "warnline" : ""}">${esc(line)}</li>`).join("")}</ul>` : ""}
  </div>`;
  }).join("");
}

function drawReview() {
  const r = intakePreview;
  const label = labelMap();
  $("addCard").hidden = true;
  $("addReview").hidden = false;

  const readBack = r.review.map((line) => line.startsWith("  ")
    ? `<li class="sub">${esc(line.trim())}</li>` : `<li>${esc(line)}</li>`).join("");


  $("addReview").innerHTML = `
    <div class="card gate">
      <h2>Is this what you meant? <em>check before trusting anything below</em></h2>
      <ul class="readback">${readBack}</ul>
    </div>
    <div class="card">
      <h2>Rehearsed in imagination <em>${esc(r.rehearsals[0]?.summary[0] || "")}</em></h2>
      ${r.rehearsals.length ? renderOpenings(r.rehearsals, label)
        : `<div class="err">Every possible opening crosses one of your own red lines. Go back and
           revisit what you marked “No”.</div>`}
      <p class="status">A quick estimate from your read of the other side, not a prediction. Save the
        crisis to hear the two sides argue it out.</p>
    </div>
    <div class="foot"><span class="msg2" id="rMsg" role="status"></span>
      <button class="go ghost" id="rBack" type="button">← Change answers</button>
      <button class="go" id="rSave" type="button">Looks right — save</button></div>`;

  $("rBack").onclick = () => { drawAddForm(); window.scrollTo(0, 0); };
  $("rSave").onclick = async () => {
    try {
      $("rMsg").textContent = "Saving…";
      const res = await api("/intake", { method: "POST",
        body: JSON.stringify({ form: intakePayload(), confirm: true }) });
      intake = null; intakePreview = null;
      location.hash = `#/case/${res.id}`;
    } catch (e) { $("rMsg").textContent = e.message; }
  };
  window.scrollTo(0, 0);
}

/* ----------------------------------------------------------------- boot */
$("gateBtn").onclick = () => setConfirmed(!confirmed);
// Changing what the advice protects does not invalidate the problem, so it does
// not reopen the gate; it only has to keep the two selects distinct.
$("priority1").onchange = () => { syncPriorities(); stages = []; };
$("priority2").onchange = () => { syncPriorities(); stages = []; };
$("runBtn").onclick = runCase;
$("stopBtn").onclick = () => abort?.abort();
$("cond").onchange = () => { stages = []; drawStages(); };
$("rounds").onchange = () => { stages = []; drawStages(); };

let savedMode = "policy";
try { savedMode = localStorage.getItem(MODE_KEY) || "policy"; } catch { /* no storage */ }
setMode(savedMode);

api("/health").then((h) => { health = h; drawProvider(); route(); })
  .catch(() => { drawProvider(); route(); });
