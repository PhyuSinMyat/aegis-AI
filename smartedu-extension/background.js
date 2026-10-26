// Aegis Study Tracker background service worker
//
// Tracks the active browser tab during study sessions, reports heartbeats to the
// Aegis server, and keeps per-episode inactivity/distraction streaks for
// banners and notifications.

const SMARTEDU_PATH_PATTERNS = [
  '/dashboard', '/tracker', '/plan', '/upload',
  '/weekly-timetable', '/today-tasks', '/insights', '/settings',
];

const SAFE_INTERNAL_HOSTS = ['localhost', '127.0.0.1'];
const USER_APPS_CACHE_KEY = 'userAllowedIdentifiers';
const USER_APPS_CACHE_TS_KEY = 'userAllowedIdentifiersFetchedAt';
const USER_APPS_CACHE_TTL_MS = 60 * 1000;
const NOTIFY_BEFORE_MINS = 10;
const IDLE_THRESHOLD_SECS = 90;
const HEARTBEAT_SECS = 30;
const WARNING_MILESTONE_SECS = 2 * 60;
const ENCOURAGE_MILESTONE_SECS = 5 * 60;

function createEpisodeState() {
  return {
    inactiveStreakSecs: 0,
    distractStreakSecs: 0,
    inactiveWarnShown: false,
    inactiveEncourageShown: false,
    distractWarnShown: false,
    distractEncourageShown: false,
    trackingMode: 'idle',
    inactiveWarningEventId: 0,
    distractWarningEventId: 0,
    lastHeartbeatTs: 0,
  };
}

async function readEpisodeState() {
  const stored = await chrome.storage.local.get([
    'inactiveStreakSecs',
    'distractStreakSecs',
    'inactiveWarnShown',
    'inactiveEncourageShown',
    'distractWarnShown',
    'distractEncourageShown',
    'trackingMode',
    'inactiveWarningEventId',
    'distractWarningEventId',
    'lastHeartbeatTs',
  ]);

  return {
    inactiveStreakSecs: Number(stored.inactiveStreakSecs || 0),
    distractStreakSecs: Number(stored.distractStreakSecs || 0),
    inactiveWarnShown: Boolean(stored.inactiveWarnShown),
    inactiveEncourageShown: Boolean(stored.inactiveEncourageShown),
    distractWarnShown: Boolean(stored.distractWarnShown),
    distractEncourageShown: Boolean(stored.distractEncourageShown),
    trackingMode: String(stored.trackingMode || 'idle'),
    inactiveWarningEventId: Number(stored.inactiveWarningEventId || 0),
    distractWarningEventId: Number(stored.distractWarningEventId || 0),
    lastHeartbeatTs: Number(stored.lastHeartbeatTs || 0),
  };
}

async function writeEpisodeState(next) {
  await chrome.storage.local.set(next);
}

async function resetEpisodeState(extra = {}) {
  await writeEpisodeState({
    ...createEpisodeState(),
    ...extra,
  });
}

const NOTIFICATION_DISPLAY_MS = 60 * 1000; // show OS notification for 1 minute

async function notifySessionResult(serverUrl, sessionId) {
  const resp = await fetch(`${serverUrl}/tracker/session/${sessionId}/result-notification`, {
    method: 'POST', credentials: 'include',
  });
  if (!resp.ok) return;
  const result = await resp.json();
  if (result.notify) {
    chrome.notifications.create(`smartedu_result_${sessionId}`, {
      type: 'basic', iconUrl: 'icon.png', title: result.title,
      message: result.message, priority: 2, requireInteraction: true,
    });
  }
}

async function showMilestoneNotification(kind, milestoneSecs, hostname) {
  const isInactive = kind === 'inactive';
  const isEncourage = milestoneSecs === ENCOURAGE_MILESTONE_SECS;

  const title = isEncourage
    ? 'Keep Studying!'
    : isInactive
      ? 'Inactivity Warning'
      : 'Distraction Warning';

  let message = '';
  if (isEncourage) {
    message = "Studying can feel really tough sometimes but let's stay focused for a bit more. You can do this — just don't give up halfway.";
  } else if (isInactive) {
    message = 'No activity is detected. Return to studying to reset this streak.';
  } else {
    message = `${hostname || 'This site'} has been distracting you. Return to an allowed study site.`;
  }

  const notifId = `smartedu_${kind}_${milestoneSecs}_${Date.now()}`;

  // requireInteraction: true keeps the OS notification on screen until dismissed.
  // We then clear it programmatically after 1 minute.
  chrome.notifications.create(notifId, {
    type: 'basic',
    iconUrl: 'icon.png',
    title,
    message,
    priority: 2,
    requireInteraction: true,
  });

  setTimeout(() => chrome.notifications.clear(notifId), NOTIFICATION_DISPLAY_MS);
}

