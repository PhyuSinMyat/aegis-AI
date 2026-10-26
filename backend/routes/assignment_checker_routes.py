"""
assignment_checker_routes.py
----------------------------
Flask Blueprint for the Assignment Checker feature.

Routes
------
GET  /assignment-checker                          — render the page
POST /assignment-checker/extract-brief            — extract text from uploaded rubric file
POST /assignment-checker/extract-instructions     — extract text from uploaded instructions file
POST /assignment-checker/analyse                  — analyse pasted text (JSON)
POST /assignment-checker/upload                   — upload submission file(s)
GET  /assignment-checker/history                  — list all history entries for current user
GET  /assignment-checker/history/<id>             — fetch a single entry (full result)
DELETE /assignment-checker/history/<id>           — delete a single entry
"""
from __future__ import annotations

import json

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from backend.agents.assignment_checker_agent import AssignmentCheckerAgent
from backend.utils.template_context import build_user_page_context
from database import DatabaseHelper

assignment_checker_bp = Blueprint("assignment_checker", __name__)
db = DatabaseHelper()

_MAX_BRIEF_LENGTH = 30_000
_MAX_UPLOAD_BYTES = 50_000_000
_SNIPPET_LEN      = 300

# Server-side caches for non-text content blocks (images / native doc blocks)
_brief_image_cache:        dict[int, list] = {}
_instructions_image_cache: dict[int, list] = {}


def _require_login():
    if "user_id" not in session:
        flash("Please log in first.", "danger")
        return redirect(url_for("login"))
    return None


def _file_suffix(filename: str) -> str:
    filename = secure_filename(filename)
    return ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""


def _describe_unreadable_file(filename: str, size_bytes: int, field_name: str) -> str:
    safe_name = secure_filename(filename) or "uploaded_file"
    return (
        f"[Uploaded {field_name}: {safe_name}]\n"
        f"Size: {size_bytes:,} bytes.\n"
        "The file was accepted, but its contents could not be extracted as text, "
        "a supported document, an image, a notebook, a presentation, or readable UTF-8 text."
    )


def _read_uploaded_content(file_storage, field_name: str) -> tuple[str, list, str | None]:
    """Returns (text, content_blocks, error). content_blocks may be image or native doc blocks."""
    if not file_storage or not file_storage.filename:
        return "", [], f"No {field_name} file provided."
    suffix = _file_suffix(file_storage.filename)
    try:
        file_bytes = file_storage.read()
        if len(file_bytes) > _MAX_UPLOAD_BYTES:
            return "", [], (
                f"The {field_name} file is too large. Assignment Checker files must be "
                "50 MB or smaller."
            )
        try:
            text, content_blocks = AssignmentCheckerAgent.extract_content_from_file(
                file_bytes, suffix, file_storage.filename
            )
        except ValueError:
            try:
                text = file_bytes.decode("utf-8")
                content_blocks = []
            except UnicodeDecodeError:
                text = _describe_unreadable_file(file_storage.filename, len(file_bytes), field_name)
                content_blocks = []
    except Exception as exc:
        return "", [], f"Could not read {field_name}: {exc}"
    if not text and not content_blocks:
        text = _describe_unreadable_file(file_storage.filename, len(file_bytes), field_name)
    return text, content_blocks, None


def _read_uploaded_submission_files(file_storages) -> tuple[str, list, list[str], str | None]:
    """Read one or more submission files into labelled text plus native/image blocks."""
    files = [f for f in file_storages if f and f.filename]
    if not files:
        return "", [], [], "No submission file provided."

    text_parts: list[str] = []
    content_blocks: list = []
    filenames: list[str] = []

    for index, file_storage in enumerate(files, start=1):
        filename = file_storage.filename
        text, blocks, err = _read_uploaded_content(file_storage, f"submission file '{filename}'")
        if err:
            return "", [], [], err

        filenames.append(filename)
        safe_label = secure_filename(filename) or f"submission_file_{index}"
        if text.strip():
            text_parts.append(
                f"SUBMISSION FILE {index}: {safe_label}\n"
                f"---\n"
                f"{text.strip()}"
            )
        elif blocks:
            text_parts.append(
                f"SUBMISSION FILE {index}: {safe_label}\n"
                "[Attached as a native document or image content block.]"
            )
        content_blocks.extend(blocks)

    return "\n\n".join(text_parts), content_blocks, filenames, None


