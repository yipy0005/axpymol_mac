# AxPyMOL for Mac

Mac PowerPoint has no ActiveX, so this is an Office add-in plus a local bridge to PyMOL.

- `bridge/` – local HTTPS service (stdlib Python). Renders scenes with PyMOL.app's bundled Python, stores them, opens them live, and serves the add-in.
- `addin/` – Office.js task-pane add-in (insert scene, open live, re-render).
- `tools/axpptx.py` – inspect/extract content from Windows AxPyMOL decks.

## Setup
1. `scripts/setup_certs.sh` (once; trusts a localhost cert, asks for sudo)
2. `python3 bridge/axpymol_bridge.py` (leave running)
3. Sideload: `mkdir -p ~/Library/Containers/com.microsoft.Powerpoint/Data/Documents/wef && cp addin/manifest.xml ~/Library/Containers/com.microsoft.Powerpoint/Data/Documents/wef/`
4. Restart PowerPoint → Insert → My Add-ins → AxPyMOL.

## Use
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
