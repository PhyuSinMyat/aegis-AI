"""
Aegis Windows tray tracker.

The app runs silently from the Windows tray. After the user logs in through
the Aegis website, the tracker page sends a desktop token to this app over
a localhost-only bridge and the app saves it in %APPDATA%\\Aegis\\config.json.

Requirements:
    pip install pystray Pillow psutil plyer requests
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import queue
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import psutil
import requests
from PIL import Image, ImageDraw
import pystray
from pystray import Menu, MenuItem

try:
    from plyer import notification as plyer_notification
except ImportError:
    plyer_notification = None

try:
    import tkinter as tk
    from tkinter import messagebox
except ImportError:
    tk = None
    messagebox = None

try:
    import winreg
except ImportError:
    winreg = None


APP_NAME = "Aegis"
RUN_KEY_NAME = "AegisTray"
DEFAULT_SERVER_URL = "http://127.0.0.1:5000"
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 8765
CHECK_INTERVAL   =  3   # seconds — local window/mode detection cadence
POLL_INTERVAL    = 30   # seconds — server session poll & max heartbeat interval
IDLE_THRESHOLD   = 90   # 1 min 30 s grace period before inactive kicks in
CACHE_TTL        = 60

BANNER_SECS = 60
WARNING_SECS = 2 * 60
ENCOURAGE_SECS = 5 * 60
BANNER_RESHOW_SECS = 60

BROWSER_PROCESSES = {
    "chrome.exe",
    "firefox.exe",
    "msedge.exe",
    "brave.exe",
    "opera.exe",
    "vivaldi.exe",
    "chromium.exe",
}

# Window-title keywords for fallback allowed-app detection. Checked as
# case-insensitive substrings when the process name is not in the explicit
# server-provided list.
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

STATE_COLORS = {
    "study": (22, 163, 74),
    "inactive": (245, 158, 11),
    "distract": (220, 38, 38),
    "idle": (107, 114, 128),
    "defer": (107, 114, 128),
    "offline": (55, 65, 81),
}


class LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]


@dataclass
class TrayStatus:
    state: str = "idle"
    message: str = "Starting..."
    current_app: str = ""
    study_time: str = "-"
    inactivity_time: str = "-"
    distraction_time: str = "-"
    session_status: str = "-"
    last_error: str = ""
    updated_at: float = field(default_factory=time.time)


class SharedState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = TrayStatus()
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()

    def get(self) -> TrayStatus:
        with self._lock:
            return TrayStatus(**self._status.__dict__)

    def update(self, **kwargs) -> TrayStatus:
        with self._lock:
            for key, value in kwargs.items():
                setattr(self._status, key, value)
            self._status.updated_at = time.time()
            return TrayStatus(**self._status.__dict__)

    def wait_or_wake(self, timeout: float) -> None:
        if self.stop_event.is_set():
            return
        self.wake_event.wait(timeout)
        self.wake_event.clear()


class StreakState:
    def __init__(self) -> None:
        self.inactive_secs = 0.0
        self.distract_secs = 0.0
        self._inactive_fired: set[int] = set()
        self._distract_fired: set[int] = set()

    def reset(self) -> None:
        self.inactive_secs = 0.0
        self.distract_secs = 0.0
        self._inactive_fired.clear()
        self._distract_fired.clear()
        desktop_banner.clear()

    def update(self, mode: str, elapsed: float, proc_name: str) -> None:
        if mode == "study":
            self.reset()
            return

        if mode == "inactive":
            if self.distract_secs > 0 or self._distract_fired:
                desktop_banner.clear()
            self.distract_secs = 0.0
            self._distract_fired.clear()
            self.inactive_secs += elapsed
            self._check_inactive()
            return

        if mode == "distract":
            if self.inactive_secs > 0 or self._inactive_fired:
                desktop_banner.clear()
            self.inactive_secs = 0.0
            self._inactive_fired.clear()
            self.distract_secs += elapsed
            self._check_distract(proc_name)

    def _fire_once(self, bucket: set[int], threshold: int, title: str, message: str) -> None:
        if threshold in bucket:
            return
        bucket.add(threshold)
        show_notification(title, message)

    def _check_inactive(self) -> None:
        seconds = self.inactive_secs
        if seconds >= BANNER_SECS:
            desktop_banner.show(
                "inactive",
                "Inactivity Warning",
                "No activity is detected. Move your mouse or press a key to resume studying.",
            )
        if seconds >= WARNING_SECS:
            self._fire_once(
                self._inactive_fired,
                WARNING_SECS,
                "Inactivity Warning",
                "No activity is detected. Return to studying to reset this streak.",
            )
        if seconds >= ENCOURAGE_SECS:
            self._fire_once(
                self._inactive_fired,
                ENCOURAGE_SECS,
                "Keep Studying!",
                "Studying can feel really tough sometimes but let's stay focused for a bit more.",
            )
    def _check_distract(self, proc_name: str) -> None:
        app = proc_name or "This app"
        seconds = self.distract_secs
        if seconds >= BANNER_SECS:
            desktop_banner.show(
                "distract",
                "Distraction Warning",
                f"{app} is not an allowed study app. Please return to your study tools.",
            )
        if seconds >= WARNING_SECS:
            self._fire_once(
                self._distract_fired,
                WARNING_SECS,
                "Distraction Warning",
                f"{app} has been distracting you. Return to an allowed study app.",
            )
        if seconds >= ENCOURAGE_SECS:
            self._fire_once(
                self._distract_fired,
                ENCOURAGE_SECS,
                "Keep Studying!",
                "Studying can feel really tough sometimes but let's stay focused for a bit more.",
            )

class AllowedAppsCache:
    def __init__(self) -> None:
        self.identifiers: list[str] = []
        self.fetched_at = 0.0

    def get(self, server_url: str, token: str) -> list[str]:
        now = time.time()
        if self.identifiers and now - self.fetched_at < CACHE_TTL:
            return self.identifiers

        try:
            resp = requests.get(
                f"{server_url}/tracker/allowed-apps",
                params={"token": token},
                timeout=8,
            )
            if resp.ok:
                data = resp.json()
                self.identifiers = [
                    str(item).strip().lower()
                    for item in data.get("desktop_identifiers", [])
                    if str(item).strip()
                ]
                self.fetched_at = now
        except requests.RequestException:
            pass
        return self.identifiers


def config_dir() -> Path:
    root = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(root) / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def log_path() -> Path:
    return config_dir() / "tray.log"


def _log(message: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [AegisTray] {message}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        config_dir().mkdir(parents=True, exist_ok=True)
        with log_path().open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _old_config_path() -> Path:
    root = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(root) / "SmartEdu" / "config.json"


def _migrate_old_config() -> None:
    """Copy config from old SmartEdu path once if the new Aegis path doesn't exist."""
    new_path = config_path()
    old_path = _old_config_path()
    if new_path.exists() or not old_path.exists():
        return
    try:
        raw = old_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict) and str(data.get("token") or "").strip():
            config_dir().mkdir(parents=True, exist_ok=True)
            new_path.write_text(raw, encoding="utf-8")
            _log(f"Migrated legacy config from {old_path} to {new_path}")
        old_path.unlink(missing_ok=True)
    except (OSError, json.JSONDecodeError) as exc:
        _log(f"Skipped legacy config migration: {exc}")