async function updateEpisodeState(mode, hostname, extra = {}) {
  const current = await readEpisodeState();
  const next = { ...current };

  // Compute real elapsed seconds since the last heartbeat so that immediate
  // calls (onActivated, startup) do not inflate the streak the way a fixed
  // HEARTBEAT_SECS constant would. Cap at HEARTBEAT_SECS to guard against
  // clock gaps (e.g. laptop waking from sleep).
  const now = Date.now();
  const elapsed = current.lastHeartbeatTs > 0
    ? Math.min((now - current.lastHeartbeatTs) / 1000, HEARTBEAT_SECS)
    : 0;
  next.lastHeartbeatTs = now;

  if (mode === 'study') {
    next.inactiveStreakSecs = 0;
    next.distractStreakSecs = 0;
    next.inactiveWarnShown = false;
    next.inactiveEncourageShown = false;
    next.distractWarnShown = false;
    next.distractEncourageShown = false;
    next.trackingMode = 'study';
  } else if (mode === 'inactive') {
    next.inactiveStreakSecs += elapsed;
    next.distractStreakSecs = 0;
    next.distractWarnShown = false;
    next.distractEncourageShown = false;
    next.trackingMode = 'inactive';

    if (next.inactiveStreakSecs >= WARNING_MILESTONE_SECS && !next.inactiveWarnShown) {
      next.inactiveWarnShown = true;
      next.inactiveWarningEventId = Date.now();
      await showMilestoneNotification('inactive', WARNING_MILESTONE_SECS, hostname);
    }
    if (next.inactiveStreakSecs >= ENCOURAGE_MILESTONE_SECS && !next.inactiveEncourageShown) {
      next.inactiveEncourageShown = true;
      await showMilestoneNotification('inactive', ENCOURAGE_MILESTONE_SECS, hostname);
    }
  } else if (mode === 'distract') {
    next.distractStreakSecs += elapsed;
    next.inactiveStreakSecs = 0;
    next.inactiveWarnShown = false;
    next.inactiveEncourageShown = false;
    next.trackingMode = 'distract';

    if (next.distractStreakSecs >= WARNING_MILESTONE_SECS && !next.distractWarnShown) {
      next.distractWarnShown = true;
      next.distractWarningEventId = Date.now();
      await showMilestoneNotification('distract', WARNING_MILESTONE_SECS, hostname);
    }
    if (next.distractStreakSecs >= ENCOURAGE_MILESTONE_SECS && !next.distractEncourageShown) {
      next.distractEncourageShown = true;
      await showMilestoneNotification('distract', ENCOURAGE_MILESTONE_SECS, hostname);
    }
  } else {
    next.inactiveStreakSecs = 0;
    next.distractStreakSecs = 0;
    next.inactiveWarnShown = false;
    next.inactiveEncourageShown = false;
    next.distractWarnShown = false;
    next.distractEncourageShown = false;
    next.trackingMode = 'idle';
  }

  await writeEpisodeState({
    ...next,
    ...extra,
  });
}

async function detectServerUrl() {
  const { serverUrl } = await chrome.storage.local.get('serverUrl');
  if (serverUrl) return serverUrl;

  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (!tab.url) continue;
    try {
      const url = new URL(tab.url);
      if (SMARTEDU_PATH_PATTERNS.some(path => url.pathname.startsWith(path))) {
        await chrome.storage.local.set({ serverUrl: url.origin });
        return url.origin;
      }
    } catch {
      continue;
    }
  }

  return 'http://localhost:5000';
}

function normalizeHostname(value) {
  let host = String(value || '').trim().toLowerCase();
  if (!host) return '';

  if (host.startsWith('http://') || host.startsWith('https://')) {
    try {
      host = new URL(host).hostname;
    } catch {
      // keep best-effort original value
    }
  }

  const slash = host.indexOf('/');
  if (slash >= 0) host = host.slice(0, slash);
  if (host.startsWith('www.')) host = host.slice(4);
  return host;
}

function hostnameMatchesIdentifier(hostname, identifier) {
  const normalizedHost = normalizeHostname(hostname);
  const normalizedIdentifier = normalizeHostname(identifier);
  if (!normalizedHost || !normalizedIdentifier) return false;
  if (normalizedHost === normalizedIdentifier) return true;
  return normalizedHost.endsWith('.' + normalizedIdentifier);
}

