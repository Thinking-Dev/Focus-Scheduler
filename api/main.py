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

def get_clean_url():
    url = os.environ.get("UPSTASH_REDIS_REST_URL", "")
    url = url.strip().strip("'").strip('"')
    if url and not url.startswith("http"):
        url = f"https://{url}"
    if url.endswith("/"):
        url = url[:-1]
    return url

GROQ_API_KEY  = os.environ.get("GROQ_API_KEY", "").strip().strip("'").strip('"')
APP_PASSWORD  = os.environ.get("APP_PASSWORD", "focus123")
SESSION_HOURS = 6
UPSTASH_URL   = get_clean_url()
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip().strip("'").strip('"')

async def kv_get(key: str):
    if not UPSTASH_URL: return None
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{UPSTASH_URL}/get/{key}",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            timeout=5,
        )
        data = r.json()
        return data.get("result")

async def kv_set(key: str, value: str):
    if not UPSTASH_URL: return
    async with httpx.AsyncClient() as client:
        # We explicitly add /pipeline to ensure httpx doesn't fail on a bare domain
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
    if not token: return False
    data = await kv_get(f"session:{token}")
    if not data: return False
    try:
        exp = datetime.fromisoformat(data)
        return datetime.utcnow() < exp
    except Exception:
        return False

@app.get("/api/health")
async def health():
    return {"status": "ok", "groq_key_set": bool(GROQ_API_KEY), "upstash_set": bool(UPSTASH_URL)}

# ── RESTORED: /api/time (Fixes the 404 Error) ─────────────────────────────
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
    if data: return json.loads(data)
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
    
    current_schedule_str = json.dumps(req.current_schedule, indent=2)

    user_prompt = f"CURRENT TIME: {current_time_str}\nTODAY'S DATE KEY: {today_str}\n\n--- CURRENT SCHEDULE ---\n{current_schedule_str}\n\n--- NEW USER COMMAND ---\n{req.command}\n\nACTION REQUIRED: Output the newly updated JSON array reflecting this command."

    # ── Error Pinpointing ──────────────────────────────────────────────────
    try:
        # Step 1: Talk to Groq AI
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
                    "temperature": 0.2,
                    "stream": False,
                },
            )
            response.raise_for_status()
            data = response.json()
            raw = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Groq API Error: {str(e)}")

    try:
        # Step 2: Clean the JSON
        start_idx = raw.find("[")
        end_idx = raw.rfind("]")
        if start_idx == -1 or end_idx == -1:
            raise Exception(f"AI did not return a valid JSON array. Raw output: {raw[:100]}")
        
        raw = raw[start_idx:end_idx+1]
        raw = re.sub(r',\s*]', ']', raw)
        raw = re.sub(r',\s*}', '}', raw)
        schedule = json.loads(raw)
        
        for item in schedule:
            if "date" not in item:
                item["date"] = today_str
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"JSON Parse Error: {str(e)}")

    try:
        # Step 3: Save back to Upstash
        await kv_set("focus_schedule", json.dumps(schedule))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upstash Save Error: {str(e)}")

    return {"schedule": schedule}

app.mount("/", StaticFiles(directory="static", html=True), name="static")