def load_config() -> dict:
    _migrate_old_config()
    path = config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log(f"Failed to load config from {path}: {exc}")
        return {}


def save_config(config: dict) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps(config, indent=2), encoding="utf-8")


def clear_config() -> None:
    for path in (config_path(), _old_config_path()):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            _log(f"Failed to remove config {path}: {exc}")


def merge_config(updates: dict) -> dict:
    config = load_config()
    config.update(updates)
    save_config(config)
    return config


def normalize_server_url(value: str) -> str:
    value = (value or DEFAULT_SERVER_URL).strip().rstrip("/")
    return value or DEFAULT_SERVER_URL


def _canonical_server_url(value: str) -> str:
    """Canonicalize localhost and 127.0.0.1 URLs so local server checks match."""
    norm = normalize_server_url(value).lower()
    return norm.replace("://localhost", "://127.0.0.1")


def show_notification(title: str, message: str, timeout: int = 10) -> None:
    if plyer_notification is None:
        return
    try:
        plyer_notification.notify(
            title=title,
            message=message,
            app_name=APP_NAME,
            timeout=timeout,
        )
    except Exception:
        pass


class DesktopBanner:
    def __init__(self) -> None:
        self._commands: "queue.Queue[tuple[str, dict]]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def show(self, kind: str, title: str, message: str) -> None:
        if tk is None:
            return
        self._ensure_thread()
        self._commands.put(("show", {
            "kind": kind,
            "title": title,
            "message": message,
        }))

    def clear(self) -> None:
        if tk is None:
            return
        self._ensure_thread()
        self._commands.put(("clear", {}))

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _run(self) -> None:
        try:
            root = tk.Tk()
        except Exception:
            return

        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg="#d97706")

        frame = tk.Frame(root, bg="#d97706")
        frame.pack(fill="both", expand=True)

        icon = tk.Label(frame, text="!", bg="#d97706", fg="white", font=("Segoe UI", 18, "bold"))
        icon.pack(side="left", padx=(18, 10), pady=12)

        text_frame = tk.Frame(frame, bg="#d97706")
        text_frame.pack(side="left", fill="both", expand=True, pady=10)

        title_var = tk.StringVar(value="")
        msg_var = tk.StringVar(value="")
        title_label = tk.Label(
            text_frame, textvariable=title_var, bg="#d97706", fg="white",
            font=("Segoe UI", 10, "bold"), anchor="w"
        )
        title_label.pack(fill="x")
        msg_label = tk.Label(
            text_frame, textvariable=msg_var, bg="#d97706", fg="white",
            font=("Segoe UI", 9), anchor="w", justify="left", wraplength=760
        )
        msg_label.pack(fill="x", pady=(2, 0))

        state = {
            "desired": None,
            "visible": False,
            "hidden_until": 0.0,
        }

        def place_window() -> None:
            root.update_idletasks()
            width = min(max(root.winfo_reqwidth(), 560), root.winfo_screenwidth())
            height = root.winfo_reqheight()
            x = max(0, int((root.winfo_screenwidth() - width) / 2))
            y = 0
            root.geometry(f"{width}x{height}+{x}+{y}")

        def close_temporarily() -> None:
            state["hidden_until"] = time.time() + BANNER_RESHOW_SECS
            state["visible"] = False
            root.withdraw()

        close_btn = tk.Button(
            frame, text="x", command=close_temporarily, bg="#ffffff",
            fg="#111827", relief="flat", font=("Segoe UI", 9, "bold"),
            width=3, cursor="hand2"
        )
        close_btn.pack(side="right", padx=(10, 16), pady=12)

        def apply_payload(payload: dict) -> None:
            kind = payload.get("kind", "inactive")
            bg = "#d97706" if kind == "inactive" else "#dc2626"
            state["desired"] = payload
            root.configure(bg=bg)
            frame.configure(bg=bg)
            icon.configure(bg=bg)
            text_frame.configure(bg=bg)
            title_label.configure(bg=bg)
            msg_label.configure(bg=bg)
            title_var.set(payload.get("title", "Aegis Warning"))
            msg_var.set(payload.get("message", "Return to study mode to clear this banner."))
            if time.time() >= state["hidden_until"]:
                state["visible"] = True
                place_window()
                root.deiconify()

        def pump() -> None:
            try:
                while True:
                    command, payload = self._commands.get_nowait()
                    if command == "show":
                        apply_payload(payload)
                    elif command == "clear":
                        state["desired"] = None
                        state["hidden_until"] = 0.0
                        state["visible"] = False
                        root.withdraw()
            except queue.Empty:
                pass

            if state["desired"] and not state["visible"] and time.time() >= state["hidden_until"]:
                apply_payload(state["desired"])

            root.after(500, pump)

        root.after(0, pump)
        try:
            root.mainloop()
        except Exception:
            pass


