"""
summary_routes.py
-----------------
Flask Blueprint for all daily-summary related routes.

Key changes vs previous version
---------------------------------
- /daily-summary          now shows ALL summaries (week/day grouped history)
                          instead of just today's cards.
- /api/daily-summary      returns today's cards (read-only from DB).
- /api/summary-history    returns full week→day grouped history for the
                          history/archive view.
- /api/daily-summary/<id>/generate  (POST) lets the UI trigger on-demand
                          generation for a single card that the scheduler
                          may have missed.
- All other existing endpoints (note, resources/refresh, flashcard-progress,
  quiz-result, quiz-results) are preserved unchanged.
"""

from __future__ import annotations

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for
import threading

from backend.services.resource_service import ensure_working_resources, find_resources_with_bedrock
from backend.agents.summary_agent import (
    get_daily_summary_page_data,
    get_all_summary_history,
    build_daily_summary_cards,          # backwards-compat shim
)
from database import DatabaseHelper

summary_bp = Blueprint("summary", __name__)
db = DatabaseHelper()

# In-memory tracking for async quiz generation  { (user_id, summary_id): 'generating' | 'done' | 'error:<msg>' }
_quiz_gen_status: dict = {}


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def _require_login():
    if "user_id" not in session:
        flash("Please log in first.", "danger")
        return redirect(url_for("login"))
    return None


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@summary_bp.route("/daily-summary", methods=["GET"])
def daily_summary_page():
    guard = _require_login()
    if guard:
        return guard

    user_id = session["user_id"]
    today_data = get_daily_summary_page_data(db, user_id)
    history_data = get_all_summary_history(db, user_id)

    return render_template(
        "daily_summary.html",
        current_page="daily_summary",
        page_data=today_data,           # ← keeps old template working
        today_data=today_data,
        history_data=history_data,
    )

# ---------------------------------------------------------------------------
# API – today's summary cards
# ---------------------------------------------------------------------------

@summary_bp.route("/api/daily-summary", methods=["GET"])
def daily_summary_api():
    """
    Return today's pre-generated summary cards.
    Generation is NOT triggered here.
    If the scheduler hasn't run yet, cards list will be empty.
    """
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    user_id = session["user_id"]
    page_data = get_daily_summary_page_data(db, user_id)

    return jsonify({
        "weekday":      page_data.get("weekday"),
        "current_week": page_data.get("current_week"),
        "cards":        page_data.get("cards", []),
        "card_count":   page_data.get("card_count", 0),
    })


# ---------------------------------------------------------------------------
# API – full summary history (week → day grouped)
# ---------------------------------------------------------------------------

@summary_bp.route("/api/summary-history", methods=["GET"])
def summary_history_api():
    """
    Return all daily summary cards grouped by academic week and weekday.

    Response shape
    --------------
    {
        "weeks": [
            {
                "week_number": 1,
                "days": [
                    {
                        "weekday": "Monday",
                        "date": "2025-01-20",
                        "cards": [ { id, subject, topic, lesson_time, ... } ]
                    }
                ]
            },
            ...
        ],
        "total_cards": 42
    }
    """
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    user_id = session["user_id"]
    history = get_all_summary_history(db, user_id)
    return jsonify(history)


# ---------------------------------------------------------------------------
# API – on-demand single-card generation (fallback if scheduler missed)
# ---------------------------------------------------------------------------

@summary_bp.route("/api/daily-summary/<int:summary_id>/generate", methods=["POST"])
def regenerate_summary_card(summary_id: int):
    """
    Trigger on-demand (re)generation for a single summary card.
    Use this when the scheduler job was missed or the card needs a refresh.
    The card must already exist in the DB (created by the scheduler as a
    stub, or a previous generation attempt).
    """
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    user_id = session["user_id"]
    card = db.get_daily_summary_card(user_id=user_id, summary_id=summary_id)
    if not card:
        return jsonify({"error": "Summary card not found."}), 404

    # Import here to avoid circular imports at module level
    from backend.services.summary_scheduler import generate_summaries_for_user
    from datetime import date as _date

    summary_date_str = str(card.get("summary_date") or _date.today().isoformat())
    try:
        target_date = _date.fromisoformat(summary_date_str)
    except ValueError:
        target_date = _date.today()

    try:
        count = generate_summaries_for_user(db, user_id, target_date)
        return jsonify({"ok": True, "summary_id": summary_id, "cards_refreshed": count})
    except Exception as exc:
        current_app.logger.exception("Daily summary generation failed")
        return jsonify({"error": "Generation failed. Please try again in a moment."}), 500


# ---------------------------------------------------------------------------
# API – save / update personal note on a card
# ---------------------------------------------------------------------------

