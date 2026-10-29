"""Reading media: hashing, thumbnails, keyframes, sharpness, audio."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
import pillow_heif

pillow_heif.register_heif_opener()

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".m2ts", ".3gp", ".webm", ".mpg", ".mpeg", ".wmv"}
THUMB = 360           # px, long edge. Big enough for large previews in the grid.
MAX_FRAMES = 90       # keyframes per video, whatever its length
FRAME_EVERY = 2.0     # seconds between keyframes for short videos


def kind_of(path):
    ext = Path(path).suffix.lower()
    if ext in PHOTO_EXT:
        return "photo"
    if ext in VIDEO_EXT:
        return "video"
    return None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def photo_thumb(src, dest):
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        size = im.size
        im.thumbnail((THUMB, THUMB))
        im.save(dest, "JPEG", quality=84)
    return size


def probe(path):
    """Duration in seconds and whether there is an audio stream."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
         "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise MediaError("This video could not be opened.")
    info = json.loads(out.stdout or "{}")
    try:
        duration = float(info.get("format", {}).get("duration", 0))
    except ValueError:
        duration = 0.0
    types = {s.get("codec_type") for s in info.get("streams", [])}
    if "video" not in types:
        raise MediaError("This file has no picture in it.")
    return duration, "audio" in types


def frame_times(duration):
    if duration <= 0:
        return [0.0]
    step = max(FRAME_EVERY, duration / MAX_FRAMES)
    times, t = [], min(0.5, duration / 2)
    while t < duration and len(times) < MAX_FRAMES:
        times.append(round(t, 2))
        t += step
    return times or [0.0]


def video_frames(src, duration, dest_prefix):
    """Write one thumbnail per keyframe time. Returns [(time, path)].

    Uses the nearest keyframe, the Mac's hardware decoder and several frames at
    once: on a 4-minute 4K film, 90 frames took 62 s one by one and 4.9 s this way.
    Falls back to an ordinary decode for anything the hardware can't read."""
    def grab(i, t):
        dest = Path(f"{dest_prefix}_{i:03d}.jpg")
        scale = ["-vf", f"scale={THUMB}:{THUMB}:force_original_aspect_ratio=decrease"]
        for fast in (["-hwaccel", "videotoolbox", "-skip_frame", "nokey"], ["-skip_frame", "nokey"], []):
            subprocess.run(["ffmpeg", "-v", "error", "-y", *fast, "-ss", str(t), "-i", str(src),
                            "-frames:v", "1", *scale, str(dest)], capture_output=True, timeout=120)
            if dest.exists() and dest.stat().st_size > 0:
                return t, dest
        return None

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as pool:
        frames = [f for f in pool.map(lambda a: grab(*a), enumerate(frame_times(duration))) if f]
    if not frames:
        raise MediaError("We couldn't read any frames from this video.")
    return frames


def sharpness(img_path):
    """Variance of the Laplacian on a grey 256px copy. Low means blurry."""
    with Image.open(img_path) as im:
        g = np.asarray(ImageOps.grayscale(im).resize((256, 256)), dtype=np.float32)
    lap = (-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


def audio_16k(path):
    """Mono 16 kHz float audio for speech recognition."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
        capture_output=True, timeout=600)
    if raw.returncode != 0:
        raise MediaError("We couldn't read the sound in this video.")
    return np.frombuffer(raw.stdout, np.int16).astype(np.float32) / 32768.0


def copy_preserving(src, dest):
    """Copy with timestamps. Some system files refuse their flags, so fall back."""
    try:
        shutil.copy2(src, dest)
    except PermissionError:
        shutil.copyfile(src, dest)


class MediaError(Exception):
    """A problem with one file, worded for the person using the app."""
