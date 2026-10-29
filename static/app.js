"use strict";
// Find My Clip front end. All movement on screen follows real events from the
// server: a thumbnail only leaves the pile when that file has finished.

const $ = (id) => document.getElementById(id);
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const S = {
  snap: null,
  items: new Map(),
  view: "start",
  selected: new Set(),
  cut: [],
  openGroup: null,
  exporting: false,
};
const REVIEW = "Needs review";
const FAILED = "Couldn't open";
const SKIPPED = "Skipped";
const DUPES = "Duplicate media";
const SPECIAL = [DUPES, REVIEW, SKIPPED, FAILED];

// ------------------------------------------------------------------ helpers
async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Find-My-Clip": "1" },
    body: JSON.stringify(body),
  };
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.message || "Something went wrong. Try again.");
  return data;
}
const thumbUrl = (name) => (name ? `/thumb/${encodeURIComponent(name)}` : "");
const fmtTime = (s) => {
  s = Math.max(0, Math.round(s || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function el(tag, attrs = {}, html) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
  }
  if (html != null) e.innerHTML = html;
  return e;
}
let toastTimer;
function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 5000);
}
function groupOf(it) {
  // Only files left out of the folders get a card of their own, so every folder card
  // counts exactly what Create the folders copies. Similar and blurry shots are real
  // photos: they stay in their group, flagged on the tile.
  if (it.status === "review" && (it.duplicate_kind === "exact" || it.duplicate_kind === "edit") && it.decision !== "keep") return DUPES;
  if (it.status === "review") return it.decision === "skip" ? SKIPPED : it.category;
  if (it.status === "failed") return FAILED;
  if (it.status === "ready") return it.decision === "skip" ? SKIPPED : it.category;
  return null; // still in the pile
}
function reasonText(it) {
  const bits = [];
  if (it.duplicate_of) {
    const o = S.items.get(it.duplicate_of);
    const other = o ? `${o.name}${o.folder ? ` in ${o.folder}` : ""}` : "another file";
    if (it.duplicate_kind === "exact") bits.push(`Same file as ${other}`);
    else if (it.duplicate_kind === "edit") bits.push(`Another edit of ${other}, which we'd keep`);
    else bits.push(`Similar shot to ${other}${it.mono ? ", in black and white" : ""}. Keep the one you like best`);
  }
  if (it.blurry) bits.push("Looks blurry");
  return bits.join(". ");
}

// ------------------------------------------------------------------ views
function show(view) {
  S.view = view;
  for (const v of ["start", "sort", "review", "find", "cut"]) $(`view-${v}`).hidden = v !== view;
  document.querySelectorAll("#tabs button").forEach((b) => {
    if (b.dataset.view === view || (view === "review" && b.dataset.view === "sort")) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  if (view === "cut") renderCut();
  if (view === "review") renderReview();
  globe.running(view === "sort" && $("view-sort").dataset.stage !== "done");
  if (view === "find") $("q").focus();
  window.scrollTo({ top: 0 });
}
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
document.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => show(b.dataset.go)));

// ------------------------------------------------------------------ start
async function openFolder(kind) {
  $("start-error").textContent = "";
  try {
    if (kind === "pick") {
      const r = await api("/api/choose-folder", {});
      if (r.cancelled) return;
    } else if (kind === "sample") {
      $("try-sample").disabled = true;
      $("try-sample").textContent = "Making the sample files…";
      await api("/api/open", { sample: true });
    } else {
      await api("/api/open", { path: $("path-input").value });
    }
  } catch (e) {
    $("start-error").textContent = e.message;
  } finally {
    $("try-sample").disabled = false;
    $("try-sample").textContent = "Try it with sample files";
  }
}
$("choose").addEventListener("click", () => openFolder("pick"));
$("try-sample").addEventListener("click", () => openFolder("sample"));
$("path-form").addEventListener("submit", (e) => { e.preventDefault(); openFolder("path"); });
$("another").addEventListener("click", () => $("change-folder").click());
$("resort").addEventListener("click", async () => {
  try {
    $("resort").disabled = true;
    await api("/api/resort", { mode: $("mode-again").value });
    toast("Tidying up again. Photos you moved stay where you put them.");
  } catch (e) { toast(e.message); } finally { $("resort").disabled = false; }
});
$("change-folder").addEventListener("click", () => {
  if (S.snap && S.snap.state === "analysing" && !confirm("Sorting is still running. Stop and choose another folder?")) return;
  if (S.snap && S.snap.state === "analysing") api("/api/cancel", {});
  $("tabs").hidden = true;
  $("sample-note").hidden = true;
  show("start");
});

// ------------------------------------------------------------------ state
function applySnapshot(snap) {
  S.snap = snap;
  S.items = new Map(snap.items.map((i) => [i.id, i]));
  S.cut = snap.cut || [];
  S.selected.clear();
  $("tabs").hidden = false;
  $("folder-word").textContent = snap.folder;
  $("review-folder").textContent = snap.folder;
  $("fc-name").textContent = snap.folder;
  document.title = `${snap.folder} · Find My Clip`;
  $("sample-note").hidden = !snap.sample;
  $("mode").value = $("mode-again").value = snap.mode_setting || "auto";
  modeHint(snap);
  modelNote(snap.models);
  globe.clear();
  for (const id of ["photo-cards", "video-cards", "special-cards"]) $(id).innerHTML = "";
  groupEls.clear();
  S.copiedFolders = null;
  S.copied = null;
  $("review-btn").textContent = "Check the groups first";
  $("done-word").textContent = "Sorted.";
  for (const c of snap.categories) ensureGroup(c, "photo");
  ensureGroup(DUPES, "photo");
  S.bulk = true;             // build every card once at the end, not once per file
  for (const it of snap.items) {
    if (groupOf(it)) addToGroup(it, false);
    else addToPile(it);
  }
  S.bulk = false;
  refreshAllGroups();
  renderCounts(snap.counts, snap.state);
  if (snap.copied) copyEvent({ type: "copy_done", quiet: true, ...snap.copied });
  renderSelbar();
  $("cut-count").hidden = !S.cut.length;
  $("cut-count").textContent = S.cut.length;
  if (S.view === "start") show("sort");
  if (S.view === "review") renderReview();
}

function modelNote(m) {
  const off = [];
  if (m && m.errors && m.errors.clip) off.push("Picture matching is switched off, so groups and visual search are a demonstration only. Install it with: pip install open_clip_torch");
  if (m && m.errors && m.errors.speech) off.push("Speech is switched off, so we can't search what's said. Install it with: pip install faster-whisper");
  $("model-note").hidden = !off.length;
  $("model-note").textContent = off.join(" ");
}