@summary_bp.route("/api/daily-summary/<int:summary_id>/note", methods=["POST"])
def save_daily_summary_note(summary_id: int):
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    user_id = session["user_id"]
    payload = request.get_json(silent=True) or {}
    note_text = str(payload.get("note_text") or "").strip()

    if len(note_text) > 6000:
        return jsonify({"error": "Note is too long. Please keep it under 6000 characters."}), 400

    updated = db.update_daily_summary_note(
        user_id=user_id,
        summary_id=summary_id,
        note_text=note_text,
    )
    if not updated:
        return jsonify({"error": "Summary card not found."}), 404

    return jsonify({"ok": True, "summary_id": summary_id, "note_text": note_text})


# ---------------------------------------------------------------------------
# API – refresh resources for a card
# ---------------------------------------------------------------------------

@summary_bp.route("/api/daily-summary/<int:summary_id>/resources/refresh", methods=["POST"])
def refresh_daily_summary_resources(summary_id: int):
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    user_id = session["user_id"]
    card = db.get_daily_summary_card(user_id=user_id, summary_id=summary_id)
    if not card:
        return jsonify({"error": "Summary card not found."}), 404

    topic = str(card.get("topic") or "").strip()
    existing_resources = card.get("resources") or []

    try:
        refreshed_resources = find_resources_with_bedrock(topic).get("resources", [])
    except Exception:
        refreshed_resources = []

    merged_resources = ensure_working_resources(
        refreshed_resources or existing_resources, topic
    )
    if not merged_resources:
        return jsonify({"error": "Could not find working resource links right now. Please try again."}), 502

    updated = db.update_daily_summary_resources(
        user_id=user_id,
        summary_id=summary_id,
        resources=merged_resources,
    )
    if not updated:
        return jsonify({"error": "Summary card not found."}), 404

    return jsonify({"ok": True, "summary_id": summary_id, "resources": merged_resources})


# ---------------------------------------------------------------------------
# API – flashcard progress (save / get)
# ---------------------------------------------------------------------------

@summary_bp.route("/api/daily-summary/<int:summary_id>/flashcard-progress", methods=["POST"])
def save_flashcard_progress(summary_id: int):
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    user_id = session["user_id"]
    payload = request.get_json(silent=True) or {}
    known_indices = payload.get("known_indices", [])

    if not isinstance(known_indices, list):
        return jsonify({"error": "known_indices must be a list"}), 400
    try:
        known_indices = [int(i) for i in known_indices]
    except (ValueError, TypeError):
        return jsonify({"error": "known_indices must contain only integers"}), 400

    db.save_flashcard_progress(
        user_id=user_id,
        summary_id=summary_id,
        known_card_indices=known_indices,
    )
    return jsonify({"ok": True, "summary_id": summary_id, "known_indices": known_indices})


@summary_bp.route("/api/daily-summary/<int:summary_id>/flashcard-progress", methods=["GET"])
def get_flashcard_progress(summary_id: int):
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    user_id = session["user_id"]
    known_indices = db.get_flashcard_progress(user_id=user_id, summary_id=summary_id)
    return jsonify({"ok": True, "summary_id": summary_id, "known_indices": known_indices})


# ---------------------------------------------------------------------------
# API – quiz results (save / get)
# ---------------------------------------------------------------------------

@summary_bp.route("/api/daily-summary/<int:summary_id>/quiz-result", methods=["POST"])
def save_quiz_result(summary_id: int):
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    user_id = session["user_id"]
    payload = request.get_json(silent=True) or {}

    difficulty      = str(payload.get("difficulty") or "").strip()
    question_count  = payload.get("question_count", 0)
    correct_count   = payload.get("correct_count", 0)
    answers         = payload.get("answers", [])

    if not difficulty:
        return jsonify({"error": "difficulty is required"}), 400
    if not isinstance(question_count, int) or not isinstance(correct_count, int):
        return jsonify({"error": "question_count and correct_count must be integers"}), 400
    if question_count < 0 or correct_count < 0 or correct_count > question_count:
        return jsonify({"error": "Invalid question/correct counts"}), 400

    result_id = db.save_quiz_result(
        user_id=user_id,
        summary_id=summary_id,
        difficulty=difficulty,
        question_count=question_count,
        correct_count=correct_count,
        answers=answers,
    )

    return jsonify({
        "ok":         True,
        "summary_id": summary_id,
        "result_id":  result_id,
        "difficulty": difficulty,
        "correct":    correct_count,
        "total":      question_count,
    })


@summary_bp.route("/api/daily-summary/<int:summary_id>/quiz-results", methods=["GET"])
def get_quiz_results(summary_id: int):
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    user_id = session["user_id"]
    results = db.get_quiz_results_for_summary(user_id=user_id, summary_id=summary_id)
    return jsonify({"ok": True, "summary_id": summary_id, "results": results})


# ---------------------------------------------------------------------------
# API – on-demand quiz bank generation (POST, GET, DELETE)
# ---------------------------------------------------------------------------

