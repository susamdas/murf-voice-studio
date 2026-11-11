# Murf Voice Studio

A small app that runs on your own PC. Use it to turn Bangla or English script lines into voiceover files with Murf, one file per video shot. You can listen to each file before you keep it.

---

## 1. One-time setup

1. **Install Python** (version 3.10 or newer) from https://www.python.org/downloads/.
   During setup, tick **"Add python.exe to PATH"**.
2. **Add your Murf API key.**
   - Make a copy of `.env.example` and name it `.env`. If you skip this, `start.bat` makes the copy for you the first time it runs.
   - Open `.env` in Notepad and paste your key after the `=` sign:
     ```
     MURF_KEY_MAIN=your_real_key_here
     ```
   - If you have more Murf accounts, add one line for each. The part after `MURF_KEY_` is the name you will see in the app:
     ```
     MURF_KEY_PROJECT2=another_key_here
     ```
   - Save the file. **Never share `.env` or email it to anyone.**

## 2. Start the app

Double-click **`start.bat`**.

- The first start takes a minute while it installs what it needs.
- Your browser opens **http://localhost:5050**. If it doesn't, type that address yourself.
- Keep the black window open while you work. Close it to stop the app.

## 3. Make voiceovers

1. **Project and voice:** type a project name and choose a voice profile.
2. **Script:** write one line per audio file, in the form `name | seconds | text`. The name and the seconds are both optional:
   ```
   shot1 | 5 | স্বপ্ন ছিল, কিন্তু পুঁজি ছিল না।      ← file shot1, target 5 seconds
   shot2 | গ্রামীণ ব্যাংক এল আমার দোরগোড়ায়।        ← file shot2, no target
   আমার সন্তানরা এখন স্কুলে যায়।                   ← file line03, no target
   ```
   Lines without a name are saved as `line01`, `line02`, and so on. The seconds can be written `4.5`, `4,5` or `4.5 sec`.
3. **Generate:** click **Generate all**, or **Generate** on one line. Press play to listen. Each line shows its length, for example **1.9 sec**.
   - With **"Save to folder automatically"** ticked, every file is saved as soon as it is ready.
   - Untick it if you want to listen first. Then click **Save** on the lines you like, or **Save all**.
4. Click **Open output folder** to see your files. They are saved like this:
   ```
   output/<project name>/<voice profile name>/shot1.mp3
   ```
   The app never replaces a file you already have. If `shot1.mp3` exists, the new one is saved as `shot1_v2.mp3`.
5. **Merge all into one file** joins the generated lines into one file, with a short pause between them (0.5 seconds unless you change it).

### Speed and pitch
- Every voice profile has a **default speed** and **default pitch**, saved in `voices.json`.
- Each line also has its own **Speed** and **Pitch** sliders. Moving one overrides the profile for that line only. Click **reset** to go back to the profile value.
- Both run from **−50 to +50**, which is Murf's allowed range. 0 is normal; for speed, a negative number is slower and a positive number is faster.

### Fit to video length (Auto-fit)
Tick **Auto-fit to video length**. Then, for every line that has a target in seconds:

1. The app generates the line and measures how long it is.
2. If it is too long or much too short, it changes the speed and generates it again, up to **3 tries in total**.
   It only uses speeds between **−20 and +20**, so the voice stays natural.
3. If the speech is shorter than the target, silence is added: **0.5 s at the start and the rest at the end**. The file is then exactly the target length. For MP3 this is accurate to about ±0.012 s, because MP3 is made of 24 ms blocks; WAV is exact.
4. If the line is still too long at the fastest natural speed, you see:
   *"Line too long for 5 sec – shorten the text or make the clip longer."*

The **Fit results** table shows each line's target, final length, speech length, the speed used, the number of tries and the status. Hover over a speed to see every try.

> Each try is a separate Murf generation and uses credits. Lines that already fit use only one try.

**Changing the limits:** the natural speed range (−20 to +20) and the number of tries (3) are set at the top of `autofit.py` (`NATURAL_RATE_MIN`, `NATURAL_RATE_MAX`, `MAX_TRIES`).

### Silence before / after
Each line has **Silence before** and **Silence after** boxes, in seconds:
- With Auto-fit **off**, this silence is simply added to the start and end of the file.
- With Auto-fit **on**, the speech is fitted into the time that is left. "Silence before" replaces the standard 0.5 s at the start.

**Merge all into one file** joins the final files of each line, including any fitting and silence.

### Compare voices
Open **Compare voices**, type one line and pick 2 or 3 profiles. Listen to them side by side and save the one you like.

### Voice profiles
Click **Manage voice profiles** to add, edit, duplicate or delete profiles. Your changes are saved in `voices.json`.
- **API key name** sets which key from `.env` the profile uses, for example `MAIN`.
- **Load voices from Murf** gets the list of voices your key can use. Type `bn` in the filter to see the Bangla voices, then pick one. The app fills in the voice ID, locale and style for you.

### History
Every generate, save and error is written to `history.csv`, which opens in Excel. API keys are never written there.

---

## Common errors

| Message | What to do |
|---|---|
| *API key 'MAIN' was not found* | Add `MURF_KEY_MAIN=...` to `.env`, save it, and refresh the page. |
| *Invalid API key* | The key in `.env` is wrong or expired. Copy it again from your Murf account. |
| *Out of credits* | Top up the Murf account, or switch to a profile that uses another key. |
| *Voice not available with these settings* | The voice, locale or style don't go together. Use **Load voices from Murf** to pick a valid set. |
| *Line too long for X sec* | Even at the fastest natural speed the line doesn't fit. Shorten the text, make the video clip longer, or set a faster speed by hand. |
| *Silence before/after leaves no room for speech* | The silence boxes add up to almost the whole target. Make them smaller. |
| *Could not connect to Murf* | Check your internet connection. The app already tried twice. |
| *The app is not running* | The black window was closed. Double-click `start.bat` again. |

## Files

| File | Purpose |
|---|---|
| `app.py` | The web app (Flask) |
| `murf_client.py` | Talks to the Murf API. This is the only place that uses the key values. |
| `audio_tools.py` | Measures audio length, adds silence and merges clips (no extra software needed) |
| `autofit.py` | Fits a line to its target length (speed tries, silence) |
| `voices.json` | Your voice profiles |
| `.env` | Your private API keys (not in Git) |
| `.env.example` | Template for `.env`, with no real keys |
| `templates/index.html` | The page you see in the browser |
| `output/` | Your saved audio files |
| `history.csv` | Log of what was generated |

## Do I need ffmpeg?
**No.** Audio length is measured with the small Python library `mutagen`, which `start.bat` installs for you. Silence and merging are done by the app's own code, so ffmpeg and pydub aren't needed.

If you want ffmpeg anyway for other audio work, here's how to install it on Windows:
1. Open **PowerShell** and run: `winget install --id Gyan.FFmpeg -e`
2. Close and reopen PowerShell, then run `ffmpeg -version` to check that it works.

## Security notes
- Keys are only stored in `.env`, and `.gitignore` keeps that file out of Git.
- The browser only ever sees key *names* such as "MAIN", never the key itself.
- The app only listens on your own PC (`127.0.0.1`), so other computers can't open it.