async function getUserAllowedIdentifiers(serverUrl) {
  const cache = await chrome.storage.local.get([USER_APPS_CACHE_KEY, USER_APPS_CACHE_TS_KEY]);
  const cachedList = Array.isArray(cache[USER_APPS_CACHE_KEY]) ? cache[USER_APPS_CACHE_KEY] : [];
  const fetchedAt = Number(cache[USER_APPS_CACHE_TS_KEY] || 0);

  if (cachedList.length && (Date.now() - fetchedAt) < USER_APPS_CACHE_TTL_MS) {
    return cachedList;
  }

  try {
    const resp = await fetch(`${serverUrl}/tracker/allowed-apps`, { credentials: 'include' });
    if (!resp.ok) return cachedList;

    const data = await resp.json();
    const identifiers = Array.isArray(data.allowed_identifiers)
      ? data.allowed_identifiers.map(value => normalizeHostname(value)).filter(Boolean)
      : [];

    await chrome.storage.local.set({
      [USER_APPS_CACHE_KEY]: identifiers,
      [USER_APPS_CACHE_TS_KEY]: Date.now(),
    });
    return identifiers;
  } catch {
    return cachedList;
  }
}

async function classifyUrl(url, serverUrl) {
  if (!url || url.startsWith('chrome://') || url.startsWith('about:')) {
    return { allowed: true, hostname: 'New Tab' };
  }

  let hostname = '';
  try {
    hostname = new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return { allowed: true, hostname: url };
  }

  if (SAFE_INTERNAL_HOSTS.includes(hostname)) {
    return { allowed: true, hostname };
  }

  const allowedIdentifiers = await getUserAllowedIdentifiers(serverUrl);
  const allowed = allowedIdentifiers.some(identifier => hostnameMatchesIdentifier(hostname, identifier));
  return { allowed, hostname };
}

async function getBrowserActivity() {
  try {
    const state = await chrome.idle.queryState(IDLE_THRESHOLD_SECS);
    return state === 'active';
  } catch {
    return true;
  }
}

async function checkUpcomingSession(serverUrl) {
  try {
    const resp = await fetch(`${serverUrl}/tracker/schedule`, { credentials: 'include' });
    if (!resp.ok) return;
    const data = await resp.json();

    const next = data.next_session;
    if (!next) return;

    const now = new Date();
    const [h, m] = next.start.split(':').map(Number);
    const target = new Date(now);
    target.setHours(h, m, 0, 0);
    const diffMins = (target - now) / 60000;

    if (diffMins < 0 || diffMins > NOTIFY_BEFORE_MINS) return;

    const key = `notified_${data.today}_${next.start}`;
    const stored = await chrome.storage.local.get(key);
    if (stored[key]) return;

    await chrome.storage.local.set({ [key]: true });

    const minsLeft = Math.ceil(diffMins);
    chrome.notifications.create(`session_${next.start}`, {
      type: 'basic',
      iconUrl: 'icon.png',
      title: 'Study Session Starting Soon',
      message: `${next.subject} starts in ${minsLeft} minute${minsLeft !== 1 ? 's' : ''} (${next.start} - ${next.end})`,
      priority: 2,
    });
  } catch {}
}

