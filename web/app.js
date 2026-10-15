/* Issue-to-Repro UI. Plain JS, no build step. Everything is rendered from the run's event stream
   (GET /runs/{id}/events). All text from issues and models is inserted as text nodes, never as HTML.

   The stage list is the navigation: every artifact, sandbox command and model call is filed under the
   stage (and the attempt of that stage) that produced it, and the right pane shows the selected stage.
   While a run is live the selection follows the running stage, until the viewer picks one. */
"use strict";

const STAGES = [
  { id: "FETCH",   label: "Fetch issue",    desc: "Read the issue from GitHub",              tier: null },
  { id: "TRIAGE",  label: "Triage",         desc: "Is it a bug, a feature or a question?",   tier: "nano" },
  { id: "SETUP",   label: "Set up sandbox", desc: "Clone, pick the version, install",        tier: "super" },
  { id: "REPRO",   label: "Reproduce",      desc: "Write a script that fails while the bug exists", tier: "super" },
  { id: "VERDICT", label: "Verdict",        desc: "A separate judge: is it the reported failure?", tier: "ultra" },
  { id: "FIX",     label: "Fix",            desc: "Root cause, patch and regression test",   tier: "super → ultra" },
  { id: "VERIFY",  label: "Verify",         desc: "Fails before the patch, passes after",    tier: null },
  { id: "REPORT",  label: "Report",         desc: "Draft the comment for the issue",         tier: "nano" },
];
const STAGE = Object.fromEntries(STAGES.map((s) => [s.id, s]));

const OUTCOME = {
  CONFIRMED:        { title: "Confirmed",        text: "Bug reproduced, and a patch passed every check.", icon: "check" },
  NOT_REPRODUCED:   { title: "Not reproduced",   text: "The failure could not be reproduced; the author is asked for details.", icon: "help" },
  NEEDS_MAINTAINER: { title: "Needs a maintainer", text: "Not a bug to fix automatically, or no patch could be verified.", icon: "user" },
  SETUP_FAILED:     { title: "Setup failed",     text: "The project could not be installed in the sandbox.", icon: "x" },
  ERROR:            { title: "Run stopped",      text: "The run ended early.", icon: "alert" },
};

const EXAMPLES = [
  { url: "https://github.com/pallets/click/issues/2877", t: "click #2877", d: "A BytesWarning that only appears with python -bb. Known fix exists." },
  { url: "https://github.com/astanin/python-tabulate/issues/399", t: "tabulate #399", d: "A call that never returns. The agent must recognise a hang." },
  { url: "https://github.com/pallets/click/issues/1629", t: "click #1629", d: "A feature request. The right answer is to stop at triage, not to patch." },
];

/* ---- icons: one stroke family, drawn here ------------------------------------------------------ */
const ICON = {
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  alert: '<path d="M12 8v5"/><path d="M12 16.5v.01"/>',
  help: '<path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5v.7"/><path d="M12 17v.01"/>',
  user: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20c1-3.5 3.8-5.5 7-5.5s6 2 7 5.5"/>',
  spin: '<path d="M12 3a9 9 0 1 0 9 9"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  chev: '<path d="M9 6l6 6-6 6"/>',
  ext: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>',
  live: '<circle cx="12" cy="12" r="3"/><path d="M5.6 5.6a9 9 0 0 0 0 12.8M18.4 5.6a9 9 0 0 1 0 12.8"/>',
};
function icon(name, cls = "i") {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", cls); s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICON[name];                 // static markup from the table above, never user data
  return s;
}

const $ = (id) => document.getElementById(id);

/* ---- tiny DOM helper ------------------------------------------------------------------------- */
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const fmtTok = (n) => n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k" : String(n);
const asText = (v) => typeof v === "string" ? v : JSON.stringify(v, null, 2);
const asList = (v) => Array.isArray(v) ? v : (v ? [v] : []);
const fmtSecs = (s) => s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/* ---- state ------------------------------------------------------------------------------------- */
let S = null;          // per-run state
let source = null;     // EventSource
let clock = null;

function freshState(runId) {
  return {
    runId, stages: {}, order: [], current: null, outcome: null, reason: null,
    tokens: { nano: 0, super: 0, ultra: 0 }, calls: 0, cmds: 0, cost: 0,
    firstTs: null, lastTs: null, selected: null, pinned: false,
    trace: { stages: [], calls: [], cmds: [], marks: [] },
  };
}
function currentRun() {
  const st = S.current && S.stages[S.current];
  return st ? st.runs[st.runs.length - 1] : null;
}

