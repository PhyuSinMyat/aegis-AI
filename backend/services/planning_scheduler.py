"""
planning_scheduler.py
─────────────────────
Automatic weekly study plan generation scheduler.
Runs every Tuesday at 1:35 PM to generate study plans for all active users.

Architecture:
- APScheduler with BackgroundScheduler for running cron jobs
- Generates 2-week plans (current week + next week) for each user
- Integrates with existing planning_agent.py logic
- Logs all operations for monitoring and debugging
"""

import logging
from datetime import date, datetime
from typing import Dict, List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask

from backend.agents.planning_agent import (
    build_class_priority_text,
    build_study_plan_prompt,
    compute_academic_week,
    enforce_planning_constraints,
    extract_plan_sections,
    format_extraction_for_prompt,
    parse_and_validate_sessions,
    stream_study_plan,
)
from backend.schemas.extraction_schema import ExtractionResult as ExtractionSchema
from database import DatabaseHelper

logger = logging.getLogger("planning_scheduler")

# Global scheduler instance
_scheduler: Optional[BackgroundScheduler] = None


def _build_prompt_for_week(
    user_context: Dict,
    extraction_result: ExtractionSchema,
    week_number: int,
    today_str: str,
) -> tuple[str, bool]:
    """
    Build the planning prompt scoped to a specific academic week.
    Returns (prompt_str, is_term_break).
    """
    from datetime import timedelta
    from backend.agents.planning_agent import _parse_iso_date

    sem_start = (user_context.get("preferences", {}) or {}).get("semester_start_date", "")

    # Create synthetic date for the target week
    sem_start_date = _parse_iso_date(sem_start)
    if sem_start_date:
        target_date = sem_start_date + timedelta(weeks=week_number - 1)
        synthetic_today = target_date.strftime("%Y-%m-%d (%A)")
    else:
        synthetic_today = today_str

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


