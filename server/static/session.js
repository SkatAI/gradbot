/* Gradbot — single call detail page (/sessions/{id}). */

import { bootAdmin, makeGetJSON, makePostJSON } from "/auth.js";
import { renderDetail, renderLatencyAnalysis, escapeHtml } from "/format.js";

// Pull the session id from the /sessions/{id} path.
function sessionIdFromPath() {
  const m = window.location.pathname.match(/\/sessions\/([^/?#]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

async function boot() {
  const auth = await bootAdmin();
  if (!auth) return;
  const getJSON = makeGetJSON(auth.getToken);
  const postJSON = makePostJSON(auth.getToken);

  const id = sessionIdFromPath();
  const detail = document.getElementById("detail");
  const laBox = document.getElementById("latency-analysis");
  const laBtn = document.getElementById("latency-btn");
  if (!id) {
    detail.innerHTML = `<div class="agg-placeholder">No session id in the URL.</div>`;
    return;
  }

  // Render a stored analysis (if any) and set the button label accordingly.
  function showAnalysis(analysis) {
    if (analysis) {
      laBox.innerHTML = renderLatencyAnalysis(analysis);
      laBtn.textContent = "Regenerate analysis";
    } else {
      laBtn.textContent = "Latency analysis";
    }
  }

  // Generate (or regenerate) on click — one collector pass + one LLM call.
  async function runAnalysis() {
    laBtn.disabled = true;
    const prev = laBtn.textContent;
    laBtn.textContent = "Analyzing…";
    try {
      const analysis = await postJSON(`/api/sessions/${encodeURIComponent(id)}/latency-analysis`);
      showAnalysis(analysis);
    } catch (e) {
      laBox.innerHTML = `<div class="latency-analysis-panel"><div class="la-flag">Analysis failed · ${escapeHtml(e.message)}</div></div>`;
      laBtn.textContent = prev;
    } finally {
      laBtn.disabled = false;
    }
  }
  laBtn.addEventListener("click", runAnalysis);

  try {
    const data = await getJSON(`/api/sessions/${encodeURIComponent(id)}`);
    detail.innerHTML = renderDetail(data);
    showAnalysis(data.latency_analysis);
    laBtn.disabled = false;
    const name = (data.persona && data.persona.name) || data.persona_name;
    if (name) {
      document.getElementById("page-title").textContent = name;
      document.title = `Gradbot · ${name}`;
    }
    document.getElementById("page-subtitle").textContent =
      `Session ${id}`;
  } catch (e) {
    detail.innerHTML = `<div class="agg-placeholder">Could not load · ${escapeHtml(e.message)}</div>`;
  }
}

boot();