const n = (x) => (x || 0).toLocaleString("en-GB");
function stageOf(c, state) {
  const grouped = (c.ready || 0) + (c.review || 0);
  if (!grouped && (state === "idle" || state === "preparing")) return "choose";
  if (c.total && (c.waiting || 0) + (c.analysing || 0) === 0 && state !== "analysing") return "done";
  return "watch";
}
function renderCounts(c, state) {
  if (!c) return;
  const finished = (c.ready || 0) + (c.review || 0) + (c.failed || 0);
  const grouped = (c.ready || 0) + (c.review || 0);
  const left = (c.waiting || 0) + (c.analysing || 0);
  const stage = stageOf(c, state);
  const running = state === "analysing";

  $("view-sort").dataset.stage = stage;
  $("choose-step").hidden = stage !== "choose";
  $("watch-step").hidden = false; // the globe is always in the middle
  $("sort-sub").hidden = stage !== "choose";
  // "My photos" reads better without "your" in front of it.
  const mine = /^my\b/i.test(S.snap.folder);
  $("title-a").textContent = stage === "choose" ? (mine ? "Let's tidy up" : "Let's tidy up your") : stage === "done" ? (mine ? "" : "Your") : (mine ? "Sorting" : "Sorting your");
  $("review-title").firstChild.textContent = mine ? "" : "Your ";
  $("title-b").textContent = stage === "done" ? "folder, sorted." : "folder.";

  const kinds = [c.photos && `${n(c.photos)} photo${c.photos === 1 ? "" : "s"}`, c.videos && `${n(c.videos)} video${c.videos === 1 ? "" : "s"}`].filter(Boolean);
  $("folder-meta").textContent = state === "preparing" ? `Reading the folder. ${n(c.total)} so far.` : kinds.length ? kinds.join(" and ") : "No photos or videos found";
  $("go").hidden = (c.waiting || 0) === 0 && stage === "choose";
  $("go").disabled = state === "preparing";

  const all = [...S.items.values()];
  const sorted = all.filter((i) => i.status === "ready" || i.status === "review");
  $("c-found").textContent = n(c.total);
  $("c-photos").textContent = n(sorted.filter((i) => i.kind === "photo").length);
  $("c-videos").textContent = n(sorted.filter((i) => i.kind === "video").length);
  // One folder per kind and group, exactly what Copy into folders makes.
  const folders = new Set(all.filter(willCopy).map((i) => `${i.kind}|${i.category}`));
  $("c-folders").textContent = n(S.copiedFolders ?? folders.size);
  $("c-folders-label").textContent = S.copiedFolders != null ? "folders made" : "new folders";
  $("c-dupes").textContent = n(all.filter((i) => groupOf(i) === DUPES).length);
  const pct = c.total ? Math.round((100 * finished) / c.total) : 0;
  $("progress").firstElementChild.style.width = pct + "%";
  $("progress").setAttribute("aria-valuenow", pct);
  $("failed-line").hidden = !c.failed;
  $("failed-text").textContent = `${n(c.failed)} file${c.failed === 1 ? "" : "s"} couldn't be opened.`;

  const done = stage === "done" && !flying;
  $("canvas").classList.toggle("done", done);
  $("next").hidden = !done;
  $("globe-foot").hidden = done;
  $("pause").hidden = !running;
  $("pause").textContent = "Pause";
  const s = $("status");
  const busyVideo = all.find((i) => i.status === "analysing" && i.kind === "video");
  if (running && busyVideo && (busyVideo.duration || 0) > 60) s.textContent = `Listening to ${busyVideo.name}. A ${fmtTime(busyVideo.duration)} video takes a minute or two.`;
  else if (running) s.textContent = !finished ? "Getting started. The first file takes a little longer." : mode() === "event" ? `Grouping the moments of the day. ${n(left)} to go.` : `Grouping similar moments. ${n(left)} to go.`;
  else if (state === "paused") s.textContent = `Paused with ${n(c.waiting)} still to go.`;
  else s.textContent = "";
  const blurry = all.filter((i) => i.blurry && willCopy(i)).length;
  if (stage === "done" && blurry) s.textContent = `Includes ${n(blurry)} blurry shot${blurry === 1 ? "" : "s"}. Check the groups first to skip them.`;
  if (state === "paused") {
    $("pause").hidden = false;
    $("pause").textContent = "Carry on";
  }
  if (done) globe.ghost();
  fitDone();
  globe.running(stage !== "done");

  if (done && !S.copying && !S.copied) loadPlan();
  if (S.view === "review") renderReviewMeta();
}
const mode = () => (S.snap && S.snap.mode) || "everyday";
// The same rule the server uses to create the folders (fmc/export.py, chosen):
// one best version of each picture; extra copies and older edits stay out.
const willCopy = (i) => (i.status === "ready" || i.status === "review") && i.decision !== "skip"
  && !((i.duplicate_kind === "exact" || i.duplicate_kind === "edit") && i.decision !== "keep");

$("go").addEventListener("click", async () => {
  // One press: the server sorts, then creates the folders as soon as it's done.
  try { await api("/api/analyse", { then_create: true }); } catch (e) { toast(e.message); }
});
$("pause").addEventListener("click", () => {
  if (S.snap.state === "paused") { $("go").click(); return; }
  api("/api/cancel", {});
  $("status").textContent = "Pausing after this file…";
});
$("retry").addEventListener("click", () => api("/api/retry", {}).catch((e) => toast(e.message)));
$("review-btn").addEventListener("click", () => openGroup(null));
$("back-to-sort").addEventListener("click", () => show("sort"));

