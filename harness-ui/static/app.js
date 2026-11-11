// harness-ui front end: plain JS, no build step.
"use strict";

const $ = (id) => document.getElementById(id);
const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d));
const pct = (v) => (v === null || v === undefined ? "—" : Math.round(v * 100) + "%");

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || (opts.body ? "POST" : "GET"),
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({ error: "bad response (" + res.status + ")" }));
  if (!res.ok || data.error) throw new Error(data.error || "HTTP " + res.status);
  return data;
}

let toastTimer;
function toast(msg, err = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (err ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), err ? 8000 : 3500);
}

function dl(el, rows) {
  el.replaceChildren();
  for (const [k, v] of rows) {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v;
    el.append(dt, dd);
  }
}

function duration(s) {
  if (!s && s !== 0) return "—";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min ${Math.floor(s % 60)} s`;
}

// ---------------- tabs ----------------
let activeTab = "chat";  // the app opens on the Chat tab
document.querySelectorAll("#tabs button").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
    activeTab = b.dataset.tab;
    if (activeTab === "logs") loadLogs();
  })
);

// ---------------- state ----------------
let profiles = [];
let st = null;
let busy = false;
let httpHost = "127.0.0.1";

const ENGINES = { llama: "llama.cpp", strata: "Strata" };
const engineName = (e) => ENGINES[e] || e || "llama.cpp";

async function loadConfig() {
  const c = await api("/api/config");
  $("hdr-conn").textContent = c.host ? "host: " + c.host : "host: this machine";
  $("cfg-host").value = c.host || "";
  $("cfg-home").value = c.remote_home || "";
  $("cfg-strata").value = c.strata_home || "";
  httpHost = c.http_host || "127.0.0.1";
}

// One list for both engines, grouped: the llama.cpp profiles from the host, the Strata ones bundled with the app.
async function loadProfiles() {
  const data = await api("/api/profiles");
  profiles = data.profiles;
  for (const sel of [$("ctl-profile"), $("set-profile")]) {
    const keep = sel.value;
    sel.replaceChildren();
    for (const eng of Object.keys(ENGINES)) {
      const group = profiles.filter((p) => (p.engine || "llama") === eng);
      if (!group.length) continue;
      const og = document.createElement("optgroup");
      og.label = ENGINES[eng];
      for (const p of group) og.append(new Option(`${p.name} — ${p.description}`, p.name));
      sel.append(og);
    }
    if (keep && profiles.some((p) => p.name === keep)) sel.value = keep;
    else if (profiles.length) sel.value = runningProfile() || profiles[0].name;
  }
  const want = runningProfile();
  if (want && !$("ctl-profile").dataset.touched) $("ctl-profile").value = want;
  if (want && !$("set-profile").dataset.touched) $("set-profile").value = want;
  renderSettings();
}

// The running profile when it is in the list; else the first profile of the running engine (a Strata started
// outside the app reports "external"); else nothing.
function runningProfile() {
  const b = st && st.backend;
  if (!b) return null;
  if (profiles.some((p) => p.name === b.profile)) return b.profile;
  const same = profiles.find((p) => (p.engine || "llama") === (b.engine || "llama"));
  return same ? same.name : null;
}

// ---------------- dashboard ----------------
async function refreshStatus() {
  try {
    st = await api("/api/status");
  } catch (e) {
    $("hdr-dot").className = "dot bad";
    $("d-state").textContent = "unreachable";
    $("ctl-msg").textContent = e.message;
    return;
  }
  const b = st.backend, m = st.memory;
  $("ctl-msg").textContent = "";
  if (b) {
    const ok = b.health === "ok", strata = b.engine === "strata";
    $("hdr-dot").className = "dot " + (ok ? "ok" : "warn");
    $("hdr-model").textContent = `${engineName(b.engine)} · ${b.profile} · ${b.busy_slots ? b.busy_slots + " busy" : "idle"}`;
    $("d-state").textContent = ok ? (b.busy_slots ? "Generating" : "Running") : "Loading…";
    const f = b.flags || {};
    const rows = [
      ["Engine", engineName(b.engine) + (b.version ? " " + b.version : "")],
      ["Profile", b.profile + (b.profile === "external" ? " (started outside the app)" : "")],
      ["PID", b.pid], ["Uptime", duration(b.uptime_s)],
      ["Endpoint", `http://${httpHost}:${b.port}/v1`],
      ["Slots", `${b.busy_slots}/${b.slots} busy`],
      ["Context", f.ctx ? Number(f.ctx).toLocaleString() + " tokens" : "—"],
    ];
    if (strata) {
      rows.push(
        ["Weights", f.weights === "resident" ? "resident on GPU" : f.weights === "streaming" ? "experts streamed from SSD" : "engine decides"],
        ["MTP", f.mtp ? `on (spec window ${f.spec || "?"})` : "off"],
        ["Prompt-lookup draft", f.suffix_draft === "0" ? "off" : f.suffix_draft === "default" ? "engine default" : f.suffix_draft],
        ["KV cache", f.kv || "—"], ["Conversation cache", f.conv_cache ? f.conv_cache + " MiB" : "default"],
        ["API key", f.auth ? "required" : "none"],
      );
    } else {
      rows.push(
        ["MTP", f.mtp ? `on (draft ${f.draft_n_max || 1})` : "off"],
        ["Thinking", (f.effort || "xhigh") + (f.reasoning_budget && f.reasoning_budget !== "-1" ? `, budget ${f.reasoning_budget}` : "")],
        ["KV cache", f.cache_type || "—"], ["Prompt cache", f.cache_ram ? f.cache_ram + " MiB" : "default"],
        ["API key", f.auth ? "required" : "none"],
      );
    }
    dl($("d-backend"), rows);
    const s = b.stats || {}, last = s.last || {};
    const drafting = strata ? (f.mtp || f.suffix_draft !== "0") : f.mtp;
    $("d-acc-label").textContent = strata ? "draft acceptance" : "MTP acceptance";
    $("d-tps").textContent = fmt(s.decode_tps);
    $("d-acc").textContent = drafting ? pct(s.accept) : "off";
    dl($("d-perf"), [
      ["Requests", s.requests ?? "—"], ["Tokens generated", (s.generated ?? 0).toLocaleString()],
      ["Last reply", last.tokens ? `${last.tokens} tok at ${fmt(last.decode_tps)} tok/s` : "—"],
      ["Last prompt", last.prompt_tokens ? `${last.prompt_tokens} new tok at ${fmt(last.prefill_tps, 0)} tok/s` : "—"],
      ["Last draft accept", drafting ? pct(last.accept) : "—"],
    ]);
    if (b.settings_changed) {
      showBanner("Settings for the running profile changed since it started. Restart to apply.");
    } else if (strata && st.desired && st.desired.running && st.watchdog.installed) {
      showBanner("The harness watchdog still wants llama.cpp running and could start it beside Strata (out of memory).",
        "Keep llama.cpp stopped", async () => { try { await api("/api/release", { body: {} }); toast("llama.cpp released."); } catch (e) { toast(e.message, true); } refreshStatus(); });
    } else showBanner(null);
  } else {
    $("hdr-dot").className = "dot bad";
    $("hdr-model").textContent = "stopped";
    $("d-state").textContent = st.desired && st.desired.running ? "Stopped (watchdog will restart llama.cpp)" : "Stopped";
    dl($("d-backend"), [
      ["llama.cpp", st.build.llama_server ? "llama-server built" : "not built — run: harness build"],
      ["Strata", st.build.strata ? "engine built in " + st.build.strata_home : "not found in " + st.build.strata_home + " (see Settings → Connection)"],
    ]);
    $("d-tps").textContent = "—"; $("d-acc").textContent = "—"; dl($("d-perf"), []);
    if (!$("cfg-host").value && !st.build.llama_server && !st.build.strata) {
      showBanner("No inference host is set and this Mac has no backend build. Enter the host in Settings → Connection.",
        "Open Connection", () => { openTab("settings"); $("cfg-host").focus(); });
    } else showBanner(null);
  }
  const total = m.total || 64;
  $("d-mem-wired").style.width = (m.wired / total) * 100 + "%";
  $("d-mem-active").style.width = (m.active / total) * 100 + "%";
  $("d-mem-comp").style.width = (m.compressed / total) * 100 + "%";
  dl($("d-mem"), [
    ["Wired (GPU)", fmt(m.wired) + " GB"], ["Available", fmt(m.available) + " GB"],
    ["Compressed", fmt(m.compressed) + " GB"], ["Swap used", fmt(m.swap_mb, 0) + " MB"],
    ["GPU wired limit", m.wired_limit_mb ? m.wired_limit_mb + " MB" : "macOS default"], ["Total", fmt(total, 0) + " GB"],
  ]);
  const w = st.watchdog;
  $("d-wd").textContent = w.installed ? "Installed" : "Not installed";
  $("btn-wd-install").disabled = w.installed || busy;
  $("btn-wd-remove").disabled = !w.installed || busy;
  $("btn-start").disabled = !!b || busy;
  $("btn-stop").disabled = !b || busy;
  $("btn-restart").disabled = busy;
}