def _read_uploaded_context_files(file_storages) -> tuple[str, list, list[str], str | None]:
    """Read one or more rubric/instruction files into labelled context text plus native/image blocks."""
    files = [f for f in file_storages if f and f.filename]
    if not files:
        return "", [], [], "No context file provided."

    text_parts: list[str] = []
    content_blocks: list = []
    filenames: list[str] = []

    for index, file_storage in enumerate(files, start=1):
        filename = file_storage.filename
        text, blocks, err = _read_uploaded_content(file_storage, f"context file '{filename}'")
        if err:
            return "", [], [], err

        filenames.append(filename)
        safe_label = secure_filename(filename) or f"context_file_{index}"
        if text.strip():
            text_parts.append(
                f"CONTEXT FILE {index}: {safe_label}\n"
                f"---\n"
                f"{text.strip()}"
            )
        elif blocks:
            text_parts.append(
                f"CONTEXT FILE {index}: {safe_label}\n"
                "[Attached as a native document or image content block.]"
            )
        content_blocks.extend(blocks)

    return "\n\n".join(text_parts), content_blocks, filenames, None


def _friendly_error(exc: Exception) -> str:
    msg = str(exc)
    if "maximum document size is 4.5 MB" in msg or "document size is 4.5 MB" in msg:
        return (
            "One uploaded file could not be sent directly to the AI reader. "
            "Please try a PDF, DOCX, Excel, text/code file, or compress the file and try again."
        )
    if "InvalidSignatureException" in msg or "Signature not yet current" in msg:
        return (
            "AWS request failed due to a system clock mismatch. "
            "Go to Windows Settings → Time & Language → Date & Time → 'Sync now', then try again."
        )
    if "ExpiredTokenException" in msg or "ExpiredToken" in msg:
        return "AWS credentials have expired. Please refresh your AWS credentials in the .env file."
    if "UnrecognizedClientException" in msg or "InvalidClientTokenId" in msg:
        return "AWS credentials are invalid. Check AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env."
    if "AccessDeniedException" in msg or "not authorized" in msg.lower():
        return "AWS access denied. Ensure your IAM user has Bedrock Converse permission."
    if "ThrottlingException" in msg or "Too Many Requests" in msg:
        return "AWS is rate-limiting requests. Please wait a moment and try again."
    if "ModelNotReadyException" in msg or "ModelErrorException" in msg:
        return "The AI model is temporarily unavailable. Please try again in a moment."
    return f"Analysis failed: {exc}"


def _make_title(submission_text: str, filename: str = "") -> str:
    if filename:
        name = secure_filename(filename)
        if "." in name:
            name = name.rsplit(".", 1)[0]
        return name.replace("_", " ").replace("-", " ")[:80]
    first = submission_text.strip().split("\n")[0].strip()
    return (first[:60] + "…") if len(first) > 60 else (first or "Assignment Check")


def _make_upload_title(submission_text: str, filenames: list[str]) -> str:
    if len(filenames) == 1:
        return _make_title(submission_text, filenames[0])
    if filenames:
        first = secure_filename(filenames[0])
        if "." in first:
            first = first.rsplit(".", 1)[0]
        first = first.replace("_", " ").replace("-", " ") or "Assignment"
        return f"{first[:50]} + {len(filenames) - 1} more file{'s' if len(filenames) != 2 else ''}"
    return _make_title(submission_text)


def _save_history(
    user_id: int,
    title: str,
    strictness: str,
    result: dict,
    submission_text: str,
    brief_text: str,
) -> None:
    try:
        db.save_assignment_history(
            user_id=user_id,
            title=title,
            strictness=strictness,
            rubric_mode=bool(result.get("rubric_mode")),
            overall_score=result.get("overall_score"),
            grade=result.get("grade"),
            total_marks_awarded=result.get("total_marks_awarded"),
            total_marks_possible=result.get("total_marks_possible"),
            submission_snippet=submission_text[:_SNIPPET_LEN],
            brief_snippet=brief_text[:_SNIPPET_LEN] if brief_text else "",
            result_json=json.dumps(result, ensure_ascii=False),
        )
    except Exception:
        pass