// ------------------------------------------------------------------ 02 globe
// The heap is a sphere of real thumbnails, projected by hand so depth sets
// size, fade and stacking. It turns slowly and shrinks as files leave it.
const PILE_MAX = 420;
const globe = {
  els: new Map(), backlog: [], free: [], start: 0, angle: 0, raf: 0, last: 0, on: false, relayoutTimer: 0,
  clear() {
    $("pile").innerHTML = "";
    this.els.clear();
    this.backlog.length = 0;
    this.free.length = 0;
    this.start = 0;
  },
  add(it) {
    if (!it.thumb) return;
    let g = this.els.get(it.id);
    if (!g) {
      if (this.els.size >= PILE_MAX) { if (!this.backlog.includes(it.id)) this.backlog.push(it.id); return; }
      const img = new Image();
      img.alt = "";
      img.decoding = "async";
      $("pile").appendChild(img);
      // Take the place of a thumbnail that just left, or wait for a fresh layout.
      const spot = this.free.pop() || { x: 0, y: 0, z: 1 };
      g = { img, ...spot, spin: (Math.random() * 24 - 12) };
      img.addEventListener("mouseenter", () => { g.hover = true; this.frame(performance.now(), !this.raf); });
      img.addEventListener("mouseleave", () => { g.hover = false; this.frame(performance.now(), !this.raf); });
      this.els.set(it.id, g);
      this.start = Math.max(this.start, this.els.size + this.backlog.length);
      if (!this.free.length) {
        clearTimeout(this.relayoutTimer);
        this.relayoutTimer = setTimeout(() => this.layout(), 60);
      }
    }
    if (g.img.dataset.src !== it.thumb) { g.img.src = thumbUrl(it.thumb); g.img.dataset.src = it.thumb; }
    g.img.className = it.status;
    if (!this.raf) this.frame(performance.now(), true);
  },
  // Spread every thumbnail evenly over the sphere (a Fibonacci lattice).
  layout() {
    const all = [...this.els.values()];
    const N = Math.max(all.length, 12);
    all.forEach((g, k) => {
      const y = 1 - (2 * (k + 0.5)) / N;
      const r = Math.sqrt(1 - y * y);
      const th = k * 2.399963;
      g.x = Math.cos(th) * r; g.y = y; g.z = Math.sin(th) * r;
    });
    this.frame(performance.now(), true);
  },
  take(id) {
    const g = this.els.get(id);
    this.els.delete(id);
    if (g) this.free.push({ x: g.x, y: g.y, z: g.z });
    while (this.els.size < PILE_MAX && this.backlog.length) {
      const it = S.items.get(this.backlog.shift());
      if (it && !groupOf(it)) this.add(it);
    }
    return g ? g.img : null;
  },
  running(on) {
    this.on = on && !reduced;
    if (this.on && !this.raf) this.raf = requestAnimationFrame((t) => this.frame(t));
    if (!this.on) this.frame(performance.now(), true);
  },
  frame(t, once) {
    // 30 frames a second is smooth enough for a slow turn and halves the work.
    if (!once && this.drawn && t - this.drawn < 32) { this.raf = requestAnimationFrame((tt) => this.frame(tt)); return; }
    this.drawn = t;
    const box = $("pile-wrap");
    const w = box.clientWidth, h = box.clientHeight;
    // Rotation stops on hover, on pause and once sorting is done.
    const still = this.hovering || (S.snap && S.snap.state === "paused");
    if (!once && !still) {
      const dt = this.last ? Math.min(64, t - this.last) : 16;
      this.angle += dt * 0.00018;
    }
    this.last = t;
    // Shrink as files leave, measured against the most there have ever been.
    const left = this.els.size + this.backlog.length;
    this.start = Math.max(this.start, left);
    const shrink = this.start ? 0.5 + 0.5 * Math.cbrt(left / this.start) : 1;
    const reserve = this.reserve || 60;
    // Keep the whole ball inside its frame, with room around it, so it reads as a globe.
    const R = Math.min(w * 0.42, (h - reserve) * 0.44) * shrink;
    const size = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--thumb")) || 48;
    const cx = w / 2, cy = (h - reserve + 20) / 2;
    const ca = Math.cos(this.angle), sa = Math.sin(this.angle), tilt = 0.32, ct = Math.cos(tilt), st = Math.sin(tilt);
    // Fewer files, bigger thumbnails, so the ball always reads as one full heap.
    const spacing = R * Math.sqrt((4 * Math.PI) / Math.max(1, this.els.size));
    // Big folders get small thumbnails (a dense ball), small folders bigger ones.
    const grow = Math.max(0.5, Math.min(2.0, (spacing * 0.95) / size));
    for (const g of this.els.values()) {
      const x1 = g.x * ca + g.z * sa;
      const z1 = -g.x * sa + g.z * ca;
      const y2 = g.y * ct - z1 * st;
      const z2 = g.y * st + z1 * ct;
      const depth = (z2 + 1) / 2;
      const busy = g.img.className === "analysing";
      const s = (0.5 + 0.55 * depth) * grow * (g.hover ? 2.6 : busy ? 1.4 : 1);
      g.img.style.transform = `translate(${(cx + x1 * R - size / 2).toFixed(1)}px, ${(cy + y2 * R - size / 2).toFixed(1)}px) rotate(${g.spin}deg) scale(${s.toFixed(3)})`;
      // Only touch what changed: opacity and stacking move in coarse steps.
      const op = (0.3 + 0.7 * Math.round(depth * 10) / 10).toFixed(2);
      if (g.op !== op) { g.img.style.opacity = op; g.op = op; }
      const z = g.hover ? 1200 : busy ? 999 : Math.round(depth * 20) * 40;
      if (g.zi !== z) { g.img.style.zIndex = z; g.zi = z; }
    }
    if (once || !this.on || document.hidden) { this.raf = 0; return; }
    this.raf = requestAnimationFrame((tt) => this.frame(tt));
  },
};
document.addEventListener("visibilitychange", () => { if (!document.hidden) globe.running(globe.on); });
$("pile-wrap").addEventListener("mouseenter", () => { globe.hovering = true; });
$("pile-wrap").addEventListener("mouseleave", () => { globe.hovering = false; });
// When sorting is done the globe stays in the middle: every sorted picture,
// still and dimmed, so the groups around it become the focus.
globe.ghost = function () {
  if (this.els.size) return;
  const pics = [...S.items.values()].filter((i) => i.thumb && groupOf(i) && groupOf(i) !== FAILED).slice(0, PILE_MAX);
  for (const it of pics) this.add({ ...it, id: `ghost:${it.id}`, status: "ghost" });
  this.start = this.els.size;
  this.layout();
};
addEventListener("resize", () => globe.frame(performance.now(), true));
// Once sorted, the next step sits under the globe. Measure it, so the globe always has
// its own space above, however much text and how many buttons the step shows.
const GLOBE_ROOM = () => (innerWidth >= 1080 ? 380 : 300);
function fitDone() {
  const wrap = $("pile-wrap"), next = $("next");
  const done = $("view-sort").dataset.stage === "done" && !next.hidden;
  if (!done) { wrap.style.removeProperty("height"); globe.reserve = 60; return; }
  const h = next.offsetHeight;
  wrap.style.setProperty("height", `${GLOBE_ROOM() + h}px`, "important");
  if (globe.reserve !== h + 16) { globe.reserve = h + 16; globe.frame(performance.now(), true); }
}
new ResizeObserver(fitDone).observe($("next"));
addEventListener("resize", fitDone);
function addToPile(it) { globe.add(it); }

