"""Single-file HTML/CSS/JS for the dashboard. No external dependencies."""

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>KNET Reconciler</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root {
    --bg: #f7f7f8; --card: #fff; --text: #1a1a1a; --muted: #6b6b6b;
    --border: #e3e3e6; --hover: #f2f2f5; --accent: #2954c4; --accent-hover: #1f4399;
    --missing: #c0392b; --pending: #b07d00; --matched: #1f7a3a; --manual: #2954c4;
    --orphan: #7a4b9e; --danger: #c0392b;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
    background: var(--bg); color: var(--text); font-size: 14px;
  }
  header {
    padding: 16px 24px; background: var(--card); border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 16px; justify-content: space-between;
  }
  header h1 { margin: 0; font-size: 18px; font-weight: 600; }
  header .actions { display: flex; gap: 8px; }

  button, .btn {
    background: var(--card); border: 1px solid var(--border); color: var(--text);
    padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 13px;
    font-family: inherit; line-height: 1.4;
  }
  button:hover, .btn:hover { background: var(--hover); }
  button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
  button.primary:hover { background: var(--accent-hover); border-color: var(--accent-hover); }
  button:disabled { opacity: 0.5; cursor: not-allowed; }

  main { padding: 16px 24px; }

  .chips { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
  .chip {
    padding: 6px 12px; border-radius: 999px; border: 1px solid var(--border);
    background: var(--card); cursor: pointer; font-size: 13px; user-select: none;
  }
  .chip:hover { background: var(--hover); }
  .chip.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  .chip .count { opacity: 0.7; margin-left: 6px; font-variant-numeric: tabular-nums; }
  .chip.active .count { opacity: 1; }

  .toolbar {
    display: flex; gap: 8px; align-items: center; margin-bottom: 12px;
  }
  .toolbar input[type="search"] {
    padding: 6px 10px; border-radius: 6px; border: 1px solid var(--border);
    font-size: 13px; min-width: 240px; font-family: inherit;
  }

  .bulk-bar {
    position: sticky; top: 0; z-index: 5;
    background: #fef9e6; border: 1px solid #f0d75a;
    padding: 10px 12px; border-radius: 8px;
    display: flex; gap: 8px; align-items: center; margin-bottom: 12px;
  }
  .bulk-bar.hidden { display: none; }
  .bulk-bar input[type="text"] {
    flex: 1; padding: 6px 10px; border-radius: 6px; border: 1px solid var(--border);
    font-size: 13px; font-family: inherit;
  }

  table {
    width: 100%; border-collapse: collapse; background: var(--card);
    border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
    font-variant-numeric: tabular-nums;
  }
  th, td {
    padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--border);
    vertical-align: top;
  }
  thead th {
    background: #fafafb; font-weight: 600; font-size: 12px;
    text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted);
    position: sticky; top: 0;
  }
  tbody tr:hover { background: var(--hover); }
  td.num { text-align: right; }
  td.mono { font-family: ui-monospace, "Cascadia Mono", "Consolas", monospace; font-size: 12px; }
  td.checkbox { width: 24px; text-align: center; }
  td .meta { color: var(--muted); font-size: 12px; }

  .pill {
    display: inline-block; padding: 2px 8px; border-radius: 999px;
    font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
  }
  .pill.missing { background: #fbe6e3; color: var(--missing); }
  .pill.pending { background: #fbf1cf; color: var(--pending); }
  .pill.matched { background: #ddf2e4; color: var(--matched); }
  .pill.manually_resolved { background: #dfe6f7; color: var(--manual); }
  .pill.orphan { background: #ebdef4; color: var(--orphan); }

  .row-actions { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
  .row-actions input[type="text"] {
    padding: 4px 8px; border-radius: 4px; border: 1px solid var(--border);
    font-size: 12px; min-width: 180px; font-family: inherit;
  }
  a.link { color: var(--accent); text-decoration: none; font-size: 12px; }
  a.link:hover { text-decoration: underline; }

  .empty { padding: 32px; text-align: center; color: var(--muted); }
  .toast {
    position: fixed; bottom: 16px; right: 16px; padding: 10px 14px;
    background: #1a1a1a; color: #fff; border-radius: 6px; font-size: 13px;
    opacity: 0; transition: opacity 0.2s; pointer-events: none; max-width: 360px;
  }
  .toast.show { opacity: 1; }
  .toast.error { background: var(--danger); }
</style>
</head>
<body>

<header>
  <h1>KNET Reconciler</h1>
  <div class="actions">
    <button id="refresh-btn">Refresh</button>
    <button id="label-btn" class="primary">Apply KNET-Missing labels</button>
  </div>
</header>

<main>
  <div class="chips" id="chips"></div>

  <div class="toolbar">
    <input type="search" id="search" placeholder="Search retailer, order #, tracking, SKU...">
  </div>

  <div class="bulk-bar hidden" id="bulk-bar">
    <strong id="bulk-count">0 selected</strong>
    <input type="text" id="bulk-note" placeholder="Optional note (e.g. KNET support confirmed 2026-05-28)">
    <button class="primary" id="bulk-resolve-btn">Resolve selected</button>
    <button id="bulk-clear-btn">Clear selection</button>
  </div>

  <div id="table-wrap"></div>
</main>

<div class="toast" id="toast"></div>

<script>
const STATUSES = ["all", "missing", "pending", "matched", "manually_resolved", "orphans"];
const STATUS_LABEL = {
  all: "All", missing: "Missing", pending: "Pending", matched: "Matched",
  manually_resolved: "Manually resolved", orphans: "Orphans",
};

let state = {
  shipments: [], orphans: [], counts: {},
  filter: "missing",          // default to the actionable bucket
  search: "",
  selected: new Set(),        // shipment IDs
};

function toast(msg, isError) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.toggle("error", !!isError);
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2500);
}

async function fetchData() {
  const r = await fetch("/api/data");
  const data = await r.json();
  state.shipments = data.shipments;
  state.orphans = data.orphans;
  state.counts = data.counts;
  render();
}

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

function fmtDate(iso) {
  if (!iso) return "";
  return iso.slice(0, 10);
}

function fmtPrice(p, c) {
  if (p == null) return "";
  const sym = c === "USD" ? "$" : (c || "");
  return `${sym}${Number(p).toFixed(2)}`;
}

function visibleShipments() {
  let rows = state.shipments;
  if (state.filter !== "all" && state.filter !== "orphans") {
    rows = rows.filter(r => r.status === state.filter);
  }
  if (state.search) {
    const q = state.search.toLowerCase();
    rows = rows.filter(r =>
      (r.retailer || "").toLowerCase().includes(q) ||
      (r.order_number || "").toLowerCase().includes(q) ||
      (r.tracking_number || "").toLowerCase().includes(q) ||
      (r.sku || "").toLowerCase().includes(q) ||
      (r.item_description || "").toLowerCase().includes(q) ||
      (r.email_subject || "").toLowerCase().includes(q)
    );
  }
  return rows;
}

function visibleOrphans() {
  let rows = state.orphans;
  if (state.search) {
    const q = state.search.toLowerCase();
    rows = rows.filter(r =>
      (r.tracking_number || "").toLowerCase().includes(q) ||
      (r.sku || "").toLowerCase().includes(q) ||
      (r.email_subject || "").toLowerCase().includes(q)
    );
  }
  return rows;
}

function renderChips() {
  const c = state.counts;
  const chipDefs = [
    ["all", c.all], ["missing", c.missing], ["pending", c.pending],
    ["matched", c.matched], ["manually_resolved", c.manually_resolved], ["orphans", c.orphans],
  ];
  document.getElementById("chips").innerHTML = chipDefs.map(([key, n]) => `
    <div class="chip ${key === state.filter ? "active" : ""}" data-key="${key}">
      ${STATUS_LABEL[key]}<span class="count">${n ?? 0}</span>
    </div>
  `).join("");
  document.querySelectorAll(".chip").forEach(el => {
    el.onclick = () => {
      state.filter = el.dataset.key;
      state.selected.clear();
      render();
    };
  });
}

function renderBulkBar() {
  const bar = document.getElementById("bulk-bar");
  const n = state.selected.size;
  if (n === 0) { bar.classList.add("hidden"); return; }
  bar.classList.remove("hidden");
  document.getElementById("bulk-count").textContent = `${n} selected`;
}

function renderTable() {
  const wrap = document.getElementById("table-wrap");
  if (state.filter === "orphans") {
    renderOrphansTable(wrap);
  } else {
    renderShipmentsTable(wrap);
  }
}

function renderShipmentsTable(wrap) {
  const rows = visibleShipments();
  if (rows.length === 0) {
    wrap.innerHTML = `<div class="empty">No ${state.filter === "all" ? "" : state.filter + " "}shipments.</div>`;
    return;
  }
  const isResolvable = (s) => s.status === "missing" || s.status === "pending";
  const head = `
    <thead><tr>
      <th class="checkbox"><input type="checkbox" id="select-all"></th>
      <th>Status</th><th>Retailer</th><th>Order #</th><th>Ship date</th>
      <th>Tracking</th><th>Item / SKU</th><th>Price</th><th>Actions</th>
    </tr></thead>`;
  const body = rows.map(s => `
    <tr data-id="${s.id}">
      <td class="checkbox">
        ${isResolvable(s) ? `<input type="checkbox" class="row-check" data-id="${s.id}"
            ${state.selected.has(s.id) ? "checked" : ""}>` : ""}
      </td>
      <td><span class="pill ${s.status}">${STATUS_LABEL[s.status] || s.status}</span></td>
      <td>${escapeHtml(s.retailer || "")}</td>
      <td class="mono">${escapeHtml(s.order_number || "")}</td>
      <td>${fmtDate(s.ship_date)}</td>
      <td class="mono">${escapeHtml(s.tracking_number || "")}<div class="meta">${escapeHtml(s.carrier || "")}</div></td>
      <td>${escapeHtml(s.item_description || "")}<div class="meta">${escapeHtml(s.sku || "")} ${s.size ? "&middot; " + escapeHtml(s.size) : ""}</div></td>
      <td class="num">${escapeHtml(fmtPrice(s.price, s.currency))}</td>
      <td>${renderRowActions(s)}</td>
    </tr>
    ${s.note || s.resolved_at ? `<tr class="meta-row"><td colspan="9" class="meta" style="padding-left:34px">
        ${s.resolved_at ? "Resolved " + fmtDate(s.resolved_at) : ""}
        ${s.note ? "&middot; " + escapeHtml(s.note) : ""}
    </td></tr>` : ""}
  `).join("");
  wrap.innerHTML = `<table>${head}<tbody>${body}</tbody></table>`;
  wireRowEvents();
}

function renderRowActions(s) {
  const parts = [];
  if (s.gmail_url) {
    parts.push(`<a class="link" href="${s.gmail_url}" target="_blank" rel="noopener">Open email &#x2197;</a>`);
  }
  if (s.status === "missing" || s.status === "pending") {
    parts.push(`<button class="resolve-btn" data-id="${s.id}">Resolve</button>`);
  } else if (s.status === "manually_resolved") {
    parts.push(`<button class="unresolve-btn" data-id="${s.id}">Undo</button>`);
  }
  return `<div class="row-actions">${parts.join("")}</div>`;
}

function renderOrphansTable(wrap) {
  const rows = visibleOrphans();
  if (rows.length === 0) {
    wrap.innerHTML = `<div class="empty">No orphan receipts.</div>`;
    return;
  }
  const head = `
    <thead><tr>
      <th>Received</th><th>Tracking</th><th>Carrier</th><th>SKU</th><th>Email subject</th><th>Actions</th>
    </tr></thead>`;
  const body = rows.map(r => `
    <tr data-id="${r.id}">
      <td>${fmtDate(r.received_at)}</td>
      <td class="mono">${escapeHtml(r.tracking_number || "")}</td>
      <td>${escapeHtml(r.carrier || "")}</td>
      <td class="mono">${escapeHtml(r.sku || "")}</td>
      <td>${escapeHtml(r.email_subject || "")}</td>
      <td>${r.gmail_url ? `<a class="link" href="${r.gmail_url}" target="_blank" rel="noopener">Open email &#x2197;</a>` : ""}</td>
    </tr>
  `).join("");
  wrap.innerHTML = `<table>${head}<tbody>${body}</tbody></table>`;
}

function wireRowEvents() {
  document.querySelectorAll(".row-check").forEach(cb => {
    cb.onchange = () => {
      const id = parseInt(cb.dataset.id, 10);
      if (cb.checked) state.selected.add(id); else state.selected.delete(id);
      renderBulkBar();
    };
  });
  const selAll = document.getElementById("select-all");
  if (selAll) {
    selAll.onchange = () => {
      const checked = selAll.checked;
      document.querySelectorAll(".row-check").forEach(cb => {
        cb.checked = checked;
        const id = parseInt(cb.dataset.id, 10);
        if (checked) state.selected.add(id); else state.selected.delete(id);
      });
      renderBulkBar();
    };
  }
  document.querySelectorAll(".resolve-btn").forEach(btn => {
    btn.onclick = async () => {
      const id = parseInt(btn.dataset.id, 10);
      const note = prompt("Resolution note (optional):", "") || "";
      btn.disabled = true;
      const r = await postJSON(`/api/shipments/${id}/resolve`, {note: note.trim()});
      if (r.ok) { toast(`Marked #${id} resolved.`); await fetchData(); }
      else { toast("Failed: " + (r.error || "unknown"), true); btn.disabled = false; }
    };
  });
  document.querySelectorAll(".unresolve-btn").forEach(btn => {
    btn.onclick = async () => {
      const id = parseInt(btn.dataset.id, 10);
      if (!confirm("Undo manual resolution? It will be re-classified on the next reconcile run.")) return;
      btn.disabled = true;
      const r = await postJSON(`/api/shipments/${id}/unresolve`, {});
      if (r.ok) { toast(`Undo #${id}.`); await fetchData(); }
      else { toast("Failed: " + (r.error || "unknown"), true); btn.disabled = false; }
    };
  });
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function render() {
  renderChips();
  renderBulkBar();
  renderTable();
}

document.getElementById("search").addEventListener("input", (e) => {
  state.search = e.target.value;
  renderTable();
});
document.getElementById("refresh-btn").onclick = fetchData;
document.getElementById("label-btn").onclick = async () => {
  const btn = document.getElementById("label-btn");
  btn.disabled = true;
  const r = await postJSON("/api/label-missing", {});
  btn.disabled = false;
  if (r.ok) {
    toast(`Labelled ${r.added} new, ${r.skipped} already-tagged${r.message ? ": " + r.message : ""}.`);
  } else {
    toast("Label failed: " + (r.error || "unknown"), true);
  }
};
document.getElementById("bulk-resolve-btn").onclick = async () => {
  const note = document.getElementById("bulk-note").value.trim();
  const ids = Array.from(state.selected);
  if (ids.length === 0) return;
  const r = await postJSON("/api/shipments/bulk-resolve", {ids, note});
  if (r.ok) {
    toast(`Resolved ${r.count}.`);
    state.selected.clear();
    document.getElementById("bulk-note").value = "";
    await fetchData();
  } else {
    toast("Failed: " + (r.error || "unknown"), true);
  }
};
document.getElementById("bulk-clear-btn").onclick = () => {
  state.selected.clear();
  render();
};

fetchData();
</script>
</body>
</html>
"""
