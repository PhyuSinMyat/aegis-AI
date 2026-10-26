from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, Response, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

from backend.services.extraction_pipeline_service import ExtractionPipelineService
from backend.utils.template_context import build_upload_page_context
from database import DatabaseHelper

upload_bp = Blueprint("upload", __name__)
db = DatabaseHelper()

ALLOWED_EXTENSIONS = {"pdf", "csv", "xlsx"}
VALID_STUDY_TIMES = {"Morning", "Afternoon", "Night"}
VALID_INTENSITIES = {"relaxed", "balanced", "focused", "intensive", "intense"}
VALID_SESSION_LENS = {"25", "45", "60", "90"}
VALID_BREAK_PREFS = {"short", "medium", "long"}
VALID_DAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}


def allowed_file(filename: str) -> bool:
    return bool(filename and "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS)


def make_unique_file_path(folder: Path, filename: str) -> Path:
    safe_name = secure_filename(filename)
    path = folder / safe_name
    if not path.exists():
        return path
    return folder / f"{Path(safe_name).stem}_{uuid4().hex[:8]}{Path(safe_name).suffix}"


def _require_login():
    if "user_id" not in session:
        flash("Please log in first.", "danger")
        return redirect(url_for("login"))
    return None


def read_modules_from_form(form, module_file_count: int) -> list[dict]:
    modules = []
    seen_codes: set[str] = set()
    for i in range(module_file_count):
        code = form.get(f"module_code_{i}", "").strip().upper()
        name = form.get(f"module_name_{i}", "").strip()
        if not code or not name:
            raise ValueError("Please fill in both module code and module name for every uploaded file.")
        if code in seen_codes:
            raise ValueError(f"Duplicate module code detected: {code}.")
        seen_codes.add(code)
        modules.append({"module_code": code, "module_name": name})
    return modules


def read_study_apps_from_form(form) -> list[dict]:
    apps = []
    count = int(form.get("study_app_count", 0) or 0)
    for i in range(count):
        name = form.get(f"study_app_name_{i}", "").strip()
        app_type = form.get(f"study_app_type_{i}", "").strip()
        identifier = form.get(f"study_app_identifier_{i}", "").strip()
        purpose = form.get(f"study_app_purpose_{i}", "").strip()
        if name and app_type and identifier:
            apps.append({
                "name": name,
                "type": app_type,
                "identifier": identifier,
                "purpose": purpose,
            })
    return apps


def read_occupied_times_from_form(form) -> list[dict]:
    blocks = []
    count = int(form.get("occupied_count", 0) or 0)
    for i in range(count):
        title = form.get(f"occupied_title_{i}", "").strip()
        category = form.get(f"occupied_category_{i}", "").strip()
        day = form.get(f"occupied_day_{i}", "").strip()
        start_time = form.get(f"occupied_start_{i}", "").strip()
        end_time = form.get(f"occupied_end_{i}", "").strip()
        notes = form.get(f"occupied_notes_{i}", "").strip()
        if title and day and start_time and end_time:
            blocks.append({
                "title": title,
                "category": category,
                "day_of_week": day,
                "start_time": start_time,
                "end_time": end_time,
                "notes": notes,
            })
    return blocks


def save_uploaded_file(file_storage, user_folder: Path, role: str) -> dict:
    save_path = make_unique_file_path(user_folder, file_storage.filename)
    file_storage.save(save_path)
    return {
        "file_role": role,
        "original_filename": file_storage.filename,
        "stored_filename": save_path.name,
        "file_path": str(save_path),
        "file_extension": save_path.suffix.lower(),
    }


def remove_old_user_uploads(user_id: int) -> None:
    old_files = db.get_uploaded_files_by_user_id(user_id)
    for item in old_files:
        raw_path = (item.get("file_path") or "").strip()
        if not raw_path:
            continue
        try:
            path = Path(raw_path)
            if path.exists() and path.is_file():
                path.unlink()
        except OSError:
            current_app.logger.warning("[upload] Could not delete old file: %s", raw_path)




def normalize_extraction_payload(payload: dict) -> dict:
    """Normalize cached extraction payload so the review page can render safely.

    Handles both the new correct format (dict) and the older broken format where
    extraction_result was accidentally saved as a string representation.
    """
    if not isinstance(payload, dict):
        return {
            "extraction_result": {
                "modules": [],
                "class_sessions": [],
                "module_schedule": [],
                "assessments": [],
                "special_weeks": [],
                "remarks": ["Cached extraction payload is not a valid JSON object."],
            }
        }

    payload = normalize_extraction_payload(payload)
    extraction_result = payload.get("extraction_result", {})

    if isinstance(extraction_result, str):
        repaired = None
        try:
            maybe_json = json.loads(extraction_result)
            if isinstance(maybe_json, dict):
                repaired = maybe_json
        except Exception:
            repaired = None

        if repaired is None:
            repaired = {
                "modules": [],
                "class_sessions": [],
                "module_schedule": [],
                "assessments": [],
                "special_weeks": [],
                "remarks": [
                    "This extraction cache was saved using the old broken serializer, so the structured review data cannot be displayed.",
                    "Please click Process Timetable once more to regenerate and save the corrected JSON format."
                ],
            }
        payload["extraction_result"] = repaired

    for key in ("modules", "class_sessions", "module_schedule", "assessments", "special_weeks", "remarks"):
        payload.setdefault("extraction_result", {}).setdefault(key, [])

    payload.setdefault("per_file_results", [])
    payload.setdefault("uploaded_files_total", 0)
    payload.setdefault("files_sent_to_claude", 0)
    payload.setdefault("files_extracted_success", 0)
    payload.setdefault("files_extracted_error", 0)
    payload.setdefault("skipped_missing", [])
    payload.setdefault("skipped_unsupported", [])
    return payload

def build_extraction_review_context(user_id: int) -> dict | None:
    cache = db.get_extraction_cache(user_id)
    payload = db.get_extraction_result_json(user_id)

    print(f"[review] session user_id={user_id}")
    print(f"[review] cache exists={bool(cache)}")

    if not cache:
        return None
    if payload is None:
        print("[review] cache row exists but JSON payload could not be parsed")
        return None

    extraction_result = payload.get("extraction_result") or {}
    if not isinstance(extraction_result, dict):
        print(f"[review] extraction_result is not dict, got {type(extraction_result)}")
        return None

    return {
        "cached_at": cache.get("cached_at"),
        "uploaded_files": db.get_uploaded_files_by_user_id(user_id),
        "extraction_meta": {
            "uploaded_files_total": payload.get("uploaded_files_total", 0),
            "files_sent_to_claude": payload.get("files_sent_to_claude", 0),
            "files_extracted_success": payload.get("files_extracted_success", 0),
            "files_extracted_error": payload.get("files_extracted_error", 0),
            "skipped_missing": payload.get("skipped_missing", []),
            "skipped_unsupported": payload.get("skipped_unsupported", []),
        },
        "per_file_results": payload.get("per_file_results", []),
        "extracted": extraction_result,
        "summary_cards": {
            "modules": len(extraction_result.get("modules", [])),
            "class_sessions": len(extraction_result.get("class_sessions", [])),
            "module_schedule": len(extraction_result.get("module_schedule", [])),
            "assessments": len(extraction_result.get("assessments", [])),
            "special_weeks": len(extraction_result.get("special_weeks", [])),
            "remarks": len(extraction_result.get("remarks", [])),
        },
        "status": "needs_review" if extraction_result.get("remarks") else "ready",
        "raw_json": json.dumps(payload, indent=2, ensure_ascii=False),
    }


@upload_bp.route("/upload", methods=["GET", "POST"])
def upload():
    guard = _require_login()
    if guard:
        return guard

    user_id = session["user_id"]

    if request.method == "POST":
        class_timetable = request.files.get("class_timetable")
        module_timetables = [f for f in request.files.getlist("module_timetables") if f and f.filename]

        semester_start_date = request.form.get("semester_start_date", "").strip()
        semester_end_date = request.form.get("semester_end_date", "").strip()
        preferred_study_time = request.form.get("preferred_study_time", "").strip()
        study_intensity = request.form.get("study_intensity", "").strip()
        session_length = request.form.get("session_length", "").strip()
        break_preference = request.form.get("break_preference", "").strip()
        break_preference = request.form.get("break_preference", "").strip()
        study_days = "Mon,Tue,Wed,Thu,Fri,Sat,Sun"

        study_apps = read_study_apps_from_form(request.form)
        occupied_times = read_occupied_times_from_form(request.form)

        if not class_timetable or not class_timetable.filename:
            flash("Please upload your class timetable file.", "danger")
            return redirect(url_for("upload.upload"))
        if not allowed_file(class_timetable.filename):
            flash("Class timetable must be a CSV, XLSX, or PDF file.", "danger")
            return redirect(url_for("upload.upload"))
        if not module_timetables:
            flash("Please upload at least one module timetable file.", "danger")
            return redirect(url_for("upload.upload"))
        if any(not allowed_file(f.filename) for f in module_timetables):
            flash("All module timetable files must be CSV, XLSX, or PDF.", "danger")
            return redirect(url_for("upload.upload"))
        if not semester_start_date:
            flash("Please select the semester start date.", "danger")
            return redirect(url_for("upload.upload"))
        if not semester_end_date:
            flash("Please select the semester end / exam date.", "danger")
            return redirect(url_for("upload.upload"))
        if semester_end_date <= semester_start_date:
            flash("Semester end date must be after the start date.", "danger")
            return redirect(url_for("upload.upload"))
        if preferred_study_time not in VALID_STUDY_TIMES:
            flash("Please choose a preferred study time.", "danger")
            return redirect(url_for("upload.upload"))
        if study_intensity not in VALID_INTENSITIES:
            flash("Please select a study intensity level.", "danger")
            return redirect(url_for("upload.upload"))
        if study_intensity == "intense":
            study_intensity = "intensive"
        if session_length not in VALID_SESSION_LENS:
            flash("Please select a study session length.", "danger")
            return redirect(url_for("upload.upload"))
        if break_preference not in VALID_BREAK_PREFS:
            flash("Please select a break preference.", "danger")
            return redirect(url_for("upload.upload"))
        if not study_apps:
            flash("Please add at least one study app or website.", "danger")
            return redirect(url_for("upload.upload"))

        try:
            modules = read_modules_from_form(request.form, len(module_timetables))
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("upload.upload"))

        user_folder = Path(current_app.config["UPLOAD_FOLDER"]) / f"user_{user_id}"
        user_folder.mkdir(parents=True, exist_ok=True)

        try:
            remove_old_user_uploads(user_id)
            saved_files = [save_uploaded_file(class_timetable, user_folder, "class_timetable")]
            saved_files.extend(save_uploaded_file(f, user_folder, "module_timetable") for f in module_timetables)

            db.save_study_preferences(
                user_id=user_id,
                semester_start_date=semester_start_date,
                semester_end_date=semester_end_date,
                preferred_study_time=preferred_study_time,
                study_intensity=study_intensity,
                session_length=session_length,
                break_preference=break_preference,
                study_days=study_days,
            )
            db.replace_user_modules(user_id, modules)
            db.replace_study_apps(user_id, study_apps)
            db.replace_occupied_times(user_id, occupied_times)
            db.replace_uploaded_files(user_id, saved_files)
            db.clear_extraction_cache(user_id)

            pipeline = ExtractionPipelineService(db=db)
            pipeline_result = pipeline.run_for_user(user_id)
            db.save_extraction_cache(
                user_id,
                json.dumps(pipeline_result, ensure_ascii=False),
            )

            flash("Timetable extracted successfully! You can now generate your study plan.", "success")
            return redirect(url_for("plan.plan_page"))

        except Exception as exc:
            current_app.logger.exception("[upload] Timetable processing failed for user %s", user_id)
            error_message = str(exc)
            if "bedrock" in error_message.lower():
                error_message = "AWS Bedrock API error. Please check your credentials and try again."
            elif "timeout" in error_message.lower():
                error_message = "Processing timed out. Please try again with a smaller file."
            else:
                error_message = f"Failed to process timetable: {error_message}"
            flash(error_message, "danger")
            return redirect(url_for("plan.plan_page"))

    return render_template(
        "upload.html",
        current_page="upload",
        **build_upload_page_context(db, user_id),
    )


