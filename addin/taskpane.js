const BRIDGE = location.origin; // add-in is served by the bridge
const TOKEN = document.querySelector('meta[name="axpymol-token"]').content;
const api = (path, opts = {}) => fetch(BRIDGE + path, { ...opts, headers: { ...(opts.headers || {}), "X-Axpymol-Token": TOKEN } });

const TAG = "AXPYMOL_ID";
const $ = (id) => document.getElementById(id);
const say = (m) => ($("status").textContent = m);

function b64(buf) {
  let s = "", u = new Uint8Array(buf);
  for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000));
  return btoa(s);
}
const setImage = (png64, w, h) => new Promise((res, rej) =>
  Office.context.document.setSelectedDataAsync(png64,
    { coercionType: Office.CoercionType.Image, imageWidth: w / 2, imageHeight: h / 2 },
    (r) => (r.status === Office.AsyncResultStatus.Succeeded ? res() : rej(r.error))));

async function selectedShape() {
  return PowerPoint.run(async (ctx) => {
    const shapes = ctx.presentation.getSelectedShapes();
    shapes.load("items"); await ctx.sync();
    if (!shapes.items.length) return null;
    const sh = shapes.items[0]; sh.tags.load("items/key,items/value"); await ctx.sync();
    const t = sh.tags.items.find((x) => x.key === TAG);
    return t ? t.value : null;
  });
}

async function insertScene(files) {
  files = [...files];
  const lonePml = files.length === 1 && /\.pml$/i.test(files[0].name);
  const file = files.find((f) => /\.(pml|pse)$/i.test(f.name)) || files[0];
  say("Rendering in PyMOL…");
  const payload = { main: file.name, files: await Promise.all(files.map(async (f) => ({ name: f.name, b64: b64(await f.arrayBuffer()) }))) };
  const r = await api(`/scenes`, { method: "POST", body: JSON.stringify(payload) });
  const j = await r.json(); if (!r.ok) throw new Error(j.error + " " + (j.detail || ""));
  const w = +$("w").value, h = +$("h").value;
  await setImage(j.png_base64, w, h);
  await PowerPoint.run(async (ctx) => {
    // The new image is the last (topmost) shape on the current slide; the selection may not have updated yet.
    const slide = ctx.presentation.getSelectedSlides().getItemAt(0);
    slide.shapes.load("items"); await ctx.sync();
    const sh = slide.shapes.items[slide.shapes.items.length - 1];
    if (!sh) throw new Error("Inserted image not found on the slide");
    sh.tags.add(TAG, j.id);
    sh.name = "AxPyMOL " + file.name;
    sh.altTextDescription = "PyMOL scene " + file.name;
    try { sh.setHyperlink({ address: `${BRIDGE}/open/${j.id}`, screenTip: "Open live in PyMOL" }); } catch (_) {}
    await ctx.sync();
  });
  say(`Inserted ${file.name}. Click it in slideshow mode to open it live.` +
    (lonePml ? " Warning: only the .pml was uploaded; if it loads structure files, use “Insert scene from folder…” instead." : ""));
}

Office.onReady(async () => {
  try { const r = await (await api("/health")).json(); say(r.pymol ? "Bridge ready." : "Bridge running, but PyMOL.app not found."); }
  catch { say("Bridge not reachable."); }
  $("insert").onclick = () => $("file").click();
  $("insertdir").onclick = () => $("dir").click();
  $("dir").onchange = (e) => {
    const fs = [...e.target.files].filter((f) => !f.name.startsWith("."));
    if (!fs.some((f) => /\.(pml|pse)$/i.test(f.name))) return say("No .pml or .pse in that folder.");
    insertScene(fs).catch((x) => say("Error: " + x.message));
  };
  $("file").onchange = (e) => e.target.files.length && insertScene(e.target.files).catch((x) => say("Error: " + x.message));
  $("open").onclick = async () => {
    const id = await selectedShape();
    if (!id) return say("Select an AxPyMOL scene first.");
    api(`/open/${id}`).then(() => say("Opening PyMOL…"));
  };
  $("refresh").onclick = async () => {
    const id = await selectedShape();
    if (!id) return say("Select an AxPyMOL scene first.");
    say("Re-rendering…");
    const r = await api(`/scenes/${id}/render?w=${$("w").value}&h=${$("h").value}&ray=1`, { method: "POST" });
    if (!r.ok) return say("Render failed.");
    await setImage(b64(await r.arrayBuffer()), +$("w").value, +$("h").value);
    say("Re-rendered (inserted as new image; delete the old one).");
  };
});
