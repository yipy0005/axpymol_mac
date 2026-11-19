// Live PyMOL viewer for a slide. Input -> bridge -> persistent PyMOL session -> PNG frames.
// The scene (id + small bundle of its files) is stored in the add-in's document settings so the
// deck is self-contained: if the bridge has forgotten the scene, it is re-uploaded.
const BRIDGE = location.origin;
const TOKEN = document.querySelector('meta[name="axpymol-token"]').content;
const api = (path, opts = {}) => fetch(BRIDGE + path, { ...opts, headers: { ...(opts.headers || {}), "X-Axpymol-Token": TOKEN } });

const KEY = "axpymol";
const BUNDLE_LIMIT = 1.5e6; // bytes of base64 we are willing to store in the deck
const $ = (id) => document.getElementById(id);
const canvas = $("c"), g = canvas.getContext("2d");
let cfg = null, view = { id: null }, pending = { turn: [0, 0], pan: [0, 0], zoom: 0, extra: {} };
let inflight = false, settleTimer = null, dirty = false;
const say = (m) => ($("msg").textContent = m || "");

function b64(buf) {
  let s = "", u = new Uint8Array(buf);
  for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000));
  return btoa(s);
}
const settings = () => Office.context.document.settings;
const saveCfg = () => new Promise((res) => { settings().set(KEY, cfg); settings().saveAsync(() => res()); });

async function upload(files) {
  files = [...files].filter((f) => !f.name.startsWith("."));
  const main = files.find((f) => /\.(pml|pse)$/i.test(f.name)) || files[0];
  if (!main) throw new Error("No files");
  const payload = { main: main.name, files: await Promise.all(files.map(async (f) => ({ name: f.name, b64: b64(await f.arrayBuffer()) }))) };
  const r = await api(`/scenes`, { method: "POST", body: JSON.stringify(payload) });
  const j = await r.json(); if (!r.ok) throw new Error(j.error);
  const size = payload.files.reduce((n, f) => n + f.b64.length, 0);
  cfg = { id: j.id, main: payload.main, bundle: size <= BUNDLE_LIMIT ? payload.files : null };
  if (!cfg.bundle) say("Scene too large to embed in the deck; it will only work where the bridge has it.");
  await saveCfg();
}

// --- helper/bridge status, with a friendly full-view message and automatic reconnect ---
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const health = () => fetch(`${BRIDGE}/health`, { cache: "no-store" }).then((r) => r.json()).catch(() => null);
let helping = false;
function showHelp(title, body, extra = "") {
  $("ht").textContent = title; $("hb").innerHTML = body; $("hx").innerHTML = extra;
  $("help").style.display = "flex"; say("");
}
const hideHelp = () => { $("help").style.display = "none"; };
/** Show `why` until the bridge answers (and PyMOL exists), then resume. */
async function waitForBridge(why) {
  if (helping) return; helping = true;
  for (;;) {
    const h = await health();
    if (!h) {
      showHelp("AxPyMOL Helper isn't running",
        "The helper keeps PyMOL ready for your slides. Open <b>AxPyMOL Helper</b> (atom icon in the menu bar). This view will reconnect by itself.",
        '<a href="axpymol://start">Open AxPyMOL Helper</a>');
    } else if (!h.pymol) {
      showHelp("PyMOL.app not found",
        "AxPyMOL needs PyMOL installed. Install it, or click the atom icon in the menu bar → <b>Choose PyMOL.app…</b>.");
    } else break;
    await sleep(2000);
  }
  helping = false; hideHelp(); start();
}
function fail(e) {
  if (e instanceof TypeError) return waitForBridge(); // network error: bridge went away
  if (/PyMOL session ended/.test(e.message))
    return showHelp("PyMOL stopped", "The PyMOL session ended unexpectedly. Choose the helper's <b>Show Log</b> for details.",
      '<button onclick="location.reload()">Reload</button>');
  say("Error: " + e.message);
}

async function ensureScene() {
  const r = await api(`/scenes/${cfg.id}`);
  if (r.ok) return;
  if (!cfg.bundle) throw new Error("Scene missing on this Mac and not embedded in the deck.");
  say("Restoring scene from deck…");
  const rr = await api(`/scenes`, { method: "POST", body: JSON.stringify({ main: cfg.main, files: cfg.bundle }) });
  const j = await rr.json(); if (!rr.ok) throw new Error(j.error);
  cfg.id = j.id; await saveCfg();
}