// ------------------------------------------------------------------ groups
const groupEls = new Map();
const ICONS = {
  rings: "M9 15a5 5 0 1 0 0-.01M15 15a5 5 0 1 0 0-.01",
  heart: "M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z",
  people: "M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM16 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM2 20c0-3.3 2.7-6 6-6s6 2.7 6 6M14 14.2c.6-.1 1.3-.2 2-.2 3.3 0 6 2.7 6 6",
  glass: "M7 3h10l-1 7a4 4 0 0 1-8 0zM12 14v6M8 21h8",
  fork: "M7 3v8M5 3v4a2 2 0 0 0 4 0V3M7 11v10M16 3c-2 1-3 3-3 6s1 4 3 4v8",
  music: "M9 18V5l10-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM19 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z",
  house: "M3 11 12 4l9 7M5 10v10h14V10M10 20v-6h4v6",
  plane: "M2 13l20-8-6 16-4-6-6-2zM12 15l4-6",
  screen: "M3 5h18v11H3zM8 20h8M12 16v4",
  sport: "M6 8v8M3 10v4M18 8v8M21 10v4M6 12h12",
  tree: "M12 3 6 13h4l-3 5h10l-3-5h4zM12 18v3",
  doc: "M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6",
  shirt: "M8 3 3 6l2 4 3-1v12h8V9l3 1 2-4-5-3a4 4 0 0 1-8 0z",
  chat: "M4 5h16v11H9l-5 4z",
  mirror: "M12 3a5 6 0 1 0 0 12 5 6 0 0 0 0-12zM12 15v6M8 21h8",
  flower: "M12 8a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 2v4M12 16v6M2 12h4M18 12h4",
  warn: "M12 4 2.5 20h19zM12 10v4M12 17v.5",
  cross: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM9 9l6 6M15 9l-6 6",
  minus: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM8 12h8",
  question: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7M12 17v.5",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
};
const GROUP_LOOK = [
  [/ceremony|wedding/i, "rings", "#b9400f"], [/couple/i, "heart", "#b4235a"], [/getting ready/i, "mirror", "#7c3a8f"],
  [/groups|friends|family/i, "people", "#3a5fa8"], [/drinks/i, "glass", "#2b7a8a"], [/food|speeches/i, "fork", "#8a5300"],
  [/dancing|party|music/i, "music", "#7c3a8f"], [/venue|places|home/i, "house", "#33694a"], [/travel/i, "plane", "#3a5fa8"],
  [/screenshots|work/i, "screen", "#5b5f6b"], [/sport/i, "sport", "#b9400f"], [/outdoors/i, "tree", "#33694a"],
  [/notes/i, "doc", "#5b5f6b"], [/outfits/i, "shirt", "#b4235a"], [/talking/i, "chat", "#2b7a8a"],
  [/needs review/i, "warn", "#8a5300"], [/couldn't/i, "cross", "#a3262a"], [/skipped/i, "minus", "#625a51"], [/not sure/i, "question", "#625a51"],
];
function lookOf(name) {
  for (const [re, icon, colour] of GROUP_LOOK) if (re.test(name)) return { icon, colour };
  return { icon: "folder", colour: "#b9400f" };
}
const iconSvg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[lookOf(name).icon]}"/></svg>`;
function groupOrder(name) {
  const i = SPECIAL.indexOf(name);
  return i < 0 ? 0 : 990 + i;
}
function groupItems(name) {
  return [...S.items.values()].filter((i) => groupOf(i) === name);
}
// On the sorting canvas, photo folders sit on the left and video folders on the
// right, like the copy: Photos/<group> and Videos/<group>. Duplicates and
// review sit under the videos.
function sideOf(name, kind) { return SPECIAL.includes(name) ? "special" : kind; }
function cardItems(key) {
  const [side, name] = key.split("|");
  return [...S.items.values()].filter((i) => groupOf(i) === name && (side === "special" || i.kind === side));
}
function ensureGroup(name, kind) {
  const key = `${sideOf(name, kind)}|${name}`;
  let g = groupEls.get(key);
  if (g) return g;
  const look = lookOf(name);
  g = el("button", { type: "button", class: "gcard empty" + (name === DUPES ? " dupes" : name === REVIEW ? " review" : name === FAILED ? " failed" : name === SKIPPED ? " skipped" : "") });
  g.style.setProperty("--g", look.colour);
  g.innerHTML = `<span class="ghead"><span class="gicon">${iconSvg(name)}</span><span class="gname"></span><b class="gnum">0</b></span><span class="gthumbs"></span>`;
  g.querySelector(".gname").textContent = name;
  g.dataset.name = name;
  g.dataset.key = key;
  g.addEventListener("click", () => openGroup(name));
  groupEls.set(key, g);
  const side = sideOf(name, kind);
  const box = side === "photo" ? $("photo-cards") : side === "video" ? $("video-cards") : $("special-cards");
  const before = [...box.children].find((c) => groupOrder(c.dataset.name) > groupOrder(name));
  box.insertBefore(g, before || null);
  $("video-head").hidden = !$("video-cards").children.length;
  return g;
}
// The folder list follows the grouping in use: swap empty cards for the new set.
function rebuildPhotoCards() {
  for (const [key, g] of [...groupEls]) {
    if (key.startsWith("photo|") && !cardItems(key).length) { g.remove(); groupEls.delete(key); }
  }
  for (const c of S.snap.categories) ensureGroup(c, "photo");
  const box = $("photo-cards");
  for (const c of S.snap.categories) { const g = groupEls.get(`photo|${c}`); if (g) box.appendChild(g); }
}
function refreshCard(key) {
  const g = groupEls.get(key);
  if (!g) return;
  const items = cardItems(key);
  const [side] = key.split("|");
  if (!items.length && side === "video") {
    g.remove();
    groupEls.delete(key);
    $("video-head").hidden = !$("video-cards").children.length;
    return;
  }
  g.classList.toggle("empty", !items.length);
  g.querySelector(".gnum").textContent = n(items.length);
  g.setAttribute("aria-label", `${g.dataset.name}, ${items.length} file${items.length === 1 ? "" : "s"}. Review`);
  const strip = g.querySelector(".gthumbs");
  const want = items.slice(-18).map((i) => i.thumb).filter(Boolean);
  if (strip.dataset.k !== want.join()) {
    strip.dataset.k = want.join();
    strip.innerHTML = "";
    want.forEach((t) => strip.appendChild(Object.assign(new Image(), { src: thumbUrl(t), alt: "" })));
  }
}
function refreshGroup(name) {
  for (const it of S.items.values()) if (groupOf(it) === name) ensureGroup(name, it.kind);
  for (const key of [...groupEls.keys()]) if (key.endsWith(`|${name}`)) refreshCard(key);
}
function refreshAllGroups() {
  for (const it of S.items.values()) { const g = groupOf(it); if (g) ensureGroup(g, it.kind); }
  for (const key of [...groupEls.keys()]) refreshCard(key);
}
function addToGroup(it, animate = true) {
  const name = groupOf(it);
  if (!name) return;
  const card = ensureGroup(name, it.kind);
  const src = globe.take(it.id);
  if (!animate || !src || reduced || $("watch-step").hidden) {
    if (src) src.remove();
    if (!S.bulk) refreshCard(card.dataset.key);
    return;
  }
  queueFlight(() => fly(src, card));
}

// Releases flights on a steady beat so a fast run still reads one by one.
const flights = [];
let flying = false;
function queueFlight(fn) { flights.push(fn); if (!flying) pump(); }
function pump() {
  const fn = flights.shift();
  if (!fn) { flying = false; if (S.snap) renderCounts(S.snap.counts, S.snap.state); return; }
  flying = true;
  fn();
  setTimeout(pump, flights.length > 120 ? 12 : flights.length > 30 ? 40 : 110);
}
function fly(src, target) {
  const name = target.dataset.name;
  const look = lookOf(name);
  const a = src.getBoundingClientRect();
  const strip = target.querySelector(".gthumbs") || target;
  const b = strip.getBoundingClientRect();
  const x0 = a.left + a.width / 2, y0 = a.top + a.height / 2;
  const x1 = b.left + Math.min(b.width, 160) / 2, y1 = b.top + b.height / 2;
  // A gentle arc: bend the path away from the straight line.
  const mx = (x0 + x1) / 2, my = (y0 + y1) / 2 - Math.abs(x1 - x0) * 0.18 - 20;
  const f = src.cloneNode();
  f.className = "flyer";
  f.removeAttribute("style");
  f.style.cssText = `left:${x0 - 28}px;top:${y0 - 28}px;`;
  document.body.appendChild(f);
  src.remove();
  const pts = [];
  for (let i = 0; i <= 10; i++) {
    const t = i / 10;
    const x = (1 - t) * (1 - t) * x0 + 2 * (1 - t) * t * mx + t * t * x1;
    const y = (1 - t) * (1 - t) * y0 + 2 * (1 - t) * t * my + t * t * y1;
    pts.push({ transform: `translate(${(x - x0).toFixed(1)}px, ${(y - y0).toFixed(1)}px) scale(${(1 - 0.45 * t).toFixed(2)})`, opacity: 1 - 0.6 * t * t });
  }
  trail(x0, y0, mx, my, x1, y1, look.colour);
  const anim = f.animate(pts, { duration: 640, easing: "cubic-bezier(.45,.05,.3,1)" });
  let done = false;
  const land = () => {
    if (done) return;
    done = true;
    f.remove();
    refreshCard(target.dataset.key);
    target.classList.add("bump");
    setTimeout(() => target.classList.remove("bump"), 180);
  };
  anim.onfinish = land;
  setTimeout(land, 900);
}
function trail(x0, y0, mx, my, x1, y1, colour) {
  const svg = $("trails");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", `M${x0},${y0} Q${mx},${my} ${x1},${y1}`);
  p.setAttribute("stroke", colour);
  svg.appendChild(p);
  const len = p.getTotalLength();
  p.style.strokeDasharray = len;
  p.animate([{ strokeDashoffset: len, opacity: 0.55 }, { strokeDashoffset: 0, opacity: 0.45, offset: 0.5 }, { strokeDashoffset: 0, opacity: 0 }], { duration: 1000, easing: "ease-out" }).onfinish = () => p.remove();
  while (svg.childNodes.length > 14) svg.firstChild.remove();
}

