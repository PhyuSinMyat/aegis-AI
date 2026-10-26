"""
summary_scheduler.py
--------------------
Scheduled job that runs daily at 1:20 PM Asia/Singapore time.

Responsibilities
----------------
1. Fetch every active user from the database.
2. For each user, look up today's timetable sessions from their latest study plan.
3. Call the SummaryAgent to generate:
   - AI summary payload   (LLM call)
   - Flashcards           (LLM call)
   - Quiz bank            (rule-based, built from summary payload)
   - Resources            (Bedrock search + reachability check)
4. Upsert every card into `daily_summaries` with academic week + weekday metadata.
5. Log success / failure per user – never crash the scheduler.

Usage
-----
Call `init_scheduler(app, db)` once from your Flask application factory.
The scheduler is started automatically.  The job also exposes
`run_daily_summary_job(app, db)` so you can trigger it manually from a
management CLI command.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone

from backend.agents.planning_agent import compute_academic_week
from backend.agents.summary_agent import SummaryAgent
from backend.services.resource_service import (
    ensure_working_resources,
    find_resources_with_bedrock,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers (pure functions – no DB access)
# ---------------------------------------------------------------------------

def _normalize(value: Any) -> str:
    return str(value or "").strip()


def _weekday_name(d: date) -> str:
    return d.strftime("%A")


def _guess_difficulty(topic: str) -> str:
    lower = (topic or "").lower()
    if any(k in lower for k in ["cryptography", "algorithm", "recursion", "normalization", "network security"]):
        return "Medium"
    if any(k in lower for k in ["introduction", "overview", "variables", "basic", "fundamentals"]):
        return "Beginner"
    return "Medium"


def _estimate_read_time(summary_payload: Dict[str, Any]) -> int:
    word_total = 0
    for value in summary_payload.values():
        if isinstance(value, str):
            word_total += len(value.split())
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    word_total += len(item.split())
                elif isinstance(item, dict):
                    word_total += len(str(item.get("question", "")).split())
                    word_total += len(str(item.get("answer", "")).split())
    return max(3, min(6, round(word_total / 110) + 2))


def _build_flashcards_from_summary(topic: str, summary: Dict[str, Any]) -> List[Dict[str, str]]:
    """Rule-based fallback flashcard builder (used when LLM flashcard call fails)."""
    compare_left = _normalize(summary.get("compare_left_title")) or "Main idea"
    items = [
        {"front": "What is the core concept here?",
         "back": _normalize(summary.get("core_idea"))},
        {"front": "Why is this concept important?",
         "back": _normalize(summary.get("why_it_matters"))},
        {"front": f"What should you remember about {compare_left}?",
         "back": " • ".join(summary.get("compare_left_points") or [])},
        {"front": "What confusion should be avoided?",
         "back": " • ".join((summary.get("common_confusions") or [])[:2])},
        {"front": "What is the key insight to remember?",
         "back": _normalize(summary.get("key_insight"))},
        {"front": "What is the one-line takeaway?",
         "back": _normalize(summary.get("one_line_takeaway"))},
    ]
    return [i for i in items if _normalize(i["back"])]


def _build_quiz_bank(topic: str, s: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Build easy / medium / hard question banks from the summary payload."""
    topic_label = topic or "this topic"

    wrong_pool = [
        *( s.get("key_points") or []),
        *( s.get("common_confusions") or []),
        *( s.get("before_class_checklist") or []),
        *( s.get("exam_focus") or []),
        *( s.get("compare_left_points") or []),
        *( s.get("compare_right_points") or []),
    ]
    wrong_pool = [str(x).strip() for x in wrong_pool if str(x or "").strip()]

    def choices(correct: str, take: int = 3) -> List[str]:
        opts = [correct]
        for item in wrong_pool:
            if item and item != correct and item not in opts:
                opts.append(item)
            if len(opts) >= take + 1:
                break
        return opts

    def unique(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen: set = set()
        result = []
        for q in items:
            key = _normalize(q.get("question")).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(q)
        return result

    easy: List[Dict[str, Any]] = []
    medium: List[Dict[str, Any]] = []
    hard: List[Dict[str, Any]] = []

    # Easy – from quick_self_check
    for q in s.get("quick_self_check") or []:
        answer = _normalize((q or {}).get("answer"))
        question = _normalize((q or {}).get("question"))
        if answer and question:
            easy.append({"type": "mc", "question": question, "correct": answer, "options": choices(answer)})

    # Medium – from key_points
    for kp in s.get("key_points") or []:
        text = _normalize(kp)
        if text:
            medium.append({
                "type": "mc",
                "question": f"Which of the following is a key point about {topic_label}?",
                "correct": text,
                "options": choices(text),
            })

    # Medium – from memory_hook
    for hook in s.get("memory_hook") or []:
        text = _normalize(hook)
        if text:
            medium.append({
                "type": "mc",
                "question": f"What does this memory hook refer to about {topic_label}?",
                "correct": text,
                "options": choices(text),
            })

    # Hard – from exam_focus
    for ef in s.get("exam_focus") or []:
        text = _normalize(ef)
        if text:
            hard.append({
                "type": "mc",
                "question": f"In an exam context, which statement is most accurate about {topic_label}?",
                "correct": text,
                "options": choices(text),
            })

    # Hard – from core_idea as a True/False
    core = _normalize(s.get("core_idea"))
    if core:
        short = core[:140] + "…" if len(core) > 140 else core
        hard.append({
            "type": "tf",
            "question": f"True or False: {short}",
            "correct": "True",
            "options": ["True", "False"],
        })

    # Hard – compare left vs right
    left_title = _normalize(s.get("compare_left_title"))
    right_title = _normalize(s.get("compare_right_title"))
    for lp in s.get("compare_left_points") or []:
        text = _normalize(lp)
        if text and left_title:
            hard.append({
                "type": "mc",
                "question": f"Which of the following belongs to '{left_title}'?",
                "correct": text,
                "options": choices(text),
            })
            break

    return {
        "easy": unique(easy)[:5],
        "medium": unique(medium)[:8],
        "hard": unique(hard)[:8],
    }


def _find_schedule_context(extraction_payload: dict | None, subject: str, topic: str, current_week: Optional[int]) -> str:
    if not extraction_payload:
        return ""
    result = extraction_payload.get("extraction_result", {}) or {}
    context_parts: List[str] = []
    for row in result.get("module_schedule", []) or []:
        module_name = _normalize(row.get("module_name"))
        activities = _normalize(row.get("activities"))
        week_number = _normalize(row.get("week_number"))
        if current_week is not None and week_number and week_number != str(current_week):
            continue
        if (module_name and (module_name.lower() in subject.lower() or subject.lower() in module_name.lower())) \
                or topic.lower() in activities.lower():
            piece = f"Week {week_number}: {activities}" if week_number else activities
            if piece:
                context_parts.append(piece)
        if len(context_parts) >= 2:
            break
    for row in result.get("weekly_topics", []) or []:
        module_name = _normalize(row.get("module_name"))
        week_number = _normalize(row.get("week_number"))
        topic_title = _normalize(row.get("topic") or row.get("title"))
        if current_week is not None and week_number and week_number != str(current_week):
            continue
        if (module_name and (module_name.lower() in subject.lower() or subject.lower() in module_name.lower())) \
                or topic.lower() in topic_title.lower():
            piece = f"Week {week_number}: {topic_title}" if week_number else topic_title
            if piece and piece not in context_parts:
                context_parts.append(piece)
        if len(context_parts) >= 4:
            break
    return " | ".join(context_parts)


def _get_plan_for_week(db, user_id: int, current_week: Optional[int]):
    """
    Return the study plan whose title contains 'Week <current_week>'.
    If not found, fall back to the plan with the smaller week number among recent ones
    to ensure we use the current week plan instead of the upcoming week plan.
    """
    import re
    plans_meta = db.get_study_plans_by_user_id(user_id)
    if not plans_meta:
        return None

    if current_week is not None:
        for meta in plans_meta:
            title = str(meta.get("title") or "")
            match = re.search(r"(?i)\bweek\s*(\d+)\b", title)
            if match and int(match.group(1)) == current_week:
                plan = db.get_study_plan_by_id(meta["plan_id"])
                if plan:
                    return plan

    logger.warning(
        "[Scheduler] user_id=%s  no plan found for Week %s – attempting smart fallback.",
        user_id, current_week,
    )

    # Smart fallback: if the study planner generated 'Current' and 'Upcoming' plans,
    # the 'Upcoming' week is the latest (index 0). We should pick the 'Current' week (index 1)
    # to avoid accidentally generating summaries for the upcoming week.
    recent_two = plans_meta[:2]
    best_candidate = recent_two[0]

    if len(recent_two) == 2:
        w1_match = re.search(r"(?i)\bweek\s*(\d+)\b", str(recent_two[0].get("title") or ""))
        w2_match = re.search(r"(?i)\bweek\s*(\d+)\b", str(recent_two[1].get("title") or ""))
        
        w1 = int(w1_match.group(1)) if w1_match else None
        w2 = int(w2_match.group(1)) if w2_match else None
        
        if w1 is not None and w2 is not None and w2 < w1:
            best_candidate = recent_two[1]

    return db.get_study_plan_by_id(best_candidate["plan_id"])


def _dedupe_sessions(sessions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set = set()
    result = []
    for s in sessions:
        key = (
            _normalize(s.get("subject")).lower(),
            _normalize(s.get("topic")).lower(),
            _normalize(s.get("start")).lower(),
            _normalize(s.get("end")).lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(s)
    return result


# ---------------------------------------------------------------------------
# Core generation logic for a single user
# ---------------------------------------------------------------------------

def generate_summaries_for_user(db, user_id: int, target_date: Optional[date] = None) -> int:
    """
    Generate (or refresh) all daily summary cards for one user.

    Returns the number of cards successfully written to the database.
    """
    target_date = target_date or date.today()
    target_date_str = target_date.isoformat()
    weekday = _weekday_name(target_date)

    # ------------------------------------------------------------------
    # 1. Gather context from DB
    # ------------------------------------------------------------------
    extraction_payload = db.get_extraction_result_json(user_id)
    preferences = db.get_study_preferences_by_user_id(user_id) or {}
    current_week = compute_academic_week(
        preferences.get("semester_start_date", ""),
        target_date_str,
    )
    latest_plan = _get_plan_for_week(db, user_id, current_week)
    
    if not latest_plan:
        logger.info("[Scheduler] user_id=%s has no study plan – skipping.", user_id)
        return 0

    sessions = [
        item for item in (latest_plan.get("timetable_json") or [])
        if _normalize(item.get("day")) == weekday
    ]
    sessions = _dedupe_sessions(sessions)

    if not sessions:
        logger.info("[Scheduler] user_id=%s has no sessions on %s – skipping.", user_id, weekday)
        return 0

    # ------------------------------------------------------------------
    # 2. Check which cards already exist for today; purge stale ones
    # ------------------------------------------------------------------
    persisted_cards = db.get_daily_summary_cards_by_date(user_id, target_date_str)

    # Build the set of (subject, topic, lesson_time) keys the current plan expects
    expected_keys = set()
    for s in sessions:
        subj = _normalize(s.get("subject")) or "General Study"
        tpc  = _normalize(s.get("topic")) or subj
        st   = _normalize(s.get("start"))
        en   = _normalize(s.get("end"))
        lt   = f"{st} - {en}" if st and en else (st or "Planned today")
        expected_keys.add((subj.lower(), tpc.lower(), lt.lower()))

    # If any existing card doesn't belong to today's plan, the DB has stale
    # cards from a previous (wrong-week) run – wipe them all and start fresh.
    persisted_keys = {
        (
            _normalize(item.get("subject")).lower(),
            _normalize(item.get("topic")).lower(),
            _normalize(item.get("lesson_time")).lower(),
        )
        for item in persisted_cards
    }
    if persisted_keys - expected_keys:
        deleted = db.delete_daily_summary_cards_by_date(user_id, target_date_str)
        logger.info(
            "[Scheduler] user_id=%s  purged %d stale card(s) for %s before regenerating.",
            user_id, deleted, target_date_str,
        )
        persisted_cards = []

    persisted_map = {
        (
            _normalize(item.get("subject")).lower(),
            _normalize(item.get("topic")).lower(),
            _normalize(item.get("lesson_time")).lower(),
        ): item
        for item in persisted_cards
    }

    agent = SummaryAgent()
    saved_count = 0

    for session in sessions:
        subject = _normalize(session.get("subject")) or "General Study"
        topic = _normalize(session.get("topic")) or subject
        start = _normalize(session.get("start"))
        end = _normalize(session.get("end"))
        lesson_time = f"{start} - {end}" if start and end else (start or "Planned today")
        map_key = (subject.lower(), topic.lower(), lesson_time.lower())

        # Skip if already fully generated today (has real AI summary, flashcards & quiz_bank)
        existing = persisted_map.get(map_key)
        if existing:
            existing_summary = existing.get("summary") or {}
            existing_flashcards = existing.get("flashcards") or []
            existing_quiz = existing.get("quiz_bank") or {}
            # Only skip if all three artifacts exist and have real content
            if existing_summary.get("core_idea") and existing_flashcards and existing_quiz:
                logger.info(
                    "[Scheduler] user_id=%s  %s | %s – already generated, skipping.",
                    user_id, subject, topic,
                )
                saved_count += 1
                continue

        # ------------------------------------------------------------------
        # 3. Generate AI summary
        # ------------------------------------------------------------------
        context_text = _find_schedule_context(extraction_payload, subject, topic, current_week)
        difficulty = _guess_difficulty(topic)

        try:
            summary_payload = agent.generate_summary(
                module_name=subject,
                topic_title=topic,
                lesson_time=lesson_time,
                class_type=session.get("type", "Class"),
                difficulty=difficulty,
                class_date=target_date_str,
                topic_context=context_text,
                source_material="",
            )
            if not summary_payload:
                raise ValueError("Empty summary returned")
        except Exception as exc:
            logger.warning("[Scheduler] user_id=%s  summary LLM failed for '%s': %s", user_id, topic, exc)
            # Build a rule-based fallback so the card is still stored
            from backend.agents.summary_agent import _build_fallback_summary  # local import to avoid circular
            summary_payload = _build_fallback_summary(subject, topic, context_text)

        # ------------------------------------------------------------------
        # 4. Generate flashcards
        # ------------------------------------------------------------------
        try:
            flashcards = agent.generate_flashcards(
                module_name=subject,
                topic_title=topic,
                summary_payload=summary_payload,
            ) or _build_flashcards_from_summary(topic, summary_payload)
        except Exception as exc:
            logger.warning("[Scheduler] user_id=%s  flashcard LLM failed for '%s': %s", user_id, topic, exc)
            flashcards = _build_flashcards_from_summary(topic, summary_payload)

        # ------------------------------------------------------------------
        # 5. Build quiz bank (rule-based, fast)
        # ------------------------------------------------------------------
        quiz_bank = _build_quiz_bank(topic, summary_payload)

        # ------------------------------------------------------------------
        # 6. Fetch resources (Bedrock search + reachability validation)
        # ------------------------------------------------------------------
        resources: List[Dict[str, Any]] = []
        try:
            raw_resources = find_resources_with_bedrock(topic).get("resources", [])
            resources = ensure_working_resources(raw_resources, topic)
        except Exception as exc:
            logger.warning("[Scheduler] user_id=%s  resource fetch failed for '%s': %s", user_id, topic, exc)
            resources = []

        # ------------------------------------------------------------------
        # 7. Compute display metadata
        # ------------------------------------------------------------------
        read_time = _estimate_read_time(summary_payload)
        prompt_count = len(summary_payload.get("quick_self_check", []))
        short_description = summary_payload.get("why_it_matters", "")

        # ------------------------------------------------------------------
        # 8. Upsert into database
        # ------------------------------------------------------------------
        try:
            db.upsert_daily_summary_card(
                user_id=user_id,
                summary_date=target_date_str,
                academic_week=current_week,          # ← stored explicitly
                weekday=weekday,
                subject=subject,
                topic=topic,
                start_time=start,
                end_time=end,
                lesson_time=lesson_time,
                difficulty=difficulty,
                read_time=read_time,
                prompt_count=prompt_count,
                short_description=short_description,
                summary_payload=summary_payload,
                resources=resources,
                flashcards=flashcards,
                quiz_bank=quiz_bank,
            )
            saved_count += 1
            logger.info(
                "[Scheduler] user_id=%s  saved card: week=%s  %s | %s | %s",
                user_id, current_week, weekday, subject, topic,
            )
        except Exception as exc:
            logger.error("[Scheduler] user_id=%s  DB upsert failed for '%s': %s", user_id, topic, exc)

    return saved_count


# ---------------------------------------------------------------------------
# Scheduler entry points
# ---------------------------------------------------------------------------

def run_daily_summary_job(app, db) -> None:
    """
    Main scheduler job.  Iterates all active users and generates their summaries.
    Wrapped in the Flask app context so DB helpers work correctly.
    """
    with app.app_context():
        logger.info("[Scheduler] Job fired – starting daily summary generation")
        try:
            user_ids = db.get_all_active_user_ids()
        except Exception as exc:
            logger.error("[Scheduler] Could not fetch user IDs: %s", exc)
            return

        total_cards = 0
        for user_id in user_ids:
            try:
                count = generate_summaries_for_user(db, user_id)
                total_cards += count
                logger.info("[Scheduler] user_id=%s → %d card(s) saved.", user_id, count)
            except Exception as exc:
                logger.error("[Scheduler] Unhandled error for user_id=%s: %s", user_id, exc)

        logger.info("[Scheduler] Daily summary job complete. Total cards saved: %d", total_cards)


def init_scheduler(app, db) -> BackgroundScheduler:
    """
    Initialise and start the APScheduler background scheduler.
    Call this once from your Flask application factory.

    Example
    -------
    from backend.services.summary_scheduler import init_scheduler
    scheduler = init_scheduler(app, db)
    """
    scheduler = BackgroundScheduler(daemon=True)
    sg_tz = timezone('Asia/Singapore')
    scheduler.add_job(
        func=run_daily_summary_job,
        trigger=CronTrigger(hour=19, minute=36, timezone=sg_tz),
        args=[app, db],
        id="daily_summary_generation",
        name="Generate daily pre-class summaries",
        replace_existing=True,
        misfire_grace_time=3600,                  # allow up to 1 h late if server was down
    )
    scheduler.start()
    logger.info("[Scheduler] APScheduler started – next run at 19:36(Asia/Singapore)")
    return scheduler
