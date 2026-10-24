/* ScriptCutter web app. Talks to web/server.py over a small JSON API. */
"use strict";

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- storage
const store = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem("sc_" + key);
      return v === null ? fallback : JSON.parse(v);
    } catch (e) { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem("sc_" + key, JSON.stringify(value)); } catch (e) { /* private mode */ }
  },
};

// ---------------------------------------------------------------- state
const state = {
  source: "resolve",
  footage: "talking_head",
  policy: "balanced",
  filter: "all",
  preview: store.get("preview", "video"),
  editor: store.get("editor", "resolve"),
  view: store.get("view", "hinglish"),
  job: null,
  review: null,
  selected: null,
  providers: {},
};

const STAGES = {
  audio: "Reading audio", levels: "Measuring levels", language: "Detecting language",
  transcribe: "Transcribing", words: "Reading the words in your language", analyse: "Finding retakes", video: "Checking the screen", ai: "AI second opinion",
};
const SEND_LABELS = {
  resolve: "Create timeline in Resolve", premiere: "Save for Premiere Pro", fcp: "Save for Final Cut Pro", edl: "Save EDL",
};
const VIEW_ORDER = ["original", "hinglish", "english"];
const VIEW_LABELS = { hinglish: "Hinglish", english: "English" };
const ORIGINAL_LABELS = { hi: "हिंदी", mr: "मराठी", ne: "नेपाली", bn: "বাংলা", ta: "தமிழ்", te: "తెలుగు", ur: "اردو" };
const FOOTAGE_HINTS = {
  talking_head: "Silences are cut. Retakes are found from the words.",
  screen: "Silent parts where the screen changes stay as brown clips, so on-screen action is never lost.",
};
const SETTINGS_FIELDS = ["track", "filePath", "language", "textMode", "model", "sensitivity", "maxPause", "headPad",
  "tailPad", "topic", "llmUrl", "overlap", "script"];
const SETTINGS_CHECKS = ["markers", "checkerboard", "voiceIsolation"];

// ---------------------------------------------------------------- helpers
const escapeHtml = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const clock = (t) => {
  const m = Math.floor(t / 60);
  return `${String(m).padStart(2, "0")}:${(t - m * 60).toFixed(1).padStart(4, "0")}`;
};
const duration = (s) => (s >= 60 ? `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, "0")}s` : `${Math.round(s)}s`);
const modelLabel = (m) => m.replace(/^ggml-|\.bin$/g, "");
const basename = (p) => p.split(/[\\/]/).pop();

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({ error: "Bad response from the local server" }));
  if (!res.ok || data.error) throw new Error(data.error || res.statusText);
  return data;
}

// a file path adds a "Show file" button that opens Finder / Explorer at it
function toast(message, bad = false, file = "") {
  const t = $("toast");
  t.textContent = message;
  if (file) {
    const b = document.createElement("button");
    b.className = "link-button";
    b.textContent = "Show file";
    b.addEventListener("click", () => api(`/api/job/${state.job}/reveal`, { path: file }).catch((e) => toast(e.message, true)));
    t.append(" ", b);
  }
  t.className = "toast" + (bad ? " is-bad" : "");
  t.hidden = false;
}

function showError(message) { $("error").textContent = message || ""; }

// segmented switches: <div class="segmented"><button data-value=...>
function setSwitch(id, value) {
  [...$(id).children].forEach((b) => b.classList.toggle("is-on", b.dataset.value === value));
}
function onSwitch(id, handler) {
  $(id).addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    setSwitch(id, b.dataset.value);
    handler(b.dataset.value);
  });
}

// ---------------------------------------------------------------- theme
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("themeToggle").textContent = theme === "dark" ? "☀" : "☾";
}
$("themeToggle").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  store.set("theme", next);
  applyTheme(next);
});

// ---------------------------------------------------------------- dialogs
// a click on the dark backdrop closes a dialog, but not the second click of a double-click that opened it
function openDialog(d) {
  if (d.open) return;
  d.showModal();
  d.openedAt = Date.now();
}
function closeOnBackdrop(d) {
  d.addEventListener("click", (e) => { if (e.target === d && Date.now() - (d.openedAt || 0) > 500) d.close(); });
}

