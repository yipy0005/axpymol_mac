"""Write a content-add-in manifest whose front door is a hosted page (see docs/launch.html).
usage: pixi run python scripts/make_manifest.py https://<user>.github.io/axpymol_mac > manifest.hosted.xml
The hosted page forwards to the local helper (https://localhost:3443) when it is running, and otherwise
shows install/start instructions, which a page served by the helper itself could never do.
"""
import re, sys
from pathlib import Path

base = sys.argv[1].rstrip("/")
if not base.startswith("https://"):
    sys.exit("base URL must be https")
from urllib.parse import urlparse
origin = "https://" + urlparse(base).netloc
x = (Path(__file__).resolve().parent.parent / "addin/manifest.content.xml").read_text()
x = re.sub(r'(<SourceLocation DefaultValue=")[^"]*(")', rf"\g<1>{base}/launch.html\g<2>", x)
x = x.replace("<AppDomains><AppDomain>https://localhost:3443</AppDomain></AppDomains>",
              f"<AppDomains><AppDomain>https://localhost:3443</AppDomain><AppDomain>{origin}</AppDomain></AppDomains>")
sys.stdout.write(x)