async function sendHeartbeat() {
  // ── Guard: only send when Chrome is the focused application ─────────────────
  // When the user is in a non-browser desktop app the desktop tracker (monitor.py)
  // owns the heartbeats for that window.  Sending a heartbeat here when Chrome is
  // in the background would incorrectly attribute that time to whatever browser
  // tab happens to be open.
  try {
    const win = await chrome.windows.getLastFocused();
    if (!win || !win.focused) return;
  } catch {
    // If we can't determine focus state, proceed anyway to avoid silent gaps.
  }

  const serverUrl = await detectServerUrl();

  let tab;
  try {
    [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  } catch {
    return;
  }
  if (!tab) return;

  const isAegisTab = SMARTEDU_PATH_PATTERNS.some(path => (tab.url || '').includes(path));
  const { hostname, allowed } = isAegisTab
    ? { hostname: 'Aegis', allowed: true }
    : await classifyUrl(tab.url, serverUrl);

  let sessionId;
  try {
    const { lastTrackingSessionId } = await chrome.storage.local.get('lastTrackingSessionId');
    const query = lastTrackingSessionId ? `?session_id=${lastTrackingSessionId}` : '';
    const resp = await fetch(`${serverUrl}/tracker/status${query}`, { credentials: 'include' });
    if (!resp.ok) {
      await resetEpisodeState({ state: 'no_session', hostname, allowed });
      return;
    }

    const data = await resp.json();
    if (['completed', 'incompleted'].includes(data.display?.status)) {
      await notifySessionResult(serverUrl, data.session_id);
      await chrome.storage.local.remove('lastTrackingSessionId');
      await resetEpisodeState({ state: 'no_session', hostname, allowed });
      return;
    }
    if (!data.active) {
      await resetEpisodeState({ state: 'no_session', hostname, allowed });
      return;
    }
    sessionId = data.session_id;
    await chrome.storage.local.set({ lastTrackingSessionId: sessionId });
  } catch {
    await resetEpisodeState({ state: 'server_offline', hostname, allowed });
    return;
  }

  const browserActive = await getBrowserActivity();
  const trackingMode = allowed ? (browserActive ? 'study' : 'inactive') : 'distract';
  const isActiveForHeartbeat = allowed ? browserActive : true;
  const episodeBefore = await readEpisodeState();
  const elapsedForServer = episodeBefore.lastHeartbeatTs > 0
    ? Math.min((Date.now() - episodeBefore.lastHeartbeatTs) / 1000, HEARTBEAT_SECS)
    : 0;
  let studyElapsedSecs = 0;
  let inactivityElapsedSecs = 0;
  let distractionElapsedSecs = 0;

  if (!allowed) {
    distractionElapsedSecs = Math.round(elapsedForServer);
  } else if (browserActive) {
    studyElapsedSecs = Math.round(elapsedForServer);
  } else {
    inactivityElapsedSecs = Math.round(elapsedForServer);
  }

  try {
    const resp = await fetch(`${serverUrl}/tracker/heartbeat`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        is_active: isActiveForHeartbeat,
        elapsed_secs: HEARTBEAT_SECS,
        is_allowed: allowed,
        study_elapsed_secs: studyElapsedSecs,
        inactivity_elapsed_secs: inactivityElapsedSecs,
        distraction_elapsed_secs: distractionElapsedSecs,
        current_app: hostname,
        source: 'extension',
      }),
    });

    if (!resp.ok) {
      await resetEpisodeState({ state: 'server_offline', hostname, allowed });
      return;
    }

    const heartbeatData = await resp.json();
    if (['completed', 'incompleted'].includes(heartbeatData.display?.status)) {
      await notifySessionResult(serverUrl, sessionId);
      await chrome.storage.local.remove('lastTrackingSessionId');
      await resetEpisodeState({ state: 'no_session', hostname, allowed });
      return;
    }

    await updateEpisodeState(trackingMode, hostname, {
      state: 'tracking',
      hostname,
      allowed,
      lastSent: Date.now(),
    });
  } catch {
    await resetEpisodeState({ state: 'server_offline', hostname, allowed });
  }
}

// Injects content.js into a tab if it is not already running there.
// Uses a ping/pong check so we never double-inject into a live tab.
async function ensureContentScript(tabId) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: 'ping' });
    // Content script responded — already running, nothing to do.
  } catch {
    // No response means the content script is not present. Inject it now.
    try {
      await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] });
    } catch {
      // Tab may be a chrome:// or other restricted URL — silently ignore.
    }
  }
}

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  await ensureContentScript(tabId);
  await sendHeartbeat();
});

chrome.tabs.onUpdated.addListener((_tabId, changeInfo, tab) => {
  if (changeInfo.status === 'complete' && tab.url) {
    try {
      const url = new URL(tab.url);
      if (SMARTEDU_PATH_PATTERNS.some(path => url.pathname.startsWith(path))) {
        chrome.storage.local.set({ serverUrl: url.origin });
      }
    } catch {}

    chrome.tabs.query({ active: true, lastFocusedWindow: true }, async ([activeTab]) => {
      if (activeTab && activeTab.id === tab.id) {
        await ensureContentScript(tab.id);
        await sendHeartbeat();
      }
    });
  }
});

chrome.alarms.create('heartbeat', { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(async alarm => {
  if (alarm.name === 'heartbeat') {
    const serverUrl = await detectServerUrl();
    await sendHeartbeat();
    await checkUpcomingSession(serverUrl);
  }
});

chrome.runtime.onInstalled.addListener(async () => {
  await resetEpisodeState({ state: 'idle', hostname: '', allowed: true });
  const serverUrl = await detectServerUrl();
  await sendHeartbeat();
  await checkUpcomingSession(serverUrl);
});

(async () => {
  const serverUrl = await detectServerUrl();
  await sendHeartbeat();
  await checkUpcomingSession(serverUrl);
})();