// ------------------------------------------------------------------ 03 review
function openGroup(name) {
  const names = reviewNames();
  S.openGroup = name && names.includes(name) ? name : names[0] || null;
  show("review");
}
function reviewNames() {
  return [...new Set([...S.items.values()].map(groupOf).filter(Boolean))]
    .sort((a, b) => groupOrder(a) - groupOrder(b) || groupItems(b).length - groupItems(a).length);
}
function renderReviewMeta() {
  // Same definitions as the counters on the sorting screen.
  const all = [...S.items.values()];
  const folders = new Set(all.filter(willCopy).map((i) => `${i.kind}|${i.category}`)).size;
  const dupes = groupItems(DUPES).length, blurry = groupItems(REVIEW).length;
  const bits = [`${n(all.length)} files`, `${n(folders)} folder${folders === 1 ? "" : "s"} ready`];
  if (dupes) bits.push(`${n(dupes)} duplicate${dupes === 1 ? "" : "s"} to check`);
  if (blurry) bits.push(`${n(blurry)} blurry`);
  $("review-meta").textContent = bits.join(" · ");
}
function renderReview() {
  if (!S.snap) return;
  renderReviewMeta();
  const names = reviewNames();
  if (!names.includes(S.openGroup)) S.openGroup = names[0] || null;
  const list = $("glist");
  list.innerHTML = "";
  for (const name of names) {
    const items = groupItems(name);
    const b = el("button", { type: "button", class: "gli" + (name === S.openGroup ? " on" : ""), "aria-current": name === S.openGroup ? "true" : false });
    b.style.setProperty("--g", lookOf(name).colour);
    b.innerHTML = `<span class="gicon">${iconSvg(name)}</span><span class="gtext"><span class="gname"></span><span class="gcount">${n(items.length)} file${items.length === 1 ? "" : "s"}</span></span><svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>`;
    b.querySelector(".gname").textContent = name;
    b.addEventListener("click", () => { S.openGroup = name; renderReview(); });
    list.appendChild(b);
  }
  const name = S.openGroup;
  const items = name ? groupItems(name) : [];
  $("gv-title").textContent = name || "Nothing sorted yet";
  $("gv-count").textContent = name ? `${n(items.length)} file${items.length === 1 ? "" : "s"}` : "";
  const special = SPECIAL.includes(name);
  $("gv-sub").textContent = name === DUPES
    ? "Exact copies and other edits of pictures in your groups. Your folders get the best version of each, so these are left out unless you keep one."
    : name === REVIEW ? "These look blurry. Keep the ones you want. Skipped files are never copied."
    : name === FAILED ? "We couldn't open these. They may be damaged. Your files haven't been changed."
      : name === SKIPPED ? "These won't be copied. Changed your mind? Keep a file to put it back in its group."
        : "Wrong group? Select files and move them, or open one to check it.";
  const copiesLeft = items.filter((i) => i.duplicate_kind === "exact" || i.duplicate_kind === "edit").length;
  $("group-actions").hidden = !(name === DUPES && copiesLeft);
  $("skip-copies").textContent = `Keep the best of each, skip ${n(copiesLeft)} cop${copiesLeft === 1 ? "y" : "ies"}`;
  const grid = $("gv-grid");
  grid.className = special ? "tiles" : "photos";
  grid.innerHTML = "";
  const shown = items.slice(0, 600);
  for (const it of shown) grid.appendChild(special ? tile(it) : photoTile(it));
  if (items.length > shown.length) grid.appendChild(el("p", { class: "sub" }, `Showing the first ${n(shown.length)} of ${n(items.length)}.`));
  $("sel-all").checked = items.length > 0 && items.every((i) => S.selected.has(i.id));
  $("sel-all").disabled = !items.length || name === FAILED;
  // Right panel.
  $("gp-name").value = name || "";
  $("rename-form").hidden = special || !name;
  $("gp-files").textContent = name ? `${n(items.length)} file${items.length === 1 ? "" : "s"}` : "";
  const kinds = new Set(items.map((i) => i.kind));
  const sub = kinds.size > 1 ? "Photos and Videos" : kinds.has("video") ? "Videos" : "Photos";
  $("gp-dest").textContent = special ? (name === REVIEW || name === DUPES ? "Left out until you decide" : "Not copied") : `${S.snap.folder} sorted / ${sub} / ${name}`;
  renderMove();
}
function photoTile(it) {
  const t = el("div", { class: "ptile" + (S.selected.has(it.id) ? " selected" : "") });
  const open = el("button", { type: "button", class: "open", "aria-label": `Preview ${it.name}` });
  if (it.thumb) open.appendChild(Object.assign(new Image(), { src: thumbUrl(it.thumb), alt: "", loading: "lazy" }));
  if (it.kind === "video") open.appendChild(el("span", { class: "kind" }, `Video ${fmtTime(it.duration)}`));
  else if (it.blurry || it.duplicate_kind === "similar") open.appendChild(el("span", { class: "kind" }, it.blurry ? "Blurry" : "Similar shot"));
  open.addEventListener("click", () => openViewer(it.id));
  const pick = el("label", { class: "pick" });
  const cb = el("input", { type: "checkbox", "aria-label": `Select ${it.name}` });
  cb.checked = S.selected.has(it.id);
  cb.addEventListener("change", () => {
    cb.checked ? S.selected.add(it.id) : S.selected.delete(it.id);
    t.classList.toggle("selected", cb.checked);
    renderSelbar();
    renderMove();
  });
  pick.appendChild(cb);
  t.append(open, pick);
  return t;
}
function renderMove() {
  const sel = [...S.selected].filter((id) => { const it = S.items.get(id); return it && groupOf(it) === S.openGroup; });
  $("gp-move").hidden = !sel.length || S.openGroup === FAILED;
  $("gp-move-n").textContent = `${n(sel.length)} file${sel.length === 1 ? "" : "s"}`;
  const to = $("gp-move-to");
  if (to.dataset.k !== S.snap.categories.join()) {
    to.dataset.k = S.snap.categories.join();
    to.innerHTML = S.snap.categories.map((c) => `<option>${esc(c)}</option>`).join("");
  }
}
$("sel-all").addEventListener("change", () => {
  for (const it of groupItems(S.openGroup)) $("sel-all").checked ? S.selected.add(it.id) : S.selected.delete(it.id);
  renderReview();
  renderSelbar();
});
$("gp-move-go").addEventListener("click", async () => {
  const to = $("gp-move-to").value;
  const ids = [...S.selected].filter((id) => groupOf(S.items.get(id)) === S.openGroup);
  for (const id of ids) await api(`/api/items/${id}`, { category: to }).catch((e) => toast(e.message));
  ids.forEach((id) => S.selected.delete(id));
  renderSelbar();
  toast(`Moved ${n(ids.length)} file${ids.length === 1 ? "" : "s"} to ${to}. Looking for others like ${ids.length === 1 ? "it" : "them"}…`);
});
$("rename-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const from = S.openGroup, to = $("gp-name").value.trim();
  if (!to || to === from) return;
  try {
    await api("/api/rename-group", { from, to });
    S.openGroup = to;
    const k = groupItems(to).length;
    toast(`Renamed to ${to}. Its ${n(k)} file${k === 1 ? "" : "s"} will be copied into a folder with that name.`);
  } catch (err) { toast(err.message); }
});
function renderGroupDialog() { if (S.view === "review") renderReview(); }
function tile(it, result) {
  const t = el("div", { class: "tile" + (S.selected.has(it.id) ? " selected" : "") });
  const start = result ? result.start || 0 : 0;
  const img = result && result.thumb ? result.thumb : it.thumb;
  const kindLabel = it.kind === "video" ? (result && result.start ? `Video at ${fmtTime(start)}` : `Video ${fmtTime(it.duration)}`) : "Photo";
  const open = el("button", { type: "button", class: "open", "aria-label": `Preview ${it.name}` });
  if (img) open.appendChild(Object.assign(new Image(), { src: thumbUrl(img), alt: "" }));
  else open.appendChild(el("span", { class: "noimg" }, "No preview"));
  open.appendChild(el("span", { class: "kind" }, esc(kindLabel)));
  open.addEventListener("click", () => openViewer(it.id, start));
  t.appendChild(open);
  if (it.status === "ready" || it.status === "review") {
    const pick = el("label", { class: "pick" });
    const cb = el("input", { type: "checkbox", "aria-label": `Select ${it.name}` });
    cb.checked = S.selected.has(it.id);
    cb.addEventListener("change", () => {
      cb.checked ? S.selected.add(it.id) : S.selected.delete(it.id);
      t.classList.toggle("selected", cb.checked);
      renderSelbar();
    });
    pick.appendChild(cb);
    t.appendChild(pick);
  }
  const body = el("div", { class: "body" });
  body.appendChild(el("div", { class: "name", title: it.name }, esc(it.name)));
  if (result && result.versions > 1) body.appendChild(el("div", { class: "versions" }, `${result.versions} versions of this picture`));
  if (result) {
    body.appendChild(el("div", { class: "why" }, result.why === "said"
      ? `Said: <q>${esc(result.quote)}</q>`
      : result.why === "name" ? "Name matches" : "Looks like a match"));
    const row = el("div", { class: "row2" });
    row.appendChild(el("button", { type: "button", class: "btn", onclick: () => addClip(it.id, result.start, result.end) }, "Add to my video"));
    body.appendChild(row);
  } else if (it.status === "failed") {
    body.appendChild(el("div", { class: "reason" }, esc(it.error)));
  } else {
    if (it.decision === "skip" && it.status === "ready") {
      const row = el("div", { class: "row2" });
      row.appendChild(el("button", { type: "button", class: "btn", onclick: () => decide(it.id, "keep") }, "Keep after all"));
      body.appendChild(row);
    } else if (it.status === "review") {
      body.appendChild(el("div", { class: "reason" }, esc(reasonText(it))));
      const row = el("div", { class: "row2" });
      row.appendChild(el("button", { type: "button", class: "btn", onclick: () => decide(it.id, "keep") }, "Keep"));
      row.appendChild(el("button", { type: "button", class: "btn", onclick: () => decide(it.id, "skip") }, "Skip"));
      body.appendChild(row);
    }
    body.appendChild(categorySelect(it));
  }
  t.appendChild(body);
  return t;
}
function categorySelect(it) {
  const label = el("label", {});
  label.appendChild(el("span", { class: "sr-only" }, `Group for ${esc(it.name)}`));
  const sel = el("select", { "aria-label": `Move ${it.name} to` });
  for (const c of S.snap.categories) {
    const o = el("option", { value: c }, esc(c));
    if (c === it.category) o.selected = true;
    sel.appendChild(o);
  }
  sel.addEventListener("change", () => moveTo(it.id, sel.value));
  label.appendChild(sel);
  return label;
}
async function moveTo(id, category) {
  try {
    const r = await api(`/api/items/${id}`, { category });
    toast(`Moved ${r.item.name} to ${category}.`);
  } catch (e) { toast(e.message); }
}
async function decide(id, decision) {
  try {
    const r = await api(`/api/items/${id}`, { decision });
    toast(decision === "keep" ? `Kept ${r.item.name}. It's now in ${r.item.category}.` : `Skipped ${r.item.name}. It won't be copied.`);
  } catch (e) { toast(e.message); }
}
document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));
$("viewer").addEventListener("close", () => { const v = $("viewer-media").querySelector("video"); if (v) v.pause(); });

