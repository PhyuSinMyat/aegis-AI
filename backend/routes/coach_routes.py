from flask import Blueprint, jsonify, request, session
from database import DatabaseHelper
from backend.agents.coach_agent import handle_confusion

coach_bp = Blueprint("coach", __name__)
db = DatabaseHelper()

def _require_login():
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401
    return None

@coach_bp.route("/api/coach/confusion", methods=["POST"])
def submit_confusion():
    """Provides a targeted explanation for a confused standard."""
    guard = _require_login()
    if guard: return guard
    
    data = request.get_json(silent=True) or {}
    
    context = data.get("context", "General study material")
    confusion_type = data.get("confusion_type", "concept")
    user_question = data.get("user_question", "")
    
    explanation = handle_confusion(context, confusion_type, user_question)
    
    return jsonify({
        "explanation": explanation
    })
