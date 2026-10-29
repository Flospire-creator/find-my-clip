"""The two ways of grouping a folder.

Everyday: a phone camera roll, with screenshots, food, travel and so on.
Event: one occasion such as a wedding or a party, grouped by the parts of the day.

Each group has a few descriptions the picture model compares against. The
event descriptions were tuned on a 230-photo sample of one wedding and then
tested on the rest of that wedding, which they had not seen.
"""

import numpy as np

EVERYDAY = {
    "Talking to camera": [
        "a selfie of a person looking at the camera",
        "a person talking to the camera in a vlog",
        "a close-up of someone's face",
    ],
    "Friends & family": [
        "a group of friends smiling together",
        "a family photo with children",
        "people hugging at a party or celebration",
    ],
    "Food & drink": [
        "a photo of a plate of food",
        "a cup of coffee on a café table",
        "a snack or a dessert",
    ],
    "Outdoors": [
        "a landscape of mountains, trees or the sea",
        "flowers and plants outdoors",
        "a wild animal in nature",
    ],
    "Sport & fitness": [
        "a person exercising at the gym",
        "a ball used to play a sport",
        "people playing a sport on a pitch or court",
    ],
    "Work": [
        "a laptop on a desk",
        "a home office with a computer",
        "a meeting or a presentation at work",
    ],
    "Screenshots": [
        "a screenshot of a phone app",
        "a screenshot of a website",
        "a screenshot of a chat conversation",
    ],
    "Outfits": [
        "a mirror selfie showing an outfit",
        "a person showing off their clothes",
        "clothes and shoes laid out flat",
    ],
    "Travel": [
        "a tourist photo of a famous landmark",
        "a view from an aeroplane window",
        "a beach holiday by the sea",
    ],
    "Aesthetic spaces": [
        "a beautifully styled café or restaurant interior with soft light",
        "an aesthetic corner with plants, candles and natural light",
        "the inside of a stylish shop or hotel lobby",
    ],
    "Home decor": [
        "a living room with a sofa, cushions and a rug",
        "home decor on a shelf, vases and picture frames",
        "a bedroom with a made bed and bedside lamp",
    ],
    "Inspo": [
        "a mood board collage of inspiration images",
        "a saved Pinterest style inspiration photo",
        "a magazine editorial layout or fashion lookbook page",
    ],
    "Music & hobbies": [
        "a musical instrument",
        "a person playing music",
        "a hobby such as painting, crafts or gaming",
    ],
    "Notes & text": [
        "a page of printed text",
        "a handwritten note on paper",
        "a poster with a quote in large letters",
    ],
}

EVENT = {
    "Getting ready": [
        "a bride having her hair and makeup done before the wedding",
        "a groom getting dressed, fastening his tie, cufflinks or jacket",
        "a man in a kilt or suit getting ready in a hotel room",
        "bridesmaids getting ready in a bedroom",
        "a close-up of perfume, jewellery or shoes laid out before a wedding",
    ],
    "Ceremony": [
        "a wedding ceremony with guests seated in rows of chairs",
        "a bride walking down the aisle",
        "the couple holding hands at the altar during their vows",
        "exchanging rings during a wedding ceremony",
        "an officiant reading to the bride and groom",
    ],
    "The couple": [
        "a portrait of the bride and groom together outdoors",
        "the newlyweds kissing",
        "a bride and groom walking together across a lawn",
        "a romantic photo of the married couple",
    ],
    "Groups": [
        "a large group photo of wedding guests standing in rows",
        "a formal family group portrait at a wedding",
        "bridesmaids and groomsmen lined up posing together",
        "a small group of people posing for a photo outdoors",
    ],
    "Drinks": [
        "guests holding drinks and chatting on a lawn",
        "a drinks reception outdoors with champagne glasses",
        "people mingling in the sunshine at a garden party",
    ],
    "Food & speeches": [
        "guests seated at long dining tables at a wedding breakfast",
        "a man giving a speech with a microphone at a wedding",
        "guests laughing at a speech while seated at tables",
        "cutting a wedding cake",
    ],
    "Dancing & party": [
        "the couple's first dance",
        "guests dancing on a dance floor",
        "a band or musician playing at a party",
        "an evening party with people celebrating",
    ],
    "Details & venue": [
        "a grand country house venue and its gardens",
        "table decorations, flowers and place settings",
        "an empty room set up for a wedding",
        "a close-up of wedding rings or the bouquet",
    ],
}