desktop_banner = DesktopBanner()


def show_message(title: str, body: str) -> None:
    if tk is None or messagebox is None:
        return
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    messagebox.showinfo(title, body)
    root.destroy()


def get_idle_secs() -> float:
    try:
        info = LastInputInfo()
        info.cbSize = ctypes.sizeof(LastInputInfo)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
        millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
        return max(0.0, millis / 1000.0)
    except Exception:
        return 0.0


def get_active_window() -> tuple[str, str]:
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return "", ""

        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc_name = psutil.Process(pid.value).name().lower()

        title_buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.GetWindowTextW(hwnd, title_buf, 512)
        return proc_name, title_buf.value
    except Exception:
        return "", ""


def get_active_session(server_url: str, token: str, session_id: int | None = None) -> Optional[dict]:
    try:
        resp = requests.get(
            f"{server_url}/tracker/status",
            params={"token": token, "session_id": session_id},
            headers={"Authorization": f"Bearer {token}"},
            timeout=8,
        )
        if resp.status_code == 401:
            _log(f"GET {server_url}/tracker/status returned 401 Unauthorized (token_prefix={token[:6]}...)")
            return {"unauthorized": True}
        if resp.ok:
            data = resp.json()
            return data if data.get("active") or data.get("display") else None
        _log(f"GET {server_url}/tracker/status returned HTTP {resp.status_code}")
    except requests.RequestException as exc:
        _log(f"GET {server_url}/tracker/status network error: {exc}")
        return {"offline": str(exc)}
    return None


