"use strict";
// The Curt web page. It only talks to the API of the server that served it, and builds every element from text nodes
// (never from HTML strings), so nothing a paper contains can run as script.
const $ = (id) => document.getElementById(id);
const PAGE = 20;
let offset = 0, loadedConfig = null, filtersLoaded = false, memToken = "", editingToken = false, searchTimer = null, resizeTimer = null;

const token = () => { try { return sessionStorage.getItem("token") || memToken; } catch (e) { return memToken; } };
function saveToken(v) { memToken = v; try { sessionStorage.setItem("token", v); } catch (e) { /* private window: memory only */ } }

function say(text, kind) { const m = $("msg"); m.textContent = text || ""; m.className = kind || ""; }

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) { if (v !== null && v !== undefined) e.setAttribute(k, v); }
  for (const k of kids) { if (k !== null && k !== undefined) e.append(k.nodeType ? k : document.createTextNode(String(k))); }
  return e;
}

const SVG_NS = "http://www.w3.org/2000/svg";
const ICONS = { x: ["M2.5 2.5l7 7M9.5 2.5l-7 7", "0 0 12 12"], plus: ["M7 2.5v9M2.5 7h9", "0 0 14 14"], search: ["M10.5 10.5L14 14", "0 0 16 16", true] };
function icon(name) {
  const [d, box, ring] = ICONS[name];
  const s = document.createElementNS(SVG_NS, "svg");
  for (const [k, v] of [["viewBox", box], ["fill", "none"], ["stroke", "currentColor"], ["stroke-width", "2"], ["stroke-linecap", "round"], ["aria-hidden", "true"]]) s.setAttribute(k, v);
  const p = document.createElementNS(SVG_NS, "path"); p.setAttribute("d", d); s.append(p);
  if (ring) { const c = document.createElementNS(SVG_NS, "circle"); c.setAttribute("cx", "7"); c.setAttribute("cy", "7"); c.setAttribute("r", "4.5"); s.append(c); }
  return s;
}

const fold = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

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

// ---------------------------------------------------------------- chips
// add: a coloured option you can click; sel: a chosen one with a cross that appears on hover or keyboard focus (always shown on touch screens); off: a switched-off option.
function addChip(cls, label, n, onClick) {
  const b = el("button", { type: "button", class: "chip add " + cls }, icon("plus"), label, n === undefined || n === null ? null : el("span", { class: "n" }, n));
  b.addEventListener("click", onClick);
  return b;
}
function selChip(cls, label, onRemove) {
  const x = el("button", { type: "button", class: "x", "aria-label": "Remove " + label }, icon("x"));
  x.addEventListener("click", onRemove);
  return el("span", { class: "chip sel " + cls }, label, x);
}
function offChip(label, onClick) {
  const b = el("button", { type: "button", class: "chip off", title: "Switch on again" }, icon("plus"), label);
  b.addEventListener("click", onClick);
  return b;
}

// How many chips of a collapsed row are hidden: those that wrapped below the first row.
function hiddenCount(box) {
  const chips = box.children;
  if (!chips.length) return 0;
  const first = chips[0].offsetTop;
  let n = 0;
  for (const c of chips) { if (c.offsetTop > first + 6) n += 1; }
  return n;
}

// Collapses a chip box to its first row with a "Show all" link under it. `state` is {expanded}; `more` is the link element.
function fitRow(box, more, state, noun) {
  box.classList.toggle("collapsed", !state.expanded);
  const hidden = hiddenCount(box);
  more.classList.toggle("hidden", hidden === 0 && !state.expanded);
  more.textContent = state.expanded ? "Show fewer" : "Show the other " + hidden + " " + noun;
  if (state.expanded && hidden === 0) more.classList.add("hidden");
}

function dayParam(n) {
  const d = new Date(); d.setDate(d.getDate() - n);
  const p = (x) => String(x).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
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
  if (name === "search") refreshSearchChips();
}
$("tab-search").addEventListener("click", () => showTab("search"));
$("tab-editions").addEventListener("click", () => showTab("editions"));
$("tab-settings").addEventListener("click", () => showTab("settings"));

