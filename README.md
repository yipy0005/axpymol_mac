# AxPyMOL for Mac

## Install (users)
Requires macOS 13+, PowerPoint for Mac, and [PyMOL](https://pymol.org/) installed in Applications.

1. Download `AxPyMOL-Helper-<version>.dmg` from [Releases](https://github.com/yipy0005/axpymol_mac/releases), open it, and drag **AxPyMOL Helper** to Applications.
2. The app is not notarized, so macOS will refuse the first launch ("cannot be opened"). Either:
   - open **System Settings → Privacy & Security**, scroll down and click **Open Anyway** next to AxPyMOL Helper (on older macOS: right-click the app → Open), or
   - run `xattr -dr com.apple.quarantine "/Applications/AxPyMOL Helper.app"` in Terminal.
3. Follow the welcome: it asks for your password once (to trust a local certificate) and a Terminal window briefly installs the add-in into PowerPoint.
4. Restart PowerPoint → Insert → Add-ins → My Add-ins → Developer Add-ins → **AxPyMOL Live Scene**.

The rest of this README is for developers.

Mac PowerPoint has no ActiveX, so this is an Office add-in plus a local bridge to PyMOL.

- `bridge/` – local HTTPS service (stdlib Python). Renders scenes with PyMOL.app's bundled Python, stores them, opens them live, and serves the add-in.
- `addin/` – Office.js add-ins: `viewer.*` + `manifest.content.xml` (live scene, current) and `taskpane.*` + `manifest.xml` (legacy static-image tool).
- `tools/axpptx.py` – inspect/extract content from Windows AxPyMOL decks.

## Developer setup (legacy task-pane add-in, no longer installed by the helper)
1. `scripts/setup_certs.sh` (once; trusts a localhost cert, asks for sudo)
2. `python3 bridge/axpymol_bridge.py` (leave running)
3. Sideload: `mkdir -p ~/Library/Containers/com.microsoft.Powerpoint/Data/Documents/wef && cp addin/manifest.xml ~/Library/Containers/com.microsoft.Powerpoint/Data/Documents/wef/`
4. Restart PowerPoint → Insert → My Add-ins → AxPyMOL.

## Legacy task-pane use
Insert a `.pse`/`.pdb`/`.cif` → a rendered image is placed on the slide, tagged with a scene id and hyperlinked. In slideshow, clicking it opens the live scene in PyMOL.

## Status / limitations
- Live 3D opens in a separate PyMOL window (the browser flashes briefly via the hyperlink); it is not embedded in the slide.
- Windows deck import: format is undocumented; `axpptx.py` sniffs parts. Needs a sample deck to finish.
- Add-in untested inside PowerPoint so far; the bridge is tested.

## Live in-slide scene (content add-in)
`addin/manifest.content.xml` + `addin/viewer.{html,js}` put an interactive PyMOL canvas **inside the slide**.
- Sideload it the same way as the task-pane manifest (copy `manifest.content.xml` into the `wef` folder), restart PowerPoint, then Insert → My Add-ins → *AxPyMOL Live Scene*.
- First load shows a scene picker (pick an existing bridge scene or upload a `.pml`/`.pse`/folder). The scene id and, if small (<1.5 MB), the files are saved in the add-in settings, so the deck re-creates the scene on a Mac whose bridge has never seen it.
- Slideshow: drag = rotate, shift/right-drag = pan, wheel = zoom, double-click = reset, bar = scenes / PyMOL command / ray-on-settle.
- How: `bridge/worker.py` keeps one headless PyMOL per scene alive; the viewer sends coalesced drag deltas to `POST /scenes/<id>/frame` and draws the returned PNG (half-res while moving, full-res once idle).
- Tests without PyMOL: `AXPYMOL_FAKE=1 AXPYMOL_INSECURE=1 AXPYMOL_HOME=<tmp> pixi run python bridge/axpymol_bridge.py`.

## Security model
The bridge runs PyMOL commands, so it is locked down: loopback only; `Host` must be localhost (DNS rebinding); a request with an `Origin` must be the add-in's own; every API call needs a per-install secret (`X-Axpymol-Token`, from `~/Library/Application Support/AxPyMOL/token`, mode 0600) that is injected into the add-in pages the bridge serves (a foreign site can't read them); `/open/*` rejects cross-site navigations. Test: `pixi run python tests/test_security.py` (no PyMOL needed).

## Installer / menu-bar app (recommended for users)
`scripts/build_app.sh` builds `build/AxPyMOL Helper.app` (universal) and `build/AxPyMOL-Helper-<ver>.dmg`.
- Users: drag the app to Applications and open it. First run creates a localhost certificate and trusts it in the **login keychain** (one password prompt, no sudo), installs both manifests into PowerPoint's `wef` folder, and offers "Start at login". Then restart PowerPoint → Insert → My Add-ins → Developer Add-ins → *AxPyMOL Live Scene*.
- The helper runs the bridge with PyMOL.app's own Python (no Python install needed; PyMOL.app is auto-detected or chosen from the menu), restarts it if it dies, and shows its status in the menu bar (atom icon, `!` when something needs attention). Menu: Repair add-in, Restart bridge, Show log (`~/Library/Logs/AxPyMOL`), Start at login, Quit.
- `AxPyMOLHelper --check` prints environment status; `--setup` runs setup headlessly.
- Ad-hoc signed by default (recipients must right-click → Open the first time). For wide distribution sign with a Developer ID (`SIGN_ID=… scripts/build_app.sh`) and notarize.

## Friendly "helper not running" state
Inside the viewer (already loaded): if the helper stops or PyMOL is missing, it shows a message and reconnects by itself.
But a page *served by the helper* can't load at all when the helper is down, so for a first-load message use the hosted front door:
- `docs/launch.html` polls `https://localhost:3443/health`; if up it forwards to the live viewer (keeping Office's query string), otherwise it shows install/start steps and an `axpymol://start` button (the helper registers that URL scheme).
- Publish `docs/` (e.g. GitHub Pages), then `pixi run python scripts/make_manifest.py https://<user>.github.io/axpymol_mac > manifest.content.xml` and ship that manifest instead of the default.
- Test locally first: `pixi run python scripts/serve_docs.py` (https://localhost:3445, same trusted cert) and generate the manifest with `https://localhost:3445`.
- Untested inside PowerPoint: whether the task-pane web view follows the cross-origin redirect and honours `axpymol://`.