function showBanner(text, label = "Restart now", action = () => control("restart", st.backend.profile)) {
  const el = $("banner");
  if (!text) return el.classList.add("hidden");
  el.replaceChildren(document.createTextNode(text));
  const btn = document.createElement("button");
  btn.textContent = label;
  btn.onclick = action;
  el.append(btn);
  el.classList.remove("hidden");
}

function openTab(name) {
  document.querySelector(`#tabs [data-tab="${name}"]`).click();
}

async function control(action, profile) {
  busy = true;
  refreshButtons();
  const name = profile || $("ctl-profile").value;
  $("ctl-msg").textContent = action === "stop" ? "Stopping…" : `${action === "restart" ? "Restarting" : "Starting"} ${name}…`;
  try {
    const body = action === "stop" ? {} : { profile: name, force: $("ctl-force").checked };
    const r = await api("/api/" + action, { body });
    if (r.started === false) {
      showPreflight(r.preflight);
      toast("Preflight failed — see the results, or tick “Skip preflight”.", true);
    } else if (action !== "stop") {
      toast(r.engine === "strata" ? `Started ${name} (pid ${r.pid}). Strata loads the model in about a minute (longer after a reboot).`
        : `Started ${name} (pid ${r.pid}). Loading the model takes 5–30 s.`);
      await waitHealthy();
    } else {
      toast("Backend stopped.");
    }
  } catch (e) {
    toast(e.message, true);
  } finally {
    busy = false;
    $("ctl-msg").textContent = "";
    refreshStatus();
  }
}

