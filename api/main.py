import os
import json
import secrets
import httpx
from datetime import datetime, timedelta
import pytz
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from google import genai
from google.genai import types

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
APP_PASSWORD   = os.environ.get("APP_PASSWORD", "focus123")
SESSION_HOURS  = 6

# ─── Upstash Redis (REST-based, works on Vercel serverless) ───────────────────
UPSTASH_URL   = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

async def kv_get(key: str):
    """Read a key from Upstash Redis via REST API."""
    if not UPSTASH_URL:
        return None
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{UPSTASH_URL}/get/{key}",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            timeout=5,
        )
        data = r.json()
        return data.get("result")  # returns None if key doesn't exist

async def kv_set(key: str, value: str):
    """Write a key to Upstash Redis via REST API (Upstash REST format)."""
    if not UPSTASH_URL:
        return
    async with httpx.AsyncClient() as client:
        await client.post(
            UPSTASH_URL,
            headers={
                "Authorization": f"Bearer {UPSTASH_TOKEN}",
                "Content-Type": "application/json",
            },
            json=["SET", key, value],
            timeout=5,
        )

# ─── MASTER PROMPT ────────────────────────────────────────────────────────────
MASTER_PROMPT = """
You are a highly efficient personal scheduler. Your job is to manage a MULTI-DAY rolling schedule for a high school student in EST (Eastern Standard Time).

FIXED WEEKLY SCHEDULE
- School: 08:00–14:20, Mon–Fri
- Cello Lesson: 17:00–17:45, Monday
- Gym: 15:30–17:30, Tuesday & Thursday
- Robotics: 14:30–17:15, Friday
- Korean School: 09:30–12:30, Saturday
- Saturday Fellowship: 18:30–22:00, Saturday
- Church: 11:30–12:30, Sunday

PERSONAL RULES
- Always leave free time at the end of each day for friends and fun.
- Target sleep at 23:00. Flexible up to 01:00 only for big assignments — nothing after 01:00.
- Protect weekends: front-load hard work on weekdays so weekends feel lighter.
- NEVER cram everything into one day. If a deadline is Friday, spread studying across Wed/Thu/Fri — not all on Monday.
- For multi-day tasks (essays, test prep, projects): break them into small daily chunks leading up to the deadline.
- Prioritize urgent + important tasks first. Deprioritize low-stakes tasks if the day is already full.
- If the user finishes something early, compress or remove that block and give back free time.

OUTPUT FORMAT — CRITICAL
- Respond ONLY with a valid JSON array. Zero prose. No markdown. No explanation.
- Each item must have exactly: "task", "start", "end", and "date" fields.
- Format: [{"task": "Name", "start": "HH:MM", "end": "HH:MM", "date": "YYYY-MM-DD"}]
- 24-hour time. No overlaps within a day. Sorted by date then start time.
- When updating today's schedule, keep future days intact unless the user asks to change them.
"""

# ─── In-memory session store ──────────────────────────────────────────────────
async def validate_token(token: str) -> bool:
    if not token:
        return False
    data = await kv_get(f"session:{token}")
    if not data:
        return False
    exp = datetime.fromisoformat(data)
    if datetime.utcnow() > exp:
        await kv_set(f"session:{token}", "")  # clean up
        return False
    return True

# ─── Auth helpers ─────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    password: str

class UpdateRequest(BaseModel):
    token: str
    command: str
    current_schedule: list

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
    exp = datetime.utcnow() + timedelta(hours=SESSION_HOURS)
    await kv_set(f"session:{token}", exp.isoformat())
    return {"token": token, "expires_in": SESSION_HOURS * 3600}


@app.get("/api/schedule")
async def get_schedule():
    """Fetch the synced multi-day schedule from Upstash KV."""
    data = await kv_get("focus_schedule")
    if data:
        return json.loads(data)
    return []


@app.post("/api/schedule")
async def save_schedule(request: Request):
    """Manually save a schedule to Upstash KV."""
    schedule_data = await request.json()
    await kv_set("focus_schedule", json.dumps(schedule_data))
    return {"success": True}


@app.post("/api/update-schedule")
async def update_schedule(req: UpdateRequest):
    if not await validate_token(req.token):
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY not configured")

    client = genai.Client(api_key=GEMINI_API_KEY)

    rolling_log.append(f"User: {req.command}")
    if len(rolling_log) > 20:
        rolling_log.pop(0)

    est = pytz.timezone("America/New_York")
    now_est = datetime.now(est)
    current_time_str = now_est.strftime("%A, %B %d %Y — %I:%M %p EST")
    today_str = now_est.strftime("%Y-%m-%d")
    current_schedule_str = json.dumps(req.current_schedule, indent=2)
    log_str = "\n".join(rolling_log[-10:])

    prompt = f"""{MASTER_PROMPT}

--- CURRENT TIME & DATE ---
{current_time_str}
Today's date key: {today_str}

--- CURRENT MULTI-DAY SCHEDULE ---
{current_schedule_str}

--- RECENT COMMAND LOG ---
{log_str}

--- USER'S LATEST COMMAND ---
{req.command}

Think carefully about spreading work across multiple days if deadlines are mentioned.
Return ONLY the updated JSON array with all days included.
"""

    async def stream_response():
        """Stream the Gemini response, accumulate, validate, save, then return."""
        full_text = ""
        try:
            # Stream with low thinking + token cap to prevent timeouts
            async for chunk in await client.aio.models.generate_content_stream(
                model="gemini-2.0-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=600,
                    temperature=0.4,
                ),
            ):
                if chunk.text:
                    full_text += chunk.text
                    # Send heartbeat so Vercel doesn't kill the connection
                    yield " "

            # Clean up markdown fences
            raw = full_text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            schedule = json.loads(raw)
            for item in schedule:
                assert "task" in item and "start" in item and "end" in item

            # Backfill date = today for any items missing it (backward compat)
            for item in schedule:
                if "date" not in item:
                    item["date"] = today_str

            # Persist to Upstash
            await kv_set("focus_schedule", json.dumps(schedule))

            # Send the real payload at the end
            yield "\n__SCHEDULE__" + json.dumps({"schedule": schedule, "log": rolling_log[-5:]})

        except Exception as e:
            yield "\n__ERROR__" + json.dumps({"detail": str(e)})

    return StreamingResponse(stream_response(), media_type="text/plain")


@app.get("/api/time")
async def get_time():
    est = pytz.timezone("America/New_York")
    now = datetime.now(est)
    return {
        "time": now.strftime("%H:%M:%S"),
        "date": now.strftime("%A, %B %d %Y"),
        "time_24": now.strftime("%H:%M"),
        "date_key": now.strftime("%Y-%m-%d"),
    }


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Serve static files
app.mount("/", StaticFiles(directory="static", html=True), name="static")
