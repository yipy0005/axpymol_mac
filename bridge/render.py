"""Runs inside PyMOL's bundled Python: render a scene file to PNG.
usage: render.py <scene> <out.png> <width> <height> [ray]
"""
import sys
import pymol
from pymol import cmd
from scene_loader import load_scene

scene, out, w, h = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
ray = int(sys.argv[5]) if len(sys.argv) > 5 else 0
pymol.finish_launching(["pymol", "-cq"])
load_scene(scene)
cmd.png(out, w, h, dpi=150, ray=ray)
cmd.sync()
cmd.quit()
