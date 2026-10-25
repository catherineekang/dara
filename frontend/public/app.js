/* DARA console — plain browser JavaScript, no framework and no build step.
   Talks to the Flask API through this server's /api proxy, so every URL here is
   relative and there is no CORS to configure. */

const $ = (id) => document.getElementById(id);
// The page uses no em dashes, including in server and model text it displays.
const undash = (s) => String(s ?? "").replace(/\s*—\s*/g, ", ");
const esc = (s) => undash(s).replace(/[&<>"]/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));
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
  }
  showTab(currentTab);
  refreshRunBtn();
}
document.querySelectorAll(".modeseg button").forEach((b) => { b.onclick = () => setMode(b.dataset.mode); });
$("toCases").onclick = () => $("casesCard").scrollIntoView({ behavior: "smooth", block: "start" });

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
    el.title = "No OPENAI_API_KEY in .env, so runs replay the recording in demo/";
    el.innerHTML = '<span class="dot"></span>recorded';
  }
}

/* ----------------------------------------------------------------- home */
/* "COVID-19 border and supply measures — Singapore · Malaysia (2020)" splits
   into the name of the file and its year; the delegations are shown separately. */
function caseTitle(title) {
  const s = undash(title);
  const m = s.match(/^(.*?)(?:,\s*[^,]*·[^,]*)?\s*\(([^)]*\d{4}[^)]*)\)\s*$/);
  return m ? { name: m[1].trim(), when: m[2] } : { name: s, when: "" };
}
const CODES = { singapore: "SG", malaysia: "MY", indonesia: "ID", thailand: "TH", philippines: "PH",
  vietnam: "VN", "viet nam": "VN", brunei: "BN", cambodia: "KH", laos: "LA", myanmar: "MM",
  china: "CN", japan: "JP", india: "IN", australia: "AU", "united states": "US", "united kingdom": "UK" };
const code = (name) => CODES[String(name).trim().toLowerCase()]
  || String(name).trim().split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

