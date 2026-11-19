#!/usr/bin/env python3
"""AxPyMOL Mac bridge: local HTTPS service used by the PowerPoint add-in.

POST /scenes          JSON {main, files:[{name,b64}]}  (.pse/.pdb/.cif/.pml + files it loads)
                      -> {"id", "png_base64"}
POST /scenes/<id>/render?w=&h=&ray=   -> re-render, returns PNG bytes
GET  /open/<id>       -> launch live PyMOL window for the scene
GET  /scenes          -> [{id, main, mtime}]       GET /scenes/<id> -> {id, main} or 404
POST /scenes/<id>/frame  JSON {w,h,ray,turn,pan,zoom,scene,do,reset,info}
                      -> PNG from the scene's persistent PyMOL session (X-Axpymol: JSON meta)
GET  /health

Security: every call except /health, /addin/* and /open/* needs the per-install secret in header
X-Axpymol-Token. The token is stored in ~/Library/Application Support/AxPyMOL/token (0600) and injected
into the add-in pages the bridge serves, which a foreign website cannot read (no CORS). Requests must
also carry a localhost Host header (DNS-rebinding) and, if they have an Origin, an allowed one.
/open/* (a hyperlink click in a deck) is refused when the browser says the navigation is cross-site.
"""
import base64, hmac, json, os, secrets, ssl, subprocess, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("AXPYMOL_PORT", "3443"))
HOME = Path(os.environ.get("AXPYMOL_HOME") or Path.home() / "Library/Application Support/AxPyMOL")
SCENES = HOME / "scenes"
CERTS = HOME / "certs"
PYMOL_APP = Path(os.environ.get("PYMOL_APP", "/Applications/PyMOL.app"))
PYMOL_PY = PYMOL_APP / "Contents/bin/python"
RENDER = Path(__file__).with_name("render.py")
ADDIN = Path(__file__).resolve().parent.parent / "addin"
ORIGINS = {f"https://localhost:{PORT}"}
WORKER = Path(__file__).with_name("worker.py")
FAKE = os.environ.get("AXPYMOL_FAKE") == "1"  # tests: stub worker, no PyMOL
INSECURE = os.environ.get("AXPYMOL_INSECURE") == "1"  # tests: plain HTTP
IDLE_SECS = 600
MAX_BODY = 256 * 1024 * 1024
HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
TOKEN = ""  # set in main()
PYMOL_BIN = PYMOL_APP / "Contents/bin/pymol"
GL = (not FAKE) and os.environ.get("AXPYMOL_GL", "1") == "1"
if FAKE:
    GL = False
    PYMOL_PY = Path(sys.executable)
    ORIGINS.add(f"http://localhost:{PORT}")


def load_token():
    f = HOME / "token"
    if not f.exists():
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_urlsafe(32))
    f.chmod(0o600)
    return f.read_text().strip()


def scene_path(sid):
    """Return the main file of a scene (each scene is a directory with main.txt naming it)."""
    if not sid.isalnum():
        raise ValueError("bad id")
    d = SCENES / sid
    if not (d / "main.txt").exists():
        raise FileNotFoundError(sid)
    return d / (d / "main.txt").read_text().strip()


def render(path, w=800, h=600, ray=0):
    out = path.parent / "preview.png"
    out.unlink(missing_ok=True)
    subprocess.run([str(PYMOL_PY), str(RENDER), str(path), str(out), str(w), str(h), str(ray)],
                   check=True, timeout=120, capture_output=True, cwd=path.parent)
    return out.read_bytes()


def launcher(path):
    """What to hand to PyMOL for a live session. Scripts run from their own folder so relative loads work."""
    if path.suffix.lower() != ".pml":
        return path
    f = path.parent / "_launch.py"
    f.write_text(f"import os\nos.chdir({str(path.parent)!r})\nfrom pymol import cmd\ncmd.do({'@' + path.name!r})\n")
    return f