@upload_bp.route("/review-extraction", methods=["GET"])
def review_extraction():
    guard = _require_login()
    if guard:
        return guard

    context = build_extraction_review_context(session["user_id"])
    if context is None:
        flash("No extracted timetable data found. Please process your timetable first.", "warning")
        return redirect(url_for("upload.upload"))

    return render_template(
        "review_extraction.html",
        current_page="upload",
        **context,
    )


@upload_bp.route("/review-extraction/raw", methods=["GET"])
def review_extraction_raw():
    guard = _require_login()
    if guard:
        return guard

    cache = db.get_extraction_cache(session["user_id"])
    if not cache:
        flash("No extracted timetable data found.", "warning")
        return redirect(url_for("upload.upload"))

    return Response(
        cache["result_json"],
        mimetype="application/json",
        headers={"Content-Disposition": 'inline; filename="extraction_review.json"'},
    )


# ════════════════════════════════════
# PARTIAL UPDATE ROUTES
# ════════════════════════════════════

@upload_bp.route("/update-preferences", methods=["POST"])
def update_preferences():
    guard = _require_login()
    if guard: return guard
    user_id = session["user_id"]

    semester_start_date = request.form.get("semester_start_date", "").strip()
    semester_end_date = request.form.get("semester_end_date", "").strip()
    preferred_study_time = request.form.get("preferred_study_time", "").strip()
    study_intensity = request.form.get("study_intensity", "").strip()
    session_length = request.form.get("session_length", "").strip()
    break_preference = request.form.get("break_preference", "").strip()
    break_preference = request.form.get("break_preference", "").strip()
    study_days = "Mon,Tue,Wed,Thu,Fri,Sat,Sun"

    if not (semester_start_date and semester_end_date and preferred_study_time and study_intensity and session_length and break_preference):
        flash("Missing preferences. Please fill all fields.", "danger")
        return redirect(url_for("upload.upload"))

    if semester_end_date <= semester_start_date:
        flash("Semester end date must be after the start date.", "danger")
        return redirect(url_for("upload.upload"))

    db.save_study_preferences(
        user_id=user_id,
        semester_start_date=semester_start_date,
        semester_end_date=semester_end_date,
        preferred_study_time=preferred_study_time,
        study_intensity=study_intensity,
        session_length=session_length,
        break_preference=break_preference,
        study_days=study_days,
    )
    flash("Study preferences updated successfully.", "success")
    return redirect(url_for("upload.upload"))


@upload_bp.route("/update-commitments", methods=["POST"])
def update_commitments():
    guard = _require_login()
    if guard: return guard
    user_id = session["user_id"]

    occupied_times = read_occupied_times_from_form(request.form)
    db.replace_occupied_times(user_id, occupied_times)
    flash("Recurring commitments updated successfully.", "success")
    return redirect(url_for("upload.upload"))


@upload_bp.route("/update-apps", methods=["POST"])
def update_apps():
    guard = _require_login()
    if guard: return guard
    user_id = session["user_id"]

    study_apps = read_study_apps_from_form(request.form)
    db.replace_study_apps(user_id, study_apps)
    flash("Study apps updated successfully.", "success")
    return redirect(url_for("upload.upload"))