function renderToken() {
  const saved = !!token() && !editingToken;
  $("tokenok").classList.toggle("hidden", !saved);
  $("token").classList.toggle("hidden", saved);
  $("tokenbtn").textContent = saved ? "Change" : "Use token";
  $("tokenbtn").className = saved ? "btn" : "btn primary";
}
$("tokenform").addEventListener("submit", (ev) => {
  ev.preventDefault();
  if (token() && !editingToken) { editingToken = true; renderToken(); $("token").focus(); return; }
  const v = $("token").value.trim();
  if (!v) { say("Paste your access token first.", "error"); return; }
  saveToken(v); $("token").value = ""; editingToken = false; filtersLoaded = false; renderToken(); say("Token saved for this tab.", "ok");
  runSearch(0);
});

// ---------------------------------------------------------------- search: chip filters
// Each kind: the API parameter, the chip colour class and what is shown. `picked` holds what is chosen (value -> label); `options` what can be chosen.
const KINDS = [
  { kind: "sources", cls: "s", title: "Sources", param: "source", noun: "sources" },
  { kind: "journals", cls: "j", title: "Journals", param: "journal", noun: "journals", find: "Find any journal…" },
  { kind: "researchers", cls: "r", title: "Researchers", param: "researcher", noun: "researchers", find: "Find a researcher…", collapse: true },
  { kind: "topics", cls: "t", title: "Topics", param: "topic", noun: "topics", find: "Find a topic…", collapse: true },
];
const picked = { sources: new Map(), journals: new Map(), researchers: new Map(), topics: new Map() };
const options = { sources: [], journals: [], researchers: [], topics: [] };    // [{value, label, n, extra}]
const ui = { sources: { expanded: false, find: "" }, journals: { expanded: false, find: "" }, researchers: { expanded: false, find: "" }, topics: { expanded: false, find: "" } };
const parts = {};                                                              // kind -> {box, more, sub}

async function loadFilters() {
  if (filtersLoaded) return;
  const f = await api("/v1/papers/filters");
  options.sources = f.sources.map((s) => ({ value: s.key, label: s.name, n: s.papers }));
  const followed = f.followed_journals.map((j) => ({ value: j.key, label: j.name, n: j.papers }));
  const names = new Set(followed.map((j) => fold(j.label)));
  const extra = f.journals_in_database.filter((j) => !names.has(fold(j.name))).map((j) => ({ value: j.name, label: j.name, n: j.papers, extra: true }));
  options.journals = followed.concat(extra);
  options.researchers = f.researchers.map((r) => ({ value: r.key, label: r.name, n: r.papers }));
  options.topics = f.topics.map((t) => ({ value: t.code, label: t.name, n: t.papers }));
  for (const k of Object.keys(picked)) picked[k].clear();
  buildGroups();
  filtersLoaded = true;
}

function buildGroups() {
  const root = $("grps"); root.replaceChildren();
  for (const g of KINDS) {
    const sub = el("span", { class: "sub" });
    const head = el("div", { class: "grphead" }, el("i", { class: "dot " + g.cls }), el("b", {}, g.title), sub);
    if (g.find) {
      const input = el("input", { type: "search", name: "find-" + g.kind, placeholder: g.find, "aria-label": g.find, autocomplete: "off" });
      input.addEventListener("input", () => { ui[g.kind].find = input.value; refreshGroup(g); });
      head.append(el("label", { class: "find" }, icon("search"), input));
    }
    const box = el("div", { class: "chips" });
    const more = el("button", { type: "button", class: "linkbtn more hidden" });
    more.addEventListener("click", () => { ui[g.kind].expanded = !ui[g.kind].expanded; refreshGroup(g); });
    parts[g.kind] = { box, more, sub };
    root.append(el("div", { class: "grp" }, head, box, more));
  }
  refreshSearchChips();
}

function pick(g, o) { picked[g.kind].set(o.value, o.label); refreshSearchChips(); scheduleSearch(); }
function unpick(g, value) { picked[g.kind].delete(value); refreshSearchChips(); scheduleSearch(); }

