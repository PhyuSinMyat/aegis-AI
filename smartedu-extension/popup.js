// popup.js — reads the active tab URL directly so the display is always
// accurate, regardless of when the last heartbeat fired.

const SMARTEDU_PATH_PATTERNS = [
  '/dashboard', '/tracker', '/plan', '/upload',
  '/weekly-timetable', '/today-tasks', '/insights', '/settings',
];
const SAFE_INTERNAL_HOSTS = ['localhost', '127.0.0.1'];

function normalizeHostname(value) {
  let host = String(value || '').trim().toLowerCase();
  if (!host) return '';
  if (host.startsWith('http://') || host.startsWith('https://')) {
    try { host = new URL(host).hostname; } catch {}
  }
  const slash = host.indexOf('/');
  if (slash >= 0) host = host.slice(0, slash);
  if (host.startsWith('www.')) host = host.slice(4);
  return host;
}

function hostnameMatchesIdentifier(hostname, identifier) {
  const h = normalizeHostname(hostname);
  const id = normalizeHostname(identifier);
  if (!h || !id) return false;
  return h === id || h.endsWith('.' + id);
}

async function classifyActiveTab(identifiers) {
  let tab;
  try {
    [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  } catch {
    return { hostname: '', allowed: true };
  }
  if (!tab || !tab.url) return { hostname: '', allowed: true };

  const url = tab.url;
  if (url.startsWith('chrome://') || url.startsWith('about:')) {
    return { hostname: 'New Tab', allowed: true };
  }

  let hostname = '';
  try {
    hostname = new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return { hostname: url, allowed: true };
  }

  if (SAFE_INTERNAL_HOSTS.includes(hostname)) return { hostname, allowed: true };
  if (SMARTEDU_PATH_PATTERNS.some(p => url.includes(p))) return { hostname: 'Aegis', allowed: true };

  const allowed = identifiers.some(id => hostnameMatchesIdentifier(hostname, id));
  return { hostname, allowed };
}

const dot     = document.getElementById('statusDot');
const label   = document.getElementById('statusLabel');
const sub     = document.getElementById('statusSub');
const tabRow  = document.getElementById('tabRow');
const tabName = document.getElementById('tabName');
const chip    = document.getElementById('tabChip');
const footer  = document.getElementById('lastSent');

async function render() {
  const storage = await chrome.storage.local.get([
    'state', 'lastSent', 'serverUrl', 'userAllowedIdentifiers',
  ]);

  const { state, lastSent, serverUrl } = storage;
  const identifiers = Array.isArray(storage.userAllowedIdentifiers)
    ? storage.userAllowedIdentifiers
    : [];

  // Session state (tracking / no_session / server_offline / idle)
  if (state === 'tracking') {
    sub.textContent = 'Session active, sending heartbeats';
  } else if (state === 'no_session') {
    dot.className = 'dot idle';
    label.textContent = 'No active session';
    sub.textContent = 'Waiting for a session to start';
    tabRow.style.display = 'none';
  } else if (state === 'server_offline') {
    dot.className = 'dot offline';
    label.textContent = 'Cannot reach Aegis';
    sub.textContent = 'Make sure the app is running';
    tabRow.style.display = 'none';
  } else {
    dot.className = 'dot idle';
    label.textContent = 'Waiting…';
    sub.textContent = 'Will check in 30 seconds';
    tabRow.style.display = 'none';
  }

  // Active-tab row — always derived from the real current tab, not cached storage
  if (state === 'tracking') {
    const { hostname, allowed } = await classifyActiveTab(identifiers);

    dot.className = 'dot ' + (allowed ? 'allowed' : 'distract');
    label.textContent = allowed ? 'Tracking — Study tab' : 'Tracking — Distraction detected';

    if (hostname) {
      tabRow.style.display = 'block';
      tabName.textContent = hostname;
      chip.textContent = allowed ? 'Study' : 'Distraction';
      chip.className = 'chip ' + (allowed ? 'allowed' : 'distract');
    }
  }

  // Footer
  if (lastSent) {
    const secs = Math.round((Date.now() - lastSent) / 1000);
    footer.textContent = `Last report: ${secs}s ago · ${serverUrl || 'localhost:5000'}`;
  } else if (serverUrl) {
    footer.textContent = `Connected to: ${serverUrl}`;
  }
}

render();

// Keep the popup live while it's open — re-render on any storage change
chrome.storage.onChanged.addListener((_changes, area) => {
  if (area === 'local') render();
});
