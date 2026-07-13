/* Shared Supabase auth boot for operator pages (dashboard + session view). */

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

// Boot Supabase, require a signed-in admin. Redirects to "/" and returns null
// if config is missing, the visitor is signed out, or not an admin.
// Returns { supabase, getToken } on success; getToken() yields the live token.
export async function bootAdmin() {
  let cfg;
  try {
    cfg = await (await fetch("/api/config")).json();
  } catch (e) {
    document.body.innerHTML = "<p style='padding:2rem'>could not load config</p>";
    return null;
  }
  if (!cfg.supabase_url || !cfg.supabase_anon_key) {
    document.body.innerHTML = "<p style='padding:2rem'>auth not configured</p>";
    return null;
  }
  const supabase = createClient(cfg.supabase_url, cfg.supabase_anon_key, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
      storageKey: "gradbot-auth",
    },
  });
  const { data: { session } } = await supabase.auth.getSession();
  if (!session) {
    window.location.href = "/";
    return null;
  }
  let token = session.access_token;

  // Verify admin via /api/me before showing anything.
  const meRes = await fetch("/api/me", {
    headers: { "Authorization": `Bearer ${token}` },
  });
  if (meRes.status === 401) {
    // Stale token. Clear it before bouncing home, or the landing page loads the
    // same dead session, 401s again, and shows the sign-in form with no clue why.
    console.warn("/api/me rejected the stored session; signing out");
    await supabase.auth.signOut();
    window.location.href = "/";
    return null;
  }
  if (!meRes.ok) {
    window.location.href = "/";
    return null;
  }
  const me = await meRes.json();
  if (!me.is_admin) {
    window.location.href = "/";
    return null;
  }

  supabase.auth.onAuthStateChange((_e, s) => {
    if (!s) window.location.href = "/";
    else token = s.access_token;
  });

  return { supabase, getToken: () => token };
}

// Build an authed JSON fetcher bound to a live-token getter.
export function makeGetJSON(getToken) {
  return async function getJSON(url) {
    const token = getToken();
    const headers = token ? { "Authorization": `Bearer ${token}` } : {};
    const r = await fetch(url, { headers });
    if (r.status === 401 || r.status === 403) {
      window.location.href = "/";
      throw new Error(`${url} → ${r.status}`);
    }
    if (!r.ok) throw new Error(`${url} → ${r.status}`);
    return r.json();
  };
}

// Build an authed JSON POST helper bound to a live-token getter.
export function makePostJSON(getToken) {
  return async function postJSON(url, body) {
    const token = getToken();
    const headers = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const r = await fetch(url, {
      method: "POST",
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (r.status === 401 || r.status === 403) {
      window.location.href = "/";
      throw new Error(`${url} → ${r.status}`);
    }
    if (!r.ok) throw new Error(`${url} → ${r.status}`);
    return r.json();
  };
}