function refreshGroup(g) {
  const { box, more, sub } = parts[g.kind] || {};
  if (!box) return;
  const all = options[g.kind];
  const q = fold(ui[g.kind].find);
  const monitored = all.filter((o) => !o.extra).length;
  const extras = all.filter((o) => o.extra && !picked[g.kind].has(o.value)).length;
  sub.textContent = g.kind === "sources" ? "where the paper was found · click to add" : monitored + (g.kind === "topics" ? " search topics" : " monitored") + " · click to add";
  let shown = all.filter((o) => !picked[g.kind].has(o.value));
  if (g.kind === "journals") shown = shown.filter((o) => !o.extra || ui.journals.expanded || q);
  if (q) shown = shown.filter((o) => fold(o.label).includes(q));
  box.replaceChildren(...shown.map((o) => addChip(g.cls, o.label, o.n, () => pick(g, o))));
  if (!shown.length) box.append(el("span", { class: "none" }, q ? "Nothing matches." : "Everything here is selected."));
  if (g.collapse && !q) {
    fitRow(box, more, ui[g.kind], g.noun);
  } else if (g.kind === "journals" && !q) {
    box.classList.remove("collapsed");
    more.classList.toggle("hidden", extras === 0 && !ui.journals.expanded);
    more.textContent = ui.journals.expanded ? "Show only the monitored journals" : "Show all " + extras + " journals in the database";
  } else {
    box.classList.remove("collapsed"); more.classList.add("hidden");
  }
}

function refreshTray() {
  const tray = $("tray"); tray.replaceChildren();
  let n = 0;
  for (const g of KINDS) {
    for (const [value, label] of picked[g.kind]) { tray.append(selChip(g.cls, label, () => unpick(g, value))); n += 1; }
  }
  if (!n) tray.append(el("span", { class: "none" }, "Nothing selected. Click a chip above to filter."));
  $("traycount").textContent = String(n);
  $("clear").classList.toggle("hidden", n === 0);
}

function refreshSearchChips() { for (const g of KINDS) refreshGroup(g); refreshTray(); }

function scheduleSearch() { clearTimeout(searchTimer); searchTimer = setTimeout(() => runSearch(0), 250); }

function params(off) {
  const p = new URLSearchParams();
  const q = $("q").value.trim();
  if (q) p.append("q", q);
  for (const g of KINDS) for (const value of picked[g.kind].keys()) p.append(g.param, value);
  const pub = $("published").value;
  if (pub === "custom") { if ($("from").value) p.append("published_from", $("from").value); if ($("to").value) p.append("published_to", $("to").value); }
  else if (pub !== "all") p.append("published_from", dayParam(Number(pub)));
  if ($("fulltext").checked) p.append("has_full_text", "true");
  p.set("sort", $("sort").value); p.set("direction", $("direction").value);
  p.set("limit", String(PAGE)); p.set("offset", String(off));
  return p;
}

function card(p, showScore) {
  const href = safeUrl(p.url);
  const title = href ? el("a", { href, target: "_blank", rel: "noopener noreferrer" }, p.title || "(untitled)") : document.createTextNode(p.title || "(untitled)");
  const meta = [p.journal, p.authors, p.published].filter(Boolean).join(" · ");
  const c = el("article", { class: "paper" }, el("h3", {}, title), el("div", { class: "meta" }, meta));
  const pills = [];
  if (p.full_text && p.full_text.available) pills.push(el("span", { class: "pill ft" }, "Full text held"));
  if (p.preprint) pills.push(el("span", { class: "pill warn" }, "Preprint"));
  if (p.retracted) pills.push(el("span", { class: "pill bad" }, "Retracted"));
  for (const w of p.watched || []) pills.push(el("span", { class: "pill mon" }, "Monitored " + w.type + ": " + w.name));
  if (showScore) pills.push(el("span", { class: "pill" }, "Relevance " + p.relevance));
  if (pills.length) c.append(el("div", {}, ...pills));
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

let searchSeq = 0;      // only the newest search may draw its answer: an older, slower one must never overwrite it
async function runSearch(off) {
  clearTimeout(searchTimer);
  offset = off;
  if (!token()) { say("Paste your access token to begin."); return; }
  const mine = ++searchSeq;
  try {
    await loadFilters();
    const d = await api("/v1/papers?" + params(off).toString());
    if (mine !== searchSeq) return;
    const list = $("results"); list.replaceChildren();
    const rel = $("sort").value === "relevance";
    for (const p of d.items) list.append(card(p, rel));
    $("count").textContent = d.total === 0 ? "No papers match." : d.total + " paper" + (d.total === 1 ? "" : "s") + " match";
    const pages = Math.max(1, Math.ceil(d.total / PAGE));
    $("page").textContent = "Page " + (Math.floor(off / PAGE) + 1) + " of " + pages;
    $("prev").disabled = off === 0; $("next").disabled = off + PAGE >= d.total;
    $("pager").classList.toggle("hidden", d.total <= PAGE);
  } catch (e) { if (mine === searchSeq) fail(e); }
}
$("searchform").addEventListener("submit", (ev) => { ev.preventDefault(); say(""); runSearch(0); });
$("clear").addEventListener("click", () => { for (const k of Object.keys(picked)) picked[k].clear(); refreshSearchChips(); runSearch(0); });
$("prev").addEventListener("click", () => runSearch(Math.max(0, offset - PAGE)));
$("next").addEventListener("click", () => runSearch(offset + PAGE));
$("published").addEventListener("change", () => { $("custom").classList.toggle("hidden", $("published").value !== "custom"); if ($("published").value !== "custom") runSearch(0); });
for (const id of ["fulltext", "sort", "direction", "from", "to"]) $(id).addEventListener("change", () => runSearch(0));
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { refreshSearchChips(); if (loadedConfig) refreshConfig(); }, 150); });