async function waitHealthy() {
  for (let i = 0; i < 300; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    await refreshStatus();
    if (st && st.backend && st.backend.health === "ok") return toast("Backend ready.");
    if (st && !st.backend && i > 2) return toast("Backend exited while loading — check Logs.", true);
  }
}

function refreshButtons() {
  for (const id of ["btn-start", "btn-stop", "btn-restart", "btn-preflight", "btn-wd-install", "btn-wd-remove"]) $(id).disabled = busy;
}

function showPreflight(results) {
  const t = $("preflight-table");
  t.replaceChildren();
  for (const [st, name, detail] of results) {
    const tr = t.insertRow();
    const a = tr.insertCell(); a.textContent = { PASS: "✓", WARN: "!", FAIL: "✗" }[st] || st; a.className = "st-" + st;
    tr.insertCell().textContent = name;
    tr.insertCell().textContent = detail;
  }
  $("preflight-card").classList.remove("hidden");
}

$("btn-start").onclick = () => control("start");
$("btn-stop").onclick = () => control("stop");
$("btn-restart").onclick = () => control("restart");
$("ctl-profile").onchange = () => ($("ctl-profile").dataset.touched = "1");
$("btn-preflight").onclick = async () => {
  $("btn-preflight").disabled = true;
  try {
    const r = await api("/api/preflight", { body: { profile: $("ctl-profile").value } });
    showPreflight(r.results);
  } catch (e) { toast(e.message, true); }
  $("btn-preflight").disabled = false;
};
for (const [id, action] of [["btn-wd-install", "install"], ["btn-wd-remove", "uninstall"]]) {
  $(id).onclick = async () => {
    try { await api("/api/agent", { body: { action } }); toast(action === "install" ? "Watchdog installed." : "Watchdog removed."); }
    catch (e) { toast(e.message, true); }
    refreshStatus();
  };
}

// ---------------- settings ----------------
const ORDER = {
  llama: ["CTX", "PARALLEL", "MTP_ENABLED", "DRAFT_N_MAX", "REASONING_EFFORT", "REASONING_BUDGET", "CACHE_TYPE", "CACHE_RAM_MB", "GPU_BUDGET_GB",
    "THREADS", "PORT", "HOST", "API_KEY", "EXTRA_ARGS"],
  strata: ["CTX", "PARALLEL", "STREAM", "MTP", "SPEC", "BATCH_MTP", "SUFFIX_DRAFT", "KV_TYPE", "CONV_CACHE_MIB", "GPU_BUDGET_GB",
    "PORT", "HOST", "STRATA_API_KEY", "EXTRA_ARGS"],
};
const WHERE = {
  llama: "Changes are saved on the inference host as per-machine overrides (inference/profiles/<profile>.local.env, not committed). Restart the backend to apply them.",
  strata: "Changes are saved on this Mac (strata-overrides.json next to the connection settings) and passed to the host's scripts/strata-start.sh as its environment knobs. Restart the backend to apply them.",
};

function renderSettings() {
  const p = profiles.find((x) => x.name === $("set-profile").value) || profiles[0];
  if (!p) return;
  const eng = p.engine || "llama";
  $("set-profile").value = p.name;
  $("set-desc").textContent = `${engineName(eng)} · ${p.model || ""}${p.alias ? " · model alias " + p.alias : ""}`;
  $("set-where").textContent = WHERE[eng] || WHERE.llama;
  const form = $("set-form");
  form.replaceChildren();
  form.className = "form";
  for (const key of ORDER[eng] || ORDER.llama) {
    const s = p.settings[key];
    if (!s) continue;
    if ((key === "MTP_ENABLED" || key === "DRAFT_N_MAX") && !p.mtp_available) continue;
    const box = document.createElement("div");
    box.className = "field" + (s.overridden ? " changed" : "");
    const lbl = document.createElement("div"); lbl.className = "lbl";
    lbl.append(document.createTextNode(s.label));
    if (s.overridden) { const bd = document.createElement("span"); bd.className = "badge"; bd.textContent = "custom"; lbl.append(bd); }
    let input;
    if (s.type === "bool") {
      input = document.createElement("input"); input.type = "checkbox"; input.checked = !!s.value;
    } else if (s.choices) {
      input = document.createElement("select");
      for (const o of s.choices) input.add(new Option(key === "CTX" ? Number(o).toLocaleString() : o === "" ? "engine default" : o, o));
      input.value = String(s.value);
    } else {
      input = document.createElement("input");
      input.type = s.type === "secret" ? "password" : (s.type === "int" || s.type === "float") ? "number" : "text";
      if (s.range) { input.min = s.range[0]; input.max = s.range[1]; }
      if (s.type === "float") input.step = "0.5";
      input.value = s.value ?? "";
      if (s.type === "secret") input.placeholder = "not set";
    }
    input.name = key;
    input.addEventListener("input", () => box.classList.add("changed"));
    const help = document.createElement("div"); help.className = "help"; help.textContent = s.help;
    const def = document.createElement("div"); def.className = "def";
    def.textContent = s.type === "secret" ? "" : `profile default: ${s.type === "bool" ? (s.default ? "on" : "off") : (s.default === "" ? (s.choices ? "engine default" : "(empty)") : s.default)}`;
    box.append(lbl, input, help, def);
    form.append(box);
  }
}