def generate_plan_for_user(db: DatabaseHelper, user_id: int) -> Dict[str, any]:
    """
    Generate 2-week study plans for a single user.

    Returns:
        Dict with keys: success, week1_plan_id, week2_plan_id, error
    """
    logger.info(f"Starting automatic plan generation for user_id={user_id}")

    try:
        # Check if user setup is complete
        if not db.is_setup_complete(user_id):
            logger.warning(f"User {user_id} setup incomplete - skipping")
            return {
                "success": False,
                "error": "User setup incomplete",
                "user_id": user_id,
            }

        # Load cached extraction result
        payload = db.get_extraction_result_json(user_id)
        if not payload:
            logger.warning(f"User {user_id} has no extraction data - skipping")
            return {
                "success": False,
                "error": "No extraction data found",
                "user_id": user_id,
            }

        extraction_result_dict = payload.get("extraction_result")
        if not isinstance(extraction_result_dict, dict):
            logger.warning(f"User {user_id} has invalid extraction data - skipping")
            return {
                "success": False,
                "error": "Invalid extraction data",
                "user_id": user_id,
            }

        try:
            extraction_result = ExtractionSchema(**extraction_result_dict)
        except Exception as e:
            logger.warning(f"User {user_id} extraction schema validation failed: {e}")
            return {
                "success": False,
                "error": f"Extraction validation failed: {str(e)}",
                "user_id": user_id,
            }

        # Load user context
        user_context = db.get_full_user_context(user_id)
        if not user_context:
            logger.warning(f"User {user_id} has no user context - skipping")
            return {
                "success": False,
                "error": "No user context found",
                "user_id": user_id,
            }

        # Determine week numbers
        today = date.today()
        today_str = today.strftime("%Y-%m-%d (%A)")

        sem_start = (user_context.get("preferences", {}) or {}).get("semester_start_date", "")
        current_week_number = compute_academic_week(sem_start, today_str)

        if current_week_number == 1:
            week1_num = 0
            week2_num = 1
        else:
            week1_num = current_week_number
            week2_num = (current_week_number + 1) if current_week_number is not None else None

        logger.info(f"User {user_id}: Generating Week {week1_num} and Week {week2_num}")

        # Generate Week 1
        week1_title = f"Week {week1_num} Study Plan" if week1_num else "Study Plan"

        try:
            prompt_w1, w1_is_term_break = _build_prompt_for_week(
                user_context=user_context,
                extraction_result=extraction_result,
                week_number=week1_num,
                today_str=today_str,
            )

            if w1_is_term_break:
                week1_title = f"Week {week1_num} Term Break Plan" if week1_num else "Term Break Plan"

            logger.info(f"User {user_id}: Streaming Week {week1_num} plan...")

            # Stream and collect Week 1
            full_text_w1 = []
            timetable_w1 = []
            sections_w1 = {}

            for chunk in stream_study_plan(
                prompt=prompt_w1,
                user_context=user_context,
                extraction_result=extraction_result,
            ):
                if "error" in chunk:
                    raise Exception(f"Week {week1_num} generation error: {chunk['error']}")

                if "text" in chunk:
                    full_text_w1.append(chunk["text"])

                if chunk.get("done"):
                    timetable_w1 = chunk.get("timetable_json", [])
                    sections_w1 = chunk.get("sections", {})
                    break

            full_text_w1 = "".join(full_text_w1)

            # Save Week 1 plan
            plan_id_w1 = db.save_study_plan(
                user_id=user_id,
                title=week1_title,
                plan_text=full_text_w1,
                timetable_json=timetable_w1,
            )

            logger.info(f"User {user_id}: Week {week1_num} plan saved (plan_id={plan_id_w1})")

        except Exception as e:
            logger.error(f"User {user_id}: Week {week1_num} generation failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Week {week1_num} generation failed: {str(e)}",
                "user_id": user_id,
            }

        # Generate Week 2
        if week2_num is None:
            logger.info(f"User {user_id}: No Week 2 to generate")
            return {
                "success": True,
                "week1_plan_id": plan_id_w1,
                "week2_plan_id": None,
                "user_id": user_id,
            }

        week2_title = f"Week {week2_num} Study Plan"

        try:
            prompt_w2, w2_is_term_break = _build_prompt_for_week(
                user_context=user_context,
                extraction_result=extraction_result,
                week_number=week2_num,
                today_str=today_str,
            )

            if w2_is_term_break:
                week2_title = f"Week {week2_num} Term Break Plan"

            logger.info(f"User {user_id}: Streaming Week {week2_num} plan...")

            # Stream and collect Week 2
            full_text_w2 = []
            timetable_w2 = []
            sections_w2 = {}

            for chunk in stream_study_plan(
                prompt=prompt_w2,
                user_context=user_context,
                extraction_result=extraction_result,
            ):
                if "error" in chunk:
                    raise Exception(f"Week {week2_num} generation error: {chunk['error']}")

                if "text" in chunk:
                    full_text_w2.append(chunk["text"])

                if chunk.get("done"):
                    timetable_w2 = chunk.get("timetable_json", [])
                    sections_w2 = chunk.get("sections", {})
                    break

            full_text_w2 = "".join(full_text_w2)

            # Save Week 2 plan
            plan_id_w2 = db.save_study_plan(
                user_id=user_id,
                title=week2_title,
                plan_text=full_text_w2,
                timetable_json=timetable_w2,
            )

            logger.info(f"User {user_id}: Week {week2_num} plan saved (plan_id={plan_id_w2})")

            return {
                "success": True,
                "week1_plan_id": plan_id_w1,
                "week2_plan_id": plan_id_w2,
                "user_id": user_id,
            }

        except Exception as e:
            logger.error(f"User {user_id}: Week {week2_num} generation failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Week {week2_num} generation failed: {str(e)}",
                "user_id": user_id,
                "week1_plan_id": plan_id_w1,
            }

    except Exception as e:
        logger.error(f"User {user_id}: Unexpected error during plan generation: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "user_id": user_id,
        }