class Worker:
    """One persistent PyMOL process for a scene; requests are serialized."""
    def __init__(self, sid, path):
        self.sid, self.lock, self.last = sid, threading.Lock(), time.time()
        env = dict(os.environ, AXPYMOL_SCENE=str(path), AXPYMOL_DIR=str(WORKER.parent))
        if FAKE:
            env["AXPYMOL_FAKE"] = "1"
        if GL:  # run AS PyMOL so its Qt/OpenGL window owns the main thread
            argv = [str(PYMOL_BIN), "-q", str(WORKER)]
        else:
            argv = [str(PYMOL_PY), str(WORKER), str(path)]
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=open(HOME / "worker.log", "ab"), cwd=path.parent, env=env)

    def alive(self):
        return self.p.poll() is None

    def request(self, req, timeout=180):
        with self.lock:
            self.last = time.time()
            t = threading.Timer(timeout, self.p.kill)  # first request includes scene load
            t.start()
            try:
                self.p.stdin.write(json.dumps(req).encode() + b"\n"); self.p.stdin.flush()
                hdr = self.p.stdout.readline()
                while hdr and not hdr.startswith(b'{"'):  # skip stray output
                    hdr = self.p.stdout.readline()
                if not hdr:
                    raise RuntimeError("PyMOL session ended (see worker.log)")
                meta = json.loads(hdr)
                data = self.p.stdout.read(meta["len"]) if meta["len"] else b""
            finally:
                t.cancel()
            self.last = time.time()
            return meta, data

    def stop(self):
        try: self.p.kill()
        except Exception: pass


WORKERS, WLOCK = {}, threading.Lock()


def get_worker(sid):
    path = scene_path(sid)
    with WLOCK:
        w = WORKERS.get(sid)
        if not w or not w.alive():
            w = WORKERS[sid] = Worker(sid, path)
        return w


def reaper():
    while True:
        time.sleep(30)
        with WLOCK:
            for sid, w in list(WORKERS.items()):
                if not w.alive() or time.time() - w.last > IDLE_SECS:
                    w.stop(); del WORKERS[sid]


