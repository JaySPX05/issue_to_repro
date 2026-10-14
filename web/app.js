/* Issue-to-Repro UI. Plain JS, no build step. Everything is rendered from the run's event stream
   (GET /runs/{id}/events). All text from issues/models is inserted with textContent, never as HTML. */
"use strict";

const STAGES = [
  { id: "FETCH",   label: "Fetch issue",     desc: "Read the GitHub issue",                  tier: null },
  { id: "TRIAGE",  label: "Triage",          desc: "Bug, feature or question?",              tier: "nano" },
  { id: "SETUP",   label: "Set up sandbox",  desc: "Clone, pick version, install",           tier: "super" },
  { id: "REPRO",   label: "Reproduce",       desc: "Write and run a failing script",         tier: "super" },
  { id: "VERDICT", label: "Verdict",         desc: "Does the failure match the report?",     tier: "ultra" },
  { id: "FIX",     label: "Fix",             desc: "Root cause, patch and regression test",  tier: "super → ultra" },
  { id: "VERIFY",  label: "Verify",          desc: "Test fails before, passes after",        tier: "no model" },
  { id: "REPORT",  label: "Report",          desc: "Draft the comment for the issue",        tier: "nano" },
];

const TAB_LABELS = {
  terminal: "Terminal", issue: "Issue", triage: "Triage", ref_choice: "Version", setup_plan: "Setup plan",
  environment: "Environment", environment_degraded: "Environment", environment_failed: "Setup failed",
  repro_script: "Repro script", repro_result: "Repro output", repo_modified_by_script: "Repo changes",
  repro_gave_up: "Gave up", verdict: "Verdict", fix_locate: "Located files", fix_plan: "Patch",
  verify_failed: "Verification", verify_passed: "Verification", report: "Final comment",
};
// A few artifact names share one tab (latest wins).
const TAB_KEY = { environment_degraded: "environment", environment_failed: "environment",
                  verify_failed: "verify", verify_passed: "verify" };

const OUTCOME_TEXT = {
  CONFIRMED: "Bug reproduced, patch verified",
  NOT_REPRODUCED: "Could not reproduce — asking the author for details",
  NEEDS_MAINTAINER: "Needs a maintainer",
  SETUP_FAILED: "Could not set up the project",
  ERROR: "Run stopped",
};

const $ = (id) => document.getElementById(id);

/* ---- tiny DOM helper ------------------------------------------------------------------- */
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

/* ---- state ------------------------------------------------------------------------------ */
let S = null;          // per-run state
let source = null;     // EventSource
let clock = null;

function freshState(runId) {
  return {
    runId, stages: {}, order: [], current: null, outcome: null, reason: null,
    tokens: { nano: 0, super: 0, ultra: 0 }, calls: 0, cmds: 0, cost: 0,
    firstTs: null, lastTs: null, commands: [], tabs: new Map(), active: null, follow: true, unseen: new Set(),
  };
}

/* ---- event handling --------------------------------------------------------------------- */
function apply(ev) {
  const p = ev.payload || {};
  S.firstTs ??= ev.ts;
  S.lastTs = ev.ts;
  if (typeof ev.cost_usd === "number") S.cost = ev.cost_usd;

  switch (ev.type) {
    case "stage_started": {
      const st = (S.stages[p.stage] ??= { status: "pending", runs: 0, calls: {}, error: null });
      st.status = "running"; st.runs += 1; st.error = null;
      S.current = p.stage;
      if (!S.order.includes(p.stage)) S.order.push(p.stage);
      break;
    }
    case "stage_finished": {
      const st = S.stages[p.stage];
      if (st) { st.status = p.error ? "failed" : "done"; st.error = p.error || null; }
      break;
    }
    case "model_call": {
      S.calls += 1;
      if (p.tier in S.tokens) S.tokens[p.tier] += (p.tokens_in || 0) + (p.tokens_out || 0);
      const st = S.current && S.stages[S.current];
      if (st) st.calls[p.tier] = (st.calls[p.tier] || 0) + 1;
      flash(p.tier);
      break;
    }
    case "command_run":
      S.cmds += 1;
      S.commands.push(p);
      upsertTab("terminal", p);
      break;
    case "artifact":
      if (p.name) upsertTab(TAB_KEY[p.name] || p.name, { name: p.name, content: p.content });
      break;
    case "outcome":
      S.outcome = p.outcome; S.reason = p.reason || null;
      for (const s of Object.values(S.stages)) if (s.status === "running") s.status = "done";
      stopClock();
      break;
  }
}