/* ---- event handling ---------------------------------------------------------------------------- */
function apply(ev) {
  const p = ev.payload || {};
  S.firstTs ??= ev.ts;
  S.lastTs = ev.ts;
  if (typeof ev.cost_usd === "number") S.cost = ev.cost_usd;

  switch (ev.type) {
    case "stage_started": {
      const st = (S.stages[p.stage] ??= { status: "pending", runs: [] });
      st.status = "running";
      st.runs.push({ items: [], calls: {}, error: null, status: "running" });
      S.current = p.stage;
      if (!S.order.includes(p.stage)) S.order.push(p.stage);
      const open = S.trace.stages[S.trace.stages.length - 1];
      if (open && open.end == null) open.end = ev.ts;
      S.trace.stages.push({ id: p.stage, start: ev.ts, end: null, status: "running" });
      if (!S.pinned) S.selected = p.stage;
      break;
    }
    case "stage_finished": {
      const st = S.stages[p.stage];
      if (st) {
        const run = st.runs[st.runs.length - 1];
        st.status = p.error ? "failed" : "done";
        if (run) { run.status = st.status; run.error = p.error || null; run.next = p.next || null; }
      }
      const band = [...S.trace.stages].reverse().find((b) => b.id === p.stage && b.end == null);
      if (band) { band.end = ev.ts; band.status = p.error ? "failed" : "done"; }
      break;
    }
    case "model_call": {
      S.calls += 1;
      if (p.tier in S.tokens) S.tokens[p.tier] += (p.tokens_in || 0) + (p.tokens_out || 0);
      const run = currentRun();
      if (run) run.calls[p.tier] = (run.calls[p.tier] || 0) + 1;
      const prev = S.trace.calls.length ? S.trace.calls[S.trace.calls.length - 1].end : S.firstTs;
      const band = S.trace.stages[S.trace.stages.length - 1];
      const floor = Math.max(prev, band ? band.start : S.firstTs);       // a call never starts before its stage
      S.trace.calls.push({ start: Math.max(floor, ev.ts - (p.latency_ms || 0) / 1000), end: ev.ts, tier: p.tier });
      pulse(p.tier);
      break;
    }
    case "command_run": {
      S.cmds += 1;
      const run = currentRun();
      if (run) run.items.push({ kind: "cmd", ...p });
      S.trace.cmds.push({ ts: ev.ts, failed: Boolean(p.timed_out || p.exit_code !== 0) });
      break;
    }
    case "artifact": {
      const run = currentRun();
      if (run && p.name) run.items.push({ kind: "artifact", name: p.name, content: p.content });
      if (p.name === "verify_failed") S.trace.marks.push({ ts: ev.ts, kind: "reject" });
      if (p.name === "verified_fix") S.trace.marks.push({ ts: ev.ts, kind: "pass" });
      if (p.name === "repro_result" && ["failed", "hang"].includes((p.content || {}).classification)) S.trace.marks.push({ ts: ev.ts, kind: "repro" });
      break;
    }
    case "outcome":
      S.outcome = p.outcome; S.reason = p.reason || null;
      for (const st of Object.values(S.stages)) {
        if (st.status === "running") { st.status = "done"; st.runs[st.runs.length - 1].status = "done"; }
      }
      for (const b of S.trace.stages) if (b.end == null) { b.end = ev.ts; b.status = "done"; }
      if (!S.pinned) S.selected = S.order.includes("REPORT") ? "REPORT" : S.order[S.order.length - 1] || null;
      stopClock();
      break;
  }
}

/* ---- rendering ----------------------------------------------------------------------------------- */
let renderQueued = false;
function render() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => { renderQueued = false; renderNow(); });
}

function renderNow() {
  if (!S) { renderEmpty(); return; }
  renderRunCard();
  renderMeters();
  renderTrace();
  renderOutcome();
  renderStages();
  renderPane();
}


function renderRunCard() {
  const card = $("runcard");
  if (!S) { card.classList.add("hidden"); return; }
  card.classList.remove("hidden");
  const issue = lastArtifact("issue") || {};
  const url = issue.url || S.issueUrl || "";
  const short = url.replace("https://github.com/", "").replace("/issues/", " #");
  const repo = $("run-repo");
  repo.textContent = short || "GitHub issue";
  if (url) repo.setAttribute("href", url); else repo.removeAttribute("href");
  $("run-title").textContent = issue.title || (S.outcome ? "Issue" : "Reading the issue…");
  const st = $("run-state");
  if (S.outcome) {
    const o = OUTCOME[S.outcome] || { title: S.outcome, icon: "alert" };
    const tone = { CONFIRMED: "good", NOT_REPRODUCED: "warn", NEEDS_MAINTAINER: "info" }[S.outcome] || "bad";
    st.className = `pill ${tone}`; st.replaceChildren(icon(o.icon), o.title);
  } else {
    st.className = "pill info live";
    st.replaceChildren(S.current ? `Running: ${(STAGE[S.current] || {}).label || S.current}` : "Starting");
  }
}

function renderMeters() {
  const t = S ? S.tokens : { nano: 0, super: 0, ultra: 0 };
  const total = t.nano + t.super + t.ultra;
  for (const tier of ["nano", "super", "ultra"]) {
    $(`m-${tier}`).textContent = fmtTok(t[tier]);
    $("split-bar").querySelector(`.seg.${tier}`).style.width = total ? `${(t[tier] / total) * 100}%` : "0";
  }
  $("split-bar").setAttribute("aria-label",
    total ? `Tokens by tier: nano ${t.nano}, super ${t.super}, ultra ${t.ultra}` : "No model calls yet");
  $("m-tokens").textContent = `${total.toLocaleString()} tokens`;
  $("m-calls").textContent = S ? S.calls : 0;
  $("m-cmds").textContent = S ? S.cmds : 0;
  $("m-cost").textContent = "$" + (S ? S.cost : 0).toFixed(4);
  $("m-run").textContent = S ? S.runId : "none";
  tickClock();
}