// ------------------------------------------------------------------ viewer
function openViewer(id, start = 0) {
  const it = S.items.get(id);
  if (!it) return;
  $("viewer-title").textContent = it.name;
  const m = $("viewer-media");
  m.innerHTML = "";
  let video = null;
  if (it.kind === "video") {
    video = el("video", { controls: true, playsinline: true, preload: "metadata", src: `/media/${it.id}` });
    video.addEventListener("loadedmetadata", () => { video.currentTime = start; }, { once: true });
    m.appendChild(video);
  } else {
    m.appendChild(el("img", { src: `/media/${it.id}`, alt: it.name }));
  }
  const side = $("viewer-side");
  side.innerHTML = "";
  const dl = el("dl");
  dl.innerHTML = `<dt>Group</dt><dd>${esc(groupOf(it) || "Not sorted yet")}</dd>` +
    (it.folder ? `<dt>Folder</dt><dd>${esc(it.folder)}</dd>` : "") +
    (it.kind === "video" ? `<dt>Length</dt><dd>${fmtTime(it.duration)}</dd>` : "") +
    (reasonText(it) ? `<dt>Note</dt><dd>${esc(reasonText(it))}</dd>` : "");
  side.appendChild(dl);
  if (it.status === "ready" || it.status === "review") side.appendChild(categorySelect(it));

  if (it.kind === "video") {
    if (it.best && it.best.length) {
      side.appendChild(el("h3", {}, "Best bits"));
      const bits = el("div", { class: "bits" });
      for (const b of it.best) {
        const row = el("div", { class: "bit" });
        const play = el("button", { type: "button", class: "link", onclick: () => { video.currentTime = b.start; video.play(); } },
          `${fmtTime(b.start)} ${esc(b.label)}`);
        row.appendChild(play);
        row.appendChild(el("button", { type: "button", class: "btn", onclick: () => addClip(it.id, b.start, b.end) }, "Add"));
        bits.appendChild(row);
      }
      side.appendChild(bits);
    }
    side.appendChild(el("h3", {}, "What's said"));
    if (it.transcript && it.transcript.length) {
      const ul = el("ul", { class: "said" });
      for (const s of it.transcript) {
        const li = el("li");
        const btn = el("button", { type: "button", onclick: () => { video.currentTime = s.start; video.play(); } },
          `<time>${fmtTime(s.start)}</time><span>${esc(s.text)}</span>`);
        btn.dataset.start = s.start;
        btn.dataset.end = s.end;
        li.appendChild(btn);
        ul.appendChild(li);
      }
      side.appendChild(ul);
      video.addEventListener("timeupdate", () => {
        ul.querySelectorAll("button").forEach((b) => b.classList.toggle("now", video.currentTime >= +b.dataset.start && video.currentTime < +b.dataset.end));
      });
    } else {
      side.appendChild(el("p", { class: "sub" }, it.status === "waiting" ? "We'll listen once this video has been sorted." : "No speech in this video."));
    }
    side.appendChild(el("button", { type: "button", class: "btn primary", onclick: () => {
      const t = video.currentTime || 0;
      addClip(it.id, t, Math.min(it.duration || t + 5, t + 5));
    } }, "Add 5 seconds from here"));
  } else if (it.status === "ready" || it.status === "review") {
    side.appendChild(el("button", { type: "button", class: "btn primary", onclick: () => addClip(it.id, 0, 3) }, "Add to my video"));
  }
  $("viewer").showModal();
}

