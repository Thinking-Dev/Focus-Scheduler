import os
import json
import re
import secrets
import httpx
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

GROQ_API_KEY  = os.environ.get("GROQ_API_KEY", "")
APP_PASSWORD  = os.environ.get("APP_PASSWORD", "focus123")
SESSION_HOURS = 6
UPSTASH_URL   = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

# Automatically fix the Upstash URL if the https:// is missing in Vercel
if UPSTASH_URL and not UPSTASH_URL.startswith("http"):
    UPSTASH_URL = f"https://{UPSTASH_URL}"

async def kv_get(key: str):
    if not UPSTASH_URL:
        return None
    async with httpx.AsyncClient() as client:
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

# ── AI Prompt completely rewritten for aggressive JSON modification ───────
MASTER_PROMPT = """
You are the backend JSON engine for a dynamic high school scheduling app.
Your ONLY job is to take the user's CURRENT SCHEDULE, apply their NEW COMMAND, and output the ENTIRE updated schedule.

RULES:
1. You MUST apply the user's command. If they say "add math at 5", you MUST add math at 17:00.
2. If a new task overlaps with an existing flexible task, SHIFT or RESCHEDULE the flexible task. Do not delete it unless asked.
3. You MUST return the FULL list of all tasks. Do not just return the single new task.

FIXED WEEKLY EVENTS (Do not overwrite these unless explicitly told to):
- School: 08:00-14:20, Mon-Fri
- Cello Lesson: 17:00-17:45, Monday
- Gym: 15:30-17:30, Tuesday & Thursday
- Robotics: 14:30-17:15, Friday
- Korean School: 09:30-12:30, Saturday
- Saturday Fellowship: 18:30-22:00, Saturday
- Church: 11:30-12:30, Sunday

OUTPUT FORMAT:
- Respond with ONLY a raw JSON array. No conversational text whatsoever.
- Do NOT wrap the response in markdown blocks like ```json.
- Format: [{"task": "Task Name", "start": "HH:MM", "end": "HH:MM", "date": "YYYY-MM-DD"}]
- Use 24-hour time. Sorted chronologically.
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
    return {"status": "ok", "groq_key_set": bool(GROQ_API_KEY), "upstash_set": bool(UPSTASH_URL)}

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
    
    # We pass the current schedule cleanly to the AI
    current_schedule_str = json.dumps(req.current_schedule, indent=2)

    user_prompt = f"""
CURRENT TIME: {current_time_str}
TODAY'S DATE KEY: {today_str}

--- CURRENT SCHEDULE ---
{current_schedule_str}

--- NEW USER COMMAND ---
{req.command}

ACTION REQUIRED: Output the newly updated JSON array reflecting this command.
"""

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "[https://api.groq.com/openai/v1/chat/completions](https://api.groq.com/openai/v1/chat/completions)",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": MASTER_PROMPT},
                        {"role": "user", "content": user_prompt}
                    ],
                    "max_tokens": 2000,
                    "temperature": 0.2, # Bumped slightly so it doesn't freeze up
                    "stream": False,
                },
            )
            
            response.raise_for_status()
            data = response.json()
            raw = data["choices"][0]["message"]["content"].strip()

        # Bulletproof JSON extraction
        start_idx = raw.find("[")
        end_idx = raw.rfind("]")
        if start_idx == -1 or end_idx == -1:
            raise Exception(f"AI did not return a valid JSON array. Raw output: {raw[:200]}")
        
        raw = raw[start_idx:end_idx+1]

        # Fix any trailing commas Llama might have hallucinaton
        raw = re.sub(r',\s*]', ']', raw)
        raw = re.sub(r',\s*}', '}', raw)

        schedule = json.loads(raw)
        
        # Ensure every item has a date
        for item in schedule:
            if "date" not in item:
                item["date"] = today_str

        # Save to database
        await kv_set("focus_schedule", json.dumps(schedule))
        
        return {
            "schedule": schedule
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

app.mount("/", StaticFiles(directory="static", html=True), name="static")
