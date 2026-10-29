"""Find My Clip: local web app.

    .venv/bin/python app.py                 open http://localhost:8770
    .venv/bin/python app.py ~/Movies/Phone  start with a folder

The server only listens on this Mac (127.0.0.1) and refuses requests from
other websites, so a page elsewhere can't drive it.
"""

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

from fmc import export, models, search
from fmc.library import Events, Library

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "samples"
PORT = int(os.environ.get("FMC_PORT", 8770))

app = Flask(__name__, static_folder=None)
events = Events()
current = {"lib": None, "export": None}
copy_lock = threading.Lock()


def lib():
    if current["lib"] is None:
        abort(409, "Choose a folder first.")
    return current["lib"]


# ---------------------------------------------------------------------- #
# Guard: only this Mac, only this page
# ---------------------------------------------------------------------- #
@app.before_request
def guard():
    host = (request.host or "").split(":")[0]
    if host not in ("localhost", "127.0.0.1"):
        abort(403)
    if request.method != "GET":
        origin = request.headers.get("Origin")
        if origin and origin not in (f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"):
            abort(403)
        if request.headers.get("X-Find-My-Clip") != "1":
            abort(403)


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(409)
def err(e):
    return jsonify({"ok": False, "message": getattr(e, "description", "Something went wrong.")}), e.code


# ---------------------------------------------------------------------- #
# Pages and files
# ---------------------------------------------------------------------- #
@app.get("/")
def index():
    return send_from_directory(ROOT / "static", "index.html")


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(ROOT / "static", name)


@app.get("/thumb/<name>")
def thumb(name):
    if "/" in name or ".." in name:
        abort(404)
    return send_from_directory(lib().cache / "thumbs", name, max_age=3600)


@app.get("/media/<item_id>")
def media_file(item_id):
    """The original file, for previews. Only files that are in the library."""
    l = lib()
    try:
        it = l.item(item_id)
        path = l.path_of(it)
    except KeyError:
        abort(404)
    if it["kind"] == "photo" and path.suffix.lower() in (".heic", ".heif"):
        # Browsers can't show HEIC, so previews use a large JPEG made on the fly.
        from PIL import Image, ImageOps
        import io
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((1600, 1600))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=88)
        buf.seek(0)
        return send_file(buf, mimetype="image/jpeg")
    mimetype = "video/mp4" if path.suffix.lower() in (".mov", ".m4v") else None
    return send_file(path, mimetype=mimetype, conditional=True)


# ---------------------------------------------------------------------- #
# API
# ---------------------------------------------------------------------- #
@app.get("/api/state")
def state():
    if current["lib"] is None:
        return jsonify({"state": "empty", "models": models.status(), "has_samples": SAMPLES.exists()})
    snap = current["lib"].snapshot()
    job = current.get("copy") or {}
    if job.get("result") and job.get("lib") == current["lib"].key:
        snap["copied"] = job["result"]    # so a reload still shows the folders were made
    return jsonify(snap)


@app.post("/api/choose-folder")
def choose_folder():
    """The Mac's own folder picker. Nothing is read until a folder is chosen."""
    script = 'POSIX path of (choose folder with prompt "Choose a folder of photos and videos")'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        return jsonify({"ok": False, "cancelled": True})
    return open_folder(r.stdout.strip(), sample=False)


@app.post("/api/open")
def open_path():
    body = request.get_json(silent=True) or {}
    if body.get("sample"):
        if not SAMPLES.exists():
            subprocess.run([sys.executable, str(ROOT / "scripts/make_samples.py")], check=True)
        return open_folder(SAMPLES, sample=True)
    path = (body.get("path") or "").strip()
    if not path:
        abort(400, "Paste a folder path, like /Users/you/Pictures/Phone.")
    return open_folder(path, sample=False)


def open_folder(path, sample):
    old = current["lib"]
    if old:
        old.close()           # stop its scan and sorting, and silence its updates
    try:
        l = Library(path, events=events, is_sample=sample)
    except ValueError as e:
        abort(400, str(e))
    current["lib"] = l
    threading.Thread(target=l.scan, daemon=True).start()
    return jsonify({"ok": True, "folder": l.root.name})