function tickClock() {
  if (!S || S.firstTs == null) { $("m-time").textContent = "0s"; return; }
  const end = S.outcome ? S.lastTs : Math.max(S.lastTs, Date.now() / 1000);
  $("m-time").textContent = fmtSecs(Math.max(0, Math.round(end - S.firstTs)));
  if (!S.outcome) renderTrace();
}
function startClock() { stopClock(); clock = setInterval(tickClock, 1000); }
function stopClock() { if (clock) clearInterval(clock); clock = null; }

function pulse(tier) {
  const el = document.querySelector(`.legend .t-${tier}`);
  if (!el) return;
  el.classList.remove("pulse"); void el.offsetWidth; el.classList.add("pulse");
}

function renderOutcome() {
  const box = $("outcome");
  if (!S.outcome) { box.className = "outcome hidden"; box.replaceChildren(); return; }
  const o = OUTCOME[S.outcome] || { title: S.outcome, text: "", icon: "alert" };
  const secs = S.firstTs != null ? Math.round(S.lastTs - S.firstTs) : null;
  box.className = `outcome o-${S.outcome}`;
  box.replaceChildren(
    h("h2", {}, icon(o.icon), o.title),
    h("p", {}, S.reason || o.text),
    h("p", { class: "meta num" }, [secs != null ? `${fmtSecs(secs)}` : null, `$${S.cost.toFixed(4)}`,
      plural(S.calls, "model call")].filter(Boolean).join(" · ")));
}

function stageStatus(id) {
  const st = S.stages[id];
  if (st) return st.status;
  return S.outcome ? "skipped" : "pending";
}

function renderStages() {
  const ol = $("stages");
  ol.replaceChildren();
  STAGES.forEach((def, i) => {
    const st = S.stages[def.id];
    const status = stageStatus(def.id);
    const mark = status === "done" ? icon("check") : status === "failed" ? icon("x")
      : status === "running" ? icon("spin") : String(i + 1);
    const meta = [];
    if (st) {
      const calls = {};
      for (const r of st.runs) for (const [tier, n] of Object.entries(r.calls)) calls[tier] = (calls[tier] || 0) + n;
      for (const [tier, n] of Object.entries(calls)) meta.push(h("span", { class: `tier ${tier}` }, `${tier} · ${plural(n, "call")}`));
      if (st.runs.length > 1) meta.push(h("span", {}, plural(st.runs.length, "attempt")));
      if (!Object.keys(calls).length && def.id === "VERIFY") meta.push(h("span", { class: "tier none" }, "no model, code only"));
    } else if (def.tier) {
      meta.push(h("span", { class: `tier ${def.tier.split(" ")[0]}` }, def.tier));
    }
    const lastErr = st && st.runs.length ? st.runs[st.runs.length - 1].error : null;
    ol.append(h("li", { class: `stage ${status}${S.selected === def.id ? " selected" : ""}` },
      h("button", {
        class: "stage-btn", type: "button", disabled: !st,
        "aria-current": S.selected === def.id ? "step" : null,
        onclick: () => { S.selected = def.id; S.pinned = !S.outcome && def.id !== S.current; renderNow(); },
      },
        h("span", { class: "mark-dot" }, mark),
        h("span", {},
          h("span", { class: "stage-name" }, def.label),
          h("span", { class: "stage-desc" }, def.desc),
          meta.length ? h("span", { class: "stage-meta" }, meta) : null,
          lastErr ? h("span", { class: "stage-err" }, lastErr) : null))));
  });
}


/* ---- run trace: the run drawn over time ----------------------------------------------------------
   Row 1: stages as bands (click to open). Row 2: each model call as a bar in its tier colour, as long
   as the call took. Row 3: sandbox commands as ticks, red when they failed. Marks: the moment the bug
   was reproduced, each patch the gate rejected, and the verified fix. */
const SVGNS = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}, text) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, v);
  if (text != null) el.textContent = text;
  return el;
}
const SHORT = { FETCH: "Fetch", TRIAGE: "Triage", SETUP: "Setup", REPRO: "Reproduce", VERDICT: "Verdict", FIX: "Fix", VERIFY: "Verify", REPORT: "Report" };