$("set-profile").onchange = () => { $("set-profile").dataset.touched = "1"; renderSettings(); };
$("set-form").onsubmit = (e) => e.preventDefault();
$("set-save").onclick = async () => {
  const name = $("set-profile").value;
  const values = {};
  for (const el of $("set-form").elements) {
    if (!el.name) continue;
    values[el.name] = el.type === "checkbox" ? el.checked : el.value;
  }
  try {
    const view = await api("/api/profiles/" + encodeURIComponent(name), { body: values });
    profiles = profiles.map((p) => (p.name === name ? view : p));
    renderSettings();
    const running = st && st.backend && st.backend.profile === name;
    $("set-msg").textContent = running ? "Saved. Restart the backend to apply." : "Saved. Applies the next time this profile starts.";
    toast("Settings saved for " + name);
    refreshStatus();
  } catch (e) { toast(e.message, true); }
};
$("set-reset").onclick = async () => {
  const name = $("set-profile").value;
  if (!confirm(`Remove all custom settings for “${name}” on the host?`)) return;
  try {
    const view = await api("/api/profiles/" + encodeURIComponent(name), { method: "DELETE" });
    profiles = profiles.map((p) => (p.name === name ? view : p));
    renderSettings();
    $("set-msg").textContent = "Reset to the profile's defaults.";
  } catch (e) { toast(e.message, true); }
};
$("cfg-save").onclick = async () => {
  try {
    await api("/api/config", { body: { host: $("cfg-host").value, remote_home: $("cfg-home").value, strata_home: $("cfg-strata").value } });
    await loadConfig();
    $("cfg-msg").textContent = "Saved. Reconnecting…";
    await refreshStatus();
    await loadProfiles();
    $("cfg-msg").textContent = "Connected.";
  } catch (e) { $("cfg-msg").textContent = e.message; }
};

// ---------------- chat ----------------
const PRESETS = {
  think: { temp: 1.0, topp: 0.95, topk: 20, pres: 0.0, max: 4096 },
  nothink: { temp: 0.7, topp: 0.8, topk: 20, pres: 1.5, max: 2048 },
};
const PARAMS = [["temp", "o-temp", 2], ["topp", "o-topp", 2], ["topk", "o-topk", 0], ["pres", "o-pres", 1], ["max", "o-max", 0]];
function applyPreset() {
  const p = PRESETS[$("p-effort").value === "off" ? "nothink" : "think"];
  for (const [k] of PARAMS) $("p-" + k).value = p[k];
  showParams();
}
function showParams() { for (const [k, o, d] of PARAMS) $(o).textContent = Number($("p-" + k).value).toFixed(d); }
let lastThinking = true;
$("p-effort").onchange = () => {
  const on = $("p-effort").value !== "off";
  if (on !== lastThinking) applyPreset();
  lastThinking = on;
};
for (const [k] of PARAMS) $("p-" + k).oninput = showParams;
applyPreset();

// The open chat. Messages keep what is needed to redraw them; only role/content go to the model.
let chatHistory = [];
let chatId = null;
let chatFolder = "";   // absolute path the model may read in the open chat ("" for none), saved with the chat
// The reply being generated, if any: {id, controller, div, history, folder}. It keeps running (and keeps updating its own
// detached message element) while you look at another chat; opening its chat again re-attaches that element.
let gen = null;

const newChatId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8);

function addMsg(role, text, error) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  const body = document.createElement("div");
  div.append(body);
  setBody(body, role, text, error);
  $("chat-log").append(div);
  return { div, body };
}

// Follow new text only while you are at the bottom; scrolling up stops it, scrolling back down resumes it.
let stick = true;
const nearBottom = (el) => el.scrollHeight - el.scrollTop - el.clientHeight < 60;
$("chat-log").addEventListener("scroll", () => { stick = nearBottom($("chat-log")); });
function scrollLog(force) {
  const log = $("chat-log");
  if (force) stick = true;
  if (stick) log.scrollTop = log.scrollHeight;
}