SETS = {"everyday": EVERYDAY, "event": EVENT}
NOT_SURE = "Other photos"
NOT_SURE_VIDEO = "Other videos"   # the same group on the video side, so its folder reads right
OLD_NOT_SURE = "Not sure yet"     # its name before 29 Oct 2026, still in older saved folders
# How strongly a photo you moved pulls lookalikes into the same group.
# Set on the wedding tuning set by simulating 5 fixes per group over 5 random
# draws (eval/simulate_corrections.py): from the middle of the range where
# every draw improved the photos that weren't moved (+3 to +8 points).
EXAMPLE_WEIGHT = 30.0
EXAMPLE_SHARPNESS = 10.0

# Used to tell a camera roll from one occasion, on a sample of the folder.
EVENT_HINTS = ["a photo from a wedding", "guests at a formal party or celebration",
               "a professional photograph of an event"]
EVERYDAY_HINTS = ["an everyday phone photo", "a screenshot of a phone", "a casual snapshot at home",
                  "a photo of food or a pet"]


def names(mode):
    return list(SETS[mode]) + [NOT_SURE]


class Grouper:
    """Caches the text side of the picture model for each set of groups."""

    def __init__(self, clip):
        self.clip = clip
        self._text = {}
        self._protos = {}
        self._examples = {}       # mode -> {group index: mean of the photos you put there}
        self.example_weight = EXAMPLE_WEIGHT
        self.example_sharpness = EXAMPLE_SHARPNESS

    def rows(self, mode):
        """The group prototypes in use: the descriptions, refined from the folder."""
        return self._protos.get(mode, self._rows(mode))

    def set_examples(self, mode, by_group):
        """by_group: {group name: (n, d) array of photos you put in that group}."""
        names = list(SETS[mode])
        ex = {}
        for name, vecs in by_group.items():
            if name in names and len(vecs):
                ex[names.index(name)] = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
        self._examples[mode] = ex

    def example_bonus(self, mode, v):
        """A bonus per group for looking like a photo you put there (Tip-Adapter
        style): near-identical shots get a strong pull, everything else almost none."""
        ex = self._examples.get(mode)
        bonus = np.zeros(len(SETS[mode]))
        if not ex:
            return bonus
        for g, E in ex.items():
            bonus[g] = np.exp(-self.example_sharpness * (1 - E @ v)).sum()
        return self.example_weight * bonus

    def refine(self, mode, vecs, alpha=0.5):
        """Learn from the folder itself, without labels: after a first pass,
        pull each event group's prototype towards the photos it already holds.
        On the wedding tuning set this took Ceremony from 37% to 46% and
        overall from 60.3% to 61.6%. Settled on one pass at weight 0.5 before
        testing on unseen photos, where it barely moved: 60.4% to 60.7%
        overall, Ceremony 29% to 30%. Kept because it does no harm."""
        self._protos.pop(mode, None)
        if mode != "event" or len(vecs) < 20:
            return
        V = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
        T = self._rows(mode)
        s = V @ T.T
        s = s - s.mean(0, keepdims=True)
        pred = s.argmax(1)
        P = []
        for g in range(len(T)):
            m = V[pred == g]
            if len(m):
                c = m.mean(0)
                p_ = T[g] + alpha * c / np.linalg.norm(c)
                P.append(p_ / np.linalg.norm(p_))
            else:
                P.append(T[g])
        self._protos[mode] = np.stack(P)

    def _rows(self, mode):
        if mode not in self._text:
            rows = []
            for n, prompts in SETS[mode].items():
                m = self.clip.texts(prompts).mean(0)
                rows.append(m / np.linalg.norm(m))
            self._text[mode] = np.stack(rows)
        return self._text[mode]

    def leaning(self, mode, vecs):
        """Each group's average score over a set of photos, for centring."""
        if mode != "event" or len(vecs) == 0:
            return None
        v = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
        return (v @ self.rows(mode).T).mean(0)

    def categorise(self, vec, mode, confident, leaning=None):
        v = vec / np.linalg.norm(vec)
        sims = self.rows(mode) @ v
        if leaning is not None:
            sims = sims - leaning
        logits = 100.0 * sims
        if mode in SETS:
            logits = logits + self.example_bonus(mode, v)
        p = np.exp(logits - logits.max())
        p /= p.sum()
        i = int(p.argmax())
        name = list(SETS[mode])[i]
        return (name if p[i] >= confident else NOT_SURE), float(p[i])

    def detect(self, vecs):
        """'event' when most of a sample of photos look like one occasion."""
        if len(vecs) == 0:
            return "everyday"
        ev = self.clip.texts(EVENT_HINTS)
        ed = self.clip.texts(EVERYDAY_HINTS)
        v = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
        votes = (v @ ev.T).max(1) > (v @ ed.T).max(1)
        return "event" if votes.mean() >= 0.6 else "everyday"
