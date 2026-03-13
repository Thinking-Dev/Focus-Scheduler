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

MASTER_PROMPT = """
You are a highly efficient personal scheduler managing a MULTI-DAY schedule for a high school student in EST.

FIXED WEEKLY SCHEDULE
- School: 08:00-14:20, Mon-Fri
- Cello Lesson: 17:00-17:45, Monday
- Gym: 15:30-17:30, Tuesday & Thursday
- Robotics: 14:30-17:15, Friday
- Korean School: 09:30-12:30, Saturday
- Saturday Fellowship: 18:30-22:00, Saturday
- Church: 11:30-12:30, Sunday

PERSONAL RULES
- Always leave free time at end of day for friends and fun.
- Target sleep at 23:00. Flexible up to 01:00 only for big assignments.
- Protect weekends, front-load work on weekdays.
- NEVER cram everything into one day. Spread work across days leading up to deadlines.
- Break multi-day tasks into small daily chunks.

OUTPUT FORMAT - CRITICAL
- Respond ONLY with a valid JSON array. Zero prose. No markdown. No explanation.
- Format: [{"task": "Name", "start": "HH:MM", "end": "HH:MM", "date": "YYYY-MM-DD"}]
- 24-hour time. No overlaps within a day. Sorted by date then start time.
"""

rolling_log: list[str] = []

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

    rolling_log.append(f"User: {req.command}")
    if len(rolling_log) > 20:
        rolling_log.pop(0)

    est = pytz.timezone("America/New_York")
    now_est = datetime.now(est)
    current_time_str = now_est.strftime("%A, %B %d %Y - %I:%M %p EST")
    today_str = now_est.strftime("%Y-%m-%d")
    current_schedule_str = json.dumps(req.current_schedule, indent=2)
    log_str = "\n".join(rolling_log[-10:])

    prompt = f"""{MASTER_PROMPT}

--- CURRENT TIME & DATE ---
{current_time_str}
Today's date key: {today_str}

--- CURRENT SCHEDULE ---
{current_schedule_str}

--- RECENT LOG ---
{log_str}

--- USER COMMAND ---
{req.command}

Return ONLY the updated JSON array.
"""

    async def stream_response():
        full_text = ""
        try:
            # Call Groq directly via HTTP to avoid any import issues
            async with httpx.AsyncClient(timeout=30) as client:
                async with client.stream(
                    "POST",
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 600,
                        "temperature": 0.4,
                        "stream": True,
                    },
                ) as response:
                    async for line in response.aiter_lines():
                        if line.startswith("data: ") and line != "data: [DONE]":
                            try:
                                chunk = json.loads(line[6:])
                                content = chunk["choices"][0]["delta"].get("content", "")
                                if content:
                                    full_text += content
                                    yield " "
                            except Exception:
                                pass

            raw = full_text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            schedule = json.loads(raw)
            for item in schedule:
                assert "task" in item and "start" in item and "end" in item

            for item in schedule:
                if "date" not in item:
                    item["date"] = today_str

            await kv_set("focus_schedule", json.dumps(schedule))
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

app.mount("/", StaticFiles(directory="static", html=True), name="static")
