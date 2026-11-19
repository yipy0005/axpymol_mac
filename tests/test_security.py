import json, os, subprocess, sys, time, urllib.request, urllib.error, tempfile, pathlib, stat
root = pathlib.Path(__file__).resolve().parent.parent
home = tempfile.mkdtemp(); d = pathlib.Path(home, "scenes", "abc123"); d.mkdir(parents=True)
(d/"main.txt").write_text("x.pml"); (d/"x.pml").write_text("# n\n")
env = dict(os.environ, AXPYMOL_FAKE="1", AXPYMOL_INSECURE="1", AXPYMOL_HOME=home, AXPYMOL_PORT="3498")
p = subprocess.Popen([sys.executable, str(root/"bridge/axpymol_bridge.py")], env=env, stderr=subprocess.DEVNULL)
time.sleep(1.5)
tok = pathlib.Path(home, "token").read_text().strip()
print("token file mode", oct(stat.S_IMODE(os.stat(pathlib.Path(home,"token")).st_mode)), len(tok))
def call(path, method="GET", body=None, **hdr):
    h = {"Host": "localhost:3498"}; h.update({k.replace("_","-"): v for k, v in hdr.items() if v is not None})
    r = urllib.request.Request("http://127.0.0.1:3498"+path, data=body, method=method, headers=h)
    try: return urllib.request.urlopen(r).status
    except urllib.error.HTTPError as e: return e.code
B = b'{"w":64,"h":64}'
T = "X_Axpymol_Token"
cases = [
 ("valid token",              call("/scenes/abc123/frame","POST",B,**{T:tok}), 200),
 ("no token",                 call("/scenes/abc123/frame","POST",B), 403),
 ("wrong token",              call("/scenes/abc123/frame","POST",B,**{T:"x"*len(tok)}), 403),
 ("list scenes no token",     call("/scenes"), 403),
 ("evil origin + token",      call("/scenes/abc123/frame","POST",B,Origin="https://evil.example",**{T:tok}), 403),
 ("null origin + token",      call("/scenes","GET",None,Origin="null",**{T:tok}), 403),
 ("evil origin no token (simple POST)", call("/scenes/abc123/frame","POST",B,Origin="https://evil.example",Content_Type="text/plain"), 403),
 ("rebinding host + token",   call("/scenes","GET",None,Host="evil.example:3498",**{T:tok}), 403),
 ("preflight evil origin",    call("/scenes/abc123/frame","OPTIONS",None,Origin="https://evil.example"), 403),
 ("preflight own origin",     call("/scenes/abc123/frame","OPTIONS",None,Origin="http://localhost:3498"), 204),
 ("/open cross-site",         call("/open/abc123","GET",None,Sec_Fetch_Site="cross-site"), 403),
 ("health",                   call("/health"), 200),
 ("huge body",                call("/scenes","POST",None,Content_Length="999999999999",**{T:tok}), 413),
]
bad = 0
for n, got, want in cases:
    ok = got == want; bad += not ok; print("PASS" if ok else "FAIL", n, got, "" if ok else f"(want {want})")
html = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:3498/addin/viewer.html", headers={"Host":"localhost:3498"})).read().decode()
inj = f'content="{tok}"' in html and "__AXPYMOL_TOKEN__" not in html
print("PASS" if inj else "FAIL", "token injected into viewer.html"); bad += not inj
p.kill(); sys.exit(bad)
