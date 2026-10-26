"""
plan_routes.py
──────────────
Plan page + planning-agent generation routes.

Flow:
- Extraction runs earlier from the upload flow and is cached in DB.
- This file reads the cached extraction payload + user preferences/modules/
  occupied times from DB.
- It builds the planning prompt and streams the generated result back to the UI.
- TWO-WEEK MODE: generates current week AND next week plans in a single SSE stream.
  Week 1 streams first, signals week1_done, then Week 2 streams, signals done.
  Each week is independently planned and saved to DB with its own plan_id.

History behaviour:
- The database may contain multiple saved rows for the same academic week.
- In the UI history list, only the latest saved row for each week is shown.
- The 2-week display and generation logic remain unchanged.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    stream_with_context,
    url_for,
)

from backend.agents.planning_agent import (
    BREAK_LENGTH_MINS,
    _normalize_day_name,
    _parse_iso_date,
    build_class_priority_text,
    build_study_plan_prompt,
    compute_academic_week,
    extract_plan_sections,
    format_extraction_for_prompt,
    is_term_break_week,
    stream_study_plan,
)
from backend.schemas.extraction_schema import ExtractionResult as ExtractionSchema
from database import DatabaseHelper

plan_bp = Blueprint("plan", __name__)
db = DatabaseHelper()


def _require_login():
    if "user_id" not in session:
        flash("Please log in first.", "danger")
        return redirect(url_for("login"))
    return None


def _load_cached_extraction_result(user_id: int) -> tuple[ExtractionSchema | None, str | None]:
    payload = db.get_extraction_result_json(user_id)
    if not payload:
        return None, "No processed timetable found. Please go back to Upload and click Process Timetable."

    extraction_result = payload.get("extraction_result")
    if not isinstance(extraction_result, dict):
        return None, "Saved timetable data is invalid. Please process your timetable again."

    try:
        return ExtractionSchema(**extraction_result), None
    except Exception:
        return None, "Saved timetable data is invalid. Please process your timetable again."


def _build_default_plan_title(user_context: dict, today_str: str | None = None) -> str:
    preferences = (user_context or {}).get("preferences", {}) or {}
    semester_start_date = preferences.get("semester_start_date", "")
    current_week_number = compute_academic_week(semester_start_date, today_str)
    if current_week_number is None:
        return "Study Plan"
    return f"Week {current_week_number} Study Plan"


def _week_today_str(sem_start: str, week_number: int, real_today_str: str) -> str:
    """
    Return a synthetic date string that falls in the given academic week.
    Used so build_study_plan_prompt computes the correct week number for
    the target week without changing any underlying logic.
    """
    sem_start_date = _parse_iso_date(sem_start)
    if sem_start_date is None:
        return real_today_str
    target_date = sem_start_date + timedelta(weeks=week_number - 1)
    return target_date.strftime("%Y-%m-%d (%A)")


def _build_prompt_for_week(
    user_context: dict,
    extraction_result: ExtractionSchema,
    week_number: int,
    real_today_str: str,
) -> tuple[str, bool]:
    """
    Build the planning prompt scoped to a specific academic week.
    Returns (prompt_str, is_term_break).
    """
    sem_start = (user_context.get("preferences", {}) or {}).get("semester_start_date", "")
    synthetic_today = _week_today_str(sem_start, week_number, real_today_str)

    formatted = format_extraction_for_prompt(
        extraction_result,
        current_week_number=week_number,
        user_modules=user_context.get("modules", []) or [],
    )

    week_is_term_break: bool = formatted.get("is_term_break", False)

    class_priority_text = build_class_priority_text(
        user_context=user_context,
        extraction_result=extraction_result,
    )

    prompt = build_study_plan_prompt(
        user_context=user_context,
        class_sessions_text=formatted["class_sessions_text"],
        assessments_text=formatted["assessments_text"],
        module_schedule_text=formatted["module_schedule_text"],
        current_week_module_schedule_text=formatted["current_week_module_schedule_text"],
        upcoming_assessments_text=formatted["upcoming_assessments_text"],
        class_priority_text=class_priority_text,
        week_planning_focus_text=formatted.get("week_planning_focus_text", ""),
        today_date=synthetic_today,
        target_week_number=week_number,
    )

    return prompt, week_is_term_break


def _extract_week_number_from_title(title: str) -> int | None:
    text = (title or "").strip()
    match = re.search(r"(?i)\bweek\s*(\d+)\b", text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _calculate_timetable_hours(timetable_json: list) -> float:
    total_mins = 0
    for s in timetable_json or []:
        try:
            sh, sm = map(int, s.get("start", "0:0").split(":"))
            eh, em = map(int, s.get("end", "0:0").split(":"))
            dur = (eh * 60 + em) - (sh * 60 + sm)
            if dur > 0:
                total_mins += dur
        except Exception:
            pass
    return round(total_mins / 60.0, 1)


def _serialize_plan_row(row: dict | None) -> dict | None:
    if not row:
        return None

    full_text = row.get("plan_text", "") or ""
    timetable = row.get("timetable_json", []) or []
    return {
        "plan_id": row.get("plan_id"),
        "title": row.get("title", "") or "",
        "created_at": row.get("created_at", "") or "",
        "plan_text": full_text,
        "timetable_json": timetable,
        "scheduled_hours": _calculate_timetable_hours(timetable),
        "sections": extract_plan_sections(full_text),
        "week_number": _extract_week_number_from_title(row.get("title", "") or ""),
    }


def _build_latest_saved_plan_history(rows: list[dict] | None) -> list[dict]:
    """
    Keep only the newest saved row for each academic week in the visible history UI.
    Newest is determined by larger plan_id.
    """
    rows = rows or []
    latest_by_week: dict[int, dict] = {}
    fallback_rows: list[dict] = []

    for row in rows:
        week_number = _extract_week_number_from_title(row.get("title", "") or "")
        if week_number is None:
            fallback_rows.append(row)
            continue

        existing = latest_by_week.get(week_number)
        if existing is None:
            latest_by_week[week_number] = row
            continue

        try:
            if int(row.get("plan_id", 0)) > int(existing.get("plan_id", 0)):
                latest_by_week[week_number] = row
        except Exception:
            latest_by_week[week_number] = row

    history_items: list[dict] = []

    for week_number, row in latest_by_week.items():
        history_items.append({
            "plan_id": row.get("plan_id"),
            "title": row.get("title", "") or f"Week {week_number} Study Plan",
            "created_at": row.get("created_at", "") or "",
            "week_number": week_number,
            "status_label": "Latest saved",
            "status_kind": "latest",
        })

    for row in fallback_rows:
        history_items.append({
            "plan_id": row.get("plan_id"),
            "title": row.get("title", "") or "Study Plan",
            "created_at": row.get("created_at", "") or "",
            "week_number": None,
            "status_label": "Saved",
            "status_kind": "saved",
        })

    def sort_key(item: dict):
        week_number = item.get("week_number")
        if isinstance(week_number, int):
            return (0, -week_number, -(int(item.get("plan_id") or 0)))
        return (1, 0, -(int(item.get("plan_id") or 0)))

    history_items.sort(key=sort_key)
    return history_items


def _build_saved_plan_bundle(user_id: int, clicked_plan_id: int) -> dict | None:
    clicked_row = db.get_study_plan_by_id(clicked_plan_id)
    if not clicked_row:
        return None
    if clicked_row.get("user_id") != user_id:
        return None

    all_rows = db.get_study_plans_by_user_id(user_id) or []

    week_map: dict[int, dict] = {}
    for row in all_rows:
        week_num = _extract_week_number_from_title(row.get("title", "") or "")
        if week_num is None:
            continue

        existing = week_map.get(week_num)
        if existing is None:
            week_map[week_num] = row
        else:
            try:
                if int(row.get("plan_id", 0)) > int(existing.get("plan_id", 0)):
                    week_map[week_num] = row
            except Exception:
                pass

    clicked_week = _extract_week_number_from_title(clicked_row.get("title", "") or "")

    left_row = clicked_row
    right_row = None
    active_week = 1

    if clicked_week is not None:
        next_row_meta = week_map.get(clicked_week + 1)
        prev_row_meta = week_map.get(clicked_week - 1)

        # Important Week 0 pairing rule:
        # if Week 1 has a Week 0 partner, prefer showing Week 0 + Week 1
        # instead of accidentally pairing Week 1 with an old Week 2 row.
        if clicked_week == 1 and prev_row_meta and prev_row_meta.get("plan_id") != clicked_plan_id:
            prev_row = db.get_study_plan_by_id(prev_row_meta["plan_id"])
            if prev_row and prev_row.get("user_id") == user_id:
                left_row = prev_row
                right_row = clicked_row
                active_week = 2
        elif next_row_meta and next_row_meta.get("plan_id") != clicked_plan_id:
            right_row = db.get_study_plan_by_id(next_row_meta["plan_id"])
            active_week = 1
        elif prev_row_meta and prev_row_meta.get("plan_id") != clicked_plan_id:
            prev_row = db.get_study_plan_by_id(prev_row_meta["plan_id"])
            if prev_row and prev_row.get("user_id") == user_id:
                left_row = prev_row
                right_row = clicked_row
                active_week = 2

    if right_row and right_row.get("user_id") != user_id:
        right_row = None
        active_week = 1

    prefs = db.get_study_preferences_by_user_id(user_id) or {}
    intensity_key = str(prefs.get("study_intensity", "balanced")).strip().lower()
    from backend.agents.planning_agent import INTENSITY_HOURS
    intensity_label, hrs_min, hrs_max = INTENSITY_HOURS.get(intensity_key, ("Steady Progress", 16, 25))

    return {
        "primary": _serialize_plan_row(left_row),
        "secondary": _serialize_plan_row(right_row),
        "active_week": active_week,
        "clicked_plan_id": clicked_plan_id,
        "intensity_label": intensity_label,
        "target_hrs_min": hrs_min,
        "target_hrs_max": hrs_max,
        "study_intensity": intensity_key,
    }


@plan_bp.route("/plan", methods=["GET"])
def plan_page():
    guard = _require_login()
    if guard:
        return guard

    user_id = session["user_id"]
    extraction_result, extraction_error = _load_cached_extraction_result(user_id)
    setup_ok = db.is_setup_complete(user_id) and extraction_result is not None

    if not setup_ok:
        return redirect(url_for("upload.upload"))

    user_context = db.get_full_user_context(user_id) or {}
    today_str = date.today().strftime("%Y-%m-%d (%A)")
    default_plan_title = _build_default_plan_title(user_context, today_str)

    raw_saved_plans = db.get_study_plans_by_user_id(user_id) or []
    latest_saved_plans = _build_latest_saved_plan_history(raw_saved_plans)

    return render_template(
        "plan.html",
        current_page="plan",
        setup_ok=setup_ok,
        extraction_ready=extraction_result is not None,
        extraction_error=extraction_error,
        preferences=db.get_study_preferences_by_user_id(user_id),
        modules=db.get_user_modules_by_user_id(user_id),
        saved_plans=latest_saved_plans,
        default_plan_title=default_plan_title,
    )


@plan_bp.route("/plan/generate", methods=["POST"])
def generate_plan():
    guard = _require_login()
    if guard:
        return guard

    user_id = session["user_id"]

    if not db.is_setup_complete(user_id):
        return jsonify({"error": "Please complete upload and timetable processing first."}), 400

    extraction_result, extraction_error = _load_cached_extraction_result(user_id)
    if extraction_error or extraction_result is None:
        return jsonify({"error": extraction_error or "Processed timetable data is missing."}), 400

    user_context = db.get_full_user_context(user_id)
    if not user_context:
        return jsonify({"error": "Could not load your saved study preferences and profile data."}), 500

    today = date.today()
    today_str = today.strftime("%Y-%m-%d (%A)")

    body = request.get_json(silent=True) or {}
    requested_plan_title = (body.get("plan_title") or "").strip()

    def sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    def generate():
        sem_start = (user_context.get("preferences", {}) or {}).get("semester_start_date", "")
        current_week_number = compute_academic_week(sem_start, today_str) or 1

        week1_num = current_week_number
        week2_num = current_week_number + 1

        next_week_number = week2_num

        week1_title = requested_plan_title or f"Week {week1_num} Study Plan"
        week2_title = f"Week {week2_num} Study Plan"

        # ── WEEK 1 ────────────────────────────────────────────────────────────
        try:
            prompt_w1, w1_is_term_break = _build_prompt_for_week(
                user_context=user_context,
                extraction_result=extraction_result,
                week_number=week1_num,
                real_today_str=today_str,
            )

            # Adjust title to reflect term-break mode
            if w1_is_term_break:
                week1_title = requested_plan_title or f"Week {week1_num} Term Break Plan"

            yield sse({
                "status": f"Aegis is generating Week {week1_num} {'(Term Break) ' if w1_is_term_break else ''}plan…",
                "current_week_number": week1_num,
                "next_week_number": next_week_number,
                "plan_title": week1_title,
                "week2_title": week2_title,
                "is_term_break": w1_is_term_break,
            })

        except Exception as exc:
            current_app.logger.exception("Failed to prepare Week %s prompt", week1_num)
            yield sse({"error": f"Failed to prepare Week {week1_num} plan. Please try again in a moment."})
            return

        try:
            week1_done_payload = None
            for chunk in stream_study_plan(
                prompt=prompt_w1,
                user_context=user_context,
                extraction_result=extraction_result,
            ):
                if "error" in chunk:
                    current_app.logger.error("Week %s planning stream error: %s", week1_num, chunk["error"])
                    yield sse({"error": f"Week {week1_num} generation failed. Please try again in a moment."})
                    return
                if "text" in chunk:
                    yield sse({"text": chunk["text"], "week": 1})
                if chunk.get("done"):
                    week1_done_payload = chunk
                    break

            if not week1_done_payload:
                yield sse({"error": "Week 1 stream ended without a result."})
                return

            timetable_w1 = week1_done_payload.get("timetable_json", [])
            sections_w1  = week1_done_payload.get("sections", {})
            full_text_w1 = week1_done_payload.get("full_text", "")

            try:
                plan_id_w1 = db.save_study_plan(
                    user_id=user_id,
                    title=week1_title,
                    plan_text=full_text_w1,
                    timetable_json=timetable_w1,
                )
            except Exception as exc:
                plan_id_w1 = None
                current_app.logger.exception("Week %s generated but could not be saved", week1_num)
                yield sse({"warning": f"Week {week1_num} generated but could not be saved. Please try again in a moment."})

            yield sse({
                "week1_done": True,
                "plan_id": plan_id_w1,
                "plan_title": week1_title,
                "week_number": week1_num,
                "timetable_json": timetable_w1,
                "scheduled_hours": week1_done_payload.get("scheduled_hours", _calculate_timetable_hours(timetable_w1)),
                "target_hrs_min": week1_done_payload.get("target_hrs_min"),
                "target_hrs_max": week1_done_payload.get("target_hrs_max"),
                "intensity_label": week1_done_payload.get("intensity_label"),
                "limitation_note": week1_done_payload.get("limitation_note"),
                "sections": sections_w1,
                "full_text": full_text_w1,
                "current_week_number": week1_num,
                "next_week_number": next_week_number,
                "is_term_break": w1_is_term_break,
            })

        except Exception as exc:
            current_app.logger.exception("Week %s streaming failed", week1_num)
            yield sse({"error": f"Week {week1_num} streaming failed. Please try again in a moment."})
            return

        # ── WEEK 2 ────────────────────────────────────────────────────────────
        if week2_num is None:
            yield sse({"done": True, "current_week_number": week1_num, "next_week_number": None})
            return

        try:
            prompt_w2, w2_is_term_break = _build_prompt_for_week(
                user_context=user_context,
                extraction_result=extraction_result,
                week_number=week2_num,
                real_today_str=today_str,
            )

            # Adjust Week 2 title if it's a term-break week
            if w2_is_term_break:
                week2_title = f"Week {week2_num} Term Break Plan"

            yield sse({
                "status": f"Aegis is generating Week {week2_num} {'(Term Break) ' if w2_is_term_break else ''}plan…",
                "is_term_break_w2": w2_is_term_break,
            })

        except Exception as exc:
            current_app.logger.exception("Failed to prepare Week %s prompt", week2_num)
            yield sse({"error": f"Failed to prepare Week {week2_num} plan. Please try again in a moment."})
            return

        try:
            week2_done_payload = None
            for chunk in stream_study_plan(
                prompt=prompt_w2,
                user_context=user_context,
                extraction_result=extraction_result,
            ):
                if "error" in chunk:
                    current_app.logger.error("Week %s planning stream error: %s", week2_num, chunk["error"])
                    yield sse({"error": f"Week {week2_num} generation failed. Please try again in a moment."})
                    return
                if "text" in chunk:
                    yield sse({"text": chunk["text"], "week": 2})
                if chunk.get("done"):
                    week2_done_payload = chunk
                    break

            if not week2_done_payload:
                yield sse({"error": "Week 2 stream ended without a result."})
                return

            timetable_w2 = week2_done_payload.get("timetable_json", [])
            sections_w2  = week2_done_payload.get("sections", {})
            full_text_w2 = week2_done_payload.get("full_text", "")

            try:
                plan_id_w2 = db.save_study_plan(
                    user_id=user_id,
                    title=week2_title,
                    plan_text=full_text_w2,
                    timetable_json=timetable_w2,
                )
            except Exception as exc:
                plan_id_w2 = None
                current_app.logger.exception("Week %s generated but could not be saved", week2_num)
                yield sse({"warning": f"Week {week2_num} generated but could not be saved. Please try again in a moment."})

            yield sse({
                "done": True,
                "week2_done": True,
                "plan_id_w2": plan_id_w2,
                "plan_title_w2": week2_title,
                "week_number_w2": week2_num,
                "timetable_json_w2": timetable_w2,
                "scheduled_hours_w2": week2_done_payload.get("scheduled_hours", _calculate_timetable_hours(timetable_w2)),
                "target_hrs_min_w2": week2_done_payload.get("target_hrs_min"),
                "target_hrs_max_w2": week2_done_payload.get("target_hrs_max"),
                "intensity_label_w2": week2_done_payload.get("intensity_label"),
                "limitation_note_w2": week2_done_payload.get("limitation_note"),
                "sections_w2": sections_w2,
                "full_text_w2": full_text_w2,
                "current_week_number": week1_num,
                "next_week_number": next_week_number,
                "is_term_break_w2": w2_is_term_break,
            })

        except Exception as exc:
            current_app.logger.exception("Week %s streaming failed", week2_num)
            yield sse({"error": f"Week {week2_num} streaming failed. Please try again in a moment."})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@plan_bp.route("/plan/<int:plan_id>", methods=["GET"])
def get_plan(plan_id: int):
    guard = _require_login()
    if guard:
        return guard

    row = db.get_study_plan_by_id(plan_id)
    if not row:
        return jsonify({"error": "Plan not found."}), 404
    if row["user_id"] != session["user_id"]:
        return jsonify({"error": "Forbidden."}), 403

    full_text = row.get("plan_text", "") or ""
    row["sections"] = extract_plan_sections(full_text)

    return jsonify(row)


@plan_bp.route("/plan/<int:plan_id>/bundle", methods=["GET"])
def get_plan_bundle(plan_id: int):
    guard = _require_login()
    if guard:
        return guard

    user_id = session["user_id"]
    bundle = _build_saved_plan_bundle(user_id, plan_id)
    if bundle is None:
        return jsonify({"error": "Plan not found."}), 404

    return jsonify(bundle)


@plan_bp.route("/plan/<int:plan_id>", methods=["DELETE"])
def delete_plan(plan_id: int):
    guard = _require_login()
    if guard:
        return guard

    row = db.get_study_plan_by_id(plan_id)
    if not row:
        return jsonify({"error": "Plan not found."}), 404
    if row["user_id"] != session["user_id"]:
        return jsonify({"error": "Forbidden."}), 403

    db.delete_study_plan(plan_id)
    return jsonify({"ok": True})


@plan_bp.route("/plan/latest", methods=["GET"])
def get_latest_plans():
    """
    Get the latest 2-week plan bundle for the current user.
    Returns the most recent Week N and Week N+1 plans.
    """
    guard = _require_login()
    if guard:
        return guard

    user_id = session["user_id"]

    # Get all plans for this user
    all_plans = db.get_study_plans_by_user_id(user_id) or []

    if not all_plans:
        return jsonify({"error": "No plans found"}), 404

    # Get the most recent plan (highest plan_id)
    latest_plan = max(all_plans, key=lambda p: p.get("plan_id", 0))

    # Build a bundle using the latest plan
    bundle = _build_saved_plan_bundle(user_id, latest_plan["plan_id"])

    if not bundle:
        return jsonify({"error": "Could not build plan bundle"}), 404

    preferences = db.get_study_preferences_by_user_id(user_id) or {}
    current_week = compute_academic_week(
        preferences.get("semester_start_date", ""),
        date.today().isoformat(),
    )
    primary_week = (bundle.get("primary") or {}).get("week_number")
    secondary_week = (bundle.get("secondary") or {}).get("week_number")
    if current_week is not None:
        if primary_week == current_week:
            bundle["active_week"] = 1
        elif secondary_week == current_week:
            bundle["active_week"] = 2
        else:
            bundle["active_week"] = 1
    else:
        bundle["active_week"] = 1

    return jsonify(bundle)


@plan_bp.route("/plan/update", methods=["POST"])
def update_plan():
    """
    Update a study plan's timetable JSON after drag-and-drop changes.
    """
    guard = _require_login()
    if guard:
        return guard

    user_id = session["user_id"]
    body = request.get_json(silent=True) or {}

    plan_id = body.get("plan_id")
    timetable_json = body.get("timetable_json")

    print(f"[UPDATE_PLAN] Received update request for plan_id={plan_id}, user_id={user_id}")
    print(f"[UPDATE_PLAN] Timetable has {len(timetable_json) if isinstance(timetable_json, list) else 0} sessions")

    if not plan_id:
        print("[UPDATE_PLAN] Error: Missing plan_id")
        return jsonify({"error": "Missing plan_id"}), 400

    if not isinstance(timetable_json, list):
        print("[UPDATE_PLAN] Error: Invalid timetable_json type")
        return jsonify({"error": "Invalid timetable_json - must be a list"}), 400

    # Verify the plan belongs to this user
    plan = db.get_study_plan_by_id(plan_id)
    if not plan:
        print(f"[UPDATE_PLAN] Error: Plan {plan_id} not found")
        return jsonify({"error": "Plan not found"}), 404

    if plan["user_id"] != user_id:
        print(f"[UPDATE_PLAN] Error: Plan {plan_id} belongs to user {plan['user_id']}, not {user_id}")
        return jsonify({"error": "Forbidden"}), 403

    # Update the timetable_json field
    try:
        db.update_study_plan_timetable(plan_id, timetable_json)
        print(f"[UPDATE_PLAN] Successfully updated plan {plan_id}")
        return jsonify({"ok": True, "message": "Schedule updated successfully"})
    except Exception as exc:
        current_app.logger.exception("Failed to update plan %s", plan_id)
        return jsonify({"error": "Failed to update plan. Please try again in a moment."}), 500


@plan_bp.route("/plan/blocked-times", methods=["GET"])
def get_blocked_times():
    """
    Return class sessions and occupied times organized by day,
    plus the user's break preference in minutes.
    Used by the frontend to validate drag-and-drop time selections.
    """
    guard = _require_login()
    if guard:
        return guard

    user_id = session["user_id"]

    extraction_result, _ = _load_cached_extraction_result(user_id)
    class_sessions = []
    if extraction_result:
        data = extraction_result.model_dump() if hasattr(extraction_result, "model_dump") else {}
        class_sessions = data.get("class_sessions", []) or []

    occupied_times = db.get_occupied_times_by_user_id(user_id) or []

    prefs = db.get_study_preferences_by_user_id(user_id) or {}
    break_pref = prefs.get("break_preference", "medium")
    break_mins = BREAK_LENGTH_MINS.get(str(break_pref).strip().lower(), 15)

    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    blocked_by_day = {day: [] for day in days}

    for s in class_sessions:
        day = _normalize_day_name(s.get("day", ""))
        if day in blocked_by_day:
            blocked_by_day[day].append({
                "start": s.get("start_time", ""),
                "end": s.get("end_time", ""),
                "label": s.get("module_name", "") or s.get("module_code", "") or "Class",
                "type": "class",
            })

    for o in occupied_times:
        day = _normalize_day_name(o.get("day_of_week", ""))
        if day in blocked_by_day:
            blocked_by_day[day].append({
                "start": o.get("start_time", ""),
                "end": o.get("end_time", ""),
                "label": o.get("title", "") or "Commitment",
                "type": "commitment",
            })

    return jsonify({
        "blocked_by_day": blocked_by_day,
        "break_mins": break_mins,
    })