@app.post("/api/analyse")
def analyse():
    l = lib()
    if l.state == "preparing":
        abort(409, "Still reading the folder. Try again in a moment.")
    started = l.start()
    if (request.get_json(silent=True) or {}).get("then_create") and l.worker:
        threading.Thread(target=create_when_sorted, args=(l,), daemon=True).start()
    return jsonify({"ok": started})


def create_when_sorted(l):
    """One press: once sorting finishes, create the folders. This waits on the
    server, so it still happens if the page is reloaded halfway through."""
    if l.worker:
        l.worker.join()
    if l.state == "done" and current["lib"] is l and not l.closed.is_set():
        start_copy(l)


@app.post("/api/resort")
def resort():
    """Sort the same folder again: pick up new files, regroup, create the folders."""
    l = lib()
    body = request.get_json(silent=True) or {}
    mode = body.get("mode")
    if mode not in (None, "auto", "everyday", "event"):
        abort(400, "Choose everyday or event.")
    if l.state in ("preparing", "analysing") or (current.get("copy") and current["copy"].get("running")):
        abort(409, "Still busy with this folder. Try again when it's finished.")

    def work():
        l.scan()                       # new files come in as waiting; unchanged ones keep their analysis
        if l.closed.is_set():
            return
        if mode:
            l.set_mode(mode)
        l.start()
        create_when_sorted(l)
    threading.Thread(target=work, daemon=True).start()
    return jsonify({"ok": True})


@app.post("/api/cancel")
def cancel():
    lib().cancel()
    if current["export"]:
        current["export"]["cancel"] = True
    if current.get("copy"):
        current["copy"]["cancel"] = True
    return jsonify({"ok": True})


@app.post("/api/retry")
def retry():
    body = request.get_json(silent=True) or {}
    return jsonify({"ok": lib().retry(body.get("ids"))})


@app.post("/api/items/<item_id>")
def update_item(item_id):
    body = request.get_json(silent=True) or {}
    try:
        it = lib().update_item(item_id, category=body.get("category"),
                               decision=body.get("decision") if "decision" in body else None)
    except KeyError:
        abort(404)
    except ValueError as e:
        abort(400, str(e))
    return jsonify({"ok": True, "item": it})


@app.post("/api/mode")
def set_mode():
    body = request.get_json(silent=True) or {}
    try:
        lib().set_mode(body.get("mode", "auto"))
    except ValueError as e:
        abort(400, str(e))
    return jsonify({"ok": True})


@app.post("/api/rename-group")
def rename_group():
    body = request.get_json(silent=True) or {}
    try:
        moved = lib().rename_group(body.get("from", ""), body.get("to", ""))
    except ValueError as e:
        abort(400, str(e))
    return jsonify({"ok": True, "moved": moved})


@app.post("/api/review/skip-copies")
def skip_copies():
    return jsonify({"ok": True, "skipped": lib().skip_copies()})


@app.get("/api/search")
def do_search():
    return jsonify({"results": search.search(lib(), request.args.get("q", ""))})


@app.get("/api/folders-plan")
def folders_plan():
    return jsonify(export.plan_folders(lib()))


@app.post("/api/copy")
def copy():
    """Copy to a project (quick, a few files), or create the sorted folders in the background."""
    body = request.get_json(silent=True) or {}
    l = lib()
    ids = body.get("ids")
    if body.get("to") == "project":
        if not ids:
            abort(400, "Pick some files to add to the project.")
        return jsonify({"ok": True, **export.copy_to_project(l, body.get("name") or "My project", set(ids))})
    if not start_copy(l, set(ids) if ids else None):
        abort(409, "The folders are already being created.")
    return jsonify({"ok": True, "started": True})


