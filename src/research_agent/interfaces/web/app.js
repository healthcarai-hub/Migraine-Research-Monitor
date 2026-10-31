"use strict";
// The Curt web page. It only talks to the API of the server that served it, and builds every element from text nodes
// (never from HTML strings), so nothing a paper contains can run as script.
const $ = (id) => document.getElementById(id);
const PAGE = 20;
let offset = 0, loadedConfig = null, filtersLoaded = false, memToken = "";

const token = () => { try { return sessionStorage.getItem("token") || memToken; } catch (e) { return memToken; } };
function saveToken(v) { memToken = v; try { sessionStorage.setItem("token", v); } catch (e) { /* private window: memory only */ } }

function say(text, kind) { const m = $("msg"); m.textContent = text || ""; m.className = kind || ""; }

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) { if (v !== null && v !== undefined) e.setAttribute(k, v); }
  for (const k of kids) { if (k !== null && k !== undefined) e.append(k.nodeType ? k : document.createTextNode(String(k))); }
  return e;
}

function safeUrl(u) {
  try { const x = new URL(u); return x.protocol === "https:" || x.protocol === "http:" ? x.href : null; } catch (e) { return null; }
}

async function api(path, opts) {
  const o = Object.assign({}, opts || {});
  o.headers = Object.assign({ Authorization: "Bearer " + token() }, o.headers || {});
  const r = await fetch(path, o);
  let body = null;
  try { body = await r.json(); } catch (e) { /* not json */ }
  if (!r.ok) {
    const d = body && body.detail;
    const detail = d ? (typeof d === "string" ? d : d.map((x) => (x.loc || []).slice(1).join(".") + ": " + x.msg).join("; ")) : r.statusText;
    const err = new Error(detail); err.status = r.status; throw err;
  }
  return body;
}

function fail(e) {
  if (e.status === 401) say("That token was not accepted. Paste a valid access token above.", "error");
  else if (e.status === 403) say("This token can read but not change the newsletter settings. Use an admin token.", "error");
  else say(e.message || "Something went wrong.", "error");
}

// ---------------------------------------------------------------- tabs
function showTab(name) {
  say("");
  for (const t of ["search", "editions", "settings"]) {
    $("tab-" + t).setAttribute("aria-selected", String(t === name));
    $("panel-" + t).classList.toggle("hidden", t !== name);
  }
  if (name === "settings") loadConfig();
  if (name === "editions") loadEditions();
}
$("tab-search").addEventListener("click", () => showTab("search"));
$("tab-editions").addEventListener("click", () => showTab("editions"));
$("tab-settings").addEventListener("click", () => showTab("settings"));
$("tokenform").addEventListener("submit", (ev) => {
  ev.preventDefault();
  saveToken($("token").value.trim()); $("token").value = ""; filtersLoaded = false; say("Token saved for this tab.", "ok");
  runSearch(0);
});

// ---------------------------------------------------------------- search
async function loadFilters() {
  if (filtersLoaded) return;
  const f = await api("/v1/papers/filters");
  const dl = $("journals"); dl.replaceChildren();
  for (const j of f.followed_journals) dl.append(el("option", { value: j.key }, j.name));
  for (const j of f.journals_in_database.slice(0, 80)) dl.append(el("option", { value: j.name }));
  const sel = $("researcher"); sel.replaceChildren(el("option", { value: "" }, "Anyone"));
  for (const r of f.researchers) sel.append(el("option", { value: r.key }, r.name + " (" + r.papers + ")"));
  filtersLoaded = true;
}

function params(off) {
  const p = new URLSearchParams();
  const add = (k, v) => { if (v) p.append(k, v); };
  add("q", $("q").value.trim());
  for (const a of $("author").value.split(",")) add("author", a.trim());
  for (const j of $("journal").value.split(",")) add("journal", j.trim());
  add("researcher", $("researcher").value);
  add("published_from", $("from").value); add("published_to", $("to").value);
  add("has_full_text", $("fulltext").value);
  p.set("sort", $("sort").value); p.set("direction", $("direction").value);
  p.set("limit", String(PAGE)); p.set("offset", String(off));
  return p;
}

