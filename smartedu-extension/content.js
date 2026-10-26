// content.js — injects inactivity/distraction banners into the page.
// Milestone notifications (2 min / 5 min) are handled as Windows OS
// notifications by background.js — no in-page popup is needed here.

const BANNER_ID = 'smartedu-study-banner';
const BANNER_THRESHOLD_SECS = 60;
const BANNER_RESHOW_MS = 60 * 1000;
let hasHydrated = false;
let lastAppliedState = null;
let snoozedMode = '';
let snoozedUntil = 0;
let reshowTimer = null;

function removeBanner() {
  const existing = document.getElementById(BANNER_ID);
  if (existing) existing.remove();
}

function scheduleReshow() {
  if (reshowTimer) clearTimeout(reshowTimer);
  const delay = Math.max(0, snoozedUntil - Date.now());
  reshowTimer = setTimeout(() => {
    reshowTimer = null;
    if (lastAppliedState) applyState(lastAppliedState);
  }, delay);
}

function snoozeBanner(mode) {
  snoozedMode = mode;
  snoozedUntil = Date.now() + BANNER_RESHOW_MS;
  removeBanner();
  scheduleReshow();
}

function showBanner({ mode, title, message, background }) {
  if (snoozedMode === mode && Date.now() < snoozedUntil) {
    removeBanner();
    scheduleReshow();
    return;
  }

  if (snoozedMode && snoozedMode !== mode) {
    snoozedMode = '';
    snoozedUntil = 0;
  }

  const existing = document.getElementById(BANNER_ID);
  if (existing) {
    existing.dataset.mode = mode;
    existing.querySelector('[data-role="title"]').textContent = title;
    existing.querySelector('[data-role="message"]').textContent = message;
    existing.style.background = background;
    return;
  }

  const banner = document.createElement('div');
  banner.id = BANNER_ID;
  banner.dataset.mode = mode;
  banner.style.cssText = `
    all: initial;
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    z-index: 2147483647;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    color: white;
    padding: 12px 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.3);
    box-sizing: border-box;
    background: ${background};
  `;

  const left = document.createElement('div');
  left.style.cssText = 'display:flex; align-items:center; gap:10px;';

  const icon = document.createElement('span');
  icon.textContent = '!';
  icon.style.cssText = 'font-size:18px; font-weight:700;';

  const text = document.createElement('div');

  const titleEl = document.createElement('div');
  titleEl.dataset.role = 'title';
  titleEl.textContent = title;
  titleEl.style.cssText = 'font-size:14px; font-weight:700; color:white;';

  const sub = document.createElement('div');
  sub.dataset.role = 'message';
  sub.textContent = message;
  sub.style.cssText = 'font-size:12px; opacity:0.9; margin-top:2px; color:white;';

  text.appendChild(titleEl);
  text.appendChild(sub);
  left.appendChild(icon);
  left.appendChild(text);

  const closeBtn = document.createElement('button');
  closeBtn.textContent = 'x';
  closeBtn.title = 'Dismiss';
  closeBtn.style.cssText = `
    background: rgba(255,255,255,0.2);
    border: none;
    color: white;
    font-size: 14px;
    font-weight: bold;
    width: 26px;
    height: 26px;
    border-radius: 50%;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  `;
  closeBtn.addEventListener('click', () => snoozeBanner(banner.dataset.mode || mode));

  banner.appendChild(left);
  banner.appendChild(closeBtn);
  document.documentElement.insertBefore(banner, document.documentElement.firstChild);
}

function applyState(state) {
  lastAppliedState = state;

  if (state.state !== 'tracking') {
    snoozedMode = '';
    snoozedUntil = 0;
    if (reshowTimer) {
      clearTimeout(reshowTimer);
      reshowTimer = null;
    }
    removeBanner();
    return;
  }

  const inactiveSecs = Number(state.inactiveStreakSecs || 0);
  const distractSecs = Number(state.distractStreakSecs || 0);

  if (inactiveSecs >= BANNER_THRESHOLD_SECS) {
    showBanner({
      mode: 'inactive',
      title: 'Inactivity Warning',
      message: 'No activity is detected. Move your mouse or press a key to resume studying.',
      background: 'linear-gradient(135deg, #f59e0b, #d97706)',
    });
  } else if (distractSecs >= BANNER_THRESHOLD_SECS) {
    showBanner({
      mode: 'distract',
      title: 'Distraction Warning',
      message: `${state.hostname || 'This site'} is not an allowed study website. Please return to your study site.`,
      background: 'linear-gradient(135deg, #dc2626, #b91c1c)',
    });
  } else {
    snoozedMode = '';
    snoozedUntil = 0;
    if (reshowTimer) {
      clearTimeout(reshowTimer);
      reshowTimer = null;
    }
    removeBanner();
  }
}

const STORAGE_KEYS = [
  'state',
  'hostname',
  'inactiveStreakSecs',
  'distractStreakSecs',
];

chrome.storage.local.get(STORAGE_KEYS, state => {
  applyState(state);
  hasHydrated = true;
});

chrome.storage.onChanged.addListener((_changes, area) => {
  if (area !== 'local' || !hasHydrated) return;
  chrome.storage.local.get(STORAGE_KEYS, state => applyState(state));
});

// Respond to ping from background.js so it knows this content script is alive
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === 'ping') sendResponse({ pong: true });
});