// Assistant replies are Markdown (markdown.js); what the user typed stays plain text.
function setBody(body, role, text, error, streaming) {
  if (role === "assistant") {
    body.className = "md";
    Markdown.into(body, text, streaming);
    const old = body.querySelector(":scope > .err"); if (old) old.remove();
    if (error) { const e = document.createElement("div"); e.className = "err"; e.textContent = "[error] " + error; body.append(e); }
  } else {
    body.textContent = text;
  }
}

// Streaming replies re-render at most once per frame.
let pendingRender = null;
function renderLater(body, text) {
  const first = !pendingRender;
  pendingRender = { body, text };
  if (first) requestAnimationFrame(() => {
    const p = pendingRender; pendingRender = null;
    if (!p) return;
    setBody(p.body, "assistant", p.text, null, true);
    if (gen && gen.id === chatId) scrollLog();
  });
}

// Copy buttons on code blocks; images that fail to load fall back to their link.
$("chat-log").addEventListener("click", async (e) => {
  const btn = e.target.closest(".md-copy");
  if (!btn) return;
  const code = btn.closest(".md-code").querySelector("pre").textContent;
  try { await navigator.clipboard.writeText(code); } catch (_) {
    const ta = document.createElement("textarea"); ta.value = code; document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove();
  }
  btn.textContent = "Copied"; setTimeout(() => { btn.textContent = "Copy"; }, 1500);
});
$("chat-log").addEventListener("error", (e) => {
  const img = e.target;
  if (!(img instanceof HTMLImageElement) || !img.closest(".md-img")) return;
  const a = img.closest(".md-img");
  a.classList.add("missing"); a.textContent = "Image unavailable: " + (img.alt || a.href);
}, true);

function addMeta(div, text) {
  const meta = document.createElement("div"); meta.className = "meta"; meta.textContent = text;
  div.append(meta);
}

// Redraw a saved message (thinking collapsed, tool lines, stats).
function renderMsg(m) {
  const { div, body } = addMsg(m.role, m.content, m.error);
  if (m.role !== "assistant") return;
  if (m.reasoning) {
    const d = document.createElement("details");
    const sm = document.createElement("summary"); sm.textContent = "Thinking (" + m.reasoning.length + " chars)";
    const pre = document.createElement("div"); pre.textContent = m.reasoning;
    d.append(sm, pre); div.insertBefore(d, body);
  }
  for (const t of m.tools || []) {
    const el = document.createElement("div"); el.className = "tool" + (t.err ? " err" : ""); el.textContent = t.text;
    div.insertBefore(el, body);
  }
  if (m.meta) addMeta(div, m.meta);
}

// One line per web tool call, updated in place when it finishes.
function showTool(div, before, ev) {
  const a = ev.args || {};
  const label = ev.name === "web_search" ? `Searching: ${a.query || ""}`
    : ev.name === "image_search" ? `Images: ${a.query || ""}`
    : ev.name === "list_files" ? `Listing: ${a.path || "."}${a.recursive ? " (recursive)" : ""}`
    : ev.name === "search_files" ? `Searching files: ${a.query || ""}${a.path ? " in " + a.path : ""}`
    : ev.name === "read_file" ? `Reading file: ${a.path || ""}${a.start_line > 1 ? " from line " + a.start_line : ""}`
    : `Reading: ${a.url || ""}`;
  let el = [...div.querySelectorAll(".tool")].find((x) => x.dataset.label === label && x.dataset.done !== "1");
  if (!el) {
    el = document.createElement("div");
    el.className = "tool"; el.dataset.label = label;
    div.insertBefore(el, before);
  }
  if (ev.status === "done") { el.dataset.done = "1"; el.classList.toggle("err", !ev.ok); }
  const icon = { web_search: "🔎 ", image_search: "🖼️ ", list_files: "📁 ", search_files: "🔍 ", read_file: "📃 " }[ev.name] || "📄 ";
  el.textContent = icon + label + (ev.status === "done" ? ` — ${ev.summary}` : " …");
}

// ---- history ----
function when(ts) {
  const d = new Date(ts * 1000), now = new Date();
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const opts = { month: "short", day: "numeric" };
  if (d.getFullYear() !== now.getFullYear()) opts.year = "numeric";
  return d.toLocaleDateString([], opts);
}

async function loadHistory() {
  let r;
  try { r = await api("/api/chats"); } catch (e) { $("hist-note").textContent = "History unavailable: " + e.message; return; }
  const ul = $("hist-list");
  ul.replaceChildren();
  for (const c of r.chats) {
    const li = document.createElement("li");
    li.dataset.id = c.id;
    li.classList.toggle("active", c.id === chatId);
    li.classList.toggle("gen", !!gen && c.id === gen.id);
    li.title = c.title;
    const t = document.createElement("div"); t.className = "t";
    const a = document.createElement("div"); a.textContent = c.title;
    const b = document.createElement("div"); b.className = "when"; b.textContent = `${when(c.updated)} · ${c.count} messages`;
    if (c.folder) { const f = document.createElement("span"); f.className = "fld"; f.textContent = "📁"; f.title = c.folder; b.prepend(f); }
    t.append(a, b);
    const del = document.createElement("button"); del.className = "del"; del.textContent = "×"; del.title = "Delete chat";
    del.onclick = (e) => { e.stopPropagation(); deleteChat(c.id, c.title); };
    li.append(t, del);
    li.onclick = () => openChat(c.id);
    ul.append(li);
  }
  if (!r.chats.length) {
    const li = document.createElement("li"); li.className = "muted small"; li.style.cursor = "default";
    li.textContent = "No saved chats yet."; ul.append(li);
  }
  $("hist-count").textContent = `${r.chats.length} / ${r.max}`;
  $("hist-note").textContent = r.chats.length >= r.max ? `Only the ${r.max} most recent chats are kept; the oldest is removed when another is saved.` : "";
}

