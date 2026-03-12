# Focus Scheduler

A minimalist AI-powered daily focus scheduler. Dark mode, glassmorphism UI, Gemini AI brain, FastAPI backend — deployable to Vercel in minutes.

---

## Project Structure

```
/
├── api/
│   └── main.py          # FastAPI backend (auth, Gemini, time)
├── static/
│   ├── index.html       # App shell + PWA manifest
│   ├── style.css        # Glassmorphism dark UI
│   └── script.js        # Clock, progress bar, AI logic
├── requirements.txt
├── vercel.json
└── README.md
```

---

## Setup & Deployment

### 1. Clone / fork this repo

```bash
git clone <your-repo-url>
cd focus-scheduler
```

### 2. Set Environment Variables in Vercel

In your Vercel project dashboard → **Settings → Environment Variables**, add:

| Variable | Value | Description |
|---|---|---|
| `GEMINI_API_KEY` | `AIza...` | Your Google Gemini API key |
| `APP_PASSWORD` | `your-password` | Password for the lock screen |

**Getting a Gemini API key:**
1. Go to [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
2. Click **Create API Key**
3. Copy the key and paste it into Vercel

### 3. Customize Your Master Prompt

Open `api/main.py` and find the `MASTER_PROMPT` variable near the top:

```python
MASTER_PROMPT = """
You are a scheduling assistant...

PASTE YOUR SCHOOL SCHEDULE AND CUSTOM INSTRUCTIONS HERE.
"""
```

Replace the placeholder with your **actual school schedule** and any **custom AI rules** you want Gemini to follow. For example:

```
My Schedule:
- Monday–Friday: School 8:00–14:45
  - Period 1: Math 8:00–8:50
  - Period 2: English 8:55–9:45
  ...
- I do homework from 15:30–18:00
- Dinner is always at 18:30

Rules:
- Never schedule tasks during dinner (18:30–19:00)
- Math homework should always be done before 17:00
- Protect 30 minutes of free time between school and homework
```

### 4. Deploy to Vercel

```bash
npm i -g vercel   # install Vercel CLI if needed
vercel            # follow prompts
```

Or simply push to a GitHub repo connected to Vercel for auto-deploy.

---

## Features

- **🔒 Password Lock** — session token stored in localStorage, expires after 6 hours
- **🕐 Live Clock** — synced to EST via backend, ticks every second
- **⚡ Now Filter** — only the task overlapping the current time is shown
- **📊 Progress Bar** — grows from 0→100% over the task's duration
- **🤖 AI Commands** — type natural language like "move math to 7pm" or "add gym at 5"
- **💾 Offline Fallback** — latest schedule cached in localStorage; works without internet
- **⏱ Rate Limiting** — 5-second cooldown between AI requests
- **📋 Full Schedule Modal** — "See full schedule" shows the day view with current task highlighted

---

## Local Development

```bash
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000
# open http://localhost:8000
```

---

## AI Command Examples

| Command | What it does |
|---|---|
| `Move math to 7pm` | Reschedules math homework to 19:00 |
| `Add 30 min break at 3pm` | Inserts a break 15:00–15:30 |
| `I finished math early` | Removes or shortens the math block |
| `Push everything after 5pm back 1 hour` | Shifts the evening schedule |
| `What's my afternoon look like?` | Gemini will explain (and return the same schedule) |

---

## Security Notes

- The `APP_PASSWORD` is **never sent to the client** — it lives only in Vercel's environment
- Session tokens are cryptographically random 64-character hex strings
- Sessions auto-expire after 6 hours; the UI forces re-login
- The Gemini API key is **server-side only** — never exposed in frontend code
