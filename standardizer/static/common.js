const $ = id => document.getElementById(id);
const FIELDS = ["transaction_id","merchant_name","merchant_category","transaction_date","transaction_time","amount","currency","card_network","card_last4","bank_name","payment_method","description","reference"];
let user = localStorage.getItem("user") || "";

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

async function api(url, opt = {}) {
  const r = await fetch(url, {...opt, headers: {"X-User": user, ...(opt.headers || {})}});
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch {} throw new Error(m); }
  return r.json();
}

// Shared header: nav + user + pending-review badge. `active` is "upload" or "review".
async function initHeader(active) {
  const us = await (await fetch("/api/users")).json();
  if (!us.includes(user)) user = us[0];
  document.querySelector("header").innerHTML = `
    <h1>Transaction Standardizer</h1>
    <nav><a href="/" class="${active === "upload" ? "on" : ""}">1. Upload</a>
         <a href="/review.html" class="${active === "review" ? "on" : ""}">2. Review &amp; Approve<span class="badge" id="pendingBadge" hidden></span></a></nav>
    <div class="sp"><label class="mute">User <select id="user">${us.map(u => `<option>${esc(u)}</option>`).join("")}</select></label>
      <button class="sec" onclick="location.href='/api/export.csv'">Export approved CSV</button></div>`;
  $("user").value = user;
  $("user").onchange = () => { user = $("user").value; localStorage.setItem("user", user); location.reload(); };
  try {
    const m = await api("/api/me");
    const bad = m.stages.filter(s => !s.ok), mock = m.stages.filter(s => s.mock);
    const msg = bad.length ? `${bad[0].error}. Fix config.json (or the environment variable it points to).`
      : mock.length ? `MOCK mode for: ${mock.map(s => s.provider).join(", ")} (no key for ${user}). Not real AI output.` : "";
    if (msg) document.querySelector("header").insertAdjacentHTML("afterend", `<div class="banner">${esc(msg)}</div>`);
  } catch {}
  refreshBadge();
}
async function refreshBadge() {
  try {
    const rs = await api("/api/records"), n = rs.filter(r => r.status === "pending").length;
    $("pendingBadge").textContent = n; $("pendingBadge").hidden = !n;
  } catch {}
}