function markActive() {
  document.querySelectorAll("#hist-list li").forEach((li) => li.classList.toggle("active", li.dataset.id === chatId));
}

// Stop shows in the chat that is replying; in any other chat Send waits, because the backend answers one at a time.
function syncControls() {
  const here = !!gen && gen.id === chatId;
  $("chat-send").classList.toggle("hidden", here); $("chat-stopgen").classList.toggle("hidden", !here);
  $("chat-send").disabled = !!gen && !here;
  $("chat-send").title = $("chat-send").disabled ? "Another chat is still replying. Wait for it or stop it first." : "";
  document.querySelectorAll("#hist-list li").forEach((li) => li.classList.toggle("gen", !!gen && li.dataset.id === gen.id));
}

// Saves run one at a time so a quick reply cannot be overwritten by the earlier save of its question.
let saveQueue = Promise.resolve();
function saveChat(id, messages, folder) {
  const snapshot = messages.slice();
  saveQueue = saveQueue.then(() => doSave(id, snapshot, folder));
}
async function doSave(id, messages, folder) {
  try {
    const r = await api("/api/chats/" + id, { body: { messages, folder: folder || "" } });
    if (r.pruned && r.pruned.length) toast(`Removed ${r.pruned.length} oldest chat${r.pruned.length > 1 ? "s" : ""} (history keeps 20).`);
  } catch (e) { toast("Could not save chat: " + e.message, true); }
  loadHistory();
}

function newChat() {
  openToken++;
  chatId = null; chatHistory = []; chatFolder = "";
  $("chat-log").replaceChildren();
  markActive(); syncControls(); showFolder();
  $("chat-text").focus();
}

let openToken = 0;
async function openChat(id) {
  if (id === chatId) return;
  const token = ++openToken;
  try {
    // The chat that is replying is shown from memory: its latest answer is not saved yet.
    const live = gen && gen.id === id ? gen : null;
    const c = live ? { id, messages: live.history, folder: live.folder } : await api("/api/chats/" + id);
    if (token !== openToken) return;               // a newer click won
    chatId = c.id; chatHistory = c.messages || []; chatFolder = c.folder || "";
    $("chat-log").replaceChildren();
    for (const m of chatHistory) renderMsg(m);
    if (live) $("chat-log").append(live.div);
    markActive(); syncControls(); showFolder();
    scrollLog(true);
  } catch (e) { toast(e.message, true); loadHistory(); }
}

async function deleteChat(id, title) {
  if (gen && id === gen.id) return toast("Stop the reply first.");
  if (!confirm(`Delete the chat “${title}”? This cannot be undone.`)) return;
  try { await api("/api/chats/" + id, { method: "DELETE" }); } catch (e) { toast(e.message, true); }
  if (id === chatId) newChat();
  loadHistory();
}