# ── Page ──────────────────────────────────────────────────────────────────────

@assignment_checker_bp.route("/assignment-checker")
def assignment_checker_page():
    guard = _require_login()
    if guard:
        return guard
    user_id = session["user_id"]
    return render_template(
        "assignment_checker.html",
        current_page="assignment_checker",
        **build_user_page_context(db, user_id),
    )


# ── Extract rubric/brief text ──────────────────────────────────────────────────

@assignment_checker_bp.route("/assignment-checker/extract-brief", methods=["POST"])
def extract_brief():
    if "user_id" not in session:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401

    brief_file = request.files.get("brief_file")
    text, content_blocks, err = _read_uploaded_content(brief_file, "rubric/brief")
    if err:
        return jsonify({"ok": False, "error": err}), 400
    if len(text) > _MAX_BRIEF_LENGTH:
        text = text[:_MAX_BRIEF_LENGTH]

    user_id = session["user_id"]
    if content_blocks:
        _brief_image_cache[user_id] = content_blocks
    else:
        _brief_image_cache.pop(user_id, None)

    return jsonify({"ok": True, "brief_text": text, "has_native_doc": bool(content_blocks)})


# ── Extract instructions text ──────────────────────────────────────────────────

@assignment_checker_bp.route("/assignment-checker/extract-instructions", methods=["POST"])
def extract_instructions():
    if "user_id" not in session:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401

    instr_file = request.files.get("instructions_file")
    text, content_blocks, err = _read_uploaded_content(instr_file, "instructions")
    if err:
        return jsonify({"ok": False, "error": err}), 400
    if len(text) > _MAX_BRIEF_LENGTH:
        text = text[:_MAX_BRIEF_LENGTH]

    user_id = session["user_id"]
    if content_blocks:
        _instructions_image_cache[user_id] = content_blocks
    else:
        _instructions_image_cache.pop(user_id, None)

    return jsonify({"ok": True, "instructions_text": text, "has_native_doc": bool(content_blocks)})


@assignment_checker_bp.route("/assignment-checker/extract-context", methods=["POST"])
def extract_context():
    if "user_id" not in session:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401

    text, content_blocks, filenames, err = _read_uploaded_context_files(
        request.files.getlist("context_file")
    )
    if err:
        return jsonify({"ok": False, "error": err}), 400
    if len(text) > _MAX_BRIEF_LENGTH:
        text = text[:_MAX_BRIEF_LENGTH]

    user_id = session["user_id"]
    if content_blocks:
        _brief_image_cache[user_id] = content_blocks
    else:
        _brief_image_cache.pop(user_id, None)

    return jsonify({
        "ok": True,
        "context_text": text,
        "filenames": filenames,
        "has_native_doc": bool(content_blocks),
        "native_doc_count": len(content_blocks),
    })


@assignment_checker_bp.route("/assignment-checker/clear-context", methods=["POST"])
def clear_context():
    if "user_id" not in session:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401

    user_id = session["user_id"]
    _brief_image_cache.pop(user_id, None)
    _instructions_image_cache.pop(user_id, None)
    return jsonify({"ok": True})


# ── Analyse pasted text ────────────────────────────────────────────────────────