def start_copy(l, ids=None):
    """Create the sorted folders in the background. False if that's already running."""
    with copy_lock:
        if current.get("copy") and current["copy"].get("running"):
            return False
        job = {"running": True, "cancel": False, "lib": l.key}
        current["copy"] = job
    events.send({"type": "copy", "done": 0, "total": 0, "lib": l.key})

    def work():
        try:
            out = export.copy_to_folders(
                l, ids,
                progress=lambda d, t: events.send({"type": "copy", "done": d, "total": t, "lib": l.key}),
                cancelled=lambda: job["cancel"])
            job["result"] = out
            events.send({"type": "copy_done", "lib": l.key, **out})
        except Exception:
            events.send({"type": "copy_failed", "lib": l.key,
                         "message": "The folders couldn't be finished. Your originals are untouched."})
        finally:
            job["running"] = False

    threading.Thread(target=work, daemon=True).start()
    return True


@app.post("/api/cut")
def save_cut():
    body = request.get_json(silent=True) or {}
    try:
        return jsonify({"ok": True, "cut": lib().set_cut(body.get("clips", []))})
    except KeyError:
        abort(400, "One of those clips is no longer in the folder.")


@app.post("/api/cut/export")
def export_cut():
    l = lib()
    body = request.get_json(silent=True) or {}
    if current["export"] and current["export"].get("running"):
        abort(409, "A first cut is already being made.")
    clips = l.set_cut(body.get("clips", l.cut))
    if not clips:
        abort(400, "Add at least one clip first.")
    job = {"running": True, "cancel": False}
    current["export"] = job

    def work():
        try:
            out = export.render_cut(
                l, clips, body.get("shape", "vertical"),
                progress=lambda d, t: events.send({"type": "export", "done": d, "total": t}),
                cancelled=lambda: job["cancel"])
            events.send({"type": "export_done", **out})
        except Exception as e:
            events.send({"type": "export_failed", "message": str(e) if isinstance(e, (ValueError, RuntimeError, InterruptedError)) else "The first cut couldn't be made."})
        finally:
            job["running"] = False

    threading.Thread(target=work, daemon=True).start()
    return jsonify({"ok": True})


@app.get("/made")
def made():
    """Play back a first cut we exported. Only files inside our output folder."""
    p = Path(request.args.get("path", "")).resolve()
    out = export.OUTPUT.resolve()
    if out not in p.parents or not p.is_file():
        abort(404)
    return send_file(p, conditional=True)


@app.post("/api/reveal")
def reveal():
    """Show a file we made in Finder. Only paths inside our own output folders."""
    body = request.get_json(silent=True) or {}
    p = Path(body.get("path", "")).resolve()
    allowed = [export.OUTPUT.resolve(), export.SORTED.resolve()]
    if current["lib"]:
        allowed.append(current["lib"].root.parent.resolve())
    if not p.exists() or not any(a == p or a in p.parents for a in allowed):
        abort(400, "That file isn't one we made.")
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(p)])
    elif os.name == "nt":
        subprocess.run(["explorer", "/select,", str(p)])
    else:
        subprocess.run(["xdg-open", str(p if p.is_dir() else p.parent)])
    return jsonify({"ok": True})


@app.get("/events")
def stream():
    q = events.subscribe()

    def gen():
        try:
            while True:
                try:
                    yield f"data: {json.dumps(q.get(timeout=15))}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            events.unsubscribe(q)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def main():
    ap = argparse.ArgumentParser(description="Find My Clip")
    ap.add_argument("folder", nargs="?", help="a folder to open straight away")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if args.folder:
        with app.test_request_context():
            open_folder(args.folder, sample=Path(args.folder).resolve() == SAMPLES)
    global PORT
    PORT = port = free_port(PORT)     # the safety checks above compare against PORT
    url = f"http://localhost:{port}"
    print(f"Find My Clip is running at {url}")
    print("Keep this window open while you use it. Closing it stops Find My Clip.")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, threaded=True)


def free_port(start):
    """The first free port from start, so a second copy or another app never blocks it."""
    import socket
    for port in range(start, start + 20):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


if __name__ == "__main__":
    main()
