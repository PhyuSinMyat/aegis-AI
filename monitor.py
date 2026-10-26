"""
monitor.py — Aegis Desktop Activity Tracker
===============================================
Monitors the active desktop application and sends heartbeats to the Aegis
server every 30 seconds.  Mirrors the browser extension's logic for websites
but at the OS level.

State logic (same as the browser extension):
  - Active window is an ALLOWED desktop app  → Studying
  - Active window is a NON-ALLOWED desktop app → Distracted
  - No mouse / keyboard input for >90 s        → Inactive
  - Active window is a BROWSER                 → defer to browser extension
                                                  (no heartbeat sent)

Milestone notifications — identical wording to the browser extension:
  1 min  → Windows OS notification: banner warning
  2 min  → Windows OS notification: Inactivity / Distraction Warning
  5 min  → Windows OS notification: Keep Studying!
  Session end → Congratulations (>=70% study time) or Study Session Missed (<70%)

Timers on the Tracker page are CUMULATIVE across both websites and desktop
apps.  Every heartbeat adds to exactly one bucket: study / inactive / distract.

Usage
-----
    python monitor.py <token>

Get your token from the Study Tracker page in the browser
("Generate Monitor Token" button).  The token is valid for 24 hours.

Requirements
------------
    pip install psutil plyer requests
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import datetime
import sys
import time
from typing import Optional

# ── Third-party imports (with graceful degradation) ───────────────────────────

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False
    print("ERROR: 'psutil' not installed.  Run:  pip install psutil")
    sys.exit(1)

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    print("ERROR: 'requests' not installed.  Run:  pip install requests")
    sys.exit(1)

try:
    from plyer import notification as _plyer_notif
    _HAS_PLYER = True
except ImportError:
    _HAS_PLYER = False
    print("WARNING: 'plyer' not installed.  Run:  pip install plyer")
    print("         Windows OS notifications will be disabled.")

# ── Config ────────────────────────────────────────────────────────────────────
FLASK_BASE     = "http://127.0.0.1:5000"
POLL_INTERVAL  = 30   # seconds between heartbeats
IDLE_THRESHOLD = 90   # seconds of no input = inactive (1 min 30 s grace period)

# ── Milestone thresholds (same as browser extension) ─────────────────────────
BANNER_SECS    =  1 * 60   #  1 min — banner warning toast
WARNING_SECS   =  2 * 60   #  2 min — OS warning notification
ENCOURAGE_SECS =  5 * 60   #  5 min — "Keep Studying!" notification

# ── Browser process names — defer to the browser extension for these ──────────
BROWSER_PROCESSES = {
    'chrome.exe', 'firefox.exe', 'msedge.exe', 'brave.exe',
    'opera.exe',  'vivaldi.exe', 'chromium.exe',
}

ALLOWED_TITLE_KEYWORDS = [
    "visual studio code", "vs code", "vscode",
    "pycharm", "intellij", "eclipse", "sublime text", "atom",
    "notepad++", "notepad", "spyder", "thonny",
    "jupyter", "anaconda",
    "terminal", "command prompt", "powershell", "bash", "cmd",
    "microsoft word", "word", "powerpoint", "microsoft excel", "excel",
    "onenote", "libreoffice",
    "acrobat", "pdf",
    "chatgpt", "claude", "gemini", "copilot", "perplexity",
    "youtube", "google", "stackoverflow", "github",
    "wikipedia", "khan academy", "coursera", "udemy",
    "quizlet", "anki", "docs.python", "developer.mozilla", "w3schools",
    "localhost", "127.0.0.1", "aegis",
    "file explorer",
]


# ── Windows idle-time detection ───────────────────────────────────────────────

class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]


def get_idle_secs() -> float:
    """Return seconds since the last keyboard / mouse input (Windows only)."""
    try:
        lii        = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
        millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return max(0.0, millis / 1000.0)
    except Exception:
        return 0.0


# ── Active-window detection ───────────────────────────────────────────────────

def get_active_window() -> tuple[str, str]:
    """
    Return (process_name_lower, window_title) for the current foreground window.
    Returns ('', '') on any failure.
    """
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return ('', '')

        # Get the process ID for this window
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        # Resolve PID → process name via psutil
        proc_name = psutil.Process(pid.value).name().lower()

        # Get the window title
        buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value

        return (proc_name, title)
    except Exception:
        return ('', '')


# ── Windows OS notification ───────────────────────────────────────────────────

def show_os_notification(title: str, message: str, timeout: int = 10) -> None:
    """Show a Windows OS toast notification via plyer (falls back to console)."""
    if _HAS_PLYER:
        try:
            _plyer_notif.notify(
                title    = title,
                message  = message,
                app_name = 'Aegis',
                timeout  = timeout,
            )
            return
        except Exception:
            pass
    # Console fallback when plyer is unavailable
    print(f"\n  *** NOTIFICATION *** {title}: {message}\n")


# ── Allowed-apps cache ────────────────────────────────────────────────────────

_desktop_cache:    list[str] = []
_desktop_cache_ts: float     = 0.0
CACHE_TTL = 60.0  # refresh every 60 s


def get_allowed_desktop_identifiers(token: str) -> list[str]:
    """Fetch allowed desktop process names from server (cached 60 s)."""
    global _desktop_cache, _desktop_cache_ts
    now = time.time()
    if _desktop_cache and (now - _desktop_cache_ts) < CACHE_TTL:
        return _desktop_cache
    try:
        resp = requests.get(
            f"{FLASK_BASE}/tracker/allowed-apps",
            params={"token": token},
            timeout=8,
        )
        if resp.ok:
            data        = resp.json()
            identifiers = [i.lower() for i in data.get("desktop_identifiers", [])]
            _desktop_cache    = identifiers
            _desktop_cache_ts = now
            return identifiers
    except Exception:
        pass
    return _desktop_cache   # serve stale cache on network failure


def is_allowed_desktop_app(proc_name: str, title: str, token: str) -> bool:
    if not proc_name:
        return True   # unknown → don't penalise
    if proc_name in get_allowed_desktop_identifiers(token):
        return True
    if title:
        title_lower = title.lower()
        return any(kw in title_lower for kw in ALLOWED_TITLE_KEYWORDS)
    return False


# ── Server helpers ────────────────────────────────────────────────────────────

def _load_aegis_config() -> dict:
    import json
    import os
    from pathlib import Path
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    cfg_path = Path(appdata) / "Aegis" / "config.json"
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_active_session(token: str, session_id: int | None = None) -> Optional[dict]:
    try:
        resp = requests.get(
            f"{FLASK_BASE}/tracker/status",
            params={"token": token, "session_id": session_id},
            headers={"Authorization": f"Bearer {token}"},
            timeout=8,
        )
        if resp.status_code == 401:
            print(f"[{_ts()}] [Auth] Token rejected by {FLASK_BASE}/tracker/status (401 Unauthorized).")
            return {"unauthorized": True}
        if resp.ok:
            data = resp.json()
            if data.get("active") or data.get("display"):
                return data
            return None
        print(f"[{_ts()}] [HTTP] {FLASK_BASE}/tracker/status returned status {resp.status_code}")
    except Exception as exc:
        print(f"[{_ts()}] [Network] Cannot reach {FLASK_BASE}/tracker/status: {exc}")
    return None


def notify_session_result(token: str, session_id: int) -> None:
    try:
        response = requests.post(
            f"{FLASK_BASE}/tracker/session/{session_id}/result-notification",
            headers={"Authorization": f"Bearer {token}"}, timeout=8,
        )
        data = response.json() if response.ok else {}
        if data.get("notify"):
            show_os_notification(data["title"], data["message"], timeout=15)
    except requests.RequestException as exc:
        print(f"[{_ts()}] Session result notification error: {exc}")


def send_heartbeat(
    token:        str,
    session_id:   int,
    is_active:    bool,
    is_allowed:   bool,
    current_app:  str,
    elapsed_secs: int,
) -> dict:
    try:
        resp = requests.post(
            f"{FLASK_BASE}/tracker/heartbeat",
            json={
                "token":        token,
                "session_id":   session_id,
                "is_active":    is_active,
                "is_allowed":   is_allowed,
                "current_app":  current_app,
                "elapsed_secs": elapsed_secs,
                "source":       "desktop",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=8,
        )
        if not resp.ok:
            print(f"[{_ts()}] [HTTP] Heartbeat returned status {resp.status_code}")
        return resp.json() if resp.ok else {}
    except Exception as exc:
        print(f"[{_ts()}] [Network] Heartbeat error: {exc}")
        return {"error": str(exc)}


# ── Streak state & milestone notifications ────────────────────────────────────

class StreakState:
    """Tracks per-episode inactive / distract streaks and fires OS notifications
    at the same milestones as the browser extension."""

    def __init__(self) -> None:
        self._inactive_secs: float = 0.0
        self._distract_secs: float = 0.0
        # notification-fired flags
        self._in_banner    = self._in_warn = self._in_enc = False
        self._di_banner    = self._di_warn = self._di_enc = False

    # ── helpers ───────────────────────────────────────────────────────────────

    def _reset_inactive(self) -> None:
        self._inactive_secs = 0.0
        self._in_banner = self._in_warn = self._in_enc = False

    def _reset_distract(self) -> None:
        self._distract_secs = 0.0
        self._di_banner = self._di_warn = self._di_enc = False

    # ── public interface ──────────────────────────────────────────────────────

    def update(self, mode: str, elapsed: float, proc_name: str) -> None:
        """
        Call once per heartbeat cycle.
        mode : 'study' | 'inactive' | 'distract'
        elapsed : real seconds since last call
        proc_name : active process name (for notification messages)
        """
        if mode == 'study':
            self._reset_inactive()
            self._reset_distract()
            return

        if mode == 'inactive':
            self._distract_secs = 0.0   # only reset distract streak, keep flags
            self._di_banner = self._di_warn = self._di_enc = False
            self._inactive_secs += elapsed
            self._check_inactive_notifications()
            return

        if mode == 'distract':
            self._inactive_secs = 0.0
            self._in_banner = self._in_warn = self._in_enc = False
            self._distract_secs += elapsed
            self._check_distract_notifications(proc_name)

    # ── notification checks ───────────────────────────────────────────────────

    def _check_inactive_notifications(self) -> None:
        s = self._inactive_secs

        if s >= BANNER_SECS and not self._in_banner:
            self._in_banner = True
            show_os_notification(
                '⚠️ Inactivity Warning',
                'No activity is detected. Move your mouse or press a '
                'key to resume studying.',
                timeout=8,
            )

        if s >= WARNING_SECS and not self._in_warn:
            self._in_warn = True
            show_os_notification(
                'Inactivity Warning',
                'No activity is detected. Return to studying to reset '
                'this streak.',
                timeout=10,
            )

        if s >= ENCOURAGE_SECS and not self._in_enc:
            self._in_enc = True
            show_os_notification(
                'Keep Studying!',
                "Studying can feel really tough sometimes but let\u2019s stay "
                "focused for a bit more. You can do this \u2014 just don\u2019t "
                "give up halfway.",
                timeout=15,
            )


    def _check_distract_notifications(self, proc_name: str) -> None:
        app = proc_name or 'a non-study app'
        s   = self._distract_secs

        if s >= BANNER_SECS and not self._di_banner:
            self._di_banner = True
            show_os_notification(
                '\U0001f534 Distraction Warning',
                f'{app} is not an allowed study app. Please return to your '
                'study tools.',
                timeout=8,
            )

        if s >= WARNING_SECS and not self._di_warn:
            self._di_warn = True
            show_os_notification(
                'Distraction Warning',
                f'{app} has been distracting you. Return to an '
                'allowed study site.',
                timeout=10,
            )

        if s >= ENCOURAGE_SECS and not self._di_enc:
            self._di_enc = True
            show_os_notification(
                'Keep Studying!',
                "Studying can feel really tough sometimes but let\u2019s stay "
                "focused for a bit more. You can do this \u2014 just don\u2019t "
                "give up halfway.",
                timeout=15,
            )


    @property
    def inactive_secs(self) -> float:
        return self._inactive_secs

    @property
    def distract_secs(self) -> float:
        return self._distract_secs


# ── Utilities ─────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    global FLASK_BASE
    cfg = _load_aegis_config()
    if cfg.get("server_url"):
        FLASK_BASE = str(cfg["server_url"]).strip().rstrip("/") or FLASK_BASE

    token = sys.argv[1].strip() if len(sys.argv) >= 2 else str(cfg.get("token") or "").strip()
    if not token:
        print(__doc__)
        print("Usage:  python monitor.py [token]")
        print("No token provided on command line or found in %APPDATA%\\Aegis\\config.json.")
        sys.exit(1)

    streak = StreakState()
    active_session_id = None
    session_grace_until = 0.0

    print(f"[{_ts()}] Aegis Desktop Tracker started  (polling every {POLL_INTERVAL}s)")
    print(f"[{_ts()}] Flask base URL : {FLASK_BASE}")
    print(f"[{_ts()}] Notifications  : {'enabled (plyer)' if _HAS_PLYER else 'DISABLED — install plyer'}")
    print("           Press Ctrl+C to stop.\n")

    last_tick = time.time()

    while True:
        try:
            now     = time.time()
            elapsed = min(now - last_tick, POLL_INTERVAL)
            last_tick = now

            # ── Find active session ────────────────────────────────────────────
            status = get_active_session(token, active_session_id)
            if status and status.get("unauthorized"):
                cfg = _load_aegis_config()
                new_token = str(cfg.get("token") or "").strip()
                if new_token and new_token != token:
                    print(f"[{_ts()}] [Auth] Reloaded updated token from %APPDATA%\\Aegis\\config.json")
                    token = new_token
                    status = get_active_session(token, active_session_id)
            if status and status.get("display", {}).get("status") in ("completed", "incompleted"):
                notify_session_result(token, status["session_id"])
                status = None
            if not status or status.get("unauthorized"):
                if not status:
                    print(f"[{_ts()}] No active session — waiting…")
                streak = StreakState()   # reset streaks when session is gone
                active_session_id = None
                session_grace_until = 0.0
                time.sleep(POLL_INTERVAL)
                continue

            session_id = status["session_id"]
            if session_id != active_session_id:
                active_session_id = session_id
                session_grace_until = now + IDLE_THRESHOLD
                streak = StreakState()

            # ── Detect foreground window ───────────────────────────────────────
            proc_name, title = get_active_window()

            # ── Browser? Defer entirely to the browser extension ───────────────
            if proc_name in BROWSER_PROCESSES:
                print(
                    f"[{_ts()}] [{proc_name}] "
                    "Browser detected — deferring to extension (no heartbeat sent)"
                )
                # Reset desktop streaks; the extension owns notification state
                streak = StreakState()
                time.sleep(POLL_INTERVAL)
                continue

            # ── Classify desktop state ─────────────────────────────────────────
            # Rule: check app type FIRST.
            # - Distracted app → always 'distract', regardless of idle state.
            # - Study app      → 'inactive' only after 90 s of no input;
            #                    otherwise 'study'.
            idle_s = get_idle_secs()

            if is_allowed_desktop_app(proc_name, title, token):
                if idle_s >= IDLE_THRESHOLD and now > session_grace_until:
                    mode       = 'inactive'
                    is_active  = False
                    is_allowed = True
                else:
                    mode       = 'study'
                    is_active  = True
                    is_allowed = True
            else:
                mode       = 'distract'
                is_active  = True
                is_allowed = False

            # ── Update local streak & fire OS notifications ────────────────────
            streak.update(mode, elapsed, proc_name)

            # ── Send heartbeat to backend ──────────────────────────────────────
            result = send_heartbeat(
                token        = token,
                session_id   = session_id,
                is_active    = is_active,
                is_allowed   = is_allowed,
                current_app  = title or proc_name or 'Unknown',
                elapsed_secs = max(1, int(elapsed)),
            )

            display    = result.get("display", {})
            mode_label = {
                'study':    '\u2713 Studying ',
                'inactive': '~ Inactive ',
                'distract': '\u2717 Distracted',
            }.get(mode, mode)

            app_short = f"{proc_name}"
            if title:
                app_short += f": {title[:35]}"

            print(
                f"[{_ts()}] [{app_short[:52]:52s}] {mode_label}  "
                f"study={display.get('study_time','?')}  "
                f"inact={display.get('inactivity_time','?')}  "
                f"distract={display.get('distraction_time','?')}"
            )

            # Stop if the session ended
            if display.get("status") in ("completed", "incompleted"):
                notify_session_result(token, session_id)
                print(f"\n[{_ts()}] Session {display.get('status')}. Monitor stopping.")
                break

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print(f"\n[{_ts()}] Monitor stopped by user.")
            break
        except Exception as exc:
            print(f"[{_ts()}] Unexpected error: {exc}")
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
