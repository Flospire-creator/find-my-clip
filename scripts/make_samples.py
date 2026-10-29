"""Build the 20-file sample library from pictures that ship with macOS.

Everything is generated on this Mac into ./samples. Nothing is downloaded.
The set is designed to exercise every path:

- 15 photos, mostly HEIC, including an exact copy, a resized copy and a blurred copy
- 4 videos with known content and known speech at known times (for search tests)
- 1 broken file that looks like a JPEG (failed analysis)
- 1 unsupported file (not counted in the 20)

Run:  .venv/bin/python scripts/make_samples.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
import pillow_heif

pillow_heif.register_heif_opener()

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples"
UP = Path("/Library/User Pictures")
DP = Path("/System/Library/Desktop Pictures/.thumbnails")
VOICE = "Daniel"

# Speech in the videos, with the second it starts. Tests rely on these.
VIDEOS = {
    "Business update.mp4": {
        "shots": [(UP / "Instruments/Piano.heic", 5), (UP / "Fun/Medal.heic", 7)],
        "speech": (5.0, "Quick update on my business. This week the new booking page went live, "
                        "and twelve people signed up."),
    },
    "Holiday evening.mov": {
        "shots": [(DP / "The Beach.heic", 4), (DP / "Catalina Sunset.heic", 6)],
        "speech": (4.0, "Look at that sunset. Best evening of the whole holiday."),
    },
    "Match day.mp4": {
        "shots": [(UP / "Sports/Football.heic", 4), (UP / "Sports/Basketball.heic", 4)],
        "speech": (1.0, "Match day. We won three nil and I scored the second goal."),
    },
    "Practice session.mp4": {
        "shots": [(UP / "Instruments/Violin.heic", 4), (UP / "Instruments/Drum.heic", 4)],
        "speech": None,
    },
}

PHOTOS = [
    UP / "Instruments/Guitar.heic",
    UP / "Instruments/Piano.heic",
    UP / "Flowers/Sunflower.heic",
    UP / "Flowers/Poppy.heic",
    UP / "Animals/Zebra.heic",
    UP / "Animals/Owl.heic",
    UP / "Fun/Fortune Cookie.heic",
    UP / "Sports/Tennis.heic",
    DP / "The Cliffs.heic",
    DP / "Catalina Coast.heic",
]


def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def frame_png(src, dest, w=720, h=1280):
    """Fit a picture into a vertical frame on a soft background."""
    im = Image.open(src).convert("RGB")
    bg = im.resize((w, h)).filter(ImageFilter.GaussianBlur(40))
    im.thumbnail((w, h))
    bg.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
    bg.save(dest)


def make_video(name, spec, tmp):
    parts = []
    for i, (src, secs) in enumerate(spec["shots"]):
        png = tmp / f"{name}-{i}.png"
        frame_png(src, png)
        part = tmp / f"{name}-{i}.mp4"
        # A slow push-in so frames within a shot are not identical.
        run(["ffmpeg", "-y", "-loop", "1", "-i", str(png), "-t", str(secs), "-r", "25",
             "-vf", f"zoompan=z='min(zoom+0.0008,1.08)':d={secs * 25}:s=720x1280:fps=25,format=yuv420p",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(part)])
        parts.append(part)
    listing = tmp / f"{name}.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    silent = tmp / f"{name}-video.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(silent)])
    total = sum(s for _, s in spec["shots"])
    dest = OUT / name
    if spec["speech"]:
        start, text = spec["speech"]
        aiff = tmp / f"{name}.aiff"
        run(["say", "-v", VOICE, "-o", str(aiff), text])
        ms = int(start * 1000)
        run(["ffmpeg", "-y", "-i", str(silent), "-i", str(aiff),
             "-filter_complex", f"[1:a]adelay={ms}|{ms},apad[a]",
             "-map", "0:v", "-map", "[a]", "-t", str(total),
             "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-ac", "2", str(dest)])
    else:
        run(["ffmpeg", "-y", "-i", str(silent), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
             "-t", str(total), "-c:v", "copy", "-c:a", "aac", "-shortest", str(dest)])


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "From my phone").mkdir(parents=True)
    tmp = Path(tempfile.mkdtemp())

    for src in PHOTOS:
        shutil.copyfile(src, OUT / src.name)

    # Planted duplicates and a planted blurry photo.
    shutil.copyfile(UP / "Instruments/Guitar.heic", OUT / "From my phone/Guitar copy.heic")
    Image.open(UP / "Flowers/Sunflower.heic").convert("RGB").resize((300, 300)).save(
        OUT / "From my phone/IMG_2041.jpg", quality=85)
    Image.open(UP / "Sports/Tennis.heic").convert("RGB").filter(ImageFilter.GaussianBlur(9)).save(
        OUT / "From my phone/IMG_3310.jpg", quality=85)

    # A phone screenshot and a photo of text.
    f = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 34)
    s = Image.new("RGB", (750, 1334), "white")
    d = ImageDraw.Draw(s)
    d.rectangle([0, 0, 750, 120], fill="#f2f2f7")
    d.text((30, 50), "Messages", fill="black", font=f)
    for i, y in enumerate(range(180, 1200, 150)):
        x = 40 if i % 2 else 300
        d.rounded_rectangle([x, y, x + 400, y + 100], 30, fill="#e5e5ea" if i % 2 else "#0a84ff")
        d.text((x + 25, y + 30), "See you at 7?", fill="black" if i % 2 else "white", font=f)
    s.save(OUT / "From my phone/Screenshot 2026-10-12 at 09.14.22.png")

    t = Image.new("RGB", (900, 1200), "#fbf6ee")
    d = ImageDraw.Draw(t)
    g = ImageFont.truetype("/System/Library/Fonts/Supplemental/Georgia.ttf", 40)
    words = ("The best time to plant a tree was twenty years ago. The second best time is now. "
             "Write it down, start small and keep going.").split()
    y, row = 120, ""
    for w in words:
        if len(row + w) > 26:
            d.text((80, y), row, fill="#222", font=g)
            y, row = y + 70, ""
        row += w + " "
    d.text((80, y), row, fill="#222", font=g)
    t.save(OUT / "quote.png")

    for name, spec in VIDEOS.items():
        make_video(name, spec, tmp)

    # A broken file and an unsupported one.
    (OUT / "From my phone/IMG_9999.jpg").write_bytes(b"\xff\xd8\xff\xe0 this is not really a photo")
    (OUT / "Receipt.pdf").write_bytes(b"%PDF-1.4 sample")

    shutil.rmtree(tmp)
    files = sorted(p for p in OUT.rglob("*") if p.is_file())
    print(f"{len(files)} files in {OUT}")
    for p in files:
        print(" ", p.relative_to(OUT))


if __name__ == "__main__":
    sys.exit(main())
