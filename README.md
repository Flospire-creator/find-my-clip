# Find My Clip

Find My Clip tidies a messy folder of photos and videos into neat folders, like Travel, Outfits, Food & drink or Screenshots, and keeps one best copy of each picture. It can also find the moment you're looking for, and turn the clips you pick into a first draft of a video.

Everything happens on your own computer. Your photos stay exactly where they are, and Find My Clip makes tidy copies in a new folder.

## How to set it up

1. Open **Claude Code**. It's in the Claude desktop app, under the Code tab.
2. Drag this file into the chat.
3. Type **set this up for me** and press Enter.

Claude will explain each step and ask before it installs anything. Setting up takes about 15 minutes and uses about 3 GB of space, most of it for the parts that recognise what's in your photos. If Claude asks you to type your computer password, that's your computer's own installer asking, so type it into the box that appears.

For your first go, try a copy of a folder, so you can see how it works.

---

## Instructions for Claude

The person who gave you this file would like Find My Clip running on their computer. They may be new to code, so explain what you're doing in plain, friendly language, one short step at a time, and check in before anything that takes a while or needs their password.

### Rules for the whole setup

- Before you start, tell them in three or four plain sentences what you'll do: download the app, install what it needs, then open it. Ask if that's OK.
- Leave their photos and videos exactly as they are. The app only reads them, and you should never move, rename or delete any of them.
- Ask before installing anything that changes the computer itself, such as Python, Homebrew or ffmpeg, and say why it's needed.
- Nothing gets uploaded. The app runs on this computer and only downloads its own picture and speech models the first time.
- If a step fails, say what happened in one plain sentence, try the next fix below, and keep going. Show them error text only if they ask.

Where things go, so you can tell them if they ask:

| What | Mac | Windows |
|---|---|---|
| The app (about 1 GB) | `~/Find My Clip` | `%USERPROFILE%\Find My Clip` |
| Picture and speech models (about 2 GB, first use) | `~/.cache/huggingface` | `%USERPROFILE%\.cache\huggingface` |
| The app's notes about each folder | `~/Library/Application Support/Find My Clip` | `%APPDATA%\Find My Clip` |
| Their tidy folders | `~/Pictures/Find My Clip` | `%USERPROFILE%\Pictures\Find My Clip` |
| Videos they make | `~/Movies/Find My Clip` | `%USERPROFILE%\Videos\Find My Clip` |

### 1. Check the computer

Find out whether this is a Mac or Windows, then check for Python 3.10 or newer and for ffmpeg, which the app uses for video (`ffmpeg -version`).

**Python on a Mac:** `python3 --version` often shows Apple's own Python 3.9, even when a newer one is installed. Before installing anything, look for a newer one: try `/opt/homebrew/bin/python3`, `/usr/local/bin/python3`, then `python3.14`, `python3.13`, `python3.12`, `python3.11` and `python3.10`. Use the newest you find by its full path in step 3. Only if none is 3.10 or newer, ask, then install Python.

**Python on Windows:** run `py --list`. If it lists 3.10 or newer, use that version in step 3, for example `py -3.12`.

To install what's missing, after asking:

- **Mac:** `brew install python ffmpeg`, then use `/opt/homebrew/bin/python3` (or `/usr/local/bin/python3` on older Intel Macs). If Homebrew is missing, install it from https://brew.sh. That step asks for their Mac password, so tell them before it does.
- **Windows:** `winget install Python.Python.3.12` and `winget install Gyan.FFmpeg`. New programs are only found in a new window, so ask them to close and reopen Claude, then carry on from step 2.

### 2. Download the app

Download the zip from **https://github.com/Flospire-creator/find-my-clip/archive/refs/heads/main.zip** into their Downloads folder, for example with `curl -L -o find-my-clip.zip <link>`. Unzip it in their home folder. It makes a folder called `find-my-clip-main`: rename that to `Find My Clip`, so the app lives at `~/Find My Clip` (or `%USERPROFILE%\Find My Clip`). Check that `requirements.txt` sits directly inside it, then delete the downloaded zip.

If a `Find My Clip` folder is already there, this is a reinstall: ask whether to replace it. Their tidy folders live in Pictures, so replacing the app leaves them safe.

Use the home folder, because Desktop and Documents are often synced to iCloud or OneDrive, and syncing the app's thousands of small files slows the whole computer down.

### 3. Install what it needs

In the `Find My Clip` folder, with the Python you found in step 1:

- **Mac:** `/opt/homebrew/bin/python3 -m venv .venv` (or your Python's full path), then `.venv/bin/pip install -r requirements.txt`
- **Windows:** `py -3.12 -m venv .venv` (or the version you found), then `.venv\Scripts\python -m pip install -r requirements.txt`

Tell them this installs about 1 GB and can take 5 to 15 minutes, depending on their internet. If a package fails, upgrade pip inside the app's own Python and try again: `.venv/bin/python -m pip install --upgrade pip` on a Mac, `.venv\Scripts\python -m pip install --upgrade pip` on Windows.

### 4. Open it

- **Mac:** `.venv/bin/python app.py`
- **Windows:** `.venv\Scripts\python app.py`

Run it in the background, so the chat stays free. It opens in their web browser and prints the address, usually http://localhost:8770. If that address is busy, it picks the next free one and prints that instead. The window also shows a red line saying it's a development server, which is normal for an app that runs only on their own computer. Its "Keep this window open" line is meant for the Desktop shortcut in step 6, so you can leave it out while it runs in the background.

The first time they tidy a folder, the app downloads its picture and speech models, about 2 GB, so the first folder takes a little longer.

### 5. Show them how to use it

Tell them, in their words:

1. Press **Choose a folder** and pick a folder of photos and videos. On Windows, open **Or paste a folder path** and paste the folder's address instead.
2. Press **Tidy up this folder**. They can watch the photos fly into their folders, and the new folders are made as soon as it finishes.
3. When it says **All tidied up**, press **View organised folders**. The tidy copies are in their Pictures folder, under `Find My Clip`, in a folder named after the one they tidied, for example `Holiday sorted`, with Photos and Videos inside.
4. Extra copies of the same picture stay out of the new folders, and they can see them under **Duplicate media**. Files the app can't read, like PDFs, are left as they are.
5. **Find the moment** searches by what's in a photo or what's said in a video, for example "the beach at sunset" or "the bit where I said we won".
6. **Make a video** turns the clips they pick into a first draft they can finish in any editor.

On a Mac, **Try it with sample files** makes about 20 practice files from pictures that come with macOS, which is a nice way to see it work before using their own folder.

### 6. Make it easy to open next time

Offer to put a **Find My Clip** shortcut on their Desktop that they can double-click:

- **Mac:** a file called `Find My Clip.command` containing `cd ~/"Find My Clip" && .venv/bin/python app.py`, made runnable with `chmod +x`. If macOS asks them to confirm it the first time, they right-click it and choose Open.
- **Windows:** a file called `Find My Clip.bat` containing these two lines, so any message stays on screen:
  ```
  cd /d "%USERPROFILE%\Find My Clip" && .venv\Scripts\python app.py
  pause
  ```

Then stop the copy you started in the background in step 4, so the shortcut starts fresh. Finish by telling them, in one or two sentences, that they double-click the shortcut to open Find My Clip, and that closing its window stops it.
