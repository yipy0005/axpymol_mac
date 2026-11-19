"""Scene listing and deletion (no PyMOL needed). Run: pixi run python tests/test_scenes.py"""
import json, os, pathlib, subprocess, sys, tempfile, time, urllib.error, urllib.request

root = pathlib.Path(__file__).resolve().parent.parent
home = pathlib.Path(tempfile.mkdtemp())
for sid, main in (("aaa111", "a.pml"), ("bbb222", "b.pse")):
    d = home / "scenes" / sid; d.mkdir(parents=True)
    (d / "main.txt").write_text(main); (d / main).write_bytes(b"x" * 1000)
env = dict(os.environ, AXPYMOL_FAKE="1", AXPYMOL_INSECURE="1", AXPYMOL_HOME=str(home), AXPYMOL_PORT="3497")
p = subprocess.Popen([sys.executable, str(root / "bridge/axpymol_bridge.py")], env=env, stderr=subprocess.DEVNULL)
time.sleep(1.5)
tok = (home / "token").read_text().strip()


def call(path, method="GET", body=None, token=True):
    h = {"Host": "localhost:3497"}
    if token: h["X-Axpymol-Token"] = tok
    r = urllib.request.Request("http://127.0.0.1:3497" + path, data=body, method=method, headers=h)
    try:
        r = urllib.request.urlopen(r); return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


bad = 0
def check(name, ok):
    global bad; bad += not ok; print("PASS" if ok else "FAIL", name)

try:
    st, b = call("/scenes"); lst = json.loads(b)
    check("list has 2 scenes with size", st == 200 and len(lst) == 2 and all(s["size"] >= 1000 for s in lst))
    check("list has used/mtime", all("used" in s and "mtime" in s for s in lst))
    call("/scenes/aaa111/frame", "POST", b'{"w":64,"h":64}')  # a frame marks it used
    check(".used written on frame", (home / "scenes/aaa111/.used").exists())
    check("delete without token refused", call("/scenes/aaa111", "DELETE", token=False)[0] == 403 and (home / "scenes/aaa111").exists())
    st, b = call("/scenes/aaa111", "DELETE")
    check("delete ok", st == 200 and not (home / "scenes/aaa111").exists())
    check("other scene untouched", (home / "scenes/bbb222/b.pse").exists())
    check("delete unknown -> 404", call("/scenes/aaa111", "DELETE")[0] == 404)
    check("bad id rejected", call("/scenes/..%2f..", "DELETE")[0] in (400, 404, 500) and home.exists())
    check("list now 1", len(json.loads(call("/scenes")[1])) == 1)
finally:
    p.kill()
sys.exit(bad)