def run_weekly_planning_job(app: Flask, db: DatabaseHelper) -> None:
    """
    Weekly cron job that runs every Tuesday at 1:35 PM.
    Generates study plans for all active users.
    """
    logger.info("=" * 80)
    logger.info("WEEKLY PLANNING JOB STARTED")
    logger.info(f"Execution time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 80)

    with app.app_context():
        try:
            # Get all active user IDs from database
            # Assuming users table has user_id as primary key
            all_users = db.get_all_active_user_ids()

            if not all_users:
                logger.warning("No users found in database")
                return

            logger.info(f"Found {len(all_users)} user(s) to process")

            success_count = 0
            skip_count = 0
            error_count = 0

            for user_id in all_users:
                try:
                    result = generate_plan_for_user(db, user_id)

                    if result["success"]:
                        success_count += 1
                        logger.info(
                            f"✓ User {user_id}: Plans generated successfully "
                            f"(Week1={result.get('week1_plan_id')}, Week2={result.get('week2_plan_id')})"
                        )
                    else:
                        error_reason = result.get("error", "Unknown error")
                        if "setup incomplete" in error_reason.lower() or "no extraction" in error_reason.lower():
                            skip_count += 1
                            logger.info(f"⊘ User {user_id}: Skipped - {error_reason}")
                        else:
                            error_count += 1
                            logger.error(f"✗ User {user_id}: Failed - {error_reason}")

                except Exception as e:
                    error_count += 1
                    logger.error(f"✗ User {user_id}: Unexpected error - {e}", exc_info=True)

            logger.info("=" * 80)
            logger.info("WEEKLY PLANNING JOB COMPLETED")
            logger.info(f"Success: {success_count} | Skipped: {skip_count} | Errors: {error_count}")
            logger.info("=" * 80)

        except Exception as e:
            logger.error(f"Weekly planning job failed with critical error: {e}", exc_info=True)


def init_planning_scheduler(app: Flask) -> BackgroundScheduler:
    """
    Initialize and start the APScheduler for automatic weekly plan generation.

    Args:
        app: Flask application instance

    Returns:
        BackgroundScheduler instance
    """
    global _scheduler

    if _scheduler is not None:
        logger.warning("Planning scheduler already initialized - returning existing instance")
        return _scheduler

    logger.info("Initializing planning scheduler...")

    db = DatabaseHelper()
    scheduler = BackgroundScheduler(
        daemon=True,
        job_defaults={
            'coalesce': True,  # Combine multiple missed runs into one
            'max_instances': 1,  # Only one instance of each job at a time
            'misfire_grace_time': 3600,  # Allow job to run if missed by up to 1 hour
        }
    )

    # Schedule weekly job: Every Thursday at 1:47 PM
    scheduler.add_job(
        func=lambda: run_weekly_planning_job(app, db),
        trigger=CronTrigger(
            day_of_week='Thu',  # Thursday
            hour=14,  # 2:07 PM
            minute=7,
        ),
        id='weekly_planning_job',
        name='Weekly Study Plan Generation',
        replace_existing=True,
    )

    scheduler.start()
    _scheduler = scheduler

    logger.info("✓ Planning scheduler started successfully")
    logger.info("  - Job: Weekly Study Plan Generation")
    logger.info("  - Schedule: Every Thursday at 2:07 PM")
    logger.info("  - Next run: " + str(scheduler.get_job('weekly_planning_job').next_run_time))

    return scheduler


def shutdown_planning_scheduler() -> None:
    """Shutdown the planning scheduler gracefully."""
    global _scheduler

    if _scheduler is None:
        return

    logger.info("Shutting down planning scheduler...")

    try:
        _scheduler.shutdown(wait=True)
        _scheduler = None
        logger.info("✓ Planning scheduler shut down successfully")
    except Exception as e:
        logger.error(f"Error shutting down planning scheduler: {e}", exc_info=True)


def trigger_manual_planning_job(app: Flask, user_id: Optional[int] = None) -> Dict:
    """
    Manually trigger the planning job for testing or admin purposes.

    Args:
        app: Flask application instance
        user_id: If specified, only generate plans for this user. Otherwise, all users.

    Returns:
        Dict with execution results
    """
    logger.info(f"Manual planning job triggered (user_id={user_id or 'ALL'})")

    db = DatabaseHelper()

    with app.app_context():
        if user_id:
            result = generate_plan_for_user(db, user_id)
            return {"results": [result]}
        else:
            run_weekly_planning_job(app, db)
            return {"message": "Full weekly job executed"}
