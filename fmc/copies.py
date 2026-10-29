"""Finding copies, other edits and similar shots, and choosing which to keep.

Three kinds, calibrated on a wedding delivery (graded set, two downloads):
- exact:   the same bytes
- edit:    the same frame with a different grade or crop. Structure match >= 0.985
           (graded versions scored 0.999+, a colour and black-and-white pair 0.993)
- similar: a shot taken moments apart, or a black-and-white alternative.
           Hash distance <= 6 and structure >= 0.90 (bursts scored 0.92 to 0.95)

Structure match is the correlation of a 32px grey copy after histogram
equalisation, so a change of grade barely moves it but a change of pose does.
"""

import re

import numpy as np
from PIL import Image, ImageOps

EDIT_AT = 0.985
SIMILAR_AT = 0.90
HASH_NEAR = 6
COPYISH = re.compile(r"(\bcopy\b|\(\d+\)|\s\d+$|download \d+|duplicate)", re.I)


def signature(img_path):
    """Grey 32x32, equalised and normalised; plus how colourful it is."""
    with Image.open(img_path) as im:
        g = ImageOps.equalize(ImageOps.grayscale(im)).resize((32, 32))
        sat = float(np.asarray(im.convert("HSV"))[..., 1].mean())
    a = np.asarray(g, np.float32).ravel()
    return (a - a.mean()) / (a.std() + 1e-6), sat


def _popcount64(x):
    b = x.view(np.uint8).reshape(-1, 8)
    return np.unpackbits(b, axis=1).sum(1)


def keeper_score(it):
    """Higher is the one to keep: not in a folder that looks like a copy, then
    not blurry, then the bigger picture, then the most recent edit, then the larger file."""
    path_bits = it["rel"].split("/")
    copyish = any(COPYISH.search(p.rsplit(".", 1)[0]) for p in path_bits)
    return (not copyish, not it.get("blurry"), it.get("pixels", 0), it.get("mtime", 0), it.get("size", 0), -len(it["rel"]))


def resolve(items, sigs):
    """Set duplicate_of / duplicate_kind on every item. Returns the number of pairs found."""
    for it in items:
        it["duplicate_of"] = it["duplicate_kind"] = None
    live = [it for it in items if it["status"] in ("ready", "review")]

    # 1. Exact copies, any kind of file.
    by_sha = {}
    for it in live:
        by_sha.setdefault(it["sha"], []).append(it)

    # 2. Union exact copies and other edits into "same photo" clusters.
    parent = {it["id"]: it["id"] for it in live}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        parent[find(a)] = find(b)

    for group in by_sha.values():
        for o in group[1:]:
            union(o["id"], group[0]["id"])

    photos = [it for it in live if it["kind"] == "photo" and it.get("_phash") and it["id"] in sigs]
    similar = []
    if photos:
        hashes = np.array([int(it["_phash"], 16) for it in photos], dtype=np.uint64)
        mat = np.stack([sigs[it["id"]][0] for it in photos])
        for i in range(len(photos) - 1):
            dist = _popcount64(hashes[i + 1:] ^ hashes[i])
            for j in np.nonzero(dist <= HASH_NEAR)[0] + i + 1:
                a, b = photos[i], photos[j]
                if a["sha"] == b["sha"]:
                    continue
                corr = float(mat[i] @ mat[j]) / mat.shape[1]
                if corr >= EDIT_AT:
                    union(a["id"], b["id"])
                elif corr >= SIMILAR_AT:
                    similar.append((a, b))

    # 3. In each cluster keep the best and point the rest at it.
    clusters = {}
    for it in live:
        clusters.setdefault(find(it["id"]), []).append(it)
    keeper = {}
    for members in clusters.values():
        best = max(members, key=keeper_score)
        for it in members:
            keeper[it["id"]] = best
            if it is not best:
                it["duplicate_of"] = best["id"]
                it["duplicate_kind"] = "exact" if it["sha"] == best["sha"] else "edit"

    # 4. Similar shots: the sharper one stays, the other is offered for review.
    seen = set()
    for a, b in similar:
        ka, kb = keeper[a["id"]], keeper[b["id"]]
        if ka is kb or (ka["id"], kb["id"]) in seen:
            continue
        seen.add((ka["id"], kb["id"]))
        seen.add((kb["id"], ka["id"]))
        stay, other = (ka, kb) if (ka.get("sharpness") or 0) >= (kb.get("sharpness") or 0) else (kb, ka)
        if other["duplicate_of"] is None:
            other["duplicate_of"], other["duplicate_kind"] = stay["id"], "similar"
            other["mono"] = sigs[other["id"]][1] < 3 <= sigs[stay["id"]][1] if other["id"] in sigs and stay["id"] in sigs else False
    return sum(1 for it in live if it["duplicate_of"])


def root_of(item, by_id):
    """The picture a copy or edit belongs to, for collapsing search results."""
    seen = set()
    while item.get("duplicate_of") and item.get("duplicate_kind") in ("exact", "edit") and item["id"] not in seen:
        seen.add(item["id"])
        nxt = by_id.get(item["duplicate_of"])
        if not nxt:
            break
        item = nxt
    return item["id"]