// ---------------------------------------------------------------- help
const openHelp = () => openDialog($("help"));
$("helpOpen").addEventListener("click", openHelp);
$("helpClose").addEventListener("click", () => $("help").close());
closeOnBackdrop($("help"));
document.querySelectorAll("[data-help]").forEach((b) => b.addEventListener("click", openHelp));
document.addEventListener("keydown", (e) => {
  if (e.key === "?" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) openHelp();
});

// ---------------------------------------------------------------- settings
function saveSettings() {
  const saved = { source: state.source, footage: state.footage, policy: state.policy, provider: $("provider").value };
  SETTINGS_FIELDS.forEach((id) => { saved[id] = $(id).value; });
  SETTINGS_CHECKS.forEach((id) => { saved[id] = $(id).checked; });
  store.set("settings", saved);
  const p = $("provider").value;
  if (p) {
    store.set("key_" + p, $("apiKey").value);
    store.set("model_" + p, $("llmModel").value);
  }
}

function loadSettings() {
  const s = store.get("settings", null);
  if (!s) return;
  Object.assign(state, { source: s.source || "resolve", footage: s.footage || "talking_head", policy: s.policy || "balanced" });
  SETTINGS_FIELDS.forEach((id) => { if (s[id] !== undefined && s[id] !== null && id !== "model") $(id).value = s[id]; });
  SETTINGS_CHECKS.forEach((id) => { if (s[id] !== undefined) $(id).checked = s[id]; });
  state.savedModel = s.model;
  state.savedProvider = s.provider;
}

function showSource() {
  setSwitch("sourceSwitch", state.source);
  $("sourceResolve").hidden = state.source !== "resolve";
  $("sourceFile").hidden = state.source !== "file";
}
function showFootage() {
  setSwitch("footageSwitch", state.footage);
  $("footageHint").textContent = FOOTAGE_HINTS[state.footage];
}
function showProvider(resetModel) {
  const p = $("provider").value;
  const meta = state.providers[p];
  $("aiFields").hidden = !p;
  $("keyField").hidden = !p || p === "ollama";
  $("urlField").hidden = !(p === "ollama" || p === "openai_compatible");
  if (meta && (resetModel || !$("llmModel").value)) $("llmModel").value = store.get("model_" + p, meta.model);
  if (p) $("apiKey").value = store.get("key_" + p, "");
}

