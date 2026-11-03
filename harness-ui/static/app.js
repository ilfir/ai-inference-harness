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

async function loadConfig() {
  const c = await api("/api/config");
  $("hdr-conn").textContent = c.host ? "host: " + c.host : "host: this machine";
  $("cfg-host").value = c.host || "";
  $("cfg-home").value = c.remote_home || "";
  httpHost = c.http_host || "127.0.0.1";
}

async function loadProfiles() {
  const data = await api("/api/profiles");
  profiles = data.profiles;
  for (const sel of [$("ctl-profile"), $("set-profile")]) {
    const keep = sel.value;
    sel.replaceChildren(...profiles.map((p) => new Option(`${p.name} — ${p.description}`, p.name)));
    if (keep && profiles.some((p) => p.name === keep)) sel.value = keep;
  }
  if (st && st.backend && !$("ctl-profile").dataset.touched) $("ctl-profile").value = st.backend.profile;
  if (st && st.backend && !$("set-profile").dataset.touched) $("set-profile").value = st.backend.profile;
  renderSettings();
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
    const ok = b.health === "ok";
    $("hdr-dot").className = "dot " + (ok ? "ok" : "warn");
    $("hdr-model").textContent = `${b.profile} · ${b.busy_slots ? b.busy_slots + " busy" : "idle"}`;
    $("d-state").textContent = ok ? (b.busy_slots ? "Generating" : "Running") : "Loading…";
    const f = b.flags || {};
    dl($("d-backend"), [
      ["Profile", b.profile], ["PID", b.pid], ["Uptime", duration(b.uptime_s)],
      ["Endpoint", `http://${httpHost}:${b.port}/v1`],
      ["Slots", `${b.busy_slots}/${b.slots} busy`],
      ["Context", f.ctx ? Number(f.ctx).toLocaleString() + " tokens" : "—"],
      ["MTP", f.mtp ? `on (draft ${f.draft_n_max || 1})` : "off"],
      ["Thinking", (f.effort || "xhigh") + (f.reasoning_budget && f.reasoning_budget !== "-1" ? `, budget ${f.reasoning_budget}` : "")],
      ["KV cache", f.cache_type || "—"], ["Prompt cache", f.cache_ram ? f.cache_ram + " MiB" : "default"],
      ["API key", f.auth ? "required" : "none"],
    ]);
    const s = b.stats || {}, last = s.last || {};
    $("d-tps").textContent = fmt(s.decode_tps);
    $("d-acc").textContent = f.mtp ? pct(s.accept) : "off";
    dl($("d-perf"), [
      ["Requests", s.requests ?? "—"], ["Tokens generated", (s.generated ?? 0).toLocaleString()],
      ["Last reply", last.tokens ? `${last.tokens} tok at ${fmt(last.decode_tps)} tok/s` : "—"],
      ["Last prompt", last.prompt_tokens ? `${last.prompt_tokens} new tok at ${fmt(last.prefill_tps, 0)} tok/s` : "—"],
      ["Last MTP accept", f.mtp ? pct(last.accept) : "—"],
    ]);
    showBanner(b.settings_changed ? "Settings for the running profile changed since it started. Restart to apply." : null);
  } else {
    $("hdr-dot").className = "dot bad";
    $("hdr-model").textContent = "stopped";
    $("d-state").textContent = st.desired && st.desired.running ? "Stopped (watchdog will restart)" : "Stopped";
    dl($("d-backend"), [["Build", st.build.llama_server ? "llama-server built" : "not built — run: harness build"]]);
    $("d-tps").textContent = "—"; $("d-acc").textContent = "—"; dl($("d-perf"), []);
    if (!$("cfg-host").value && !st.build.llama_server) {
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
      toast(`Started ${name} (pid ${r.pid}). Loading the model takes 5–30 s.`);
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
  for (let i = 0; i < 120; i++) {
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
const ORDER = ["CTX", "PARALLEL", "MTP_ENABLED", "DRAFT_N_MAX", "REASONING_EFFORT", "REASONING_BUDGET", "CACHE_TYPE", "CACHE_RAM_MB", "GPU_BUDGET_GB",
  "THREADS", "PORT", "HOST", "API_KEY", "EXTRA_ARGS"];

function renderSettings() {
  const p = profiles.find((x) => x.name === $("set-profile").value) || profiles[0];
  if (!p) return;
  $("set-profile").value = p.name;
  $("set-desc").textContent = `${p.model || ""}${p.alias ? " · model alias " + p.alias : ""}`;
  const form = $("set-form");
  form.replaceChildren();
  form.className = "form";
  for (const key of ORDER) {
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
      for (const o of s.choices) input.add(new Option(key === "CTX" ? Number(o).toLocaleString() : o, o));
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
    def.textContent = s.type === "secret" ? "" : `profile default: ${s.type === "bool" ? (s.default ? "on" : "off") : (s.default === "" ? "(empty)" : s.default)}`;
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
    await api("/api/config", { body: { host: $("cfg-host").value, remote_home: $("cfg-home").value } });
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
let controller = null;

const newChatId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8);

function addMsg(role, text) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  const body = document.createElement("div"); body.textContent = text;
  div.append(body);
  $("chat-log").append(div);
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
  return { div, body };
}

function addMeta(div, text) {
  const meta = document.createElement("div"); meta.className = "meta"; meta.textContent = text;
  div.append(meta);
}

// Redraw a saved message (thinking collapsed, tool lines, stats).
function renderMsg(m) {
  const { div, body } = addMsg(m.role, m.content + (m.error ? "\n[error] " + m.error : ""));
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
  const label = ev.name === "web_search" ? `Searching: ${ev.args.query || ""}` : `Reading: ${ev.args.url || ""}`;
  let el = [...div.querySelectorAll(".tool")].find((x) => x.dataset.label === label && x.dataset.done !== "1");
  if (!el) {
    el = document.createElement("div");
    el.className = "tool"; el.dataset.label = label;
    div.insertBefore(el, before);
  }
  if (ev.status === "done") { el.dataset.done = "1"; el.classList.toggle("err", !ev.ok); }
  el.textContent = (ev.name === "web_search" ? "🔎 " : "📄 ") + label + (ev.status === "done" ? ` — ${ev.summary}` : " …");
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
    li.title = c.title;
    const t = document.createElement("div"); t.className = "t";
    const a = document.createElement("div"); a.textContent = c.title;
    const b = document.createElement("div"); b.className = "when"; b.textContent = `${when(c.updated)} · ${c.count} messages`;
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

function setGenerating(on) {
  $("chat-send").classList.toggle("hidden", on); $("chat-stopgen").classList.toggle("hidden", !on);
  $("hist-list").classList.toggle("busy", on);
  $("chat-new").disabled = on;
}

// Saves run one at a time so a quick reply cannot be overwritten by the earlier save of its question.
let saveQueue = Promise.resolve();
function saveChat(id, messages) {
  const snapshot = messages.slice();
  saveQueue = saveQueue.then(() => doSave(id, snapshot));
}
async function doSave(id, messages) {
  try {
    const r = await api("/api/chats/" + id, { body: { messages } });
    if (r.pruned && r.pruned.length) toast(`Removed ${r.pruned.length} oldest chat${r.pruned.length > 1 ? "s" : ""} (history keeps 20).`);
  } catch (e) { toast("Could not save chat: " + e.message, true); }
  loadHistory();
}

function newChat() {
  if (controller) return toast("Stop the reply first.");
  chatId = null; chatHistory = [];
  $("chat-log").replaceChildren();
  markActive();
  $("chat-text").focus();
}

async function openChat(id) {
  if (controller) return toast("Stop the reply first.");
  if (id === chatId) return;
  try {
    const c = await api("/api/chats/" + id);
    chatId = c.id; chatHistory = c.messages || [];
    $("chat-log").replaceChildren();
    for (const m of chatHistory) renderMsg(m);
    markActive();
  } catch (e) { toast(e.message, true); loadHistory(); }
}

async function deleteChat(id, title) {
  if (controller && id === chatId) return toast("Stop the reply first.");
  if (!confirm(`Delete the chat “${title}”? This cannot be undone.`)) return;
  try { await api("/api/chats/" + id, { method: "DELETE" }); } catch (e) { toast(e.message, true); }
  if (id === chatId) newChat();
  loadHistory();
}

async function send() {
  const text = $("chat-text").value.trim();
  if (!text || controller) return;
  $("chat-text").value = "";
  if (!chatId) chatId = newChatId();
  const id = chatId, history = chatHistory;
  addMsg("user", text);
  history.push({ role: "user", content: text });
  saveChat(id, history);
  const messages = ($("p-system").value.trim() ? [{ role: "system", content: $("p-system").value.trim() }] : [])
    .concat(history.map(({ role, content }) => ({ role, content })));
  const effort = $("p-effort").value;
  const body = {
    messages, temperature: +$("p-temp").value, top_p: +$("p-topp").value, top_k: +$("p-topk").value,
    presence_penalty: +$("p-pres").value, max_tokens: +$("p-max").value,
    chat_template_kwargs: effort === "off" ? { enable_thinking: false } : { enable_thinking: true, reasoning_effort: effort },
    web: $("p-web").checked,
  };
  const { div, body: out } = addMsg("assistant", "");
  let reasoningEl = null, reasoning = "", content = "", final = null, error = null;
  controller = new AbortController();
  setGenerating(true);
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
            content += d.content; out.textContent = content;
          }
        }
        $("chat-log").scrollTop = $("chat-log").scrollHeight;
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") { error = e.message; out.textContent = content + "\n[error] " + e.message; }
  } finally {
    controller = null;
    setGenerating(false);
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
  saveChat(id, history);
}

$("chat-send").onclick = send;
$("chat-stopgen").onclick = () => controller && controller.abort();
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
      for (const v of [when, r.profile, r.fill ? "~" + r.fill : "—", row.prompt_tokens, fmt(row.prefill_tps, 0), fmt(row.ttft_s), row.gen_tokens, fmt(row.decode_tps), pct(row.accept)])
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
    const r = await api(`/api/logs?lines=${$("l-lines").value}&watchdog=${$("l-which").value}`);
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
