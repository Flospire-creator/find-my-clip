"""Find a clip: match a plain-English search against pictures and speech.

Two signals:
- What it looks like: the picture model scores the search against every
  photo and every video keyframe, so a video result carries a timestamp.
- What was said: the words of the search (minus filler) against the
  transcript, with word-level times.

A spoken match outranks a visual one, because it is literal.
"""

import re

import numpy as np

from . import copies, models

STOP = set("""a an the and or of to in on at for with from by about into over is are was were be been
this that these those it its my me i im i'm we our you your he she they them his her their
where when what which who how video videos clip clips photo photos picture pictures one find show
said say says saying talked talk talking mentioned mention speak spoke spoken""".split())
SPEECH_HINTS = {"said", "say", "says", "saying", "talked", "talk", "talking", "mentioned", "mention", "spoke", "speak"}
VISUAL_FLOOR = 0.15      # cosine below this is never shown
STAND_OUT = 1.5          # standard deviations above the library's average for this search
NEAR_TOP = 0.04          # and within this of the best score
MAX_RESULTS = 24


def stem(w):
    w = w.lower().strip(".,!?\"'“”‘’:;()")
    for suffix in ("ing", "ed", "es", "s"):
        if len(w) > 4 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def visual_query(q):
    q = q.strip().lower()
    q = re.sub(r"^(the )?(video|clip|photo|picture)s?( of| where| with)?\s+", "", q)
    q = re.sub(r"^(me|i|myself)\b", "a person", q)
    return f"a photo of {q}"


def search(lib, q, limit=MAX_RESULTS):
    q = (q or "").strip()
    if not q:
        return []
    terms = [stem(w) for w in re.findall(r"[\w'’]+", q) if w.lower() not in STOP]
    wants_speech = any(w.lower() in SPEECH_HINTS for w in re.findall(r"\w+", q))
    results = []

    # What was said.
    if terms:
        for it in lib.items:
            for seg in it.get("transcript", []):
                words = seg.get("words") or [{"w": w, "t": seg["start"]} for w in seg["text"].split()]
                hits = [w for w in words if stem(w["w"]) in terms]
                if not hits:
                    continue
                matched = len({stem(w["w"]) for w in hits})
                score = 0.6 + 0.4 * matched / len(set(terms))
                start = max(0.0, hits[0]["t"] - 1.0)
                results.append({
                    "id": it["id"], "kind": it["kind"], "start": round(start, 2),
                    "end": round(seg["end"], 2), "score": round(score, 3), "why": "said",
                    "quote": seg["text"], "thumb": frame_at(it, start),
                })

    # What it looks like.
    # Picture scores swing a lot between searches, so a fixed cut-off fails.
    # Each search is judged against its own spread: a match has to stand out
    # from the rest of the library and sit near the best score.
    clip = models.clip()
    said_hit = any(r["why"] == "said" for r in results)
    if clip and lib.emb and not (wants_speech and said_hit):
        qv = clip.texts([visual_query(q)])[0]
        best_frame = {}
        for it in lib.items:
            emb = lib.emb.get(it["id"])
            if emb is not None:
                sims = emb @ qv
                i = int(sims.argmax())
                best_frame[it["id"]] = (float(sims[i]), i)
        scores = np.array([s for s, _ in best_frame.values()]) if best_frame else np.zeros(1)
        mean, std, top = float(scores.mean()), float(scores.std()) or 1.0, float(scores.max())
        for it in lib.items:
            if it["id"] not in best_frame:
                continue
            s, i = best_frame[it["id"]]
            if s < VISUAL_FLOOR or (s - mean) / std < STAND_OUT or s < top - NEAR_TOP:
                continue
            score = s * (0.8 if wants_speech else 1.0)
            r = {"id": it["id"], "kind": it["kind"], "score": round(score, 3), "why": "looks"}
            if it["kind"] == "video":
                t = it["frames"][i]["t"]
                r.update(start=round(max(0.0, t - 1.0), 2),
                         end=round(min(it["duration"] or t + 3, t + 3), 2),
                         thumb=it["frames"][i]["thumb"])
            else:
                r.update(start=0, end=0, thumb=it["thumb"])
            results.append(r)

    # Filename words, so a search still works without the picture model.
    for it in lib.items:
        name = stem(it["name"].rsplit(".", 1)[0])
        if terms and any(t in re.findall(r"\w+", it["name"].lower()) or t == name for t in terms):
            results.append({"id": it["id"], "kind": it["kind"], "start": 0, "end": 0,
                            "score": 0.45, "why": "name", "thumb": it["thumb"]})

    # One result per picture: copies and other edits fold into the one we'd keep.
    by_id = {it["id"]: it for it in lib.items}
    best, versions = {}, {}
    for it in lib.items:
        versions.setdefault(copies.root_of(it, by_id), set()).add(it["id"])
    for r in sorted(results, key=lambda r: r["score"], reverse=True):
        root = copies.root_of(by_id[r["id"]], by_id)
        if root != r["id"] and r["why"] != "said":
            keep = by_id[root]
            r = {**r, "id": root, "thumb": keep["thumb"] if keep["kind"] == "photo" else r.get("thumb")}
        key = (root, round(r.get("start", 0) / 4))
        if r["why"] == "name" and any(k[0] == root for k in best):
            continue
        if key not in best:
            best[key] = {**r, "versions": len(versions.get(root, ())) }
    out = sorted(best.values(), key=lambda r: r["score"], reverse=True)
    return out[:limit]


def frame_at(it, t):
    if not it.get("frames"):
        return it.get("thumb")
    f = min(it["frames"], key=lambda f: abs(f["t"] - t))
    return f["thumb"]
