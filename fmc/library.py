"""A library is one folder of photos and videos and everything we learn about it.

Lifecycle of a file:
    waiting -> analysing -> ready
                         -> review   (likely duplicate or blurry: the person decides)
                         -> failed   (can be tried again)
    unsupported files are counted and never touched.

Originals are only ever read. Thumbnails, frames and results live in a cache
folder outside the library, keyed by the folder path.
"""

import hashlib
import json
import os
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import imagehash
import numpy as np
from PIL import Image

from . import copies, groups, media, models
from .media import MediaError

def _app_dir():
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / "Find My Clip"
    return Path.home() / "Library/Application Support/Find My Clip"


APP_DIR = Path(os.environ.get("FMC_HOME", _app_dir()))

NOT_SURE = groups.NOT_SURE


def unsure_for(it):
    return groups.NOT_SURE_VIDEO if it["kind"] == "video" else NOT_SURE
CATEGORY_NAMES = groups.names("everyday")
CONFIDENT = 0.30          # below this a file goes to "Other photos" or "Other videos"
BLURRY_BELOW = 15.0       # Laplacian variance on 256px grey. Sample set: planted blur 3.1, softest real file 39


# Folders that never hold your own photos: code, app bundles and photo libraries
# managed by other apps. Skipping them keeps a big folder quick to read.
SKIP_DIRS = {"node_modules", "__pycache__", "site-packages", "venv", "env", "Pods", "DerivedData"}
SKIP_SUFFIXES = (".app", ".photoslibrary", ".imovielibrary", ".fcpbundle", ".framework", ".bundle",
                 ".xcodeproj", ".lrdata", ".photolibrary")
WORKERS = max(2, min(8, (os.cpu_count() or 4) - 1))


class Events:
    def __init__(self):
        self.clients = []
        self.lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def send(self, event):
        with self.lock:
            for q in self.clients:
                q.put(event)