class H(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        origin = self.headers.get("Origin", "")
        if origin in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "X-Axpymol-Token, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        for k, v in getattr(self, "extra", {}).items():
            self.send_header(k, v)
        self.extra = {}
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode())

    def _guard(self, path):
        """True if the request may proceed; otherwise a 403 has been sent."""
        if self.headers.get("Host", "") not in HOSTS:
            return self._deny("bad host")
        origin = self.headers.get("Origin")
        if origin is not None and origin not in ORIGINS:
            return self._deny("origin not allowed")
        if path.startswith("/open/"):
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                return self._deny("cross-site")
            return True
        if path == "/health" or path.startswith("/addin/"):
            return True
        if not hmac.compare_digest(self.headers.get("X-Axpymol-Token", ""), TOKEN):
            return self._deny("missing or bad token")
        return True

    def _deny(self, why):
        sys.stderr.write(f"[bridge] denied: {why} ({self.command} {self.path})\n")
        self._json(403, {"error": "forbidden"})
        return False

    def do_OPTIONS(self):
        if self.headers.get("Host", "") in HOSTS and self.headers.get("Origin") in ORIGINS:
            self._send(204)
        else:
            self._deny("bad preflight")

    def do_GET(self):
        u = urlparse(self.path)
        if not self._guard(u.path):
            return
        try:
            if u.path == "/health":
                return self._json(200, {"ok": True, "pymol": PYMOL_PY.exists()})
            if u.path.startswith("/addin/"):
                f = (ADDIN / u.path[len("/addin/"):]).resolve()
                if ADDIN not in f.parents or not f.is_file():
                    return self._json(404, {"error": "not found"})
                ct = {".html": "text/html", ".js": "text/javascript", ".xml": "text/xml", ".png": "image/png"}.get(f.suffix, "application/octet-stream")
                data = f.read_bytes()
                if f.suffix == ".html":
                    data = data.replace(b"__AXPYMOL_TOKEN__", TOKEN.encode())
                return self._send(200, data, ct)
            if u.path == "/scenes":
                out = []
                for d in sorted(SCENES.glob("*/main.txt"), key=lambda p: -p.stat().st_mtime):
                    out.append({"id": d.parent.name, "main": d.read_text().strip(), "mtime": d.stat().st_mtime})
                return self._json(200, out)
            if u.path.startswith("/scenes/") and u.path.count("/") == 2:
                p = scene_path(u.path.split("/")[2])
                return self._json(200, {"id": p.parent.name, "main": p.name})
            if u.path.startswith("/open/"):
                p = launcher(scene_path(u.path.split("/")[2]))
                subprocess.Popen(["open", "-n", "-a", str(PYMOL_APP), "--args", str(p)])
                return self._send(200, b"<html><body>Opening PyMOL\xe2\x80\xa6 you can close this tab.</body></html>", "text/html")
            self._json(404, {"error": "not found"})
        except FileNotFoundError:
            self._json(404, {"error": "unknown scene"})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        if not self._guard(u.path):
            return
        q = parse_qs(u.query)
        try:
            n = int(self.headers.get("Content-Length", 0))
            if not 0 <= n <= MAX_BODY:
                return self._json(413, {"error": "body too large"})
            body = self.rfile.read(n)
            if u.path == "/scenes":
                req = json.loads(body)  # {"main": name, "files": [{"name", "b64"}]}
                sid = uuid.uuid4().hex[:12]
                d = SCENES / sid
                d.mkdir(parents=True)
                for f in req["files"]:
                    n = os.path.basename(f["name"])
                    if n and not n.startswith("."):
                        (d / n).write_bytes(base64.b64decode(f["b64"]))
                main = os.path.basename(req["main"])
                if not (d / main).exists():
                    raise ValueError("main file missing")
                (d / "main.txt").write_text(main)
                png = render(d / main)
                return self._json(200, {"id": sid, "png_base64": base64.b64encode(png).decode()})
            parts = u.path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "scenes" and parts[2] == "frame":
                req = json.loads(body or b"{}")
                meta, png = get_worker(parts[1]).request(req)
                if not meta.get("ok"):
                    return self._json(500, {"error": meta.get("error", "frame failed")})
                self.extra = {"X-Axpymol": json.dumps({k: v for k, v in meta.items() if k not in ("ok", "len")}),
                              "Access-Control-Expose-Headers": "X-Axpymol"}
                return self._send(200, png, "image/png")
            if len(parts) == 3 and parts[0] == "scenes" and parts[2] == "render":
                p = scene_path(parts[1])
                png = render(p, int(q.get("w", [800])[0]), int(q.get("h", [600])[0]), int(q.get("ray", [0])[0]))
                return self._send(200, png, "image/png")
            self._json(404, {"error": "not found"})
        except FileNotFoundError:
            self._json(404, {"error": "unknown scene"})
        except subprocess.CalledProcessError as e:
            self._json(500, {"error": "PyMOL render failed", "detail": e.stderr.decode(errors="replace")[-500:]})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def log_message(self, fmt, *a):
        sys.stderr.write("[bridge] " + fmt % a + "\n")


def main():
    global TOKEN
    SCENES.mkdir(parents=True, exist_ok=True)
    TOKEN = load_token()
    threading.Thread(target=reaper, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)  # loopback only
    if INSECURE:
        print(f"AxPyMOL bridge (INSECURE test mode) on http://localhost:{PORT}")
    else:
        cert, key = CERTS / "localhost.crt", CERTS / "localhost.key"
        if not cert.exists():
            sys.exit("No certificate found. Run scripts/setup_certs.sh first.")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        print(f"AxPyMOL bridge on https://localhost:{PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