// ---------------------------------------------------------------- editions
async function fetchBody(path) {
  const r = await fetch(path, { headers: { Authorization: "Bearer " + token() } });
  if (!r.ok) throw new Error("Could not load it (" + r.status + ").");
  return r;
}

// Count chips take the newsletter's section colours. The formatted edition is shown in a locked frame: scripts are switched off in it (the sandbox leaves that permission out), its links open in a new tab,
// and it is the server's own rendered edition, never page content built here from strings.
const SECTION_LABELS = { retractions: "Retractions", direct: "Direct research", mechanisms: "Related mechanisms", preprints: "Preprints", trials: "Clinical trials" };
const SECTION_TONES = { retractions: "red", preprints: "amber", trials: "teal" };
const STATUS_LABELS = { built: "Draft not created yet" };      // owner's wording for a built edition; the other statuses are shown as they are stored
let openEdition = null;     // the one edition whose preview is showing

function stat(n, label, tone) { return el("span", { class: "stat" + (tone ? " " + tone : "") }, el("b", {}, n), " " + label); }

function editionCard(e) {
  const counts = e.counts || {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const stats = el("div", { class: "stats" }, stat(total, total === 1 ? "publication" : "publications"));
  for (const k of Object.keys(SECTION_LABELS)) { if (counts[k]) stats.append(stat(counts[k], SECTION_LABELS[k], SECTION_TONES[k])); }
  const held = Object.values(e.held_back || {}).reduce((a, b) => a + b, 0);
  if (held) stats.append(stat(held, "held back for later", "grey"));
  if (e.left_out_as_stale) stats.append(stat(e.left_out_as_stale, "left out as already old", "grey"));
  const subject = e.subject || e.key;
  const status = el("span", { class: "status " + (e.status || "") }, el("i"), STATUS_LABELS[e.status] || e.status);

  const previewBtn = el("button", { type: "button", class: "btn primary" }, "Preview");
  const textBtn = el("button", { type: "button", class: "btn" }, "Plain text");
  const tabBtn = el("button", { type: "button", class: "btn" }, "Open in new tab");
  const textBox = el("div", { class: "fulltext hidden" });
  const stage = el("div", { class: "stage" });
  const desk = el("button", { type: "button", "aria-pressed": "true" }, "Desktop");
  const phone = el("button", { type: "button", "aria-pressed": "false" }, "Phone");
  const closeBtn = el("button", { type: "button", class: "btn" }, "Close");
  const panel = el("div", { class: "pv hidden" }, el("div", { class: "pvbar" }, el("span", { class: "lbl" }, "Formatted preview"), el("span", { class: "sub" }, subject),
    el("span", { class: "seg" }, desk, phone), closeBtn), stage);
  const card = el("article", { class: "ed" }, el("div", { class: "edtop" }, el("div", { class: "edtitle" }, el("h3", {}, subject), status), stats),
    el("div", { class: "edbar" }, previewBtn, tabBtn, textBtn), textBox, panel);

  const width = (isPhone) => { stage.classList.toggle("phone", isPhone); desk.setAttribute("aria-pressed", String(!isPhone)); phone.setAttribute("aria-pressed", String(isPhone)); };
  desk.addEventListener("click", () => width(false));
  phone.addEventListener("click", () => width(true));

  const hideText = () => { textBox.classList.add("hidden"); textBtn.textContent = "Plain text"; };      // one view at a time on a card: the formatted preview or the plain text
  let loaded = false;
  async function show() {
    if (openEdition && openEdition !== handle) openEdition.hide();
    openEdition = handle;
    hideText();
    panel.classList.remove("hidden"); card.classList.add("open"); previewBtn.textContent = "Hide preview";
    if (loaded) return;
    loaded = true;
    stage.replaceChildren(el("p", { class: "none" }, "Loading the edition…"));
    try {
      const html = await (await fetchBody("/v1/issues/" + encodeURIComponent(e.key) + "/html")).text();
      const frame = el("iframe", { title: subject, sandbox: "allow-same-origin allow-popups allow-popups-to-escape-sandbox" });
      frame.srcdoc = html.replace("<head>", '<head><base target="_blank">');
      stage.replaceChildren(frame);
    } catch (x) { loaded = false; stage.replaceChildren(el("p", { class: "none" }, x.message)); }
  }
  function hide() { panel.classList.add("hidden"); card.classList.remove("open"); previewBtn.textContent = "Preview"; if (openEdition === handle) openEdition = null; }
  const handle = { show, hide };
  previewBtn.addEventListener("click", () => (panel.classList.contains("hidden") ? show() : hide()));
  closeBtn.addEventListener("click", hide);

  let textLoaded = false;
  textBtn.addEventListener("click", async () => {
    const hidden = textBox.classList.toggle("hidden");
    textBtn.textContent = hidden ? "Plain text" : "Hide plain text";
    if (hidden) return;
    if (!panel.classList.contains("hidden")) hide();
    if (textLoaded) return;
    textLoaded = true; textBox.textContent = "Loading…";
    try { textBox.textContent = await (await fetchBody("/v1/issues/" + encodeURIComponent(e.key) + "/text")).text(); } catch (x) { textLoaded = false; textBox.textContent = x.message; }
  });
  tabBtn.addEventListener("click", async () => {
    const w = window.open("", "_blank");               // opened first so the browser allows it, then pointed at the edition
    try {
      const blob = await (await fetchBody("/v1/issues/" + encodeURIComponent(e.key) + "/html")).blob();
      if (w) w.location = URL.createObjectURL(new Blob([blob], { type: "text/html" }));
    } catch (x) { if (w) w.close(); say(x.message, "error"); }
  });
  return { card, handle };
}

async function loadEditions() {
  say("");
  if (!token()) { say("Paste your access token to begin."); return; }
  try {
    const list = await api("/v1/issues");
    const root = $("editions"); root.replaceChildren();
    openEdition = null;
    if (!list.length) root.append(el("p", { class: "note" }, "No edition has been built yet."));
    const cards = list.map(editionCard);
    for (const c of cards) root.append(c.card);
    if (cards.length) cards[0].handle.show();          // the newest edition opens by itself
  } catch (e) { fail(e); }
}

// ---------------------------------------------------------------- newsletter settings
// Chosen things are "included" chips with a cross; clicking the cross switches one off (a dashed chip on the right), clicking a dashed chip switches it back on.
// Nothing is saved until "Save changes"; `draft` is what the page shows, `loadedConfig` what the server holds.
const GROUPS = [["sources", "s", "Retrieval sources", "sources", "Find a source…"], ["subjects", "t", "Search topics", "topics", "Find a topic…", true],
                ["journals", "j", "Journals", "journals", "Find a journal…"], ["researchers", "r", "Researchers", "researchers", "Find a researcher…", true]];
let draft = {};
const cfgUi = {};
const cfgParts = {};

function draftChanges() {
  const changes = {};
  for (const [kind] of GROUPS) {
    for (const it of loadedConfig[kind]) {
      if (draft[kind][it.key] !== it.enabled) (changes[kind] = changes[kind] || {})[it.key] = draft[kind][it.key];
    }
  }
  return changes;
}

function renderConfig(cfg) {
  loadedConfig = cfg;
  draft = {};
  for (const [kind] of GROUPS) { draft[kind] = {}; for (const it of cfg[kind]) draft[kind][it.key] = it.enabled; }
  $("applies").textContent = "Applies to " + cfg.applies_to + (cfg.changed_at ? " Last changed " + cfg.changed_at.slice(0, 16).replace("T", " ") + " UTC." : "");
  const root = $("groups"); root.replaceChildren();
  for (const [kind, cls, title, noun, findText, collapse] of GROUPS) {
    cfgUi[kind] = cfgUi[kind] || { expanded: false, find: "" };
    const sub = el("span", { class: "meta" });
    const input = el("input", { type: "search", name: "find-cfg-" + kind, placeholder: findText, "aria-label": findText, autocomplete: "off" });
    input.value = cfgUi[kind].find;
    input.addEventListener("input", () => { cfgUi[kind].find = input.value; refreshConfig(); });
    const head = el("div", { class: "cardhead" }, el("div", { class: "left" }, el("i", { class: "dot " + cls }), el("h2", {}, title), sub), el("label", { class: "find" }, icon("search"), input));
    const onLabel = el("p", { class: "sublabel" }), offLabel = el("p", { class: "sublabel" });
    const onBox = el("div", { class: "chips" }), offBox = el("div", { class: "chips" });
    const more = el("button", { type: "button", class: "linkbtn more hidden" });
    more.addEventListener("click", () => { cfgUi[kind].expanded = !cfgUi[kind].expanded; refreshConfig(); });
    cfgParts[kind] = { sub, onLabel, offLabel, onBox, offBox, more, cls, noun, collapse };
    root.append(el("div", { class: "card" }, head, el("div", { class: "pad" }, el("div", { class: "two" },
      el("div", {}, onLabel, onBox, more), el("div", {}, offLabel, offBox)))));
  }
  refreshConfig();
}

function refreshConfig() {
  if (!loadedConfig) return;
  for (const [kind] of GROUPS) {
    const p = cfgParts[kind];
    if (!p) continue;
    const q = fold(cfgUi[kind].find);
    const items = loadedConfig[kind].filter((it) => !q || fold(it.name).includes(q));
    const on = items.filter((it) => draft[kind][it.key]), off = items.filter((it) => !draft[kind][it.key]);
    const total = loadedConfig[kind].length, onAll = loadedConfig[kind].filter((it) => draft[kind][it.key]).length;
    p.sub.textContent = onAll + " of " + total + " included";
    p.onLabel.textContent = "Included in the newsletter · " + onAll;
    p.offLabel.textContent = "Switched off · " + (total - onAll) + " (click to add back)";
    const tip = (it) => [it.track ? "track " + it.track : "", it.tier || ""].filter(Boolean).join(", ");
    p.onBox.replaceChildren(...on.map((it) => { const c = selChip(p.cls, it.name, () => { draft[kind][it.key] = false; refreshConfig(); }); if (tip(it)) c.setAttribute("title", tip(it)); return c; }));
    p.offBox.replaceChildren(...off.map((it) => offChip(it.name, () => { draft[kind][it.key] = true; refreshConfig(); })));
    if (!on.length) p.onBox.append(el("span", { class: "none" }, q ? "Nothing matches." : "Nothing is included. Pick from the list on the right."));
    if (!off.length) p.offBox.append(el("span", { class: "none" }, q ? "Nothing matches." : "Everything is included."));
    if (p.collapse && !q) fitRow(p.onBox, p.more, cfgUi[kind], p.noun);
    else { p.onBox.classList.remove("collapsed"); p.more.classList.add("hidden"); }
  }
  const n = Object.values(draftChanges()).reduce((a, c) => a + Object.keys(c).length, 0);
  $("saveinfo").textContent = n ? n + " unsaved change" + (n === 1 ? "" : "s") : "No unsaved changes";
  $("save").disabled = n === 0; $("discard").disabled = n === 0;
}

async function loadConfig() {
  say("");
  if (!token()) { say("Paste your access token to begin."); return; }
  try { renderConfig(await api("/v1/newsletter/config")); } catch (e) { fail(e); }
}

$("save").addEventListener("click", async () => {
  if (!loadedConfig) return;
  const changes = draftChanges();
  if (!Object.keys(changes).length) { say("Nothing changed."); return; }
  try {
    renderConfig(await api("/v1/newsletter/config", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes) }));
    say("Saved. The next newsletter will use these settings.", "ok");
  } catch (e) { fail(e); }
});

$("discard").addEventListener("click", () => { if (loadedConfig) { renderConfig(loadedConfig); say(""); } });

$("resetcfg").addEventListener("click", async () => {
  if (!window.confirm("Switch every topic, journal, researcher and source back on?")) return;
  try { renderConfig(await api("/v1/newsletter/config", { method: "DELETE" })); say("Everything is back on.", "ok"); } catch (e) { fail(e); }
});

renderToken();
runSearch(0);
