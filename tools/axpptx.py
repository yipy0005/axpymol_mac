#!/usr/bin/env python3
"""Inspect/extract AxPyMOL content from a Windows-authored .pptx.

  axpptx.py inspect deck.pptx            list embedded/ActiveX parts with sniffed type
  axpptx.py extract deck.pptx outdir     dump candidate PyMOL payloads (.pse/.pdb/...)

AxPyMOL's on-disk format is undocumented; this sniffs parts rather than assuming a layout.
Please share a sample deck so a precise slide->scene mapping can be added.
"""
import re, sys, zipfile, zlib
from pathlib import Path

INTEREST = re.compile(r"^ppt/(activeX|embeddings|ink|media)/", re.I)


def sniff(data: bytes) -> str:
    if data[:4] == b"PK\x03\x04": return "zip"
    if data[:8] == bytes.fromhex("D0CF11E0A1B11AE1"): return "ole-compound"
    if data[:1] == b"\x80" and data[1:2] in b"\x02\x03\x04\x05": return "pickle(.pse?)"
    if re.match(rb"\s*(HEADER|ATOM|HETATM|data_)", data): return "pdb/cif"
    if b"PyMOL" in data[:4096] or b"pymol" in data[:4096]: return "mentions-pymol"
    for off in (0, 4, 8):
        try: zlib.decompress(data[off:]); return f"zlib@{off}"
        except Exception: pass
    return "unknown"


def parts(z):
    return [n for n in z.namelist() if INTEREST.match(n) and not n.endswith("/")]


def inspect(path):
    with zipfile.ZipFile(path) as z:
        for n in parts(z):
            d = z.read(n); print(f"{n:55} {len(d):>10}  {sniff(d)}")
        for n in z.namelist():
            if n.startswith("ppt/slides/") and n.endswith(".xml"):
                x = z.read(n).decode("utf8", "replace")
                if re.search(r"axpymol|activeX|oleObj", x, re.I): print("slide uses ActiveX/OLE:", n)


def extract(path, out):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as z:
        for n in parts(z):
            d = z.read(n); k = sniff(d)
            if k in ("unknown", "zip") and not n.startswith("ppt/embeddings"): continue
            for off in (0, 4, 8):
                if k.startswith("zlib@"):
                    d = zlib.decompress(d[int(k[5:]):]); k = sniff(d); break
            ext = ".pse" if k.startswith("pickle") else ".pdb" if k == "pdb/cif" else ".bin"
            f = out / (Path(n).stem + ext); f.write_bytes(d); print("wrote", f, k)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "inspect": inspect(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == "extract": extract(sys.argv[2], sys.argv[3])
    else: sys.exit(__doc__)