function renderTrace() {
  const box = $("trace");
  if (!box) return;
  if (!S || S.firstTs == null) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  const plot = box.querySelector(".trace-plot");
  const W = Math.max(280, Math.floor(plot.getBoundingClientRect().width)), H = 62;
  const t0 = S.firstTs;
  const t1 = Math.max(t0 + 1, S.outcome ? S.lastTs : Math.max(S.lastTs, Date.now() / 1000));
  const x = (t) => ((Math.min(Math.max(t, t0), t1) - t0) / (t1 - t0)) * W;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img",
    "aria-label": `Run trace: ${S.trace.stages.length} stage runs, ${S.trace.calls.length} model calls, ${S.trace.cmds.length} sandbox commands` });

  // stage bands
  S.trace.stages.forEach((b) => {
    const x0 = x(b.start), x1 = x(b.end ?? t1), w = Math.max(2, x1 - x0 - 2);
    const g = svg("g", { class: `band ${b.status}${S.selected === b.id ? " sel" : ""}`, tabindex: "0", role: "button",
      "aria-label": `${STAGE[b.id] ? STAGE[b.id].label : b.id} stage` });
    g.append(svg("title", {}, `${STAGE[b.id] ? STAGE[b.id].label : b.id} · ${fmtSecs(Math.round((b.end ?? t1) - b.start))}`));
    g.append(svg("rect", { x: x0, y: 4, width: w, height: 24, rx: 4 }));
    const label = SHORT[b.id] || b.id;
    if (w > label.length * 7 + 12) g.append(svg("text", { x: x0 + 8, y: 20 }, label));
    const open = () => { S.selected = b.id; S.pinned = !S.outcome && b.id !== S.current; renderNow(); };
    g.addEventListener("click", open);
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    root.append(g);
  });
  // model calls
  S.trace.calls.forEach((c) => {
    const x0 = x(c.start), w = Math.max(3, x(c.end) - x0);
    root.append(svg("rect", { class: `call ${c.tier}`, x: x0, y: 33, width: w, height: 9, rx: 2 },
    ));
  });
  // sandbox commands
  S.trace.cmds.forEach((c) => root.append(svg("rect", { class: `tick${c.failed ? " failed" : ""}`, x: x(c.ts) - 0.75, y: 47, width: 1.5, height: 10 })));
  // marks
  S.trace.marks.forEach((m) => {
    const cx = x(m.ts);
    const g = svg("g", { class: `mark ${m.kind}` });
    g.append(svg("title", {}, m.kind === "reject" ? "Patch rejected by the gate" : m.kind === "pass" ? "Fix verified" : "Bug reproduced"));
    g.append(svg("line", { x1: cx, x2: cx, y1: 2, y2: 58 }));
    g.append(svg("circle", { cx, cy: 4, r: 4 }));
    root.append(g);
  });
  // live edge
  if (!S.outcome) root.append(svg("rect", { class: "now", x: W - 2, y: 2, width: 2, height: 56 }));
  box.querySelector(".trace-plot").replaceChildren(root);
  box.querySelector(".trace-start").textContent = "0s";
  box.querySelector(".trace-end").textContent = fmtSecs(Math.round(t1 - t0));
}
window.addEventListener("resize", () => { if (S) renderTrace(); });

/* ---- pane ------------------------------------------------------------------------------------------ */
function renderPane() {
  const pane = $("pane");
  const id = S.selected;
  if (!id || !S.stages[id]) {
    pane.replaceChildren(h("div", { class: "waiting" }, icon("spin"), "Starting the run…"));
    return;
  }
  const def = STAGE[id] || { label: id, desc: "" };
  const st = S.stages[id];
  const live = !S.outcome && S.current !== id && S.pinned;
  const head = h("div", { class: "pane-head" },
    h("div", {}, h("h1", {}, def.label), h("p", {}, def.desc)),
    h("div", { class: "status" },
      statusBadge(st.status),
      live ? h("button", { class: "btn small", type: "button",
        onclick: () => { S.pinned = false; S.selected = S.current; renderNow(); } }, icon("live"), "Follow live") : null));

  const body = h("div");
  if (id === "REPORT") body.append(viewReport(st));
  st.runs.forEach((run, i) => {
    if (id === "REPORT") return;
    const many = st.runs.length > 1;
    const wrap = h("section", { class: "attempt" });
    if (many) wrap.append(h("h2", { class: "attempt-head" }, `Attempt ${i + 1}`, attemptBadge(id, run, i),
      h("span", { class: "sub" }, attemptNote(run))));
    wrap.append(...viewRun(id, run));
    if (run.error) wrap.append(h("div", { class: "block" }, h("h3", {}, "Stage error"), h("pre", { class: "out stderr" }, run.error)));
    if (!run.items.length && run.status === "running") wrap.append(h("div", { class: "waiting" }, icon("spin"), "Working…"));
    body.append(wrap);
  });

  const doc = document.scrollingElement;
  const stick = doc.scrollTop + window.innerHeight >= doc.scrollHeight - 80;
  const sameStage = pane.dataset.stage === id;
  pane.replaceChildren(head, body);
  pane.dataset.stage = id;
  const top = pane.getBoundingClientRect().top + doc.scrollTop - 84;
  if (!sameStage && doc.scrollTop > top) window.scrollTo({ top, behavior: "smooth" });
  else if (sameStage && stick && st.status === "running" && doc.scrollTop > 0) window.scrollTo({ top: doc.scrollHeight });
}

function statusBadge(status) {
  if (status === "done") return h("span", { class: "badge good" }, icon("check"), "Done");
  if (status === "failed") return h("span", { class: "badge bad" }, icon("x"), "Failed");
  if (status === "running") return h("span", { class: "badge info" }, "Running");
  return h("span", { class: "badge neutral" }, status);
}
function attemptBadge(id, run, index) {
  if (id === "FIX" && S.stages.VERIFY && S.stages.VERIFY.runs[index]) return attemptBadge("VERIFY", S.stages.VERIFY.runs[index], index);
  const names = run.items.filter((x) => x.kind === "artifact").map((x) => x.name);
  if (names.includes("verify_failed")) return h("span", { class: "badge bad" }, "Rejected");
  if (names.includes("verified_fix") || names.includes("verify_passed")) return h("span", { class: "badge good" }, "Verified");
  if (run.status === "running") return h("span", { class: "badge info" }, "Running");
  if (run.status === "failed") return h("span", { class: "badge bad" }, "Failed");
  return null;
}
function attemptNote(run) {
  const n = Object.values(run.calls).reduce((a, b) => a + b, 0);
  const cmds = run.items.filter((x) => x.kind === "cmd").length;
  return [n ? plural(n, "model call") : null, cmds ? plural(cmds, "command") : null].filter(Boolean).join(" · ");
}