function card(p, showScore) {
  const href = safeUrl(p.url);
  const title = href ? el("a", { href, target: "_blank", rel: "noopener noreferrer" }, p.title || "(untitled)") : document.createTextNode(p.title || "(untitled)");
  const meta = [p.journal, p.authors, p.published].filter(Boolean).join(" · ");
  const c = el("article", { class: "paper" }, el("h3", {}, title), el("div", { class: "meta" }, meta));
  if (p.full_text && p.full_text.available) c.append(el("span", { class: "badge ft" }, "Full text held"));
  if (p.preprint) c.append(el("span", { class: "badge pre" }, "Preprint"));
  if (p.retracted) c.append(el("span", { class: "badge retracted" }, "Retracted"));
  for (const w of p.watched || []) c.append(el("span", { class: "badge followed" }, "Followed " + w.type + ": " + w.name));
  if (showScore) c.append(el("span", { class: "badge" }, "Relevance " + p.relevance));
  if (p.summary) c.append(el("p", {}, p.summary));
  if (p.full_text && p.full_text.available) {
    const d = el("details", {}, el("summary", {}, "Read full text (" + p.full_text.words + " words)"));
    const box = el("div", { class: "fulltext" });
    let loaded = false;
    d.addEventListener("toggle", async () => {
      if (!d.open || loaded) return;
      loaded = true; box.textContent = "Loading…";
      try {
        const r = await fetch("/v1/papers/" + p.id + "/fulltext?format=text", { headers: { Authorization: "Bearer " + token() } });
        box.textContent = r.ok ? await r.text() : "Could not load the full text.";
      } catch (e) { box.textContent = "Could not load the full text."; }
    });
    d.append(box); c.append(d);
  }
  return c;
}

async function runSearch(off) {
  offset = off;
  if (!token()) { say("Paste your access token to begin."); return; }
  try {
    await loadFilters();
    const d = await api("/v1/papers?" + params(off).toString());
    const list = $("results"); list.replaceChildren();
    const rel = $("sort").value === "relevance";
    for (const p of d.items) list.append(card(p, rel));
    $("count").textContent = d.total === 0 ? "No papers match." : d.total + " paper" + (d.total === 1 ? "" : "s") + " match.";
    const pages = Math.max(1, Math.ceil(d.total / PAGE));
    $("page").textContent = "Page " + (Math.floor(off / PAGE) + 1) + " of " + pages;
    $("prev").disabled = off === 0; $("next").disabled = off + PAGE >= d.total;
    $("pager").classList.toggle("hidden", d.total <= PAGE);
  } catch (e) { fail(e); }
}
$("searchform").addEventListener("submit", (ev) => { ev.preventDefault(); say(""); runSearch(0); });
$("clear").addEventListener("click", () => { $("searchform").reset(); runSearch(0); });
$("prev").addEventListener("click", () => runSearch(Math.max(0, offset - PAGE)));
$("next").addEventListener("click", () => runSearch(offset + PAGE));

// ---------------------------------------------------------------- editions
async function fetchBody(path) {
  const r = await fetch(path, { headers: { Authorization: "Bearer " + token() } });
  if (!r.ok) throw new Error("Could not load it (" + r.status + ").");
  return r;
}