async function loadHome() {
  try {
    const [cases, h] = await Promise.all([api("/scenarios"), api("/health")]);
    health = h;
    drawProvider();
    $("caseCount").textContent = `${cases.length} on file`;
    drawRecent();

    $("homeNote").innerHTML = h.mode === "recorded"
      ? `<div class="note" style="margin-bottom:20px">No <b>OPENAI_API_KEY</b> in <b>.env</b>, so runs
         replay the recorded debate in <b>backend/demo/</b>: the same pipeline, at no cost. Add a
         key and the identical run calls the model live; nothing else changes.</div>`
      : "";

    const add = `<a class="case add" href="#/add">
        <span class="filetab">New file</span>
        <span class="plus">+</span>
        <h3>Add a crisis</h3>
        <p class="desc">Bring a negotiation DARA has never seen. Name the delegations and the issues,
          then mark each outcome good, OK or no for each side. No numbers needed.</p>
        <span class="open">Start a file →</span></a>`;

    $("gallery").innerHTML = add + cases.map((c, k) => {
      const t = caseTitle(c.title);
      const [you, ...others] = [c.focus_country, ...c.parties.filter((p) => p !== c.focus_country)];
      return `
      <div class="case file" role="button" tabindex="0" data-open="${esc(c.file)}">
        <span class="filetab">Case file No. ${String(k + 1).padStart(2, "0")}${t.when ? ` · ${esc(t.when)}` : ""}</span>
        <div class="delegations">
          <span class="roundel mine" title="${esc(you)}, the side you advise">${esc(code(you))}</span>
          <span class="across">↔</span>
          ${others.map((o) => `<span class="roundel" title="${esc(o)}">${esc(code(o))}</span>`).join("")}
          ${c.has_recording
            ? '<span class="pill rec" title="A recorded debate exists, so this plays with no key">recorded</span>'
            : '<span class="pill off" title="No recorded debate, so this needs a live model">live only</span>'}
        </div>
        <h3>${esc(t.name)}</h3>
        <p class="who">Advising ${esc(you)} · across the table: ${esc(others.join(", "))}</p>
        <p class="desc">${esc(c.description)}</p>
        <div class="filefoot">
          <span>${c.issues} issues · ${c.settlements} possible settlements</span>
          <span class="rx">${c.pareto_count} on the frontier · ${fmt(c.at_stake, 2)} at stake</span>
          ${c.user_created ? `<button class="mini" data-delete="${esc(c.file)}">delete</button>` : ""}
          <span class="open">Open file →</span>
        </div>
      </div>`;
    }).join("");

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

  $("runMeta").textContent = s.countries.map((c) => c.name).join(" ↔ ");
  $("runTitle").textContent = undash(s.title);
  // Lead with whose side the user is on; the narrative is supporting context.
  const you = s.countries.find((c) => c.name === s.focus_country) || s.countries[0];
  // Crises added through the form may leave a list empty; skip its heading then.
  const list = (c, label, items, cls = "") => (items || []).length
    ? `<span class="sl">${label}</span><ul class="${cls}">${items.map((i) =>
        `<li>${esc(i)} ${provChip(c.provenance?.[i])}</li>`).join("")}</ul>`
    : "";
  const side = (c, mine) => `
    <div class="side${mine ? " mine" : ""}">
      <span class="role">${mine ? "You represent" : "Across the table"}</span>
      <h3>${esc(c.name)}</h3>
      ${list(c, mine ? "What you need" : "What they need", c.interests)}
      ${list(c, mine ? "Your red lines" : "Their red lines", c.red_lines, "red")}
    </div>`;
  $("runSides").innerHTML = side(you, true)
    + s.countries.filter((c) => c !== you).map((c) => side(c, false)).join("");
  $("runLede").textContent = undash(s.description);
  const links = (s.sources || []).filter((x) => /^https?:\/\//.test(x.url));
  const dateText = (d) => new Date(d + "T00:00:00").toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
  $("runCutoff").innerHTML = s.evidence_cutoff ? `<span class="sl">Evidence cutoff</span>
    <p>${esc(dateText(s.evidence_cutoff))}. The debaters see nothing published after this date.</p>` : "";
  $("runSource").innerHTML = links.length ? `<span class="sl">Sources</span><ol>${links.map((x) => `
      <li><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.title)} ↗</a>${
        x.publisher ? `<span>, ${esc(x.publisher)}</span>` : ""}</li>`).join("")}</ol>` : "";
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
  drawJump();
  $("resultCard").hidden = true;
  // A new crisis is a new problem, so the previous confirmation cannot carry
  // over — that would be the gate approving something nobody looked at.
  drawGate(); setConfirmed(false);
  if (!isResearch()) { $("cond").value = "B2"; $("rounds").value = "3"; stages = []; }
  drawStages(); drawBoard(); drawParties(); drawPlot(); scoreBoard();
  showTab(isResearch() ? "debate" : "rehearse");
  drawImagine(id);

  // Coming back to a case brings back the debate last run on it (or the one
  // picked from the home page), rather than an empty box.
  const mine = savedDebates().filter((d) => d.file === id);
  const pick = mine.find((d) => d.key === pendingReplay) || mine[0];
  pendingReplay = null;
  if (pick) replayDebate(pick);
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

/* ---- provenance ---- */
/* Paper Fig. 1 stage 1: every brief element is labelled, never dropped. */
const PROV = {
  sourced: "Sourced", projected: "Projected",
  unknown: "Unknown", user_edited: "Entered by you",
};
function provChip(p) {
  // Only show a label when there is support to show; an unsourced element
  // simply has none.
  if (!p || p.status === "unsupported") return "";
  const cites = (p.evidence || []).map((e) => cite(`[${e.source_id}]`)).join(" ");
  const tip = [(p.evidence || []).map((e) => e.quote).join(" "), p.note].filter(Boolean).join(" ");
  return `<span class="prov p-${esc(p.status)}" title="${esc(tip)}">${esc(PROV[p.status] || p.status)}${cites ? " " + cites : ""}</span>`;
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
    : `recorded mode: ${n} of ${health.priorities.length} objectives are replayable without a key`;
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
  const rankOf = (name) => RANK[distinct.indexOf(w[name] ?? 0)] || "n/a";
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

/* The server holds the gate too: confirming fetches a fingerprint of this exact
   problem, and /api/run refuses to start without it. */
let reviewToken = "";
const caseFile = () => location.hash.split("/").pop();
async function toggleConfirmed() {
  if (confirmed) return setConfirmed(false);
  try {
    const r = await api(`/scenarios/${caseFile()}/review`, { method: "POST", body: JSON.stringify({ confirm: true }) });
    reviewToken = r.token;
    setConfirmed(true);
  } catch (e) { $("gateState").textContent = undash(e.message); }
}

function setConfirmed(v) {
  confirmed = v;
  if (!v) reviewToken = "";
  $("gateCard").classList.toggle("ok", v);
  $("gateBtn").textContent = v ? "Confirmed (click to change)" : "Confirm and unlock the run";
  $("gateState").textContent = v
    ? `Confirmed: ${detail ? detail.scenario.issues.length : 0} issues, ${detail ? detail.frontier.settlements : 0} possible settlements. Next, press "Hear them argue it" above to start the debate.`
    : "Not confirmed yet. The run is locked.";
  refreshRunBtn();
}

/* Research: locked until the review is confirmed. Policymaker: the review sits
   in the Debate & advice tab, so an unconfirmed press takes them to it instead
   of doing nothing. */
function refreshRunBtn() {
  $("runBtn").disabled = !canRun || (!confirmed && isResearch());
  $("runBtn").title = !canRun ? "no recording for this case, and no API key"
    : !confirmed ? "confirm the problem in the review first" : "";
}
function onRunPress() {
  if (!confirmed) {
    showTab("debate");
    $("gateCard").scrollIntoView({ behavior: "smooth", block: "start" });
    $("gateBtn").focus({ preventScroll: true });
    return;
  }
  runCase();
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
      <span class="t">${st.status === "done" ? (st.ms / 1000).toFixed(1) + "s" : st.status === "run" ? "running" : ""}</span>
    </div>`).join("")}</div></div>`).join("");
}
function mark(id, status, ms) {
  const st = stages.find((x) => x.id === id);
  if (st) { st.status = status; if (ms != null) st.ms = ms; drawStages(); }
}

/* ---- chat ---- */
/* Agents cite the scenario's sources as [S1], [S2]…, numbered in the scenario's
   own order, so each tag links to the document it names. */
function cite(text) {
  const src = detail?.scenario.sources || [];
  return esc(text).replace(/\[S(\d+)\]/g, (m, n) => {
    const s = src[n - 1];
    return s && /^https?:\/\//.test(s.url)
      ? `<a class="cite" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer" title="${esc(s.title)}">${m}</a>`
      : m;
  });
}
const sentence = (t) => { const s = String(t || "").trim(); return /[.!?]$/.test(s) ? s : s + "."; };
function claimList(claims) {
  // Recorded runs stand the position in as its own claim; that adds nothing.
  const cl = (claims || []).filter((c) => c.evidence && c.evidence !== "recorded run");
  return cl.length ? `<details class="claims"><summary>Why: the evidence behind this (${cl.length})</summary>
    ${cl.map((c) => `<p>${cite(sentence(c.claim))} ${cite(sentence(c.evidence))}</p>`).join("")}</details>` : "";
}
function pkgList(pkg) {
  return `<ul class="pkg">${Object.entries(pkg || {}).map(([k, v]) =>
    `<li><span>${esc(caseLabel(k))}:</span> ${esc(pretty(v))}</li>`).join("")}</ul>`;
}
function bubble(side, name, round, text, pkg, concessions, score, claims) {
  const initials = name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  const legal = score && score.legal;
  const line = legal
    ? `joint ${fmt(score.joint_utility)} · ${score.is_pareto_optimal ? "on the frontier" : "off the frontier"} · recovered ${(score.joint_value_recovered * 100).toFixed(1)}%`
    : score ? `Not legal: ${esc((score.problems || []).join("; "))}` : "";
  return `<div class="msg ${side}"><div class="av">${esc(initials)}</div>
    <div class="bub"><div class="nm">${esc(name)} · round ${round}</div>
      <div class="bd">${cite(text)}</div>
      ${claimList(claims)}
      <div class="attach"><b>${esc(name)}'s offer this round</b>${pkgList(pkg)}
        ${concessions && concessions.filter(Boolean).length ? `<span class="cede">Concedes: ${esc(concessions.filter(Boolean).join("; "))}</span>` : ""}
        <span class="cede rx" style="color:var(--ink-3)">${line}</span>
      </div></div></div>`;
}
function say(html) { $("chat").insertAdjacentHTML("beforeend", html); const c = $("chat"); c.scrollTop = c.scrollHeight; drawJump(); }

/* ---- timeline ---- */
/* A table of contents for the debate box: one stop per section tagged
   data-jump, a click scrolls the box there, and the stop in view is lit. */
function drawJump() {
  const stops = [...$("chat").querySelectorAll("[data-jump]")];
  const nav = $("jump");
  nav.hidden = !stops.length;
  nav.innerHTML = stops.map((el, i) => `<button type="button" data-i="${i}"
      class="stop${el.dataset.sub ? " sub" : ""}"><i></i><span>${esc(el.dataset.jump)}</span></button>`).join("");
  nav.querySelectorAll("[data-i]").forEach((b) => {
    b.onclick = () => {
      const c = $("chat");
      c.scrollTo({ top: stops[+b.dataset.i].offsetTop - 6, behavior: "smooth" });
    };
  });
  litJump();
}
function litJump() {
  const c = $("chat");
  const stops = [...c.querySelectorAll("[data-jump]")];
  let at = 0;
  stops.forEach((el, i) => { if (el.offsetTop <= c.scrollTop + 40) at = i; });
  // At the very bottom the last stop is in view even if it starts lower down.
  if (c.scrollTop + c.clientHeight >= c.scrollHeight - 4) at = stops.length - 1;
  $("jump").querySelectorAll("[data-i]").forEach((b) => b.classList.toggle("on", +b.dataset.i === at));
}
$("chat").addEventListener("scroll", litJump, { passive: true });

/* ---- your check of the judge ---- */
/* Paper contribution 4: after each round the run waits while a person checks the
   judge. What they leave here is what both sides hear next round and what the
   recommender reads; scores stay the judge's own. */
const lines = (xs) => (xs || []).join("\n");
const unlines = (s) => String(s || "").split("\n").map((x) => x.trim()).filter(Boolean);
/* By default the card reads like a message with one obvious answer ("looks
   right"); the edit boxes only appear for someone who wants to change
   something. Once answered it shrinks to one line so the debate flows on. */
function reviewCard(ev) {
  const r = ev.round, last = r >= ev.of;
  const j = ev.judgment;
  const open = j.unaddressed_tradeoffs || [];
  const head = `<div class="sys review${replaying ? " done" : ""}" data-jump="Your check" data-sub="1"
      data-round="${r}" id="rv-${ev.run_id}-${r}">`;
  if (replaying) {
    return `${head}<div class="rvdone">✓ Round ${r} checked during the run, no changes.</div></div>`;
  }
  return `${head}
    <div class="rvfull">
      <div class="rvstep">Your check · round ${r} of ${ev.of}</div>
      <h4 class="rvq">Does the judge's verdict look right?</h4>
      <p class="rvlede">${last ? "The recommendation is written from this."
        : `Both sides hear this before round ${r + 1}.`} Change it only if the judge missed something.</p>
      ${j.judgments.map((x) => `<div class="rvv">
        <b>${esc(x.country)}</b>
        <p class="rvread">${esc(x.summary)}</p>
        ${(x.unsupported_claims || []).length ? `<p class="rvread rvflag">Flagged as unsupported: ${esc(x.unsupported_claims.join("; "))}</p>` : ""}
        <textarea class="rvedit" data-sum="${esc(x.country)}" rows="3" hidden>${esc(x.summary)}</textarea>
        <label class="lab rvedit" hidden>Claims with no source <i>one per line; leave empty if none</i>
          <textarea data-uns="${esc(x.country)}" rows="2">${esc(lines(x.unsupported_claims))}</textarea></label>
      </div>`).join("")}
      ${open.length ? `<div class="rvopen rvread"><b>Still open</b><ul>${open.map((o) => `<li>${esc(o)}</li>`).join("")}</ul></div>` : ""}
      <label class="lab rvedit" hidden>Still open <i>one per line</i>
        <textarea data-unad rows="2">${esc(lines(open))}</textarea></label>
      <label class="lab rvedit" hidden>Anything else to pass on <i>optional; read by the recommender</i>
        <textarea data-note rows="2" placeholder="e.g. One side's point on funding deserved more weight."></textarea></label>
      ${ev.mode === "recorded" ? `<p class="rvlede rvedit" hidden>This debate is a recording: your changes are saved with it, but cannot change what was recorded next.</p>` : ""}
      <div class="rvactions">
        <button class="go" type="button" data-ok>${last ? "Looks right, write the recommendation" : `Looks right, continue to round ${r + 1}`}</button>
        <button class="go ghost" type="button" data-edit>Make changes</button>
        <button class="linkbtn" type="button" data-skip>Continue and don't ask again</button>
        <span class="meta" data-state></span>
      </div>
    </div>
    <div class="rvdone" hidden></div>
  </div>`;
}
function bindReview(ev) {
  const r = ev.round, last = r >= ev.of;
  const card = $(`rv-${ev.run_id}-${r}`);
  const q = (sel) => card.querySelector(sel);
  const boxes = [...card.querySelectorAll("textarea")];
  const original = boxes.map((b) => b.value);
  let editing = false;
  const okText = () => editing
    ? (last ? "Save and write the recommendation" : `Save and continue to round ${r + 1}`)
    : (last ? "Looks right, write the recommendation" : `Looks right, continue to round ${r + 1}`);

  q("[data-edit]").onclick = () => {
    editing = !editing;
    if (!editing) boxes.forEach((b, k) => { b.value = original[k]; });   // cancel = put it back
    card.querySelectorAll(".rvedit").forEach((el) => { el.hidden = !editing; });
    card.querySelectorAll(".rvread").forEach((el) => { el.hidden = editing; });
    q("[data-edit]").textContent = editing ? "Cancel changes" : "Make changes";
    q("[data-ok]").textContent = okText();
    if (editing) q("[data-sum]").focus();
  };

  const send = async (skip) => {
    // Send back only what the reviewer actually changed, so an untouched field
    // can never register as an edit (the judge's own text may differ from what
    // a text box hands back by a stray space or an empty line).
    const edited = (el) => el.value.trim() !== original[boxes.indexOf(el)].trim();
    const reply = { summaries: {}, unsupported: {}, no_more_stops: skip, note: q("[data-note]").value.trim() };
    if (edited(q("[data-unad]"))) reply.unaddressed = unlines(q("[data-unad]").value);
    card.querySelectorAll("[data-sum]").forEach((el) => { if (edited(el)) reply.summaries[el.dataset.sum] = el.value.trim(); });
    card.querySelectorAll("[data-uns]").forEach((el) => { if (edited(el)) reply.unsupported[el.dataset.uns] = unlines(el.value); });
    const changed = boxes.some(edited);
    card.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    try {
      await api(`/runs/${ev.run_id}/review`, { method: "POST", body: JSON.stringify(reply) });
      const what = !changed ? ", no changes"
        : `: you changed ${reply.note && boxes.slice(0, -1).some((b, k) => b.value.trim() !== original[k].trim())
          ? "the verdict and left a note" : reply.note ? "nothing, but left a note" : "the verdict"}`;
      q(".rvdone").textContent = `✓ Round ${r} checked${what}. `
        + (skip ? "No more stops this run. " : "")
        + (last ? "Writing the recommendation…" : `Round ${r + 1} is starting…`);
      q(".rvfull").hidden = true;
      q(".rvdone").hidden = false;
      card.classList.add("done");
      drawJump();
    } catch (e) {
      card.querySelectorAll("button").forEach((b) => { b.disabled = false; });
      q("[data-state]").textContent = undash(e.message);
    }
  };
  q("[data-ok]").onclick = () => send(false);
  q("[data-skip]").onclick = () => send(true);
  q("[data-ok]").focus({ preventScroll: true });
}

/* ---- saved debates ---- */
/* The last five finished debates, kept in this browser so leaving the page
   does not throw a paid run away. A per-viewer convenience: if storage is
   unavailable the page works exactly as before, it just forgets. */
const SAVED_KEY = "dara.debates";
const SAVED_MAX = 5;
let replaying = false;
let pendingReplay = null;   // a saved debate's key, opened from the home page
function savedDebates() {
  try { return JSON.parse(localStorage.getItem(SAVED_KEY)) || []; } catch { return []; }
}
function saveDebate(d) {
  let all = [d, ...savedDebates()].slice(0, SAVED_MAX);
  // A full store drops the oldest until the new debate fits.
  while (all.length) {
    try { localStorage.setItem(SAVED_KEY, JSON.stringify(all)); return; } catch { all = all.slice(0, -1); }
  }
}
const savedWhen = (d) => new Date(d.key).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
function replayDebate(d) {
  const parties = detail.scenario.countries.map((c) => c.name);
  replaying = true;
  stages = buildStages(d.cond, d.rounds, parties);
  trail = [];
  $("chat").innerHTML = "";
  drawJump();
  $("resultCard").hidden = true;
  say(`<div class="divider">Saved debate from ${esc(savedWhen(d))}. Press "Hear them argue it" to run a new one.</div>`);
  let i = 0;
  d.events.forEach((ev) => { i = onEvent(ev, i); });
  drawStages();
  replaying = false;
  showTab("debate");
}
function drawRecent() {
  const all = savedDebates();
  $("recent").innerHTML = all.length ? `<ol class="minutes">${all.map((d) => {
    const when = new Date(d.key);
    return `<li><button class="minute" type="button" data-key="${d.key}">
      <span class="mdate"><b>${esc(when.toLocaleDateString([], { day: "numeric", month: "short" }))}</b>
        <span>${esc(when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}</span></span>
      <span class="mbody"><b>${esc(caseTitle(d.title).name)}</b>
        <span>Protecting ${esc(d.priorities.map((p) => priorityLabel(p).toLowerCase()).join(", then "))}</span></span>
      <span class="open">Reopen →</span>
    </button></li>`;
  }).join("")}</ol>`
    : `<p class="empty-minutes">No sessions yet. Open a case file and press "Hear them argue it": every finished
       debate is minuted here so you can come back to it.</p>`;
  $("recent").querySelectorAll("[data-key]").forEach((b) => {
    b.onclick = () => {
      const d = all.find((x) => String(x.key) === b.dataset.key);
      pendingReplay = d.key;
      location.hash = `#/case/${d.file}`;
    };
  });
}

/* ---- the run ---- */
async function runCase() {
  const cond = $("cond").value;
  const rounds = +$("rounds").value;
  const parties = detail.scenario.countries.map((c) => c.name);

  abort = new AbortController();
  stages = buildStages(cond, rounds, parties);
  trail = [];
  $("chat").innerHTML = "";
  drawJump();
  $("runError").innerHTML = "";
  $("resultCard").hidden = true;
  $("runBtn").hidden = true; $("stopBtn").hidden = false;
  $("pipeMeta").textContent = "running";
  drawStages(); showTab("debate");

  const q = new URLSearchParams({
    scenario: detail.scenario.scenario_id && location.hash.split("/").pop(),
    condition: cond, rounds: String(rounds), speed: $("speed").value,
    priorities: selectedPriorities().join(","),
    review: reviewToken,
    // Pause after each round so a person can check the judge (B2 only: B0 and
    // B1 have no judge to check).
    pause: cond === "B2" && $("pauseJudge").checked ? "1" : "0",
  });

  let recIndex = 0;
  const log = [];
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
          log.push(ev);
          recIndex = onEvent(ev, recIndex);
        }
      }
    }
    if (log.some((ev) => ev.type === "done")) {
      saveDebate({ key: Date.now(), file: location.hash.split("/").pop(), title: detail.scenario.title,
                   cond, rounds, priorities: selectedPriorities(), events: log });
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
    say(`<div class="divider" data-jump="Intro"><span class="rx">${esc(ev.condition)} · </span>${esc(ev.mode)}</div>`);
    return recIndex;
  }
  if (ev.type === "argument") {
    const side = ev.country === detail.scenario.countries[0].name ? "left" : "right";
    mark(`r${ev.round}.${ev.country}`, "done", ev.elapsed * 1000);
    if (!$("chat").querySelector(`[data-r="${ev.round}"]`))
      say(`<div class="divider" data-r="${ev.round}" data-jump="Round ${ev.round}">Round ${ev.round}</div>`);
    say(bubble(side, ev.country, ev.round, ev.position, ev.package, ev.concessions, ev.score, ev.claims));
    if (ev.score && ev.score.legal) {
      trail.push(detail.scenario.countries.map((c) => ev.score[`utility_${c.name}`] ?? 0));
      drawPlot();
    }
    return recIndex;
  }
  if (ev.type === "judgment") {
    mark(`r${ev.round}.judge`, "done", ev.elapsed * 1000);
    say(`<div class="sys" data-jump="Judge" data-sub="1"><div class="hd">Judge · round ${ev.round}</div>
      ${ev.judgments.map((j) => `<div class="row"><span class="who2">${esc(j.country)}</span>
        <span class="pk rx">${j.total}/25</span><span class="sm">${esc(j.summary)}</span>
        <span class="rx">${Object.entries(j.scores).map(([k, v]) => `<span class="sc">${esc(pretty(k))} ${v}</span>`).join("")}</span>
        ${(j.unsupported || []).length ? `<span class="unsup">Not supported by any source: ${esc(j.unsupported.join("; "))}</span>` : ""}
      </div>`).join("")}
      ${ev.unaddressed.length ? `<div class="un">Unaddressed: ${esc(ev.unaddressed.join("; "))}</div>` : ""}
    </div>`);
    return recIndex;
  }
  if (ev.type === "review") {
    say(reviewCard(ev));
    if (!replaying) bindReview(ev);
    return recIndex;
  }
  if (ev.type === "intervention") {
    // Live, the card already says what was changed. Replayed, it learns it here.
    const done = [...document.querySelectorAll(`.sys.review[data-round="${ev.round}"] .rvdone`)].pop();
    if (replaying && done) {
      done.textContent = `✓ Round ${ev.round} checked during the run: the verdict was changed`
        + (ev.note ? " and a note was left for the recommender." : ".");
    }
    return recIndex;
  }
  if (ev.type === "recommendation") {
    mark(`rec.${ev.priority}`, "done", ev.elapsed * 1000);
    const label = priorityLabel(ev.priority);
    if (ev.index === 0) {
      board = { ...ev.package };
      say(`<div class="sys final reccompare" data-jump="Recommendation">
        <div class="hd">Recommendations: one per objective, same crisis, same reader</div>
        <div class="reccols" id="recCols"></div></div>`);
    }
    const cols = $("recCols");
    if (cols) {
      cols.insertAdjacentHTML("beforeend", `<div class="reccard">
        <div class="top"><span class="sc prio">${esc(label)}</span></div>
        <h4>${esc(ev.headline)}</h4>
        <div class="bd">${cite(ev.rationale)}</div>
        ${ev.options.map((o) => `<div class="opt2">If ${esc(o.condition)} → ${esc(o.action)} <em>${esc(pretty(o.priority_tag))}</em></div>`).join("")}
        </div>`);
    }
    drawBoard(); drawParties(); scoreBoard();
    return recIndex + 1;
  }
  if (ev.type === "done") {
    mark("score", "done", 1);
    $("pipeMeta").textContent = `${fmt(ev.elapsed, 1)}s · ${ev.usage.calls || 0} calls`;
    drawResults(ev);
    if (replaying) return recIndex;
    if (isResearch()) showTab("analysis");
    else $("resultCard").scrollIntoView({ behavior: "smooth", block: "start" });
  }
  return recIndex;
}