function upsertTab(key, value) {
  S.tabs.set(key, value);
  if (S.follow) S.active = key;
  else if (key !== S.active) S.unseen.add(key);
}

/* ---- rendering -------------------------------------------------------------------------- */
let renderQueued = false;
function render() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => { renderQueued = false; renderNow(); });
}

function renderNow() {
  if (!S) return;
  renderMeters();
  renderStages();
  renderOutcome();
  renderTabs();
  renderPane();
}

function renderMeters() {
  for (const t of ["nano", "super", "ultra"]) $(`m-${t}`).textContent = fmtTok(S.tokens[t]);
  $("m-calls").textContent = S.calls;
  $("m-cmds").textContent = S.cmds;
  $("m-cost").textContent = "$" + S.cost.toFixed(4);
  $("m-run").textContent = S.runId;
  tickClock();
}

function tickClock() {
  if (!S || S.firstTs == null) { $("m-time").textContent = "0s"; return; }
  const end = S.outcome ? S.lastTs : Math.max(S.lastTs, Date.now() / 1000);
  const s = Math.max(0, Math.round(end - S.firstTs));
  $("m-time").textContent = s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
}
function startClock() { stopClock(); clock = setInterval(tickClock, 1000); }
function stopClock() { if (clock) clearInterval(clock); clock = null; }

function flash(tier) {
  const el = document.querySelector(`.meter[data-tier="${tier}"]`);
  if (!el) return;
  el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
}

function renderStages() {
  const ol = $("stages");
  ol.replaceChildren();
  STAGES.forEach((def, i) => {
    const st = S.stages[def.id];
    let status = st ? st.status : "pending";
    if (!st && S.outcome) status = "skipped";
    const dot = status === "done" ? "✓" : status === "failed" ? "!" : String(i + 1);
    const chips = [];
    if (def.tier) chips.push(h("span", { class: "chip" + (def.tier in S.tokens ? ` tier-${def.tier}` : "") }, def.tier));
    if (st) {
      for (const [tier, n] of Object.entries(st.calls)) chips.push(h("span", { class: `chip tier-${tier}` }, `${n}× ${tier}`));
      if (st.runs > 1) chips.push(h("span", { class: "chip" }, `attempt ${st.runs}`));
    }
    ol.append(h("li", { class: `stage ${status}` },
      h("span", { class: "dot" }, dot),
      h("div", { class: "name" }, def.label),
      h("div", { class: "desc" }, def.desc),
      chips.length ? h("div", { class: "meta" }, chips) : null,
      st && st.error ? h("div", { class: "err" }, st.error) : null));
  });
}

function renderOutcome() {
  const box = $("outcome");
  if (!S.outcome) { box.className = "outcome hidden"; return; }
  box.className = `outcome o-${S.outcome}`;
  box.replaceChildren(
    h("div", { class: "label" }, "Outcome"),
    h("div", { class: "value" }, S.outcome.replace(/_/g, " ")),
    h("div", { class: "why" }, S.reason || OUTCOME_TEXT[S.outcome] || ""));
}

function renderTabs() {
  const nav = $("tabs");
  nav.replaceChildren();
  for (const key of S.tabs.keys()) {
    const v = S.tabs.get(key);
    const label = TAB_LABELS[key] || TAB_LABELS[v && v.name] || key.replace(/_/g, " ");
    nav.append(h("button", {
      class: "tab" + (key === S.active ? " active" : ""), role: "tab",
      onclick: () => { S.active = key; S.unseen.delete(key); S.follow = false; renderNow(); },
    }, label, S.unseen.has(key) ? h("span", { class: "new", title: "updated" }) : null));
  }
  if (S.tabs.size) {
    nav.append(h("button", {
      class: "tab", title: "Jump to the newest artifact as it arrives",
      onclick: () => { S.follow = !S.follow; renderNow(); },
    }, S.follow ? "● following" : "○ follow"));
  }
}

