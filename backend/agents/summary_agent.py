"""
summary_agent_service.py
------------------------
Service layer consumed by summary_routes.py.

IMPORTANT DESIGN CHANGE
-----------------------
Generation (LLM calls, resource fetching, quiz building) now happens
exclusively inside summary_scheduler.py which runs at 09:00 every day.

This file's only job is to READ already-generated data from the database
and format it for the API / templates.

Public API
----------
get_daily_summary_page_data(db, user_id, target_date=None)
    → Used by the daily-summary page.  Returns today's cards.

get_all_summary_history(db, user_id)
    → Used by the history/archive view.  Returns cards grouped by
      academic week → weekday so the UI can render a week/day tree.

_build_fallback_summary(subject, topic, context_text)
    → Still exported so summary_scheduler.py can import it when an LLM
      call fails and a rule-based card needs to be stored.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.agents.planning_agent import compute_academic_week

_SUMMARY_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "summary_prompt.txt"
_FLASHCARD_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "flashcard_prompt.txt"
_QUIZ_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "quiz_prompt.txt"


def _clean_json(raw: str) -> str:
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s*```$", "", cleaned.strip()).strip()


def _bedrock_text(prompt: str, system_prompt: str, max_tokens: int = 3000) -> str:
    import boto3
    from config import Config

    session = boto3.Session(**Config.bedrock_session_kwargs())
    bedrock = session.client("bedrock-runtime", region_name=Config.AWS_REGION)
    model_id = Config.resolve_bedrock_model_id(None)

    for attempt in range(1, 4):
        try:
            response = bedrock.converse(
                modelId=model_id,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
            )
            content = response.get("output", {}).get("message", {}).get("content", [])
            return "\n".join(b.get("text", "") for b in content if b.get("text")).strip()
        except Exception as exc:
            if attempt == 3:
                raise
            time.sleep(2 ** (attempt - 1))


class SummaryAgent:
    def __init__(self):
        self._summary_system = _SUMMARY_PROMPT_PATH.read_text(encoding="utf-8").strip()
        self._flashcard_system = _FLASHCARD_PROMPT_PATH.read_text(encoding="utf-8").strip()
        self._quiz_system = _QUIZ_PROMPT_PATH.read_text(encoding="utf-8").strip()

    def generate_summary(
        self,
        module_name: str,
        topic_title: str,
        lesson_time: str = "",
        class_type: str = "Class",
        difficulty: str = "Medium",
        class_date: str = "",
        topic_context: str = "",
        source_material: str = "",
    ) -> Dict[str, Any]:
        parts = [
            f"Module: {module_name}",
            f"Topic: {topic_title}",
            f"Class type: {class_type}",
            f"Difficulty: {difficulty}",
        ]
        if lesson_time:
            parts.append(f"Lesson time: {lesson_time}")
        if class_date:
            parts.append(f"Date: {class_date}")
        if topic_context:
            parts.append(f"Context from timetable: {topic_context}")
        if source_material:
            parts.append(f"Source material: {source_material}")

        raw = _bedrock_text("\n".join(parts), self._summary_system, max_tokens=3000)
        return json.loads(_clean_json(raw))

    def generate_flashcards(
        self,
        module_name: str,
        topic_title: str,
        summary_payload: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        summary_text = json.dumps(summary_payload, ensure_ascii=False)
        prompt = (
            f"Module: {module_name}\n"
            f"Topic: {topic_title}\n"
            f"Summary:\n{summary_text}"
        )
        raw = _bedrock_text(prompt, self._flashcard_system, max_tokens=1500)
        data = json.loads(_clean_json(raw))
        return data.get("flashcards", data) if isinstance(data, dict) else data

    def generate_quiz(
        self,
        module_name: str,
        topic_title: str,
        summary_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        summary_text = json.dumps(summary_payload, ensure_ascii=False)
        prompt = (
            f"Module: {module_name}\n"
            f"Topic: {topic_title}\n"
            f"Summary:\n{summary_text}"
        )
        raw = _bedrock_text(prompt, self._quiz_system, max_tokens=6000)
        return json.loads(_clean_json(raw))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _weekday_name(d: date) -> str:
    return d.strftime("%A")


# ---------------------------------------------------------------------------
# Fallback summary (used by scheduler when LLM fails)
# ---------------------------------------------------------------------------

def _build_fallback_summary(subject: str, topic: str, context_text: str = "") -> Dict[str, Any]:
    """
    Rule-based summary that is stored when the LLM call fails.
    Always returns the full schema so the DB is never missing fields.
    """
    ctx = f"  Context: {context_text}" if context_text else ""
    return {
        "why_it_matters": (
            f"{topic} is studied in {subject} because it builds core knowledge "
            f"needed for class, lab, and assessments.{ctx}"
        ),
        "before_class_checklist": [
            f"Know what {topic} means in one sentence.",
            "Know one practical example.",
            "Know where this topic appears in class or lab.",
            "Know one common mistake to avoid.",
        ],
        "compare_left_title": "Main idea",
        "compare_left_points": [
            "Definition or key meaning.",
            "Main purpose.",
            "What to notice.",
            "One useful example.",
        ],
        "compare_right_title": "How it is used",
        "compare_right_points": [
            "When it is applied.",
            "What it helps achieve.",
            "How it differs from related ideas.",
            "Where it appears in class or lab.",
        ],
        "key_insight": (
            f"Do not just memorise the term – understand why {topic} is used "
            f"and be able to explain it simply."
        ),
        "core_idea": (
            f"{topic} is a key concept in {subject}. "
            f"Before class, the student should understand the main idea, "
            f"what problem it solves, and how it connects to upcoming work."
        ),
        "example_intro": f"Think of one small real-world example of {topic} before class.",
        "real_world_example_steps": [
            "Start from a simple real situation.",
            "Show how the idea is applied.",
            "Explain what happens next.",
            "Connect it back to why the topic matters.",
        ],
        "common_confusions": [
            "Memorising the term without understanding when to use it.",
            f"Confusing {topic} with a closely related concept.",
            "Focusing on detail before grasping the main purpose.",
            "Missing why this appears in class, lab, or quiz.",
        ],
        "memory_hook": [
            f"Main idea = what {topic} means.",
            "Use case = when it matters.",
        ],
        "key_points": [
            f"Know the definition of {topic}.",
            "Know the purpose.",
            "Know one example.",
            "Know one difference from a related idea.",
        ],
        "quick_self_check": [
            {
                "question": f"What is {topic} in one simple sentence?",
                "answer": f"Explain the meaning of {topic} without copying the topic title.",
            },
            {
                "question": f"Where might {topic} appear in class, tutorial, or lab?",
                "answer": "Give one realistic learning or practical situation.",
            },
            {
                "question": f"What is one common confusion about {topic}?",
                "answer": "Name one likely mistake and correct it.",
            },
        ],
        "exam_focus": [
            f"Define {topic} clearly.",
            "Give one useful example.",
            "Explain when it is used.",
            "Show one difference from a related idea if relevant.",
        ],
        "one_line_takeaway": (
            f"Before class, be able to explain what {topic} means, "
            f"why it matters, and one example of how it is used."
        ),
    }


# ---------------------------------------------------------------------------
# Page data for Today's Daily Summary
# ---------------------------------------------------------------------------

def get_daily_summary_page_data(
    db,
    user_id: int,
    target_date: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Return all pre-generated summary cards for a specific date.
    Generation is NOT triggered here – that is the scheduler's job.
    If no cards exist yet for today, return an empty list so the
    template can show a 'Summaries are being prepared' message.
    """
    target_date = target_date or date.today()
    target_date_str = target_date.isoformat()
    weekday = _weekday_name(target_date)

    preferences = db.get_study_preferences_by_user_id(user_id) or {}
    current_week = compute_academic_week(
        preferences.get("semester_start_date", ""),
        target_date_str,
    )

    raw_cards = db.get_daily_summary_cards_by_date(user_id, target_date_str)

    cards: List[Dict[str, Any]] = []
    for row in raw_cards:
        cards.append({
            "id":                int(row.get("summary_id") or 0),
            "subject":           _normalize_text(row.get("subject")),
            "topic":             _normalize_text(row.get("topic")),
            "start":             _normalize_text(row.get("start_time")),
            "end":               _normalize_text(row.get("end_time")),
            "lesson_time":       _normalize_text(row.get("lesson_time")),
            "difficulty":        _normalize_text(row.get("difficulty")) or "Medium",
            "read_time":         int(row.get("read_time") or 3),
            "prompt_count":      int(row.get("prompt_count") or 3),
            "short_description": _normalize_text(row.get("short_description")),
            "summary":           row.get("summary") or {},
            "resources":         row.get("resources") or [],
            "flashcards":        row.get("flashcards") or [],
            "note_text":         _normalize_text(row.get("note_text")),
            "academic_week":     row.get("academic_week") or current_week,
            "weekday":           _normalize_text(row.get("weekday")) or weekday,
            "summary_date":      _normalize_text(row.get("summary_date")) or target_date_str,
        })

    return {
        "target_date":  target_date,
        "weekday":      weekday,
        "current_week": current_week,
        "cards":        cards,
        "card_count":   len(cards),
    }