class Library:
    def __init__(self, folder, events=None, is_sample=False):
        self.root = Path(folder).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError("That folder doesn't exist.")
        self.is_sample = is_sample
        self.events = events or Events()
        key = hashlib.sha1(str(self.root).encode()).hexdigest()[:12]
        self.key = key
        self.closed = threading.Event()
        self.cache = APP_DIR / "libraries" / key
        (self.cache / "thumbs").mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.items = []
        self.unsupported = []
        self.emb = {}             # item id -> (n, d) array of picture embeddings
        self.cut = []             # first cut: [{id, start, end}]
        self.state = "idle"       # idle, preparing, analysing, paused, done
        self.cancel_flag = threading.Event()
        self.worker = None
        self.sigs = {}            # photo id -> (structure signature, saturation)
        self.mode_setting = "auto"   # auto, everyday, event
        self.mode = "everyday"
        self._grouper = None
        self._leaning = None
        self.custom = []          # group names the person made up by renaming
        self._load_cache()

    # ------------------------------------------------------------------ #
    # Public state
    # ------------------------------------------------------------------ #
    def counts(self):
        c = {"waiting": 0, "analysing": 0, "ready": 0, "review": 0, "failed": 0}
        photos = videos = 0
        for it in self.items:
            c[it["status"]] = c.get(it["status"], 0) + 1
            photos += it["kind"] == "photo"
            videos += it["kind"] == "video"
        c.update(total=len(self.items), photos=photos, videos=videos,
                 unsupported=len(self.unsupported),
                 duplicates=sum(1 for i in self.items if i.get("duplicate_of") is not None),
                 blurry=sum(1 for i in self.items if i.get("blurry")))
        return c

    def snapshot(self):
        with self.lock:
            return {
                "folder": self.root.name, "path": str(self.root), "sample": self.is_sample,
                "state": self.state, "counts": self.counts(), "categories": self.group_names(),
                "mode": self.mode, "mode_setting": self.mode_setting,
                "lib": self.key,
                "items": [public(i) for i in self.items], "unsupported": self.unsupported[:20],
                "cut": self.cut, "models": models.status(),
            }

    def item(self, item_id):
        for it in self.items:
            if it["id"] == item_id:
                return it
        raise KeyError(item_id)

    def path_of(self, it):
        p = (self.root / it["rel"]).resolve()
        if self.root not in p.parents:
            raise KeyError(it["id"])
        return p

    def close(self):
        """Stop everything for this folder: another folder has been opened."""
        self.closed.set()
        self.cancel_flag.set()

    def _emit(self, type_, **data):
        if self.closed.is_set():
            return                     # a folder you've moved on from stays quiet
        data["type"] = type_
        data["lib"] = self.key
        data.setdefault("counts", self.counts())
        self.events.send(data)

    # ------------------------------------------------------------------ #
    # Scan: list files and make the thumbnails for the pile
    # ------------------------------------------------------------------ #
    def scan(self):
        self.state = "preparing"
        found = []
        for dirpath, dirs, names in os.walk(self.root):
            if self.closed.is_set():
                return
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS
                             and not d.endswith(SKIP_SUFFIXES))
            for n in sorted(names):
                if n.startswith("."):
                    continue
                p = Path(dirpath) / n
                found.append(p)

        known = {i["rel"]: i for i in self.items}
        items, unsupported = [], []
        for p in found:
            rel = str(p.relative_to(self.root))
            kind = media.kind_of(p)
            if kind is None:
                unsupported.append(rel)
                continue
            st = p.stat()
            old = known.get(rel)
            if old and old["size"] == st.st_size and old["mtime"] == int(st.st_mtime):
                if old["status"] in ("ready", "review"):
                    items.append(old)      # unchanged since last time: keep the analysis
                    continue
                if old.get("thumb") and (self.cache / "thumbs" / old["thumb"]).exists() and old["status"] != "failed":
                    old["status"] = "waiting"
                    items.append(old)      # not sorted yet, but its thumbnail is already made
                    continue
            items.append({
                "id": hashlib.sha1(rel.encode()).hexdigest()[:10], "rel": rel, "name": p.name,
                "folder": str(Path(rel).parent) if Path(rel).parent != Path(".") else "",
                "kind": kind, "size": st.st_size, "mtime": int(st.st_mtime),
                "status": "waiting", "error": None, "thumb": None, "sha": None,
                "duration": None, "has_audio": False, "frames": [],
                "category": None, "suggested": None, "confidence": None, "corrected": False,
                "sharpness": None, "blurry": False, "duplicate_of": None, "duplicate_kind": None,
                "decision": None, "transcript": [], "best": [],
            })
        with self.lock:
            self.items, self.unsupported = items, unsupported
            keep = {i["id"] for i in items}
            self.emb = {k: v for k, v in self.emb.items() if k in keep}
        self._emit("library", snapshot=self.snapshot())

        # Thumbnails on several cores at once; each one shows as soon as it's ready.
        todo = [it for it in self.items if not it["thumb"] and it["status"] != "failed"]
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(self._make_thumb, it): it for it in todo}
            for f in as_completed(futures):
                if self.closed.is_set():
                    pool.shutdown(wait=False, cancel_futures=True)
                    return
                it = futures[f]
                if f.exception():
                    self._fail(it, f.exception())
                self._emit("thumb", item=public(it))
        self._save_cache()
        # Decide event or everyday now, so the folders shown before Sort fit the photos.
        if self.mode_setting == "auto" and not self.closed.is_set():
            self._choose_mode()
        if self.state == "preparing":
            waiting = any(i["status"] == "waiting" for i in self.items)
            self.state = "idle" if waiting else "done"
            self._emit("state", state=self.state)

    def _make_thumb(self, it):
        src = self.path_of(it)
        it["sha"] = media.sha256(src)
        base = self.cache / "thumbs" / it["sha"][:16]
        if it["kind"] == "photo":
            dest = Path(f"{base}.jpg")
            try:
                w, h = media.photo_thumb(src, dest)
                it["pixels"] = w * h
            except Exception:
                raise MediaError("We couldn't open this photo. It may be damaged or not really an image.")
            it["thumb"] = dest.name
        else:
            it["duration"], it["has_audio"] = media.probe(src)
            frames = media.video_frames(src, it["duration"], base)
            it["frames"] = [{"t": t, "thumb": p.name} for t, p in frames]
            it["thumb"] = it["frames"][min(1, len(frames) - 1)]["thumb"]

    def _fail(self, it, e):
        it["status"] = "failed"
        it["error"] = str(e) if isinstance(e, MediaError) else "Something went wrong reading this file."
        it["_detail"] = repr(e)

    # ------------------------------------------------------------------ #
    # Analyse: the worker that drives the animation
    # ------------------------------------------------------------------ #
    def start(self):
        """Begin or resume analysis in the background."""
        with self.lock:
            if self.worker and self.worker.is_alive():
                return False
            self.cancel_flag.clear()
            self.worker = threading.Thread(target=self._run, daemon=True)
            self.worker.start()
            return True

    def cancel(self):
        self.cancel_flag.set()

    def retry(self, ids=None):
        with self.lock:
            for it in self.items:
                if it["status"] == "failed" and (ids is None or it["id"] in ids):
                    it.update(status="waiting", error=None, thumb=None, frames=[])
        return self.start()

    def _run(self):
        self.state = "analysing"
        self._emit("state", state=self.state)
        try:
            # Thumbnails first, for anything new or retried.
            for it in self.items:
                if it["status"] == "waiting" and not it["thumb"]:
                    try:
                        self._make_thumb(it)
                        self._emit("thumb", item=public(it))
                    except Exception as e:
                        self._fail(it, e)
                        self._emit("item", item=public(it))
            self._choose_mode()
            self._index = None
            last_save = time.time()
            # Photos first, then videos shortest first, so one long film can't hold up the rest.
            order = sorted(self.items, key=lambda i: (i["kind"] == "video", i.get("duration") or 0))
            for it in order:
                if self.cancel_flag.is_set():
                    break
                if it["status"] != "waiting":
                    continue
                it["status"] = "analysing"
                self._emit("item", item=public(it))
                try:
                    self._analyse(it)
                except Exception as e:
                    self._fail(it, e)
                self._emit("item", item=public(it))
                if time.time() - last_save > 10:
                    self._save_cache()
                    last_save = time.time()
            if not self.cancel_flag.is_set():
                self._emit("status", text="Checking for copies and other edits")
                self.resolve_copies()
                self.regroup()
            self.state = "paused" if self.cancel_flag.is_set() and any(
                i["status"] == "waiting" for i in self.items) else "done"
        finally:
            self._save_cache()
            self._emit("state", state=self.state)

    def _choose_mode(self):
        """Everyday camera roll or one occasion, judged on a sample of photos."""
        if self.mode_setting != "auto":
            self.mode = self.mode_setting
            return
        clip = models.clip()
        photos = [i for i in self.items if i["kind"] == "photo" and i["thumb"]]
        if not clip or not photos:
            return
        step = max(1, len(photos) // 40)
        vecs = clip.images([self.cache / "thumbs" / i["thumb"] for i in photos[::step][:40]])
        self.mode = self.grouper().detect(vecs)
        self._leaning = self.grouper().leaning(self.mode, vecs)
        self._emit("mode", mode=self.mode, mode_setting=self.mode_setting,
                   categories=groups.names(self.mode))

    def grouper(self):
        if self._grouper is None and models.clip():
            self._grouper = groups.Grouper(models.clip())
        return self._grouper

    def set_mode(self, setting):
        """Switch how the folder is grouped. Corrections you made are kept."""
        if setting not in ("auto", "everyday", "event"):
            raise ValueError("Choose everyday or event.")
        with self.lock:
            self.mode_setting = setting
            if setting != "auto":
                self.mode = setting
            self._leaning = None
            self.regroup(emit=False)
            self._save_cache()
        self._emit("library", snapshot=self.snapshot())

    def regroup(self, emit=True):
        """Re-suggest groups using every photo in the folder for the centring."""
        g = self.grouper()
        if not g:
            return 0
        vecs = [self.emb[i["id"]].mean(0) for i in self.items if i["kind"] == "photo" and i["id"] in self.emb]
        if vecs:
            g.refine(self.mode, np.stack(vecs))
        # Photos you moved yourself are examples for the group you chose.
        by_group = {}
        for i in self.items:
            if i.get("corrected") and i["kind"] == "photo" and i["id"] in self.emb and i["status"] in ("ready", "review"):
                by_group.setdefault(i["category"], []).append(self.emb[i["id"]].mean(0))
        g.set_examples(self.mode, {k: np.stack(v) for k, v in by_group.items()})
        self._leaning = g.leaning(self.mode, np.stack(vecs)) if vecs else None
        changed = []
        for it in self.items:
            if it["id"] in self.emb and it["status"] in ("ready", "review"):
                old = it["category"]
                self._set_category(it, *self._suggest(it))
                if it["category"] != old:
                    changed.append(it)
        if emit:
            for it in changed:
                self._emit("item", item=public(it))
        return len(changed)

    def _suggest(self, it):
        g = self.grouper()
        if not g or it["id"] not in self.emb:
            return unsure_for(it), 0.0
        category, conf = g.categorise(self.emb[it["id"]].mean(0), self.mode, CONFIDENT, self._leaning)
        if self.mode == "everyday" and it["kind"] == "photo" and it["name"].lower().startswith(("screenshot", "screen shot")):
            category, conf = "Screenshots", 1.0
        if self.mode == "everyday" and it["kind"] == "video" and it.get("transcript"):
            spoken = sum(s["end"] - s["start"] for s in it["transcript"])
            if spoken > 0.4 * (it["duration"] or 1) and conf < 0.5:
                category, conf = "Talking to camera", max(conf, 0.5)
        if category == NOT_SURE:
            category = unsure_for(it)
        return category, conf

    def _set_category(self, it, category, conf):
        it["suggested"], it["confidence"] = category, round(conf, 3)
        if not it["corrected"]:
            it["category"] = category

    def _analyse(self, it):
        src = self.path_of(it)
        thumbs = self.cache / "thumbs"
        clip = models.clip()
        pics = [thumbs / it["thumb"]] if it["kind"] == "photo" else [thumbs / f["thumb"] for f in it["frames"]]

        # Sharpness. For video, the median frame, so one soft frame doesn't flag it.
        scores = [media.sharpness(p) for p in pics]
        it["sharpness"] = round(float(np.median(scores)), 1)
        it["blurry"] = it["sharpness"] < BLURRY_BELOW
        if it["kind"] == "video":
            for f, s in zip(it["frames"], scores):
                f["sharp"] = round(s, 1)

        # Signatures for finding copies.
        if it["kind"] == "photo":
            it["_phash"] = str(imagehash.phash(Image.open(pics[0])))
            self.sigs[it["id"]] = copies.signature(pics[0])

        # Pictures: embeddings for search.
        if clip:
            self.emb[it["id"]] = clip.images(pics)

        # Speech.
        if it["kind"] == "video" and it["has_audio"]:
            it["transcript"] = self._transcribe(src)
        if it["kind"] == "video":
            it["best"] = best_bits(it)

        self._set_category(it, *self._suggest(it))
        self._quick_copy_check(it)
        self._set_status(it)

    def _set_status(self, it):
        if it["status"] in ("failed", "waiting"):
            return
        needs = (it["duplicate_of"] or it["blurry"]) and it["decision"] is None
        it["status"] = "review" if needs else "ready"

    def _quick_copy_check(self, it):
        """A fast check while sorting, so copies show up straight away.
        resolve_copies() makes the final decision once every file is done."""
        if self._index is None:
            self._index = {"sha": {}, "ids": [], "hash": []}
            for o in self.items:
                if o is not it and o["status"] in ("ready", "review") and not o.get("duplicate_of"):
                    self._index_add(o)
        it["duplicate_of"] = it["duplicate_kind"] = None
        first = self._index["sha"].get(it["sha"])
        if first:
            it["duplicate_of"], it["duplicate_kind"] = first, "exact"
            return
        if it["kind"] == "photo" and self._index["hash"]:
            h = np.array(self._index["hash"], dtype=np.uint64)
            dist = copies._popcount64(h ^ np.uint64(int(it["_phash"], 16)))
            mine = self.sigs[it["id"]][0]
            for j in np.argsort(dist)[:5]:
                if dist[j] > copies.HASH_NEAR:
                    break
                oid = self._index["ids"][j]
                corr = float(self.sigs[oid][0] @ mine) / mine.size
                if corr >= copies.SIMILAR_AT:
                    it["duplicate_of"] = oid
                    it["duplicate_kind"] = "edit" if corr >= copies.EDIT_AT else "similar"
                    return
        self._index_add(it)

    def _index_add(self, o):
        self._index["sha"].setdefault(o["sha"], o["id"])
        if o["kind"] == "photo" and o.get("_phash") and o["id"] in self.sigs:
            self._index["ids"].append(o["id"])
            self._index["hash"].append(int(o["_phash"], 16))

    def resolve_copies(self):
        """Final say on copies: picks which of each set to keep and relabels the rest."""
        with self.lock:
            before = {i["id"]: (i["duplicate_of"], i["duplicate_kind"], i["status"]) for i in self.items}
            copies.resolve(self.items, self.sigs)
            changed = []
            for it in self.items:
                self._set_status(it)
                if before[it["id"]] != (it["duplicate_of"], it["duplicate_kind"], it["status"]):
                    changed.append(it)
        for it in changed:
            self._emit("item", item=public(it))
        return len(changed)

    def skip_copies(self):
        """Keep the suggested best of each set and skip exact copies and older edits."""
        n = 0
        with self.lock:
            for it in self.items:
                if it["status"] == "review" and it["duplicate_kind"] in ("exact", "edit") and it["decision"] is None:
                    it["decision"] = "skip"
                    it["status"] = "ready"
                    n += 1
            self._save_cache()
        self._emit("library", snapshot=self.snapshot())
        return n

    def _transcribe(self, src):
        model = models.speech()
        if not model:
            return []
        audio = media.audio_16k(src)
        if audio.size == 0 or float(np.abs(audio).max()) < 1e-3:
            return []
        segs, _ = model.transcribe(audio, vad_filter=True, word_timestamps=True, batch_size=8)
        out = []
        for s in segs:
            words = [{"w": w.word.strip(), "t": round(w.start, 2)} for w in (s.words or [])]
            out.append({"start": round(s.start, 2), "end": round(s.end, 2),
                        "text": s.text.strip(), "words": words})
        return out

    # ------------------------------------------------------------------ #
    # Corrections
    # ------------------------------------------------------------------ #
    def update_item(self, item_id, category=None, decision=None):
        with self.lock:
            it = self.item(item_id)
            moved = False
            if category is not None:
                if category not in self.group_names():
                    raise ValueError("That isn't one of the groups.")
                moved = category != it["category"]
                # A group you set by hand is always your choice, even if it matches the suggestion.
                it["category"], it["corrected"] = category, True
            if decision is not None:
                if decision not in ("keep", "skip", None):
                    raise ValueError("Choose keep or skip.")
                it["decision"] = decision
                if it["status"] == "review" and decision:
                    it["status"] = "ready"
            self._save_cache()
        self._emit("item", item=public(it))
        if moved:
            self._regroup_soon()
        return public(it)

    def _regroup_soon(self):
        """Learn from a move a moment later, so a run of moves counts as one."""
        if getattr(self, "_regroup_timer", None):
            self._regroup_timer.cancel()
        if self.state == "analysing":
            return                     # the end of the run regroups anyway
        self._regroup_timer = threading.Timer(0.8, self._learn_from_moves)
        self._regroup_timer.daemon = True
        self._regroup_timer.start()

    def _learn_from_moves(self):
        with self.lock:
            n = self.regroup(emit=True)
            self._save_cache()
        self._emit("regrouped", moved=n)

    def group_names(self):
        return groups.names(self.mode) + [c for c in self.custom if c not in groups.names(self.mode)]

    def rename_group(self, old, new):
        """Rename a group, which is also the folder it's copied into."""
        new = " ".join((new or "").split())[:40]
        if not new or "/" in new or new.startswith("."):
            raise ValueError("Use a short name without slashes, like Wedding or Holiday 2026.")
        if new in ("Needs review", "Couldn't open", "Skipped"):
            raise ValueError("That name is already used for something else. Try another.")
        with self.lock:
            moved = 0
            for it in self.items:
                if it["category"] == old:
                    it["category"], it["corrected"] = new, True
                    moved += 1
            if new not in self.group_names():
                self.custom.append(new)
            self._save_cache()
        self._emit("library", snapshot=self.snapshot())
        return moved

    def set_cut(self, clips):
        clean = []
        for c in clips:
            it = self.item(str(c["id"]))
            if it["status"] not in ("ready", "review"):
                continue
            length = it["duration"] or 3.0
            start = max(0.0, min(float(c.get("start", 0)), length))
            end = float(c.get("end", start + 3)) if it["kind"] == "video" else start + float(c.get("length", 3))
            if it["kind"] == "video":
                end = max(start + 0.5, min(end, length))
            clean.append({"id": it["id"], "start": round(start, 2), "end": round(end, 2)})
        with self.lock:
            self.cut = clean
            self._save_cache()
        return clean

    # ------------------------------------------------------------------ #
    # Cache
    # ------------------------------------------------------------------ #
    def _save_cache(self):
        with self.lock:
            data = {"items": self.items, "unsupported": self.unsupported, "cut": self.cut,
                    "mode": self.mode, "mode_setting": self.mode_setting, "custom": self.custom}
            tmp = self.cache / "index.json.tmp"
            tmp.write_text(json.dumps(data))
            tmp.replace(self.cache / "index.json")
            if self.emb:
                np.savez(self.cache / "emb.npz", **self.emb)
            if self.sigs:
                np.savez(self.cache / "sigs.npz", **{k: np.append(v[0], v[1]) for k, v in self.sigs.items()})

    def _load_cache(self):
        idx = self.cache / "index.json"
        if not idx.exists():
            return
        try:
            data = json.loads(idx.read_text())
            self.items = data.get("items", [])
            self.cut = data.get("cut", [])
            self.mode = data.get("mode", "everyday")
            self.mode_setting = data.get("mode_setting", "auto")
            self.custom = data.get("custom", [])
            for it in self.items:          # an interrupted run goes back to the queue
                if it["status"] == "analysing":
                    it["status"] = "waiting"
                for key in ("category", "suggested"):
                    if it.get(key) == groups.OLD_NOT_SURE:
                        it[key] = unsure_for(it)
            if (self.cache / "emb.npz").exists():
                with np.load(self.cache / "emb.npz") as z:
                    self.emb = {k: z[k] for k in z.files}
            if (self.cache / "sigs.npz").exists():
                with np.load(self.cache / "sigs.npz") as z:
                    self.sigs = {k: (z[k][:-1], float(z[k][-1])) for k in z.files}
        except Exception:
            self.items, self.emb = [], {}


def best_bits(it):
    """Up to three suggested moments: things said first, then the sharpest stretch."""
    out = []
    for s in sorted(it["transcript"], key=lambda s: s["end"] - s["start"], reverse=True)[:3]:
        words = s["text"].split()
        label = " ".join(words[:7]) + ("…" if len(words) > 7 else "")
        out.append({"start": float(s["start"]), "end": float(s["end"]), "label": f"“{label}”"})
    if len(out) < 3 and it["frames"]:
        f = max(it["frames"], key=lambda f: f.get("sharp", 0))
        dur = it["duration"] or 3
        start = max(0.0, f["t"] - 1.5)
        span = {"start": round(start, 2), "end": round(min(dur, start + 4), 2), "label": "Clearest shot"}
        if not any(abs(span["start"] - o["start"]) < 2 for o in out):
            out.append(span)
    return sorted(out, key=lambda b: b["start"])


def public(it):
    return {k: v for k, v in it.items() if not k.startswith("_") and k not in ("sha",)}