/* A run's items in order: consecutive sandbox commands are grouped into one block. */
const QUIET_COMMANDS = new Set(["FETCH", "TRIAGE", "VERDICT", "FIX", "REPORT"]);   // commands are plumbing here

function viewRun(stageId, run) {
  const out = [];
  let cmds = null;
  const names = new Set(run.items.filter((x) => x.kind === "artifact").map((x) => x.name));
  if (QUIET_COMMANDS.has(stageId)) {
    const quiet = run.items.filter((x) => x.kind === "cmd");
    for (const item of run.items) if (item.kind === "artifact") out.push(viewArtifact(item.name, item.content));
    if (quiet.length) out.push(h("details", { class: "block" },
      h("summary", { class: "muted" }, `${plural(quiet.length, "sandbox command")} used to read the code`),
      h("div", { class: "cmds", style: "margin-top:12px" }, quiet.map(viewCommand))));
    return out;
  }
  for (const item of run.items) {
    if (item.kind === "cmd") {
      if (!cmds) { cmds = h("div", { class: "cmds" }); out.push(h("div", { class: "block" }, h("h3", {}, "Sandbox commands"), cmds)); }
      cmds.append(viewCommand(item));
      continue;
    }
    cmds = null;
    if (item.name === "verify_passed" && names.has("verified_fix")) continue;   // shown inside verified_fix
    out.push(viewArtifact(item.name, item.content));
  }
  return out;
}

function viewCommand(c) {
  const failed = c.timed_out || c.exit_code !== 0;
  const exit = c.timed_out ? h("span", { class: "exit timeout" }, "timed out")
    : h("span", { class: "exit " + (c.exit_code === 0 ? "ok" : "fail") }, `exit ${c.exit_code}`);
  const hasOut = Boolean(c.stdout || c.stderr);
  const det = h("details", { class: "cmd", open: failed && hasOut ? true : null },
    h("summary", {}, hasOut ? icon("chev", "i chev") : h("span", { style: "width:14px" }),
      h("span", { class: "prompt" }, "$"), h("span", { class: "text" }, c.cmd), exit));
  if (hasOut) {
    const pre = h("pre", { class: "out" });
    if (c.stdout) pre.append(c.stdout.endsWith("\n") ? c.stdout : c.stdout + "\n");
    if (c.stderr) pre.append(h("span", { class: "stderr" }, c.stderr));
    det.append(pre);
  }
  return det;
}

/* ---- artifact views ------------------------------------------------------------------------------ */
function block(title, ...kids) { return h("div", { class: "block" }, title ? h("h3", {}, title) : null, ...kids); }
function kv(obj) {
  return h("dl", { class: "kv" }, Object.entries(obj).filter(([, v]) => v != null && v !== "" && !(Array.isArray(v) && !v.length))
    .flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, Array.isArray(v) ? v.join(", ") : asText(v))]));
}
function code(text, cls = "code") { return h("pre", { class: cls }, text ?? ""); }
function diffView(text) {
  const pre = h("pre", { class: "diff" });
  for (const line of String(text || "").split("\n")) {
    const cls = line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff ") || line.startsWith("index ") ? "file"
      : line.startsWith("@@") ? "hunk" : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "";
    pre.append(h("span", { class: `l ${cls}` }, line || " "), "\n");
  }
  return pre;
}
function badge(text, kind, ic) { return h("span", { class: `badge ${kind}` }, ic ? icon(ic) : null, text); }
function list(items) { return h("ul", { class: "plain" }, asList(items).map((s) => h("li", {}, asText(s)))); }