function drawResults(ev) {
  $("resultCard").hidden = false;
  drawJump();
  const you = detail.scenario.focus_country;
  const pctOf = (s, u) => (s.focus_utility_best > 1e-9 ? Math.round((u / s.focus_utility_best) * 100) : null);
  const cards = ev.results.map((r) => {
    const label = priorityLabel(r.priority);
    const s = r.score;
    // One bar: how much of the best deal this opening gets you, with a marker
    // where meeting halfway on every issue would land.
    const got = pctOf(s, s.focus_utility), split = pctOf(s, s.focus_utility_split);
    const gap = got != null && split != null ? got - split : null;
    return `<div class="reccard">
      <div class="top"><span class="prio-lead">If you protect</span> <span class="sc prio">${esc(label)}</span></div>
      <h4>${esc(r.headline)}</h4>
      ${s.legal && got != null ? `<div class="gain px">
        <div class="gnum"><b>${got}%</b><span>of the best deal ${esc(you)} could get</span></div>
        <div class="gbar" role="img" aria-label="${got}% of the best deal; splitting every issue gets ${split}%">
          <i style="width:${Math.max(0, Math.min(100, got))}%"></i>
          <span class="gsplit" style="left:${Math.max(0, Math.min(100, split))}%"></span>
        </div>
        <div class="gscale"><span>0%</span><span class="gsplitlbl" style="left:${Math.max(0, Math.min(100, split))}%">meet halfway: ${split}%</span><span>100%</span></div>
        <p class="gnote">${gap > 0 ? `${gap} points better than meeting halfway on every issue.`
          : gap < 0 ? `${-gap} points worse than meeting halfway on every issue.`
          : "The same as meeting halfway on every issue."}</p>
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
      <button class="mini rx" data-board='${esc(JSON.stringify(r.package))}'>put on the board</button>
    </div>`;
  }).join("");

  // The takeaway, in one plain sentence for the policymaker.
  const scored = ev.results.filter((r) => r.score.legal)
    .map((r) => ({ label: priorityLabel(r.priority).toLowerCase(), got: pctOf(r.score, r.score.focus_utility) }));
  const plain = !ev.objectives_diverged
    ? `<b>Both priorities lead to the same opening</b>, so here you do not have to choose between them.`
    : scored.length === 2
      ? `<b>Your two priorities lead to different openings.</b> Protecting ${esc(scored[0].label)} gets
         ${esc(you)} ${scored[0].got}% of its best deal; protecting ${esc(scored[1].label)} gets
         ${scored[1].got}%. Choose the one that matters more: the other card shows what that choice gives up.`
      : `<b>Your priorities lead to different openings.</b> Choose the one that matters more.`;
  const verdict = `<p class="diverge px">${plain}</p>
    <p class="diverge rx">${ev.objectives_diverged
      ? "<b>The two objectives open differently</b> on the same crisis, for the same reader. The gap between the columns is the price of the choice."
      : "<b>Both objectives open the same way</b> on this crisis: a finding, not a fault."}</p>`;

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
/* Policymakers get Rehearse | Debate & advice; research gets Debate | Analysis.
   A tab the current view does not have falls back to the debate. */
let currentTab = "debate";
function showTab(which) {
  if ((which === "rehearse" && isResearch()) || (which === "analysis" && !isResearch())) which = "debate";
  currentTab = which;
  $("paneRehearse").hidden = which !== "rehearse";
  $("paneDebate").hidden = which !== "debate";
  $("paneAnalysis").hidden = which !== "analysis";
  $("resultWrap").hidden = which === "rehearse";
  document.querySelectorAll(".tab").forEach((t) => {
    t.classList.toggle("on", t.dataset.tab === which);
    t.setAttribute("aria-selected", String(t.dataset.tab === which));
  });
  if (which === "analysis") drawPlot();
}
document.querySelectorAll("[data-goto]").forEach((b) => { b.onclick = () => { showTab(b.dataset.goto); window.scrollTo(0, 0); }; });
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

const today = () => new Date().toISOString().slice(0, 10);
const blankSide = (name) => ({ name, red: "", interests: [], resources: [], provenance: {} });
function freshIntake() {
  return {
    title: "", situation: "", cutoff: today(), sources: "",
    you: blankSide("Singapore"), them: blankSide(""),
    issues: [blankIssue(1), blankIssue(2)],
    docs: [{ title: "", url: "", published: today(), text: "" }],
  };
}

/* An example's "options" object is keyed by labels; map it onto the state. */
function intakeFromForm(f) {
  const s = {
    title: f.title || "", situation: f.situation || "",
    cutoff: f.evidence_cutoff || today(), sources: "",
    you: { ...blankSide(f.you.name), red: (f.you.red_lines || []).join("; ") },
    them: { ...blankSide(f.them.name), red: (f.them.red_lines || []).join("; ") },
    issues: [],
    docs: [{ title: "", url: "", published: today(), text: "" }],
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
      interests: p.interests, resources: p.resources, provenance: p.provenance,
    };
  };
  return {
    title: intake.title.trim(), situation: intake.situation.trim(),
    issues: issues.map((i) => ({ name: i.name, options: i.keep.map(([o]) => o) })),
    you: side("you"), them: side("them"),
    evidence_cutoff: intake.cutoff,
    sources: parseSources(intake.sources),
  };
}

/* One source per line: "Title | link | 2020-03-16 | publisher" (all but the
   link optional). Lines without a web link are dropped: a source the reader
   cannot open is not one they can check. */
function parseSources(text) {
  return String(text || "").split("\n").map((line) => {
    const [title, url, published, publisher] = line.split("|").map((x) => (x || "").trim());
    return { title: title || url, url, published: /^\d{4}-\d{2}-\d{2}$/.test(published || "") ? published : "", publisher: publisher || "" };
  }).filter((s) => /^https?:\/\//.test(s.url));
}
const sourceLines = (sources) => sources.map((s) =>
  [s.title, s.url, s.published, s.publisher].join(" | ").replace(/( \| )+$/, "")).join("\n");

/* Pasted documents -> issues and both briefs, each element keeping its source. */
async function readDocuments() {
  const msg = (x) => { $("dMsg").textContent = undash(x); };
  const docs = intake.docs.filter((d) => d.text.trim());
  if (!intake.you.name.trim() || !intake.them.name.trim()) return msg("Name both sides in step 2 first.");
  if (!docs.length) return msg("Paste the text of at least one document.");
  msg("Reading the documents… this takes a minute.");
  $("dRead").disabled = true;
  try {
    const out = await api("/extract", { method: "POST", body: JSON.stringify({
      documents: docs, parties: [intake.you.name.trim(), intake.them.name.trim()], cutoff: intake.cutoff }) });
    intake.issues = out.issues.slice(0, MAX_ISSUES).map((i, ix) => ({
      name: i.name, options: i.options.slice(0, MAX_OPTIONS),
      you: { rank: ix + 1, marks: i.options.slice(0, MAX_OPTIONS).map(() => "") },
      them: { rank: ix + 1, marks: i.options.slice(0, MAX_OPTIONS).map(() => "") },
    }));
    ["you", "them"].forEach((who, k) => {
      const s = out.sides[k];
      Object.assign(intake[who], { interests: s.interests, resources: s.resources,
        provenance: s.provenance, red: s.red_lines.join("; ") });
    });
    intake.sources = sourceLines(out.sources);
    const r = out.report;
    drawAddForm();
    $("dMsg").textContent = `Read ${r.documents_used} of ${r.documents_in} documents`
      + (r.after_cutoff ? ` (${r.after_cutoff} after the cutoff, not read)` : "")
      + `: ${out.issues.length} issues, ${r.sourced} of ${r.elements} positions backed by a quote. `
      + "Now rank the issues and mark each outcome below.";
  } catch (e) { msg(e.message); $("dRead").disabled = false; }
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
        placeholder="Issue ${ix + 1}, e.g. Who enforces the rules">
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
      <input type="text" id="nTitle" value="${esc(intake.title)}" placeholder="Short title, e.g. Johor River drought shortfall">
      <textarea id="nSum" placeholder="One or two sentences on the situation.">${esc(intake.situation)}</textarea>
      <label class="lab">Evidence cutoff <i>nothing published after this date is used; today for a crisis that is still open</i>
        <input type="date" id="nCut" value="${esc(intake.cutoff)}" max="${today()}"></label></div>

    <div class="step"><div class="sh">Step 2 · Who is at the table <i>(DARA advises the first)</i></div>
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

    <details class="step docs" ${intake.docs.some((d) => d.text) ? "open" : ""}>
      <summary class="sh">Optional · Fill this in from documents <i>(needs a live model)</i></summary>
      <div class="note">Paste statements, press releases or articles. DARA finds the issues and what each
        side has said, and keeps a quote for every position it can source. You still rank the issues
        and mark the outcomes.</div>
      ${intake.docs.map((d, k) => `<div class="doc">
        <div class="two">
          <label class="lab">Title<input type="text" data-doc="${k}" data-f="title" value="${esc(d.title)}"></label>
          <label class="lab">Published<input type="date" data-doc="${k}" data-f="published" value="${esc(d.published)}"></label>
        </div>
        <label class="lab">Link <i>optional</i><input type="url" data-doc="${k}" data-f="url" value="${esc(d.url)}" placeholder="https://"></label>
        <label class="lab">Text<textarea data-doc="${k}" data-f="text" rows="5">${esc(d.text)}</textarea></label>
      </div>`).join("")}
      <div class="gatefoot">
        <button type="button" class="go ghost sm" id="dAdd">+ Another document</button>
        <button type="button" class="go sm" id="dRead">Read these documents</button>
        <span class="msg2" id="dMsg" role="status"></span>
      </div>
    </details>

    <div class="step"><div class="sh">Step 3 · What is on the table <i>(and how each side sees every outcome)</i></div>
      <div class="note"><b>Good</b> = they would be happy with it. <b>OK</b> = they could live with it.
        <b>No</b> = they would walk away rather than accept it. For the other side, give your best guess:
        DARA tests how much the advice depends on that guess.</div>
      ${intake.issues.map(issueCard).join("")}
      ${n < MAX_ISSUES ? `<button type="button" class="go ghost sm" id="nAddIssue">+ Add an issue</button>` : ""}
    </div>

    <div class="step"><div class="sh">Step 4 · Sources <i>(optional, shown under the background as links)</i></div>
      <label class="lab">One per line: title | link | date published | publisher
        <textarea id="nSrc" rows="3" placeholder="PM announces Movement Control Order | https://example.com/article | 2020-03-16 | Malay Mail">${esc(intake.sources)}</textarea></label></div>

    <div class="foot"><span class="msg2" id="nMsg" role="status"></span>
      <a class="go ghost" href="#/" style="display:grid;place-items:center">Cancel</a>
      <button class="go" id="nReview" type="button">Review →</button></div>`;

  /* text: update state without redrawing, so typing keeps focus */
  const bind = (id, set) => { $(id).oninput = (e) => { set(e.target.value); }; };
  bind("nTitle", (v) => (intake.title = v));
  bind("nSum", (v) => (intake.situation = v));
  bind("nAr", (v) => (intake.you.red = v));
  bind("nBr", (v) => (intake.them.red = v));
  bind("nCut", (v) => (intake.cutoff = v));
  bind("nSrc", (v) => (intake.sources = v));
  $("addForm").querySelectorAll("[data-doc]").forEach((el) => {
    el.oninput = () => { intake.docs[el.dataset.doc][el.dataset.f] = el.value; };
  });
  $("dAdd").onclick = () => { intake.docs.push({ title: "", url: "", published: today(), text: "" }); drawAddForm(); };
  $("dRead").onclick = readDocuments;
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
    catch (e) { $("nMsg").textContent = undash(e.message); }
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
  const msg = (t) => { $("nMsg").textContent = undash(t); };
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
      <div class="stat"><b>${pct(reh.expected_share)}</b><span>of your best case: where you likely land</span></div>
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
      <button class="go" id="rSave" type="button">Looks right, save</button></div>`;

  $("rBack").onclick = () => { drawAddForm(); window.scrollTo(0, 0); };
  $("rSave").onclick = async () => {
    try {
      $("rMsg").textContent = "Saving…";
      const res = await api("/intake", { method: "POST",
        body: JSON.stringify({ form: intakePayload(), confirm: true }) });
      intake = null; intakePreview = null;
      location.hash = `#/case/${res.id}`;
    } catch (e) { $("rMsg").textContent = undash(e.message); }
  };
  window.scrollTo(0, 0);
}

/* ----------------------------------------------------------------- boot */
$("gateBtn").onclick = toggleConfirmed;
// Changing what the advice protects does not invalidate the problem, so it does
// not reopen the gate; it only has to keep the two selects distinct.
$("priority1").onchange = () => { syncPriorities(); stages = []; };
$("priority2").onchange = () => { syncPriorities(); stages = []; };
$("runBtn").onclick = onRunPress;
$("stopBtn").onclick = () => abort?.abort();
$("cond").onchange = () => { stages = []; drawStages(); };
$("rounds").onchange = () => { stages = []; drawStages(); };

let savedMode = "policy";
try { savedMode = localStorage.getItem(MODE_KEY) || "policy"; } catch { /* no storage */ }
setMode(savedMode);

api("/health").then((h) => { health = h; drawProvider(); route(); })
  .catch(() => { drawProvider(); route(); });