@assignment_checker_bp.route("/assignment-checker/analyse", methods=["POST"])
def analyse_text():
    if "user_id" not in session:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401

    data = request.get_json(silent=True) or {}
    assignment_text   = (data.get("assignment_text") or "").strip()
    strictness        = (data.get("strictness") or "strict").strip().lower()
    brief_text        = (data.get("brief_text") or "").strip() or None
    requirements      = (data.get("requirements") or "").strip() or None
    instructions_text = (data.get("instructions_text") or "").strip() or None

    if not assignment_text:
        return jsonify({"ok": False, "error": "Assignment text is required."}), 400
    if strictness not in {"lenient", "normal", "strict"}:
        strictness = "strict"

    user_id = session["user_id"]
    brief_content_blocks        = _brief_image_cache.get(user_id) or []
    instructions_content_blocks = _instructions_image_cache.get(user_id) or []

    # Rubric is compulsory
    if not brief_text and not brief_content_blocks:
        return jsonify({
            "ok": False,
            "error": "Rubric/brief is required. Please upload or paste the marking rubric before analysing.",
        }), 400

    try:
        result = AssignmentCheckerAgent.analyse(
            assignment_text=assignment_text,
            strictness=strictness,
            brief_text=brief_text,
            requirements=requirements,
            instructions_text=instructions_text,
            brief_image_blocks=brief_content_blocks or None,
            instructions_image_blocks=instructions_content_blocks or None,
        )
        _save_history(
            user_id=user_id,
            title=_make_title(assignment_text),
            strictness=strictness,
            result=result,
            submission_text=assignment_text,
            brief_text=brief_text or "",
        )
        return jsonify({"ok": True, "result": result})
    except Exception as exc:
        return jsonify({"ok": False, "error": _friendly_error(exc)}), 500


# ── Upload submission file ─────────────────────────────────────────────────────

@assignment_checker_bp.route("/assignment-checker/upload", methods=["POST"])
def upload_and_analyse():
    if "user_id" not in session:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401

    submission_files = request.files.getlist("file")
    assignment_text, submission_content_blocks, filenames, err = _read_uploaded_submission_files(
        submission_files
    )
    if err:
        return jsonify({"ok": False, "error": err}), 400
    strictness        = (request.form.get("strictness") or "strict").strip().lower()
    if strictness not in {"lenient", "normal", "strict"}:
        strictness = "strict"
    brief_text        = (request.form.get("brief_text") or "").strip() or None
    requirements      = (request.form.get("requirements") or "").strip() or None
    instructions_text = (request.form.get("instructions_text") or "").strip() or None

    user_id = session["user_id"]
    brief_content_blocks        = _brief_image_cache.get(user_id) or []
    instructions_content_blocks = _instructions_image_cache.get(user_id) or []

    # Rubric is compulsory
    if not brief_text and not brief_content_blocks:
        return jsonify({
            "ok": False,
            "error": "Rubric/brief is required. Please upload or paste the marking rubric before analysing.",
        }), 400

    try:
        result = AssignmentCheckerAgent.analyse(
            assignment_text=assignment_text,
            strictness=strictness,
            brief_text=brief_text,
            requirements=requirements,
            instructions_text=instructions_text,
            submission_image_blocks=submission_content_blocks or None,
            brief_image_blocks=brief_content_blocks or None,
            instructions_image_blocks=instructions_content_blocks or None,
        )
        _save_history(
            user_id=user_id,
            title=_make_upload_title(assignment_text, filenames),
            strictness=strictness,
            result=result,
            submission_text=assignment_text,
            brief_text=brief_text or "",
        )
        return jsonify({"ok": True, "result": result})
    except Exception as exc:
        return jsonify({"ok": False, "error": _friendly_error(exc)}), 500


# ── History endpoints ──────────────────────────────────────────────────────────

@assignment_checker_bp.route("/assignment-checker/history", methods=["GET"])
def get_history():
    if "user_id" not in session:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401
    entries = db.get_assignment_history(session["user_id"])
    return jsonify({"ok": True, "history": entries})


@assignment_checker_bp.route("/assignment-checker/history/<int:history_id>", methods=["GET"])
def get_history_entry(history_id: int):
    if "user_id" not in session:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401
    entry = db.get_assignment_history_entry(history_id, session["user_id"])
    if not entry:
        return jsonify({"ok": False, "error": "Entry not found."}), 404
    return jsonify({"ok": True, "entry": entry})


@assignment_checker_bp.route("/assignment-checker/history/<int:history_id>", methods=["DELETE"])
def delete_history_entry(history_id: int):
    if "user_id" not in session:
        return jsonify({"ok": False, "error": "Not authenticated."}), 401
    db.delete_assignment_history_entry(history_id, session["user_id"])
    return jsonify({"ok": True})