def notify_session_result(server_url: str, token: str, session_id: int) -> None:
    """Announce the server's final 70% result once across desktop and web."""
    if plyer_notification is None:
        return
    try:
        response = requests.post(
            f"{server_url}/tracker/session/{session_id}/result-notification",
            headers={"Authorization": f"Bearer {token}"}, timeout=8,
        )
        data = response.json() if response.ok else {}
        if data.get("notify"):
            show_notification(data["title"], data["message"], timeout=15)
    except requests.RequestException as exc:
        _log(f"Session result notification error: {exc}")


def send_heartbeat(
    server_url: str,
    token: str,
    session_id: int,
    is_active: bool,
    is_allowed: bool,
    current_app: str,
    elapsed_secs: int,
    study_elapsed_secs: int | None = None,
    inactivity_elapsed_secs: int | None = None,
    distraction_elapsed_secs: int | None = None,
) -> dict:
    payload = {
        "token": token,
        "session_id": session_id,
        "is_active": is_active,
        "is_allowed": is_allowed,
        "current_app": current_app,
        "elapsed_secs": elapsed_secs,
        "source": "desktop_tray",
    }
    if (
        study_elapsed_secs is not None
        or inactivity_elapsed_secs is not None
        or distraction_elapsed_secs is not None
    ):
        payload.update({
            "study_elapsed_secs": study_elapsed_secs or 0,
            "inactivity_elapsed_secs": inactivity_elapsed_secs or 0,
            "distraction_elapsed_secs": distraction_elapsed_secs or 0,
        })

    try:
        resp = requests.post(
            f"{server_url}/tracker/heartbeat",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=8,
        )
        if not resp.ok:
            _log(f"POST {server_url}/tracker/heartbeat returned HTTP {resp.status_code}")
        return resp.json() if resp.ok else {"error": f"HTTP {resp.status_code}"}
    except requests.RequestException as exc:
        _log(f"POST {server_url}/tracker/heartbeat error: {exc}")
        return {"error": str(exc)}


