import os
import json
import secrets
from datetime import datetime, timedelta
from typing import Optional
import pytz
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import google.generativeai as genai

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Config ───────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
APP_PASSWORD    = os.environ.get("APP_PASSWORD", "focus123")
SESSION_HOURS   = 6

# ─── MASTER PROMPT ────────────────────────────────────────────────────────────
# Paste your full school schedule and any custom AI instructions here.
# This is injected into every Gemini request as the authoritative context.
MASTER_PROMPT = """
You are a highly efficient personal scheduler. Your job is to manage a rolling daily schedule for a high school student in EST (Eastern Standard Time).

MASTER DATA
[
School - 8:00-14:20 Mon - Fri
Cello Lession - 17:00-17:45 Mon
Gym - 15:30-17:30 - Tue, Thrus
Korean School - 9:30-12:30 - Sat
Saturday Fellowship - 18:30-22:00
Church - 11:30-12:30 Sun
]

CORE RULES
1. OUTPUT FORMAT: Respond ONLY with a valid JSON array of objects. No prose, no "Here is your schedule."
   Format: [{"task": "String", "start": "HH:MM", "end": "HH:MM"}]
2. TIME: Use a 24-hour clock (00:00 to 23:59).
3. NO OVERLAPS: Tasks must be strictly sequential. If a new task is added that conflicts, you must adjust or shorten other flexible tasks (like "Free Time" or "Study") to fit it.
4. ORDERING: Always sort the array by start time.
5. LOGIC: If the user provides a "Task Dump," integrate those tasks into the existing Master Schedule.

BEHAVIOR
- The user might finish early or late or might get distracted. If this is the case try and prioitize things that is important and put things that doesn't need to be done to the othe side.
"""

# ─── In-memory session store ──────────────────────────────────────────────────
sessions: dict[str, datetime] = {}
rolling_log: list[str] = []  # stores recent schedule update messages


# ─── Auth helpers ─────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    password: str

class UpdateRequest(BaseModel):
    token: str
    command: str
    current_schedule: list  # current schedule from client

def validate_token(token: str) -> bool:
    if token not in sessions:
        return False
    if datetime.utcnow() - sessions[token] > timedelta(hours=SESSION_HOURS):
        del sessions[token]
        return False
    return True


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.post("/api/login")
async def login(req: LoginRequest):
    if req.password != APP_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid password")
    token = secrets.token_hex(32)
    sessions[token] = datetime.utcnow()
    return {"token": token, "expires_in": SESSION_HOURS * 3600}


@app.post("/api/update-schedule")
async def update_schedule(req: UpdateRequest):
    if not validate_token(req.token):
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY not configured")

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-3-flash-preview")

    # Build rolling log context
    rolling_log.append(f"User command: {req.command}")
    if len(rolling_log) > 20:
        rolling_log.pop(0)

    est = pytz.timezone("America/New_York")
    now_est = datetime.now(est)
    current_time_str = now_est.strftime("%A, %B %d %Y — %I:%M %p EST")
    current_schedule_str = json.dumps(req.current_schedule, indent=2)
    log_str = "\n".join(rolling_log[-10:])  # last 10 entries

    prompt = f"""{MASTER_PROMPT}

--- CURRENT TIME & DATE ---
{current_time_str}

--- CURRENT SCHEDULE ---
{current_schedule_str}

--- RECENT COMMAND LOG ---
{log_str}

--- USER'S LATEST COMMAND ---
{req.command}

Apply the user's command to the schedule. Return ONLY the updated JSON array.
"""

    try:
        response = model.generate_content(prompt)
        raw = response.text.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        schedule = json.loads(raw)

        # Validate structure
        for item in schedule:
            assert "task" in item and "start" in item and "end" in item

        return {"schedule": schedule, "log": rolling_log[-5:]}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI error: {str(e)}")


@app.get("/api/time")
async def get_time():
    """Returns current time in EST for the UI."""
    est = pytz.timezone("America/New_York")
    now = datetime.now(est)
    return {
        "time": now.strftime("%H:%M:%S"),
        "date": now.strftime("%A, %B %d %Y"),
        "time_24": now.strftime("%H:%M"),
    }


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Serve static files
app.mount("/", StaticFiles(directory="static", html=True), name="static")