// --- frame pump: one request in flight, input coalesced, hi-res "settle" frame after idle ---
function size(scale) {
  const dpr = window.devicePixelRatio || 1;
  return [Math.max(64, Math.round(canvas.clientWidth * dpr * scale)), Math.max(64, Math.round(canvas.clientHeight * dpr * scale))];
}
async function frame(req, fast) {
  const [w, h] = size(fast ? 0.6 : 1);
  const r = await api(`/scenes/${cfg.id}/frame`, { method: "POST", body: JSON.stringify({ ...req, w, h }) });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status);
  const meta = JSON.parse(r.headers.get("X-Axpymol") || "{}");
  const bmp = await createImageBitmap(await r.blob());
  const [cw, ch] = size(1);
  if (canvas.width !== cw || canvas.height !== ch) { canvas.width = cw; canvas.height = ch; }
  g.drawImage(bmp, 0, 0, canvas.width, canvas.height);
  return meta;
}
async function pump() {
  if (inflight || !cfg) return;
  const p = pending;
  pending = { turn: [0, 0], pan: [0, 0], zoom: 0, extra: {} };
  inflight = true;
  try {
    const meta = await frame({ turn: p.turn, pan: p.pan, zoom: p.zoom, ...p.extra, ray: 0, info: !!p.extra.info }, true);
    if (meta.scenes) fillScenes(meta.scenes);
    say("");
  } catch (e) { fail(e); }
  inflight = false;
  if (dirty) { dirty = false; pump(); }
  else {
    clearTimeout(settleTimer);
    settleTimer = setTimeout(settle, 250);
  }
}
async function settle() {
  if (inflight || dirty) return;
  inflight = true;
  try { await frame({ ray: $("ray").checked ? 1 : 0 }, false); } catch (e) { fail(e); }
  inflight = false;
  if (dirty) { dirty = false; pump(); }
}
function push(fn) { fn(pending); dirty = true; clearTimeout(settleTimer); pump(); }

function fillScenes(list) {
  const sel = $("scenes");
  sel.innerHTML = '<option value="">scenes…</option>' + list.map((s) => `<option>${s}</option>`).join("");
}

// --- input: drag = rotate, shift/right/middle drag = pan, wheel = zoom, dblclick = reset ---
let drag = null;
canvas.addEventListener("pointerdown", (e) => { canvas.setPointerCapture(e.pointerId); drag = { x: e.clientX, y: e.clientY, pan: e.shiftKey || e.button !== 0 }; canvas.style.cursor = "grabbing"; });
canvas.addEventListener("pointerup", () => { drag = null; canvas.style.cursor = "grab"; });
canvas.addEventListener("contextmenu", (e) => e.preventDefault());
canvas.addEventListener("pointermove", (e) => {
  if (!drag) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  drag.x = e.clientX; drag.y = e.clientY;
  const dpr = window.devicePixelRatio || 1;
  if (drag.pan) push((p) => { p.pan[0] += dx * dpr * 0.6; p.pan[1] += dy * dpr * 0.6; }); // fast frames are 0.6x res
  else push((p) => { p.turn[0] += dx * 0.5; p.turn[1] += dy * 0.5; });
});
canvas.addEventListener("wheel", (e) => { e.preventDefault(); push((p) => (p.zoom += Math.max(-0.3, Math.min(0.3, e.deltaY * 0.001)))); }, { passive: false });
canvas.addEventListener("dblclick", () => push((p) => (p.extra.reset = true)));
$("reset").onclick = () => push((p) => (p.extra.reset = true));
$("scenes").onchange = (e) => e.target.value && push((p) => (p.extra.scene = e.target.value));
$("cmd").onkeydown = (e) => { if (e.key === "Enter" && e.target.value.trim()) { const c = e.target.value; e.target.value = ""; push((p) => (p.extra.do = c)); } };
$("ray").onchange = () => settle();
window.addEventListener("resize", () => { clearTimeout(settleTimer); settleTimer = setTimeout(settle, 200); });

// --- scene picker ---
async function showPicker() {
  $("pick").style.display = "block";
  try {
    const list = await (await api(`/scenes`)).json();
    $("existing").innerHTML = list.map((s) => `<option value="${s.id}">${s.main} (${s.id})</option>`).join("");
  } catch { $("pmsg").textContent = "Bridge not reachable."; }
}
async function choose(fn) {
  try { $("pmsg").textContent = "Working…"; await fn(); $("pick").style.display = "none"; await start(); }
  catch (e) { $("pmsg").textContent = "Error: " + e.message; }
}
$("use").onclick = () => choose(async () => {
  const id = $("existing").value; if (!id) throw new Error("Pick a scene");
  const j = await (await api(`/scenes/${id}`)).json();
  cfg = { id, main: j.main, bundle: null }; await saveCfg(); // existing bridge scene: not embedded
});
$("upfile").onclick = () => $("file").click();
$("updir").onclick = () => $("dir").click();
$("file").onchange = (e) => choose(() => upload(e.target.files));
$("dir").onchange = (e) => choose(() => upload(e.target.files));
$("change").onclick = showPicker;

async function start() {
  const h = await health();
  if (!h || !h.pymol) return waitForBridge();
  try {
    await ensureScene();
    say("Loading PyMOL…");
    push((p) => (p.extra.info = true));
  } catch (e) { fail(e); }
}

Office.onReady(() => {
  cfg = settings().get(KEY);
  if (cfg && cfg.id) start(); else { say(""); showPicker(); }
});
