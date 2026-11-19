"""Persistent PyMOL session driven by the bridge (one per scene).

Protocol on stdin/stdout, one request per line:
  request : JSON line  {"w","h","ray","turn":[dx_deg,dy_deg],"pan":[px,py],"zoom":f,
                        "scene":"F1","do":"cmd","reset":true,"info":true}
  response: JSON header line {"ok","len","view","scenes",...} then `len` bytes of PNG.

Modes (env):
  AXPYMOL_GL=1 (default): launched *as* PyMOL's main program (`pymol -q worker.py`) so the Qt/OpenGL
      window lives on the main thread (macOS requires it); requests are served from a thread and
      png(ray=0) is a real GPU draw.
  AXPYMOL_GL=0: headless `python worker.py <scene>`; png(ray=0) silently ray-traces (seconds/frame).
  AXPYMOL_FAKE=1: stub instead of PyMOL, for testing the plumbing.
Scene path comes from argv[1] or $AXPYMOL_SCENE; this file's folder from $AXPYMOL_DIR.
"""
import json, math, os, struct, subprocess, sys, tempfile, threading, zlib

# PyMOL and user scripts print to stdout; keep the real stdout private for the protocol.
OUT = os.fdopen(os.dup(1), "wb", buffering=0)
os.dup2(2, 1)
FAKE = os.environ.get("AXPYMOL_FAKE") == "1"
GL = (not FAKE) and os.environ.get("AXPYMOL_GL", "1") == "1"
HERE = os.environ.get("AXPYMOL_DIR") or os.path.dirname(os.path.abspath(__file__))
SCENE = os.environ.get("AXPYMOL_SCENE") or sys.argv[1]
sys.path.insert(0, HERE)

if FAKE:
    class _Cmd:
        angle = 0.0
        def get_view(self): return [1.0] * 11 + [-100.0] + [0.0] * 6
    cmd = _Cmd()
    def load_scene(p): pass
    def fake_png(path, w, h, angle):
        w, h = max(8, min(w, 2000)), max(8, min(h, 2000))
        rows = b"".join(b"\x00" + bytes(v for x in range(w) for v in (
            int(127 + 127 * math.sin(x / 40 + angle)), int(255 * y / h), 160)) for y in range(h))
        def chunk(t, d):
            c = struct.pack(">I", len(d)) + t + d
            return c + struct.pack(">I", zlib.crc32(t + d))
        open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                                + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
else:
    import pymol
    from pymol import cmd
    if not GL:
        pymol.finish_launching(["pymol", "-cq"])
    from scene_loader import load_scene

FAST = {"ray_shadow": 0, "antialias": 0, "ray_trace_mode": 0, "ray_trace_fog": 0,
        "ambient_occlusion_mode": 0, "ray_max_passes": 1}
INITIAL = QUALITY = TMP = None


def reply(obj, data=b""):
    obj["len"] = len(data)
    OUT.write(json.dumps(obj).encode() + b"\n" + data)


def hide_window():
    """Best effort: hide the PyMOL window; needs Automation/Accessibility permission, else ignored."""
    try:
        subprocess.run(["osascript", "-e", 'tell application "System Events" to set visible of '
                        f'(first process whose unix id is {os.getpid()}) to false'], timeout=10,
                       capture_output=True)
    except Exception:
        pass


def init():
    global INITIAL, QUALITY, TMP
    load_scene(SCENE)
    INITIAL = list(cmd.get_view())
    QUALITY = {} if FAKE else {k: cmd.get(k) for k in FAST}  # whatever the .pml/.pse set up
    TMP = os.path.join(tempfile.mkdtemp(prefix="axpymol_"), "frame.png")
    if GL and os.environ.get("AXPYMOL_HIDE", "1") == "1":
        hide_window()


def handle(req):
    if not FAKE:
        if req.get("reset"): cmd.set_view(INITIAL)
        if req.get("scene"): cmd.do(f"scene {req['scene']}, recall")
        if req.get("do"): cmd.do(req["do"])
    dx, dy = req.get("turn", (0, 0))
    w, h = int(req.get("w", 800)), int(req.get("h", 600))
    ray = int(req.get("ray", 0))
    if FAKE:
        cmd.angle += math.radians(dx)
        fake_png(TMP, w, h, cmd.angle)
    else:
        if dx: cmd.turn("y", dx)
        if dy: cmd.turn("x", dy)
        v = cmd.get_view()
        dist = abs(v[11]) or 100.0
        per_px = 0.353 * dist / max(1, h)  # visible height at the focal plane for PyMOL's default 20° fov
        px, py = req.get("pan", (0, 0))
        if px or py:
            cmd.move("x", px * per_px); cmd.move("y", -py * per_px)
        if req.get("zoom"): cmd.move("z", req["zoom"] * dist)
        if GL and not ray:
            cmd.png(TMP, w, h, dpi=-1, ray=0)
        else:
            # Headless png(ray=0) is really a ray trace; ray=0 frames strip the expensive parts,
            # ray=1 restores the user's quality settings.
            for k, val in (QUALITY if ray else FAST).items():
                cmd.set(k, val)
            cmd.png(TMP, w, h, dpi=-1, ray=1)
        cmd.sync()
    data = open(TMP, "rb").read()
    info = {"ok": True, "view": list(cmd.get_view())}
    if req.get("info"):
        info["scenes"] = [] if FAKE else list(cmd.get_scene_list())
    reply(info, data)


def serve():
    try:
        init()
    except Exception as e:
        reply({"ok": False, "error": f"scene load failed: {type(e).__name__}: {e}"})
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            handle(json.loads(line))
        except Exception as e:  # keep the session alive on bad requests
            reply({"ok": False, "error": f"{type(e).__name__}: {e}"})
    if GL:
        cmd.quit()


if GL:
    threading.Thread(target=serve, daemon=True).start()  # PyMOL keeps the main thread for the GUI
else:
    serve()