# ---------------------------------------------------------------------------
# History view – all summaries grouped by week → weekday
# ---------------------------------------------------------------------------

# Canonical weekday ordering (Mon-first)
_WEEKDAY_ORDER = {
    "Monday": 0, "Tuesday": 1, "Wednesday": 2,
    "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6,
}


def get_all_summary_history(db, user_id: int) -> Dict[str, Any]:
    """
    Fetch every daily summary card for the user and return a structure
    ready for the week/day grouped history page:

    {
        "weeks": [
            {
                "week_number": 1,
                "days": [
                    {
                        "weekday": "Monday",
                        "date": "2025-01-20",
                        "cards": [ ... ]
                    },
                    ...
                ]
            },
            ...
        ],
        "total_cards": 42
    }
    """
    all_rows = db.get_all_daily_summary_cards(user_id)  # new DB method – see note below

    # ---- Group rows by (academic_week, weekday, summary_date) ----
    # week_map: { week_number(int) -> { weekday(str) -> { date(str) -> [cards] } } }
    week_map: Dict[int, Dict[str, Dict[str, List[Dict[str, Any]]]]] = {}

    for row in all_rows:
        week_num = int(row.get("academic_week") or 0)
        weekday  = _normalize_text(row.get("weekday")) or "Unknown"
        date_str = _normalize_text(row.get("summary_date"))

        week_map.setdefault(week_num, {})
        week_map[week_num].setdefault(weekday, {})
        week_map[week_num][weekday].setdefault(date_str, [])

        week_map[week_num][weekday][date_str].append({
            "id":                int(row.get("summary_id") or 0),
            "subject":           _normalize_text(row.get("subject")),
            "topic":             _normalize_text(row.get("topic")),
            "lesson_time":       _normalize_text(row.get("lesson_time")),
            "difficulty":        _normalize_text(row.get("difficulty")) or "Medium",
            "read_time":         int(row.get("read_time") or 3),
            "prompt_count":      int(row.get("prompt_count") or 3),
            "short_description": _normalize_text(row.get("short_description")),
            "summary":           row.get("summary") or {},
            "resources":         row.get("resources") or [],
            "flashcards":        row.get("flashcards") or [],
            "note_text":         _normalize_text(row.get("note_text")),
        })

    # ---- Flatten into sorted output ----
    weeks_output = []
    for week_num in sorted(week_map.keys()):
        days_output = []
        weekday_dict = week_map[week_num]
        for weekday in sorted(weekday_dict.keys(), key=lambda d: _WEEKDAY_ORDER.get(d, 99)):
            date_dict = weekday_dict[weekday]
            for date_str in sorted(date_dict.keys()):
                days_output.append({
                    "weekday": weekday,
                    "date":    date_str,
                    "cards":   date_dict[date_str],
                })
        if days_output:
            weeks_output.append({
                "week_number": week_num,
                "days":        days_output,
            })

    return {
        "weeks":       weeks_output,
        "total_cards": sum(
            len(day["cards"])
            for week in weeks_output
            for day in week["days"]
        ),
    }


# ---------------------------------------------------------------------------
# Backwards-compatible shim
# (old code called build_daily_summary_cards; keep it working)
# ---------------------------------------------------------------------------

def build_daily_summary_cards(db, user_id: int, target_date: Optional[date] = None) -> Dict[str, Any]:
    """Deprecated shim – wraps get_daily_summary_page_data for backwards compat."""
    return get_daily_summary_page_data(db, user_id, target_date)