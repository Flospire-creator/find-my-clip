"""Copying into folders, project collections and the first cut.

Every function here writes somewhere new and only reads the originals.
"""

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageOps

from . import media

# Windows calls the Movies folder Videos.
OUTPUT = Path(os.environ.get("FMC_OUTPUT", Path.home() / ("Videos" if os.name == "nt" else "Movies") / "Find My Clip"))
SHAPES = {"vertical": (1080, 1920), "landscape": (1920, 1080), "square": (1080, 1080)}
PHOTO_SECONDS = 3.0


def safe_name(name):
    name = re.sub(r"[^\w &().,'-]+", " ", name).strip()
    return name[:80] or "Untitled"


def unique(dest):
    stem, suffix, n = dest.stem, dest.suffix, 1
    while dest.exists():
        dest = dest.with_name(f"{stem} ({n}){suffix}")
        n += 1
    return dest


# Sorted folders go in Pictures, which iCloud Drive doesn't sync (Desktop often is).
SORTED = Path(os.environ.get("FMC_SORTED", Path.home() / "Pictures/Find My Clip"))


def chosen(lib, ids=None):
    """Files to copy: the ones asked for, or one best version of every picture.

    Without a list: everything sorted, except files you skipped, files that
    couldn't be opened, and the extra copies and older edits of a picture we
    keep elsewhere (unless you chose to keep them). Similar shots and blurry
    ones are real photos, so they're copied unless you skip them."""
    out = []
    for it in lib.items:
        if ids is not None and it["id"] not in ids:
            continue
        if it["status"] not in ("ready", "review") or it.get("decision") == "skip":
            continue
        if ids is None and it.get("duplicate_kind") in ("exact", "edit") and it.get("decision") != "keep":
            continue
        out.append(it)
    return out


def plan_folders(lib):
    """What Create the folders will do, for the screen: files, folders and copies left out."""
    files = chosen(lib)
    folders = {(i["kind"], i["category"]) for i in files}
    left_out = sum(1 for i in lib.items if i.get("duplicate_kind") in ("exact", "edit")
                   and i.get("decision") != "keep" and i["status"] in ("ready", "review"))
    return {"files": len(files), "folders": len(folders), "left_out": left_out,
            "target": str(SORTED / f"{safe_name(lib.root.name)} sorted")}


def copy_to_folders(lib, ids=None, progress=lambda done, total: None, cancelled=lambda: False):
    SORTED.mkdir(parents=True, exist_ok=True)
    target = unique(SORTED / f"{safe_name(lib.root.name)} sorted")
    files = chosen(lib, ids)
    copied, folders = 0, set()
    for n, it in enumerate(files):
        if cancelled():
            break
        group = "Photos" if it["kind"] == "photo" else "Videos"
        folder = target / group / it["category"]
        if not folder.exists():
            folder.mkdir(parents=True)
        folders.add(folder)
        media.copy_preserving(lib.path_of(it), unique(folder / it["name"]))
        copied += 1
        if n % 10 == 0 or n == len(files) - 1:
            progress(n + 1, len(files))
    return {"folder": str(target), "copied": copied, "folders": len(folders), "total": len(files)}


def copy_to_project(lib, name, ids):
    target = OUTPUT / "Projects" / safe_name(name)
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for it in chosen(lib, ids):
        media.copy_preserving(lib.path_of(it), unique(target / it["name"]))
        copied += 1
    return {"folder": str(target), "copied": copied}


def render_cut(lib, clips, shape="vertical", progress=lambda done, total: None, cancelled=lambda: False):
    """Assemble clips into one MP4, plus a JSON draft that can be reopened."""
    if not clips:
        raise ValueError("Add at least one clip first.")
    w, h = SHAPES.get(shape, SHAPES["vertical"])
    out_dir = OUTPUT / "First cuts"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H.%M")
    dest = unique(out_dir / f"{safe_name(lib.root.name)} first cut {stamp}.mp4")

    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x1f1b17,fps=30,format=yuv420p,setsar=1")
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "160k"]
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        parts = []
        for n, c in enumerate(clips):
            if cancelled():
                raise InterruptedError("Export cancelled.")
            it = lib.item(c["id"])
            src = lib.path_of(it)
            part = tmp / f"{n:03d}.mp4"
            length = max(0.5, c["end"] - c["start"]) if it["kind"] == "video" else PHOTO_SECONDS
            if it["kind"] == "photo":
                still = tmp / f"{n:03d}.png"
                with Image.open(src) as im:
                    im = ImageOps.exif_transpose(im).convert("RGB")
                    im.thumbnail((w, h))
                    im.save(still)
                cmd = ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-t", f"{length}", "-i", str(still),
                       "-f", "lavfi", "-t", f"{length}", "-i", "anullsrc=r=48000:cl=stereo",
                       "-map", "0:v", "-map", "1:a", "-vf", vf, *enc, "-t", f"{length}", str(part)]
            elif it.get("has_audio"):
                cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{c['start']}", "-t", f"{length}", "-i", str(src),
                       "-map", "0:v:0", "-map", "0:a:0", "-vf", vf, *enc, str(part)]
            else:
                cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{c['start']}", "-t", f"{length}", "-i", str(src),
                       "-f", "lavfi", "-t", f"{length}", "-i", "anullsrc=r=48000:cl=stereo",
                       "-map", "0:v:0", "-map", "1:a", "-vf", vf, *enc, "-t", f"{length}", str(part)]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                raise RuntimeError(f"Clip {n + 1} ({it['name']}) couldn't be added.")
            parts.append(part)
            progress(n + 1, len(clips) + 1)
        listing = tmp / "list.txt"
        listing.write_text("".join(f"file '{p}'\n" for p in parts))
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                            "-c", "copy", "-movflags", "+faststart", str(dest)], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("The clips couldn't be joined together.")
    draft = dest.with_suffix(".json")
    draft.write_text(json.dumps({
        "source_folder": str(lib.root), "shape": shape, "made": stamp,
        "clips": [{"file": lib.item(c["id"])["rel"], "start": c["start"], "end": c["end"]} for c in clips],
    }, indent=2))
    progress(len(clips) + 1, len(clips) + 1)
    return {"file": str(dest), "draft": str(draft)}