onSwitch("sourceSwitch", (v) => { state.source = v; showSource(); saveSettings(); });
onSwitch("footageSwitch", (v) => { state.footage = v; showFootage(); saveSettings(); });
onSwitch("policySwitch", (v) => { state.policy = v; setSwitch("policySwitchReview", v); saveSettings(); });
$("provider").addEventListener("change", () => { showProvider(true); saveSettings(); });
$("checkerboard").addEventListener("change", () => { $("overlapField").hidden = !$("checkerboard").checked; });
[...SETTINGS_FIELDS, ...SETTINGS_CHECKS, "llmModel", "apiKey"].forEach((id) => $(id).addEventListener("change", saveSettings));
$("filePath").addEventListener("drop", (e) => {
  const t = e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain");
  if (!t) return;
  e.preventDefault();
  $("filePath").value = decodeURIComponent(t.replace(/^file:\/\//, "").trim());
  saveSettings();
});

// ---------------------------------------------------------------- Resolve status
async function refreshStatus(first) {
  try {
    const s = await api("/api/status");
    $("statusDot").classList.toggle("is-on", s.mode === "live" || s.mode === "menu");
    $("statusText").textContent = statusLine(s);
    $("statusText").title = s.mode === "menu"
      ? "Free version of Resolve: re-open ScriptCutter from Workspace > Scripts after switching timelines." : "";
    if (first) fillChoices(s);
  } catch (e) {
    $("statusText").textContent = "Local server not responding";
  }
}

function statusLine(s) {
  if (s.mode === "live") return s.project ? `${s.project} · ${s.timeline || "no timeline open"}` : (s.message || "Resolve is open, no project");
  if (s.mode === "menu") return `${s.timeline} · opened from the Resolve menu`;
  return "Resolve not connected (video files still work)";
}

function fillChoices(s) {
  (s.models || []).forEach((m) => $("model").add(new Option(modelLabel(m), m)));
  if (state.savedModel) $("model").value = state.savedModel;
  state.providers = s.providers || {};
  Object.entries(state.providers).forEach(([key, v]) => $("provider").add(new Option(v.label, key)));
  if (state.savedProvider) $("provider").value = state.savedProvider;
  showProvider(false);
  (s.editors || []).forEach((e) => $("editor").add(new Option(e.label, e.id)));
  $("editor").value = state.editor;
  showEditor();
  if (!s.whisper) showError("whisper-cli is missing. Run the installer again.");
  else if (!(s.models || []).length) showError("No Whisper model found. Run the installer again.");
}

function showEditor() {
  if (!SEND_LABELS[state.editor]) state.editor = "resolve";
  $("sendButton").textContent = SEND_LABELS[state.editor];
}
$("editor").addEventListener("change", () => {
  state.editor = $("editor").value;
  store.set("editor", state.editor);
  showEditor();
});

// ---------------------------------------------------------------- analyse
function collectSettings() {
  const p = $("provider").value;
  return {
    footage: state.footage, policy: state.policy, language: $("language").value, text_mode: $("textMode").value,
    model: $("model").value, sensitivity: +$("sensitivity").value, max_pause: +$("maxPause").value,
    head_pad: +$("headPad").value, tail_pad: +$("tailPad").value,
    llm_provider: p, llm_model: p ? $("llmModel").value : "", llm_api_key: p ? $("apiKey").value : "",
    llm_base_url: $("llmUrl").value, topic: $("topic").value, script: $("script").value,
  };
}

$("analyse").addEventListener("click", async () => {
  showError("");
  saveSettings();
  if (state.source === "file" && !$("filePath").value.trim()) return showError("Enter the path of the video file.");
  $("analyse").disabled = true;
  $("progressCard").hidden = false;
  $("log").textContent = "";
  $("progressBar").style.width = "0%";
  try {
    const r = await api("/api/analyze", {
      source: state.source, path: $("filePath").value, track: +$("track").value || 1, settings: collectSettings(),
    });
    state.job = r.job_id;
    pollJob();
  } catch (e) {
    showError(e.message);
    $("analyse").disabled = false;
  }
});

async function pollJob() {
  try {
    const j = await api(`/api/job/${state.job}`);
    $("progressBar").style.width = `${Math.round(j.progress * 100)}%`;
    $("stage").textContent = j.state === "done" ? "Finished" : j.state === "error" ? "Something went wrong" : `${STAGES[j.stage] || "Working"}…`;
    $("log").textContent = j.logs.join("\n");
    $("log").scrollTop = 1e9;
    if (j.state === "running") return setTimeout(pollJob, 700);
    $("analyse").disabled = false;
    if (j.state === "error") return showError(j.error);
    state.review = await api(`/api/job/${state.job}/review`);
    if (!$("timelineName").value) $("timelineName").value = `${state.review.timeline_name} - AI Cut`;
    state.selected = null;
    render();
  } catch (e) {
    showError(e.message);
    $("analyse").disabled = false;
  }
}

// ---------------------------------------------------------------- review
function render() {
  const R = state.review;
  if (!R) return;
  $("emptyState").hidden = true;
  $("reviewBody").hidden = false;
  $("dock").hidden = false;
  renderSteps(R);
  renderStats(R);
  renderViews(R);
  renderFiles(R);
  watchSteps();
}

// the engine's steps, each with a flag when it needs the creator's eyes
function renderSteps(R) {
  const steps = (R.job.steps || []).filter((s) => s.state !== "skipped");
  const chips = steps.map((s) => {
    const flagged = s.state === "done" && s.flag;
    const icon = flagged ? "⚑" : s.state === "running" ? `${Math.round(s.progress * 100)}%`
      : { done: "✓", failed: "!", waiting: "…" }[s.state] || "";
    const note = flagged ? s.flag : s.state === "failed" ? "did not work" : "";
    return `<button class="step is-${flagged ? "flagged" : s.state}" data-step="${s.id}" title="${escapeHtml(s.error)}">`
      + `<i>${icon}</i>${escapeHtml(s.title)}${note ? `<small>${escapeHtml(note)}</small>` : ""}</button>`;
  });
  chips.push(`<span class="step is-${R.job.approved ? "done" : "waiting"}"><i>${R.job.approved ? "✓" : "→"}</i>Send to your editor</span>`);
  $("steps").innerHTML = chips.join("");
}

$("steps").addEventListener("click", (e) => {
  const b = e.target.closest(".step.is-flagged");
  if (b && b.dataset.step === "cut") {
    state.filter = "review";
    setSwitch("filterSwitch", "review");
    render();
  }
});

// background steps (the Hindi script) keep running after the review opens
let stepTimer = null;
function watchSteps() {
  clearTimeout(stepTimer);
  const busy = (state.review.job.steps || []).some((s) => s.state === "running" || s.state === "waiting");
  if (!busy) return;
  stepTimer = setTimeout(async () => {
    try {
      const j = await api(`/api/job/${state.job}`);
      const changed = j.steps.some((s, i) => s.state !== state.review.job.steps[i].state);
      if (changed) state.review = await api(`/api/job/${state.job}/review`);
      else state.review.job = j;
      render();
    } catch (e) { /* the local server stopped */ }
  }, 1500);
}

async function refreshJob() {
  try {
    state.review.job = await api(`/api/job/${state.job}`);
    render();
  } catch (e) { /* keep the page as it is */ }
}

// ---------------------------------------------------------------- words: हिंदी / Hinglish / English
function availableViews(R) {
  const have = new Set(R.files.flatMap((f) => f.views || []));
  return VIEW_ORDER.filter((v) => have.has(v));
}
function currentView(R) {
  const views = availableViews(R);
  return views.includes(state.view) ? state.view : views[0] || "english";
}
function viewButtons(R) {
  const lang = (R.files[0] || {}).language;
  return availableViews(R).map((v) =>
    `<button data-value="${v}">${escapeHtml(v === "original" ? ORIGINAL_LABELS[lang] || "Original" : VIEW_LABELS[v])}</button>`).join("");
}
function renderViews(R) {
  $("viewSwitch").hidden = availableViews(R).length < 2;
  $("viewSwitch").innerHTML = viewButtons(R);
  setSwitch("viewSwitch", currentView(R));
}
function rowText(d, view) {
  if (view === "original" && d.orig !== undefined) return d.orig || d.text;
  if (view === "hinglish" && d.hinglish !== undefined) return d.hinglish || d.text;
  return d.text;
}
function chooseView(v) {
  state.view = v;
  store.set("view", v);
  render();
}
onSwitch("viewSwitch", chooseView);

function renderStats(R) {
  const st = R.job.stats;
  $("stats").innerHTML = `
    <div class="stat"><b>${duration(st.source_seconds)} → ${duration(st.kept_seconds)}</b><span>raw → cut</span></div>
    <div class="stat"><b>${st.removed_takes}</b><span>takes and noises cut</span></div>
    <div class="stat is-review"><b>${st.review_clips}</b><span>brown clips (${duration(st.review_seconds)})</span></div>
    <div class="stat"><b>${st.clips}</b><span>clips on the new timeline</span></div>`;
  const S = R.script || {};
  if (S.lines) {
    const p = document.createElement("div");
    p.className = "learned";
    const missing = (S.missing || []).slice(0, 3).map((m) => `line ${m.n} "${escapeHtml(m.text.slice(0, 50))}"`).join(", ");
    p.innerHTML = `Script: <b>${S.found} of ${S.lines}</b> lines found in the recording`
      + (S.weak ? ` · ${S.weak} unsure (brown)` : "")
      + (missing ? ` · Not found: ${missing}${S.missing.length > 3 ? ` and ${S.missing.length - 3} more` : ""}` : "");
    $("stats").appendChild(p);
  }
  const learned = (R.learned || []).filter((x) => x.kept + x.cut > 0);
  if (learned.length) {
    const p = document.createElement("div");
    p.className = "learned";
    p.innerHTML = "Learned from you: " + learned.map((x) =>
      `<b>${escapeHtml(x.label)}</b> ${x.shift > 0 ? "cut more" : x.shift < 0 ? "flag less" : "still learning"} (${x.cut} cut / ${x.kept} kept)`).join(" · ");
    $("stats").appendChild(p);
  }
  $("dockSummary").innerHTML = `<b>${duration(st.kept_seconds)}</b> final · ${duration(st.source_seconds - st.kept_seconds)} removed · ${st.review_clips} to check`;
}

function renderFiles(R) {
  const holder = $("files");
  holder.innerHTML = "";
  R.files.forEach((f) => {
    const head = document.createElement("div");
    head.className = "file-head";
    const ai = f.llm ? ` · AI: ${f.llm.notes} notes${f.llm.errors.length ? `, ${f.llm.errors.length} failed` : ""}` : "";
    head.innerHTML = `<b>${escapeHtml(f.name)}</b><span>${f.language}${f.translated ? " · via English" : ""} · ${escapeHtml(modelLabel(f.model))}${ai}</span>`;
    holder.appendChild(head);

    const rows = document.createElement("div");
    rows.className = "rows";
    const byId = Object.fromEntries(f.decisions.map((d) => [d.id, d]));
    const view = currentView(R);
    f.decisions
      .filter((d) => state.filter === "all" || d.action === state.filter)
      .forEach((d) => rows.appendChild(renderRow(d, f.path, byId, view)));
    if (!rows.children.length) rows.innerHTML = `<div class="empty">Nothing in this filter.</div>`;
    holder.appendChild(rows);
  });
}

function renderRow(d, path, byId, view) {
  const row = document.createElement("div");
  row.className = `row ${d.action}${state.selected === d.id ? " is-selected" : ""}`;
  row.dataset.id = d.id;
  row.dataset.path = path;
  const tag = d.kind === "phrase" ? "" : `<span class="tag">${escapeHtml(d.kind)}</span>`;
  const conf = d.confidence > 0
    ? `<span class="meter"><i style="width:${Math.round(d.confidence * 100)}%"></i></span>${Math.round(d.confidence * 100)}% likely a retake`
    : "";
  const ref = d.ref && byId[d.ref] ? ` · <a data-play="${d.ref}">hear the later take</a>` : "";
  const mine = d.user_action ? ` · <span class="by-you">set by you</span>` : "";
  const why = d.reason || conf
    ? `<div class="row-why">${escapeHtml(d.reason)}${d.reason && conf ? " · " : ""}${conf}${ref}${mine}</div>` : "";
  const choice = ["keep", "review", "remove"].map((a) =>
    `<button class="${a}${d.action === a ? " is-on" : ""}" data-action="${a}">${{ keep: "Keep", review: "Review", remove: "Cut" }[a]}</button>`).join("");
  row.innerHTML = `
    <button class="play" data-play="${d.id}" title="Play">▶</button>
    <div class="row-time">${clock(d.start)}</div>
    <div><div class="row-text">${tag}${rowText(d, view) ? escapeHtml(rowText(d, view)) : "<i>(no words)</i>"}</div>${why}</div>
    <div class="choice">${choice}</div>`;
  return row;
}

const findDecision = (path, id) => {
  const f = state.review.files.find((x) => x.path === path);
  return f && f.decisions.find((d) => d.id === id);
};

async function setDecision(path, id, action) {
  try {
    state.review = await api(`/api/job/${state.job}/decision`, { path, id, action: action === "auto" ? null : action });
    render();
  } catch (e) { toast("Could not update: " + e.message, true); }
}

async function changeCut(body) {
  if (!state.job || !state.review) return;
  try {
    state.review = await api(`/api/job/${state.job}/settings`, body);
    render();
  } catch (e) { toast("Could not update: " + e.message, true); }
}

onSwitch("filterSwitch", (v) => { state.filter = v; render(); });
onSwitch("policySwitchReview", (v) => {
  state.policy = v;
  setSwitch("policySwitch", v);
  saveSettings();
  changeCut({ policy: v });
});
["maxPause", "headPad", "tailPad"].forEach((id) => $(id).addEventListener("change", () =>
  changeCut({ max_pause: +$("maxPause").value, head_pad: +$("headPad").value, tail_pad: +$("tailPad").value })));
document.querySelectorAll("[data-bulk]").forEach((b) => b.addEventListener("click", async () => {
  if (!state.job) return;
  try {
    state.review = await api(`/api/job/${state.job}/bulk`, { from: "review", action: b.dataset.bulk });
    render();
  } catch (e) { toast("Could not update: " + e.message, true); }
}));

$("files").addEventListener("click", (e) => {
  const row = e.target.closest(".row");
  if (!row) return;
  select(row);
  const p = e.target.closest("[data-play]");
  if (p) return play(row.dataset.path, p.dataset.play);
  const b = e.target.closest("[data-action]");
  if (b) setDecision(row.dataset.path, row.dataset.id, b.dataset.action);
});

function select(row) {
  state.selected = row.dataset.id;
  document.querySelectorAll(".row.is-selected").forEach((r) => r.classList.remove("is-selected"));
  row.classList.add("is-selected");
}

document.addEventListener("keydown", (e) => {
  if (!state.review || $("help").open || $("transcript").open
    || ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
  const rows = [...document.querySelectorAll(".row")];
  if (!rows.length) return;
  let i = rows.findIndex((r) => r.dataset.id === state.selected);
  const current = rows[i];
  if (["ArrowDown", "ArrowUp", "j", "k"].includes(e.key)) {
    e.preventDefault();
    i = Math.max(0, Math.min(rows.length - 1, i + (e.key === "ArrowDown" || e.key === "j" ? 1 : -1)));
    select(rows[i]);
    rows[i].scrollIntoView({ block: "nearest" });
  } else if (current && e.key === " ") {
    e.preventDefault();
    play(current.dataset.path, current.dataset.id);
  } else if (current && "1230".includes(e.key)) {
    setDecision(current.dataset.path, current.dataset.id, { 1: "keep", 2: "review", 3: "remove", 0: "auto" }[e.key]);
  }
});

// ---------------------------------------------------------------- playback
function play(path, id) {
  const d = findDecision(path, id);
  if (!d) return;
  const file = state.review.files.find((x) => x.path === path);
  const from = Math.max(0, d.start - 0.15);
  const to = d.end + 0.2;
  const video = $("video");
  const audio = $("audio");
  video.pause();
  audio.pause();
  if (state.preview === "video" && file && file.has_video) {
    const src = `/api/video?job=${state.job}&path=${encodeURIComponent(path)}`;
    if (video.dataset.src !== src) { video.src = src; video.dataset.src = src; }
    $("preview").hidden = false;
    video.dataset.stop = to;
    const start = () => { video.currentTime = from; video.play().catch(() => {}); };
    if (video.readyState >= 1) start(); else video.addEventListener("loadedmetadata", start, { once: true });
  } else {
    audio.src = `/api/audio?job=${state.job}&path=${encodeURIComponent(path)}&start=${from}&end=${to}`;
    audio.play().catch(() => {});
  }
  document.querySelectorAll(".play.is-playing").forEach((b) => b.classList.remove("is-playing"));
  const btn = document.querySelector(`.row[data-id="${id}"] .play`);
  if (btn) btn.classList.add("is-playing");
}

$("video").addEventListener("timeupdate", () => {
  const v = $("video");
  if (v.dataset.stop && v.currentTime >= +v.dataset.stop) v.pause();
});
$("video").addEventListener("error", () => {
  if (!$("video").dataset.src) return;
  state.preview = "audio";
  setSwitch("previewSwitch", "audio");
  $("preview").hidden = true;
  toast("This video format can't play in the browser, so audio preview is used instead.", true);
});
onSwitch("previewSwitch", (v) => {
  state.preview = v;
  store.set("preview", v);
  if (v === "audio") { $("video").pause(); $("preview").hidden = true; }
});

// ---------------------------------------------------------------- output
function buildMessage(r) {
  if (!r.ok) return [`Resolve did not create the timeline: ${r.error || "unknown error"}`, true];
  let msg = `Created "${r.timeline}": ${r.items} clips, ${r.review_markers} review markers.`;
  if (r.missing_media) msg += ` ${r.missing_media} clips were skipped because their media is offline.`;
  if (r.voice_isolation === false) msg += " Voice Isolation needs DaVinci Resolve Studio.";
  if (r.learned_from) msg += ` Learned from ${r.learned_from} of your choices.`;
  return [msg, false];
}

// free version of Resolve: wait until the user runs the Build menu entry inside Resolve
async function waitForMenuBuild(started) {
  try {
    const r = await api(`/api/job/${state.job}/build`);
    if (r.pending) {
      if (Date.now() - started < 30 * 60 * 1000) return setTimeout(() => waitForMenuBuild(started), 1500);
      toast("Still waiting for Resolve. Press Create timeline again when you are ready.", true);
    } else {
      toast(...buildMessage(r));
    }
  } catch (e) { toast("Lost contact with ScriptCutter: " + e.message, true); }
  $("sendButton").disabled = false;
  refreshJob();
}

async function buildInResolve() {
  $("sendButton").disabled = true;
  toast("Creating the timeline in Resolve…");
  try {
    const r = await api(`/api/job/${state.job}/build`, {
      name: $("timelineName").value, checkerboard: $("checkerboard").checked, overlap_frames: +$("overlap").value || 3,
      voice_isolation: $("voiceIsolation").checked, markers: $("markers").checked,
    });
    if (r.pending) {
      toast("Almost done: in DaVinci Resolve choose Workspace → Scripts → ScriptCutter - Build Timeline.");
      return waitForMenuBuild(Date.now());
    }
    toast(...buildMessage(r));
  } catch (e) {
    toast("Could not create the timeline: " + e.message, true);
  }
  $("sendButton").disabled = false;
  refreshJob();
}

// Premiere Pro / Final Cut Pro / EDL: a file next to the video
async function saveFor(format) {
  try {
    const r = await api(`/api/job/${state.job}/export`, { format, name: $("timelineName").value, view: currentView(state.review) });
    toast(`Saved "${basename(r.files[0])}" next to your video. ${r.how}`, false, r.files[0]);
    refreshJob();
  } catch (e) { toast("Could not save: " + e.message, true); }
}

$("sendButton").addEventListener("click", () => {
  if (!state.job) return;
  if (state.editor === "resolve") buildInResolve(); else saveFor(state.editor);
});

// ---------------------------------------------------------------- transcript of the final cut
async function loadTranscript() {
  const R = state.review;
  $("transcriptView").innerHTML = availableViews(R).length > 1 ? viewButtons(R) : "";
  setSwitch("transcriptView", currentView(R));
  try {
    const r = await api(`/api/job/${state.job}/transcript?view=${currentView(R)}`);
    $("transcriptText").textContent = r.text || "There are no words in the final cut.";
    $("transcriptNote").textContent = r.pending
      ? "The Hindi words are still being written. This shows what is ready; open it again in a moment."
      : `Final cut: ${duration(r.seconds)}. Updates when you change Keep or Cut.`;
  } catch (e) { $("transcriptText").textContent = e.message; }
}
$("transcriptButton").addEventListener("click", () => {
  if (!state.job) return;
  $("transcriptText").textContent = "Loading…";
  openDialog($("transcript"));
  loadTranscript();
});
$("transcriptClose").addEventListener("click", () => $("transcript").close());
closeOnBackdrop($("transcript"));
onSwitch("transcriptView", (v) => { chooseView(v); loadTranscript(); });
$("transcriptCopy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("transcriptText").textContent);
    $("transcriptNote").textContent = "Copied. Paste it anywhere.";
  } catch (e) { $("transcriptNote").textContent = "Copy is blocked here: select the text and press Ctrl/⌘ + C."; }
});
$("transcriptSave").addEventListener("click", async () => {
  try {
    const r = await api(`/api/job/${state.job}/export`, { format: "transcript", name: $("timelineName").value, view: currentView(state.review) });
    $("transcript").close();
    toast(`Saved the transcript, subtitles and edit data next to your video. ${r.how}`, false, r.files[0]);
    refreshJob();
  } catch (e) { $("transcriptNote").textContent = "Could not save: " + e.message; }
});

// ---------------------------------------------------------------- start
applyTheme(store.get("theme", "light"));
loadSettings();
showSource();
showFootage();
setSwitch("policySwitch", state.policy);
setSwitch("policySwitchReview", state.policy);
setSwitch("previewSwitch", state.preview);
$("overlapField").hidden = !$("checkerboard").checked;
refreshStatus(true);
setInterval(() => { if (!document.hidden) refreshStatus(false); }, 6000);