function renderPane() {
  const pane = $("pane");
  if (!S.active || !S.tabs.has(S.active)) {
    pane.replaceChildren(h("div", { class: "empty" }, h("p", { class: "muted" },
      S.outcome ? "This run produced no artifacts." : "Waiting for the first result…")));
    return;
  }
  const stick = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 40;
  const v = S.tabs.get(S.active);
  pane.replaceChildren(S.active === "terminal" ? viewTerminal() : viewArtifact(v.name, v.content));
  if (S.active === "terminal" && stick) pane.scrollTop = pane.scrollHeight;
}

/* ---- artifact views --------------------------------------------------------------------- */
function card(title, ...kids) { return h("div", { class: "card" }, title ? h("h3", {}, title) : null, ...kids); }
function kv(obj) {
  return h("dl", { class: "kv" }, Object.entries(obj).filter(([, v]) => v != null && v !== "")
    .flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, Array.isArray(v) ? v.join(", ") : asText(v))]));
}
function code(text, cls = "code") { return h("pre", { class: cls }, text ?? ""); }
function diffView(text) {
  const pre = h("pre", { class: "diff" });
  for (const line of String(text || "").split("\n")) {
    const cls = line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff ") ? "file"
      : line.startsWith("@@") ? "hunk" : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : null;
    pre.append(cls ? h("span", { class: cls }, line) : document.createTextNode(line + "\n"));
  }
  return pre;
}
function badge(text, kind) { return h("span", { class: `badge ${kind}` }, text); }

function viewTerminal() {
  const wrap = h("div");
  for (const c of S.commands) {
    const exit = c.timed_out ? h("span", { class: "exit timeout" }, "timed out")
      : h("span", { class: "exit " + (c.exit_code === 0 ? "ok" : "fail") }, `exit ${c.exit_code}`);
    const out = h("pre", { class: "term" });
    if (c.stdout) out.append(c.stdout.endsWith("\n") ? c.stdout : c.stdout + "\n");
    if (c.stderr) out.append(h("span", { class: "stderr" }, c.stderr));
    wrap.append(h("div", { class: "cmd" },
      h("div", { class: "line" }, h("span", { class: "prompt" }, "$"), h("span", { class: "text" }, c.cmd), exit),
      c.stdout || c.stderr ? out : null));
  }
  return wrap;
}