function editionCard(e) {
  const counts = Object.entries(e.counts || {}).map(([k, v]) => v + " " + k).join(", ") || "empty";
  const when = (e.window || []).map((d) => (d || "").slice(0, 10)).join(" to ");
  const c = el("article", { class: "paper" }, el("h3", {}, e.subject || e.key),
    el("div", { class: "meta" }, [e.key, "papers stored " + when, counts].join(" · ")),
    el("span", { class: "badge " + (e.status === "built" ? "pre" : "ft") }, e.status));
  const held = Object.values(e.held_back || {}).reduce((a, b) => a + b, 0);
  if (held) c.append(el("span", { class: "badge" }, held + " held back for later"));
  if (e.left_out_as_stale) c.append(el("span", { class: "badge" }, e.left_out_as_stale + " left out as already old"));
  const box = el("div", { class: "fulltext hidden" });
  const read = el("button", { type: "button" }, "Read here");
  read.addEventListener("click", async () => {
    box.classList.remove("hidden"); box.textContent = "Loading…";
    try { box.textContent = await (await fetchBody("/v1/issues/" + encodeURIComponent(e.key) + "/text")).text(); } catch (x) { box.textContent = x.message; }
  });
  const open = el("button", { type: "button" }, "Open formatted");
  open.addEventListener("click", async () => {
    const w = window.open("", "_blank");               // opened first so the browser allows it, then pointed at the edition
    try {
      const blob = await (await fetchBody("/v1/issues/" + encodeURIComponent(e.key) + "/html")).blob();
      if (w) w.location = URL.createObjectURL(new Blob([blob], { type: "text/html" }));
    } catch (x) { if (w) w.close(); say(x.message, "error"); }
  });
  c.append(el("div", { class: "row", style: "margin-top:8px" }, read, open), box);
  return c;
}

async function loadEditions() {
  say("");
  if (!token()) { say("Paste your access token to begin."); return; }
  try {
    const list = await api("/v1/issues");
    const root = $("editions"); root.replaceChildren();
    if (!list.length) root.append(el("p", { class: "meta" }, "No edition has been built yet."));
    for (const e of list) root.append(editionCard(e));
  } catch (e) { fail(e); }
}

// ---------------------------------------------------------------- newsletter settings
const GROUPS = [["subjects", "Search topics"], ["journals", "Journals"], ["researchers", "Researchers"], ["sources", "Sources"]];

function renderConfig(cfg) {
  loadedConfig = cfg;
  $("applies").textContent = "Applies to " + cfg.applies_to + (cfg.changed_at ? " Last changed " + cfg.changed_at.slice(0, 16).replace("T", " ") + " UTC." : "");
  const root = $("groups"); root.replaceChildren();
  for (const [kind, label] of GROUPS) {
    const fs = el("fieldset", {}, el("legend", {}, label));
    for (const it of cfg[kind]) {
      const id = "cfg-" + kind + "-" + it.key;
      const box = el("input", { type: "checkbox", id, "data-kind": kind, "data-key": it.key });
      box.checked = it.enabled;
      const extra = [it.track ? "track " + it.track : "", it.tier || ""].filter(Boolean).join(", ");
      fs.append(el("div", { class: "check" }, box, el("label", { for: id }, it.name), extra ? el("span", { class: "tag" }, extra) : null));
    }
    root.append(fs);
  }
}

async function loadConfig() {
  say("");
  if (!token()) { say("Paste your access token to begin."); return; }
  try { renderConfig(await api("/v1/newsletter/config")); } catch (e) { fail(e); }
}

$("save").addEventListener("click", async () => {
  if (!loadedConfig) return;
  const changes = {};
  for (const box of document.querySelectorAll("#groups input[type=checkbox]")) {
    const kind = box.dataset.kind, key = box.dataset.key;
    const was = loadedConfig[kind].find((x) => x.key === key).enabled;
    if (box.checked !== was) (changes[kind] = changes[kind] || {})[key] = box.checked;
  }
  if (!Object.keys(changes).length) { say("Nothing changed."); return; }
  try {
    renderConfig(await api("/v1/newsletter/config", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes) }));
    say("Saved. The next newsletter will use these settings.", "ok");
  } catch (e) { fail(e); }
});

$("resetcfg").addEventListener("click", async () => {
  if (!window.confirm("Switch every topic, journal, researcher and source back on?")) return;
  try { renderConfig(await api("/v1/newsletter/config", { method: "DELETE" })); say("Everything is back on.", "ok"); } catch (e) { fail(e); }
});

runSearch(0);