@summary_bp.route("/api/daily-summary/<int:summary_id>/quiz", methods=["POST"])
def generate_quiz_bank(summary_id: int):
    """Kick off async LLM quiz generation. Returns immediately with status='generating'."""
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    user_id = session["user_id"]
    key = (user_id, summary_id)

    # Already in progress — don't double-generate
    if _quiz_gen_status.get(key) == "generating":
        return jsonify({"ok": True, "status": "generating", "summary_id": summary_id})

    card = db.get_daily_summary_card(user_id=user_id, summary_id=summary_id)
    if not card:
        return jsonify({"error": "Summary card not found."}), 404

    _quiz_gen_status[key] = "generating"

    def _run():
        try:
            from backend.agents.summary_agent import SummaryAgent
            agent = SummaryAgent()
            quiz_payload = agent.generate_quiz(
                module_name=str(card.get("subject") or ""),
                topic_title=str(card.get("topic") or ""),
                summary_payload=card.get("summary") or {}
            )
            db.save_quiz_bank(user_id=user_id, summary_id=summary_id, quiz_payload=quiz_payload)
            _quiz_gen_status[key] = "done"
        except Exception as exc:
            _quiz_gen_status[key] = f"error:{exc}"

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "status": "generating", "summary_id": summary_id})


def _auto_seed_quiz_bank(user_id: int, summary_id: int):
    """Seed the quiz_banks table from the card's embedded quiz_bank (easy/medium/hard → attempt_1/2/3)."""
    card = db.get_daily_summary_card(user_id=user_id, summary_id=summary_id)
    if not card:
        return None
    embedded = card.get("quiz_bank") or {}
    if not embedded:
        return None
    quiz_payload = {
        "attempt_1": embedded.get("easy", []),
        "attempt_2": embedded.get("medium", []),
        "attempt_3": embedded.get("hard", []),
        "user_progress": {},
    }
    db.save_quiz_bank(user_id=user_id, summary_id=summary_id, quiz_payload=quiz_payload)
    return quiz_payload


@summary_bp.route("/api/daily-summary/<int:summary_id>/quiz", methods=["GET"])
def get_quiz_bank(summary_id: int):
    """Return the quiz bank, auto-seeding from the card's embedded quiz data if needed."""
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    user_id = session["user_id"]
    key = (user_id, summary_id)
    status = _quiz_gen_status.get(key)

    if status == "generating":
        return jsonify({"ok": True, "status": "generating", "summary_id": summary_id})
    if status and status.startswith("error:"):
        _quiz_gen_status.pop(key, None)
        return jsonify({"error": status[6:]}), 500

    quiz_payload = db.get_quiz_bank(user_id=user_id, summary_id=summary_id)
    if not quiz_payload:
        quiz_payload = _auto_seed_quiz_bank(user_id, summary_id)
    if not quiz_payload:
        return jsonify({"error": "Quiz bank not found."}), 404
    _quiz_gen_status.pop(key, None)
    return jsonify({"ok": True, "status": "ready", "summary_id": summary_id, "quiz_bank": quiz_payload})


@summary_bp.route("/api/daily-summary/<int:summary_id>/quiz", methods=["DELETE"])
def delete_quiz_bank(summary_id: int):
    """Delete the generated quiz bank so it can be regenerated."""
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    user_id = session["user_id"]
    deleted = db.delete_quiz_bank(user_id=user_id, summary_id=summary_id)
    if deleted:
        return jsonify({"ok": True, "summary_id": summary_id, "message": "Quiz bank deleted."})
    else:
        return jsonify({"error": "Quiz bank not found."}), 404


@summary_bp.route("/api/daily-summary/<int:summary_id>/quiz/progress", methods=["PATCH"])
def save_quiz_progress(summary_id: int):
    """Merge user answers for one attempt into the stored quiz bank."""
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    user_id = session["user_id"]
    payload = request.get_json(silent=True) or {}

    attempt   = payload.get("attempt")
    answers   = payload.get("answers", [])
    correct   = payload.get("correct", 0)
    total     = payload.get("total", 0)
    questions = payload.get("questions", [])   # full question objects for Q&A replay
    feedback  = payload.get("feedback",  [])   # pre-built self-contained Q&A records

    if attempt not in (1, 2, 3):
        return jsonify({"error": "attempt must be 1, 2, or 3"}), 400

    quiz_payload = db.get_quiz_bank(user_id=user_id, summary_id=summary_id)
    if not quiz_payload:
        quiz_payload = _auto_seed_quiz_bank(user_id, summary_id)
    if not quiz_payload:
        return jsonify({"error": "Quiz bank not found."}), 404

    progress = quiz_payload.get("user_progress", {})
    progress[f"attempt_{attempt}"] = {
        "correct":   int(correct),
        "total":     int(total),
        "answers":   answers,
        "questions": questions,  # raw question objects (for backward compat)
        "feedback":  feedback,   # pre-built feedback records (preferred for display)
    }
    quiz_payload["user_progress"] = progress
    db.save_quiz_bank(user_id=user_id, summary_id=summary_id, quiz_payload=quiz_payload)
    return jsonify({"ok": True, "summary_id": summary_id, "user_progress": progress})
