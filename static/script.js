// ── Constants ──────────────────────────────────────────────────────────────
const SESSION_KEY   = 'focus_session_token';
const SESSION_EXP   = 'focus_session_exp';
const SCHEDULE_KEY  = 'focus_schedule';
const RATE_LIMIT_MS = 5000;

// ── State ──────────────────────────────────────────────────────────────────
let schedule   = [];
let serverTime = null;
let lastSubmit = 0;

// ── DOM refs ───────────────────────────────────────────────────────────────
const $  = id => document.getElementById(id);
const overlay          = $('password-overlay');
const passwordInput    = $('password-input');
const unlockBtn        = $('unlock-btn');
const lockError        = $('lock-error');
const clockEl          = $('clock');
const dateEl           = $('date-display');
const taskEl           = $('current-task');
const taskTimeEl       = $('task-time-range');
const progressFill     = $('progress-fill');
const progressPct      = $('progress-pct');
const progressLeft     = $('progress-remaining');
const commandInput     = $('command-input');
const submitBtn        = $('submit-btn');
const aiStatus         = $('ai-status');
const scheduleModal    = $('schedule-modal');
const scheduleList     = $('schedule-list');
const sessionIndicator = $('session-indicator');
const toast            = $('toast');

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

function formatDuration(minutes) {
  if (minutes <= 0) return 'Done';
  if (minutes < 60) return `${minutes}m left`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h}h ${m}m left` : `${h}h left`;
}

function autoGrowTextarea() {
  commandInput.style.height = 'auto';
  commandInput.style.height = Math.min(commandInput.scrollHeight, 120) + 'px';
}
commandInput.addEventListener('input', autoGrowTextarea);
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
  if (!isSessionValid() && !overlay.classList.contains('hidden')) {
    clearSession();
    showLock('Session expired. Please log in again.');
  }
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
  } catch (err) {
    const cached = localStorage.getItem(SCHEDULE_KEY);
    if (cached) {
      lockError.textContent = 'Offline — loading cached schedule';
      lockError.classList.add('visible');
      lockError.style.color = 'var(--warning)';
      setTimeout(() => { hideLock(); initOffline(); }, 1500);
    } else {
      lockError.textContent = `Cannot connect to server: ${err.message}`;
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
    serverTime = data.time_24;
    return data;
  } catch {
    return null;
  }
}

function getCurrentHHMM() {
  if (serverTime) return serverTime;
  const now = new Date();
  const estOffset = -5 * 60;
  const utc = now.getTime() + now.getTimezoneOffset() * 60000;
  const estDate = new Date(utc + estOffset * 60000);
  return estDate.toTimeString().slice(0, 5);
}

// ── Clock ──────────────────────────────────────────────────────────────────
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
  const todayKey = new Date().toISOString().slice(0, 10);
  return schedule.find(item => {
    if (item.date && item.date !== todayKey) return false;
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

  const start   = timeToMinutes(task.start);
  const end     = timeToMinutes(task.end);
  const total   = end - start;
  const elapsed = nowMins - start;
  const pct     = Math.min(100, Math.max(0, Math.round((elapsed / total) * 100)));
  const minsLeft = end - nowMins;

  progressFill.style.width = `${pct}%`;
  progressFill.classList.toggle('active', pct > 0 && pct < 100);
  progressPct.textContent  = `${pct}%`;
  progressLeft.textContent = formatDuration(minsLeft);
}

// ── Schedule Modal ─────────────────────────────────────────────────────────
function renderScheduleModal() {
  const hhmm    = getCurrentHHMM();
  const nowMins = timeToMinutes(hhmm);
  const todayKey = new Date().toISOString().slice(0, 10);
  scheduleList.innerHTML = '';

  if (!schedule.length) {
    scheduleList.innerHTML = '<div style="color:var(--text-dim);text-align:center;padding:24px;font-size:0.85rem;">No schedule yet. Give the AI a command to get started.</div>';
    return;
  }

  // Group by date
  const byDate = {};
  schedule.forEach(item => {
    const d = item.date || todayKey;
    if (!byDate[d]) byDate[d] = [];
    byDate[d].push(item);
  });

  Object.keys(byDate).sort().forEach(date => {
    // Date header
    const header = document.createElement('div');
    const isToday = date === todayKey;
    const label = isToday ? 'Today' : new Date(date + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' });
    header.style.cssText = 'font-size:0.7rem;text-transform:uppercase;letter-spacing:0.12em;color:var(--text-dim);padding:12px 16px 4px;';
    header.textContent = label;
    scheduleList.appendChild(header);

    byDate[date].forEach(item => {
      const s = timeToMinutes(item.start);
      const e = timeToMinutes(item.end);
      const isCurrent = isToday && nowMins >= s && nowMins < e;

      const el = document.createElement('div');
      el.className = 'schedule-item' + (isCurrent ? ' current-item' : '');
      el.innerHTML = `
        <span class="item-time">${item.start} – ${item.end}</span>
        <span class="item-dot"></span>
        <span class="item-name">${item.task}</span>
      `;
      scheduleList.appendChild(el);
    });
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
  aiStatus.className = '';

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
    aiStatus.textContent = 'Step 1/4: Sending request…';

    const res = await fetch('/api/update-schedule', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        token: getToken(),
        command: cmd,
        current_schedule: schedule,
      }),
    });

    aiStatus.textContent = `Step 2/4: Got HTTP ${res.status}…`;

    if (res.status === 401) {
      clearSession();
      showLock('Session expired — please log in again');
      return;
    }

    if (!res.ok) {
      const errText = await res.text();
      throw new Error(`HTTP ${res.status}: ${errText}`);
    }

    aiStatus.textContent = 'Step 3/4: Reading AI response…';
    const text = await res.text();
    aiStatus.textContent = `Step 3/4: Response received (${text.length} chars)…`;

    const scheduleMarker = text.indexOf('__SCHEDULE__');
    const errorMarker    = text.indexOf('__ERROR__');

    if (errorMarker !== -1) {
      const errJson = text.slice(errorMarker + 9);
      const err = JSON.parse(errJson);
      throw new Error(`AI Error: ${err.detail}`);
    }

    if (scheduleMarker === -1) {
      throw new Error(`No __SCHEDULE__ marker. Raw: "${text.slice(0, 200)}"`);
    }

    aiStatus.textContent = 'Step 4/4: Parsing schedule…';
    const data = JSON.parse(text.slice(scheduleMarker + 12));
    saveSchedule(data.schedule);
    updateFocusUI();
    commandInput.value = '';
    autoGrowTextarea();
    aiStatus.textContent = '✓ Schedule updated';
    aiStatus.className = 'success';
    setTimeout(() => { aiStatus.textContent = ''; aiStatus.className = ''; }, 3000);
    showToast('Schedule updated');

  } catch (err) {
    aiStatus.textContent = `❌ ${err.message}`;
    aiStatus.className = 'error';
    console.error('Full error:', err);
    showToast(err.message.slice(0, 100), 6000);
    loadCachedSchedule();
    if (schedule.length) updateFocusUI();
  }
}

submitBtn.addEventListener('click', submitCommand);
commandInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    submitCommand();
  }
});

// ── Init ───────────────────────────────────────────────────────────────────
async function init() {
  loadCachedSchedule();
  await syncTime();
  startClock();
  updateFocusUI();
  setInterval(async () => { await syncTime(); }, 30000);
  setInterval(updateFocusUI, 10000);
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