function viewArtifact(name, c) {
  c = c ?? {};
  switch (name) {
    case "issue":
      return block(null,
        h("p", { class: "lead" }, c.title || "(untitled issue)"),
        kv({ Repository: c.repo, "Requested ref": c.ref }),
        c.url ? h("p", { style: "margin-top:10px" }, h("a", { href: c.url, target: "_blank", rel: "noopener" }, "Open the issue on GitHub ", icon("ext"))) : null);

    case "triage": {
      const kind = c.kind || "unknown";
      return h("div", {},
        block("Classification",
          h("p", {}, badge(kind === "bug" ? "Bug" : kind[0].toUpperCase() + kind.slice(1), kind === "bug" ? "good" : "info"),
            c.claimed_version ? h("span", { class: "muted" }, `  reported against version ${c.claimed_version}`) : null)),
        h("div", { class: "two" },
          block("Expected", h("p", {}, c.expected || "Not stated")),
          block("Actual", h("p", {}, c.actual || "Not stated"))),
        asList(c.steps).length ? block("Steps to reproduce", h("ol", { class: "plain" }, asList(c.steps).map((s) => h("li", {}, asText(s))))) : null,
        asList(c.missing_info).length ? block("Missing from the report", list(c.missing_info)) : null);
    }

    case "ref_choice":
      return block("Version", h("p", {}, "Checked out ", h("b", {}, c.ref || "default branch"), c.note ? h("span", { class: "muted" }, `: ${c.note}`) : null));

    case "environment": case "environment_degraded": case "environment_failed":
      return block(name === "environment_failed" ? "Environment could not be built" : "Environment",
        kv({ Python: c.python, Ref: c.ref, "Tests collect": c.tests_collect_ok == null ? null : (c.tests_collect_ok ? "yes" : "no"),
             "Install attempts": c.attempts, Warnings: asList(c.warnings) }),
        asList(c.install_commands).length ? [h("h4", {}, "Install commands"), code(asList(c.install_commands).join("\n"))] : null);

    case "setup_plan":
      return block("Install plan proposed by super", code(asText(c)));

    case "repro_script":
      return h("div", {},
        block("Reproduction script",
          h("p", { class: "muted" }, "Written to fail while the bug exists and pass once it is fixed."),
          kv({ File: c.path, "Python flags": asList(c.python_flags), "Extra packages": asList(c.extra_packages), "Expected symptom": c.expected_symptom }),
          h("div", { style: "margin-top:12px" }, code(c.script ?? asText(c))),
          c.reasoning ? h("details", { style: "margin-top:10px" }, h("summary", { class: "muted" }, "Why this script"), h("p", { style: "margin-top:8px" }, c.reasoning)) : null));

    case "repro_result": {
      const kind = c.classification || "unknown";
      const reproduced = kind === "failed" || kind === "hang";
      return block("Result",
        h("p", {}, badge(reproduced ? (kind === "hang" ? "Hangs, as reported" : "Fails, as expected") : kind === "passed" ? "Passed: bug not shown" : kind,
          reproduced ? "good" : "warn", reproduced ? "check" : null), h("span", { class: "muted" }, `  exit code ${c.exit_code}`)),
        code(c.output, "out"));
    }

    case "repro_gave_up":
      return block("Gave up reproducing", h("p", {}, `After ${plural(c.attempts || 0, "attempt")} no script showed the reported failure.`), c.last_result ? code(asText(c.last_result), "out") : null);

    case "repo_modified_by_script":
      return block("The script changed files in the repository", code(asText(c.changes), "out"));

    case "verdict": {
      const conf = Math.round((Number(c.confidence) || 0) * 100);
      return h("div", {},
        block("Does the failure match the issue?",
          h("p", {}, c.matches_issue ? badge("Matches the report", "good", "check") : badge("Does not match", "bad", "x")),
          h("div", { class: "conf" }, h("span", {}, `Confidence ${conf}%`), h("span", { class: "conf-bar" }, h("i", { style: `width:${conf}%` }))),
          h("ul", { class: "checks", style: "margin-top:14px" },
            h("li", { class: c.failure_matches_symptom ? "" : "no" }, icon(c.failure_matches_symptom ? "check" : "x"), "The observed failure is the reported one"),
            h("li", { class: c.would_pass_if_fixed ? "" : "no" }, icon(c.would_pass_if_fixed ? "check" : "x"), "The script would pass if the library were fixed"))),
        h("div", { class: "two" },
          block("Reported", h("p", {}, c.reported_symptom || "Not stated")),
          block("Observed", h("p", {}, c.observed_symptom || "Not stated"))),
        block("Reasoning from ultra", h("p", {}, c.reasoning || "")),
        c.cannot_reproduce_in_sandbox ? block("Cannot be reproduced in this sandbox", h("p", {}, "The judge found the bug depends on something the Linux sandbox does not have.")) : null);
    }

    case "fix_locate":
      return block("Where to look",
        kv({ Files: asList(c.files), "Search terms": asList(c.search_terms) }),
        c.reasoning ? h("p", { class: "muted", style: "margin-top:10px" }, c.reasoning) : null);

    case "fix_plan":
      return h("div", {},
        block(`Proposed patch, written by ${c.tier || "super"}`,
          c.root_cause ? h("p", {}, h("b", {}, "Root cause. "), c.root_cause) : null,
          c.test_path ? h("p", { class: "muted" }, `Regression test: ${c.test_path}`) : null,
          asList(c.plan_errors).length ? [h("h4", {}, "Problems found before running it"), list(c.plan_errors)] : null,
          c.patch ? h("div", { style: "margin-top:12px" }, diffView(c.patch)) : null));

    case "verify_failed":
      return block("Why the gate rejected it", code(c.reason || "", "out"));

    case "verified_fix":
      return viewProof(c);

    case "verify_passed":
      return block("Gate passed", h("p", {}, badge("All checks passed", "good", "check")),
        kv({ "Test exit before": c.before, "Test exit after": c.after }));

    default:
      return block(name.replace(/_/g, " "), code(asText(c)));
  }
}