def start_bridge_server(shared: SharedState) -> ThreadingHTTPServer:
    class BridgeHandler(BaseHTTPRequestHandler):
        server_version = "AegisTrayBridge/1.0"

        def log_message(self, _format: str, *_args) -> None:
            return

        def _origin_allowed(self) -> bool:
            origin = (self.headers.get("Origin") or "").strip().rstrip("/")
            if not origin:
                return True
            try:
                parsed = urlparse(origin)
                return parsed.scheme in ("http", "https") and (parsed.hostname or "").lower() in (
                    "127.0.0.1",
                    "localhost",
                    "::1",
                )
            except Exception:
                return False

        def _set_headers(self, status: int = 200) -> None:
            self.send_response(status)
            origin = self.headers.get("Origin", "")
            if origin and self._origin_allowed():
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            else:
                self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Type", "application/json")
            self.end_headers()

        def _send_json(self, payload: dict, status: int = 200) -> None:
            self._set_headers(status)
            self.wfile.write(json.dumps(payload).encode("utf-8"))

        def do_OPTIONS(self) -> None:
            self._set_headers(204)

        def _path_only(self) -> str:
            """Return the path without query string."""
            return self.path.split("?", 1)[0]

        def do_GET(self) -> None:
            path = self._path_only()
            if path != "/status":
                self._send_json({"ok": False, "error": "Not found"}, 404)
                return
            status = shared.get()
            config = load_config()
            current_server = _canonical_server_url(config.get("server_url"))
            current_user = str(config.get("username") or "").strip().lower()
            qs = parse_qs(urlparse(self.path).query)
            req_server = (qs.get("server_url") or [""])[0]
            req_user = ((qs.get("username") or [""])[0]).strip().lower()
            connected = bool(config.get("token")) and status.last_error != "Unauthorized"
            connected_to_current = (
                connected
                and _canonical_server_url(req_server) == current_server
                and (not req_user or req_user == current_user)
            ) if req_server else connected
            self._send_json({
                "ok": True,
                "connected": connected,
                "connected_to_current": connected_to_current,
                "state": status.state,
                "message": status.message,
            })

        def do_POST(self) -> None:
            if self._path_only() != "/connect":
                self._send_json({"ok": False, "error": "Not found"}, 404)
                return
            if not self._origin_allowed():
                _log(f"Bridge /connect rejected origin: {self.headers.get('Origin')}")
                self._send_json({"ok": False, "error": "Origin not allowed"}, 403)
                return

            try:
                length = min(int(self.headers.get("Content-Length", "0")), 8192)
                body = self.rfile.read(length).decode("utf-8")
                data = json.loads(body or "{}")
            except (ValueError, json.JSONDecodeError):
                self._send_json({"ok": False, "error": "Invalid JSON"}, 400)
                return

            token = str(data.get("token") or "").strip()
            if not token:
                self._send_json({"ok": False, "error": "Token is required"}, 400)
                return

            try:
                config = merge_config({
                    "token": token,
                    "server_url": normalize_server_url(data.get("server_url") or DEFAULT_SERVER_URL),
                    "username": str(data.get("username") or "").strip(),
                })
                _log(
                    f"Bridge /connect saved token for user='{config.get('username')}' "
                    f"server='{config.get('server_url')}'"
                )
            except OSError as exc:
                _log(f"Bridge /connect failed to save config: {exc}")
                self._send_json({"ok": False, "error": f"Could not save config: {exc}"}, 500)
                return

            shared.update(
                state="idle",
                message="Connected to Aegis. Waiting for an active study session.",
                last_error="",
            )
            shared.wake_event.set()
            self._send_json({
                "ok": True,
                "connected": True,
                "username": config.get("username", ""),
            })

    server = ThreadingHTTPServer((BRIDGE_HOST, BRIDGE_PORT), BridgeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _log(f"Desktop bridge listening on http://{BRIDGE_HOST}:{BRIDGE_PORT}")
    return server


def create_icon_image(state: str) -> Image.Image:
    color = STATE_COLORS.get(state, STATE_COLORS["idle"])
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, 56, 56), fill=color, outline=(255, 255, 255), width=3)
    draw.rectangle((29, 18, 35, 46), fill=(255, 255, 255, 230))
    draw.rectangle((18, 29, 46, 35), fill=(255, 255, 255, 230))
    return img


def status_text(status: TrayStatus) -> str:
    labels = {
        "study": "Studying",
        "inactive": "Inactive",
        "distract": "Distracted",
        "defer": "Browser focus",
        "idle": "No active session",
        "offline": "Flask offline",
    }
    return "\n".join([
        f"State: {labels.get(status.state, status.state)}",
        f"Message: {status.message}",
        f"Current app: {status.current_app or '-'}",
        f"Study: {status.study_time}",
        f"Inactive: {status.inactivity_time}",
        f"Distracted: {status.distraction_time}",
        f"Session: {status.session_status}",
        f"Last error: {status.last_error or '-'}",
    ])


def startup_command() -> str:
    script = Path(__file__).resolve()
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'

    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else exe
    return f'"{runner}" "{script}"'


def is_autostart_enabled() -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            value, _ = winreg.QueryValueEx(key, RUN_KEY_NAME)
            return value == startup_command()
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    if winreg is None:
        show_message(APP_NAME, "Windows startup registration is only available on Windows.")
        return

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_KEY_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_KEY_NAME)
            except FileNotFoundError:
                pass


def open_smartedu() -> None:
    config = load_config()
    server_url = normalize_server_url(config.get("server_url") or DEFAULT_SERVER_URL)
    webbrowser.open(f"{server_url}/tracker")


def _mode_to_flags(mode: str) -> tuple[bool, bool]:
    """Return (is_active, is_allowed) for a tracking mode."""
    if mode == "inactive":
        return False, True
    if mode == "distract":
        return True, False
    return True, True  # study


