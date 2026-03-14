import os
import json
import re
import secrets
import httpx
import asyncio
from datetime import datetime, timedelta
import pytz
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GROQ_API_KEY  = os.environ.get("GROQ_API_KEY", "").strip().strip("'").strip('"')
APP_PASSWORD  = os.environ.get("APP_PASSWORD", "focus123")
SESSION_HOURS = 6
UPSTASH_URL   = os.environ.get("UPSTASH_REDIS_REST_URL", "").strip().strip("'").strip('"').rstrip("/")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip().strip("'").strip('"')

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

async def kv_get(key: str):
    if not UPSTASH_URL:
        return None
    async with httpx.AsyncClient(trust_env=False) as client:
        r = await client.get(
            f"{UPSTASH_URL}/get/{key}",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            timeout=5,
        )
        data = r.json()
        return data.get("result")

async def kv_set(key: str, value: str):
    if not UPSTASH_URL:
        return
    async with httpx.AsyncClient(trust_env=False) as client:
        await client.post(
            f"{UPSTASH_URL}/pipeline",
            headers={
                "Authorization": f"Bearer {UPSTASH_TOKEN}",
                "Content-Type": "application/json",
            },
            json=[["SET", key, value]],
            timeout=5,
        )

MASTER_PROMPT = """
You are the backend JSON engine for a dynamic high school scheduling app.
Your ONLY job is to take the user's CURRENT SCHEDULE, apply their NEW COMMAND, and output the ENTIRE updated schedule.

TODAY'S CONTEXT IS PROVIDED IN EVERY REQUEST. Use the CURRENT TIME and TODAY'S DATE KEY to understand how many days you have before each deadline.

DEADLINE INTERPRETATION — READ CAREFULLY:
- "Due Monday March 16" for a TEST means: the test is ON Monday. Study in the days BEFORE (Thu/Fri/Sat/Sun).
- "Due Monday March 16" for HOMEWORK means: submit on Monday. Do the work 1-2 days before.
- NEVER schedule prep work ON the due date itself.
- NEVER repeat the same task across many days unless explicitly told to.
- Each task should appear ONCE (or split into 2-3 focused sessions max).
- Estimate realistic time: homework = 30-45min, studying = 45-60min per session, big project = 2-3 sessions of 45min spread over multiple days.

RULES:
1. You MUST apply the user's command. If they say "add math at 5", you MUST add math at 17:00.
2. If a new task overlaps with an existing flexible task, SHIFT or RESCHEDULE the flexible task.
3. You MUST return the FULL list of all tasks for all days.
4. DEADLINES: When the user says something is "due" on a date, that means it must be COMPLETED BEFORE that day — not on that day. Schedule the work in the days LEADING UP to the due date. For example, if Bio test is due/on Monday, study for it on Thursday, Friday, Saturday, and Sunday BEFORE Monday. The due date itself should only have the actual event (like the test) if it's a test, or nothing if it's a submission.
5. SPREAD THE WORK: Never put all assignments on the same day. Distribute them across available evenings leading up to each deadline. A 45-min task should be one session. A big project should be split into multiple sessions across multiple days.
6. TODAY vs DUE DATE: If something is due tomorrow, work on it today. If due in 5 days, spread it across the next 3-4 days.
7. Never ignore a command. Never refuse. Just do it.

FIXED WEEKLY EVENTS (cannot move):
- School: 08:00-14:20, Mon-Fri
- Cello Lesson: 17:00-17:45, Monday
- Gym: 15:30-17:30, Tuesday & Thursday
- Robotics: 14:30-17:15, Friday
- Korean School: 09:30-12:30, Saturday
- Saturday Fellowship: 18:30-22:00, Saturday
- Church: 11:30-12:30, Sunday

SLEEP: Target 23:00, hard limit 01:00. Nothing after 01:00.
WEEKENDS: Keep light. Front-load work on weekdays.
FREE TIME: Always leave some at end of day AFTER all tasks are placed.

OUTPUT FORMAT - NON-NEGOTIABLE:
- Respond with ONLY a raw JSON array. No text before or after. No markdown.
- Format: [{"task": "Name", "start": "HH:MM", "end": "HH:MM", "date": "YYYY-MM-DD"}]
- 24-hour time. No overlaps within a day. Sorted by date then start time.
"""

class LoginRequest(BaseModel):
    password: str

class UpdateRequest(BaseModel):
    token: str
    command: str
    current_schedule: list

async def validate_token(token: str) -> bool:
    if not token:
        return False
    data = await kv_get(f"session:{token}")
    if not data:
        return False
    try:
        exp = datetime.fromisoformat(data)
        return datetime.utcnow() < exp
    except Exception:
        return False

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "groq_key_set": bool(GROQ_API_KEY),
        "upstash_set": bool(UPSTASH_URL),
    }
# ── Clear command ──────────────────────────────────────────────────────
    if req.command.strip().lower() == "clear":
        await kv_set("focus_schedule", json.dumps([]))
        return {"schedule": []}
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
    data = await kv_get("focus_schedule")
    if data:
        return json.loads(data)
    return []

@app.post("/api/schedule")
async def save_schedule(request: Request):
    schedule_data = await request.json()
    await kv_set("focus_schedule", json.dumps(schedule_data))
    return {"success": True}

@app.post("/api/update-schedule")
async def update_schedule(req: UpdateRequest):
    if not await validate_token(req.token):
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    if not GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY not configured")

    est = pytz.timezone("America/New_York")
    now_est = datetime.now(est)
    current_time_str = now_est.strftime("%A, %B %d %Y - %H:%M EST")
    today_str = now_est.strftime("%Y-%m-%d")

    user_prompt = f"""CURRENT TIME: {current_time_str}
TODAY'S DATE KEY: {today_str}

--- CURRENT SCHEDULE ---
{json.dumps(req.current_schedule, indent=2)}

--- NEW USER COMMAND ---
{req.command}

Output the updated JSON array now."""

    try:
        async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
            response = await client.post(
                GROQ_URL,
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": MASTER_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": 2000,
                    "temperature": 0.7,
                    "stream": False,
                },
            )
        data = response.json()
        if "error" in data:
            raise Exception(data["error"].get("message", str(data["error"])))
        raw = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Groq API Error: {str(e)}")

    try:
        start_idx = raw.find("[")
        end_idx = raw.rfind("]")
        if start_idx == -1 or end_idx == -1:
            raise Exception(f"No JSON array in response. Raw: {raw[:150]}")
        raw = raw[start_idx:end_idx + 1]
        raw = re.sub(r',\s*]', ']', raw)
        raw = re.sub(r',\s*}', '}', raw)
        schedule = json.loads(raw)
        for item in schedule:
            if "date" not in item:
                item["date"] = today_str
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"JSON Parse Error: {str(e)}")

    await kv_set("focus_schedule", json.dumps(schedule))
    return {"schedule": schedule}

app.mount("/", StaticFiles(directory="static", html=True), name="static")
