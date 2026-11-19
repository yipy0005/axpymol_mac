"""Shared by render.py and worker.py (runs inside PyMOL's bundled Python)."""
import os
from pymol import cmd


def load_scene(scene):
    scene = os.path.abspath(scene)
    d = os.path.dirname(scene)
    os.chdir(d)  # .pml scripts load files relative to themselves
    if not scene.lower().endswith(".pml"):
        cmd.load(scene)  # .pse restores view/representations; structures load directly
        return
    # Headless has no GUI: drop `run x.py` lines for scripts that use the Qt GUI (e.g. legend windows).
    # The live PyMOL window (axpymol open) still runs the original script unchanged.
    keep = []
    for line in open(scene, errors="replace").read().splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0] == "run" and parts[1].rstrip(",;").endswith(".py"):
            try:
                if "Qt" in open(parts[1].rstrip(",;"), errors="replace").read():
                    continue
            except OSError:
                pass
        keep.append(line)
    pre = os.path.join(d, "_preview.pml")
    open(pre, "w").write("\n".join(keep) + "\n")
    cmd.do("@" + os.path.basename(pre))