def monitor_loop(shared: SharedState, icon_ref: dict) -> None:
    streak        = StreakState()
    allowed_cache = AllowedAppsCache()

    session: dict | None  = None   # last known active session from server
    current_mode: str | None = None  # study / inactive / distract / defer
    mode_elapsed: float  = 0.0     # seconds accumulated in current mode since last heartbeat
    active_session_id: int | None = None
    session_grace_until: float = 0.0

    last_session_poll: float = 0.0
    last_heartbeat:    float = 0.0
    last_check:        float = time.time()
    last_icon_state:   str   = ""
    last_token:        str   = ""
    unauthorized_error: bool = False

    _log(f"Monitor loop started (config_path={config_path()})")

    while not shared.stop_event.is_set():
        now           = time.time()
        check_elapsed = min(now - last_check, CHECK_INTERVAL * 4)  # cap for system sleep
        last_check    = now

        config     = load_config()
        token      = str(config.get("token") or "").strip()
        server_url = normalize_server_url(config.get("server_url") or DEFAULT_SERVER_URL)

        # ── No token ──────────────────────────────────────────────────────────
        if not token:
            last_token = ""
            if current_mode is not None:
                streak.reset()
                current_mode = None
                mode_elapsed = 0.0
                session      = None
                active_session_id = None
                session_grace_until = 0.0
            if unauthorized_error:
                shared.update(
                    state="idle",
                    message="Desktop token expired. Login to Aegis in your browser to reconnect.",
                    current_app="",
                    study_time="-",
                    inactivity_time="-",
                    distraction_time="-",
                    session_status="-",
                    last_error="Unauthorized",
                )
            else:
                shared.update(
                    state="idle",
                    message="Login to Aegis in your browser to connect desktop tracking.",
                    current_app="",
                    study_time="-",
                    inactivity_time="-",
                    distraction_time="-",
                    session_status="-",
                    last_error="",
                )
            update_icon_if_needed(icon_ref, shared, last_icon_state)
            last_icon_state = shared.get().state
            shared.wait_or_wake(CHECK_INTERVAL)  # check frequently — bridge may deliver token any second
            last_check = time.time()
            continue

        unauthorized_error = False
        token_changed = token != last_token
        if token_changed:
            _log(f"Loaded desktop token for user='{config.get('username', '')}' server='{server_url}'")
            last_token = token
            last_session_poll = 0.0

        # ── Poll server for active session ────────────────────────────────────
        # Poll immediately on token change, every CHECK_INTERVAL while idle so
        # newly started sessions are detected within seconds, and every
        # POLL_INTERVAL while a session is already active.
        poll_interval = CHECK_INTERVAL if session is None else POLL_INTERVAL
        if token_changed or (now - last_session_poll >= poll_interval):
            last_session_poll = now
            result = get_active_session(server_url, token, active_session_id)

            if result and result.get("unauthorized"):
                _log("Clearing expired/revoked desktop token after 401 response")
                clear_config()
                last_token = ""
                unauthorized_error = True
                streak.reset()
                current_mode = None
                mode_elapsed = 0.0
                session      = None
                active_session_id = None
                session_grace_until = 0.0
                shared.update(
                    state="idle",
                    message="Desktop token expired. Login to Aegis in your browser to reconnect.",
                    current_app="",
                    study_time="-",
                    inactivity_time="-",
                    distraction_time="-",
                    session_status="-",
                    last_error="Unauthorized",
                )
                update_icon_if_needed(icon_ref, shared, last_icon_state)
                last_icon_state = shared.get().state
                shared.wait_or_wake(CHECK_INTERVAL)  # re-check quickly; user may log in via bridge
                last_check = time.time()
                continue

            if result and result.get("offline"):
                streak.reset()
                current_mode = None
                mode_elapsed = 0.0
                session      = None
                active_session_id = None
                session_grace_until = 0.0
                shared.update(
                    state="offline",
                    message="Cannot reach Aegis Flask server.",
                    last_error=result.get("offline", ""),
                )
                update_icon_if_needed(icon_ref, shared, last_icon_state)
                last_icon_state = shared.get().state
                shared.wait_or_wake(CHECK_INTERVAL)  # retry quickly so recovery is fast
                last_check = time.time()
                continue

            if result and result.get("display", {}).get("status") in ("completed", "incompleted"):
                notify_session_result(server_url, token, result["session_id"])
                result = None
                active_session_id = None
            session = result  # None when no active session

        # ── No active session ─────────────────────────────────────────────────
        if not session:
            if current_mode is not None:
                streak.reset()
                current_mode = None
                mode_elapsed = 0.0
                active_session_id = None
                session_grace_until = 0.0
            shared.update(
                state="idle",
                message="No active study session.",
                current_app="",
                study_time="-",
                inactivity_time="-",
                distraction_time="-",
                session_status="-",
                last_error="",
            )
            update_icon_if_needed(icon_ref, shared, last_icon_state)
            last_icon_state = shared.get().state
            shared.wait_or_wake(CHECK_INTERVAL)  # sleep short to detect session start fast
            last_check = time.time()
            continue

        # ── Active session — detect current mode ──────────────────────────────
        session_id          = int(session["session_id"])
        if session_id != active_session_id:
            _log(f"Detected active study session id={session_id}")
            active_session_id = session_id
            session_grace_until = now + IDLE_THRESHOLD
            current_mode = None
            mode_elapsed = 0.0
            streak.reset()

        proc_name, title    = get_active_window()
        app_label           = title or proc_name or "Unknown"
        idle_secs           = 0.0
        allowed             = True

        if proc_name in BROWSER_PROCESSES:
            new_mode = "defer"
        else:
            # Rule: check app type FIRST.
            # - Distracted app → always 'distract', regardless of idle state.
            # - Study app      → 'inactive' only after 90 s of no input;
            #                    otherwise 'study'.
            idle_secs = get_idle_secs()
            explicit_list = allowed_cache.get(server_url, token)
            if not proc_name:
                allowed = True
            elif proc_name in explicit_list:
                allowed = True
            elif title:
                title_lower = title.lower()
                allowed = any(kw in title_lower for kw in ALLOWED_TITLE_KEYWORDS)
            else:
                allowed = False
            if allowed:
                if idle_secs >= IDLE_THRESHOLD and now >= session_grace_until:
                    new_mode = "inactive"
                else:
                    new_mode = "study"
            else:
                new_mode = "distract"

        mode_changed = new_mode != current_mode
        old_mode_elapsed = mode_elapsed
        new_mode_elapsed = check_elapsed
        new_streak_elapsed = check_elapsed

        if current_mode == "study" and new_mode == "inactive":
            inactive_part = min(check_elapsed, max(0.0, idle_secs - IDLE_THRESHOLD))
            study_part = max(0.0, check_elapsed - inactive_part)
            old_mode_elapsed += study_part
            new_mode_elapsed = inactive_part
            new_streak_elapsed = inactive_part

        # ── On mode change: flush heartbeat for the old mode immediately ───────
        if mode_changed and current_mode is not None and current_mode != "defer" and old_mode_elapsed > 0:
            is_active, is_allowed = _mode_to_flags(current_mode)
            hb = send_heartbeat(
                server_url=server_url, token=token, session_id=session_id,
                is_active=is_active, is_allowed=is_allowed,
                current_app=app_label, elapsed_secs=max(1, int(old_mode_elapsed)),
            )
            last_heartbeat = now
            mode_elapsed   = 0.0
            if "display" in hb:
                session = {**session, "display": hb["display"]}

        # ── Advance mode and streak ────────────────────────────────────────────
        if mode_changed:
            if new_mode in ("study", "defer"):
                streak.reset()
            current_mode = new_mode
            mode_elapsed = new_mode_elapsed
        else:
            mode_elapsed += check_elapsed

        if current_mode != "defer" and new_streak_elapsed > 0:
            streak.update(current_mode, new_streak_elapsed, proc_name)

        # ── Regular heartbeat every POLL_INTERVAL (when mode is stable) ───────
        heartbeat_result: dict = {}
        if current_mode != "defer" and (now - last_heartbeat >= POLL_INTERVAL):
            is_active, is_allowed = _mode_to_flags(current_mode)
            heartbeat_result = send_heartbeat(
                server_url=server_url, token=token, session_id=session_id,
                is_active=is_active, is_allowed=is_allowed,
                current_app=app_label, elapsed_secs=max(1, int(mode_elapsed)),
            )
            last_heartbeat = now
            mode_elapsed   = 0.0
            if "display" in heartbeat_result:
                session = {**session, "display": heartbeat_result["display"]}

        # ── Update shared state ───────────────────────────────────────────────
        display = session.get("display", {})
        if current_mode == "defer":
            shared.update(
                state="defer",
                message="Browser is focused. Extension handles this heartbeat.",
                current_app=app_label,
                study_time=display.get("study_time", "-"),
                inactivity_time=display.get("inactivity_time", "-"),
                distraction_time=display.get("distraction_time", "-"),
                session_status=display.get("status", "active"),
                last_error="",
            )
        else:
            shared.update(
                state=current_mode,
                message={
                    "study":    "Allowed desktop app is active.",
                    "inactive": "No mouse or keyboard input detected.",
                    "distract": "Focused app is not in the allowed study list.",
                }[current_mode],
                current_app=app_label,
                study_time=display.get("study_time", "-"),
                inactivity_time=display.get("inactivity_time", "-"),
                distraction_time=display.get("distraction_time", "-"),
                session_status=display.get("status", "active"),
                last_error=heartbeat_result.get("error", ""),
            )

        # ── Detect session end ────────────────────────────────────────────────
        if display.get("status") in ("completed", "incompleted"):
            notify_session_result(server_url, token, session_id)
            streak.reset()
            session      = None
            current_mode = None
            mode_elapsed = 0.0
            active_session_id = None
            session_grace_until = 0.0

        update_icon_if_needed(icon_ref, shared, last_icon_state)
        last_icon_state = shared.get().state

        shared.wait_or_wake(CHECK_INTERVAL)


