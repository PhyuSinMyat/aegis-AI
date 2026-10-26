"""
tracker_agent.py
----------------
Core study-session tracking logic (stateless).
All state lives in the database; this module only contains pure decision logic.

Status flow:
    not_started -> active -> warning

    At session end:
        completed    (study_seconds >= 70% of planned duration)
        incompleted  (study_seconds < 70% of planned duration)

Timers:
    study_seconds       - seconds active on an allowed app/site
    inactivity_seconds  - seconds with no detectable input
    distraction_seconds - seconds active on a non-allowed app/site

Thresholds:
    Inactivity   >=  2 min -> warning
    Distraction  >=  2 min -> warning
    Study time   >= 70% of planned duration at session end -> completed
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

# Thresholds
INACTIVITY_WARN_SECS       = 2  * 60
DISTRACTION_WARN_SECS      = 2  * 60
HEARTBEAT_GAP_CAP_SECS     = 15 * 60
COMPLETED_STUDY_RATIO      = 0.70   # >= 70% of planned duration = completed
MAX_ELAPSED_SECS           = 120    # cap single heartbeat elapsed at 2 minutes

# Default allowed app / website keywords
# Checked against the active window title (case-insensitive substring match).
# The desktop monitor supplements this with the user's own study_apps list.
DEFAULT_ALLOWED_KEYWORDS: List[str] = [
    # AI assistants
    "chatgpt", "claude", "gemini", "copilot", "perplexity",
    # Reference / research
    "youtube", "google", "stackoverflow", "stack overflow",
    "github", "wikipedia", "khan academy", "coursera", "udemy", "edx",
    "quizlet", "anki", "docs.python", "developer.mozilla", "w3schools",
    # IDEs / code editors
    "visual studio code", "vs code", "vscode",
    "pycharm", "intellij", "eclipse", "sublime text", "atom",
    "notepad++", "spyder", "thonny",
    # Office / writing / slides
    "microsoft powerpoint", "powerpoint",
    "microsoft word", "libreoffice",
    "microsoft excel", "excel",
    "notepad",
    # Notebooks / terminals (all fair game for CS/tech students)
    "jupyter", "anaconda navigator",
    "terminal", "command prompt", "powershell", "bash", "zsh", "cmd",
    # Our own app
    "localhost", "127.0.0.1", "smartedu", "aegis",
]


class TrackerAgent:
    """Stateless decision engine - all methods are static."""

    @staticmethod
    def start_session(session: Dict[str, Any]) -> Dict[str, Any]:
        """Mark a session as started and initialise all counters."""
        now = datetime.now().isoformat(timespec="seconds")
        session.update({
            "actual_start": now,
            "last_heartbeat": now,
            "status": "active",
            "study_seconds": 0,
            "inactivity_seconds": 0,
            "distraction_seconds": 0,
            "current_app": "",
            "actual_end": None,
        })
        return session

    @staticmethod
    def process_heartbeat(
        session: Dict[str, Any],
        is_active: bool,
        elapsed_secs: int,
        is_allowed: bool = True,
        current_app: str = "",
    ) -> Dict[str, Any]:
        """
        Update session timers based on a heartbeat (browser or desktop monitor).

        Parameters
        ----------
        session      : mutable session dict
        is_active    : True if the user had input in the heartbeat window
        elapsed_secs : seconds since the previous heartbeat (capped at MAX_ELAPSED_SECS)
        is_allowed   : True if the active app/site is on the allowed list
        current_app  : window title reported by the desktop monitor (empty = browser only)
        """
        if session["status"] in ("completed", "incompleted"):
            return session

        elapsed_secs = max(0, min(elapsed_secs, MAX_ELAPSED_SECS))

        # Cap elapsed_secs by the actual wall-clock time since the last heartbeat.
        # This prevents double-counting when two sources (browser extension + tracker
        # page visibility-change) both report for the same time window.
        if session.get("last_heartbeat"):
            try:
                last_hb = datetime.fromisoformat(session["last_heartbeat"])
                actual_elapsed = int((datetime.now() - last_hb).total_seconds())
                elapsed_secs = min(elapsed_secs, max(0, actual_elapsed))
            except (ValueError, TypeError):
                pass

        if current_app:
            session["current_app"] = current_app

        # Every elapsed second goes to exactly one bucket.
        if not is_active:
            session["inactivity_seconds"] = session.get("inactivity_seconds", 0) + elapsed_secs
        elif not is_allowed:
            session["distraction_seconds"] = session.get("distraction_seconds", 0) + elapsed_secs
        else:
            session["study_seconds"] = session.get("study_seconds", 0) + elapsed_secs

        session["last_heartbeat"] = datetime.now().isoformat(timespec="seconds")
        return TrackerAgent._resolve_status(session)

    @staticmethod
    def process_timer_delta(
        session: Dict[str, Any],
        study_secs: int = 0,
        inactivity_secs: int = 0,
        distraction_secs: int = 0,
        current_app: str = "",
    ) -> Dict[str, Any]:
        """
        Update timers from pre-split elapsed seconds.

        This is used by clients that can split one heartbeat window across the
        90-second grace boundary. Each second is still counted in exactly one
        bucket, but the server no longer has to treat the whole heartbeat as
        either study or inactive.
        """
        if session["status"] in ("completed", "incompleted"):
            return session

        study_secs = max(0, int(study_secs or 0))
        inactivity_secs = max(0, int(inactivity_secs or 0))
        distraction_secs = max(0, int(distraction_secs or 0))

        total = study_secs + inactivity_secs + distraction_secs
        if total > MAX_ELAPSED_SECS:
            overflow = total - MAX_ELAPSED_SECS
            for bucket in ("distraction", "inactivity", "study"):
                if bucket == "distraction":
                    take = min(distraction_secs, overflow)
                    distraction_secs -= take
                elif bucket == "inactivity":
                    take = min(inactivity_secs, overflow)
                    inactivity_secs -= take
                else:
                    take = min(study_secs, overflow)
                    study_secs -= take
                overflow -= take
                if overflow <= 0:
                    break

        if session.get("last_heartbeat"):
            try:
                last_hb = datetime.fromisoformat(session["last_heartbeat"])
                actual_elapsed = max(0, int((datetime.now() - last_hb).total_seconds()))
                total = study_secs + inactivity_secs + distraction_secs
                if total > actual_elapsed:
                    overflow = total - actual_elapsed
                    for bucket in ("distraction", "inactivity", "study"):
                        if bucket == "distraction":
                            take = min(distraction_secs, overflow)
                            distraction_secs -= take
                        elif bucket == "inactivity":
                            take = min(inactivity_secs, overflow)
                            inactivity_secs -= take
                        else:
                            take = min(study_secs, overflow)
                            study_secs -= take
                        overflow -= take
                        if overflow <= 0:
                            break
            except (ValueError, TypeError):
                pass

        if current_app:
            session["current_app"] = current_app

        session["study_seconds"] = session.get("study_seconds", 0) + study_secs
        session["inactivity_seconds"] = session.get("inactivity_seconds", 0) + inactivity_secs
        session["distraction_seconds"] = session.get("distraction_seconds", 0) + distraction_secs
        session["last_heartbeat"] = datetime.now().isoformat(timespec="seconds")
        return TrackerAgent._resolve_status(session)

    @staticmethod
    def end_session(session: Dict[str, Any]) -> Dict[str, Any]:
        """End a session, marking it completed or incompleted based on the 70% threshold."""
        planned_secs = int(session.get("planned_duration_mins", 60)) * 60
        threshold    = planned_secs * COMPLETED_STUDY_RATIO
        study_secs   = session.get("study_seconds", 0)
        if study_secs >= threshold:
            session["status"] = "completed"
        else:
            session["status"] = "incompleted"
        session["actual_end"] = datetime.now().isoformat(timespec="seconds")
        return session

    @staticmethod
    def check_missed_heartbeat(session: Dict[str, Any]) -> Dict[str, Any]:
        """
        Server-side check called when the browser hasn't sent a heartbeat recently.
        Accumulates inactivity for the heartbeat gap (> 30 s since last heartbeat).
        """
        if session["status"] in ("completed", "incompleted", "not_started"):
            return session

        last = session.get("last_heartbeat")
        if not last:
            return session

        try:
            elapsed = int((datetime.now() - datetime.fromisoformat(last)).total_seconds())
        except (ValueError, TypeError):
            return session

        # Ignore negative or implausibly large gaps (e.g. system clock was changed).
        if elapsed <= 30 or elapsed < 0:
            return session
        elapsed = min(elapsed, HEARTBEAT_GAP_CAP_SECS)

        # A missed heartbeat gets the same 90-second grace rule: the first
        # 90 seconds after the last known heartbeat are not inactive seconds.
        inactive_elapsed = max(0, elapsed - 90)
        if inactive_elapsed <= 0:
            return session

        session["inactivity_seconds"] = session.get("inactivity_seconds", 0) + inactive_elapsed
        session["last_heartbeat"] = datetime.now().isoformat(timespec="seconds")
        return TrackerAgent._resolve_status(session)

    @staticmethod
    def _resolve_status(session: Dict[str, Any]) -> Dict[str, Any]:
        """Re-compute status from current timer values."""
        actual_start = session.get("actual_start")
        planned_mins = session.get("planned_duration_mins", 0)
        if actual_start and planned_mins:
            try:
                start_dt = datetime.fromisoformat(str(actual_start))
                elapsed = (datetime.now() - start_dt).total_seconds()
                if elapsed >= planned_mins * 60:
                    return TrackerAgent.end_session(session)
            except (ValueError, TypeError):
                pass

        inactivity_secs  = session.get("inactivity_seconds", 0)
        distraction_secs = session.get("distraction_seconds", 0)

        if inactivity_secs >= INACTIVITY_WARN_SECS or distraction_secs >= DISTRACTION_WARN_SECS:
            session["status"] = "warning"
            return session

        session["status"] = "active"
        return session

    @staticmethod
    def get_display_data(session: Dict[str, Any]) -> Dict[str, Any]:
        """Return a serialisable dict ready to send to the frontend."""
        study_secs = session.get("study_seconds", 0)
        inactivity_secs = session.get("inactivity_seconds", 0)
        distraction_secs = session.get("distraction_seconds", 0)
        planned_secs = int(session.get("planned_duration_mins", 60)) * 60
        status = session.get("status", "not_started")
        current_app = session.get("current_app", "")

        elapsed_secs = 0
        time_left_secs = planned_secs
        actual_start_str = session.get("actual_start")
        if actual_start_str:
            try:
                start_dt = datetime.fromisoformat(str(actual_start_str))
                elapsed_secs = max(0, int((datetime.now() - start_dt).total_seconds()))
                time_left_secs = max(0, planned_secs - elapsed_secs)
            except (ValueError, TypeError):
                pass

        def fmt(secs: int) -> str:
            m, s = divmod(int(max(secs, 0)), 60)
            h, m = divmod(m, 60)
            return f"{h}h {m:02d}m {s:02d}s" if h else f"{m:02d}m {s:02d}s"

        progress_pct = min(100, int(study_secs / max(planned_secs, 1) * 100))

        status_meta = {
            "not_started":  ("Not Started",  "#6b7280"),
            "active":       ("Active",        "#16a34a"),
            "warning":      ("Warning",       "#d97706"),
            "incompleted":  ("Incompleted",   "#dc2626"),
            "completed":    ("Completed",     "#2563eb"),
        }
        label, color = status_meta.get(status, (status.title(), "#6b7280"))

        warning_msg = ""
        if status == "active" and distraction_secs > 0:
            app_label = current_app or "a non-study app"
            warning_msg = (
                f"Distraction started: '{app_label}' for "
                f"{int(distraction_secs / 60)}m {int(distraction_secs % 60)}s. "
                "Return to the allowed app to stay focused."
            )

        if status == "warning":
            if distraction_secs >= DISTRACTION_WARN_SECS:
                app_label = current_app or "a non-study app"
                warning_msg = (
                    f"Distraction detected: '{app_label}' for "
                    f"{int(distraction_secs / 60)} min. "
                    "Please return to the allowed app to continue your study session."
                )
            else:
                mins = int(inactivity_secs / 60)
                warning_msg = (
                    f"No activity detected for {mins} minute"
                    f"{'s' if mins != 1 else ''}. "
                    "Move your mouse or press a key to resume."
                )
        elif status == "incompleted":
            planned_secs = int(session.get("planned_duration_mins", 60)) * 60
            needed_pct   = int(COMPLETED_STUDY_RATIO * 100)
            warning_msg  = (
                f"Session ended before reaching {needed_pct}% of the planned study time. "
                "Study more next time to mark it as completed."
            )

        return {
            "status": status,
            "status_label": label,
            "status_color": color,
            "module_name": session.get("module_name", "Study Session"),
            "study_time": fmt(study_secs),
            "inactivity_time": fmt(inactivity_secs),
            "distraction_time": fmt(distraction_secs),
            "progress_pct": progress_pct,
            "study_seconds": study_secs,
            "inactivity_seconds": inactivity_secs,
            "distraction_seconds": distraction_secs,
            "planned_seconds": planned_secs,
            "planned_mins": session.get("planned_duration_mins", 60),
            "elapsed_seconds": elapsed_secs,
            "time_left_seconds": time_left_secs,
            "time_left": fmt(time_left_secs),
            "warning_msg": warning_msg,
            "session_id": session.get("session_id"),
            "current_app": current_app,
        }