// ------------------------------------------------------------------ selection
function renderSelbar() {
  const n = S.selected.size;
  $("selbar").hidden = !n;
  $("sel-count").textContent = `${n} selected`;
}
$("sel-clear").addEventListener("click", () => {
  S.selected.clear();
  document.querySelectorAll(".tile.selected").forEach((t) => { t.classList.remove("selected"); const c = t.querySelector("input"); if (c) c.checked = false; });
  renderSelbar();
});
$("sel-cut").addEventListener("click", () => {
  for (const id of S.selected) {
    const it = S.items.get(id);
    addClip(id, 0, it.kind === "video" ? Math.min(it.duration || 5, 5) : 3, true);
  }
  saveCut();
  toast(`Added ${S.selected.size} to your video.`);
});
$("project-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await api("/api/copy", { to: "project", name: $("project-name").value, ids: [...S.selected] });
    toast(`Copied ${r.copied} file${r.copied === 1 ? "" : "s"} to ${r.folder}.`);
  } catch (err) { toast(err.message); }
});
// ------------------------------------------------------------------ create the folders
let planTimer;
function loadPlan() {
  clearTimeout(planTimer);
  planTimer = setTimeout(async () => {
    try {
      const p = await (await fetch("/api/folders-plan")).json();
      S.plan = p;
      if (S.copying || S.copied) return;   // already creating them; keep the progress showing
      const where = p.target.replace(/^\/Users\/[^/]+/, "").replace(/^\//, "").split("/").join(" › ");
      $("next-note").textContent = `Creates ${n(p.folders)} folder${p.folders === 1 ? "" : "s"} with ${n(p.files)} photo${p.files === 1 ? "" : "s"} and videos in ${where}.`;
      $("copy-explain").textContent = `${p.left_out ? `The best version of each picture is kept, so ${n(p.left_out)} extra cop${p.left_out === 1 ? "y is" : "ies are"} left out. ` : ""}Your originals stay exactly where they are.`;
    } catch (e) { /* the plan is a nicety; the button still works */ }
  }, 300);
}
async function createFolders() {
  if (S.copying) return;
  try {
    S.copying = true;
    $("next-more").hidden = true;   // nothing else to press while copying
    for (const id of ["copy-folders", "copy-folders-2"]) { $(id).disabled = true; $(id).textContent = "Creating the folders…"; }
    $("copy-explain").textContent = "Starting…";
    await api("/api/copy", {});
  } catch (e) {
    S.copying = false;
    $("next-more").hidden = false;
    for (const id of ["copy-folders", "copy-folders-2"]) { $(id).disabled = false; $(id).textContent = "Create the folders"; }
    toast(e.message);
  }
}
$("copy-folders").addEventListener("click", createFolders);
$("copy-folders-2").addEventListener("click", createFolders);
function copyEvent(ev) {
  if (ev.type === "copy") {
    if (!S.copying) {
      S.copying = true;
      $("next-more").hidden = true;   // nothing else to press while copying
      for (const id of ["copy-folders", "copy-folders-2"]) { $(id).disabled = true; $(id).textContent = "Creating the folders…"; }
      $("next-note").textContent = "Creating your folders now.";
    }
    $("copy-explain").textContent = ev.total ? `Copying ${n(ev.done)} of ${n(ev.total)}…` : "Starting…";
    if (ev.total) $("progress").firstElementChild.style.width = Math.round((100 * ev.done) / ev.total) + "%";
  }
  if (ev.type === "copy_done") {
    S.copying = false;
    $("next-more").hidden = false;
    S.copied = ev;
    S.copiedFolders = ev.folders;
    for (const id of ["copy-folders", "copy-folders-2"]) { $(id).disabled = false; $(id).textContent = "Create them again"; }
    $("review-btn").textContent = "Check the groups";
    $("done-word").textContent = "All tidied up.";     // only once the folders exist
    $("next-note").textContent = `Done. ${n(ev.folders)} folder${ev.folders === 1 ? "" : "s"} made with ${n(ev.copied)} file${ev.copied === 1 ? "" : "s"}.`;
    $("copy-explain").innerHTML = "";
    $("copy-explain").appendChild(el("button", { type: "button", class: "btn primary", onclick: () => api("/api/reveal", { path: ev.folder }) }, "View organised folders"));
    if (!ev.quiet) toast(`All tidied up. ${n(ev.folders)} folders made. Your originals are untouched.`);
    renderCounts(S.snap.counts, S.snap.state);
  }
  if (ev.type === "copy_failed") {
    S.copying = false;
    $("next-more").hidden = false;
    for (const id of ["copy-folders", "copy-folders-2"]) { $(id).disabled = false; $(id).textContent = "Create the folders"; }
    $("copy-explain").textContent = ev.message;
  }
}

// ------------------------------------------------------------------ find
const EXAMPLES = ["holiday sunsets", "the video where I talked about my business", "playing music", "the clip where I said we won", "flowers"];
for (const ex of EXAMPLES) {
  $("chips").appendChild(el("button", { type: "button", onclick: () => { $("q").value = ex; runSearch(); } }, esc(ex)));
}
$("search-form").addEventListener("submit", (e) => { e.preventDefault(); runSearch(); });
async function runSearch() {
  const q = $("q").value.trim();
  const box = $("results");
  if (!q) return;
  const c = S.snap ? S.snap.counts : null;
  $("find-status").textContent = "Looking…";
  try {
    const r = await (await fetch(`/api/search?q=${encodeURIComponent(q)}`)).json();
    box.innerHTML = "";
    const notDone = c ? (c.waiting || 0) + (c.analysing || 0) : 0;
    const note = notDone ? ` ${notDone} file${notDone > 1 ? "s" : ""} haven't been sorted yet, so they aren't included.` : "";
    if (!r.results || !r.results.length) {
      $("find-status").textContent = `Nothing matched “${q}”. Try fewer words, or describe what's in the shot.${note}`;
      return;
    }
    $("find-status").textContent = `${r.results.length} match${r.results.length > 1 ? "es" : ""} for “${q}”.${note}`;
    for (const res of r.results) {
      const it = S.items.get(res.id);
      if (it) box.appendChild(tile(it, res));
    }
  } catch (e) {
    $("find-status").textContent = "Search didn't work just now. Try again.";
  }
}

// ------------------------------------------------------------------ first cut
function addClip(id, start, end, quiet) {
  const it = S.items.get(id);
  if (!it) return;
  if (it.kind === "photo") { start = 0; end = 3; }
  S.cut.push({ id, start: +(+start).toFixed(1), end: +(+end).toFixed(1) });
  if (!quiet) { saveCut(); toast(`Added ${it.name}${it.kind === "video" ? ` from ${fmtTime(start)}` : ""} to your video.`); }
}
let saveTimer;
function saveCut() {
  $("cut-count").hidden = !S.cut.length;
  $("cut-count").textContent = S.cut.length;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => api("/api/cut", { clips: S.cut }).then((r) => { S.cut = r.cut; if (S.view === "cut") renderCut(); }).catch((e) => toast(e.message)), 250);
  if (S.view === "cut") renderCut();
}
let dragFrom = null;
function renderCut() {
  const list = $("cut-list");
  list.innerHTML = "";
  $("cut-empty").hidden = !!S.cut.length;
  $("cut-foot").hidden = !S.cut.length;
  let total = 0;
  S.cut.forEach((c, i) => {
    const it = S.items.get(c.id);
    if (!it) return;
    const len = it.kind === "video" ? c.end - c.start : 3;
    total += len;
    const li = el("li", { class: "clip", draggable: "true" });
    li.appendChild(Object.assign(new Image(), { src: thumbUrl(it.kind === "video" ? frameNear(it, c.start) : it.thumb), alt: "" }));
    const meta = el("div", { class: "meta" });
    meta.appendChild(el("div", { class: "name" }, esc(it.name)));
    const trim = el("div", { class: "trim" });
    if (it.kind === "video") {
      trim.appendChild(numberField(`From`, c.start, 0, it.duration, (v) => { c.start = v; if (c.end <= v) c.end = Math.min(it.duration, v + 1); saveCut(); }));
      trim.appendChild(numberField(`To`, c.end, 0, it.duration, (v) => { c.end = Math.max(v, c.start + 0.5); saveCut(); }));
      trim.appendChild(el("span", {}, `${len.toFixed(1)}s of ${fmtTime(it.duration)}`));
    } else {
      trim.appendChild(el("span", {}, "Photo, shown for 3 seconds"));
    }
    meta.appendChild(trim);
    li.appendChild(meta);
    const tools = el("div", { class: "tools" });
    tools.appendChild(el("button", { type: "button", "aria-label": `Move ${it.name} earlier`, disabled: i === 0, onclick: () => moveClip(i, i - 1) }, "↑"));
    tools.appendChild(el("button", { type: "button", "aria-label": `Move ${it.name} later`, disabled: i === S.cut.length - 1, onclick: () => moveClip(i, i + 1) }, "↓"));
    tools.appendChild(el("button", { type: "button", "aria-label": `Preview ${it.name}`, onclick: () => openViewer(it.id, c.start) }, "▶"));
    tools.appendChild(el("button", { type: "button", "aria-label": `Remove ${it.name}`, onclick: () => { S.cut.splice(i, 1); saveCut(); } }, "×"));
    li.appendChild(tools);
    li.addEventListener("dragstart", () => { dragFrom = i; li.classList.add("dragging"); });
    li.addEventListener("dragend", () => li.classList.remove("dragging"));
    li.addEventListener("dragover", (e) => { e.preventDefault(); li.classList.add("over"); });
    li.addEventListener("dragleave", () => li.classList.remove("over"));
    li.addEventListener("drop", (e) => { e.preventDefault(); li.classList.remove("over"); if (dragFrom !== null) moveClip(dragFrom, i); dragFrom = null; });
    list.appendChild(li);
  });
  $("cut-total").textContent = `${S.cut.length} clip${S.cut.length === 1 ? "" : "s"}, ${fmtTime(total)} in total`;
}
function frameNear(it, t) {
  if (!it.frames || !it.frames.length) return it.thumb;
  return it.frames.reduce((a, f) => (Math.abs(f.t - t) < Math.abs(a.t - t) ? f : a)).thumb;
}
function numberField(label, value, min, max, onChange) {
  const l = el("label", {}, `${label} `);
  const inp = el("input", { type: "number", step: "0.5", min, max: (max || 0).toFixed(1), value: (+value).toFixed(1), inputmode: "decimal" });
  inp.addEventListener("change", () => onChange(Math.max(min, Math.min(max || 0, parseFloat(inp.value) || 0))));
  l.appendChild(inp);
  l.appendChild(document.createTextNode(" s"));
  return l;
}
function moveClip(from, to) {
  if (to < 0 || to >= S.cut.length || from === to) return;
  const [c] = S.cut.splice(from, 1);
  S.cut.splice(to, 0, c);
  saveCut();
}
$("make-cut").addEventListener("click", async () => {
  try {
    S.exporting = true;
    $("make-cut").disabled = true;
    $("make-cut").textContent = "Making your first draft…";
    $("cut-done").hidden = false;
    $("cut-done").innerHTML = `<p class="status" role="status">Starting…</p>`;
    await api("/api/cut/export", { clips: S.cut, shape: $("shape").value });
  } catch (e) {
    S.exporting = false;
    $("make-cut").disabled = false;
    $("make-cut").textContent = "Make my first draft";
    $("cut-done").innerHTML = `<p class="error">${esc(e.message)}</p>`;
  }
});

