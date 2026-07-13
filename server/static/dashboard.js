/* Gradbot Ledger — dashboard for recent calls. */

import { bootAdmin, makeGetJSON } from "/auth.js";
import {
  fmtInt, fmtTokens, fmtDuration, fmtDate, escapeHtml,
} from "/format.js";

let getJSON = null;

const state = {
  days: 7,
  limit: 20,
  offset: 0,
  total: 0,
  persona: "",
  environment: "",
  // This dashboard reads a database shared with the Pipecat app (sceance), so
  // the ledger shows both stacks' calls unless you narrow it.
  framework: "",
};

// ─── Aggregate / KPIs ───────────────────────────────────────────────

async function loadAggregate() {
  const agg = document.getElementById("aggregate");
  agg.innerHTML = `<div class="agg-placeholder">Summoning…</div>`;
  try {
    const data = await getJSON(`/api/aggregate?days=${state.days}`);
    renderAggregate(data);
  } catch (e) {
    agg.innerHTML = `<div class="agg-placeholder">Could not load aggregate · ${e.message}</div>`;
  }
}

function renderAggregate(data) {
  const s = data.sessions;
  const live = (s.total_sessions || 0) - (s.finished_sessions || 0);
  const subSessions = live > 0 ? `${live} still open` : `${s.finished_sessions || 0} finished`;
  const kpis = [
    {
      label: "Calls",
      value: fmtInt(s.total_sessions),
      sub: subSessions,
    },
    {
      label: "Total time",
      value: fmtDuration(s.total_duration_s),
      sub: "across all sessions",
    },
    {
      label: "Prompt tokens",
      value: fmtTokens(s.prompt_tokens),
      sub: `${fmtTokens(s.cache_read_tokens)} cache-read`,
    },
    {
      label: "Completion",
      value: fmtTokens(s.completion_tokens),
      sub: "tokens generated",
    },
    {
      label: "TTS characters",
      value: fmtTokens(s.tts_chars),
      sub: "spoken aloud",
    },
  ];

  const agg = document.getElementById("aggregate");
  agg.innerHTML = kpis.map(k => `
    <div class="kpi">
      <div class="label">${k.label}</div>
      <div class="value">${k.value}</div>
      <div class="accent-rule"></div>
      <div class="sub">${k.sub}</div>
    </div>
  `).join("");
}

// ─── Sessions list ──────────────────────────────────────────────────

async function loadSessions() {
  const list = document.getElementById("sessions");
  list.innerHTML = `<li class="loading">Loading…</li>`;
  try {
    const params = new URLSearchParams({ limit: state.limit, offset: state.offset });
    if (state.persona) params.set("persona", state.persona);
    if (state.environment) params.set("environment", state.environment);
    if (state.framework) params.set("framework", state.framework);
    const data = await getJSON(`/api/sessions?${params}`);
    state.total = data.total;
    renderPersonaFilter(data.personas || []);
    renderEnvFilter(data.environments || []);
    renderFrameworkFilter(data.frameworks || []);
    renderSessions(data.sessions);
    renderPager();
  } catch (e) {
    list.innerHTML = `<li class="loading">Could not load sessions · ${e.message}</li>`;
  }
}

function renderSessions(sessions) {
  const list = document.getElementById("sessions");
  if (!sessions.length) {
    list.innerHTML = `<li class="loading">No calls yet. Pick up the phone.</li>`;
    return;
  }
  list.innerHTML = sessions.map(s => {
    const live = !s.ended_at;
    const lang = (s.lang || "—").toUpperCase();
    return `
      <li class="session ${live ? "live" : ""}" data-id="${s.id}">
        <a class="session-row" href="/sessions/${encodeURIComponent(s.id)}">
          <div class="s-persona">
            ${escapeHtml(s.persona_name || "(unnamed)")}
            <span class="lang">${escapeHtml(lang)}</span>
          </div>
          <div class="s-env">
            ${escapeHtml(s.environment || "—")}
            <span class="lang">${escapeHtml(s.framework || "?")}</span>
          </div>
          <div class="s-date">${fmtDate(s.started_at)}</div>
          <div class="s-dur">${fmtDuration(s.duration_s)}</div>
          <div class="s-msgs">${s.msg_count} turn${s.msg_count === 1 ? "" : "s"}</div>
          <div class="s-tok">
            ${fmtTokens(s.prompt_tokens)} <span class="small">in</span>
            · ${fmtTokens(s.completion_tokens)} <span class="small">out</span>
          </div>
          <div class="s-chev">›</div>
        </a>
      </li>
    `;
  }).join("");
}

// Populate a filter <select> with options, preserving the current selection.
// Skips the rebuild if options are unchanged so the dropdown isn't disturbed
// while the user interacts with it.
function renderFilter(id, allLabel, values, selected) {
  const sel = document.getElementById(id);
  if (!sel) return;
  const want = ["", ...values];
  const have = Array.from(sel.options).map(o => o.value);
  if (have.length === want.length && have.every((v, i) => v === want[i])) return;
  sel.innerHTML =
    `<option value="">${escapeHtml(allLabel)}</option>` +
    values.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
  sel.value = selected;
}

function renderPersonaFilter(personas) {
  renderFilter("persona-filter", "All agents", personas, state.persona);
}

function renderEnvFilter(environments) {
  renderFilter("env-filter", "All envts", environments, state.environment);
}

function renderFrameworkFilter(frameworks) {
  renderFilter("framework-filter", "All stacks", frameworks, state.framework);
}

function renderPager() {
  const pager = document.getElementById("pager");
  const page = Math.floor(state.offset / state.limit) + 1;
  const pages = Math.max(1, Math.ceil(state.total / state.limit));
  pager.innerHTML = `
    <button id="prev" ${state.offset === 0 ? "disabled" : ""}>← Prev</button>
    <span class="pos">${page} / ${pages}</span>
    <button id="next" ${state.offset + state.limit >= state.total ? "disabled" : ""}>Next →</button>
  `;
  pager.querySelector("#prev").addEventListener("click", () => {
    state.offset = Math.max(0, state.offset - state.limit);
    loadSessions();
  });
  pager.querySelector("#next").addEventListener("click", () => {
    state.offset += state.limit;
    loadSessions();
  });
}

// ─── Period selector ────────────────────────────────────────────────

function bindPeriod() {
  const nav = document.getElementById("period");
  nav.querySelectorAll("button").forEach(btn => {
    btn.addEventListener("click", () => {
      nav.querySelectorAll("button").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      state.days = parseInt(btn.dataset.days, 10);
      loadAggregate();
    });
  });
}

function bindFilter(id, key) {
  const sel = document.getElementById(id);
  if (!sel) return;
  sel.addEventListener("change", () => {
    state[key] = sel.value;
    state.offset = 0;
    loadSessions();
  });
}

// ─── Boot ───────────────────────────────────────────────────────────

async function boot() {
  const auth = await bootAdmin();
  if (!auth) return;
  getJSON = makeGetJSON(auth.getToken);

  bindPeriod();
  bindFilter("persona-filter", "persona");
  bindFilter("env-filter", "environment");
  bindFilter("framework-filter", "framework");
  loadAggregate();
  loadSessions();
}

boot();