async function send() {
  const text = $("chat-text").value.trim();
  if (!text || gen) return;
  $("chat-text").value = "";
  if (!chatId) chatId = newChatId();
  const id = chatId, history = chatHistory, folder = chatFolder;
  addMsg("user", text);
  history.push({ role: "user", content: text });
  saveChat(id, history, folder);
  const messages = ($("p-system").value.trim() ? [{ role: "system", content: $("p-system").value.trim() }] : [])
    .concat(history.map(({ role, content }) => ({ role, content })));
  const effort = $("p-effort").value;
  const body = {
    messages, temperature: +$("p-temp").value, top_p: +$("p-topp").value, top_k: +$("p-topk").value,
    presence_penalty: +$("p-pres").value, max_tokens: +$("p-max").value,
    chat_template_kwargs: effort === "off" ? { enable_thinking: false } : { enable_thinking: true, reasoning_effort: effort },
    web: $("p-web").checked,
    folder,
  };
  const { div, body: out } = addMsg("assistant", "");
  let reasoningEl = null, reasoning = "", content = "", final = null, error = null;
  const controller = new AbortController();
  gen = { id, controller, div, history, folder };
  syncControls(); scrollLog(true);
  const t0 = performance.now();
  try {
    const res = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: controller.signal });
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.error || "HTTP " + res.status); }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (payload === "[DONE]") continue;
        const chunk = JSON.parse(payload);
        if (chunk.error) throw new Error(chunk.error);
        if (chunk.tool_event) {
          showTool(div, out, chunk.tool_event);
          if (reasoning && !reasoning.endsWith("\n\n")) reasoning += "\n\n";
          continue;
        }
        if (chunk.timings) final = chunk.timings;
        for (const ch of chunk.choices || []) {
          const d = ch.delta || {};
          if (d.reasoning_content) {
            if (!reasoningEl) {
              reasoningEl = document.createElement("details"); reasoningEl.open = true;
              const sm = document.createElement("summary"); sm.textContent = "Thinking…";
              const pre = document.createElement("div"); reasoningEl.append(sm, pre);
              div.insertBefore(reasoningEl, out);
            }
            reasoning += d.reasoning_content; reasoningEl.lastChild.textContent = reasoning;
          }
          if (d.content) {
            if (reasoningEl && reasoningEl.open) { reasoningEl.open = false; reasoningEl.firstChild.textContent = "Thinking (" + reasoning.length + " chars)"; }
            content += d.content; renderLater(out, content);
          }
        }
        if (chatId === id) scrollLog();
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") error = e.message;
  } finally {
    pendingRender = null;
    setBody(out, "assistant", content, error);
    gen = null;
    syncControls();
  }
  let meta;
  if (final) {
    const acc = final.draft_n ? ` · MTP ${Math.round((final.draft_n_accepted / final.draft_n) * 100)}%` : "";
    meta = `${final.predicted_n} tok · ${fmt(final.predicted_per_second)} tok/s · prompt ${final.prompt_n} tok in ${fmt(final.prompt_ms / 1000)} s${acc}`;
  } else {
    meta = `${fmt((performance.now() - t0) / 1000)} s`;
  }
  addMeta(div, meta);
  const msg = { role: "assistant", content, meta };
  if (reasoning) msg.reasoning = reasoning;
  const tools = [...div.querySelectorAll(".tool")].map((el) => ({ text: el.textContent, err: el.classList.contains("err") }));
  if (tools.length) msg.tools = tools;
  if (error) msg.error = error;
  history.push(msg);
  saveChat(id, history, folder);
  if (chatId === id) scrollLog();
}

// ---- folder access: one folder per chat that the model may list, search and read ----
function showFolder() {
  const el = $("p-folder");
  el.textContent = chatFolder || "No folder";
  el.title = chatFolder;
  el.classList.toggle("set", !!chatFolder);
  $("p-folder-clear").classList.toggle("hidden", !chatFolder);
  $("p-folder-pick").textContent = chatFolder ? "Change…" : "Choose folder…";
}

// Apply a picked or typed folder to the open chat (validated by the server), and save it if the chat exists.
async function setFolder(path) {
  const id = chatId;
  let abs = "";
  if (path) {
    try { abs = (await api("/api/folder/check", { body: { path } })).path; } catch (e) { return toast(e.message, true); }
  }
  if (id !== chatId) return;                       // the user switched chats meanwhile
  chatFolder = abs;
  if (gen && gen.id === id) gen.folder = abs;      // applies to the next message; the running reply keeps its own
  showFolder();
  if (id) saveChat(id, chatHistory, abs);
  toast(abs ? "Folder access: " + abs : "Folder access removed.");
}

// The Mac app opens a native folder panel (main.swift answers through window.onFolderPicked); in a browser the
// server shows the macOS dialog, and anywhere else a path can be typed.
let pickToken = 0;
async function pickFolder() {
  const bridge = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.pickFolder;
  if (bridge) { bridge.postMessage({ token: ++pickToken, start: chatFolder }); return; }
  $("p-folder-pick").disabled = true;
  try {
    const r = await api("/api/folder/pick", { body: { path: chatFolder } });
    if (r.path) await setFolder(r.path);
  } catch (e) {
    const typed = prompt("Folder the chat may read (" + e.message + "):", chatFolder || "~/");
    if (typed !== null && typed.trim()) await setFolder(typed.trim());
  } finally { $("p-folder-pick").disabled = false; }
}
window.onFolderPicked = (path, token) => { if (token === pickToken && path) setFolder(path); };
$("p-folder-pick").onclick = pickFolder;
$("p-folder-clear").onclick = () => setFolder("");
showFolder();

