// ── Constants ──────────────────────────────────────────────────────────────
const SESSION_KEY   = 'focus_session_token';
const SESSION_EXP   = 'focus_session_exp';
const SCHEDULE_KEY  = 'focus_schedule';
const RATE_LIMIT_MS = 5000;

// ── State ──────────────────────────────────────────────────────────────────
let schedule   = [];
let serverTime = null;   // offset from server (ms)
let lastSubmit = 0;
let clockInterval, progressInterval;

// ── DOM refs ───────────────────────────────────────────────────────────────
const $  = id => document.getElementById(id);
const overlay      = $('password-overlay');
const passwordInput = $('password-input');
const unlockBtn    = $('unlock-btn');
const lockError    = $('lock-error');
const clockEl      = $('clock');
const dateEl       = $('date-display');
const taskEl       = $('current-task');
const taskTimeEl   = $('task-time-range');
const progressFill = $('progress-fill');
const progressPct  = $('progress-pct');
const progressLeft = $('progress-remaining');
const commandInput = $('command-input');
const submitBtn    = $('submit-btn');
const aiStatus     = $('ai-status');
const scheduleModal = $('schedule-modal');
const scheduleList = $('schedule-list');
const sessionIndicator = $('session-indicator');
const toast        = $('toast');

// ── Utility ────────────────────────────────────────────────────────────────
function showToast(msg, duration = 2800) {
  toast.textContent = msg;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), duration);
}

function timeToMinutes(hhmm) {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
}

function minutesToHHMM(mins) {
  const h = Math.floor(mins / 60) % 24;
  const m = mins % 60;
  return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`;
}

function formatDuration(minutes) {
  if (minutes <= 0)  return 'Done';
  if (minutes < 60)  return `${minutes}m left`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h}h ${m}m left` : `${h}h left`;
}

function autoGrowTextarea() {
  // 1. Briefly reset the height so it can shrink if the user deletes lines
  commandInput.style.height = 'auto';
  
  // 2. Set the new height based on its native scroll height (capped at 120px)
  commandInput.style.height = Math.min(commandInput.scrollHeight, 120) + 'px';
}

// Listen to input events
commandInput.addEventListener('input', autoGrowTextarea);

// Initial sizing
autoGrowTextarea();

// ── Session Auth ───────────────────────────────────────────────────────────
function isSessionValid() {
  const token = localStorage.getItem(SESSION_KEY);
  const exp   = parseInt(localStorage.getItem(SESSION_EXP) || '0', 10);
  return token && Date.now() < exp;
}

function saveSession(token, expiresInSeconds) {
  localStorage.setItem(SESSION_KEY, token);
  localStorage.setItem(SESSION_EXP, Date.now() + expiresInSeconds * 1000);
}

function clearSession() {
  localStorage.removeItem(SESSION_KEY);
  localStorage.removeItem(SESSION_EXP);
}

function getToken() { return localStorage.getItem(SESSION_KEY); }

function checkSessionExpiry() {
  if (!isSessionValid() && !overlay.classList.contains('visible')) {
    clearSession();
    showLock('Session expired. Please log in again.');
  }
  // Update expiry indicator
  const exp = parseInt(localStorage.getItem(SESSION_EXP) || '0', 10);
  if (exp && isSessionValid()) {
    const minsLeft = Math.ceil((exp - Date.now()) / 60000);
    sessionIndicator.textContent = `session · ${minsLeft}m`;
  }
}

// ── Lock / Unlock ──────────────────────────────────────────────────────────
function showLock(msg = '') {
  overlay.classList.remove('hidden');
  passwordInput.value = '';
  if (msg) {
    lockError.textContent = msg;
    lockError.classList.add('visible');
  }
  setTimeout(() => passwordInput.focus(), 300);
}

function hideLock() {
  overlay.classList.add('hidden');
}

