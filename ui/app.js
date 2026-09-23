/* DaRe — Data Restructurer console UI. Vanilla JS, no build step. */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  schemas: [],
  file: null,
  plan: null,
  sheets: [],
};

// ── helpers ──────────────────────────────────────────────────────────────────
async function api(path, options) {
  const resp = await fetch(path, options);
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch (_) { /* keep status text */ }
    throw new Error(detail);
  }
  return resp;
}

function fileData() {
  if (!state.file) throw new Error("choose a workbook first");
  const fd = new FormData();
  fd.append("file", state.file);
  return fd;
}

function renderTable(el, columns, rows) {
  el.innerHTML = "";
  if (!columns || !columns.length) return;
  const thead = document.createElement("thead");
  const trh = document.createElement("tr");
  for (const c of columns) {
    const th = document.createElement("th");
    th.textContent = c;
    trh.appendChild(th);
  }
  thead.appendChild(trh);
  el.appendChild(thead);
  const tbody = document.createElement("tbody");
  for (const row of rows || []) {
    const tr = document.createElement("tr");
    for (const c of columns) {
      const td = document.createElement("td");
      const v = row[columns.indexOf(c)];
      td.textContent = v === null || v === undefined ? "" : String(v);
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  el.appendChild(tbody);
}

function setStatus(el, text, ok) {
  el.textContent = text || "";
  el.className = "status" + (ok === true ? " ok" : ok === false ? " err" : "");
}

// ── tabs ─────────────────────────────────────────────────────────────────────
function activateTab(which) {
  const isExtract = which === "extract";
  $("tab-extract").classList.toggle("active", isExtract);
  $("tab-excel").classList.toggle("active", !isExtract);
  $("panel-extract").classList.toggle("active", isExtract);
  $("panel-excel").classList.toggle("active", !isExtract);
}
$("tab-extract").addEventListener("click", () => activateTab("extract"));
$("tab-excel").addEventListener("click", () => activateTab("excel"));

// ── health + schemas ─────────────────────────────────────────────────────────
async function loadHealth() {
  const el = $("health");
  try {
    const resp = await api("/api/health");
    const body = await resp.json();
    el.textContent = `model: ${body.model} · schemas: ${body.schemas.join(", ") || "none"}`;
    el.className = "health ok";
    state.schemas = body.schemas;
    const sel = $("schema");
    sel.innerHTML = "";
    for (const s of body.schemas) {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s;
      sel.appendChild(opt);
    }
  } catch (err) {
    el.textContent = `offline: ${err.message}`;
    el.className = "health err";
  }
}

// ── extract panel ────────────────────────────────────────────────────────────
$("src-kind").addEventListener("change", () => {
  const kind = $("src-kind").value;
  $("src-url-row").hidden = kind !== "url";
  $("src-html-row").hidden = kind !== "html";
  $("src-pdf-row").hidden = kind !== "pdf";
});

$("btn-extract").addEventListener("click", async () => {
  const status = $("extract-status");
  const resultEl = $("extract-result");
  const kind = $("src-kind").value;
  const schema = $("schema").value;
  const payload = { schema_name: schema, include_audit: $("include-audit").checked };
  try {
    let resp;
    if (kind === "url") {
      payload.url = $("src-url").value.trim();
      if (!payload.url) throw new Error("enter a URL");
      setStatus(status, "extracting…");
      resp = await api("/extract", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } else if (kind === "html") {
      payload.html = $("src-html").value;
      if (!payload.html.trim()) throw new Error("paste HTML first");
      setStatus(status, "extracting…");
      resp = await api("/extract", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } else {
      const f = $("src-pdf").files[0];
      if (!f) throw new Error("choose a PDF file");
      const fd = new FormData();
      fd.append("file", f);
      fd.append("schema_name", schema);
      fd.append("include_audit", String($("include-audit").checked));
      setStatus(status, "extracting…");
      resp = await api("/extract/upload", { method: "POST", body: fd });
    }
    const body = await resp.json();
    const prov = body.provenance || {};
    setStatus(
      status,
      `ok · ${prov.attempt_count} attempt(s)${prov.cache_hit ? " · cache hit" : ""} · run ${prov.run_id}`,
      true
    );
    resultEl.hidden = false;
    resultEl.textContent = JSON.stringify(body.data, null, 2);
  } catch (err) {
    setStatus(status, err.message, false);
    resultEl.hidden = true;
  }
});

// ── excel panel ──────────────────────────────────────────────────────────────
$("xl-file").addEventListener("change", async (ev) => {
  state.file = ev.target.files[0] || null;
  $("btn-plan").disabled = !state.file;
  $("btn-apply").disabled = true;
  $("btn-download").disabled = true;
  setStatus($("xl-status"), state.file ? `${state.file.name} ready` : "");
  if (!state.file) return;
  // populate sheet dropdown by parsing headers client-side is not possible;
  // the plan endpoint reports sheet names, so we reset to "(first)".
  $("xl-sheet").innerHTML = '<option value="">(first)</option>';
});

async function runPlan() {
  const status = $("xl-status");
  const fd = fileData();
  const target = $("xl-target").value.trim();
  if (!target) throw new Error("describe the target layout first");
  fd.append("target", target);
  const model = $("xl-model").value.trim();
  if (model) fd.append("model", model);
  const sheet = $("xl-sheet").value;
  if (sheet) fd.append("sheet", sheet);
  setStatus(status, "generating plan…");
  const resp = await api("/api/restructure/plan", { method: "POST", body: fd });
  return resp.json();
}

$("btn-plan").addEventListener("click", async () => {
  const status = $("xl-status");
  try {
    const body = await runPlan();
    state.plan = body.plan;
    state.sheets = body.sheets || [];
    $("xl-plan").value = JSON.stringify(body.plan, null, 2);
    $("xl-steps").innerHTML = "<ul>" + (body.steps || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("") + "</ul>";
    $("xl-plan-wrap").hidden = false;
    $("xl-preview-wrap").hidden = false;
    renderTable($("xl-preview"), body.preview.columns, body.preview.rows);
    const st = body.preview.stats;
    $("xl-stats").textContent =
      `${st.rows_in} → ${st.rows_out} rows · ${st.columns_in} → ${st.columns_out} cols` +
      (st.dropped_rows ? ` · ${st.dropped_rows} empty dropped` : "") +
      (st.deduped_rows ? ` · ${st.deduped_rows} dupes removed` : "") +
      (st.filtered_rows ? ` · ${st.filtered_rows} filtered` : "");
    // sheet selector now that we know the sheet names
    const sel = $("xl-sheet");
    const current = sel.value;
    sel.innerHTML = '<option value="">(first)</option>';
    for (const s of state.sheets) {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s;
      sel.appendChild(opt);
    }
    sel.value = current;
    $("btn-apply").disabled = false;
    $("btn-download").disabled = false;
    setStatus(status, "plan ready — edit the JSON if needed, then Apply or Download", true);
  } catch (err) {
    setStatus(status, err.message, false);
  }
});

async function applyCurrent() {
  const status = $("xl-status");
  let plan;
  try {
    plan = JSON.parse($("xl-plan").value);
  } catch (err) {
    throw new Error(`plan JSON invalid: ${err.message}`);
  }
  const fd = fileData();
  fd.append("plan", JSON.stringify(plan));
  const sheet = $("xl-sheet").value;
  if (sheet) fd.append("sheet", sheet);
  setStatus(status, "applying plan…");
  const resp = await api("/api/restructure/apply", { method: "POST", body: fd });
  return resp.json();
}

$("btn-apply").addEventListener("click", async () => {
  const status = $("xl-status");
  try {
    const body = await applyCurrent();
    renderTable($("xl-preview"), body.columns, body.rows.slice(0, 100));
    const st = body.stats;
    $("xl-stats").textContent = `${st.rows_in} → ${st.rows_out} rows · ${st.columns_out} cols`;
    setStatus(status, `applied · ${body.rows.length} rows`, true);
  } catch (err) {
    setStatus(status, err.message, false);
  }
});

$("btn-download").addEventListener("click", async () => {
  const status = $("xl-status");
  try {
    let plan;
    try {
      plan = JSON.parse($("xl-plan").value);
    } catch (err) {
      throw new Error(`plan JSON invalid: ${err.message}`);
    }
    const fd = fileData();
    fd.append("plan", JSON.stringify(plan));
    const sheet = $("xl-sheet").value;
    if (sheet) fd.append("sheet", sheet);
    setStatus(status, "building workbook…");
    const resp = await api("/api/restructure/download", { method: "POST", body: fd });
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "restructured.xlsx";
    a.click();
    URL.revokeObjectURL(url);
    setStatus(status, "downloaded restructured.xlsx", true);
  } catch (err) {
    setStatus(status, err.message, false);
  }
});

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

loadHealth();
