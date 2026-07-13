// Gradbot voice agent — landing page client.
//
// Auth: passwordless email sign-in. The browser posts the email to
// /api/auth/login; the server (holding the service-role key) mints a one-time
// token_hash without sending any email, which we redeem here via verifyOtp to
// establish a real Supabase session. Session is persisted by supabase-js in
// localStorage and auto-refreshed; we attach the access_token as a Bearer
// header to /start-session so the FastAPI side can attribute the call.
//
// Transport: a raw WebSocket to /ws/chat. SyncedAudioPlayer (loaded as globals
// from /static/js — gradbot ships it) owns the microphone, Opus-encodes what it
// captures, and plays back what the server sends.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const agentsEl = document.getElementById("agents");
const hangupBtn = document.getElementById("hangup-btn");
const statusEl = document.getElementById("status");

const authForm = document.getElementById("auth-form");
const authLoggedIn = document.getElementById("auth-logged-in");
const authEmailInput = document.getElementById("auth-email-input");
const authInviteInput = document.getElementById("auth-invite-input");
const authWaitlistEl = document.getElementById("auth-waitlist");
const authSubmitBtn = document.getElementById("auth-submit");
const authStatusEl = document.getElementById("auth-status");
const authUsernameEl = document.getElementById("auth-username");
const authDashboardLink = document.getElementById("auth-dashboard-link");
const authLogoutBtn = document.getElementById("auth-logout");

let ws = null;
let player = null;
let recording = false;
let supabase = null;
let currentUser = null;

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = `status ${cls}`;
  statusEl.classList.remove("hidden");
}

function setAuthStatus(text, cls = "idle") {
  authStatusEl.textContent = text || "";
  authStatusEl.className = `status ${cls}`;
}

function showAgents() {
  agentsEl.classList.remove("hidden");
  hangupBtn.classList.add("hidden");
}

function showHangup() {
  agentsEl.classList.add("hidden");
  hangupBtn.classList.remove("hidden");
}

function talkLabel(agent) {
  return agent.lang === "fr"
    ? "Discutons"
    : "Let's talk";
}

function setAgentButtonsDisabled(disabled) {
  agentsEl.querySelectorAll("button").forEach((b) => { b.disabled = disabled; });
}

async function loadAgents() {
  try {
    // Send the access token so the server can include admin-only agents for
    // admins; anonymous/non-admin callers get only public ones.
    const { data: { session } } = await supabase.auth.getSession();
    const headers = session ? { "Authorization": `Bearer ${session.access_token}` } : {};
    const r = await fetch("/agents", { headers });
    if (!r.ok) throw new Error(`status ${r.status}`);
    const { agents } = await r.json();
    renderAgents(agents);
  } catch (e) {
    console.error(e);
    agentsEl.textContent = "could not load agents";
  }
}

function renderAgents(agents) {
  agentsEl.innerHTML = "";
  if (!agents.length) {
    agentsEl.textContent = "no agents available";
    return;
  }
  for (const agent of agents) {
    const card = document.createElement("div");
    card.className = "agent-card";

    const name = document.createElement("h2");
    name.className = "agent-name";
    name.textContent = agent.name;
    card.appendChild(name);

    if (agent.description) {
      const desc = document.createElement("p");
      desc.className = "agent-desc";
      desc.textContent = agent.description;
      card.appendChild(desc);
    }

    const btn = document.createElement("button");
    btn.className = "primary talk-btn";
    btn.textContent = talkLabel(agent);
    btn.addEventListener("click", () => startSession(agent.id));
    card.appendChild(btn);

    const rows = [];
    if (agent.llm) rows.push(["Model", agent.llm]);
    if (agent.tts_provider) rows.push(["TTS", agent.tts_provider]);
    if (agent.lang) rows.push(["Lang", String(agent.lang).toUpperCase()]);
    rows.push(["Memory", agent.memory === false ? "off" : "on"]);
    if (rows.length) {
      const meta = document.createElement("dl");
      meta.className = "agent-meta";
      for (const [label, value] of rows) {
        const k = document.createElement("dt");
        k.textContent = label;
        const v = document.createElement("dd");
        v.textContent = value;
        meta.appendChild(k);
        meta.appendChild(v);
      }
      card.appendChild(meta);
    }

    agentsEl.appendChild(card);
  }
}