async function attemptLogin() {
  const pw = passwordInput.value.trim();
  if (!pw) return;
  unlockBtn.textContent = '…';
  unlockBtn.disabled = true;
  lockError.classList.remove('visible');

  try {
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pw }),
    });

    if (!res.ok) {
      lockError.textContent = 'Incorrect password';
      lockError.classList.add('visible');
      passwordInput.value = '';
      passwordInput.focus();
      return;
    }

    const data = await res.json();
    saveSession(data.token, data.expires_in);
    hideLock();
    init();
  } catch {
    // Offline / API down — if we have a cached schedule, let them in (view-only)
    const cached = localStorage.getItem(SCHEDULE_KEY);
    if (cached) {
      lockError.textContent = 'Offline — loading cached schedule';
      lockError.classList.add('visible');
      lockError.style.color = 'var(--warning)';
      setTimeout(() => { hideLock(); initOffline(); }, 1500);
    } else {
      lockError.textContent = 'Cannot connect to server';
      lockError.classList.add('visible');
    }
  } finally {
    unlockBtn.textContent = 'Unlock';
    unlockBtn.disabled = false;
  }
}

unlockBtn.addEventListener('click', attemptLogin);
passwordInput.addEventListener('keydown', e => { if (e.key === 'Enter') attemptLogin(); });

// ── Server Time Sync ───────────────────────────────────────────────────────
async function syncTime() {
  try {
    const res  = await fetch('/api/time');
    const data = await res.json();
    // Store server's HH:MM for task matching
    serverTime = data.time_24; // "HH:MM"
    return data;
  } catch {
    return null;
  }
}

// Returns current time as "HH:MM" using the synced server time (EST)
// Falls back to local clock if server unreachable
function getCurrentHHMM() {
  if (serverTime) return serverTime;
  // Fallback: try to approximate EST
  const now = new Date();
  const estOffset = -5 * 60; // EST UTC-5 (ignores DST for fallback)
  const utc = now.getTime() + now.getTimezoneOffset() * 60000;
  const estDate = new Date(utc + estOffset * 60000);
  return estDate.toTimeString().slice(0, 5);
}

// ── Clock & Ticker ─────────────────────────────────────────────────────────
function startClock() {
  function tick() {
    const now = new Date();
    clockEl.textContent = now.toLocaleTimeString('en-US', { hour12: false, hour:'2-digit', minute:'2-digit', second:'2-digit' });
    dateEl.textContent  = now.toLocaleDateString('en-US', { weekday:'long', month:'long', day:'numeric', year:'numeric' });
  }
  tick();
  return setInterval(tick, 1000);
}

// ── Schedule Logic ─────────────────────────────────────────────────────────
function loadCachedSchedule() {
  try {
    const raw = localStorage.getItem(SCHEDULE_KEY);
    if (raw) schedule = JSON.parse(raw);
  } catch { schedule = []; }
}

function saveSchedule(s) {
  schedule = s;
  localStorage.setItem(SCHEDULE_KEY, JSON.stringify(s));
}

function getCurrentTask(hhmm) {
  const nowMins = timeToMinutes(hhmm);
  return schedule.find(item => {
    const s = timeToMinutes(item.start);
    const e = timeToMinutes(item.end);
    return nowMins >= s && nowMins < e;
  }) || null;
}

// ── Progress Bar ───────────────────────────────────────────────────────────
function updateFocusUI() {
  const hhmm = getCurrentHHMM();
  const task = getCurrentTask(hhmm);
  const nowMins = timeToMinutes(hhmm);

  if (!task) {
    taskEl.textContent = 'Free Time';
    taskEl.className   = 'free-time';
    taskTimeEl.textContent = '';
    progressFill.style.width = '0%';
    progressFill.classList.remove('active');
    progressPct.textContent  = '—';
    progressLeft.textContent = '';
    return;
  }

  taskEl.textContent = task.task;
  taskEl.className   = '';
  taskTimeEl.textContent = `${task.start} – ${task.end}`;

  const start = timeToMinutes(task.start);
  const end   = timeToMinutes(task.end);
  const total = end - start;
  const elapsed = nowMins - start;
  const pct = Math.min(100, Math.max(0, Math.round((elapsed / total) * 100)));
  const minsLeft = end - nowMins;

  progressFill.style.width = `${pct}%`;
  progressFill.classList.toggle('active', pct > 0 && pct < 100);
  progressPct.textContent  = `${pct}%`;
  progressLeft.textContent = formatDuration(minsLeft);
}