// ---- resizable chat panels: drag a divider, double-click it to reset; the layout refits when the window resizes ----
(() => {
  const layout = document.querySelector(".chat-layout");
  const P = {
    "--hist-w": { key: "hist", min: 150, max: 460, def: 230, pane: $("pane-hist") },
    "--side-w": { key: "side", min: 220, max: 600, def: 300, pane: $("pane-side") },
  };
  const want = { "--hist-w": P["--hist-w"].def, "--side-w": P["--side-w"].def };   // what the user chose
  const CENTER_MIN = 320, GAP = 14;
  const narrow = () => matchMedia("(max-width: 900px)").matches, tiny = () => matchMedia("(max-width: 640px)").matches;

  function fit() {
    let h = want["--hist-w"], s = want["--side-w"];
    if (!tiny()) {
      const room = layout.clientWidth - GAP * (narrow() ? 1 : 2) - CENTER_MIN;
      if (narrow()) h = Math.max(P["--hist-w"].min, Math.min(h, room));
      else if (h + s > room) {                       // squeeze the parameters first, then the history
        let over = h + s - room;
        const cutS = Math.min(over, s - P["--side-w"].min); s -= cutS; over -= cutS;
        h = Math.max(P["--hist-w"].min, h - over);
      }
    }
    layout.style.setProperty("--hist-w", Math.round(h) + "px");
    layout.style.setProperty("--side-w", Math.round(s) + "px");
  }

  let saveTimer = null;
  const save = () => { clearTimeout(saveTimer); saveTimer = setTimeout(() => api("/api/layout", { body: { hist: Math.round(want["--hist-w"]), side: Math.round(want["--side-w"]) } }).catch(() => {}), 300); };

  for (const rs of document.querySelectorAll(".resizer")) {
    const v = rs.dataset.var, p = P[v], dir = v === "--hist-w" ? 1 : -1;
    rs.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      rs.setPointerCapture(e.pointerId);
      const x0 = e.clientX, w0 = p.pane.getBoundingClientRect().width;
      rs.classList.add("drag"); document.body.classList.add("resizing");
      const move = (ev) => {
        const other = P[v === "--hist-w" ? "--side-w" : "--hist-w"];
        const otherW = narrow() && v === "--hist-w" ? 0 : other.pane.getBoundingClientRect().width;
        const room = layout.clientWidth - GAP * (narrow() ? 1 : 2) - CENTER_MIN - otherW;
        want[v] = Math.max(p.min, Math.min(p.max, room, w0 + dir * (ev.clientX - x0)));
        fit();
      };
      const up = () => {
        rs.classList.remove("drag"); document.body.classList.remove("resizing");
        rs.removeEventListener("pointermove", move); rs.removeEventListener("pointerup", up); rs.removeEventListener("pointercancel", up);
        save();
      };
      rs.addEventListener("pointermove", move); rs.addEventListener("pointerup", up); rs.addEventListener("pointercancel", up);
    });
    rs.addEventListener("dblclick", () => { want[v] = p.def; fit(); save(); });
  }

  new ResizeObserver(fit).observe(layout);
  fit();
  api("/api/layout").then((r) => {
    for (const [v, p] of Object.entries(P)) if (typeof r[p.key] === "number") want[v] = Math.max(p.min, Math.min(p.max, r[p.key]));
    fit();
  }).catch(() => {});
})();

$("chat-send").onclick = send;
$("chat-stopgen").onclick = () => gen && gen.controller.abort();
$("chat-new").onclick = newChat;
$("chat-text").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
loadHistory();

// ---------------- benchmark ----------------
$("b-run").onclick = async () => {
  const body = { runs: +$("b-runs").value, max_tokens: +$("b-max").value, fill: +$("b-fill").value };
  if ($("b-temp").value !== "") body.temperature = +$("b-temp").value;
  $("b-run").disabled = true;
  $("b-msg").textContent = "Running… (long fills take minutes)";
  try {
    const r = await api("/api/bench", { body });
    const tb = $("b-table").tBodies[0];
    const when = new Date().toLocaleTimeString();
    for (const row of r.rows) {
      const tr = tb.insertRow(0);
      for (const v of [when, engineName(r.engine), r.profile, r.fill ? "~" + r.fill : "—", row.prompt_tokens, fmt(row.prefill_tps, 0), fmt(row.ttft_s), row.gen_tokens, fmt(row.decode_tps), pct(row.accept)])
        tr.insertCell().textContent = v ?? "—";
    }
    const dec = r.rows.map((x) => x.decode_tps).filter(Boolean);
    $("b-msg").textContent = dec.length ? `Mean decode ${fmt(dec.reduce((a, b) => a + b, 0) / dec.length)} tok/s` : "Done";
  } catch (e) { $("b-msg").textContent = ""; toast(e.message, true); }
  $("b-run").disabled = false;
};

// ---------------- logs ----------------
async function loadLogs() {
  try {
    const r = await api(`/api/logs?lines=${$("l-lines").value}&which=${$("l-which").value}`);
    const f = $("l-filter").value.toLowerCase();
    const lines = f ? r.lines.filter((l) => l.toLowerCase().includes(f)) : r.lines;
    const pre = $("l-text");
    const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
    pre.textContent = lines.join("\n") || "(empty)";
    if (atBottom) pre.scrollTop = pre.scrollHeight;
    $("l-path").textContent = r.path;
  } catch (e) { $("l-text").textContent = e.message; }
}
$("l-refresh").onclick = loadLogs;
$("l-which").onchange = loadLogs;
$("l-lines").onchange = loadLogs;
$("l-filter").oninput = loadLogs;

// ---------------- boot ----------------
(async function boot() {
  try { await loadConfig(); } catch (e) { toast(e.message, true); }
  await refreshStatus();
  try { await loadProfiles(); } catch (e) { toast("Could not load profiles: " + e.message, true); }
  setInterval(() => { if (!busy && activeTab === "dashboard") refreshStatus(); }, 4000);
  setInterval(() => { if (activeTab === "logs" && $("l-auto").checked) loadLogs(); }, 3000);
})();