async function startSession(agentId) {
  setAgentButtonsDisabled(true);
  setStatus("starting", "connecting");

  const { data: { session } } = await supabase.auth.getSession();
  if (!session) {
    setStatus("please sign in first", "error");
    setAgentButtonsDisabled(false);
    renderAuthState(null);
    return;
  }

  let body;
  try {
    const r = await fetch("/start-session", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${session.access_token}`,
      },
      body: JSON.stringify({ agent: agentId }),
    });
    if (r.status === 401 || r.status === 403) {
      setStatus("session expired — please sign in again", "error");
      setAgentButtonsDisabled(false);
      await supabase.auth.signOut();
      return;
    }
    if (r.status === 429) {
      setStatus("the agent is busy — try again in a moment", "error");
      setAgentButtonsDisabled(false);
      return;
    }
    if (!r.ok) {
      setStatus(`server error (${r.status})`, "error");
      setAgentButtonsDisabled(false);
      return;
    }
    body = await r.json();
  } catch (e) {
    console.error(e);
    setStatus("cannot reach the server", "error");
    setAgentButtonsDisabled(false);
    return;
  }

  const { session_id, ws_url } = body;

  setStatus("connecting", "connecting");

  // Ask the server which codec it will send. gradbot can do raw PCM, but we run
  // its default (Ogg/Opus) — the player has to be told which decoder to spin up.
  let pcm = false;
  try {
    const cfg = await fetch("/api/audio-config").then((r) => r.json());
    pcm = Boolean(cfg.pcm);
  } catch (e) {
    console.warn("audio-config unavailable, assuming opus", e);
  }

  try {
    player = new SyncedAudioPlayer({
      basePath: "/static/js",
      sampleRate: 24000,
      pcmOutput: pcm,
      // Without this the agent hears its own voice through the speakers and
      // interrupts itself into a feedback loop. Daily did this for us; here it
      // is ours to remember.
      echoCancellation: true,
      onEncodedAudio: (opus) => {
        if (recording && ws?.readyState === WebSocket.OPEN) ws.send(opus);
      },
    });
    await player.start(); // prompts for microphone permission
  } catch (e) {
    console.error(e);
    setStatus("microphone unavailable", "error");
    cleanup();
    return;
  }

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}${ws_url}`);
  // Leave binaryType at its default of "blob". SyncedAudioPlayer dispatches on
  // `data instanceof Blob` to decide "this is audio"; set it to "arraybuffer"
  // and every audio frame falls through to the JSON branch, has no .type, and is
  // dropped without a word. The agent speaks and you hear nothing.

  ws.onopen = () => {
    // The socket is a fresh connection with none of the headers we authorized
    // the POST with, so it re-presents the token. session_id ties it back to the
    // slot /start-session reserved.
    ws.send(JSON.stringify({
      type: "start",
      session_id,
      access_token: session.access_token,
    }));
    recording = true;
    setStatus("connected", "connected");
    showHangup();
  };

  // Everything inbound — transcripts, audio timing, and the audio itself —
  // goes through the player; it decodes and schedules playback.
  ws.onmessage = (ev) => player?.handleMessage(ev.data);

  ws.onerror = (ev) => {
    console.error("websocket error", ev);
    setStatus("connection error", "error");
  };

  ws.onclose = (ev) => {
    // 4001 is the bridge rejecting us in on_start (bad token, expired
    // reservation, agent misconfigured); anything else is a normal hang-up.
    if (ev.code === 4001) {
      console.error("session rejected:", ev.reason);
      setStatus(`could not start: ${ev.reason || "rejected"}`, "error");
    } else {
      setStatus("ended", "idle");
    }
    cleanup();
  };
}

function endSession() {
  hangupBtn.disabled = true;
  recording = false;
  if (ws?.readyState === WebSocket.OPEN) {
    // Tell the server we're done so it can finalize the session row, rather
    // than making it wait for the socket to time out.
    ws.send(JSON.stringify({ type: "stop" }));
  }
  ws?.close();
}

function cleanup() {
  recording = false;
  if (ws) {
    try { ws.close(); } catch (_) {}
    ws = null;
  }
  if (player) {
    try { player.stop(); } catch (_) {}
    player = null;
  }
  showAgents();
  setAgentButtonsDisabled(false);
  hangupBtn.disabled = false;
}

hangupBtn.addEventListener("click", endSession);
window.addEventListener("beforeunload", () => {
  if (ws?.readyState === WebSocket.OPEN) {
    try { ws.send(JSON.stringify({ type: "stop" })); } catch (_) {}
  }
  try { ws?.close(); } catch (_) {}
});

// ─── Auth ───────────────────────────────────────────────────────────

async function fetchMe(session) {
  const r = await fetch("/api/me", {
    headers: { "Authorization": `Bearer ${session.access_token}` },
  });
  if (!r.ok) return null;
  return r.json();
}

function renderAuthState(me) {
  currentUser = me;
  authWaitlistEl.classList.add("hidden");
  if (me) {
    authForm.classList.add("hidden");
    authLoggedIn.classList.remove("hidden");
    authUsernameEl.textContent = me.username;
    authDashboardLink.classList.toggle("hidden", !me.is_admin);
    agentsEl.classList.remove("hidden");
    loadAgents();
  } else {
    authForm.classList.remove("hidden");
    authLoggedIn.classList.add("hidden");
    agentsEl.classList.add("hidden");
    setAuthStatus("");
  }
}

// Replace the whole sign-in form with a prominent waitlist confirmation.
function showWaitlist({ already, invalid_code }) {
  let msg;
  if (invalid_code) {
    msg = already
      ? "That invite code isn't valid. You're already on the beta waitlist — we'll be in touch."
      : "That invite code isn't valid, but you've joined the beta waitlist — we'll be in touch.";
  } else {
    msg = already
      ? "You're already on the beta waitlist — we'll be in touch."
      : "You're on the beta waitlist — we'll be in touch.";
  }
  authWaitlistEl.textContent = msg;
  authForm.classList.add("hidden");
  authWaitlistEl.classList.remove("hidden");
}

authForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = authEmailInput.value.trim();
  if (!email) return;
  const inviteCode = authInviteInput.value.trim();
  authSubmitBtn.disabled = true;
  setAuthStatus("connecting…", "connecting");
  try {
    const r = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, invite_code: inviteCode }),
    });
    if (r.status === 400) {
      let detail = "invalid_email";
      try { detail = (await r.json()).detail || detail; } catch (_) {}
      setAuthStatus(detail === "invalid_email" ? "that email looks off" : detail, "error");
      return;
    }
    if (r.status === 403) {
      setAuthStatus(
        "This is a private beta. Contact the website owner for an invite.",
        "error",
      );
      return;
    }
    if (!r.ok) {
      let detail = `error (${r.status})`;
      try { detail = (await r.json()).detail || detail; } catch (_) {}
      setAuthStatus(detail, "error");
      return;
    }
    const data = await r.json();
    if (data.status === "waitlisted") {
      showWaitlist(data);
      return;
    }
    const { token_hash, type } = data;
    const { error } = await supabase.auth.verifyOtp({ token_hash, type });
    if (error) {
      setAuthStatus(error.message, "error");
      return;
    }
    // onAuthStateChange takes over from here (fetchMe + renderAuthState).
    setAuthStatus("", "idle");
  } catch (err) {
    console.error(err);
    setAuthStatus("could not reach the auth server", "error");
  } finally {
    authSubmitBtn.disabled = false;
  }
});

authLogoutBtn.addEventListener("click", async () => {
  await supabase.auth.signOut();
});

async function bootAuth() {
  let cfg;
  try {
    cfg = await (await fetch("/api/config")).json();
  } catch (e) {
    console.error(e);
    setAuthStatus("could not load config", "error");
    return;
  }
  if (!cfg.supabase_url || !cfg.supabase_anon_key) {
    setAuthStatus("auth not configured on the server", "error");
    return;
  }
  supabase = createClient(cfg.supabase_url, cfg.supabase_anon_key, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
      storageKey: "gradbot-auth",
    },
  });

  const { data: { session } } = await supabase.auth.getSession();
  if (session) {
    const me = await fetchMe(session);
    renderAuthState(me);
  } else {
    renderAuthState(null);
  }

  supabase.auth.onAuthStateChange(async (_event, session) => {
    if (session) {
      const me = await fetchMe(session);
      renderAuthState(me);
    } else {
      renderAuthState(null);
    }
  });
}

bootAuth();