function viewArtifact(name, c) {
  c = c ?? {};
  switch (name) {
    case "issue":
      return card(null, h("h2", {}, c.title || "(untitled issue)"),
        kv({ repo: c.repo, ref: c.ref }),
        c.url ? h("p", {}, h("a", { href: c.url, target: "_blank", rel: "noopener" }, c.url)) : null);

    case "triage": {
      const kind = c.kind || "?";
      return h("div", {},
        card("Classification", badge(kind, kind === "bug" ? "good" : "info"),
          c.claimed_version ? h("span", { class: "muted" }, `  claimed version ${c.claimed_version}`) : null),
        c.steps?.length ? card("Steps", h("ol", {}, c.steps.map((s) => h("li", {}, s)))) : null,
        card("Expected vs actual", kv({ expected: c.expected, actual: c.actual })),
        c.missing_info?.length ? card("Missing information", h("ul", {}, c.missing_info.map((s) => h("li", {}, s)))) : null);
    }

    case "repro_script":
      return h("div", {},
        card("Reproduction script", kv({ path: c.path, "python flags": c.python_flags, attempt: c.attempt })),
        code(c.script ?? asText(c)));

    case "repro_result": {
      const kind = c.classification || "?";
      const tone = kind === "failed" || kind === "hang" ? "good" : kind === "passed" ? "warn" : "bad";
      return h("div", {},
        card("Result", badge(kind, tone), h("span", { class: "muted" }, `  exit code ${c.exit_code}`),
          h("p", { class: "muted" }, "The script is written to FAIL while the bug exists.")),
        code(c.output, "term"));
    }

    case "verdict": {
      const conf = Math.round((c.confidence || 0) * 100);
      return h("div", {},
        card("Does the failure match the issue?",
          badge(c.matches_issue ? "MATCHES" : "DOES NOT MATCH", c.matches_issue ? "good" : "bad"),
          h("div", { class: "muted", style: "margin-top:10px" }, `confidence ${conf}%`),
          h("div", { class: "confbar" }, h("i", { style: `width:${conf}%` }))),
        card("Symptoms", kv({ reported: c.reported_symptom, observed: c.observed_symptom })),
        card("Reasoning (ultra)", h("p", {}, c.reasoning || "")));
    }

    case "fix_locate":
      return card("Files chosen for the patch", code(asText(c)));

    case "fix_plan":
      return h("div", {},
        card(`Patch — attempt ${c.attempt ?? 1} (${c.tier ?? "super"})`, h("p", {}, c.root_cause || "")),
        c.plan_errors?.length ? card("Plan problems", h("ul", {}, c.plan_errors.map((e) => h("li", {}, e)))) : null,
        diffView(c.patch),
        c.test_path ? h("p", { class: "muted" }, `Regression test: ${c.test_path}`) : null);

    case "verify_passed":
      return h("div", {},
        h("div", { class: "proof" },
          h("div", { class: "side" }, h("div", { class: "muted" }, "Test on ORIGINAL code"),
            h("div", { class: "big", style: "color:var(--red)" }, "FAILS"), h("div", { class: "muted" }, `exit ${c.before}`)),
          h("div", { class: "arrow" }, "→"),
          h("div", { class: "side" }, h("div", { class: "muted" }, "Test on PATCHED code"),
            h("div", { class: "big", style: "color:var(--accent)" }, "PASSES"), h("div", { class: "muted" }, `exit ${c.after}`))),
        c.suite ? card("Existing test suite", code(asText(c.suite))) : null);

    case "verify_failed":
      return card("Verification failed", badge("REJECTED", "bad"), h("p", {}, c.reason || ""));

    case "report":
      return h("div", {},
        card(c.generated_by_model === false ? "Comment (plain template)" : "Comment drafted by nano",
          h("h2", {}, c.title || "")),
        h("div", { class: "card md" }, renderMarkdown(c.comment || "")));

    default:
      return card(TAB_LABELS[name] || name, code(asText(c)));
  }
}

/* Minimal, safe markdown for the GitHub comment: headings, fenced code, inline code, paragraphs. */
function renderMarkdown(src) {
  const out = h("div");
  const lines = String(src).split("\n");
  let i = 0, para = [];
  const flush = () => { if (para.length) { out.append(inline(h("p"), para.join(" "))); para = []; } };
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^```(\w*)/);
    if (fence) {
      flush();
      const body = [];
      for (i++; i < lines.length && !lines[i].startsWith("```"); i++) body.push(lines[i]);
      i++;
      out.append(fence[1] === "diff" ? diffView(body.join("\n")) : code(body.join("\n")));
      continue;
    }
    const head = line.match(/^(#{1,6})\s+(.*)/);
    if (head) { flush(); out.append(inline(h("h3"), head[2])); }
    else if (!line.trim()) flush();
    else para.push(line);
    i++;
  }
  flush();
  return out;
}
function inline(el, text) {
  text.split(/(`[^`]+`)/).forEach((part) => {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 1) el.append(h("code", {}, part.slice(1, -1)));
    else if (part) el.append(part);
  });
  return el;
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
    sel.replaceChildren(h("option", { value: "" }, "Previous runs…"));
    for (const r of runs) {
      const name = r.issue_url.replace("https://github.com/", "").replace("/issues/", " #");
      sel.append(h("option", { value: r.id, selected: S && S.runId === r.id }, `${name} — ${r.outcome || r.state}`));
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
  loadHistory();
})();
