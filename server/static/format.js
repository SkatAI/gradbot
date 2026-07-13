/* Shared formatters + session-detail rendering for the operator pages. */

const nf = new Intl.NumberFormat("en-US");

export function fmtInt(n) {
  if (n == null) return "—";
  return nf.format(n);
}

export function fmtTokens(n) {
  if (n == null) return "—";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}

export function fmtDuration(seconds) {
  if (seconds == null || isNaN(seconds)) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `${m}m ${String(s).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export function fmtMs(seconds) {
  if (seconds == null) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  return `${seconds.toFixed(2)}s`;
}

export function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

export function fmtClock(iso) {
  // Precise wall-clock time-of-day, to the millisecond. Used in the transcript
  // to show when each turn was recorded — for an assistant turn this is the
  // moment LLM text generation finished (recorded at LLMFullResponseEndFrame).
  if (!iso) return null;
  const d = new Date(iso);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  const ms = String(d.getMilliseconds()).padStart(3, "0");
  return `${hh}:${mm}:${ss}.${ms}`;
}

export function shortProcessor(p) {
  // "AnthropicLLMService#0" -> "Anthropic LLM"
  if (!p) return "?";
  return p
    .replace(/#\d+$/, "")
    .replace(/Service$/, "")
    .replace(/([a-z])([A-Z])/g, "$1 $2");
}

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ─── Session detail ─────────────────────────────────────────────────

export function renderDetail(s) {
  // metrics summary table
  const metricsRows = s.metrics.map(m => `
    <tr>
      <td>${shortProcessor(m.processor)}</td>
      <td>${m.kind}</td>
      <td class="num">${m.n}</td>
      <td class="num">${formatMetricValue(m.kind, m.avg)}</td>
      <td class="num">${formatMetricValue(m.kind, m.sum)}</td>
    </tr>
  `).join("") || `<tr><td colspan="5" class="empty">no metrics captured</td></tr>`;

  // transcript
  const turns = s.messages.length
    ? s.messages.map(m => {
        const t = fmtClock(m.recorded_at);
        // For assistant turns recorded_at is captured at LLMFullResponseEndFrame,
        // i.e. when LLM text generation finished; for user turns it's when the
        // final transcript was recorded.
        const tTitle = m.role === "assistant"
          ? "LLM text generation finished"
          : "transcript recorded";
        const ts = t ? `<span class="ts" title="${tTitle}">${t}</span>` : "";
        return `
        <div class="turn ${m.role}">
          <div class="who"><span>${escapeHtml(m.role)}${m.language ? ` · ${escapeHtml(m.language)}` : ""}</span>${ts}</div>
          <div class="text">${escapeHtml(m.text)}</div>
        </div>
      `;
      }).join("")
    : `<div class="empty">No transcript recorded.</div>`;

  // metadata
  const persona = s.persona || {};
  const meta = [
    ["Persona", persona.name || s.persona_name || "—"],
    ["Language", s.lang || "—"],
    ["LLM", persona.llm_model || "—"],
    ["TTS voice", persona.tts_voice_id || "—"],
    ["Started", fmtDate(s.started_at)],
    ["Ended", s.ended_at ? fmtDate(s.ended_at) : "—"],
    ["Duration", fmtDuration(s.duration_s)],
    ["Prompt tokens", fmtInt(s.prompt_tokens)],
    ["Completion", fmtInt(s.completion_tokens)],
    ["Cache read", fmtInt(s.cache_read_tokens)],
    ["Cache create", fmtInt(s.cache_creation_tokens)],
    ["TTS chars", fmtInt(s.tts_chars)],
    ["Session id", `<span style="color:var(--text-3)">${s.id}</span>`],
  ];

  return `
    <div class="detail-meta">
      <h3>Session</h3>
      <dl class="meta-grid">
        ${meta.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}
      </dl>
      ${renderResponseLatency(s.response_latency)}
      <h3>Per-step metrics</h3>
      <table class="metrics-table">
        <thead>
          <tr><th>Processor</th><th>Kind</th><th>n</th><th>avg</th><th>sum</th></tr>
        </thead>
        <tbody>${metricsRows}</tbody>
      </table>
    </div>
    <div class="detail-transcript">
      <h3>Transcript</h3>
      <div class="transcript">${turns}</div>
    </div>
  `;
}

// ─── Response latency block (stats + bar chart) ─────────────────────

function renderResponseLatency(r) {
  if (!r || !r.count) {
    return `
      <h3>Response latency</h3>
      <div class="empty" style="color:var(--text-2);padding:0.5rem 0 1.5rem">
        no completed turns yet
      </div>
    `;
  }
  const stats = [
    ["mean",   fmtMs(r.mean)],
    ["median", fmtMs(r.median)],
    ["p90",    fmtMs(r.p90)],
    ["max",    fmtMs(r.max)],
  ];
  return `
    <h3>Response latency · user → bot first word</h3>
    <div class="lat-stats">
      ${stats.map(([k, v]) => `
        <div class="lat-stat">
          <div class="lat-stat-label">${k}</div>
          <div class="lat-stat-value">${v}</div>
        </div>
      `).join("")}
    </div>
    <div class="lat-chart">
      ${renderLatencyChart(r.per_turn, r.median, r.p90)}
    </div>
  `;
}

function renderLatencyChart(perTurn, median, p90) {
  const n = perTurn.length;
  if (!n) return "";
  const w = 360, h = 140;
  const padL = 6, padR = 6, padT = 8, padB = 20;
  const innerW = w - padL - padR;
  const innerH = h - padT - padB;
  const max = Math.max(...perTurn.map(t => t.latency_s), p90 || 0, median || 0, 1);
  // round max up to a nice value for the y scale
  const yMax = Math.ceil(max * 1.1 * 10) / 10;

  const barW = Math.max(2, (innerW / n) - 2);
  const gap  = (innerW / n) - barW;

  const bars = perTurn.map((t, i) => {
    const x = padL + i * (innerW / n) + gap / 2;
    const barH = Math.max(1, (t.latency_s / yMax) * innerH);
    const y = padT + (innerH - barH);
    const color = t.latency_s > (p90 ?? Infinity)
      ? "var(--vermillion)"
      : "var(--accent)";
    return `<rect x="${x.toFixed(2)}" y="${y.toFixed(2)}"
                  width="${barW.toFixed(2)}" height="${barH.toFixed(2)}"
                  fill="${color}">
              <title>turn ${t.turn}: ${fmtMs(t.latency_s)}</title>
            </rect>`;
  }).join("");

  const refLines = [];
  if (median != null) {
    const y = padT + innerH - (median / yMax) * innerH;
    refLines.push(`
      <line x1="${padL}" x2="${padL + innerW}" y1="${y}" y2="${y}"
            stroke="var(--text-3)" stroke-dasharray="3 3" stroke-width="1"/>
      <text x="${padL + innerW - 2}" y="${y - 3}" text-anchor="end"
            font-family="JetBrains Mono, monospace" font-size="9"
            fill="var(--text-3)">median ${fmtMs(median)}</text>
    `);
  }
  if (p90 != null && p90 !== median) {
    const y = padT + innerH - (p90 / yMax) * innerH;
    refLines.push(`
      <line x1="${padL}" x2="${padL + innerW}" y1="${y}" y2="${y}"
            stroke="var(--vermillion)" stroke-dasharray="3 3" stroke-width="1"/>
      <text x="${padL + innerW - 2}" y="${y - 3}" text-anchor="end"
            font-family="JetBrains Mono, monospace" font-size="9"
            fill="var(--vermillion)">p90 ${fmtMs(p90)}</text>
    `);
  }

  // x-axis: first and last turn labels
  const xAxis = `
    <text x="${padL}" y="${h - 4}" font-family="JetBrains Mono, monospace"
          font-size="9" fill="var(--text-3)">turn 1</text>
    <text x="${padL + innerW}" y="${h - 4}" text-anchor="end"
          font-family="JetBrains Mono, monospace" font-size="9"
          fill="var(--text-3)">turn ${n}</text>
  `;
  // y-axis: max label
  const yLabel = `
    <text x="${padL}" y="${padT + 8}" font-family="JetBrains Mono, monospace"
          font-size="9" fill="var(--text-3)">${fmtMs(yMax)}</text>
  `;

  return `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"
         xmlns="http://www.w3.org/2000/svg" class="lat-svg" role="img"
         aria-label="response latency per turn">
      ${bars}
      ${refLines.join("")}
      ${xAxis}
      ${yLabel}
    </svg>
  `;
}

function formatMetricValue(kind, v) {
  if (v == null) return "—";
  if (kind === "ttfb" || kind === "processing") return fmtMs(v);
  if (kind === "tts_usage") return fmtInt(Math.round(v));
  return v.toFixed(2);
}

// ─── Latency analysis (collector bundle + LLM synthesis) ────────────

const CAUSE_LABELS = {
  intro_cold_start: "intro · cold start",
  stall_watchdog: "stall watchdog",
  llm_jitter: "LLM jitter (provider-side)",
  context_bloat: "context bloat",
};

function causeLabel(c) {
  return CAUSE_LABELS[c] || c || "—";
}

// Render the stored analysis: { bundle, report, has_unexplained, model, generated_at }.
export function renderLatencyAnalysis(a) {
  if (!a || !a.report) return "";
  const r = a.report;
  const b = a.bundle || {};
  const ps = b.perceived_stats || {};
  const stage = b.stage_ttfb || [];

  const unexplained = (r.unexplained && r.unexplained.length)
    ? `
      <div class="la-flag">
        <div class="la-flag-title">⚠ Needs a closer look — outside the known buckets</div>
        <ul>${r.unexplained.map(u => `<li>${escapeHtml(u)}</li>`).join("")}</ul>
      </div>`
    : "";

  const statBlock = ps.count
    ? `
      <div class="lat-stats">
        ${[["turns", ps.count], ["median", fmtMs(ps.median)], ["mean", fmtMs(ps.avg)],
           ["p90", fmtMs(ps.p90)], ["max", fmtMs(ps.max)]].map(([k, v]) => `
          <div class="lat-stat">
            <div class="lat-stat-label">${k}</div>
            <div class="lat-stat-value">${typeof v === "number" ? v : v}</div>
          </div>`).join("")}
      </div>`
    : `<div class="empty" style="color:var(--text-2)">no real turns to summarize</div>`;

  const stageRows = stage.length
    ? stage.map(s => `
        <tr>
          <td>${shortProcessor(s.processor)}</td>
          <td class="num">${s.n}</td>
          <td class="num">${fmtMs(s.min)}</td>
          <td class="num">${fmtMs(s.avg)}</td>
          <td class="num">${fmtMs(s.max)}</td>
        </tr>`).join("")
    : `<tr><td colspan="5" class="empty">no ttfb metrics</td></tr>`;

  const outliers = (r.outliers && r.outliers.length)
    ? r.outliers.map(o => `
        <li>
          <span class="la-outlier-head">turn ${o.turn} · ${o.perceived_s != null ? fmtMs(o.perceived_s) : "—"}
            <span class="la-cause la-cause-${o.cause || "none"}">${causeLabel(o.cause)}</span></span>
          <span class="la-outlier-note">${escapeHtml(o.note || "")}</span>
        </li>`).join("")
    : `<li class="empty" style="color:var(--text-2)">no outliers — every turn within range</li>`;

  return `
    <div class="latency-analysis-panel">
      <div class="la-head">
        <h3>Latency analysis</h3>
        <span class="la-meta">${a.model || ""}${a.generated_at ? ` · ${fmtDate(a.generated_at)}` : ""}</span>
      </div>
      ${unexplained}
      <p class="la-headline">${escapeHtml(r.headline || "")}</p>
      <p class="la-main-cause">${escapeHtml(r.main_cause || "")}</p>
      <h4>Perceived latency · real turns (intro &amp; stall excluded)</h4>
      ${statBlock}
      <h4>Per-stage TTFB</h4>
      <table class="metrics-table">
        <thead><tr><th>Stage</th><th>n</th><th>min</th><th>avg</th><th>max</th></tr></thead>
        <tbody>${stageRows}</tbody>
      </table>
      <h4>Outliers</h4>
      <ul class="la-outliers">${outliers}</ul>
    </div>
  `;
}