def update_icon_if_needed(icon_ref: dict, shared: SharedState, previous_state: str) -> None:
    icon = icon_ref.get("icon")
    status = shared.get()
    if icon is None:
        return
    try:
        new_title = f"Aegis - {status.message}"
        if getattr(icon, "title", None) != new_title:
            icon.title = new_title
        if status.state != previous_state:
            icon.icon = create_icon_image(status.state)
    except Exception:
        pass


def parent_pid_from_args() -> int | None:
    for index, arg in enumerate(sys.argv[1:], start=1):
        if arg == "--parent-pid" and index + 1 < len(sys.argv):
            try:
                return int(sys.argv[index + 1])
            except ValueError:
                return None
    return None


def parent_watch_loop(parent_pid: int, shared: SharedState, icon_ref: dict) -> None:
    while not shared.stop_event.wait(2):
        if psutil.pid_exists(parent_pid):
            continue

        shared.stop_event.set()
        shared.wake_event.set()
        icon = icon_ref.get("icon")
        if icon:
            try:
                icon.stop()
            except Exception:
                pass
        return


def start_tray() -> None:
    shared = SharedState()
    icon_ref: dict[str, pystray.Icon] = {}
    try:
        bridge_server = start_bridge_server(shared)
    except OSError as exc:
        bridge_server = None
        _log(f"Could not bind bridge server on {BRIDGE_HOST}:{BRIDGE_PORT}: {exc}")
        shared.update(
            state="offline",
            message=f"Desktop bridge unavailable on port {BRIDGE_PORT}. Status tracking will continue, but browser reconnect may fail.",
            last_error=str(exc),
        )
    parent_pid = parent_pid_from_args()

    def show_status(_icon=None, _item=None) -> None:
        threading.Thread(
            target=lambda: show_message("Aegis Status", status_text(shared.get())),
            daemon=True,
        ).start()

    def toggle_autostart(_icon=None, _item=None) -> None:
        enabled = not is_autostart_enabled()
        set_autostart(enabled)
        show_message(APP_NAME, f"Auto-start {'enabled' if enabled else 'disabled'}.")

    def logout(icon=None, _item=None) -> None:
        _log("User clicked Logout in tray menu; clearing local config")
        clear_config()
        shared.update(
            state="idle",
            message="Logged out locally. Login to Aegis in your browser to reconnect.",
            current_app="",
            study_time="-",
            inactivity_time="-",
            distraction_time="-",
            session_status="-",
            last_error="",
        )
        shared.wake_event.set()
        update_icon_if_needed(icon_ref, shared, "")

    def exit_app(icon=None, _item=None) -> None:
        shared.stop_event.set()
        shared.wake_event.set()
        if icon:
            icon.stop()

    def auto_start_label(_item) -> str:
        return "Disable auto-start" if is_autostart_enabled() else "Enable auto-start"

    menu = Menu(
        MenuItem("Status", show_status, default=True),
        MenuItem("Open Aegis", lambda _icon, _item: open_smartedu()),
        MenuItem(auto_start_label, toggle_autostart),
        MenuItem("Logout", logout),
        MenuItem("Exit", exit_app),
    )

    icon = pystray.Icon(APP_NAME, create_icon_image("idle"), "Aegis", menu)
    icon_ref["icon"] = icon

    worker = threading.Thread(target=monitor_loop, args=(shared, icon_ref), daemon=True)
    worker.start()

    if parent_pid:
        parent_watcher = threading.Thread(
            target=parent_watch_loop,
            args=(parent_pid, shared, icon_ref),
            daemon=True,
        )
        parent_watcher.start()

    icon.run()
    shared.stop_event.set()
    shared.wake_event.set()
    if bridge_server:
        bridge_server.shutdown()
        bridge_server.server_close()
    worker.join(timeout=3)


def main() -> None:
    try:
        start_tray()
    except Exception as exc:
        _log(f"Fatal error in main: {exc}")
        show_message(APP_NAME, str(exc))


if __name__ == "__main__":
    main()