/* The fail-before / pass-after proof, from the verified_fix artifact. */
function viewProof(c) {
  const before = c.before || {}, after = c.after || {}, reproAfter = c.repro_after || {}, suite = c.suite || {};
  const newFailures = asList(suite.new_failures);
  return h("div", {},
    h("div", { class: "proof" },
      h("div", { class: "side before" }, h("div", { class: "label" }, "New test on the original code"),
        h("div", { class: "big" }, before.timed_out ? "Hangs" : "Fails"), h("div", { class: "faint num" }, `exit ${before.exit_code ?? "?"}`)),
      h("div", { class: "arrow" }, icon("arrow")),
      h("div", { class: "side after" }, h("div", { class: "label" }, "Same test with the patch"),
        h("div", { class: "big" }, "Passes"), h("div", { class: "faint num" }, `exit ${after.exit_code ?? "?"}`))),
    block("The four checks",
      h("ul", { class: "checks" },
        h("li", {}, icon("check"), "The new regression test fails on the original code"),
        h("li", {}, icon("check"), "It passes with the patch applied"),
        h("li", { class: reproAfter.exit_code === 0 ? "" : "no" }, icon(reproAfter.exit_code === 0 ? "check" : "x"), "The original reproduction script now passes"),
        h("li", { class: newFailures.length ? "no" : "" }, icon(newFailures.length ? "x" : "check"),
          suite.ran === false ? "The existing test suite could not be run in time" : newFailures.length ? `Breaks existing tests: ${newFailures.join(", ")}` : "No previously passing test breaks"))),
    c.root_cause ? block("Root cause", h("p", {}, c.root_cause)) : null,
    c.patch ? block("Patch", diffView(c.patch)) : null,
    c.test_code ? block(`Regression test${c.test_path ? ` (${c.test_path})` : ""}`, code(c.test_code)) : null);
}

/* REPORT: the model's title and summary, with the evidence sections built from the run's own artifacts
   (script, output, patch, test), so formatting never depends on the model keeping its line breaks. */