// ------------------------------------------------------------------ live events
function onEvent(ev) {
  if (ev.type === "library") { applySnapshot(ev.snapshot); return; }
  if (!S.snap) return;
  if (ev.lib && S.snap.lib && ev.lib !== S.snap.lib) return; // from a folder you've moved on from
  if (ev.counts) S.snap.counts = ev.counts;
  if (ev.type === "state") { S.snap.state = ev.state; modeHint(S.snap); }
  if (ev.type === "mode") { S.snap.mode = ev.mode; S.snap.categories = ev.categories; modeHint(S.snap); rebuildPhotoCards(); }
  if (ev.type === "status") { $("status").textContent = ev.text + "…"; return; }
  if (ev.type === "regrouped") toast(ev.moved
    ? `Learnt from your change. ${n(ev.moved)} other photo${ev.moved === 1 ? "" : "s"} that look like it moved too.`
    : "Learnt from your change. Nothing else looked close enough to move.");
  if (ev.type === "thumb" || ev.type === "item") {
    const prev = S.items.get(ev.item.id);
    S.items.set(ev.item.id, ev.item);
    const was = prev ? groupOf(prev) : null;
    const now = groupOf(ev.item);
    if (!now) addToPile(ev.item);
    else if (!was) addToGroup(ev.item, true);
    else if (was !== now) { refreshGroup(was); refreshGroup(now); }
    else refreshGroup(now);
    if (S.view === "review") renderReview();
  }
  if (ev.type === "copy" || ev.type === "copy_done" || ev.type === "copy_failed") { copyEvent(ev); return; }
  if (ev.type === "export") {
    const box = $("cut-done").querySelector(".status");
    if (box) box.textContent = ev.done < ev.total ? `Adding clip ${ev.done} of ${ev.total - 1}…` : "Joining it all together…";
  }
  if (ev.type === "export_done") {
    S.exporting = false;
    $("make-cut").disabled = false;
    $("make-cut").textContent = "Make my first draft";
    const d = $("cut-done");
    d.innerHTML = "";
    const parts = ev.file.split("/");
    const name = parts.pop();
    const where = parts.join("/").replace(/^\/Users\/[^/]+/, "your home folder");
    d.appendChild(el("p", { role: "status" }, `Done. Your first draft is saved as <strong>${esc(name)}</strong> in ${esc(where)}. A list of the clips is saved next to it, so you can rebuild it in any editor.`));
    d.appendChild(el("video", { controls: true, src: `/made?path=${encodeURIComponent(ev.file)}`, playsinline: true }));
    d.appendChild(el("button", { type: "button", class: "btn", onclick: () => api("/api/reveal", { path: ev.file }) }, "Show in Finder"));
  }
  if (ev.type === "export_failed") {
    S.exporting = false;
    $("make-cut").disabled = false;
    $("make-cut").textContent = "Make my first draft";
    $("cut-done").innerHTML = `<p class="error">${esc(ev.message)} Try again, or remove the clip that caused it.</p>`;
  }
  scheduleCounts();
}

// Redraw the counters at most once a frame, however many files finish in it.
let countsQueued = false;
function scheduleCounts() {
  if (countsQueued) return;
  countsQueued = true;
  requestAnimationFrame(() => { countsQueued = false; if (S.snap) renderCounts(S.snap.counts, S.snap.state); });
}

async function init() {
  const st = await (await fetch("/api/state")).json();
  if (st.state === "empty") {
    modelNote(st.models);
    show("start");
  } else {
    applySnapshot(st);
  }
  const es = new EventSource("/events");
  es.onmessage = (m) => onEvent(JSON.parse(m.data));
  es.onerror = () => { $("status").textContent = "Lost touch with the app. Is it still running in Terminal?"; };
  es.onopen = () => { if (S.snap) fetch("/api/state").then((r) => r.json()).then((s) => { if (s.items) applySnapshot(s); }); };
}
init();

// ------------------------------------------------------------------ grouping mode
function modeHint(snap) {
  const sel = $("mode");
  const auto = sel.querySelector('option[value="auto"]');
  auto.textContent = snap.mode_setting === "auto" && ["analysing", "paused", "done"].includes(snap.state)
    ? `Let us work it out (${snap.mode === "event" ? "one event" : "everyday life"})` : "Let us work it out";
}
$("mode").addEventListener("change", async () => {
  try {
    await api("/api/mode", { mode: $("mode").value });
    toast($("mode").value === "event" ? "Grouped by the parts of the day. Anything you moved stays where you put it." : "Regrouped. Anything you moved stays where you put it.");
  } catch (e) { toast(e.message); }
});
$("skip-copies").addEventListener("click", async () => {
  try {
    const r = await api("/api/review/skip-copies", {});
    toast(`Skipped ${n(r.skipped)} cop${r.skipped === 1 ? "y" : "ies"}. The best version of each is kept. Similar shots are still yours to choose.`);
  } catch (e) { toast(e.message); }
});
