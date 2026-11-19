"""Serve docs/ over HTTPS on localhost:3445 with the helper's trusted certificate, to test the hosted
front door (docs/launch.html) before publishing it. Then:
  pixi run python scripts/make_manifest.py https://localhost:3445 > <wef folder>/manifest.content.xml
"""
import http.server, ssl, functools
from pathlib import Path

certs = Path.home() / "Library/Application Support/AxPyMOL/certs"
h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(Path(__file__).resolve().parent.parent / "docs"))
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 3445), h)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(certs / "localhost.crt", certs / "localhost.key")
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
print("serving docs/ on https://localhost:3445"); srv.serve_forever()