function findArtifact(name) {
  for (const id of S.order) for (const run of S.stages[id].runs)
    for (let i = run.items.length - 1; i >= 0; i--) if (run.items[i].kind === "artifact" && run.items[i].name === name) return run.items[i].content;
  return null;
}
function lastArtifact(name) {
  let found = null;
  for (const id of S.order) for (const run of S.stages[id].runs)
    for (const it of run.items) if (it.kind === "artifact" && it.name === name) found = it.content;
  return found;
}
function summaryOf(comment) {
  const text = String(comment || "").trim();
  const formatted = text.split("\n").length >= 3;
  if (formatted) return text.split(/\n#{1,6}\s/)[0].trim();
  return text.split(/#{2,}|```/)[0].trim();             // unformatted reply: keep the opening prose only
}
function viewReport(st) {
  const rep = lastArtifact("report");
  if (!rep) {
    const run = st.runs[st.runs.length - 1];
    return run && run.status === "running" ? h("div", { class: "waiting" }, icon("spin"), "Nano is drafting the comment…") : block(null, h("p", { class: "muted" }, "No comment was produced."));
  }
  const script = lastArtifact("repro_script"), result = lastArtifact("repro_result"), fix = lastArtifact("verified_fix");
  return h("div", {},
    block(null,
      h("p", { class: "faint", style: "margin-bottom:6px" }, rep.generated_by_model === false ? "Comment from the plain template" : "Comment drafted by nano"),
      h("p", { class: "lead", style: "font-weight:600" }, rep.title || ""),
      h("p", { class: "md" }, inline(h("span"), summaryOf(rep.comment)))),
    fix ? viewProof(fix) : null,
    script ? block("Reproduction", code(script.script || ""), result ? h("div", { style: "margin-top:10px" }, code(result.output || "", "out")) : null) : null,
    h("details", { class: "block" }, h("summary", { class: "muted" }, "Full comment text as written by the model"),
      h("pre", { class: "out", style: "margin-top:10px" }, rep.comment || "")));
}
function inline(el, text) {
  String(text).split(/(`[^`\n]+`)/).forEach((part) => {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 1) el.append(h("code", {}, part.slice(1, -1)));
    else if (part) el.append(part);
  });
  return el;
}

/* ---- empty state: teach the interface -------------------------------------------------------------- */
function renderEmpty() {
  renderRunCard();
  $("outcome").className = "outcome hidden";
  $("stages").replaceChildren(...STAGES.map((def, i) => h("li", { class: "stage pending" },
    h("button", { class: "stage-btn", type: "button", disabled: true },
      h("span", { class: "mark-dot" }, String(i + 1)),
      h("span", {}, h("span", { class: "stage-name" }, def.label), h("span", { class: "stage-desc" }, def.desc),
        def.tier ? h("span", { class: "stage-meta" }, h("span", { class: `tier ${def.tier.split(" ")[0]}` }, def.tier))
          : def.id === "VERIFY" ? h("span", { class: "stage-meta" }, h("span", { class: "tier none" }, "no model, code only")) : null)))));
  const fill = (url) => { $("issue-url").value = url; $("issue-url").focus(); };
  $("pane").replaceChildren(h("div", { class: "empty" },
    h("h1", {}, "Reproduce a GitHub issue, then prove the fix"),
    h("p", {}, "Paste an issue from a Python library. The agent reproduces it in a Nebius sandbox, a separate Nemotron model judges whether the failure is the reported one, and a patch only counts if its test fails before the fix and passes after."),
    h("h2", {}, "Try one of these"),
    h("div", { class: "pick" }, EXAMPLES.map((ex) => h("button", { type: "button", onclick: () => fill(ex.url) },
      h("span", { class: "t" }, ex.t), h("span", { class: "d" }, ex.d), h("span", { class: "go" }, icon("arrow"))))),
    h("div", { id: "recent" }),
    h("h2", {}, "What happens"),
    h("ol", { class: "steps" },
      h("li", {}, h("b", {}, "Reproduce"), "Super writes a script that fails while the bug exists."),
      h("li", {}, h("b", {}, "Judge"), "Ultra checks it is the reported failure, not another one."),
      h("li", {}, h("b", {}, "Fix"), "Super proposes a patch and a regression test."),
      h("li", {}, h("b", {}, "Verify"), "Plain code runs the test before and after. No model grades itself."))));
  showRecent();
}
async function showRecent() {
  try {
    const runs = (await api("/runs?limit=10")).filter((r) => r.state === "done").slice(0, 3);
    const box = $("recent");
    if (!box || !runs.length) return;
    box.replaceChildren(h("h2", {}, "Or open a finished run"),
      h("div", { class: "pick" }, runs.map((r) => h("button", { type: "button", onclick: () => openRun(r.id) },
        h("span", { class: "t" }, r.issue_url.replace("https://github.com/", "").replace("/issues/", " #")),
        h("span", { class: "d" }, `${(OUTCOME[r.outcome] || {}).title || r.outcome || r.state} · $${(r.cost || 0).toFixed(4)} · press Replay to watch it again`),
        h("span", { class: "go" }, icon("arrow"))))));
  } catch { /* optional */ }
}

/* ---- networking --------------------------------------------------------------------------- */
function toast(msg) {
  const t = $("toast");
  t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.add("hidden"), 5000);
}

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

let MODE = "background";   // "inline" on serverless hosts (Vercel): runs and replays live inside one request

function openRun(runId, push = true) {
  if (push) history.pushState({ runId }, "", `?run=${encodeURIComponent(runId)}`);
  openStream(`/runs/${encodeURIComponent(runId)}/events`, runId, { resumable: true });
}

/* Open an SSE stream and render it. A resumable stream may reconnect (the server resumes after
   Last-Event-ID). A single-request stream must NOT reconnect: reopening /runs/live would start a
   second, paid run, so on any error it is closed instead. */
function openStream(url, label, { resumable = false } = {}) {
  if (source) source.close();
  S = freshState(label);
  $("replay-btn").disabled = true;
  renderNow();
  startClock();
  const es = new EventSource(url);
  source = es;
  let finished = false;
  const finish = () => {
    finished = true; es.close();
    if (source === es) source = null;
    stopClock(); render();
    $("replay-btn").disabled = false; loadHistory();
  };
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (S.runId === "starting…" && ev.run_id) {          // a live run learns its id from the first event
      S.runId = ev.run_id;
      if (MODE !== "inline") history.replaceState({ runId: ev.run_id }, "", `?run=${encodeURIComponent(ev.run_id)}`);
    }
    apply(ev); render();
  };
  es.addEventListener("end", finish);
  es.addEventListener("fail", (m) => { toast(JSON.parse(m.data).detail || "The run could not start."); finish(); });
  es.onerror = () => {
    if (finished) return;
    if (!resumable) { toast("Connection to the server was lost."); finish(); }
    else if (es.readyState === EventSource.CLOSED) toast("Lost connection to the server.");
  };
}

async function loadHistory() {
  try {
    const runs = await api("/runs?limit=30");
    const sel = $("history");
    sel.replaceChildren(h("option", { value: "" }, "Previous runs"));
    for (const r of runs) {
      const name = r.issue_url.replace("https://github.com/", "").replace("/issues/", " #");
      const label = (OUTCOME[r.outcome] || {}).title || (r.state === "done" ? r.outcome : "running");
      sel.append(h("option", { value: r.id, selected: S && S.runId === r.id }, `${name} · ${label}`));
    }
  } catch { /* history is optional */ }
}

$("run-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("run-btn");
  btn.disabled = true;
  const url = $("issue-url").value.trim();
  try {
    if (MODE === "inline") {
      openStream(`/runs/live?issue_url=${encodeURIComponent(url)}`, "starting…");
      S.issueUrl = url;
    } else {
      const { run_id } = await api("/runs", { method: "POST", body: JSON.stringify({ issue_url: url }) });
      openRun(run_id);
      loadHistory();
    }
  } catch (err) { toast(err.message); }
  finally { btn.disabled = false; }
});

$("history").addEventListener("change", (e) => { if (e.target.value) openRun(e.target.value); });

$("replay-btn").addEventListener("click", async () => {
  if (!S) return;
  try {
    if (MODE === "inline") {
      openStream(`/runs/${encodeURIComponent(S.runId)}/replay-stream?speed=4`, S.runId);
    } else {
      const { run_id } = await api(`/runs/${encodeURIComponent(S.runId)}/replay?speed=4`, { method: "POST" });
      openRun(run_id);
      loadHistory();
    }
  } catch (err) { toast(err.message); }
});

window.addEventListener("popstate", () => {
  const id = new URLSearchParams(location.search).get("run");
  if (id) openRun(id, false);
});

// Boot: open ?run=<id> if present, so a link to a run shows that run.
(async () => {
  try {
    const health = await api("/health");
    MODE = health.mode || "background";
    if (health.new_runs === false) {               // public demo in replay-only mode
      $("issue-url").disabled = true;
      $("run-btn").disabled = true;
      $("issue-url").placeholder = "Replay-only demo: pick a previous run on the right, then press Replay";
    }
  } catch { /* server unreachable: the stream will report it */ }
  const id = new URLSearchParams(location.search).get("run");
  if (id) openRun(id, false);
  else renderNow();                                 // empty state
  loadHistory();
})();