// ── Schedule Modal ─────────────────────────────────────────────────────────
function renderScheduleModal() {
  const hhmm = getCurrentHHMM();
  const nowMins = timeToMinutes(hhmm);
  scheduleList.innerHTML = '';

  if (!schedule.length) {
    scheduleList.innerHTML = '<div style="color:var(--text-dim);text-align:center;padding:24px;font-size:0.85rem;">No schedule yet. Give the AI a command to get started.</div>';
    return;
  }

  schedule.forEach(item => {
    const s = timeToMinutes(item.start);
    const e = timeToMinutes(item.end);
    const isCurrent = nowMins >= s && nowMins < e;

    const el = document.createElement('div');
    el.className = 'schedule-item' + (isCurrent ? ' current-item' : '');
    el.innerHTML = `
      <span class="item-time">${item.start} – ${item.end}</span>
      <span class="item-dot"></span>
      <span class="item-name">${item.task}</span>
    `;
    scheduleList.appendChild(el);
  });
}

$('see-schedule-btn').addEventListener('click', () => {
  renderScheduleModal();
  scheduleModal.classList.add('open');
});

$('close-modal').addEventListener('click', () => scheduleModal.classList.remove('open'));

scheduleModal.addEventListener('click', e => {
  if (e.target === scheduleModal) scheduleModal.classList.remove('open');
});

// ── AI Command Submission ──────────────────────────────────────────────────
async function submitCommand() {
  const cmd = commandInput.value.trim();
  if (!cmd) return;
  if (Date.now() - lastSubmit < RATE_LIMIT_MS) {
    showToast('Please wait a moment before sending another request');
    return;
  }
  if (!isSessionValid()) { showLock('Session expired'); return; }

  lastSubmit = Date.now();
  submitBtn.disabled = true;
  submitBtn.classList.add('loading');
  aiStatus.textContent = 'Thinking…';
  aiStatus.className = '';

  // Rate limit countdown
  let countdown = RATE_LIMIT_MS / 1000;
  const timer = setInterval(() => {
    countdown--;
    if (countdown <= 0) {
      clearInterval(timer);
      submitBtn.disabled = false;
      submitBtn.classList.remove('loading');
    }
  }, 1000);

  try {
    const res = await fetch('/api/update-schedule', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    token: getToken(),
    command: commandInput.value.trim(),
    current_schedule: schedule,
  }),
});

if (res.status === 401) { clearSession(); showLock('Session expired'); return; }

const text = await res.text();
const scheduleMarker = text.indexOf('__SCHEDULE__');
const errorMarker = text.indexOf('__ERROR__');

if (errorMarker !== -1) {
  const err = JSON.parse(text.slice(errorMarker + 11));
  throw new Error(err.detail);
}
if (scheduleMarker === -1) throw new Error('No response from AI');

const data = JSON.parse(text.slice(scheduleMarker + 12));
saveSchedule(data.schedule);
updateFocusUI();
commandInput.value = '';
aiStatus.textContent = '✓ Schedule updated';
aiStatus.className = 'success';
setTimeout(() => { aiStatus.textContent = ''; aiStatus.className = ''; }, 3000);
showToast('Schedule updated');
  } catch (err) {
    // Fallback: load from localStorage if available
    loadCachedSchedule();
    if (schedule.length) {
      updateFocusUI();
      aiStatus.textContent = 'AI offline — showing cached schedule';
      aiStatus.className = 'error';
    } else {
      aiStatus.textContent = `Error: ${err.message}`;
      aiStatus.className = 'error';
    }
    setTimeout(() => { aiStatus.textContent = ''; aiStatus.className = ''; }, 5000);
  }
}

submitBtn.addEventListener('click', submitCommand);
commandInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    submitCommand();
  }
});

// ── Init (authenticated) ───────────────────────────────────────────────────
async function init() {
  loadCachedSchedule();

  // Sync server time, then start UI
  await syncTime();
  startClock();
  updateFocusUI();

  // Re-sync server time every 30s, update UI every second
  setInterval(async () => { await syncTime(); }, 30000);
  setInterval(updateFocusUI, 10000); // re-check current task every 10s
  setInterval(checkSessionExpiry, 60000);

  checkSessionExpiry();
}

function initOffline() {
  loadCachedSchedule();
  startClock();
  updateFocusUI();
  setInterval(updateFocusUI, 10000);
  submitBtn.disabled = true;
  aiStatus.textContent = 'Offline mode — AI commands disabled';
  aiStatus.className = 'error';
}

// ── Bootstrap ──────────────────────────────────────────────────────────────
if (isSessionValid()) {
  hideLock();
  init();
} else {
  showLock();
}
