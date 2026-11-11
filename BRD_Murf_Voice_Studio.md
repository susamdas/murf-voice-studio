# BRD: Murf Voice Studio (Local Voiceover Generator)

**Version:** 1.0
**Date:** 11 November 2026
**Purpose:** Generate Bangla/English voiceovers with the Murf API, choose different voices (and different API keys), preview, and save the audio files on the local PC.

---

## 1. Background

We make short animated ads (Canva AI video). Voiceovers are made with the Murf API. Today this is done with a one-off Python script where the voice, text and API key are hard-coded. We need a simple local tool where we can change the voice, write the script lines, and save the MP3s to a folder without editing code.

## 2. Goals

| # | Goal |
|---|---|
| G1 | Change voice from a list, without editing code |
| G2 | Support more than one Murf API key (one per account/project) and choose which key each voice profile uses |
| G3 | Generate one audio file per script line (shot1, shot2, …) so files match video clips |
| G4 | Preview audio in the app, then save to a local folder |
| G5 | Keep API keys safe: never in code, never in Git, never shown in the UI |

## 3. Users

- Single user on their own Windows PC (content/communications staff).
- Not a technical expert: the tool must be simple, with clear buttons and error messages.

## 4. Scope

**In scope**
- Local web app that runs on the user's PC (opens in the browser at `http://localhost`).
- Voice profiles, API key selection, script input, generate, preview, save, history.

**Out of scope (for now)**
- Online hosting, user login, multiple users.
- Video editing or uploading to Canva.

## 5. Functional requirements

### FR1: API keys (multiple)
- Keys are stored only in a local `.env` file, for example:
  ```
  MURF_KEY_MAIN=xxxx
  MURF_KEY_PROJECT2=yyyy
  ```
- The app reads every variable that starts with `MURF_KEY_` and shows only the **name** (e.g. "MAIN", "PROJECT2") in a dropdown, never the value.
- `.env` must be listed in `.gitignore`.
- Provide `.env.example` with placeholder values only.

### FR2: Voice profiles
- Profiles are stored in `voices.json`. Each profile has:

| Field | Example | Notes |
|---|---|---|
| `name` | "Rina – Bangla female" | Shown in dropdown |
| `voice_id` | "Anisha" | Murf voice ID |
| `style` | "Conversational" | Murf style |
| `locale` | "bn-IN" | Language/locale |
| `model` | "falcon-2.1" | Murf model |
| `api_key_name` | "MAIN" | Which `MURF_KEY_` to use |
| `format` | "MP3" | MP3 or WAV |
| `sample_rate` | 24000 | |

- The user can **add, edit, duplicate and delete** profiles from the UI (saved back to `voices.json`).
- Optional: a "Load voices from Murf" button that calls Murf's list-voices endpoint (check current Murf API docs) to fill voice ID, styles and locales automatically.

### FR3: Script input
- A text box where each **line = one audio file**.
- Optional file name per line using the format `shot1 | text here`. If no name is given, use `line01`, `line02`, …
- Support Bangla (UTF-8) text fully.

### FR4: Generate
- Button: **Generate all**, and a **Generate** button per line.
- Call `POST https://global.api.murf.ai/v1/speech/stream` with the selected profile's settings and API key (header `api-key`).
- Show progress for each line (waiting / done / error).
- On error, show Murf's message in plain words (e.g. "Invalid API key", "Out of credits", "Voice not available for this locale").

### FR5: Preview and save
- Each generated line has an audio player to listen before saving.
- Files are saved to: `output/<project name>/<voice profile name>/<file name>.mp3`
- Never overwrite silently: if the file exists, add `_v2`, `_v3`, …
- Button: **Open output folder**.
- Option: **Merge all lines into one file** with a short pause (e.g. 0.5 s) between lines.

### FR6: Compare voices (nice to have)
- Generate the same line with 2–3 different profiles side by side, to choose the best voice.

### FR7: History
- Keep a simple log (`history.csv`): date/time, project, profile, file name, text, status.
- Do **not** log API keys.

## 6. Non-functional requirements

| Area | Requirement |
|---|---|
| Platform | Windows 10/11, Python 3.10+ |
| Tech | Python + Flask (or Streamlit) backend, simple HTML UI, `requests`, `python-dotenv` |
| Ease of use | Start with one command or a `start.bat` double-click |
| Security | Keys only in `.env`; never sent to the browser, logged or printed |
| Reliability | 60-second timeout per request; retry once on network error |
| Language | UI labels in English; full Bangla text support |

## 7. Folder structure

```
murf-voice-studio/
├─ app.py
├─ murf_client.py
├─ voices.json
├─ .env            (private, not in Git)
├─ .env.example
├─ .gitignore
├─ requirements.txt
├─ start.bat
├─ templates/
│  └─ index.html
├─ output/
└─ README.md
```

## 8. Acceptance criteria

1. With a valid key in `.env`, the user selects a profile, pastes 5 Bangla lines, clicks **Generate all**, and gets 5 playable MP3 files in the output folder.
2. Changing the profile dropdown changes the voice with no code edits.
3. Two profiles using two different key names both work.
4. A wrong API key shows a clear error and the app does not crash.
5. Searching the project files and logs for the key value finds nothing outside `.env`.
6. `README.md` explains setup in simple steps.

## 9. Sample data

`voices.json` starting profile:
```json
[
  {
    "name": "Rina – Bangla female",
    "voice_id": "Anisha",
    "style": "Conversational",
    "locale": "bn-IN",
    "model": "falcon-2.1",
    "api_key_name": "MAIN",
    "format": "MP3",
    "sample_rate": 24000
  }
]
```

Sample script:
```
shot1 | স্বপ্ন ছিল, কিন্তু পুঁজি ছিল না।
shot2 | গ্রামীণ ব্যাংক এল আমার দোরগোড়ায়।
shot3 | জামানত ছাড়াই ঋণ, এখন আমার নিজের দোকান।
shot4 | আমার সন্তানরা এখন স্কুলে যায়।
shot5 | গ্রামীণ ব্যাংক — সবার জন্য সুযোগ।
```
