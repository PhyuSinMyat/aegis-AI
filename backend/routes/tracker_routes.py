"""
tracker_routes.py
-----------------
Flask Blueprint for the Study Tracker Agent.

Routes
------
GET  /tracker               — render the tracker page
POST /tracker/start         — start a new study session
POST /tracker/heartbeat     — receive activity heartbeat (browser or desktop monitor)
POST /tracker/end           — manually end the active session
GET  /tracker/status        — JSON: current session state (supports token auth)
GET  /tracker/schedule      — JSON: today's sessions from the latest study plan
GET  /tracker/monitor-token — JSON: generate a 24-hour auth token for monitor.py
"""
from __future__ import annotations

from datetime import datetime, date

from flask import (
    Blueprint, current_app, flash, jsonify, redirect,
    render_template, request, session, url_for,
)
from urllib.parse import urlparse

from backend.agents.tracker_agent import TrackerAgent
from backend.utils.template_context import build_user_page_context
from database import DatabaseHelper

tracker_bp = Blueprint("tracker", __name__)
db = DatabaseHelper()


def _safe_positive_int(value, default: int, *, max_value: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    number = max(1, number)
    if max_value is not None:
        number = min(number, max_value)
    return number


def _safe_nonnegative_int(value, default: int = 0, *, max_value: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    number = max(0, number)
    if max_value is not None:
        number = min(number, max_value)
    return number


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _require_login():
    """Redirect guard for browser routes that need a session cookie."""
    if "user_id" not in session:
        flash("Please log in first.", "danger")
        return redirect(url_for("login"))
    return None


def _extract_request_token() -> str | None:
    """Extract monitor token from query string, JSON body, or Authorization header."""
    body = request.get_json(silent=True) or {}
    token = (request.args.get("token") or body.get("token") or "").strip()
    if token:
        return token
    auth_header = (request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip() or None
    return None


def _get_user_id_any() -> int | None:
    """
    Accept auth from either a Flask session cookie OR a monitor token.
    Token can be in the query-string (?token=…), JSON body {"token": …},
    or Authorization: Bearer <token> header.
    Returns user_id int or None if unauthenticated.
    """
    if "user_id" in session:
        return session["user_id"]
    token = _extract_request_token()
    if token:
        user_id = db.get_user_id_from_token(token)
        if user_id is None:
            print(
                f"[TrackerAuth] Rejected invalid/expired token on {request.path} "
                f"(token_prefix={token[:6]}...)"
            )
        return user_id
    print(f"[TrackerAuth] Unauthenticated request to {request.path} (no session or token)")
    return None


def sync_desktop_tracker_config(
    user_id: int,
    username: str | None = None,
    server_url: str | None = None,
) -> dict:
    """
    Ensure %APPDATA%\\Aegis\\config.json has a valid monitor token for the
    logged-in user so smartedu_tray.py and monitor.py stay connected even if
    the browser bridge request is blocked or the page was not reloaded.
    """
    import json
    import os
    from pathlib import Path

    resolved_username = (username or session.get("username") or "").strip()
    if not resolved_username:
        user_obj = db.get_user_by_id(user_id)
        if user_obj:
            resolved_username = (user_obj.get_username() or "").strip()
            try:
                session["username"] = resolved_username
            except RuntimeError:
                pass

    raw_url = (server_url or "").strip()
    if not raw_url:
        try:
            raw_url = request.host_url.rstrip("/")
        except RuntimeError:
            raw_url = "http://127.0.0.1:5000"
    resolved_server_url = raw_url.rstrip("/") or "http://127.0.0.1:5000"

    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    cfg_dir = Path(appdata) / "Aegis"
    cfg_path = cfg_dir / "config.json"

    existing_cfg: dict = {}
    if cfg_path.exists():
        try:
            existing_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[TrackerSync] Could not read existing {cfg_path}: {exc}")

    existing_token = str(existing_cfg.get("token") or "").strip()
    if existing_token and db.get_user_id_from_token(existing_token) == user_id:
        token = existing_token
    else:
        token = db.get_or_create_monitor_token(user_id, ttl_hours=24 * 365 * 10)
        print(f"[TrackerSync] Provisioned desktop token for user_id={user_id} ({resolved_username})")

    new_cfg = {
        "token": token,
        "server_url": resolved_server_url,
        "username": resolved_username,
    }
    if existing_cfg != new_cfg or not cfg_path.exists():
        try:
            cfg_dir.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(new_cfg, indent=2), encoding="utf-8")
            print(
                f"[TrackerSync] Synced desktop config at {cfg_path} "
                f"(user={resolved_username}, server={resolved_server_url})"
            )
        except OSError as exc:
            print(f"[TrackerSync] Could not write {cfg_path}: {exc}")

    return new_cfg


# ── Retroactive missed-session helper ────────────────────────────────────────

def _get_current_week_study_plan(user_id: int):
    from backend.services.summary_scheduler import _get_plan_for_week
    from backend.agents.planning_agent import compute_academic_week
    from datetime import date
    
    preferences = db.get_study_preferences_by_user_id(user_id) or {}
    current_week = compute_academic_week(
        preferences.get("semester_start_date", ""),
        date.today().isoformat()
    )
    plan = _get_plan_for_week(db, user_id, current_week)
    return plan or db.get_latest_study_plan_by_user_id(user_id)


def _end_stale_active_session(user_id: int) -> None:
    """
    If there is an active session in the DB but its scheduled slot end time
    has already passed (browser was closed before auto-end fired), end it now.
    Only ends if the session was started BEFORE the slot ended (genuinely stale).
    """
    active = db.get_active_session(user_id)
    if not active:
        return

    plan = _get_current_week_study_plan(user_id)
    if not plan or not plan.get("timetable_json"):
        return

    today_name = datetime.now().strftime("%A")
    now_hhmm   = datetime.now().strftime("%H:%M")

    actual_start_str = active.get("actual_start") or ""
    session_start_hhmm = ""
    if actual_start_str and "T" in str(actual_start_str):
        try:
            session_start_hhmm = str(actual_start_str).split("T")[1][:5]
        except Exception:
            pass

    for slot in plan["timetable_json"]:
        if slot.get("day", "").strip().capitalize() != today_name:
            continue
        if slot.get("subject") != active["module_name"]:
            continue
        slot_start = slot.get("start", "")
        slot_end = slot.get("end", "")
        if not slot_end or slot_end > now_hhmm:
            return

        if session_start_hhmm and session_start_hhmm >= slot_end:
            continue

        ended = TrackerAgent.end_session(dict(active))
        db.update_session(ended)
        db.log_session_event(
            active["session_id"], user_id,
            "ended", "Auto-ended: slot time passed while browser was closed"
        )
        return


def _auto_replan_missed_session(session_id: int, user_id: int, missed_data: dict, current_plan: dict) -> None:
    """
    Automatically trigger replanning for a missed session.
    The replanning agent will decide whether to reschedule based on importance.
    """
    # Check if already replanned to avoid duplicate processing
    if db.is_session_replanned(session_id):
        print(f"[Auto-Replan] Session {session_id} already replanned, skipping.")
        return

    print(f"[Auto-Replan] Starting replanning for session {session_id}, module={missed_data.get('module_name')}")
    try:
        from backend.agents.replanning_agent import evaluate_and_replan

        result = evaluate_and_replan(
            current_timetable=current_plan.get("timetable_json", []),
            missed_session=missed_data
        )

        if result.get("is_rescheduled"):
            patched = result.get("patched_timetable")
            explanation = result.get("explanation", "Session was automatically rescheduled.")
            print(f"[Auto-Replan] Updating plan {current_plan['plan_id']} with {len(patched)} sessions")
            db.update_study_plan_timetable(current_plan["plan_id"], patched, f"[Auto-replan] {explanation}")
            db.log_session_event(session_id, user_id, "replanned", f"[Auto] {explanation}")
            print(f"[Auto-Replan] Session {session_id} successfully replanned and saved to database")
            # Store notification for frontend to display
            session["pending_replan_notification"] = {
                "session_id": session_id,
                "module": missed_data.get("module_name"),
                "explanation": explanation,
                "rescheduled": True
            }
        else:
            # Agent decided not to reschedule (low priority)
            explanation = result.get("explanation", "Session skipped due to low priority.")
            print(f"[Auto-Replan] Session {session_id} not rescheduled: {explanation}")
            db.log_session_event(session_id, user_id, "replanned", f"[Auto] {explanation}")
            session["pending_replan_notification"] = {
                "session_id": session_id,
                "module": missed_data.get("module_name"),
                "explanation": explanation,
                "rescheduled": False
            }
    except Exception as e:
        print(f"[Auto-Replan] Failed for session {session_id}: {e}")
        current_app.logger.exception("Auto-replan failed for session %s", session_id)
        # Don't block the main flow if replanning fails


def _record_missed_slots(user_id: int) -> None:
    """
    Check today's study plan for slots that have already ended.
    For any slot with no matching session in the DB, insert an 'incompleted' record
    so the student can see what they skipped, then automatically trigger replanning.
    """
    plan = _get_current_week_study_plan(user_id)
    if not plan or not plan.get("timetable_json"):
        return

    today_name = datetime.now().strftime("%A")
    today_str  = date.today().isoformat()          # "YYYY-MM-DD"
    now_hhmm   = datetime.now().strftime("%H:%M")

    missed_slots = []
    for slot in plan["timetable_json"]:
        if slot.get("day", "").strip().capitalize() != today_name:
            continue
        slot_end = slot.get("end", "")
        if not slot_end or slot_end > now_hhmm:
            continue  # slot hasn't ended yet

        module_name = slot.get("subject", "Study Session")
        slot_start  = slot.get("start", "")
        slot_end_t  = slot.get("end", "")
        if db.session_exists_for_slot_any(user_id, module_name, today_str,
                                          slot_start=slot_start, slot_end=slot_end_t):
            continue  # already recorded for this specific slot

        try:
            sh, sm = map(int, slot["start"].split(":"))
            eh, em = map(int, slot["end"].split(":"))
            planned_mins = max(1, (eh * 60 + em) - (sh * 60 + sm))
        except Exception:
            planned_mins = 60

        missed_data = {
            "user_id":               user_id,
            "module_name":           module_name,
            "planned_duration_mins": planned_mins,
            "status":                "incompleted",
            "study_seconds":         0,
            "inactivity_seconds":    0,
            "distraction_seconds":   0,
            "current_app":           "",
            "actual_start":          f"{today_str}T{slot['start']}:00",
            "actual_end":            f"{today_str}T{slot_end}:00",
            "last_heartbeat":        None,
        }
        session_id = db.insert_study_session(missed_data)
        db.log_session_event(session_id, user_id, "incompleted", "Never started — laptop not opened")
        missed_slots.append((session_id, missed_data))

    if missed_slots:
        import threading
        from flask import current_app
        app = current_app._get_current_object()

        def _background_replan(app, user_id, missed_slots):
            with app.app_context():
                for sid, mdata in missed_slots:
                    fp = _get_current_week_study_plan(user_id)
                    if not fp:
                        break
                    _auto_replan_missed_session(sid, user_id, mdata, fp)

        threading.Thread(
            target=_background_replan,
            args=(app, user_id, missed_slots),
            daemon=True,
        ).start()


# ── GET /tracker ──────────────────────────────────────────────────────────────

@tracker_bp.route("/tracker", methods=["GET"])
def tracker_page():
    guard = _require_login()
    if guard:
        return guard

    user_id = session["user_id"]
    sync_desktop_tracker_config(user_id)

    # ── End stale active sessions whose scheduled slot has passed ─────────────
    # If the browser was closed mid-session, the auto-end JS never fired.
    # End any active session whose slot end time is now in the past.
    _end_stale_active_session(user_id)

    # ── Retroactive missed-session check ──────────────────────────────────────
    _record_missed_slots(user_id)

    active_session = db.get_active_session(user_id)

    # ── Backfill inactivity for time the browser was closed ───────────────────
    # check_missed_heartbeat applies the same 90-second grace rule before adding
    # inactive time for gaps while the browser was closed.
    if active_session:
        active_session = TrackerAgent.check_missed_heartbeat(dict(active_session))
        db.update_session(active_session)

    recent = db.get_recent_sessions(user_id, limit=10)

    for s in recent:
        s["display"] = TrackerAgent.get_display_data(s)
        s["is_replanned"] = db.is_session_replanned(s["session_id"])

    # Retrieve and clear pending replan notification
    replan_notification = session.pop("pending_replan_notification", None)

    return render_template(
        "tracker.html",
        current_page="tracker",
        active_session=active_session,
        active_display=(
            TrackerAgent.get_display_data(active_session)
            if active_session else None
        ),
        recent_sessions=recent,
        replan_notification=replan_notification,
        **build_user_page_context(db, user_id),
    )


# ── POST /tracker/start ───────────────────────────────────────────────────────

@tracker_bp.route("/tracker/start", methods=["POST"])
def start_session():
    guard = _require_login()
    if guard:
        return guard

    user_id = session["user_id"]
    sync_desktop_tracker_config(user_id)

    body         = request.get_json(silent=True) or {}
    module_name  = (body.get("module_name") or "General Study").strip()
    planned_mins = _safe_positive_int(body.get("planned_duration_mins"), 60, max_value=12 * 60)
    today_str    = date.today().isoformat()

    # ── Step 1: If there's already an active session for this exact module, return it ──
    existing_active = db.get_active_session(user_id)
    if existing_active and existing_active["module_name"] == module_name:
        return jsonify({
            "ok":         True,
            "session_id": existing_active["session_id"],
            "display":    TrackerAgent.get_display_data(existing_active),
        })

    # ── Step 2: If a non-missed session already exists for THIS SPECIFIC SLOT, don't recreate ──
    # Keyed on (module_name + slot time range) so two same-subject slots in one day
    # are treated independently (e.g., two Cybersecurity slots at different times).
    slot_start_str = body.get("slot_start") or ""  # "HH:MM"
    slot_end_str   = body.get("slot_end")   or ""  # "HH:MM"
    if db.session_exists_for_slot(user_id, module_name, today_str,
                                  slot_start=slot_start_str, slot_end=slot_end_str):
        # Tell the browser "all good, nothing to do" — no reload needed
        return jsonify({"ok": True, "session_id": None})

    # ── Step 3: End any other active session (different module) ──
    if existing_active:
        ended = TrackerAgent.end_session(dict(existing_active))
        db.update_session(ended)

    session_data = {
        "user_id":               user_id,
        "module_name":           module_name,
        "planned_duration_mins": planned_mins,
        "status":                "not_started",
        "study_seconds":         0,
        "inactivity_seconds":    0,
        "distraction_seconds":   0,
        "current_app":           "",
        "actual_start":          None,
        "actual_end":            None,
        "last_heartbeat":        None,
    }
    # TrackerAgent.start_session sets timestamps, resets counters, and flips
    # status to active. Timers begin from the user's actual tracked mode.
    session_data = TrackerAgent.start_session(session_data)
    session_id = db.insert_study_session(session_data)
    session_data["session_id"] = session_id

    db.log_session_event(session_id, user_id, "started", f"Module: {module_name}")

    return jsonify({
        "ok":         True,
        "session_id": session_id,
        "display":    TrackerAgent.get_display_data(session_data),
    })


# ── POST /tracker/heartbeat ───────────────────────────────────────────────────

@tracker_bp.route("/tracker/heartbeat", methods=["POST"])
def heartbeat():
    user_id = _get_user_id_any()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    if "user_id" in session:
        sync_desktop_tracker_config(user_id)

    body         = request.get_json(silent=True) or {}
    session_id   = body.get("session_id")
    is_active    = bool(body.get("is_active", True))
    elapsed_secs = _safe_nonnegative_int(body.get("elapsed_secs"), 30, max_value=120)
    is_allowed   = bool(body.get("is_allowed", True))   # desktop monitor only
    current_app  = str(body.get("current_app", ""))     # desktop monitor only
    has_timer_delta = any(
        key in body
        for key in ("study_elapsed_secs", "inactivity_elapsed_secs", "distraction_elapsed_secs")
    )

    if not session_id:
        return jsonify({"error": "No session_id provided."}), 400

    try:
        session_id_int = int(session_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid session_id."}), 400

    sess = db.get_session_by_id(session_id_int)
    if not sess or sess["user_id"] != user_id:
        return jsonify({"error": "Session not found."}), 404

    if sess["status"] in ("completed", "incompleted"):
        return jsonify({"ok": True, "display": TrackerAgent.get_display_data(sess)})

    prev_status = sess["status"]
    if has_timer_delta:
        updated = TrackerAgent.process_timer_delta(
            dict(sess),
            study_secs=_safe_nonnegative_int(body.get("study_elapsed_secs"), 0, max_value=120),
            inactivity_secs=_safe_nonnegative_int(body.get("inactivity_elapsed_secs"), 0, max_value=120),
            distraction_secs=_safe_nonnegative_int(body.get("distraction_elapsed_secs"), 0, max_value=120),
            current_app=current_app,
        )
    else:
        updated = TrackerAgent.process_heartbeat(
            dict(sess), is_active, elapsed_secs,
            is_allowed=is_allowed, current_app=current_app,
        )
    db.update_session(updated)

    new_status = updated["status"]
    if new_status != prev_status:
        db.log_session_event(
            session_id_int, user_id,
            f"status_{new_status}",
            f"Changed from {prev_status}",
        )

    return jsonify({"ok": True, "display": TrackerAgent.get_display_data(updated)})


# ── POST /tracker/end ─────────────────────────────────────────────────────────

@tracker_bp.route("/tracker/end", methods=["POST"])
def end_session():
    guard = _require_login()
    if guard:
        return guard

    user_id    = session["user_id"]
    body       = request.get_json(silent=True) or {}
    session_id = body.get("session_id")

    if session_id:
        try:
            session_id_int = int(session_id)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid session_id."}), 400
        sess = db.get_session_by_id(session_id_int)
    else:
        sess = db.get_active_session(user_id)

    if not sess or sess["user_id"] != user_id:
        return jsonify({"error": "Session not found."}), 404

    updated = TrackerAgent.end_session(dict(sess))
    db.update_session(updated)
    db.log_session_event(sess["session_id"], user_id, "ended", "Manual end")

    return jsonify({"ok": True, "display": TrackerAgent.get_display_data(updated)})


# ── DELETE /tracker/session/<id> ─────────────────────────────────────────────

@tracker_bp.route("/tracker/session/<int:session_id>", methods=["DELETE"])
def delete_session(session_id):
    guard = _require_login()
    if guard:
        return guard

    user_id = session["user_id"]
    sess    = db.get_session_by_id(session_id)

    if not sess or sess["user_id"] != user_id:
        return jsonify({"error": "Session not found."}), 404

    db.delete_session(session_id)
    return jsonify({"ok": True})


# ── POST /tracker/session/<id>/replan ────────────────────────────────────────

@tracker_bp.route("/tracker/session/<int:session_id>/replan", methods=["POST"])
def replan_session(session_id):
    guard = _require_login()
    if guard:
        return guard

    user_id = session["user_id"]
    sess = db.get_session_by_id(session_id)

    if not sess or sess["user_id"] != user_id:
        return jsonify({"error": "Session not found."}), 404

    if sess["status"] != "incompleted":
        return jsonify({"error": "Only incompleted/missed sessions can be replanned."}), 400

    if db.is_session_replanned(session_id):
        return jsonify({"error": "This session has already been replanned."}), 400

    current_plan = _get_current_week_study_plan(user_id)
    if not current_plan:
        return jsonify({"error": "No study plan found to replan against."}), 400

    from backend.agents.replanning_agent import evaluate_and_replan

    result = evaluate_and_replan(
        current_timetable=current_plan.get("timetable_json", []),
        missed_session=sess
    )
    
    if result.get("is_rescheduled"):
        patched = result.get("patched_timetable")
        explanation = result.get("explanation", "Session was automatically rescheduled.")
        db.update_study_plan_timetable(current_plan["plan_id"], patched, explanation)
        db.log_session_event(session_id, user_id, "replanned", explanation)
        return jsonify({"ok": True, "explanation": explanation})
    else:
        # Not rescheduled
        explanation = result.get("explanation", "Could not find a suitable slot to reschedule.")
        db.log_session_event(session_id, user_id, "replanned", explanation)
        return jsonify({"ok": False, "error": explanation})



# ── GET /tracker/status ───────────────────────────────────────────────────────

@tracker_bp.route("/tracker/status", methods=["GET"])
def get_status():
    user_id = _get_user_id_any()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    # A client may still be watching a session ended by another client.
    watched_id = request.args.get("session_id", type=int)
    if watched_id:
        watched = db.get_session_by_id(watched_id)
        if watched and watched["user_id"] == user_id and watched["status"] in ("completed", "incompleted"):
            return jsonify({
                "active": False,
                "session_id": watched_id,
                "display": TrackerAgent.get_display_data(watched),
            })

    sess = db.get_active_session(user_id)
    if not sess:
        return jsonify({"active": False})

    return jsonify({
        "active":     True,
        "session_id": sess["session_id"],
        "display":    TrackerAgent.get_display_data(sess),
    })


@tracker_bp.route("/tracker/session/<int:session_id>/result-notification", methods=["POST"])
def claim_result_notification(session_id):
    user_id = _get_user_id_any()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    sess = db.get_session_by_id(session_id)
    if not sess or sess["user_id"] != user_id:
        return jsonify({"error": "Session not found."}), 404
    if sess["status"] not in ("completed", "incompleted"):
        return jsonify({"notify": False})

    display = TrackerAgent.get_display_data(sess)
    completed = sess["status"] == "completed"
    return jsonify({
        "notify": db.claim_session_result_notification(session_id, user_id),
        "title": "Congratulations!" if completed else "Study Session Missed",
        "message": (
            f"{display['module_name']}: {display['study_time']} studied. "
            + ("You reached at least 70% of your planned study time. Great job!"
               if completed else "You studied less than 70% of your planned study time.")
        ),
    })


# ── DELETE /tracker/history/all ──────────────────────────────────────────────

def _normalize_identifier(identifier):
    value = (identifier or '').strip().lower()
    if not value:
        return ''

    # strip URL scheme and path
    if value.startswith('http://') or value.startswith('https://'):
        try:
            parsed = urlparse(value)
            value = parsed.hostname or value
        except Exception:
            pass

    if value.startswith('www.'):
        value = value[4:]

    if '/' in value:
        value = value.split('/')[0]

    return value


_DEFAULT_ALLOWED_WEBSITES = [
    "youtube.com", "google.com", "google.co.uk",
    "stackoverflow.com", "stackexchange.com",
    "github.com", "gitlab.com", "bitbucket.org",
    "wikipedia.org",
    "khanacademy.org", "coursera.org", "udemy.com", "edx.org",
    "quizlet.com", "w3schools.com", "developer.mozilla.org",
    "docs.python.org", "geeksforgeeks.org", "medium.com",
    "chatgpt.com", "chat.openai.com", "claude.ai", "gemini.google.com",
    "perplexity.ai", "copilot.microsoft.com",
    "notion.so", "docs.google.com", "drive.google.com",
    "learn.microsoft.com",
]

_DEFAULT_ALLOWED_DESKTOP = [
    "code.exe",
    "pycharm64.exe", "pycharm.exe",
    "intellij64.exe", "idea64.exe",
    "notepad.exe", "notepad++.exe",
    "winword.exe", "excel.exe", "powerpnt.exe", "onenote.exe",
    "powershell.exe", "cmd.exe", "windowsterminal.exe",
    "python.exe", "pythonw.exe",
    "jupyter-notebook.exe", "jupyter-lab.exe",
    "sublime_text.exe", "atom.exe",
    "explorer.exe",
    "acrobat.exe", "acrord32.exe",
]


@tracker_bp.route("/tracker/allowed-apps", methods=["GET"])
def get_allowed_apps():
    """Return the user's saved study-site identifiers for tracker clients."""
    user_id = _get_user_id_any()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    study_apps = db.get_study_apps_by_user_id(user_id)
    allowed_identifiers = []   # website domains  (browser extension)
    desktop_identifiers  = []  # process names    (desktop tracker, e.g. "code.exe")

    for app in study_apps:
        app_type   = (app.get("type") or "").strip().lower()
        identifier = _normalize_identifier(app.get("identifier"))
        if not identifier:
            continue
        if app_type in ("website", "both"):
            allowed_identifiers.append(identifier)
        if app_type in ("desktop", "both"):
            desktop_identifiers.append(identifier.lower())

    if not allowed_identifiers:
        allowed_identifiers = list(_DEFAULT_ALLOWED_WEBSITES)
    if not desktop_identifiers:
        desktop_identifiers = list(_DEFAULT_ALLOWED_DESKTOP)

    return jsonify({
        "allowed_identifiers": allowed_identifiers,
        "desktop_identifiers":  desktop_identifiers,
        "study_apps":           study_apps,
    })


# ── POST /tracker/desktop-apps  (add a desktop app) ──────────────────────────

@tracker_bp.route("/tracker/desktop-apps", methods=["POST"])
def add_desktop_app():
    """Add a new allowed desktop application for the current user."""
    guard = _require_login()
    if guard:
        return guard

    user_id = session["user_id"]
    body    = request.get_json(silent=True) or {}
    name    = (body.get("name") or "").strip()
    process = (body.get("process_name") or "").strip().lower()

    if not name or not process:
        return jsonify({"error": "name and process_name are required"}), 400

    # Ensure .exe suffix on Windows process names
    if not process.endswith(".exe") and "." not in process:
        process += ".exe"

    db.upsert_study_app(user_id, name, "desktop", process, "")
    return jsonify({"ok": True})


# ── DELETE /tracker/desktop-apps/<identifier> ─────────────────────────────────

@tracker_bp.route("/tracker/desktop-apps/<path:identifier>", methods=["DELETE"])
def remove_desktop_app(identifier):
    """Remove a desktop app from the allowed list."""
    guard = _require_login()
    if guard:
        return guard

    user_id = session["user_id"]
    db.delete_study_app_by_identifier(user_id, identifier.lower())
    return jsonify({"ok": True})


@tracker_bp.route("/tracker/history/all", methods=["DELETE"])
def clear_all_history():
    guard = _require_login()
    if guard:
        return guard
    user_id = session["user_id"]
    db.clear_session_history(user_id)
    return jsonify({"ok": True})


# ── GET /tracker/schedule ─────────────────────────────────────────────────────

@tracker_bp.route("/tracker/schedule", methods=["GET"])
def get_schedule():
    """Return today's planned study sessions from the user's most recent plan."""
    user_id = _get_user_id_any()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    plan = _get_current_week_study_plan(user_id)
    if not plan or not plan.get("timetable_json"):
        print(f"[Schedule] No plan found for user {user_id}")
        return jsonify({"sessions": [], "next_session": None, "today": ""})

    print(f"[Schedule] Fetched plan {plan.get('plan_id')} for user {user_id}, timetable has {len(plan['timetable_json'])} total sessions")

    today_name = datetime.now().strftime("%A")   # "Monday", "Tuesday", …
    now_hhmm   = datetime.now().strftime("%H:%M")

    # Filter sessions for today (planning schema uses 'day', 'start', 'end', 'subject')
    today_sessions = [
        s for s in plan["timetable_json"]
        if s.get("day", "").strip().capitalize() == today_name
    ]
    today_sessions.sort(key=lambda x: x.get("start", ""))

    print(f"[Schedule] Today ({today_name}) has {len(today_sessions)} sessions")

    # Next upcoming session = first whose end time hasn't passed yet
    upcoming      = [s for s in today_sessions if s.get("end", "23:59") > now_hhmm]
    next_session  = upcoming[0] if upcoming else None

    return jsonify({
        "today":        today_name,
        "now":          now_hhmm,
        "sessions":     today_sessions,
        "next_session": next_session,
    })


# ── GET /tracker/monitor-token ────────────────────────────────────────────────

@tracker_bp.route("/tracker/monitor-token", methods=["GET"])
def get_monitor_token():
    """Generate or reuse a monitor token so monitor.py can authenticate without a cookie."""
    guard = _require_login()
    if guard:
        return guard

    user_id = session["user_id"]
    cfg = sync_desktop_tracker_config(user_id)
    return jsonify({"ok": True, "token": cfg["token"]})


@tracker_bp.route("/tracker/desktop-bridge-token", methods=["GET"])
def get_desktop_bridge_token():
    """Generate or reuse a long-lived token for the installed desktop tray app."""
    guard = _require_login()
    if guard:
        return guard

    user_id = session["user_id"]
    cfg = sync_desktop_tracker_config(user_id, server_url=request.host_url.rstrip("/"))
    return jsonify({
        "ok": True,
        "token": cfg["token"],
        "username": cfg["username"],
        "server_url": cfg["server_url"],
    })

